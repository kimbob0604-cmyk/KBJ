"""DB 쓰기 실패 로컬 디스크 큐 — GEXLAB `tests/unit/test_spool.py` 에서 승격(kbj/store/spool.py).

옮긴 것(시험 본문 그대로, import 경로만 바꿈 — `data.spool` → `kbj.store.spool`, GX `data.store` 의
타입 별칭 `store.Row`·`store.Tag` 는 같은 정의인 `kbj.store.spool.Row`·`Tag` 로):
- 값 인코딩 왕복·KST 시각·naive 거부·깨진 줄(4 — 매개변수 경우 20)
- `DiskSpool` 자체: 순서·회전·일시 오류 재개·마감·데드레터·거부 행·끊긴 줄·재기동·잠금·권한·상한·
  큰 묶음·표 이름·반쯤 쓴 줄(14)

남긴 것(P7 — GX 레코드·`PostgresSink`·가짜 DB 를 쓴다, docs/p2_design.md §1.8):
`test_every_kind_of_table_row_round_trips_through_a_line` 과 `PostgresSink + 스풀` 절 전부는
legacy/gexlab/tests/unit/test_spool.py 에 그대로 있다.
"""

from __future__ import annotations

import json
import stat
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import kbj.store.spool as spool_mod
from kbj.store.spool import (
    DEAD,
    DiskSpool,
    RejectedRows,
    Row,
    SpooledBatch,
    SpoolError,
    Tag,
    TransientWrite,
    decode_batch,
    decode_value,
    encode_batch,
    encode_value,
)

KST = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 9, 28, 10, 0, 2, 250000, tzinfo=KST)
DAY = date(2026, 9, 28)
TAG: Tag = (DAY, "day")


# ── 값 인코딩 ──


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        0,
        -(2**62),
        "한글 ^|�",
        "",
        1.5,
        float("nan"),
        float("inf"),
        Decimal("1100.00"),
        Decimal("-0.0081"),
        Decimal("1E+3"),
        datetime(2026, 9, 28, 1, 0, 2, 250000, tzinfo=UTC),
        date(2026, 9, 28),
        b"\x00\xffdigest",
    ],
)
def test_values_round_trip_with_their_type(value: object) -> None:
    back = decode_value(json.loads(json.dumps(encode_value(value))))
    if isinstance(value, float) and value != value:
        assert isinstance(back, float) and back != back
        return
    assert back == value and type(back) is type(value)
    if isinstance(value, Decimal):
        assert str(back) == str(value)  # 자릿수까지 (numeric 에 같은 값)


def test_kst_datetime_is_stored_as_the_same_instant_in_utc() -> None:
    back = decode_value(encode_value(T0))
    assert back == T0 and isinstance(back, datetime) and back.utcoffset() == timedelta(0)


def test_naive_datetime_and_unknown_types_are_refused() -> None:
    with pytest.raises(SpoolError, match="naive"):
        encode_value(datetime(2026, 9, 28, 10, 0))  # noqa: DTZ001 — 거부되는지 본다
    with pytest.raises(SpoolError, match="타입"):
        encode_value({"a": 1})
    with pytest.raises(SpoolError, match="타입"):
        encode_value([1, 2])


@pytest.mark.parametrize(
    "line",
    [
        b'{"v":1,"table":"x"',  # 끊긴 줄 (개행 없음)
        b"not json\n",
        b'{"v":99,"table":"x","at":"2026-09-28T00:00:00+00:00","trade_date":null,'
        b'"session":null,"n":0,"columns":[],"rows":[]}\n',
        b'{"v":1,"table":"x","at":"2026-09-28T00:00:00+00:00","trade_date":null,'
        b'"session":null,"n":2,"columns":["a"],"rows":[[1]]}\n',
        b'{"v":1,"table":"x","at":"2026-09-28T00:00:00+00:00","trade_date":null,'
        b'"session":null,"n":1,"columns":["a","b"],"rows":[[1]]}\n',
    ],
)
def test_malformed_lines_are_value_errors(line: bytes) -> None:
    with pytest.raises((ValueError, KeyError)):
        decode_batch(line)


# ── 디스크 스풀 ──


def _rows(n: int, start: int = 0) -> list[Row]:
    return [(i, f"v{i}") for i in range(start, start + n)]


COLS = ("a", "b")


def _collect(sp: DiskSpool) -> list[tuple[str, list[Row]]]:
    got: list[tuple[str, list[Row]]] = []
    res = sp.replay(lambda b: got.append((b.table, b.rows)))
    assert res.complete
    return got


def test_append_then_replay_in_order_and_files_are_removed(tmp_path: Path) -> None:
    sp = DiskSpool(tmp_path / "sp")
    assert not sp.pending
    sp.append("chain_snapshots", COLS, _rows(2), TAG)
    sp.append("raw_messages", COLS, _rows(1, 10), TAG)
    sp.append("chain_snapshots", COLS, _rows(1, 5), (None, None))
    assert sp.pending and sp.status().tables == ("chain_snapshots", "raw_messages")
    got = _collect(sp)
    # 표마다 넣은 순서, 먼저 연 세그먼트부터
    assert got == [
        ("chain_snapshots", _rows(2)),
        ("chain_snapshots", _rows(1, 5)),
        ("raw_messages", _rows(1, 10)),
    ]
    assert not sp.pending and sp.status().segments == 0
    assert list((tmp_path / "sp").rglob("*.jsonl")) == []


def test_segments_rotate_and_replay_follows_creation_order(tmp_path: Path) -> None:
    sp = DiskSpool(tmp_path, segment_bytes=1)  # 줄마다 새 세그먼트
    sp.append("fut_ticks", COLS, _rows(1, 0), TAG)
    sp.append("opt_ticks", COLS, _rows(1, 1), TAG)
    sp.append("fut_ticks", COLS, _rows(1, 2), TAG)
    assert sp.status().segments == 3
    got = [(t, r[0][0]) for t, r in _collect(sp)]
    assert got == [("fut_ticks", 0), ("opt_ticks", 1), ("fut_ticks", 2)]


def test_transient_error_stops_at_that_batch_and_resumes_there(tmp_path: Path) -> None:
    sp = DiskSpool(tmp_path)
    for i in range(4):
        sp.append("fut_ticks", COLS, _rows(1, i), TAG)
    sent: list[int] = []
    fail = [True]

    def flaky(b: SpooledBatch) -> None:
        if b.rows[0][0] == 2 and fail[0]:
            fail[0] = False
            raise TransientWrite
        sent.append(int(b.rows[0][0]))

    res = sp.replay(flaky)
    assert res.stopped == "transient" and res.batches == 2 and sp.pending
    sp.append("fut_ticks", COLS, _rows(1, 9), TAG)  # 멈춘 사이 새 묶음은 뒤에
    res2 = sp.replay(flaky)
    assert res2.complete and sent == [0, 1, 2, 3, 9] and not sp.pending


def test_deadline_stops_between_batches(tmp_path: Path) -> None:
    sp = DiskSpool(tmp_path)
    for i in range(3):
        sp.append("fut_ticks", COLS, _rows(1, i), TAG)
    t = [0.0]

    def write(_: SpooledBatch) -> None:
        t[0] += 1.0

    res = sp.replay(write, deadline=1.5, clock=lambda: t[0])
    assert res.stopped == "deadline" and res.batches == 2 and sp.pending
    assert sp.replay(write).batches == 1 and not sp.pending


def test_data_errors_go_to_dead_letters_and_replay_continues(tmp_path: Path) -> None:
    sp = DiskSpool(tmp_path)
    for i in range(3):
        sp.append("chain_snapshots", COLS, _rows(1, i), TAG)
    sent: list[int] = []

    def write(b: SpooledBatch) -> None:
        if b.rows[0][0] == 1:
            raise ValueError("check violation")
        sent.append(int(b.rows[0][0]))

    res = sp.replay(write)
    assert res.complete and sent == [0, 2] and (res.dead_batches, res.dead_rows) == (1, 1)
    dead = (tmp_path / DEAD / "chain_snapshots.jsonl").read_bytes()
    assert decode_batch(dead).rows == [(1, "v1")]
    assert sp.status().dead_bytes == len(dead) and not sp.pending


def test_rejected_rows_alone_go_to_dead_letters_with_the_batch_tag(tmp_path: Path) -> None:
    sp = DiskSpool(tmp_path, now=lambda: T0)
    sp.append("fut_ticks", COLS, _rows(3), TAG)
    sp.append("fut_ticks", COLS, _rows(2, 10), TAG)

    def write(b: SpooledBatch) -> None:
        bad = [r for r in b.rows if r[0] in (1, 10, 11)]
        if bad:
            raise RejectedRows(bad, "CheckViolation(23514)")

    res = sp.replay(write)
    assert res.complete and not sp.pending
    # 첫 묶음은 2행 들어가고 1행 거부, 둘째 묶음은 모두 거부
    assert (res.batches, res.rows, res.dead_batches, res.dead_rows) == (1, 2, 2, 3)
    assert res.errors == [
        "fut_ticks: 1/3행 CheckViolation(23514)",
        "fut_ticks: 2/2행 CheckViolation(23514)",
    ]
    lines = (tmp_path / DEAD / "fut_ticks.jsonl").read_bytes().splitlines(keepends=True)
    dead = [decode_batch(x) for x in lines]
    assert [b.rows for b in dead] == [[(1, "v1")], [(10, "v10"), (11, "v11")]]
    assert all((b.table, b.columns, b.tag, b.at) == ("fut_ticks", COLS, TAG, T0) for b in dead)


def test_truncated_line_from_a_crash_is_set_aside_and_the_rest_replays(tmp_path: Path) -> None:
    sp = DiskSpool(tmp_path)
    sp.append("fut_ticks", COLS, _rows(1, 0), TAG)
    sp.close()
    (seg,) = (tmp_path / "fut_ticks").glob("*.jsonl")
    with seg.open("ab") as f:
        f.write(b'{"v":1,"table":"fut_ticks","at":"2026')  # 쓰다 죽은 줄
    sp2 = DiskSpool(tmp_path)
    sp2.append("fut_ticks", COLS, _rows(1, 1), TAG)  # 새 세그먼트에 (끊긴 줄 뒤에 붙이지 않는다)
    got: list[int] = []
    res = sp2.replay(lambda b: got.append(int(b.rows[0][0])))
    assert got == [0, 1] and res.corrupt_lines == 1
    assert (tmp_path / DEAD / "_corrupt.jsonl").read_bytes().startswith(b'{"v":1,"table":"fut')


def test_restart_keeps_order_old_segments_before_new(tmp_path: Path) -> None:
    sp = DiskSpool(tmp_path)
    sp.append("minute_bars", COLS, _rows(1, 0), TAG)
    sp.close()
    sp2 = DiskSpool(tmp_path)
    assert sp2.pending
    sp2.append("minute_bars", COLS, _rows(1, 1), TAG)
    assert [r[0][0] for _, r in _collect(sp2)] == [0, 1]


def test_one_process_per_spool_directory(tmp_path: Path) -> None:
    sp = DiskSpool(tmp_path)
    with pytest.raises(SpoolError, match="다른 프로세스"):
        DiskSpool(tmp_path)
    sp.close()
    DiskSpool(tmp_path).close()  # 닫으면 다시 열 수 있다


def test_files_are_private(tmp_path: Path) -> None:
    sp = DiskSpool(tmp_path / "sp")
    sp.append("fut_ticks", COLS, _rows(1), TAG)
    (seg,) = (tmp_path / "sp" / "fut_ticks").glob("*.jsonl")
    assert stat.S_IMODE(seg.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "sp").stat().st_mode) == 0o700


def test_cap_drops_oldest_segments_and_reports_what_was_lost(tmp_path: Path) -> None:
    one = len(encode_batch("fut_ticks", COLS, _rows(3), TAG, T0))
    sp = DiskSpool(tmp_path, max_bytes=one * 3 + 10, segment_bytes=1, now=lambda: T0)
    for i in range(3):
        assert sp.append("fut_ticks", COLS, _rows(3, i * 3), TAG) == []
    drops = sp.append("fut_ticks", COLS, _rows(3, 9), TAG)
    (d,) = drops
    assert (d.reason, d.tables, d.batches, d.rows) == ("oldest", ("fut_ticks",), 1, 3)
    assert d.oldest_at == T0 == d.newest_at
    assert [r[0][0] for _, r in _collect(sp)] == [3, 6, 9]


def test_a_batch_bigger_than_the_cap_is_dropped_and_reported(tmp_path: Path) -> None:
    sp = DiskSpool(tmp_path, max_bytes=50)
    (d,) = sp.append("raw_messages", COLS, _rows(20), TAG)
    assert (d.reason, d.rows) == ("too_large", 20) and not sp.pending


def test_bad_table_names_are_refused(tmp_path: Path) -> None:
    sp = DiskSpool(tmp_path)
    for name in ("../etc", "_dead", "Raw", ""):
        with pytest.raises(SpoolError):
            sp.append(name, COLS, _rows(1), TAG)


def test_a_failed_append_leaves_no_half_line_and_the_next_batch_is_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sp = DiskSpool(tmp_path)
    sp.append("fut_ticks", COLS, _rows(1, 0), TAG)
    real = spool_mod._write_all

    def half(fd: int, data: bytes) -> None:
        real(fd, data[: len(data) // 2])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(spool_mod, "_write_all", half)
    with pytest.raises(SpoolError, match="OSError"):
        sp.append("fut_ticks", COLS, _rows(1, 1), TAG)
    monkeypatch.setattr(spool_mod, "_write_all", real)
    sp.append("fut_ticks", COLS, _rows(1, 2), TAG)
    sp.close()
    sp2 = DiskSpool(tmp_path)  # 다시 기동해도 깨진 줄이 없다
    got: list[int] = []
    res = sp2.replay(lambda b: got.append(int(b.rows[0][0])))
    assert got == [0, 2] and res.corrupt_lines == 0

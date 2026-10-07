"""raw 녹화 골든 (PLAN §6.3, docs/phase1_design.md §10 — scripts/make_golden.py `raw`·`raw-parse`).

- tests/golden/raw/<name>.jsonl(raw_messages 창을 자른 녹화)마다 파서 출력이 <name>.parsed.json 과
  같다. 파서(data.kis.ws 프레임·체결 틱, 체결 시각 변환, data.kis.models REST 모델)를 바꾸면 여기서
  걸린다 — 의도했으면 `raw-parse --write` 로 다시 만든다
- 지금 녹화는 합성 한 벌뿐이다(synthetic_20260928 — tests/golden/synthetic.py, 실제 녹화
  아님). 라이브 녹화(주간·야간 각 1일, Phase 1 소크)가 생기면 `raw --from-db` 로 잘라 넣고, 이
  시험이 그대로 돈다
- 자르기: 창 [start, end)·거르기·정렬, 가리기(체결통보 암호문·복호화 키·구독 키 HTS ID — 가릴
  모양이 아니면 거부), 비밀 같은 문자열·REDACTED 아닌 key·iv 값 거부,
  상한, JSONL 줄 오류, DB 입력(가짜 연결 — 실제 DB 는 tests/integration/test_make_golden_db.py),
  CLI
"""

from __future__ import annotations

import json
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

import scripts.make_golden as mg
from scripts.make_golden import (
    RAW_DIR,
    REDACTED,
    SECRET_PATTERNS,
    DbRawSource,
    InputError,
    RawRow,
    RawWindow,
    SecretFound,
    compare_golden,
    cut_raw,
    failure_report,
    main,
    raw_regenerate,
    raw_snapshot,
    read_jsonl,
    recording_text,
    sanitize,
    secret_hits,
    unredacted_key_iv,
)
from tests.golden.synthetic import (
    FAKE_AES_IV,
    FAKE_AES_KEY,
    FAKE_CIPHER,
    FAKE_HTS_ID,
    NAME,
    WINDOW_END,
    WINDOW_START,
    export_text,
    synthetic_export,
)
from tests.unit.test_fixtures_clean import PATTERNS

RECORDINGS = sorted(RAW_DIR.glob("*.jsonl"))
START, END = WINDOW_START.isoformat(), WINDOW_END.isoformat()


@pytest.fixture
def export(tmp_path: Path) -> Path:
    p = tmp_path / "raw_export.jsonl"
    p.write_text(export_text(), encoding="utf-8")
    return p


@pytest.fixture
def rows(export: Path) -> list[RawRow]:
    return read_jsonl(export)


def _cut_args(export: Path, out: Path, *extra: str) -> list[str]:
    base = ["raw", "--from-jsonl", str(export), "--start", START, "--end", END]
    return [*base, "--name", NAME, "--out-dir", str(out), *extra]


# ── 커밋된 녹화 골든 ─────────────────────────────────────────────────────────


def test_recordings_and_snapshots_come_in_pairs() -> None:
    assert NAME in {p.stem for p in RECORDINGS}
    snaps = {p.name.removesuffix(".parsed.json") for p in RAW_DIR.glob("*.parsed.json")}
    assert snaps == {p.stem for p in RECORDINGS}


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda p: p.stem)
def test_parser_output_matches_snapshot(path: Path) -> None:
    expected = json.loads(path.with_suffix(".parsed.json").read_text(encoding="utf-8"))
    actual = json.loads(json.dumps(raw_snapshot(path.stem, read_jsonl(path), expected["cut"])))
    diffs = compare_golden(expected, actual)
    assert not diffs, failure_report(diffs, raw_regenerate(path.stem))


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda p: p.stem)
def test_recording_is_canonical_sanitized_and_secret_free(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    recorded = read_jsonl(path)
    assert recording_text(recorded) == text  # 정규화된 줄 — 다시 잘라도 같다
    assert all(sanitize(r) == r for r in recorded)
    assert secret_hits(text) == []
    assert not any(unredacted_key_iv(r.body_text()) for r in recorded)
    assert [r.ts for r in recorded] == sorted(r.ts for r in recorded)


def test_synthetic_recording_is_reproducible(
    export: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """커밋된 합성 골든 = 합성 내보내기를 그 창으로 자른 것(다시 만드는 명령은 synthetic.py)."""
    rc = main(_cut_args(export, RAW_DIR))  # --write 없음 — 비교만
    assert rc == 0, capsys.readouterr().out


def test_synthetic_snapshot_covers_every_parser_branch() -> None:
    snap = json.loads((RAW_DIR / f"{NAME}.parsed.json").read_text(encoding="utf-8"))
    assert snap["counts"] == {
        "control": 3,
        "data": 1,
        "data_error": 1,
        "encrypted": 1,
        "error": 1,
        "raw_only": 1,
        "rest": 6,
        "ticks": 5,
        "unparsed": 2,
    }
    msgs = snap["messages"]
    ticks = [t for m in msgs if m["kind"] == "ticks" for t in m["ticks"]]
    assert {t["tr_id"] for t in ticks} == {"H0IFCNT0", "H0IOCNT0", "H0MFCNT0", "H0EUCNT0"}
    night = [t for t in ticks if t["hhmmss"] in ("003000", "243000")]
    assert {t["ts"] for t in night} == {"2026-09-28T15:30:00+00:00"}  # 00:30 KST 두 표기
    board = next(m for m in msgs if m["tr_id"] == "FHPIF05030100")
    assert [b["index"] for b in board["outputs"]["output1"]["bad"]] == [2]  # 행 단위 격리
    control = next(m for m in msgs if m["kind"] == "control" and m["output_keys"])
    assert control["output_keys"] == ["iv", "key"]
    text = json.dumps(snap, ensure_ascii=False)
    for secret in (FAKE_AES_KEY, FAKE_AES_IV, FAKE_CIPHER, FAKE_HTS_ID):
        assert secret not in text


# ── 자르기 ───────────────────────────────────────────────────────────────────


def test_window_is_half_open_filtered_and_sorted(rows: list[RawRow]) -> None:
    window = RawWindow(WINDOW_START, WINDOW_END)
    picked = cut_raw(rows, window)
    assert len(rows) == 24 and len(picked) == 21
    assert picked[0].ts == WINDOW_START  # 시작 시각은 든다
    assert all(WINDOW_START <= r.ts < WINDOW_END for r in picked)  # 끝 시각은 빠진다
    assert [r.ts for r in picked] == sorted(r.ts for r in picked)
    rest = cut_raw(rows, RawWindow(WINDOW_START, WINDOW_END, sources=("kis_rest",)))
    assert {r.source for r in rest} == {"kis_rest"} and len(rest) == 8
    night = cut_raw(rows, RawWindow(WINDOW_START, WINDOW_END, tr_ids=("H0MFCNT0",)))
    assert [r.session for r in night] == ["night", "night"]


def test_sanitize_hides_notice_keys_and_ciphertext(rows: list[RawRow]) -> None:
    picked = cut_raw(rows, RawWindow(WINDOW_START, WINDOW_END))
    text = recording_text(picked)
    for secret in (FAKE_AES_KEY, FAKE_AES_IV, FAKE_CIPHER, FAKE_HTS_ID):
        assert secret in export_text() and secret not in text
    assert "1|H0IFCNI0|001|" + REDACTED in text
    control = next(r for r in picked if r.tr_id == "H0IFCNI0" and r.key)
    frame = json.loads(control.payload_text or "")
    assert frame["body"]["output"] == {"iv": REDACTED, "key": REDACTED}
    assert (control.key, frame["header"]["tr_key"]) == (REDACTED, REDACTED)  # HTS ID
    market = next(
        r for r in picked if r.tr_id == "H0IFCNT0" and (r.payload_text or "").startswith("{")
    )
    assert market.key == "A01612" and '"tr_key": "A01612"' in (market.payload_text or "")
    untouched = [r for r in rows if r.source != "kis_ws"]
    assert all(sanitize(r) is r for r in untouched)


def _ws(text: str, tr_id: str = "H0IFCNI0", key: str = "", second: int = 10) -> RawRow:
    return RawRow.model_validate(
        {
            "ts": WINDOW_START + timedelta(seconds=second),
            "trade_date": "2026-09-28",
            "session": "day",
            "source": "kis_ws",
            "tr_id": tr_id,
            "key": key,
            "payload_text": text,
        }
    )


def _notice_control(tr_id: str = "H0IFCNI0") -> str:
    body = {"rt_cd": "0", "msg_cd": "OPSP0000", "msg1": "SUBSCRIBE SUCCESS"}
    return json.dumps(
        {
            "header": {"tr_id": tr_id, "tr_key": FAKE_HTS_ID, "encrypt": "N"},
            "body": body | {"output": {"iv": FAKE_AES_IV, "key": FAKE_AES_KEY}},
        }
    )


@pytest.mark.parametrize(
    "text",
    [
        _notice_control() + " x",  # JSON 이 아니다 — 파서도 못 읽어 키가 그대로 남던 자리
        '{"header": {"tr_id": "H0IFCNI0"}, "body": {"output": "key=' + FAKE_AES_KEY + '"}}',
        '{"header": {"tr_id": "H0IFCNI0"}, "body": ["' + FAKE_AES_KEY + '"]}',
        '{"header": "H0IFCNI0 ' + FAKE_HTS_ID + '"}',
    ],
    ids=["not_json", "output_not_object", "body_not_object", "header_not_object"],
)
def test_control_frame_that_cannot_be_sanitized_is_refused(
    text: str, export: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """가릴 모양이 아닌 제어 프레임은 통과시키지 않는다(fail closed) — 아무것도 쓰지 않는다."""
    row = _ws(text)
    with pytest.raises(SecretFound) as e:
        cut_raw([row], RawWindow(WINDOW_START, WINDOW_END))
    assert FAKE_AES_KEY not in str(e.value) and FAKE_HTS_ID not in str(e.value)
    with export.open("a", encoding="utf-8") as f:
        f.write(row.to_line() + "\n")
    out = tmp_path / "golden"
    assert main(_cut_args(export, out, "--write")) == 2
    assert "입력 오류" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize(
    "row",
    [
        _ws('[{"key": "' + FAKE_AES_KEY + '"}]', "H0IFCNT0"),  # JSON 제어 프레임 모양이 아님
        _ws("garbage " + '"iv":"' + FAKE_AES_IV + '"', "unknown"),
        RawRow.model_validate(
            {
                "ts": WINDOW_START,
                "source": "kis_rest",
                "tr_id": "FHZZZ0000000",
                "payload": {"rt_cd": "0", "output": {"iv": FAKE_AES_IV}},
            }
        ),
    ],
    ids=["ws_array", "ws_garbage", "rest"],
)
def test_key_or_iv_value_left_in_any_body_is_refused(row: RawRow) -> None:
    """가리기 규칙이 못 본 자리에 남은 `"key":"…"`·`"iv":"…"` 도 거부(값이 REDACTED 가 아니면)."""
    assert unredacted_key_iv(row.body_text())
    with pytest.raises(SecretFound, match="key"):
        cut_raw([row], RawWindow(WINDOW_START, WINDOW_END))
    assert not unredacted_key_iv('{"key":"REDACTED","iv": "REDACTED","tr_key":"A01612"}')


@pytest.mark.parametrize("tr_id", ["H0IFCNI0", "H0MFCNI0", "H0EUCNI0", "H0IFCNI9"])
def test_notice_subscriber_id_is_redacted(tr_id: str) -> None:
    """체결통보 TR 의 구독 키는 HTS ID — 행 key·header.tr_key·평문 프레임 본문을 가린다."""
    rows = [
        _ws(_notice_control(tr_id), tr_id, FAKE_HTS_ID, 10),
        _ws(_notice_control(tr_id), "", FAKE_HTS_ID, 11),  # 행 tr_id 를 모를 때 — 머리 tr_id 로
        _ws(f"0|{tr_id}|001|{FAKE_HTS_ID}^12345678^01", tr_id, FAKE_HTS_ID, 12),
    ]
    picked = cut_raw(rows, RawWindow(WINDOW_START, WINDOW_END))
    assert FAKE_HTS_ID not in recording_text(picked)
    assert [r.key for r in picked] == [REDACTED] * 3
    for r in picked[:2]:
        frame = json.loads(r.payload_text or "")
        assert frame["header"]["tr_key"] == REDACTED
        assert frame["body"]["output"] == {"iv": REDACTED, "key": REDACTED}
    assert picked[2].payload_text == f"0|{tr_id}|001|{REDACTED}"
    assert all(sanitize(r) == r for r in picked)  # 다시 가려도 같다
    assert FAKE_HTS_ID not in json.dumps(raw_snapshot("x", picked, {}))


def test_websocket_row_with_json_column_is_refused() -> None:
    row = _ws("x").model_copy(update={"payload": {"x": 1}, "payload_text": None})
    with pytest.raises(SecretFound, match="JSON 열"):
        sanitize(row)


def test_secret_like_text_is_refused_and_nothing_is_written(
    export: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    leak = synthetic_export()[0] | {
        "ts": (WINDOW_START + timedelta(seconds=40)).astimezone(UTC).isoformat(),
        "payload": {"rt_cd": "0", "access_token": "x"},
    }
    with export.open("a", encoding="utf-8") as f:
        f.write(json.dumps(leak) + "\n")
    with pytest.raises(SecretFound, match="access_token"):
        cut_raw(read_jsonl(export), RawWindow(WINDOW_START, WINDOW_END))
    out = tmp_path / "golden"
    assert main(_cut_args(export, out, "--write")) == 2
    assert "비밀 같은 문자열" in capsys.readouterr().err
    assert not out.exists()


def test_empty_window_and_row_cap(rows: list[RawRow]) -> None:
    early = WINDOW_START - timedelta(hours=1)
    with pytest.raises(InputError, match="행이 없다"):
        cut_raw(rows, RawWindow(early, early + timedelta(seconds=1)))
    with pytest.raises(InputError, match="3개를 넘는다"):
        cut_raw(rows, RawWindow(WINDOW_START, WINDOW_END, max_rows=3))


def test_window_needs_aware_ordered_times() -> None:
    naive = WINDOW_START.replace(tzinfo=None)
    with pytest.raises(InputError, match="naive"):
        RawWindow(naive, WINDOW_END)
    with pytest.raises(InputError, match="start < end"):
        RawWindow(WINDOW_END, WINDOW_START)


def test_bad_jsonl_line_names_the_line_not_the_content(tmp_path: Path) -> None:
    good = export_text().splitlines()[0]
    for bad, where in (
        (
            '{"ts": "2026-09-28T09:00:00", "source": "kis_ws", "tr_id": "X", '
            '"payload_text": "SECRETVALUE"}',
            "ts",
        ),
        (
            '{"ts": "2026-09-28T00:00:00Z", "source": "kis_ws", "tr_id": "X", '
            '"payload": {}, "payload_text": "SECRETVALUE"}',
            "(행)",
        ),
        (
            '{"ts": "2026-09-28T00:00:00Z", "source": "smtp", "tr_id": "X", '
            '"payload_text": "SECRETVALUE"}',
            "source",
        ),
    ):
        p = tmp_path / "x.jsonl"
        p.write_text(f"{good}\n\n{bad}\n", encoding="utf-8")
        with pytest.raises(InputError) as e:
            read_jsonl(p)
        assert "x.jsonl:3:" in str(e.value) and where in str(e.value)
        assert "SECRETVALUE" not in str(e.value)


def test_recording_round_trips_unusual_text(tmp_path: Path) -> None:
    """U+2028·탭·NUL 대체 문자도 한 줄 — 줄은 '\\n' 으로만 가른다."""
    row = RawRow.model_validate(
        {
            "ts": "2026-09-28T00:00:00.000001+00:00",
            "source": "kis_ws",
            "tr_id": "H0ZZZZZ0",
            "payload_text": "0|H0ZZZZZ0|001|a\u2028b\u2029c\u0085d\te\ufffd",
            "lossy": True,
        }
    )
    p = tmp_path / "r.jsonl"
    p.write_text(recording_text([row]), encoding="utf-8")
    assert read_jsonl(p) == [row]
    assert p.read_text(encoding="utf-8").count("\n") == 1


def test_parser_exception_is_isolated_per_message(
    rows: list[RawRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(row: RawRow) -> dict[str, Any]:
        raise RuntimeError(f"parser broke on {row.tr_id}")

    monkeypatch.setattr(mg, "_parse_rest", boom)
    snap = raw_snapshot("x", cut_raw(rows, RawWindow(WINDOW_START, WINDOW_END)), {})
    assert snap["counts"]["parser_exception"] == 8  # REST 행만
    assert snap["counts"]["ticks"] == 5  # 나머지는 그대로
    bad = next(m for m in snap["messages"] if m["kind"] == "parser_exception")
    assert bad["error"].startswith("RuntimeError: parser broke on")


def test_secret_patterns_cover_the_fixture_scan() -> None:
    mine = {(p.pattern, p.flags) for p in SECRET_PATTERNS}
    assert {(p.pattern, p.flags) for p in PATTERNS} <= mine


# ── DB 입력 (가짜 연결) ──────────────────────────────────────────────────────


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, query: object, params: object = None) -> None:
        self.conn.executed.append((query, params))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.conn.result


class _Conn:
    def __init__(self, result: list[tuple[Any, ...]]) -> None:
        self.result = result
        self.executed: list[tuple[object, object]] = []

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return _Cursor(self)


def _db_tuple(d: dict[str, Any]) -> tuple[Any, ...]:
    """row_to_json 줄 → DB 가 돌려줄 행 튜플(시각·날짜 형으로)."""
    r = RawRow.model_validate(d)
    head = (r.ts, r.trade_date, r.session, r.source, r.tr_id, r.key)
    return (*head, r.payload, r.payload_text, r.lossy)


def test_db_source_reads_the_window_with_filters(rows: list[RawRow]) -> None:
    in_window = [
        d for d in synthetic_export() if WINDOW_START <= RawRow.model_validate(d).ts < WINDOW_END
    ]
    conn = _Conn([_db_tuple(d) for d in in_window])
    window = RawWindow(WINDOW_START, WINDOW_END, ("kis_ws",), ("H0MFCNT0",), max_rows=50)
    got = DbRawSource(lambda: conn).read(window)  # pyright: ignore[reportArgumentType]
    assert recording_text(sorted(got, key=lambda r: r.to_line())) == recording_text(
        sorted((r for r in rows if WINDOW_START <= r.ts < WINDOW_END), key=lambda r: r.to_line())
    )
    (set_tz, _), (_, params) = conn.executed
    assert set_tz == "SET TIME ZONE 'UTC'"
    assert params == [WINDOW_START, WINDOW_END, ["kis_ws"], ["H0MFCNT0"], 51]


def test_db_source_hides_connection_errors() -> None:
    def refuse() -> Any:
        raise psycopg.OperationalError("connection failed: password=hunter2 host=db")

    with pytest.raises(InputError) as e:
        DbRawSource(refuse).read(RawWindow(WINDOW_START, WINDOW_END))
    assert str(e.value) == "raw_messages 를 읽지 못했다: OperationalError"


def test_db_source_needs_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoUrl:
        database_url = None

    monkeypatch.setattr("config.settings.Settings", NoUrl)
    with pytest.raises(InputError, match="DATABASE_URL"):
        DbRawSource.from_settings()


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_raw_writes_only_with_write_and_raw_parse_rechecks(
    export: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "golden"
    assert main(_cut_args(export, out)) == 1 and not out.exists()
    assert main(_cut_args(export, out, "--write")) == 0
    for suffix in (".jsonl", ".parsed.json"):
        committed = (RAW_DIR / f"{NAME}{suffix}").read_text(encoding="utf-8")
        assert (out / f"{NAME}{suffix}").read_text(encoding="utf-8") == committed
    assert main(_cut_args(export, out)) == 0
    assert main(["raw-parse", "--out-dir", str(out)]) == 0
    parsed = out / f"{NAME}.parsed.json"
    snap = json.loads(parsed.read_text(encoding="utf-8"))
    snap["messages"][3]["ticks"][0]["price"] = "1.00"
    parsed.write_text(json.dumps(snap), encoding="utf-8")
    capsys.readouterr()
    assert main(["raw-parse", "--out-dir", str(out), "--name", NAME]) == 1
    printed = capsys.readouterr().out
    assert "$.messages[3].ticks[0].price" in printed and raw_regenerate(NAME) in printed
    assert main(["raw-parse", "--out-dir", str(out), "--write"]) == 0
    assert parsed.read_text(encoding="utf-8") == (RAW_DIR / f"{NAME}.parsed.json").read_text(
        encoding="utf-8"
    )


def test_cli_raw_unknown_float_unit_writes_nothing(
    export: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """파서 출력에 단위 없는 float 가 생기면(파서 변경) 녹화도 스냅샷도 쓰지 않고 종료 코드 2."""
    monkeypatch.setattr(mg, "raw_snapshot", lambda *a: {"value": 1.0})
    out = tmp_path / "golden"
    assert main(_cut_args(export, out, "--write")) == 2
    assert "골든 스키마 오류" in capsys.readouterr().err
    assert not out.exists()


def test_cli_raw_rejects_bad_arguments(export: Path, tmp_path: Path) -> None:
    out = tmp_path / "golden"
    args = _cut_args(export, out)
    assert main([*args[:-4], "--name", "../evil", "--out-dir", str(out)]) == 2
    with pytest.raises(SystemExit) as e:
        main(
            [
                "raw",
                "--from-jsonl",
                str(export),
                "--start",
                "2026-09-28T09:00:00",
                "--end",
                END,
                "--name",
                "x",
            ]
        )
    assert e.value.code == 2  # 시간대 없는 시각 — argparse
    assert main(["raw-parse", "--out-dir", str(tmp_path / "empty")]) == 1  # 녹화 없음

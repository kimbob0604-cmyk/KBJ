"""옛 DDL 로 만든 **합성** SQLite·JSON → 메모리 대상 이관(kbj/store/legacy_import — DB 없이).

원본 DDL 은 글자 그대로(ET board/engine/db.py·board/us/db.py, SD db/schema.sql·migrations/004·005),
값은 시드 고정 합성(tests/fixtures/synthetic/sqlite/make_legacy_db.py — 실데이터 아님). 확인하는 것:
- 원본은 읽기 전용: 쓰기는 거부되고, 이관 뒤에도 파일 내용·수정 시각이 그대로, WAL 파일도 안 생긴다
- 행 수: `후보 − 버림 = 대상에서 이 원본 행 수`, 키 다이제스트·값 합 일치, 우선순위(board.db 가
  backtest.db 보다, monitor/kr 캐시가 ET stockflows·SD flow_cache 보다 앞) — 겹친 키는
  `other_origin`
- 멱등: 두 번째 실행은 모든 매핑 `씀 0`, 다이제스트 같음. verify-only 는 쓰지 않고 대조만
- 불일치면 그 매핑만 되돌리고 `mismatch` 기록, 한 매핑의 예외가 나머지를 멈추지 않는다
- 보고 문장에 값·키(종목코드·이름·티커·본문)가 없다. CLI dry-run·인자 오류 종료 코드
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from kbj.store.legacy_import import ImportReport, MappingResult, run
from kbj.store.legacy_import import __main__ as cli
from kbj.store.legacy_import.mappings import TableMapping
from kbj.store.legacy_import.sources import (
    SourceError,
    file_sha256,
    open_sqlite_ro,
    refuse_token_cache,
)
from kbj.store.legacy_import.targets import MemoryTarget
from kbj.store.legacy_import.verify import Observed
from tests.fixtures.synthetic.sqlite import make_legacy_db as synth

NOW = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)
SQLITE = ("sd", "board", "backtest", "us", "us_backtest")


def clock() -> datetime:
    return NOW


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    return synth.build_all(tmp_path / "legacy")


def _by_name(report: ImportReport) -> dict[str, MappingResult]:
    return {r.mapping: r for r in report.results}


def _run(paths: dict[str, Path], target: MemoryTarget, **kw: object) -> ImportReport:
    return run(paths, target, phase="P2", now=clock, batch_id="b-test", **kw)  # type: ignore[arg-type]


# ── 원본은 읽기 전용 ──


def test_sources_open_read_only_and_stay_untouched(paths: dict[str, Path]) -> None:
    before = {k: (file_sha256(p), p.stat().st_mtime_ns) for k, p in paths.items() if k in SQLITE}
    conn = open_sqlite_ro(paths["board"])
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO meta VALUES ('x', 'y')")
    conn.close()
    _run(paths, MemoryTarget())
    after = {k: (file_sha256(p), p.stat().st_mtime_ns) for k, p in paths.items() if k in SQLITE}
    assert before == after
    leftovers = [
        p.name for p in paths["board"].parent.iterdir() if p.suffix in (".db-wal", ".db-shm")
    ]
    assert leftovers == []


def test_missing_files_and_token_caches_are_refused(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="없다"):
        open_sqlite_ro(tmp_path / "nope.db")
    assert not (tmp_path / "nope.db").exists()  # 없는 파일을 만들지 않는다
    for name in ("kis_token.json", ".kis_token.json", "kis.token.json"):
        with pytest.raises(SourceError, match="토큰"):
            refuse_token_cache(tmp_path / name)


# ── 첫 실행: 수·우선순위·검증 ──


def test_first_run_counts_and_verifies_every_mapping(paths: dict[str, Path]) -> None:
    target = MemoryTarget()
    report = _run(paths, target)
    assert report.ok, report.lines()
    res = _by_name(report)
    assert set(res) == {
        "sd.ops_state", "sd.fetch_progress", "board.meta", "us.meta", "board.run_log",
        "us.run_log", "sd.index_universe", "board.snap", "us.snap", "board.px", "backtest.px",
        "us.px", "us_backtest.px", "kr.flows", "board.stockflows", "sd.flow_cache", "board.inbox",
    }  # fmt: skip
    for r in report.results:
        rep = r.report
        assert r.status == "ok" and rep is not None and rep.ok, r.line()
        assert r.candidates - r.rows_dropped == rep.observed_count, r.mapping
    # 버림 사유·우선순위
    assert dict(res["board.px"].drops) == {
        "naver": 3, "no_source": 1, "volume_not_integer": 1, "bad_date": 1,
    }  # fmt: skip
    assert dict(res["backtest.px"].drops) == {"naver": 5, "other_origin": 2}
    assert res["backtest.px"].rows_written == 5  # board.db 에 없는 앞 날짜만
    assert dict(res["board.stockflows"].drops) == {"dup_key": 3, "naver": 1, "other_origin": 3}
    assert res["board.stockflows"].rows_written == 2
    assert dict(res["sd.flow_cache"].drops) == {"naver": 1, "array_length": 1, "other_origin": 4}
    assert res["sd.flow_cache"].rows_written == 2
    assert dict(res["board.run_log"].drops) == {"dup_key": 1, "no_asof": 1}
    assert dict(res["sd.ops_state"].drops) == {"secret_like_key": 1}
    assert dict(res["board.inbox"].drops) == {"no_update_id": 1}
    # 기록 한 줄씩(값·키 없음)
    assert [r.mapping for r in target.records] == [r.mapping for r in report.results]
    assert all(r.status == "ok" and r.batch_id == "b-test" for r in target.records)
    rec = next(r for r in target.records if r.mapping == "board.px")
    assert (rec.rows_read, rec.rows_dropped, rec.rows_written) == (23, 6, 17)
    assert rec.key_digest_src == rec.key_digest_dst and set(rec.sums) == {
        "open", "high", "low", "close", "volume",
    }  # fmt: skip
    assert rec.source_sha256 == file_sha256(paths["board"])


def test_board_rows_win_over_backtest_and_codes_keep_leading_zeros(
    paths: dict[str, Path],
) -> None:
    target = MemoryTarget()
    _run(paths, target)
    bars = target.table("prv_market.daily_bar")
    overlap = bars["KR|stock|990010|2026-10-02|krx|KRX"]
    assert overlap["loaded_by"] == "legacy:board.px"
    old = bars["KR|stock|990010|2026-09-21|krx|KRX"]
    assert old["loaded_by"] == "legacy:backtest.px" and old["adjusted"] is False
    padded = bars["KR|stock|009900|2026-09-28|datago|"]
    assert padded["code"] == "009900" and padded["venue"] == ""
    snaps = target.table("prv_market.stock_snapshot")
    s = snaps["KR|990010|2026-10-02|krx|KRX"]
    assert isinstance(s["turnover"], int) and s["turnover"] % 100 == 0  # 억원 → 원 정수
    assert snaps["KR|990040|2026-10-02|krx|KRX"]["quality"] == "invalid"  # 종가 없음
    flows = target.table("prv_flows.stock_investor_daily")
    k = "990010|2026-10-01|institution|kis|"
    assert flows[k]["loaded_by"] == "legacy:kr.flows" and flows[k]["net_value"] == 1_250_000_000
    assert flows["990010|2026-09-30|foreign|kis|"]["quality"] == "estimated"  # SD 추정
    kv = target.table("ops.kv")
    assert kv["sd.fetch_progress|005930"]["value"] != {}  # 숫자로 저장된 코드에 0 을 채웠다
    assert "sd|telegram_token_hint" not in kv and "board.kr|kis_token_cache" not in kv
    inbox = target.table("prv_alerts.tg_inbox")
    assert sorted(inbox) == ["900001", "900002"]


# ── 멱등·verify-only ──


def test_second_run_writes_nothing_and_digests_match(paths: dict[str, Path]) -> None:
    target = MemoryTarget()
    first = _run(paths, target)
    snapshot = {t: dict(rows) for t, rows in target.tables.items()}
    second = run(paths, target, phase="P2", now=clock, batch_id="b-again")
    assert second.ok
    for a, b in zip(first.results, second.results, strict=True):
        assert b.rows_written == 0, b.line()
        assert a.report is not None and b.report is not None
        assert a.report.digest_dst == b.report.digest_dst
    assert {t: dict(rows) for t, rows in target.tables.items()} == snapshot


def test_verify_only_compares_without_writing(paths: dict[str, Path]) -> None:
    target = MemoryTarget()
    empty = run(paths, target, phase="P2", now=clock, verify_only=True)
    assert not empty.ok and all(r.status == "mismatch" for r in empty.results)
    assert target.tables == {} or all(not rows for rows in target.tables.values())
    assert target.records == []  # verify-only 는 기록도 남기지 않는다
    _run(paths, target)
    n_records = len(target.records)
    checked = run(paths, target, phase="P2", now=clock, verify_only=True)
    assert checked.ok and {r.status for r in checked.results} == {"verified"}
    assert len(target.records) == n_records
    # 대상 값을 바꾸면 합이 어긋난다
    bars = target.table("prv_market.daily_bar")
    bars["KR|stock|990010|2026-10-02|krx|KRX"]["close"] = Decimal(1)
    tampered = _by_name(run(paths, target, phase="P2", now=clock, verify_only=True))
    assert tampered["board.px"].status == "mismatch"
    rep = tampered["board.px"].report
    assert rep is not None and rep.digest_ok and not rep.sums_ok
    assert tampered["us.px"].status == "verified"


# ── 실패 격리 ──


class _LyingTarget(MemoryTarget):
    """한 표의 observe 가 한 행 적게 센다(검증 불일치를 흉내 낸다)."""

    def observe(self, m: TableMapping) -> Observed:
        got = super().observe(m)
        if m.name == "board.snap":
            return Observed(got.count - 1, got.digest, got.sums)
        return got


class _BrokenTarget(MemoryTarget):
    def merge(self, m: TableMapping) -> int:
        if m.name == "board.meta":
            raise RuntimeError("synthetic failure")
        return super().merge(m)


def test_a_mismatch_rolls_back_only_that_mapping_and_is_recorded(paths: dict[str, Path]) -> None:
    target = _LyingTarget()
    report = _run(paths, target)
    res = _by_name(report)
    assert not report.ok and res["board.snap"].status == "mismatch"
    assert res["board.snap"].rows_written == 0
    assert not any(r.get("loaded_by") == "legacy:board.snap"
                   for r in target.table("prv_market.stock_snapshot").values())  # fmt: skip
    assert res["us.snap"].status == "ok"  # 다른 매핑은 계속
    statuses = {r.mapping: r.status for r in target.records}
    assert statuses["board.snap"] == "mismatch" and statuses["us.snap"] == "ok"
    assert "행 수" in res["board.snap"].line()


def test_an_exception_in_one_mapping_does_not_stop_the_rest(paths: dict[str, Path]) -> None:
    target = _BrokenTarget()
    report = _run(paths, target)
    res = _by_name(report)
    assert res["board.meta"].status == "failed" and res["board.meta"].reason == "RuntimeError"
    assert "board.kr|data_version" not in target.table("ops.kv")
    assert all(r.status == "ok" for n, r in res.items() if n != "board.meta")
    assert {r.mapping: r.status for r in target.records}["board.meta"] == "failed"


def test_a_source_without_the_table_is_skipped(paths: dict[str, Path]) -> None:
    report = run({"sd": paths["board"]}, MemoryTarget(), phase="P2", now=clock)
    assert {r.status for r in report.results} == {"skipped"}
    assert all("표가 없다" in r.line() for r in report.results)


# ── 보고·CLI ──

_SECRETISH: tuple[str, ...] = (
    "990010", "009900", "합성", "ZZA", "Synthetic", "900001", "x.com", "watchdog_alerted",
    "data_version", "SYNTHETIC-NOT-A-TOKEN", "2800종목",
)  # fmt: skip


def test_report_lines_carry_no_values_or_keys(paths: dict[str, Path]) -> None:
    lines = _run(paths, MemoryTarget()).lines()
    text = "\n".join(lines)
    for s in _SECRETISH:
        assert s not in text, s
    assert any("board.px → prv_market.daily_bar: 읽음 23 · 버림 6(" in x for x in lines)
    assert all("키 다이제스트 일치" in x for x in lines[1:])


def test_cli_dry_run_needs_no_database(
    paths: dict[str, Path], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("KBJ_DATABASE_URL", raising=False)
    code = cli.main(["--dry-run", "--phase", "P2", "--source", f"board={paths['board']}"])
    out = capsys.readouterr().out
    assert code == 0
    assert "board.px → prv_market.daily_bar: 읽음 23 · 버림 6(" in out
    assert "dry-run — 17 예정" in out and "990010" not in out


@pytest.mark.parametrize(
    "argv",
    [
        ["--dry-run", "--phase", "P2", "--source", "nope=x.db"],
        ["--dry-run", "--phase", "P3", "--source", "board=__BOARD__"],
        ["--dry-run", "--phase", "P2"],
        ["--dry-run", "--phase", "P2", "--source", "board"],
        ["--dry-run", "--phase", "P2", "--json", "inbox=__DIR__/kis_token.json"],
    ],
)
def test_cli_argument_errors_exit_2(
    argv: list[str], paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fixed = [
        a.replace("__BOARD__", str(paths["board"])).replace("__DIR__", str(paths["board"].parent))
        for a in argv
    ]
    assert cli.main(fixed) == 2
    assert "오류" in capsys.readouterr().err


def test_cli_rejects_dry_run_with_verify_only(paths: dict[str, Path]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(
            ["--dry-run", "--verify-only", "--phase", "P2", "--source", f"board={paths['board']}"]
        )
    assert e.value.code == 2


def test_cli_without_database_url_exits_2(
    paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    monkeypatch.chdir(tmp_path)  # 레포 .env 를 읽지 않게
    monkeypatch.delenv("KBJ_DATABASE_URL", raising=False)
    assert cli.main(["--phase", "P2", "--source", f"board={paths['board']}"]) == 2
    assert "KBJ_DATABASE_URL" in capsys.readouterr().err


def test_generator_is_deterministic(tmp_path: Path) -> None:
    a = synth.build_all(tmp_path / "a")
    b = synth.build_all(tmp_path / "b")
    for name in ("inbox", "krflows"):
        assert a[name].read_bytes() == b[name].read_bytes()
    for name in SQLITE:
        ca, cb = sqlite3.connect(a[name]), sqlite3.connect(b[name])
        try:
            dump = [list(ca.iterdump()), list(cb.iterdump())]
        finally:
            ca.close()
            cb.close()
        assert dump[0] == dump[1], name
    assert synth.board_px_rows()[0][1] == "2026-09-28"
    assert date.fromisoformat(synth.DAYS[-1]).weekday() == 4  # 금요일 — 거래일 범위


def test_a_broken_transform_fails_only_its_mapping(
    paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import dataclasses

    import kbj.store.legacy_import as li

    def boom(_: dict[str, object]) -> list[object]:
        raise TypeError("synthetic transform bug")

    broken = tuple(
        dataclasses.replace(m, transform=boom) if m.name == "board.snap" else m  # type: ignore[arg-type]
        for m in li.MAPPINGS
    )
    monkeypatch.setattr(li, "for_phase", lambda phase: broken if phase == "P2" else ())
    target = MemoryTarget()
    res = _by_name(_run(paths, target))
    assert (
        res["board.snap"].status == "failed" and "변환 실패(TypeError)" in res["board.snap"].line()
    )
    assert all(r.status == "ok" for n, r in res.items() if n != "board.snap")
    assert {r.mapping: r.status for r in target.records}["board.snap"] == "failed"

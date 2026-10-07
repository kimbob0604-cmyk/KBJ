"""합성 옛 SQLite·JSON → 실제 Postgres 이관(docs/p2_design.md §8.6·§8.7 — Docker).

- 마이그레이션한 빈 DB 에 이관: 모든 매핑 검증 일치, 메모리 대상 실행과 버림·씀·다이제스트가 같다
  (같은 변환·같은 우선순위 — COPY → INSERT … ON CONFLICT 경로가 메모리 의미와 같다)
- 두 번째 실행은 모든 매핑 `씀 0`, 다이제스트 같음, ops.legacy_import 는 배치마다 매핑 수만큼
- 값 왕복: 억원 → 원 정수, 앞자리 0 코드, jsonb 문자열·객체, text[] 배열, 오프셋 없는 시각은 NULL
- verify-only: 쓰지 않고 대조, 대상 값을 바꾸면 그 매핑만 불일치
- CLI: KBJ_DATABASE_URL 로 붙어 종료 0, 출력에 비밀번호·값 없음
컨테이너는 tests/integration/conftest.py.
합성 원본은 tests/fixtures/synthetic/sqlite/make_legacy_db.py.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest

from kbj.store.legacy_import import ImportReport, run
from kbj.store.legacy_import import __main__ as cli
from kbj.store.legacy_import.mappings import MAPPINGS
from kbj.store.legacy_import.sources import file_sha256
from kbj.store.legacy_import.targets import MemoryTarget, PgTarget
from kbj.store.migrate import migrate
from tests.fixtures.synthetic.sqlite import make_legacy_db as synth
from tests.integration.conftest import PgContainer

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)


def clock() -> datetime:
    return NOW


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return synth.build_all(tmp_path_factory.mktemp("legacy"))


@pytest.fixture
def dsn(timescale: PgContainer) -> str:
    url = timescale.dsn(timescale.fresh_database("imp"))
    migrate(url)
    return url


def _pg_run(dsn: str, paths: dict[str, Path], batch: str, **kw: Any) -> ImportReport:
    with psycopg.connect(dsn, autocommit=True) as conn:
        return run(paths, PgTarget(conn), phase="P2", now=clock, batch_id=batch, **kw)


def _rows(dsn: str, query: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as c:
        return c.execute(query.encode(), params or None).fetchall()


def _summary(report: ImportReport) -> list[tuple[object, ...]]:
    return [
        (r.mapping, r.status, dict(r.drops), r.rows_written,
         r.report.digest_dst if r.report else None,
         {c: b for c, (_, b) in r.report.sums.items()} if r.report else None)
        for r in report.results
    ]  # fmt: skip


def test_import_matches_the_memory_run_and_is_idempotent(dsn: str, paths: dict[str, Path]) -> None:
    first = _pg_run(dsn, paths, "b1")
    assert first.ok, first.lines()
    mem = run(paths, MemoryTarget(), phase="P2", now=clock, batch_id="b1")
    assert _summary(first) == _summary(mem)
    assert len(first.results) == len(MAPPINGS)
    second = _pg_run(dsn, paths, "b2")
    assert second.ok and all(r.rows_written == 0 for r in second.results), second.lines()
    for a, b in zip(first.results, second.results, strict=True):
        assert a.report is not None and b.report is not None
        assert a.report.digest_dst == b.report.digest_dst
    logs = _rows(
        dsn, "SELECT batch_id, status, count(*) FROM ops.legacy_import GROUP BY 1, 2 ORDER BY 1"
    )
    assert logs == [("b1", "ok", len(MAPPINGS)), ("b2", "ok", len(MAPPINGS))]
    sha = _rows(
        dsn,
        "SELECT source_sha256 FROM ops.legacy_import WHERE batch_id='b1' AND mapping='board.px'",
    )
    assert sha == [(file_sha256(paths["board"]),)]


def test_values_round_trip_with_their_types(dsn: str, paths: dict[str, Path]) -> None:
    assert _pg_run(dsn, paths, "v1").ok
    snap = _rows(
        dsn,
        "SELECT turnover, mktcap, venue, quality, loaded_by FROM prv_market.stock_snapshot "
        "WHERE market = 'KR' AND code = '990040'",
    )
    assert snap == [(None, Decimal(51_225_000_000), "KRX", "invalid", "legacy:board.snap")]
    bar = _rows(
        dsn,
        "SELECT code, source, venue, adjusted, volume FROM prv_market.daily_bar "
        "WHERE code = '009900' ORDER BY trade_date LIMIT 1",
    )
    assert bar[0][:4] == ("009900", "datago", "", False) and isinstance(bar[0][4], int)
    kv = dict(_rows(dsn, "SELECT namespace || '|' || key, value FROM ops.kv"))
    assert kv["board.kr|data_version"] == "3"  # jsonb 문자열 그대로
    assert isinstance(kv["sd.fetch_progress|005930"], dict)
    assert not any("token" in k for k in kv)
    job = _rows(
        dsn,
        "SELECT finished_at, detail->>'ts_raw', source FROM ops.job_run "
        "WHERE job = 'legacy.board.kr' AND detail->>'step' = 'px'",
    )
    assert job == [(None, "2026-10-02 15:42:00", "legacy_import")]
    inbox = _rows(
        dsn, "SELECT update_id, urls, x_ids, kind, text_via FROM prv_alerts.tg_inbox ORDER BY 1"
    )
    assert inbox[0][1:4] == (
        ["https://x.com/synthetic_acct/status/1990000000000000001"],
        ["1990000000000000001"],
        "x",
    )
    assert inbox[1][1:] == (["https://example.invalid/report"], [], "other", None)
    flows = _rows(
        dsn,
        "SELECT net_value, quality, loaded_by FROM prv_flows.stock_investor_daily "
        "WHERE code = '990010' AND trade_date = %s AND investor = 'institution'",
        date(2026, 10, 1),
    )
    assert flows == [(1_250_000_000, "ok", "legacy:kr.flows")]


def test_verify_only_and_tampering(dsn: str, paths: dict[str, Path]) -> None:
    assert _pg_run(dsn, paths, "t1").ok
    before = _rows(dsn, "SELECT count(*) FROM ops.legacy_import")
    checked = _pg_run(dsn, paths, "t2", verify_only=True)
    assert checked.ok and {r.status for r in checked.results} == {"verified"}
    assert _rows(dsn, "SELECT count(*) FROM ops.legacy_import") == before  # 쓰지 않는다
    with psycopg.connect(dsn) as c:
        c.execute(
            "UPDATE prv_market.daily_bar SET close = 1 "
            "WHERE code = '990010' AND trade_date = '2026-10-02'"
        )
    tampered = {r.mapping: r for r in _pg_run(dsn, paths, "t3", verify_only=True).results}
    assert tampered["board.px"].status == "mismatch"
    rep = tampered["board.px"].report
    assert rep is not None and rep.digest_ok and not rep.sums_ok
    assert all(r.status == "verified" for n, r in tampered.items() if n != "board.px")


def test_cli_imports_with_the_settings_url(
    dsn: str,
    paths: dict[str, Path],
    timescale: PgContainer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)  # 레포 .env 를 읽지 않게
    monkeypatch.setenv("KBJ_DATABASE_URL", dsn)
    argv = ["--phase", "P2", "--source", f"board={paths['board']}", "--source",
            f"backtest={paths['backtest']}", "--json", f"inbox={paths['inbox']}"]  # fmt: skip
    assert cli.main(argv) == 0
    first = capsys.readouterr().out
    assert cli.main(argv) == 0
    second = capsys.readouterr().out
    assert "board.px → prv_market.daily_bar: 읽음 23 · 버림 6(" in first and "씀 17" in first
    assert "backtest.px → prv_market.daily_bar" in second and "씀 0" in second
    assert "씀 17" not in second
    for text in (first, second):
        assert timescale.password not in text and "990010" not in text and "합성" not in text
    assert cli.main([*argv, "--verify-only"]) == 0

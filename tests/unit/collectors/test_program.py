"""`program.*` 처리기 — 가짜 KIS + 메모리 저장소(P5 F2 §3.4·§4).

시장 장중(매분 — 슬롯 ts·검산 ④)·일별·백필(투자자별 당일 TR 은 지난 날을 부르지 않는다)·종목 장중
훑기(거래대금 상위 N + 관심종목, 재시도는 남은 종목만)·마감 전 종목 훑기(종목 단위 선점 — 끊기면
이어서).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from kbj.core.fin_rows import WatchItem
from kbj.core.quality import Quality
from kbj.core.rows import RankRow
from kbj.data.spec import DataKey
from kbj.services.collectors import program
from kbj.services.scheduler.claims import MemoryClaimStore
from tests.fakes.kis_server import SYMBOLS, FakeKisServer
from tests.unit.collectors.p3_fakes import Clock, DirectKis, ctx, kst, new_repos, seed_universe

DAY = date(2026, 11, 17)
PAST = date(2026, 11, 13)


def setup(clock_at: datetime, hook: Any = None) -> tuple[Any, FakeKisServer, Clock, DirectKis]:
    repos = new_repos()
    clock = Clock(clock_at)
    server = FakeKisServer(clock)
    return repos, server, clock, DirectKis(server, hook=hook)


def k(dataset: str, as_of: str, venue: str = "KRX") -> DataKey:
    return DataKey("KIS", dataset, as_of, venue)


# ── 시장 장중 ─────────────────────────────────────────────────────────────────────────


def test_intraday_writes_nine_rows_per_market_at_the_slot() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 10, 30, 5))
    key = k("program_market_intraday", "2026-11-17T10:30")
    res = program.intraday(
        ctx("program.intraday", key.as_of, [key], now=clock(), repos=repos, kis=kis)
    )
    assert res.status == "ok", res.detail
    for m in ("K", "Q"):
        rows = repos.program.market_minutes(DAY, m, "KRX")
        assert len(rows) == 9
        assert {(r.ts, r.quality) for r in rows} == {(kst(2026, 11, 17, 10, 30), Quality.OK)}
    assert kis.count("FHPPG04600101") == 2
    assert "check4_failed" not in res.detail


def test_intraday_check4_failure_writes_invalid() -> None:
    def broken(tr: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if tr == "FHPPG04600101" and params["FID_MRKT_CLS_CODE"] == "Q":
            rows = [dict(r) for r in body["output"]]
            for r in rows:
                r["whol_smtn_ntby_tr_pbmn"] = str(int(r["whol_smtn_ntby_tr_pbmn"]) + 1)
            return {**body, "output": rows}
        return body

    repos, _s, clock, kis = setup(kst(2026, 11, 17, 10, 31, 5), hook=broken)
    key = k("program_market_intraday", "2026-11-17T10:31")
    res = program.intraday(
        ctx("program.intraday", key.as_of, [key], now=clock(), repos=repos, kis=kis)
    )
    assert res.status == "ok"
    assert {r.quality for r in repos.program.market_minutes(DAY, "Q", "KRX")} == {Quality.INVALID}
    assert {r.quality for r in repos.program.market_minutes(DAY, "K", "KRX")} == {Quality.OK}
    assert res.detail["check4_failed"] >= 1


def test_intraday_venue_off_is_not_called() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 10, 30, 5))
    keys = [k("program_market_intraday", "2026-11-17T10:30", "NXT")]
    res = program.intraday(
        ctx("program.intraday", keys[0].as_of, keys, now=clock(), repos=repos, kis=kis)
    )
    assert res.status == "ok" and res.detail["venues_off"] == ["NXT"]
    assert kis.count() == 0


def test_intraday_preopen_is_not_ready() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 8, 50, 5))
    key = k("program_market_intraday", "2026-11-17T08:50")
    res = program.intraday(
        ctx("program.intraday", key.as_of, [key], now=clock(), repos=repos, kis=kis)
    )
    assert res.status == "not_ready"


def test_intraday_one_market_failure_is_isolated() -> None:
    repos, server, clock, kis = setup(kst(2026, 11, 17, 10, 32, 5))
    server.inject("HTTP500", 1, tr_id="FHPPG04600101")
    key = k("program_market_intraday", "2026-11-17T10:32")
    res = program.intraday(
        ctx("program.intraday", key.as_of, [key], now=clock(), repos=repos, kis=kis)
    )
    assert res.status == "failed"
    assert len(repos.program.market_minutes(DAY, "Q", "KRX")) == 9  # 코스닥은 받았다


# ── 일별·백필 ────────────────────────────────────────────────────────────────────────


def daily_keys(day: date) -> list[DataKey]:
    return [
        k("program_market_daily", day.isoformat()),
        k("program_investor_today", day.isoformat()),
    ]


def test_daily_writes_market_days_and_investors() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 15, 50, 5))
    keys = daily_keys(DAY)
    res = program.daily(
        ctx("program.daily", DAY.isoformat(), keys, now=clock(), repos=repos, kis=kis)
    )
    assert res.status == "ok", res.detail
    rows = repos.program.market_days("K", "KRX", DAY, 60)
    assert len(rows) == 9 and {r.trade_date for r in rows} == {DAY}
    inv = repos.program.investor_days(DAY, "Q", "KRX")
    assert len(inv) == 12 * 3
    assert set(res.collected) == set(keys)


def test_backfill_past_day_skips_the_today_only_investor_tr() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 19, 0))
    keys = daily_keys(PAST)
    res = program.backfill(
        ctx("program.backfill", PAST.isoformat(), keys, now=clock(), repos=repos, kis=kis)
    )
    assert res.status == "ok", res.detail
    assert len(repos.program.market_days("Q", "KRX", PAST, 1)) == 9
    assert repos.program.investor_days(PAST, "K", "KRX") == []
    assert res.detail["investor_today_past"] == PAST.isoformat()
    assert kis.count("HHPPG046600C1") == 0


def test_daily_without_the_day_row_is_not_ready() -> None:
    def no_today(tr: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if tr == "FHPPG04600001":
            return {
                **body,
                "output": [r for r in body["output"] if r["stck_bsop_date"] != "20261117"],
            }
        return body

    repos, _s, clock, kis = setup(kst(2026, 11, 17, 15, 50, 5), hook=no_today)
    keys = [k("program_market_daily", DAY.isoformat())]
    res = program.daily(
        ctx("program.daily", DAY.isoformat(), keys, now=clock(), repos=repos, kis=kis)
    )
    assert res.status == "not_ready"
    assert repos.program.market_days("K", "KRX", DAY, 5) == []


# ── 종목 장중 훑기 ────────────────────────────────────────────────────────────────────


def seed_scan(repos: Any) -> None:
    seed_universe(repos, DAY)
    t = kst(2026, 11, 17, 10, 0)
    ranks = [
        RankRow(mkt, t, i + 1, "KRX", c, None, 1_000 - i, 0.1, "kis.prelim", Quality.ESTIMATED)
        for mkt, codes in (("KOSPI", SYMBOLS[:10]), ("KOSDAQ", SYMBOLS[10:]))
        for i, c in enumerate(codes)
    ]
    repos.market.put_rank_rows(ranks, loaded_by="test", received_at=t)
    items = [WatchItem("main", c, None, None, i) for i, c in enumerate((SYMBOLS[9], SYMBOLS[19]))]
    repos.journal.replace_watchlist("main", items, loaded_by="test", now=t)


def test_scan_targets_top_n_plus_watch() -> None:
    repos = new_repos()
    seed_scan(repos)
    ts = kst(2026, 11, 17, 10, 5)
    got = program.scan_targets(repos, DAY, ts, "K", top_n=3, watch_max=20)
    assert got == [*SYMBOLS[:3], SYMBOLS[9]]  # 상위 3 + 관심종목(코스피 것만)
    assert program.scan_targets(repos, DAY, ts, "Q", top_n=2, watch_max=0) == list(SYMBOLS[10:12])


def test_scan_writes_estimated_rows_and_retry_fetches_only_missing() -> None:
    repos, server, clock, kis = setup(kst(2026, 11, 17, 10, 5, 3))
    seed_scan(repos)
    key = k("program_stock_intraday", "2026-11-17T10:00")
    server.inject("HTTP500", 2, tr_id="FHPPG04650101")
    res = program.scan(
        ctx("program.stock_scan", key.as_of, [key], now=clock(), repos=repos, kis=kis)
    )
    assert res.status == "failed" and res.detail["failed"] == 2
    ts = kst(2026, 11, 17, 10, 5)  # 슬롯 + 5분(등록부 open+5)
    have = repos.program.stock_scan(DAY, "K", "KRX", ts) + repos.program.stock_scan(
        DAY, "Q", "KRX", ts
    )
    assert len(have) == 20 - 2
    assert {(r.scope, r.quality) for r in have} == {("scan", Quality.ESTIMATED)}
    before = kis.count("FHPPG04650101")
    res2 = program.scan(
        ctx("program.stock_scan", key.as_of, [key], now=clock(), repos=repos, kis=kis, attempt=2)
    )
    assert res2.status == "ok", res2.detail
    assert kis.count("FHPPG04650101") - before == 2
    assert res2.detail["scope"].startswith("거래대금 상위")


def test_scan_without_ranks_or_watch_is_not_ready() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 9, 5, 3))
    key = k("program_stock_intraday", "2026-11-17T09:00")
    res = program.scan(
        ctx("program.stock_scan", key.as_of, [key], now=clock(), repos=repos, kis=kis)
    )
    assert res.status == "not_ready"


# ── 마감 전 종목 훑기 ─────────────────────────────────────────────────────────────────


def sweep_ctx(
    repos: Any, kis: DirectKis, claims: MemoryClaimStore, now: datetime, attempt: int = 1
) -> Any:
    return ctx("program.stock_sweep", DAY.isoformat(), [], now=now, repos=repos, kis=kis,
               claims=claims, attempt=attempt)  # fmt: skip


def test_sweep_resumes_after_failures_with_per_stock_claims() -> None:
    repos, server, clock, kis = setup(kst(2026, 11, 17, 18, 10, 5))
    seed_universe(repos, DAY)
    claims = MemoryClaimStore()
    server.inject("HTTP500", 3, tr_id="FHPPG04650201")
    res = program.sweep(sweep_ctx(repos, kis, claims, clock()))
    assert res.status == "failed" and res.detail["failed"] == 3
    assert len(repos.program.swept_codes(DAY, "KRX")) == 17
    assert res.collected == ()  # event 키는 collected 에 넣지 않는다(D-P4-25 ②)
    done = {kk.as_of for kk in claims.done_keys()}
    assert len(done) == 17 and all(a.endswith(":2026-11-17") for a in done)
    before = kis.count("FHPPG04650201")
    res2 = program.sweep(sweep_ctx(repos, kis, claims, clock(), attempt=2))
    assert res2.status == "ok", res2.detail
    assert kis.count("FHPPG04650201") - before == 3  # 이어서 — 남은 종목만
    assert res2.detail["already"] == 17
    rows = repos.program.stock_days(DAY, "K", "KRX") + repos.program.stock_days(DAY, "Q", "KRX")
    assert len(rows) == 20 and {(r.scope, r.quality) for r in rows} == {("sweep", Quality.OK)}


def test_sweep_skips_keys_claimed_by_someone_else() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 18, 10, 5))
    seed_universe(repos, DAY)
    claims = MemoryClaimStore()
    other = DataKey("KIS", "program_stock_daily", f"{SYMBOLS[0]}:2026-11-17", "KRX")
    assert claims.claim(other, "someone", "x", clock())
    res = program.sweep(sweep_ctx(repos, kis, claims, clock()))
    assert res.status == "ok" and res.detail["duplicate"] == 1
    assert SYMBOLS[0] not in repos.program.swept_codes(DAY, "KRX")


def test_sweep_without_universe_is_not_ready() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 18, 10, 5))
    res = program.sweep(sweep_ctx(repos, kis, MemoryClaimStore(), clock()))
    assert res.status == "not_ready"
    assert kis.count() == 0

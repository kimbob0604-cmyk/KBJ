"""P5 저장소 규칙 시험 묶음 — 메모리(단위)와 Postgres(통합)가 **같은 시험**을 돈다.

`tests/unit/store/test_repos_p5.py` 가 메모리로, `tests/integration/test_repos_p5_pg.py` 가 실제
TimescaleDB(0001~0017 적용)로 `CASES` 를 돈다(docs/p5_design.md §1.1 시험 칸). 값은 모두 합성이다
(로그인 등급 실데이터 fixture 금지 — CLAUDE.md §2). 시계는 고정 시각.

이 파일 이름은 `test_` 로 시작하지 않아 pytest 가 직접 모으지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from kbj.core import p5_rows as p5
from kbj.core.quality import Quality
from kbj.store.repos import Repos
from kbj.store.repos.etf import map_inputs

OK, EST, INV = Quality.OK, Quality.ESTIMATED, Quality.INVALID
NOW = datetime(2026, 11, 13, 7, 0, tzinfo=UTC)  # 16:00 KST
JOB = "test.job"
D = date(2026, 11, 13)


def obs(sid: str, period: str, value: str | None, *, q: Quality = OK, src: str = "FRED",
        released: datetime | None = None) -> p5.MacroSeriesRow:  # fmt: skip
    return p5.MacroSeriesRow(
        series_id=sid, period=period, value=None if value is None else Decimal(value), unit="%",
        source=src, as_of=NOW, quality=q if value is not None else EST, released_at=released,
    )  # fmt: skip


def meta(sid: str, *, tier: str = "public", src: str = "FRED",
         dataset: str = "FRED:gov_series") -> p5.MacroSeriesMeta:  # fmt: skip
    return p5.MacroSeriesMeta(
        series_id=sid, tab="us", title=sid, unit="%", freq="D", tier=tier,  # type: ignore[arg-type]
        source=src, dataset=dataset,
    )  # fmt: skip


def event(code: str, day: date, *, src: str = "fomc", forecast: str | None = None,
          t: time | None = None) -> p5.CalendarEvent:  # fmt: skip
    return p5.CalendarEvent(
        event_id=p5.event_id("US", "meeting", day, code), kind="meeting", date=day, time_kst=t,
        title=code, country="US", importance=1, source=src, quality=OK,
        forecast=None if forecast is None else Decimal(forecast),
    )  # fmt: skip


def minute(ts: datetime, inv: str, net: int, *, pair: tuple[str, str] = ("K2I", "F001"),
           session: str = "day") -> p5.InvestorMinute:  # fmt: skip
    return p5.InvestorMinute(
        ts=ts, trade_date=D, session=session, market_code=pair[0], sector_code=pair[1],  # type: ignore[arg-type]
        investor=inv, sell_qty=10, buy_qty=10 + net, net_qty=net, sell_value=100,
        buy_value=100 + net, net_value=net, quality=EST,
    )  # fmt: skip


def prog(day: date, kind: str, side: str, net: int, *, ts: datetime | None = None,
         market: str = "K") -> p5.ProgramMarketRow:  # fmt: skip
    return p5.ProgramMarketRow(
        trade_date=day, market=market, venue="KRX", kind=kind, side=side,  # type: ignore[arg-type]
        sell_qty=1, buy_qty=1, net_qty=0, sell_value=1_000_000, buy_value=1_000_000 + net,
        net_value=net, index_close=2500.5, source="KIS", quality=OK, ts=ts,
    )  # fmt: skip


def pstock(code: str, net: int, *, scope: str = "sweep", ts: datetime | None = None,
           day: date = D) -> p5.ProgramStockRow:  # fmt: skip
    return p5.ProgramStockRow(
        trade_date=day, code=code, market="K", venue="KRX", scope=scope,  # type: ignore[arg-type]
        price=10_000.0, chg_pct=1.5, sell_qty=1, buy_qty=2, net_qty=1, sell_value=10_000,
        buy_value=10_000 + net, net_value=net, source="KIS",
        quality=OK if scope == "sweep" else EST, ts=ts,
    )  # fmt: skip


# ── 시험 ─────────────────────────────────────────────────────────────────────────────────


def case_macro_revision_keeps_previous_value_and_none_never_erases(r: Repos) -> None:
    pub = r.macro
    assert pub.upsert_meta([meta("FRED:DGS10")], loaded_by=JOB, now=NOW) == 1
    assert [m.series_id for m in pub.meta("us")] == ["FRED:DGS10"]
    pub.upsert_series([obs("FRED:DGS10", "2026-11-12", "4.10")], loaded_by=JOB, received_at=NOW)
    pub.upsert_series([obs("FRED:DGS10", "2026-11-12", "4.15")], loaded_by=JOB, received_at=NOW)
    # 값 없는 새 행은 있는 값을 지우지 않는다(0 으로도 None 으로도)
    n = pub.upsert_series([obs("FRED:DGS10", "2026-11-12", None)], loaded_by=JOB, received_at=NOW)
    assert n == 0
    got = pub.series(["FRED:DGS10"])["FRED:DGS10"]
    assert [(x.value, x.prev_value) for x in got] == [(Decimal("4.15"), Decimal("4.10"))]
    pub.upsert_series([obs("FRED:DGS10", "2026-11-13", None)], loaded_by=JOB, received_at=NOW)
    latest = pub.latest(["FRED:DGS10", "FRED:NOPE"])
    assert list(latest) == ["FRED:DGS10"] and latest["FRED:DGS10"].period == "2026-11-12"
    assert pub.series(["FRED:DGS10"], start="2026-11-13")["FRED:DGS10"][0].value is None


def case_macro_tiers_stay_in_their_tables(r: Repos) -> None:
    with pytest.raises(ValueError, match="로그인 시리즈"):
        r.macro.upsert_meta([meta("YAHOO:CL=F", tier="private", src="YAHOO",
                                  dataset="YAHOO:macro")], loaded_by=JOB, now=NOW)  # fmt: skip
    with pytest.raises(ValueError, match="공개 시리즈는 pub_macro"):
        r.macro_prv.upsert_meta([meta("FRED:DGS2")], loaded_by=JOB, now=NOW)
    r.macro_prv.upsert_meta(
        [meta("YAHOO:CL=F", tier="private", src="YAHOO", dataset="YAHOO:macro")],
        loaded_by=JOB, now=NOW,
    )  # fmt: skip
    assert r.macro.meta() == [] and len(r.macro_prv.meta()) == 1
    # 공개 캘린더는 공개 출처·예상치 없이만 — 예상치는 로그인 표에
    with pytest.raises(ValueError, match="D-P5-8"):
        r.macro.upsert_calendar([event("cpi", D, src="ff")], loaded_by=JOB, received_at=NOW)
    with pytest.raises(ValueError, match="D-P5-8"):
        r.macro.upsert_calendar([event("cpi", D, forecast="3.1")], loaded_by=JOB, received_at=NOW)
    r.macro_prv.upsert_calendar(
        [event("cpi", D, src="ff", forecast="3.1")], loaded_by=JOB, received_at=NOW
    )
    assert r.macro_prv.calendar(D, D)[0].forecast == Decimal("3.1")


def case_calendar_order_and_released_window(r: Repos) -> None:
    rows = [
        event("b", D, t=time(22, 30)),
        event("a", D, t=time(4, 0)),
        event("c", D),
        event("z", D + timedelta(days=40)),
    ]
    r.macro.upsert_calendar(rows, loaded_by=JOB, received_at=NOW)
    assert [e.event_id.rsplit(":", 1)[1] for e in r.macro.calendar(D, D + timedelta(days=7))] == [
        "c", "a", "b",
    ]  # fmt: skip
    r.macro.upsert_series(
        [obs("ECOS:722Y001", "2026-11", "2.50", src="ECOS", released=NOW - timedelta(hours=1)),
         obs("ECOS:722Y001", "2026-10", "2.75", src="ECOS", released=NOW - timedelta(days=30))],
        loaded_by=JOB, received_at=NOW,
    )  # fmt: skip
    got = r.macro.released_between(NOW - timedelta(days=1), NOW)
    assert [(x.series_id, x.period) for x in got] == [("ECOS:722Y001", "2026-11")]


def case_market_stats_tables_and_none(r: Repos) -> None:
    def stat(item: str, value: str | None, stat: str = "credit_balance") -> p5.MarketStatRow:
        return p5.MarketStatRow(
            stat=stat, date=D, item=item, value=None if value is None else Decimal(value),  # type: ignore[arg-type]
            unit="원", source="DATAGO", as_of=NOW, quality=OK if value is not None else EST,
        )  # fmt: skip

    ms = r.market_stats
    ms.upsert([stat("credit_total", "20000000000000"), stat("deposit", "5", "market_capital")],
              loaded_by=JOB, received_at=NOW)  # fmt: skip
    ms.upsert([stat("credit_total", None)], loaded_by=JOB, received_at=NOW)
    got = ms.series("credit_balance")
    assert [(x.stat, x.item, x.value) for x in got] == [
        ("credit_balance", "credit_total", Decimal("20000000000000")),
    ]  # fmt: skip
    assert ms.series("market_capital", items=["deposit"])[0].value == Decimal("5")
    assert ms.last_date("credit_balance") == D and ms.last_date("cma") is None
    with pytest.raises(ValueError, match="모르는"):
        ms.series("nope")


def case_us_universe_daily_and_artifacts(r: Repos) -> None:
    def uni(sym: str, day: date) -> p5.UsUniverseRow:
        return p5.UsUniverseRow(sym, day, sym, "NASDAQ", "Tech", None, 1e9, False, "nasdaq", OK)

    def bar(sym: str, day: date, src: str, close: float, q: Quality = OK) -> p5.UsDailyRow:
        return p5.UsDailyRow(sym, day, "stock", close, close, close, close, 100, True, src, q)

    us = r.us
    us.put_universe(D - timedelta(days=7), [uni("AAA", D - timedelta(days=7))], loaded_by=JOB,
                    received_at=NOW)  # fmt: skip
    us.put_universe(D, [uni("AAA", D), uni("BBB", D)], loaded_by=JOB, received_at=NOW)
    assert [u.symbol for u in us.universe()] == ["AAA", "BBB"]
    assert [u.symbol for u in us.universe(D - timedelta(days=1))] == ["AAA"]
    us.upsert_daily(
        [bar("AAA", D, "stooq", 9.0), bar("AAA", D, "nasdaq", 10.0),
         bar("AAA", D - timedelta(days=1), "nasdaq", 8.0, INV),
         bar("AAA", D - timedelta(days=1), "stooq", 7.5)],
        loaded_by=JOB, received_at=NOW,
    )  # fmt: skip
    got = us.daily(["AAA"], D, 5)["AAA"]
    # 날짜마다 출처 하나 — nasdaq > yahoo > stooq, invalid 는 맨 뒤
    assert [(b.date, b.source, b.close) for b in got] == [
        (D - timedelta(days=1), "stooq", 7.5), (D, "nasdaq", 10.0),
    ]  # fmt: skip
    assert list(us.daily(None, D, 1)) == ["AAA"] and us.daily([], D, 1) == {}
    assert us.last_trade_date(D) == D
    art = p5.UsArtifact(D, "us_brief", {"text": "간밤"}, "v1", "d", NOW, NOW, "nasdaq+yahoo", OK)
    r.board.put_us_artifacts([art], loaded_by=JOB)
    r.board.put_us_artifacts([art], loaded_by=JOB)  # 멱등
    got_art = r.board.us_artifact(D, "us_brief")
    assert got_art is not None and got_art.payload == {"text": "간밤"}
    assert r.board.us_last_day(D) == D and r.board.artifact(D, "newhigh") is None
    assert r.board.last_day(D) is None  # 국장 보드 날짜에 미국 산출이 섞이지 않는다


def case_investor_minutes_are_idempotent_and_never_filled(r: Repos) -> None:
    t0 = datetime(2026, 11, 13, 0, 0, tzinfo=UTC)  # 09:00 KST
    rows = [minute(t0, "frgn", 5), minute(t0, "prsn", -5),
            minute(t0 + timedelta(minutes=2), "frgn", 7)]  # fmt: skip
    assert r.flows.put_investor_minutes(rows, loaded_by="flows.market_minute") == 3
    r.flows.put_investor_minutes([minute(t0, "frgn", 6)], loaded_by="flows.market_minute")
    got = r.flows.investor_minutes(D)
    # 빈 분(09:01)은 행이 없다 — 보간하지 않는다. 같은 분 재실행은 덮어쓴다
    assert [(x.ts.minute, x.investor, x.net_value) for x in got] == [
        (0, "frgn", 6), (0, "prsn", -5), (2, "frgn", 7),
    ]  # fmt: skip
    assert r.flows.investor_minutes(D, [("K2I", "OC01")]) == []
    assert r.flows.investor_minutes(D, session="night") == []
    with pytest.raises(ValueError, match="두 번"):
        r.flows.put_investor_minutes([minute(t0, "frgn", 1)] * 2, loaded_by="flows.market_minute")


def case_program_tables(r: Repos) -> None:
    pr = r.program
    days = [D - timedelta(days=i) for i in (0, 1, 3)]
    pr.put_market_days([prog(d, k, s, 100) for d in days for k in p5.PROGRAM_KINDS
                        for s in p5.PROGRAM_SIDES], loaded_by=JOB, received_at=NOW)  # fmt: skip
    got = pr.market_days("K", "KRX", D, 2)
    assert sorted({x.trade_date for x in got}) == days[1::-1] and len(got) == 18
    assert pr.market_days("Q", "KRX", D, 2) == []
    ts = datetime(2026, 11, 13, 0, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="ts"):
        pr.put_market_days([prog(D, "whol", "smtn", 1, ts=ts)], loaded_by=JOB, received_at=NOW)
    pr.put_market_minutes([prog(D, "whol", "smtn", 5, ts=ts)] * 1, loaded_by=JOB, received_at=NOW)
    pr.put_market_minutes([prog(D, "whol", "smtn", 9, ts=ts)], loaded_by=JOB, received_at=NOW)
    assert [x.net_value for x in pr.market_minutes(D, "K", "KRX")] == [9]  # 같은 분 멱등
    pr.put_investor_days(
        [p5.ProgramInvestorRow(D, "K", "KRX", "frgn", "whol", 1, 2, 1, 10, 20, 10, "KIS", OK)],
        loaded_by=JOB, received_at=NOW,
    )  # fmt: skip
    assert pr.investor_days(D, "K", "KRX")[0].net_value == 10
    t1, t2 = ts, ts + timedelta(minutes=10)
    pr.put_stock_scan([pstock("995010", 5, scope="scan", ts=t1),
                       pstock("995020", 6, scope="scan", ts=t2)],
                      loaded_by=JOB, received_at=NOW)  # fmt: skip
    assert [x.code for x in pr.stock_scan(D, "K", "KRX")] == ["995020"]  # 마지막 슬롯
    assert [x.code for x in pr.stock_scan(D, "K", "KRX", t1)] == ["995010"]
    with pytest.raises(ValueError, match="scope"):
        pr.put_stock_days([pstock("995010", 1, scope="scan")], loaded_by=JOB, received_at=NOW)
    pr.put_stock_days([pstock("995010", 1), pstock("995030", -3)], loaded_by=JOB,
                      received_at=NOW)  # fmt: skip
    assert pr.swept_codes(D, "KRX") == {"995010", "995030"}
    assert [x.code for x in pr.stock_days(D, "K", "KRX")] == ["995010", "995030"]


def case_etf_holdings_and_holders(r: Repos) -> None:
    from kbj.core.rows import Fund, HoldingRow

    def h(code: str, wt: float) -> HoldingRow:
        return HoldingRow(code, code, 10.0, wt, 1000.0, "ETF_ISSUERS:x", OK)

    e = r.etf
    e.upsert_funds(
        [Fund("F1", "x", "k1", "995900", "액티브A", None, True, "full", True, 0, "ETF_ISSUERS:x"),
         Fund("F2", "x", "k2", "995910", "패시브B", None, False, "full", True, 0,
              "ETF_ISSUERS:x")],
        loaded_by=JOB, now=NOW,
    )  # fmt: skip
    e.put_holdings("F1", D - timedelta(days=1), [h("995010", 5.0)], loaded_by=JOB)
    e.put_holdings("F1", D, [h("995010", 7.0), h("995020", 9.0)], loaded_by=JOB)
    e.put_holdings("F2", D - timedelta(days=2), [h("995010", 3.0)], loaded_by=JOB)
    got = e.holdings_of("995900", D)  # 상장 코드로도 찾는다
    assert [(x.fund_id, x.code, x.wt, x.asof) for x in got] == [
        ("F1", "995020", 9.0, D), ("F1", "995010", 7.0, D),
    ]  # fmt: skip
    assert [x.code for x in e.holdings_of("F1", D - timedelta(days=1))] == ["995010"]
    assert e.holdings_of("F1", D - timedelta(days=5)) == []
    holders = e.holders_of("995010", D)
    # 운용사마다 기준일이 다르면 그대로(섞지 않는다) — 비중 내림차순
    assert [(x.fund_id, x.asof, x.is_active) for x in holders] == [
        ("F1", D, True), ("F2", D - timedelta(days=2), False),
    ]  # fmt: skip
    meta, days = map_inputs(e, D, 5)
    assert meta == {} and days == {}


def case_writes_need_loader_and_aware_time(r: Repos) -> None:
    naive = datetime(2026, 11, 13, 9, 0)  # noqa: DTZ001 — 거부되는지 본다
    with pytest.raises(ValueError):
        r.macro.upsert_series([obs("FRED:DGS10", "2026-11-12", "1")], loaded_by="",
                              received_at=NOW)  # fmt: skip
    with pytest.raises(ValueError):
        r.macro.upsert_series([obs("FRED:DGS10", "2026-11-12", "1")], loaded_by=JOB,
                              received_at=naive)  # fmt: skip
    with pytest.raises(ValueError):
        r.program.put_market_days([], loaded_by=JOB, received_at=naive)
    with pytest.raises(ValueError):
        r.flows.put_investor_minutes([], loaded_by="")


CASES: list[Callable[[Repos], None]] = [
    case_macro_revision_keeps_previous_value_and_none_never_erases,
    case_macro_tiers_stay_in_their_tables,
    case_calendar_order_and_released_window,
    case_market_stats_tables_and_none,
    case_us_universe_daily_and_artifacts,
    case_investor_minutes_are_idempotent_and_never_filled,
    case_program_tables,
    case_etf_holdings_and_holders,
    case_writes_need_loader_and_aware_time,
]
CASE_IDS = [c.__name__.removeprefix("case_") for c in CASES]

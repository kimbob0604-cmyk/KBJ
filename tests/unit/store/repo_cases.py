"""저장소 규칙 시험 묶음 — 메모리 구현(단위)과 Postgres 구현(통합)이 **같은 시험**을 돈다.

`tests/unit/store/test_repos_memory.py` 가 메모리로, `tests/integration/test_repos_pg.py` 가 실제
TimescaleDB(0001~0009 적용)로 `CASES` 를 돌린다(docs/p3_design.md §1.1 시험 칸).
값은 모두 합성이다(로그인 등급 실데이터 fixture 금지 — CLAUDE.md §2). 시계는 고정 시각.

이 파일 이름은 `test_` 로 시작하지 않아 pytest 가 직접 모으지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import (
    AllTime,
    Bar,
    BoardArtifact,
    BoardDayRecord,
    Change,
    EtfDay,
    EtfMeta,
    EtfQuote,
    EtfType,
    Fund,
    HoldingRow,
    IndexBar,
    IndexQuote,
    IntradayInvestor,
    Investor,
    InvestorDay,
    Label,
    LedgerCheck,
    RankRow,
    ReconcileRow,
    SectorQuote,
    Snap,
    SplitCheck,
    SplitEvent,
    StockDay,
    UniverseRow,
)
from kbj.store.repos import Repos

OK, EST, INV = Quality.OK, Quality.ESTIMATED, Quality.INVALID
D1, D2, D3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 6)  # 10-05 대체공휴일 — 행 없음
NOW = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)  # 16:00 KST
SLOT = datetime(2026, 10, 6, 1, 0, tzinfo=UTC)  # 10:00 KST
JOB = "test.job"


def bar(code: str, d: date, close: float, *, source: str = "krx", q: Quality = OK,
        venue: str = "KRX") -> Bar:  # fmt: skip
    return Bar(code, d, close - 10, close + 5, close - 20, close, 1000, int(close * 1000),
               source, venue, q)  # fmt: skip


def snap(code: str, d: date, close: float, *, source: str = "kis", q: Quality = OK,
         flags: tuple[str, ...] | None = (), kind: str | None = "common") -> Snap:  # fmt: skip
    return Snap(
        code=code, date=d, name=f"합성{code}", market="KOSPI", kind=kind, close=close,
        chg_pct=1.25, volume=12_345, turnover=987_654_321_000, turnover_is_estimate=False,
        mktcap=12_000_000_000_000, shares=1_000_000, status_flags=flags, source=source,
        venue="KRX", quality=q,
    )  # fmt: skip


def inv(code: str, d: date, who: Investor, value: int, *, source: str = "kis",
        q: Quality = OK, venue: str = "KRX") -> InvestorDay:  # fmt: skip
    return InvestorDay(code, d, who, value, value // 1000, source, venue, q)


# ── 시세 ────────────────────────────────────────────────────────────────────────────────


def case_series_picks_ledger_priority(r: Repos) -> None:
    m = r.market
    m.upsert_daily_bars([bar("000010", D1, 100.0), bar("000010", D2, 110.0)], loaded_by=JOB)
    m.upsert_daily_bars(
        [bar("000010", D2, 111.0, source="kis", q=EST), bar("000010", D3, 120.0, source="kis")],
        loaded_by=JOB,
    )
    m.upsert_daily_bars(
        [bar("000020", D3, 50.0, venue="NXT"), bar("000020", D3, 51.0)], loaded_by=JOB
    )
    m.upsert_daily_bars([bar("000030", D3, 9.0, q=INV)], loaded_by=JOB)
    got = m.series(None, D3, 5)
    assert [b.close for b in got["000010"]] == [100.0, 110.0, 120.0]  # D2 는 krx(ok)
    assert [b.source for b in got["000010"]] == ["krx", "krx", "kis"]
    assert [(b.close, b.venue) for b in got["000020"]] == [(51.0, "KRX")]  # KRX > NXT
    assert [b.quality for b in got["000030"]] == [INV]  # 다른 행이 없으면 invalid 도 돌려준다
    assert m.trading_days(D3, 2) == [D3, D2]
    assert m.trading_days(D3, 10) == [D3, D2, D1]
    two = m.series(["000010"], D3, 2)
    assert list(two) == ["000010"] and [b.date for b in two["000010"]] == [D2, D3]
    assert m.series([], D3, 5) == {}
    assert m.series(None, date(2026, 9, 1), 5) == {}
    with pytest.raises(ValueError):
        m.trading_days(D3, 0)


def case_bars_overwrite_same_key(r: Repos) -> None:
    m = r.market
    m.upsert_daily_bars([bar("000010", D1, 100.0)], loaded_by=JOB)
    m.upsert_daily_bars([bar("000010", D1, 101.0)], loaded_by=JOB, received_at=NOW)
    assert [b.close for b in m.series(None, D1, 1)["000010"]] == [101.0]
    naive = datetime(2026, 10, 6, 7, 0)  # noqa: DTZ001 — 거부되는지 본다
    with pytest.raises(ValueError, match="naive"):
        m.upsert_daily_bars([bar("000010", D1, 1.0)], loaded_by=JOB, received_at=naive)
    with pytest.raises(ValueError, match="loaded_by"):
        m.upsert_daily_bars([bar("000010", D1, 1.0)], loaded_by=" ")


def case_etf_assets_are_separate(r: Repos) -> None:
    m = r.market
    m.upsert_daily_bars([bar("069500", D1, 30000.0)], loaded_by=JOB, asset="etf")
    m.upsert_daily_bars([bar("005930", D1, 70000.0)], loaded_by=JOB)
    assert set(m.series(None, D1, 1)) == {"005930"}
    assert set(m.series(None, D1, 1, asset="etf")) == {"069500"}


def case_index_bars_round_trip(r: Repos) -> None:
    m = r.market
    rows = [
        IndexBar("0001", D1, "코스피", 2500.0, 2510.5, 2490.0, 2505.25, 400_000_000,
                 9_000_000_000_000, "krx", OK),
        IndexBar("0001", D2, "코스피", 2505.0, 2520.0, 2500.0, 2515.75, 410_000_000,
                 9_100_000_000_000, "krx", OK),
    ]  # fmt: skip
    assert m.upsert_index_bars(rows, loaded_by=JOB) == 2
    got = m.index_series(None, D2, 5)["0001"]
    assert [(b.date, b.close, b.turnover) for b in got] == [
        (D1, 2505.25, 9_000_000_000_000), (D2, 2515.75, 9_100_000_000_000)
    ]  # fmt: skip
    assert m.series(None, D2, 5) == {}  # 지수는 종목 일봉과 섞이지 않는다


def case_snapshot_latest_and_best(r: Repos) -> None:
    m = r.market
    m.upsert_snapshots(
        [snap("000010", D1, 100.0), snap("000020", D1, 5.0, flags=None)], loaded_by=JOB
    )
    m.upsert_snapshots(
        [snap("000010", D2, 110.0, source="kis.prelim", q=EST), snap("000010", D2, 111.0),
         snap("000030", D2, 7.0, flags=("managed",), kind="pref")],
        loaded_by=JOB, received_at=NOW,
    )  # fmt: skip
    snaps, used = m.snapshot(D3)  # D3 스냅 없음 → D2
    assert used == D2 and set(snaps) == {"000010", "000030"}
    assert snaps["000010"].close == 111.0 and snaps["000010"].source == "kis"
    s30 = snaps["000030"]
    assert s30.status_flags == ("managed",) and s30.kind == "pref" and s30.flagged is True
    assert s30.turnover == 987_654_321_000 and s30.mktcap == 12_000_000_000_000
    old, used1 = m.snapshot(D1)
    assert used1 == D1 and old["000020"].status_flags is None and old["000020"].flagged is None
    assert old["000010"].status_flags == () and old["000010"].flagged is False
    assert m.snapshot(date(2026, 9, 1)) == ({}, None)
    assert {(s.code, s.source) for s in m.snapshots(D2)} == {
        ("000010", "kis.prelim"), ("000010", "kis"), ("000030", "kis")
    }  # fmt: skip
    assert {s.source for s in m.snapshots(D2, source="KIS")} == {"kis"}


def case_snapshot_quality_can_be_invalidated(r: Repos) -> None:
    m = r.market
    m.upsert_snapshots([snap("000010", D1, 100.0)], loaded_by=JOB)
    assert m.set_snapshot_quality(D1, "000010", source="kis", venue="KRX", quality=INV,
                                  note="KRX 종가 다름") is True  # fmt: skip
    assert m.set_snapshot_quality(D1, "999999", source="kis", venue="KRX", quality=INV,
                                  note="x") is False  # fmt: skip
    assert m.snapshot(D1)[0]["000010"].quality is INV
    m.upsert_snapshots([snap("000010", D1, 100.0, source="krx")], loaded_by=JOB)
    best = m.snapshot(D1)[0]["000010"]
    assert best.source == "krx" and best.quality is OK  # 대조 뒤 원장은 krx 를 고른다


def case_universe_latest_per_source(r: Repos) -> None:
    m = r.market
    u = [
        UniverseRow("000010", D1, "합성A", "KOSPI", "common", date(2001, 1, 2), "krx", OK),
        UniverseRow("000020", D1, "합성B", "KOSDAQ", "pref", None, "krx", OK,
                    flags={"rank": 3}),
        UniverseRow("000010", D2, "합성A", "KOSPI", "common", date(2001, 1, 2), "krx", OK),
        UniverseRow("000040", D3, "신규", "KOSDAQ", "common", D3, "kis", EST),
    ]  # fmt: skip
    assert m.upsert_universe(u, loaded_by=JOB) == 4
    got = m.universe(D3)
    assert [x.code for x in got] == ["000010", "000040"]  # 000020 은 krx 최신(D2)에 없다
    assert got[0].market == "KOSPI" and got[0].listed_on == date(2001, 1, 2)
    old = m.universe(D1)
    assert [x.code for x in old] == ["000010", "000020"]
    assert old[1].flags == {"rank": 3} and old[1].market == "KOSDAQ" and old[1].kind == "pref"


def case_intraday_history_windows(r: Repos) -> None:
    m = r.market
    t2 = SLOT + timedelta(minutes=10)
    m.put_index_quotes(
        [IndexQuote("0001", SLOT, "코스피", 2510.5, 0.4, 3_000_000_000_000, 100, "kis", EST),
         IndexQuote("1001", SLOT, "코스닥", 850.25, -0.2, 2_000_000_000_000, 90, "kis", EST),
         IndexQuote("0001", t2, "코스피", 2511.0, 0.42, 3_100_000_000_000, 110, "kis", EST)],
        loaded_by=JOB, received_at=SLOT,
    )  # fmt: skip
    first = m.index_quotes(SLOT, t2)
    assert [(q.code, q.value) for q in first] == [("0001", 2510.5), ("1001", 850.25)]
    assert len(m.index_quotes(SLOT, t2 + timedelta(minutes=10))) == 3
    m.put_sector_quotes(
        [SectorQuote("KOSPI", "0013", SLOT, "전기전자", 30_000.5, 1.1, 10, "kis", EST)],
        loaded_by=JOB,
    )
    assert [s.code for s in m.sector_quotes(SLOT, t2)] == ["0013"]
    m.put_rank_rows(
        [RankRow("KOSPI", SLOT, 2, "KRX", "000020", "합성B", 5_000, -1.0, "kis", EST),
         RankRow("KOSPI", SLOT, 1, "KRX", "000010", "합성A", 9_000, 2.0, "kis", EST)],
        loaded_by=JOB,
    )  # fmt: skip
    assert [x.code for x in m.rank_rows(SLOT, t2)] == ["000010", "000020"]
    m.put_rank_rows(
        [RankRow("KOSPI", SLOT, 1, "KRX", "000030", "합성C", 9_500, 3.0, "kis", EST)], loaded_by=JOB
    )
    assert [x.code for x in m.rank_rows(SLOT, t2)] == ["000030", "000020"]  # 같은 순위는 덮어쓴다


def case_reconcile_round_trip(r: Repos) -> None:
    m = r.market
    rows = [
        ReconcileRow(D2, "000010", "close", 110.0, 110.0, 0.0, "ok", NOW),
        ReconcileRow(D2, "000010", "turnover", 1_000.0, 1_010.0, -0.99, "mismatch", NOW),
        ReconcileRow(D2, "000020", "close", None, 5.0, None, "missing_kis", NOW),
    ]
    assert m.put_reconcile(rows) == 3
    assert m.reconcile(D2) == rows
    m.put_reconcile([ReconcileRow(D2, "000010", "close", 110.0, 111.0, -0.9, "mismatch", NOW)])
    assert m.reconcile(D2)[0].verdict == "mismatch" and len(m.reconcile(D2)) == 3
    assert m.reconcile(D1) == []


# ── 수급 ────────────────────────────────────────────────────────────────────────────────


def case_prelim_then_final_records_revision(r: Repos) -> None:
    f = r.flows
    est_at = SLOT + timedelta(hours=5)
    rep = f.upsert_investor_days(
        [inv("000010", D3, Investor.FOREIGN, 1_000_000, source="kis.prelim", q=EST),
         inv("000010", D3, Investor.INSTITUTION, -400_000, source="kis.prelim", q=EST)],
        revise=False, loaded_by="flows.intraday", now=est_at, received_at=est_at,
    )  # fmt: skip
    assert (rep.written, rep.skipped, rep.revised) == (2, 0, 0)
    final = [
        inv("000010", D3, Investor.FOREIGN, 1_250_000),
        inv("000010", D3, Investor.INSTITUTION, -400_000),
        inv("000010", D3, Investor.INDIVIDUAL, -850_000),
    ]
    rep = f.upsert_investor_days(final, revise=True, loaded_by="market.close_collect", now=NOW,
                                 received_at=NOW)  # fmt: skip
    assert (rep.written, rep.skipped, rep.revised) == (3, 0, 2)
    assert rep.max_abs_diff == 250_000
    revs = {v.investor: v for v in f.revisions(D3)}
    assert set(revs) == {Investor.FOREIGN, Investor.INSTITUTION}
    fv = revs[Investor.FOREIGN]
    assert (fv.est_value, fv.final_value, fv.diff, fv.final_source) == (
        1_000_000, 1_250_000, 250_000, "kis"
    )  # fmt: skip
    assert fv.est_ts == est_at and fv.revised_at == NOW
    assert revs[Investor.INSTITUTION].diff == 0
    rows = f.window(None, D3, 1)["000010"]
    assert {(x.investor, x.source, x.net_value) for x in rows} == {
        (Investor.FOREIGN, "kis", 1_250_000), (Investor.INSTITUTION, "kis", -400_000),
        (Investor.INDIVIDUAL, "kis", -850_000),
    }  # fmt: skip
    assert all(x.quality is OK for x in rows)
    # 확정 뒤에 늦게 온 잠정은 쓰지 않는다
    late = f.upsert_investor_days(
        [inv("000010", D3, Investor.FOREIGN, 9, source="kis.prelim", q=EST)],
        revise=False, loaded_by="flows.intraday", now=NOW,
    )  # fmt: skip
    assert (late.written, late.skipped) == (0, 1)
    assert f.window(["000010"], D3, 1)["000010"][0].net_value == 1_250_000


def case_non_revise_keeps_other_sources_and_overwrites_same(r: Repos) -> None:
    f = r.flows
    f.upsert_investor_days([inv("000010", D2, Investor.FOREIGN, 5, source="kis.prelim", q=EST)],
                           revise=False, loaded_by=JOB, now=NOW)  # fmt: skip
    f.upsert_investor_days([inv("000010", D2, Investor.FOREIGN, 7, source="kis.prelim", q=EST)],
                           revise=False, loaded_by=JOB, now=NOW)  # fmt: skip
    assert f.window(None, D2, 1)["000010"][0].net_value == 7  # 같은 원천 — 덮어쓰기
    f.upsert_investor_days([inv("000010", D2, Investor.FOREIGN, 8)], revise=False, loaded_by=JOB,
                           now=NOW)  # fmt: skip
    assert f.revisions(D2) == []  # revise=False 는 차이를 남기지 않는다
    assert f.window(None, D2, 1)["000010"][0].source == "kis"  # 원장은 확정을 고른다
    assert f.days(["000010"], D1, D3)["000010"][0].net_value == 8


def case_flow_window_counts_trading_days(r: Repos) -> None:
    f = r.flows
    for d, v in ((D1, 1), (D2, 2), (D3, 3)):
        f.upsert_investor_days([inv("000010", d, Investor.INDIVIDUAL, v),
                                inv("000020", d, Investor.INDIVIDUAL, -v)],
                               revise=True, loaded_by=JOB, now=NOW)  # fmt: skip
    w = f.window(["000010"], D3, 2)
    assert list(w) == ["000010"] and [x.date for x in w["000010"]] == [D2, D3]
    assert f.window(None, date(2026, 9, 30), 5) == {}
    assert f.window([], D3, 5) == {}


def case_market_days_and_intraday(r: Repos) -> None:
    f = r.flows
    rows = [
        inv("0001", D2, Investor.FOREIGN, -1_000_000_000),
        inv("1001", D2, Investor.FOREIGN, 300_000_000),
        inv("0001", D3, Investor.FOREIGN, 2_000_000_000),
    ]
    assert f.upsert_market_days(rows, loaded_by=JOB) == 3
    md = f.market_days(D3, 1)
    assert list(md) == ["0001"] and md["0001"][0].net_value == 2_000_000_000
    assert set(f.market_days(D3, 5)) == {"0001", "1001"}
    q = [
        IntradayInvestor("000010", SLOT, Investor.FOREIGN, "KRX", 1_000, 10, 1, "kis.prelim", EST),
        IntradayInvestor("000010", SLOT, Investor.INSTITUTION, "KRX", None, -5, None, "kis.prelim",
                         EST),
    ]  # fmt: skip
    assert f.put_intraday(q, loaded_by=JOB, received_at=SLOT) == 2
    got = f.intraday(SLOT, SLOT + timedelta(minutes=10))
    assert [(x.investor, x.net_value, x.rank) for x in got] == [
        (Investor.FOREIGN, 1_000, 1), (Investor.INSTITUTION, None, None)
    ]  # fmt: skip


def case_ledger_checks(r: Repos) -> None:
    f = r.flows
    rows = [
        LedgerCheck("stock", D2, "000010", "c1", 12.0, NOW, {"sum": 12}),
        LedgerCheck("etf", D2, "069500", "c3", -3.5, NOW, {"tol": 1.0}),
    ]
    assert f.put_checks(rows) == 2
    assert f.checks(D2) == sorted(rows, key=lambda c: (c.domain, c.code, c.check_id))
    assert [c.code for c in f.checks(D2, "stock")] == ["000010"]
    f.put_checks([LedgerCheck("stock", D2, "000010", "c1", 1.0, NOW)])
    assert f.checks(D2, "stock")[0].residual == 1.0 and f.checks(D2, "stock")[0].detail == {}


# ── 신고가 보드 ───────────────────────────────────────────────────────────────────────────


def _alltime(code: str, hi: float) -> AllTime:
    return AllTime(code, hi, D1, hi - 1, D1, hi - 2, hi - 3, date(2021, 10, 1), D2, 1200, False,
                   None, None, date(2021, 10, 1), "krx", OK)  # fmt: skip


def _record(
    day: date, codes: list[str], *, q: Quality = EST, source: str = "kis"
) -> BoardDayRecord:
    labels = tuple(Label(c, day, "close", "w52", 1, source, q) for c in codes)
    days = tuple(
        StockDay(c, day, f"합성{c}", 100.0, 1.5, 5_000_000_000, False, 1_000_000_000_000, "w52",
                 None, None, "new", False, "반도체", "HBM", 3.2, 10.5, 2.1, source, q,
                 extra={"hits": {"w52": True}})
        for c in codes
    )  # fmt: skip
    arts = (
        BoardArtifact(day, "newhigh", {"basis": "close", "labels": codes}, "board-1", "sha:x",
                      NOW, NOW, source, q),
        BoardArtifact(day, "sectors", {"rows": []}, "board-1", "sha:x", NOW, NOW, source, q),
    )  # fmt: skip
    return BoardDayRecord(day, labels, days, arts, source, q)


def case_board_alltime_and_split_checks(r: Repos) -> None:
    b = r.board
    assert b.put_alltime([_alltime("000010", 100.0), _alltime("000020", 50.0)], loaded_by=JOB,
                         now=NOW) == 2  # fmt: skip
    b.put_alltime([_alltime("000010", 120.0)], loaded_by=JOB, now=NOW)
    at = b.alltime()
    assert at["000010"].hi == 120.0 and at["000010"].history_from == date(2021, 10, 1)
    assert set(b.alltime(["000020"])) == {"000020"} and b.alltime([]) == {}
    b.put_split_check(
        [SplitCheck("000010", D1, "none", "공시 없음", "DART"),
         SplitCheck("000020", D1, "unknown", "조회 실패", "DART"),
         SplitCheck("000030", None, "action", "액면분할", "DART")],
        now=NOW,
    )  # fmt: skip
    assert b.split_cleared() == {"000010"}
    assert b.split_unknown() == [("000020", "조회 실패")]
    b.put_split_check([SplitCheck("000020", D1, "none", "재조회", "DART")], now=NOW)
    assert b.split_cleared() == {"000010", "000020"} and b.split_unknown() == []


def case_board_put_day_replaces_the_day(r: Repos) -> None:
    b = r.board
    assert b.put_day(_record(D2, ["000010", "000020"]), loaded_by="board.daily", now=NOW) == 6
    assert b.put_day(_record(D1, ["000030"]), loaded_by="board.daily", now=NOW) == 4
    confirmed = _record(D2, ["000010"], q=OK, source="krx")
    assert b.put_day(confirmed, loaded_by="board.confirm", now=NOW) == 4
    labels = b.labels(D2, "close")
    assert set(labels) == {"000010"} and labels["000010"].quality is OK  # 000020 라벨은 사라졌다
    assert b.labels(D2, "high") == {}
    assert set(b.labels(D1, "close")) == {"000030"}  # 다른 날은 그대로
    sd = b.stock_days(D2)["000010"]
    assert sd.turnover == 5_000_000_000 and sd.extra == {"hits": {"w52": True}}
    assert sd.source == "krx"
    art = b.artifact(D2, "newhigh")
    assert art is not None and art.payload == {"basis": "close", "labels": ["000010"]}
    assert b.artifact(D2, "events") is None
    assert b.last_day(D3) == D2 and b.last_day(D1) == D1 and b.last_day(date(2026, 9, 1)) is None


# ── ETF ─────────────────────────────────────────────────────────────────────────────────


def etf(code: str, d: date, shrs: int, nav: float, *, source: str = "krx",
        q: Quality = OK) -> EtfDay:  # fmt: skip
    return EtfDay(code, d, f"합성ETF{code}", nav * 1.001, nav, shrs, int(shrs * nav), 1_000_000,
                  100, int(shrs * nav), "코스피 200", source, "KRX", q)  # fmt: skip


def case_etf_days_window_and_priority(r: Repos) -> None:
    e = r.etf
    e.upsert_etf_days([etf("069500", D1, 1_000_000, 30_000.12),
                       etf("069500", D2, 1_010_000, 30_100.5),
                       etf("069500", D2, 9, 1.0, source="kis", q=EST),
                       etf("102110", D3, 500_000, 31_000.0)], loaded_by=JOB)  # fmt: skip
    got = e.etf_days(D3, 2)
    assert [(d.date, d.list_shrs) for d in got["069500"]] == [(D2, 1_010_000)]
    assert got["069500"][0].nav == 30_100.5 and got["069500"][0].net_asset == int(
        1_010_000 * 30_100.5
    )
    assert set(got) == {"069500", "102110"}
    assert list(e.etf_days(D3, 5, ["102110"])) == ["102110"] and e.etf_days(D3, 5, []) == {}


def case_etf_quotes_meta_and_splits(r: Repos) -> None:
    e = r.etf
    e.put_quotes([EtfQuote("069500", SLOT, 30_100.0, 30_080.5, 0.07, 10, 1, "kis", EST)],
                 loaded_by=JOB)  # fmt: skip
    assert [q.inav for q in e.quotes(SLOT, SLOT + timedelta(minutes=10))] == [30_080.5]
    meta = [
        EtfMeta("069500", "합성200", "합성운용", "KODEX", "시장대표", EtfType.KR_INDEX, None,
                "코스피 200", date(2002, 10, 14), None, "krx", OK),
        EtfMeta("122630", "합성레버리지", "합성운용", "KODEX", None, EtfType.LEVERAGED_INVERSE,
                2.0, "코스피 200", None, None, "krx", OK),
    ]  # fmt: skip
    assert e.upsert_meta(meta, loaded_by=JOB, now=NOW) == 2
    assert e.meta() == {m.code: m for m in meta}
    manual = SplitEvent("069500", D2, 10.0, "manual", "수기 1:10", "manual", OK)
    detected = SplitEvent("069500", D2, 9.8, "detected", "좌수·NAV 계단", "krx", EST)
    assert e.put_split_event(manual, loaded_by=JOB, now=NOW) is True
    assert e.put_split_event(detected, loaded_by=JOB, now=NOW) is False  # manual 이 우선
    other = SplitEvent("122630", D1, 0.2, "detected", "5:1 병합", "krx", EST)
    assert e.put_split_event(other, loaded_by=JOB, now=NOW) is True
    ev = e.split_events()
    assert ev["069500"] == [manual] and ev["122630"][0].ratio == 0.2


def case_etf_funds_holdings_and_changes(r: Repos) -> None:
    e = r.etf
    fund = Fund("kodex:1", "kodex", "1", "069500", "합성200", "시장대표", False, "full", True, 0,
                "ETF_ISSUERS:kodex")  # fmt: skip
    assert e.upsert_funds([fund], loaded_by="etf.collect", now=NOW) == 1
    e.upsert_funds([Fund(**{**fund.__dict__, "empty_streak": 2})], loaded_by="etf.collect", now=NOW)
    assert e.funds()["kodex:1"].empty_streak == 2
    src = "ETF_ISSUERS:kodex"
    h1 = [HoldingRow("005930", "합성전자", 1_000.0, 25.5, 70_000_000.0, src, OK),
          HoldingRow("000660", "합성닉스", 200.0, 10.25, 30_000_000.0, src, OK)]  # fmt: skip
    assert e.put_holdings("kodex:1", D1, h1, loaded_by="etf.collect", received_at=NOW) == 2
    e.put_holdings("kodex:1", D2, h1[:1], loaded_by="etf.collect")
    e.put_holdings("kodex:1", D2, h1, loaded_by="etf.collect")  # 같은 스냅 다시 — 통째로 바뀐다
    e.put_holdings("kodex:1", D3, h1[:1], loaded_by="etf.collect")
    assert set(e.holdings("kodex:1", D3)) == {"005930"}
    assert e.holdings("kodex:1", D1)["000660"].wt == 10.25
    assert e.holding_dates() == {"kodex:1": [D1, D2, D3]}
    with pytest.raises(ValueError, match="두 번"):
        e.put_holdings("kodex:1", D1, [h1[0], h1[0]], loaded_by="etf.collect")
    ch = [
        Change(D3, "kodex:1", "000660", "DROP", "합성닉스", D3, D2, 4, 200.0, None, 10.25, None,
               None, None),
        Change(D3, "kodex:1", "005930", "ADD", "합성전자", D3, D2, 4, 1_000.0, 1_100.0, 25.5,
               27.0, 10.0, 7.5),
    ]  # fmt: skip
    assert e.put_changes(D3, ch, engine_version="etf-1", now=NOW) == 2
    e.put_changes(D3, ch[1:], engine_version="etf-1", now=NOW)  # 다시 돌린 분석 — 통째로
    assert e.changes(D3) == ch[1:]
    assert e.last_change_run(D3) == D3 and e.last_change_run(D2) is None
    with pytest.raises(ValueError, match="run_date"):
        e.put_changes(D2, ch, engine_version="etf-1", now=NOW)


CASES: tuple[Callable[[Repos], None], ...] = (
    case_series_picks_ledger_priority,
    case_bars_overwrite_same_key,
    case_etf_assets_are_separate,
    case_index_bars_round_trip,
    case_snapshot_latest_and_best,
    case_snapshot_quality_can_be_invalidated,
    case_universe_latest_per_source,
    case_intraday_history_windows,
    case_reconcile_round_trip,
    case_prelim_then_final_records_revision,
    case_non_revise_keeps_other_sources_and_overwrites_same,
    case_flow_window_counts_trading_days,
    case_market_days_and_intraday,
    case_ledger_checks,
    case_board_alltime_and_split_checks,
    case_board_put_day_replaces_the_day,
    case_etf_days_window_and_priority,
    case_etf_quotes_meta_and_splits,
    case_etf_funds_holdings_and_changes,
)
CASE_IDS = [c.__name__.removeprefix("case_") for c in CASES]

"""engine 투자자별 순매수 (services/engine/flow.py `InvestorFeed` — metrics §6.2).

- investor_flow 의 조합(선물·콜·풋·위클리)마다 외국인·개인·기관계·증권 새 행을 그대로
  `investor_flow` 지표 행으로(ts·거래일·세션·품질 그대로, key `시장:업종:투자자`, 값 = 순매수 수량)
- 합계 데이터·증권은 딜러 프록시라는 표시, 순매수 수량이 없으면 null·invalid(`field_missing`)
- 같은 행은 다시 내지 않고, 세션이 바뀌면 처음부터
- 서비스: 30초마다 지금 세션 최신 행을 보고, shadow 는 저장만, visible 은 발행, off 는 읽지 않는다

딜러 가정 점검(`dealer_record` — metrics §6.3):

- 그날 주간 증권 계정 콜(월물·위클리 셋) 순매수 합 > 0 그리고 풋 합 < 0 이면 일치(1), 아니면 0 —
  모르는 조합은 빼고 estimated, 한쪽을 통째로 모르면 null·invalid, 쓴 행 품질 합성, 주간 행만
- 연속 불일치는 앞 거래일 행에서 — 5거래일이면 경고, 기록 없는 날·판정 없는 날은 연속을 끊는다
- 서비스: POST_DAY 한 번(일별 지표와 따로 — 실패도 따로 1분 뒤 다시), 플래그대로
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from services.bus import EngineMetrics
from services.engine.daily import daily_ts
from services.engine.flow import SHOWN_INVESTORS, InvestorFeed, dealer_record
from services.engine.records import MetricRecord
from services.engine.registry import Flag
from services.poller.records import InvestorRecord
from tests.fakes.engine_inputs import CAL, kst
from tests.unit.test_engine_service import TUE, Rig, T

PAIRS = (
    ("K2I", "F001"),
    ("K2I", "OC01"),
    ("K2I", "OP01"),
    ("WKM", "OC05"),
    ("WKM", "OP05"),
    ("WKI", "OC04"),
    ("WKI", "OP04"),
)
ALL_INVESTORS = ("frgn", "prsn", "orgn", "scrt", "ivtr", "bank")


def inv(
    at: datetime,
    market: str,
    sector: str,
    investor: str,
    net: int | None = 10,
    *,
    trade_date: date = TUE,
    session: str = "day",
    quality: str = "ok",
) -> InvestorRecord:
    return InvestorRecord.model_validate(
        {
            "ts": at,
            "trade_date": trade_date,
            "session": session,
            "market_code": market,
            "sector_code": sector,
            "investor": investor,
            "buy_qty": 100,
            "sell_qty": None if net is None else 100 - net,
            "net_qty": net,
            "net_value": None if net is None else net * 3,
            "quality": quality,
        }
    )


def snapshot(at: datetime, **kw: Any) -> list[InvestorRecord]:
    return [inv(at, m, s, i, **kw) for m, s in PAIRS for i in ALL_INVESTORS]


def test_new_rows_of_the_shown_investors_pass_through_as_they_are() -> None:
    feed = InvestorFeed()
    rows = feed.rows((TUE, "day"), snapshot(T), "shadow")
    assert len(rows) == len(PAIRS) * len(SHOWN_INVESTORS)
    assert {r.payload["investor"] for r in rows} == {"frgn", "prsn", "orgn", "scrt"}
    by_key = {r.key: r for r in rows}
    call = by_key["WKM:OC05:scrt"]
    assert (call.metric, call.scope, call.value, call.quality, call.flag) == (
        "investor_flow",
        "all",
        10.0,
        "ok",
        "shadow",
    )
    assert (call.ts, call.trade_date, call.session) == (T, TUE, "day")
    p = call.payload
    assert (p["product"], p["series"], p["net_value"], p["buy_qty"], p["sell_qty"]) == (
        "call",
        "WKM",
        30,
        100,
        90,
    )
    assert p["dealer_proxy"] is True and p["aggregate"] is True
    assert by_key["K2I:F001:frgn"].payload["product"] == "futures"
    assert by_key["K2I:F001:frgn"].payload["dealer_proxy"] is False
    assert by_key["WKI:OP04:prsn"].payload["product"] == "put"


def test_the_same_row_is_not_repeated_and_a_session_change_starts_over() -> None:
    feed = InvestorFeed()
    feed.rows((TUE, "day"), snapshot(T), "shadow")
    assert feed.rows((TUE, "day"), snapshot(T), "shadow") == []
    t1 = T + timedelta(seconds=60)
    newer = [inv(t1, "K2I", "OC01", "scrt", -5), *snapshot(T)]
    (row,) = feed.rows((TUE, "day"), newer, "shadow")
    assert (row.key, row.ts, row.value) == ("K2I:OC01:scrt", t1, -5.0)
    nd = TUE + timedelta(days=1)
    night = kst(TUE, 19, 0)
    stray = inv(night, "K2I", "OC01", "scrt", trade_date=TUE)  # 다른 세션 행 — 넘기지 않는다
    rows = feed.rows(
        (nd, "night"), [*snapshot(night, trade_date=nd, session="night"), stray], "shadow"
    )
    assert len(rows) == len(PAIRS) * len(SHOWN_INVESTORS)
    assert {(r.trade_date, r.session) for r in rows} == {(nd, "night")}


def test_quality_is_the_row_quality_and_a_missing_net_is_invalid() -> None:
    feed = InvestorFeed()
    rows = feed.rows(
        (TUE, "day"),
        [
            inv(T, "K2I", "OC01", "scrt", None),
            inv(T, "K2I", "OP01", "scrt", -3, quality="stale"),
            inv(T, "XXX", "OC01", "scrt"),  # 모르는 조합 — 넘기지 않는다
        ],
        "shadow",
    )
    by_key = {r.key: r for r in rows}
    assert set(by_key) == {"K2I:OC01:scrt", "K2I:OP01:scrt"}
    missing = by_key["K2I:OC01:scrt"]
    assert missing.value is None and missing.quality == "invalid"
    assert missing.payload["reasons"] == ["field_missing"]
    assert by_key["K2I:OP01:scrt"].quality == "stale"


# ── 서비스 ──


def flows(rig: Rig) -> list[MetricRecord]:
    return [m for m in rig.store.metrics.values() if m.metric == "investor_flow"]


def test_the_service_passes_the_session_rows_every_thirty_seconds() -> None:
    rig = Rig()
    rig.store.investor += snapshot(T - timedelta(seconds=20))
    rig.svc.tick()
    first = flows(rig)
    assert len(first) == len(PAIRS) * len(SHOWN_INVESTORS)
    assert {m.flag for m in first} == {"shadow"} and rig.svc.stats.investor_rows == len(first)
    rig.store.investor.append(inv(T + timedelta(seconds=10), "WKI", "OC04", "orgn", 7))
    rig.clock.advance(20)
    rig.svc.tick()
    assert len(flows(rig)) == len(first)  # 30초가 안 됐다
    rig.clock.advance(10)
    rig.svc.tick()
    assert len(flows(rig)) == len(first) + 1


def test_visible_investor_rows_are_published_and_off_is_not_read() -> None:
    rig = Rig()
    flags: dict[str, Flag] = {"investor_flow": "visible"}
    rig.svc.flags = flags
    rig.store.investor += snapshot(T - timedelta(seconds=20))
    rig.svc.tick()
    got = [m for m in rig.published() if isinstance(m, EngineMetrics)]
    (msg,) = got
    assert {x.metric for x in msg.metrics} == {"investor_flow"}
    assert msg.as_of == T - timedelta(seconds=20) and (msg.trade_date, msg.session) == (TUE, "day")
    rig = Rig()
    off: dict[str, Flag] = {"investor_flow": "off"}
    rig.svc.flags = off
    rig.store.investor += snapshot(T)
    rig.svc.tick()
    assert flows(rig) == []


# ── 딜러 가정 점검 (§6.3) ──

CALLS = (("K2I", "OC01"), ("WKM", "OC05"), ("WKI", "OC04"))
PUTS = (("K2I", "OP01"), ("WKM", "OP05"), ("WKI", "OP04"))
CLOSE = kst(TUE, 15, 44, 50)


def scrt_day(
    call: tuple[int | None, ...] = (30, -5, 10),
    put: tuple[int | None, ...] = (-20, 4, -1),
    *,
    d: date = TUE,
    quality: str = "ok",
) -> list[InvestorRecord]:
    """그날 주간 마지막 증권 계정 행 — None 은 그 조합 행이 없다. 다른 투자자 행도 섞는다."""
    out = [inv(kst(d, 15, 44, 50), "K2I", "OC01", "frgn", -999, trade_date=d)]
    for pairs, nets in ((CALLS, call), (PUTS, put)):
        for (m, sc), n in zip(pairs, nets, strict=True):
            if n is not None:
                out.append(inv(kst(d, 15, 44, 50), m, sc, "scrt", n, trade_date=d, quality=quality))
    return out


def past_rows(days: list[date], consistent: list[bool | None]) -> list[MetricRecord]:
    return [
        MetricRecord(
            ts=daily_ts(d),
            trade_date=d,
            session="day",
            metric="dealer_check",
            scope="all",
            value=None if c is None else float(c),
            payload={"consistent": c},
            quality="ok",
            flag="shadow",
        )
        for d, c in zip(days, consistent, strict=True)
    ]


def prior(n: int, end: date = TUE) -> list[date]:
    out: list[date] = []
    d = end
    for _ in range(n):
        d = CAL.prev_trading_day(d)
        out.append(d)
    return out[::-1]


def test_the_check_sums_the_dealer_calls_and_puts_of_all_series() -> None:
    days = prior(20)
    row = dealer_record(TUE, daily_ts(TUE), scrt_day(), [], days, "shadow")
    p = row.payload
    assert (p["call_net"], p["put_net"], p["consistent"], row.value) == (35, -17, True, 1.0)
    assert (row.metric, row.scope, row.key, row.ts, row.trade_date, row.session) == (
        "dealer_check",
        "all",
        "",
        daily_ts(TUE),
        TUE,
        "day",
    )
    assert p["pairs"]["WKM:OC05"] == -5 and p["pairs"]["K2I:OP01"] == -20
    assert p["basis"] == "flow" and p["investor"] == "scrt" and row.quality == "ok"
    assert (p["mismatch_streak"], p["warning"], p["warn_days"]) == (0, False, 5)
    # 경계: 콜 순매수 0 은 일치가 아니다
    zero = dealer_record(TUE, daily_ts(TUE), scrt_day((0, 0, 0)), [], days, "shadow")
    assert zero.value == 0.0 and zero.payload["mismatch_streak"] == 1


def test_missing_pairs_are_estimated_and_a_missing_side_is_invalid() -> None:
    days = prior(20)
    part = dealer_record(TUE, daily_ts(TUE), scrt_day(put=(-20, None, -1)), [], days, "shadow")
    assert part.quality == "estimated" and part.payload["reasons"] == ["pairs_missing"]
    assert part.payload["pairs"]["WKM:OP05"] is None and part.value == 1.0
    none = dealer_record(TUE, daily_ts(TUE), scrt_day(put=(None, None, None)), [], days, "shadow")
    assert none.value is None and none.quality == "invalid"
    assert "no_put_flow" in none.payload["reasons"]
    stale = dealer_record(TUE, daily_ts(TUE), scrt_day(quality="stale"), [], days, "shadow")
    assert stale.quality == "stale"
    night = [r.model_copy(update={"session": "night"}) for r in scrt_day()]  # 주간 행만 본다
    assert dealer_record(TUE, daily_ts(TUE), night, [], days, "shadow").value is None


def test_five_mismatching_days_in_a_row_raise_the_warning() -> None:
    days = prior(20)
    bad = scrt_day(call=(-1, -1, -1))
    four = past_rows(days[-4:], [False] * 4)
    row = dealer_record(TUE, daily_ts(TUE), bad, four, days, "shadow")
    assert row.payload["mismatch_streak"] == 5 and row.payload["warning"] is True
    three = past_rows(days[-3:], [False] * 3)
    assert dealer_record(TUE, daily_ts(TUE), bad, three, days, "shadow").payload["warning"] is False
    # 판정 없는 날·기록 없는 날은 연속을 끊는다
    gap = past_rows([days[-5], days[-4], days[-2], days[-1]], [False] * 4)
    assert (
        dealer_record(TUE, daily_ts(TUE), bad, gap, days, "shadow").payload["mismatch_streak"] == 3
    )
    unknown = past_rows(days[-4:], [False, None, False, False])
    assert (
        dealer_record(TUE, daily_ts(TUE), bad, unknown, days, "shadow").payload["mismatch_streak"]
        == 3
    )
    # 오늘 일치면 0
    assert (
        dealer_record(TUE, daily_ts(TUE), scrt_day(), four, days, "shadow").payload[
            "mismatch_streak"
        ]
        == 0
    )


def dealer_rows(rig: Rig) -> list[MetricRecord]:
    return [m for m in rig.store.metrics.values() if m.metric == "dealer_check"]


def test_the_service_checks_once_in_post_day_with_the_stored_history() -> None:
    rig = Rig(kst(TUE, 15, 44))
    rig.store.investor += scrt_day(call=(-1, -1, -1))
    for r in past_rows(prior(4), [False] * 4):
        rig.store.metrics[(r.ts, r.metric, r.scope, r.key)] = r
    rig.svc.tick()  # 아직 주간
    assert len(dealer_rows(rig)) == 4
    rig.clock.advance(6 * 60)  # 15:50 — POST_DAY
    rig.svc.tick()
    (row,) = [m for m in dealer_rows(rig) if m.trade_date == TUE]
    assert row.payload["mismatch_streak"] == 5 and row.payload["warning"] is True
    assert row.flag == "shadow" and row.ts == daily_ts(TUE)
    rig.clock.advance(60)
    rig.svc.tick()  # 그날은 한 번
    assert len(dealer_rows(rig)) == 5 and "engine_daily_failed" not in rig.health.kinds()


def test_a_failed_check_is_retried_alone_a_minute_later(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig(kst(TUE, 15, 50))
    rig.store.investor += scrt_day()
    real = rig.store.investor_latest
    calls: list[int] = []

    def flaky(*a: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db down")
        return real(*a)

    monkeypatch.setattr(rig.store, "investor_latest", flaky)
    rig.svc.tick()
    assert dealer_rows(rig) == [] and rig.svc.stats.dailies == 1  # 일별 지표는 따로 났다
    (h,) = rig.health.of("engine_daily_failed")
    assert "딜러 가정 점검" in h.detail
    rig.clock.advance(30)
    rig.svc.tick()
    assert len(calls) == 1
    rig.clock.advance(31)
    rig.svc.tick()
    (row,) = dealer_rows(rig)
    assert row.value == 1.0 and len(calls) == 2


def test_the_check_follows_the_flag() -> None:
    rig = Rig(kst(TUE, 15, 50))
    off: dict[str, Flag] = {"dealer_check": "off"}
    rig.svc.flags = off
    rig.store.investor += scrt_day()
    rig.svc.tick()
    assert dealer_rows(rig) == []
    rig = Rig(kst(TUE, 15, 50))
    on: dict[str, Flag] = {"dealer_check": "visible"}
    rig.svc.flags = on
    rig.store.investor += scrt_day()
    rig.svc.tick()
    got = [
        x
        for m in rig.published()
        if isinstance(m, EngineMetrics)
        for x in m.metrics
        if x.metric == "dealer_check"
    ]
    (x,) = got
    assert x.value == 1.0 and x.payload["consistent"] is True

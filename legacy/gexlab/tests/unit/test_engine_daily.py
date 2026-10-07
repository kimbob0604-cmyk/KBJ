"""engine 일별 지표 계산 (services/engine/daily.py — metrics §5.4·§5.5, phase3_design §1 평가 주기).

부작용 없는 `evaluate_daily` 를 가짜 입력(마지막 사이클 행·일별 이력·KRX 일별 SYNTHETIC)으로:

- 오늘 값: 그날 가장 가까운 월물(`nearest_monthly` — KIS 최종거래일 먼저)의 마지막 주간 사이클 ATM
  IV, 주간 끝(15:45)보다 90초 넘게 앞이면 stale, 없으면 invalid — 그 월물 행이 없으면 다음 월물로
  넘어가지 않는다(`nearest_monthly_missing`). 랭크·IV − HV 는 null·invalid(`no_today`)
- KRX 백필: 이력 없는 날마다 `atm_iv_daily`(source krx, 그날 15:45 행) — 기준가 없음 invalid,
  한 날의 예외는 그날만 invalid + health(원천은 남긴다). 값이 정해진 날(`daily_settled` — 값 있는
  행·KRX 로 계산한 행)만 다시 계산하지 않는다 — 자체 값이 invalid 였던 날은 KRX 로 백필
- 랭크·퍼센타일: 저장 이력(invalid·값 없는 행 제외) + KRX 백필 + 오늘 — core `iv_rank` 그대로
- IV − HV: KRX 정산가 HV20(전 거래일까지) — core `realized_vol`·`iv_minus_hv` 그대로
- 플래그: off 는 저장하지 않는다(일별 이력이 off 여도 그날 랭크엔 쓴다), 새 지표 기본 shadow
- 입력 도우미: 결제월 최종거래일(KIS 먼저·캘린더), 그날 가장 가까운 월물, 근월물 정산가, KRX 행 변환
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from core.metrics.vol import DailyIv, Settlement, iv_rank, realized_vol
from services.engine.daily import (
    DAILY_ATM,
    IV_HV,
    IV_PERCENTILE,
    IV_RANK,
    DailyInput,
    DailyResult,
    KrxDay,
    daily_settled,
    daily_ts,
    evaluate_daily,
    krx_rows,
    monthly_last_trade,
    near_settle,
    nearest_monthly,
)
from services.engine.records import MetricRecord
from tests.fakes.engine_inputs import CAL, KST, krx_option_day, kst

TODAY = date(2026, 10, 13)  # 화
CLOSE = kst(TODAY, 15, 44, 40)
NOV = date(2026, 11, 12)


def close_row(
    key: str, value: float | None, *, at: datetime = CLOSE, q: str = "ok"
) -> MetricRecord:
    return MetricRecord(
        ts=at,
        trade_date=TODAY,
        session="day",
        metric="atm_iv",
        scope="series",
        key=key,
        value=value,
        quality=q,  # type: ignore[arg-type]
        flag="visible",
    )


def daily_row(d: date, value: float | None, source: str = "self", q: str = "ok") -> MetricRecord:
    return MetricRecord(
        ts=daily_ts(d),
        trade_date=d,
        session="day",
        metric=DAILY_ATM,
        scope="all",
        value=value,
        payload={"source": source},
        quality=q,  # type: ignore[arg-type]
        flag="shadow",
    )


def prior(n: int, end: date = TODAY) -> list[date]:
    """end 앞 거래일 n 개(end 제외), 오래된 것부터."""
    out: list[date] = []
    d = end
    for _ in range(n):
        d = CAL.prev_trading_day(d)
        out.append(d)
    return out[::-1]


def one(r: DailyResult, metric: str, day: date = TODAY) -> MetricRecord:
    (x,) = [m for m in r.metrics if m.metric == metric and m.trade_date == day]
    return x


def krx_day(d: date, sigma: float, s_ref: Decimal | None = Decimal("1095.1")) -> KrxDay:
    expiry = nearest_monthly(d, {}, CAL)
    last = monthly_last_trade(expiry, {}, CAL)
    assert last is not None
    raw = [
        (r[4], r[5], r[6], r[7], r[8])
        for r in krx_option_day(d, expiry, last, sigma=sigma)
        if r[1] == "day"
    ]
    return KrxDay(d, expiry, krx_rows(raw), s_ref)


# ── 오늘 값 ──


def test_today_is_the_nearest_monthly_atm_iv_of_the_last_day_cycle() -> None:
    inp = DailyInput(
        TODAY,
        close=[
            close_row("M:202612", 0.24),
            close_row("M:202611", 0.25),
            close_row("WKI:261015", 0.40),  # 위클리는 쓰지 않는다
        ],
    )
    r = evaluate_daily(inp, cal=CAL)
    assert r.ts == datetime(2026, 10, 13, 15, 45, tzinfo=KST) == daily_ts(TODAY)
    atm = one(r, DAILY_ATM)
    assert (atm.value, atm.quality, atm.flag, atm.scope, atm.key) == (
        0.25,
        "ok",
        "shadow",
        "all",
        "",
    )
    assert atm.payload["source"] == "self" and atm.payload["series"] == "M:202611"
    assert (atm.ts, atm.session) == (r.ts, "day")
    assert r.today == DailyIv(TODAY, 0.25, "self", "ok")
    rank = one(r, IV_RANK)  # 오늘 하나뿐 — n < 20
    assert (rank.value, rank.quality) == (None, "estimated")
    assert rank.payload["n"] == 1 and rank.payload["reasons"] == ["short_window", "too_few_days"]
    hv = one(r, IV_HV)  # 정산가가 없다 — HV null, 품질은 ATM IV 품질
    assert (hv.value, hv.quality) == (None, "ok") and hv.payload["reasons"] == ["no_data"]
    assert r.health == ()


def test_without_the_nearest_monthly_row_the_next_month_is_not_used() -> None:
    """마지막 사이클에 가장 가까운 월물(202611) 행이 없으면(그 시리즈가 실패 등) 다음 달(202612)
    IV 를 오늘 값으로 쓰지 않는다 — invalid(`nearest_monthly_missing`), 이력에 남지 않는다."""
    r = evaluate_daily(DailyInput(TODAY, close=[close_row("M:202612", 0.35)]), cal=CAL)
    atm = one(r, DAILY_ATM)
    assert (atm.value, atm.quality) == (None, "invalid")
    assert atm.payload["series"] == "M:202611"
    assert atm.payload["reasons"] == ["nearest_monthly_missing"]
    assert r.today is None and one(r, IV_RANK).payload["reasons"] == ["no_today"]


def test_the_nearest_monthly_follows_the_kis_last_trading_day() -> None:
    """가장 가까운 월물은 KIS 최종거래일(`series_expiries`)로 고른다 — 만기가 미뤄진 202610(가정
    10-15)이 오늘(10-13) 가장 가까운 월물이면 그 행."""
    close = [close_row("M:202610", 0.21), close_row("M:202611", 0.25)]
    r = evaluate_daily(DailyInput(TODAY, close=close), cal=CAL)
    assert one(r, DAILY_ATM).payload["series"] == "M:202611"  # 캘린더: 202610 은 10-08 만기
    moved = {"202610": date(2026, 10, 15)}
    r = evaluate_daily(DailyInput(TODAY, close=close, option_last_trade=moved), cal=CAL)
    atm = one(r, DAILY_ATM)
    assert (atm.value, atm.payload["series"]) == (0.21, "M:202610")


def test_a_close_older_than_ninety_seconds_before_the_day_end_is_stale() -> None:
    at = kst(TODAY, 15, 43, 29)  # 15:45 보다 91초 앞
    r = evaluate_daily(DailyInput(TODAY, close=[close_row("M:202611", 0.25, at=at)]), cal=CAL)
    atm = one(r, DAILY_ATM)
    assert atm.quality == "stale" and atm.payload["reasons"] == ["close_stale"]
    assert r.today is not None and r.today.quality == "stale"
    fresh = kst(TODAY, 15, 43, 30)
    r = evaluate_daily(DailyInput(TODAY, close=[close_row("M:202611", 0.25, at=fresh)]), cal=CAL)
    assert one(r, DAILY_ATM).quality == "ok"


def test_without_a_close_value_everything_is_invalid() -> None:
    for close in ([], [close_row("M:202611", None, q="invalid")]):
        r = evaluate_daily(DailyInput(TODAY, close=close), cal=CAL)
        assert r.today is None
        for name in (DAILY_ATM, IV_RANK, IV_PERCENTILE, IV_HV):
            m = one(r, name)
            assert (m.value, m.quality) == (None, "invalid"), name
        assert one(r, IV_RANK).payload["reasons"] == ["no_today"]
        # 자체 값을 못 낸 날은 정해지지 않았다 — 다음 날 KRX 가 그날 것을 내면 백필한다
        assert not daily_settled(one(r, DAILY_ATM))
    assert one(r, DAILY_ATM).payload["series"] == "M:202611"


def test_a_daily_row_is_settled_when_it_has_a_value_or_krx_tried() -> None:
    def row(value: float | None, q: str, payload: dict[str, Any]) -> MetricRecord:
        return MetricRecord(
            ts=daily_ts(TODAY),
            trade_date=TODAY,
            session="day",
            metric=DAILY_ATM,
            scope="all",
            value=value,
            payload=payload,
            quality=q,  # type: ignore[arg-type]
            flag="shadow",
        )

    assert daily_settled(row(0.2, "stale", {"source": "self"}))
    assert daily_settled(row(None, "invalid", {"source": "krx", "reasons": ["no_s_ref"]}))
    assert daily_settled(row(0.2, "invalid", {"source": "krx"}))
    assert not daily_settled(row(None, "invalid", {"source": "self", "reasons": ["no_close"]}))
    assert not daily_settled(row(0.2, "invalid", {"source": "self"}))
    assert not daily_settled(row(None, "invalid", {"error": "ValueError"}))  # 원천 모름


# ── KRX 백필·랭크 ──


def test_krx_backfill_rows_and_the_rank_over_the_merged_history() -> None:
    days = prior(30)
    stored = [daily_row(d, 0.20 + 0.001 * i) for i, d in enumerate(days[-10:])]
    stored += [daily_row(days[0], 0.9, q="invalid"), daily_row(days[1], None)]  # 이력에서 뺀다
    krx = [krx_day(d, 0.15 + 0.002 * i) for i, d in enumerate(days[2:17])]
    krx.append(krx_day(days[17], 0.2, s_ref=None))  # 기준가 없음 → invalid
    bad = krx_day(days[18], 0.2)
    krx.append(KrxDay(bad.trade_date, bad.expiry, [*bad.rows, bad.rows[0]], bad.s_ref))  # 중복
    inp = DailyInput(TODAY, close=[close_row("M:202611", 0.23)], history=stored, krx_days=krx)
    r = evaluate_daily(inp, cal=CAL)
    backfill = {
        m.trade_date: m for m in r.metrics if m.metric == DAILY_ATM and m.trade_date < TODAY
    }
    assert set(backfill) == {k.trade_date for k in krx}
    for i, k in enumerate(krx[:15]):
        m = backfill[k.trade_date]
        assert m.ts == daily_ts(k.trade_date) and m.payload["source"] == "krx"
        assert m.payload["series"] == f"M:{k.expiry}"
        assert m.value == pytest.approx(0.15 + 0.002 * i, abs=5e-4)  # 평평한 스마일 — σ 그대로
        assert m.quality in ("ok", "estimated")
    assert (backfill[days[17]].value, backfill[days[17]].quality) == (None, "invalid")
    assert backfill[days[17]].payload["reasons"] == ["no_s_ref"]
    assert (backfill[days[18]].value, backfill[days[18]].quality) == (None, "invalid")
    # 예외 행도 원천을 남긴다 — KRX 로 못 낸 날은 다음 날 다시 계산하지 않는다(`daily_settled`)
    assert backfill[days[18]].payload["source"] == "krx"
    assert all(daily_settled(m) for m in backfill.values())
    assert [(h.kind, h.subject) for h in r.health] == [("engine_metric_failed", DAILY_ATM)]
    # 랭크 = core iv_rank(저장 이력 10 + KRX 15 + 오늘)
    hist = [DailyIv(d, 0.20 + 0.001 * i, "self", "ok") for i, d in enumerate(days[-10:])]
    hist += [
        DailyIv(k.trade_date, v, "krx", backfill[k.trade_date].quality)
        for k in krx[:15]
        if (v := backfill[k.trade_date].value) is not None
    ]
    want = iv_rank(hist, DailyIv(TODAY, 0.23, "self", "ok"), CAL)
    rank, pct = one(r, IV_RANK), one(r, IV_PERCENTILE)
    assert want.n == 26 and rank.value == pytest.approx(want.rank)
    assert pct.value == pytest.approx(want.percentile)
    assert rank.quality == pct.quality == "estimated"
    assert rank.payload["sources"] == ["krx", "self"]
    assert rank.payload["reasons"] == ["short_window", "mixed_sources"]


def test_flags_off_skip_storing_but_the_history_still_ranks() -> None:
    days = prior(25)
    stored = [daily_row(d, 0.20 + 0.002 * i) for i, d in enumerate(days)]
    inp = DailyInput(
        TODAY,
        close=[close_row("M:202611", 0.25)],
        history=stored,
        krx_days=[krx_day(prior(40)[0], 0.3)],
    )
    r = evaluate_daily(inp, cal=CAL, flags={DAILY_ATM: "off", IV_RANK: "visible", IV_HV: "off"})
    assert {m.metric for m in r.metrics} == {IV_RANK, IV_PERCENTILE}
    rank = one(r, IV_RANK)
    # 저장 이력 25 + KRX 백필 1(저장은 안 하지만 그날 랭크엔 든다) + 오늘
    assert rank.flag == "visible" and rank.value is not None and rank.payload["n"] == 27
    assert one(r, IV_PERCENTILE).flag == "shadow"
    assert r.today is not None and r.today.value == 0.25


# ── IV − HV ──


def test_iv_minus_hv_uses_krx_settlements_up_to_the_previous_day() -> None:
    days = prior(22)  # 전 거래일에서 끝나는 22일 — 수익률 21개
    rets = [0.01 if i % 2 else -0.012 for i in range(21)]
    p, settles = 1100.0, []
    for i, d in enumerate(days):
        if i:
            p *= math.exp(rets[i - 1])
        settles.append(Settlement(d, "202612", p))
    last_trade = {"202612": date(2026, 12, 10)}
    inp = DailyInput(
        TODAY,
        close=[close_row("M:202611", 0.25, q="estimated")],
        settles=settles,
        futures_last_trade=last_trade,
    )
    r = evaluate_daily(inp, cal=CAL)
    hv = realized_vol(settles, last_trade, CAL, end=TODAY)
    assert hv.value is not None and hv.end == days[-1]
    m = one(r, IV_HV)
    assert m.value == pytest.approx(0.25 - hv.value) and m.quality == "estimated"
    assert m.payload["hv20"] == pytest.approx(hv.value)
    assert m.payload["hv_end"] == days[-1].isoformat() and m.payload["contracts"] == ["202612"]


# ── 입력 도우미 ──


def test_input_helpers() -> None:
    kis = {"202610": date(2026, 10, 8), "202612": date(2026, 12, 11)}  # KIS 값이 캘린더보다 먼저
    assert monthly_last_trade("202612", kis, CAL) == date(2026, 12, 11)
    assert monthly_last_trade("202703", kis, CAL) == date(2027, 3, 11)  # 캘린더: 둘째 목요일
    assert monthly_last_trade("202613", kis, CAL) is None
    assert monthly_last_trade("2026W1", kis, CAL) is None
    # 가장 가까운 월물 — 만기일(15:20)엔 이미 차월물
    assert nearest_monthly(date(2026, 10, 7), kis, CAL) == "202610"
    assert nearest_monthly(date(2026, 10, 8), kis, CAL) == "202611"
    assert nearest_monthly(date(2026, 12, 30), kis, CAL) == "202701"
    prices = {
        (TODAY, "202612"): Decimal("1100.5"),
        (TODAY, "202703"): Decimal("1090.0"),
        (date(2026, 12, 11), "202612"): Decimal("1111.0"),
        (date(2026, 12, 11), "202703"): Decimal("1101.0"),
    }
    last = {"202612": date(2026, 12, 11), "202703": date(2027, 3, 11)}
    assert near_settle(TODAY, prices, last) == Decimal("1100.5")
    assert near_settle(date(2026, 12, 11), prices, last) == Decimal("1111.0")  # 최종거래일 당일
    assert near_settle(date(2026, 10, 14), prices, last) is None
    raw: list[Any] = [
        (Decimal("1100"), "C", Decimal("47.05"), Decimal("34.00"), 86),
        (Decimal("1100"), "P", None, None, None),  # 거래량 모름 → 0
        (Decimal("1105"), "X", None, None, 0),  # 모르는 콜풋 — 뺀다
        (Decimal("1110"), "C", Decimal("1"), Decimal("-1"), 3),  # 음수 IV — 뺀다
    ]
    rows = krx_rows(raw)
    assert [(r.strike, r.cp, r.iv_pct, r.volume) for r in rows] == [
        (Decimal("1100"), "C", 34.0, 86),
        (Decimal("1100"), "P", None, 0),
    ]
    assert daily_ts(TODAY) - timedelta(minutes=1) == datetime(2026, 10, 13, 15, 44, tzinfo=KST)

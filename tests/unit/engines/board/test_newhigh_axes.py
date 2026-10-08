"""신고가 3축 경계 사례(ADR 0017 — 사용자 요청 2026-10-08, 사양 '시험' 절의 필수 사례).

축: d120 = 판정일 직전 120 **시장 거래일**(창의 경계는 시장 달력 — 거래정지로 늘어나지 않는다), w52
= 달력 52주(판정일 − 364일 ≤ 봉 < 판정일), hist = 상장 이후 전체. 비교는 엄격 >(같은 값은 아니다).
우선순위 hist > w52 > d120, 한 종목은 가장 센 축 하나에만.

시장 달력은 평일에서 합성 휴장일을 뺀 것이다(값은 전부 가짜 — 공개 레포).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from kbj.engines.board import newhigh as nh
from kbj.engines.board.build import BoardInputs, compute_day
from kbj.engines.board.config import BoardConfig

CFG = BoardConfig.load()
D = date(2026, 10, 7)  # 판정일(수)
# 합성 휴장일 — 추석 주(2026-09-24·25)·대체공휴일(10-05)·광복절(2025-08-15)·한글날 주
# 하루(2025-10-09)
HOLIDAYS = frozenset(
    {date(2026, 9, 24), date(2026, 9, 25), date(2026, 10, 5), date(2025, 8, 15), date(2025, 10, 9)}
)
START = date(2024, 6, 3)


def _market(end: date = D) -> list[date]:
    out: list[date] = []
    d = START
    while d <= end:
        if d.weekday() < 5 and d not in HOLIDAYS:
            out.append(d)
        d += timedelta(days=1)
    return out


MARKET = _market()
BEFORE = [d for d in MARKET if d < D]  # 판정일 전 시장 거래일
T120, T121 = BEFORE[-120], BEFORE[-121]  # 120번째·121번째 이전 거래일
# 횡보 100 · 봉우리 120 · 당일 115 — 하루 등락이 ±31% 안이라 수정주가 가드에 걸리지 않는다
PEAK, TODAY = 120.0, 115.0


def rows_for(
    days: list[date], peaks: dict[date, float] | None = None, today: float = TODAY
) -> list[dict[str, Any]]:
    """그 날들의 종가=고가=100(봉우리는 peaks), 마지막 날은 today."""
    peaks = peaks or {}
    out = []
    for d in days:
        v = today if d == days[-1] else peaks.get(d, 100.0)
        out.append(dict(asof=d.isoformat(), open=v, high=v, low=v, close=v, volume=1000.0))
    return out


def ev(rows: list[dict[str, Any]], asof: date = D, **kw: Any) -> dict[str, Any]:
    got = nh.evaluate(rows, asof, CFG, calendar=MARKET, **kw)
    assert got is not None
    return got


def basis(e: dict[str, Any], b: str = "close") -> dict[str, Any]:
    return e["basis"][b]


# ── 52주: 정확히 364일 전 포함 / 365일 전 제외 ────────────────────────────────────────


def test_52주_창은_364일_전_봉을_넣고_365일_전_봉은_뺀다() -> None:
    d364, d365 = D - timedelta(days=364), D - timedelta(days=365)
    assert d364 in MARKET and d365 in MARKET  # 둘 다 시장 거래일(수·화)
    inside = basis(ev(rows_for(MARKET, {d364: PEAK})))
    assert inside["refs"]["w52"] == PEAK and inside["hit"]["w52"] is False
    assert inside["refs"]["d120"] == 100.0 and inside["hit"]["d120"] is True
    assert inside["label"] == "d120"
    outside = basis(ev(rows_for(MARKET, {d365: PEAK})))
    assert outside["refs"]["w52"] == 100.0 and outside["hit"]["w52"] is True
    assert outside["label"] == "w52"


# ── 120일: 120번째 이전 거래일 포함 / 121번째 제외 ─────────────────────────────────────


def test_120일_창은_120번째_이전_거래일을_넣고_121번째는_뺀다() -> None:
    inside = basis(ev(rows_for(MARKET, {T120: PEAK})))
    assert inside["refs"]["d120"] == PEAK and inside["hit"]["d120"] is False
    assert inside["label"] is None  # 52주 창에도 200 이 있다
    outside = basis(ev(rows_for(MARKET, {T121: PEAK})))
    assert outside["refs"]["d120"] == 100.0 and outside["hit"]["d120"] is True
    assert outside["refs"]["w52"] == PEAK and outside["label"] == "d120"
    assert nh.window_bounds(nh.MarketDays(MARKET), D.isoformat(), CFG)["d120"] == (
        T120.isoformat(),
        T120.isoformat(),
    )


# ── 공휴일이 낀 주 ──────────────────────────────────────────────────────────────────


def test_공휴일은_120거래일에_세지_않는다() -> None:
    # 창 안에 휴장일 3일(9/24·25·10/5) — 평일로 세면 120번째 날이 3거래일 늦게(최근으로) 잡힌다
    weekdays = [d for d in (D - timedelta(days=i) for i in range(1, 400)) if d.weekday() < 5]
    naive = weekdays[119]
    assert T120 < naive and len([d for d in BEFORE if T120 <= d < naive]) == 3
    # 휴장일을 빼고 센 120번째 날(T120)의 봉우리는 창 안이다
    e = basis(ev(rows_for(MARKET, {T120: PEAK})))
    assert e["refs"]["d120"] == PEAK


def test_52주_시작일이_휴장일이면_그_뒤_첫_거래일에_상장한_종목도_창을_채운다() -> None:
    d3 = date(2025, 8, 15) + timedelta(days=364)  # 2026-08-14(금) — 364일 전이 광복절(휴장)
    mk = [d for d in MARKET if d <= d3]
    first = next(d for d in mk if d > date(2025, 8, 15))  # 2025-08-18(월)
    listed_first = rows_for([d for d in mk if d >= first])
    got = basis(ev(listed_first, d3))
    assert got["refs"]["w52"] == 100.0 and got["hit"]["w52"] is True
    # 하루 늦게 상장했으면 52주를 못 채운다(판정하지 않는다)
    later = rows_for([d for d in mk if d > first])
    assert basis(ev(later, d3))["refs"]["w52"] is None


# ── 거래정지 기간이 창 안에 있는 종목 ─────────────────────────────────────────────────


def test_거래정지로_120일_창이_늘어나지_않는다() -> None:
    halted = set(BEFORE[-110:-90])  # 창 안 20거래일 정지
    days = [d for d in MARKET if d not in halted]
    rows = rows_for(days, {T121: PEAK})
    e = basis(ev(rows))
    # 창 = 시장 거래일 120일 — 그 안의 그 종목 봉 100개만. 121번째 날 봉우리는 밖이다
    assert e["refs"]["d120"] == 100.0 and e["hit"]["d120"] is True
    assert len([r for r in rows if T120.isoformat() <= r["asof"] < D.isoformat()]) == 100
    # (시장 달력 없이 그 종목 봉 120개로 세면 정지 기간만큼 창이 늘어 봉우리를 먹는다 — 그 차이)
    no_cal = nh.evaluate(rows, D, CFG)
    assert no_cal is not None and basis(no_cal)["refs"]["d120"] == PEAK


# ── 상장 100거래일 신규상장: 120일 판정 불가 · hist 가능 ───────────────────────────────


def test_상장_100거래일_신규상장은_120일_판정을_못_하고_역사적만_된다() -> None:
    days = [*BEFORE[-100:], D]
    rows = rows_for(days)
    at = nh.roll_alltime(None, rows, CFG)
    ref, why = nh.hist_ref_for(at, D)
    assert why == "" and ref == {"high": 100.0, "close": 100.0}
    e = ev(rows, hist_ref=ref, hist_days=at["n_days"])
    b = basis(e)
    assert b["refs"]["d120"] is None and b["refs"]["w52"] is None  # 지어내지 않는다
    assert b["hit"]["d120"] is False and b["gap"]["d120"] is None
    assert b["hit"]["hist"] is True and b["label"] == "hist"


def test_상장_120거래일이면_120일을_판정하고_119거래일이면_못_한다() -> None:
    assert basis(ev(rows_for([*BEFORE[-120:], D])))["refs"]["d120"] == 100.0
    assert basis(ev(rows_for([*BEFORE[-119:], D])))["refs"]["d120"] is None


# ── 당일 봉이 창에 섞이지 않음 ──────────────────────────────────────────────────────


def test_당일과_그_뒤_봉은_창에_들어가지_않는다() -> None:
    rows = rows_for(MARKET)  # 당일 115
    b = basis(ev(rows))
    assert b["refs"] == {"d120": 100.0, "w52": 100.0, "hist": None}
    assert b["gap"]["d120"] == -15.0
    # 하루 전을 판정일로 보면 그 뒤(당일 115) 봉은 보지 않는다
    prev = BEFORE[-1]
    rows2 = [*rows_for([*MARKET[:-1]], today=100.0), rows[-1]]
    b2 = basis(ev(rows2, prev))
    assert b2["refs"]["d120"] == 100.0 and b2["hit"]["d120"] is False


def test_같은_값은_신고가가_아니다() -> None:
    b = basis(ev(rows_for(MARKET, today=100.0)))
    assert b["hit"] == {"hist": False, "w52": False, "d120": False}
    assert b["gap"]["d120"] == 0.0 and b["label"] is None
    assert nh.proximity_kind(ev(rows_for(MARKET, today=100.0)), "close", CFG) is None


# ── 우선순위·중복 제거 ─────────────────────────────────────────────────────────────


def test_우선순위는_역사적_52주_120일이고_가장_센_축_하나만() -> None:
    rows = rows_for(MARKET)
    allthree = basis(ev(rows, hist_ref={"high": 110.0, "close": 110.0}, hist_days=500))
    assert allthree["hit"] == {"hist": True, "w52": True, "d120": True}
    assert (allthree["label"], allthree["rank"]) == ("hist", 0)
    two = basis(ev(rows, hist_ref={"high": 300.0, "close": 300.0}, hist_days=500))
    assert (two["label"], two["rank"]) == ("w52", 1)
    one = basis(ev(rows_for(MARKET, {T121: PEAK})))
    assert (one["label"], one["rank"]) == ("d120", 2)


def _snap(code: str, close: float) -> dict[str, Any]:
    return dict(
        name=f"합성{code[-2:]}", market="KOSPI", close=close, chg_pct=1.0, volume=1000.0,
        turnover=100.0, mktcap=5000.0, turnover_is_estimate=0, source="krx",
    )  # fmt: skip


def test_보드는_한_종목을_한_줄_한_라벨로만_담는다() -> None:
    series = {
        "000010": rows_for(MARKET),  # 셋 다 → 역사적
        "000020": rows_for(MARKET),  # 52주·120일 → 52주
        "000030": rows_for(MARKET, {T121: PEAK}),  # 120일만
        "000040": rows_for(MARKET, today=100.0),  # 같은 값 — 없음
    }
    alltime = {c: dict(nh.roll_alltime(None, rows, CFG)) for c, rows in series.items()}
    alltime["000020"].update(prev_hi=300.0, prev_cl=300.0)
    day = compute_day(
        BoardInputs(
            asof=D,
            prev_asof=BEFORE[-1],
            series=series,
            snapshot={c: _snap(c, rows[-1]["close"]) for c, rows in series.items()},
            snap_asof=D,
            generated_at="2026-10-07T16:20:00+09:00",
            alltime=alltime,
            trading_days=MARKET,
        ),
        CFG,
    )
    ach = day.newhigh["achieved"]
    assert [r["code"] for r in ach] == ["000010", "000020", "000030"]  # 중복 없이 우선순위 순
    assert [r["label"] for r in ach] == ["hist", "w52", "d120"]
    keys = [(x.code, x.basis) for x in day.labels]
    assert len(keys) == len(set(keys)) == 3  # 기본 기준이 종가라 (코드, 기준) 한 줄
    assert day.newhigh["counts"] == {"hist": 1, "w52": 2, "d120": 3}  # 상위가 하위를 포함
    assert day.newhigh["thresholds"]["lookback_trading_days"] == {"d120": 120}
    assert day.newhigh["thresholds"]["lookback_calendar_days"] == {"w52": 364}
    assert day.newhigh["labels"] == {"hist": "역사적", "w52": "52주", "d120": "120일"}


def test_이력_품질은_종목별_메모와_범위로만_남는다() -> None:
    """상장일에 못 닿은 종목은 hist 보류 + 메모, 원천 바닥에 닿은 종목은 판정하고 수만 센다."""
    series = {c: rows_for(MARKET) for c in ("000010", "000020", "000030")}
    floor = CFG["newhigh"]["hist_source_floor"]
    at = {c: dict(nh.roll_alltime(None, rows, CFG)) for c, rows in series.items()}
    at["000010"]["history_from"] = floor  # 바닥에 닿음(상장 1975) → 판정, 바닥 기준
    at["000020"]["history_from"] = MARKET[0].isoformat()  # 상장일 전에서 끊김 → 보류
    at["000030"]["history_from"] = MARKET[0].isoformat()  # 상장일이 이력 안 → 상장 기준
    listed = {"000010": "1975-06-11", "000020": "1999-05-03", "000030": "2024-07-01"}
    day = compute_day(
        BoardInputs(
            asof=D,
            prev_asof=BEFORE[-1],
            series=series,
            snapshot={c: _snap(c, TODAY) for c in series},
            snap_asof=D,
            generated_at="2026-10-07T16:20:00+09:00",
            alltime=at,
            listed_on=listed,
            trading_days=MARKET,
        ),
        CFG,
    )
    rows = {r["code"]: r for r in day.universe["stocks"]}
    assert rows["000010"]["label"] == "hist" and rows["000010"]["hist_note"] is None
    assert (
        rows["000020"]["label"] == "w52" and rows["000020"]["hist_note"] == nh.HIST_BEFORE_LISTING
    )
    assert rows["000030"]["label"] == "hist" and rows["000030"]["hist_note"] is None
    scope = day.hist_scope
    assert (scope["n_since_floor"], scope["source_floor"], scope["n_before_listing"]) == (
        1,
        floor,
        1,
    )

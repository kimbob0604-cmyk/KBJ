"""core.metrics.vol — 기간구조·25Δ 스큐·IV 랭크·HV20 (docs/metrics.md §5.2~§5.5).

- 기간구조: 0DTE·차기 위클리·월물 칸 고르기(오늘 만기 위클리는 0DTE 칸, 월물 만기일은 0DTE 와
  월물 두 칸), 없는 칸 null·ok, 값은 §3.7 ATM IV 그대로
- 25Δ 스큐: 알려진 스마일 합성 체인 — 평평한 스마일 0, 풋·콜 따로 평평하면 σ_p − σ_c, 델타에
  선형인 스마일(σ = a + b·Δ 를 행사가마다 풀어 만든 체인)이면 보간이 정확해
  (a − 0.25b) − (c + 0.25d). 보간 구간 밖 null(ok)·F 없음 invalid·쓴 점의 품질 합성
- ATM IV 표 입력(`atm_iv_from_points`)은 `core.levels.atm_iv` 와 같다. KRX 일별 ATM IV: 당일 거래
  있는 행만 — 종가 패리티 F, IMP_VOLT 로 §3.7
- IV 랭크·퍼센타일: 손계산 값, 창(252거래일·오늘 포함·창 밖·오늘 날짜 옛 값 무시), n < 20 null,
  정확히 20 계산, n < 252 estimated, 두 원천 섞음 estimated, max = min 랭크 null
- HV20: 표본표준편차 × √252, 20개 안 되면(빠진 날 포함) null, 끝 = as-of 까지의 마지막 정산가,
  롤(최종거래일 직전 거래일 장 마감에 차월물 — 월물 간 가격 차는 수익률에 안 든다). IV − HV
"""

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from core.calendar import KST, TradingCalendar, expiry_at
from core.forward import ForwardResult, time_to_expiry
from core.gex import CallPut, ExpiryEval, OptionEval, OptionQuote
from core.greeks import greeks
from core.iv import IvResult
from core.levels import atm_iv
from core.metrics.vol import (
    TERM_SLOTS,
    DailyIv,
    DeltaPoint,
    IvSource,
    KrxIvRow,
    Settlement,
    atm_iv_from_points,
    held_contract,
    interpolate_at_delta,
    iv_minus_hv,
    iv_rank,
    krx_atm_iv,
    realized_vol,
    skew_25d,
    term_structure,
)
from core.preprocess import Quality

D = Decimal
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=KST)
EXP = date(2026, 10, 8)
F0 = 1100.0
STRIKES = [1000.0 + 5 * i for i in range(41)]  # 1000~1200

Smile = Callable[[float, CallPut], float]  # (행사가, 콜/풋) → σ


def chain(
    smile: Smile,
    *,
    F: float | None = F0,
    strikes: Sequence[float] = STRIKES,
    sides: Sequence[CallPut] = ("C", "P"),
    expiry: str = "202610",
    ed: date = EXP,
    now: datetime = NOW,
    iv_quality: dict[tuple[float, CallPut], IvResult] | None = None,
) -> ExpiryEval:
    """합성 만기 — 행사가·콜풋마다 smile σ 로 자체 그릭스. iv_quality 로 종목 IV 결과를 덮는다."""
    t = time_to_expiry(now, expiry_at(ed))
    opts: list[OptionEval] = []
    for k in strikes:
        for cp in sides:
            q = OptionQuote(
                expiry=expiry, expiry_date=ed, strike=D(repr(k)), cp=cp, last=D(1), oi=100
            )
            if F is None:
                opts.append(OptionEval(q, q.price(), None, None, True, "no_forward"))
                continue
            s = smile(k, cp)
            iv = (iv_quality or {}).get((k, cp), IvResult(s, "ok", "model", None))
            assert iv.sigma is not None
            g = greeks("c" if cp == "C" else "p", F, k, t, iv.sigma)
            opts.append(OptionEval(q, q.price(), iv, g, False, None))
    fwd = (
        ForwardResult(None, "invalid", None, (), None, ("no_parity_strikes",))
        if F is None
        else ForwardResult(F, "ok", D(1100), (D(1100),), None, ())
    )
    return ExpiryEval(expiry, ed, fwd, time_to_expiry(now, expiry_at(ed)), tuple(opts))


# ── 기간구조 (§5.2) ──


def _flat(s: float) -> Smile:
    return lambda _k, _cp: s


def test_term_structure_picks_0dte_next_weekly_and_nearest_monthly() -> None:
    td = date(2026, 10, 5)  # 월 — WKM 만기일
    wkm = chain(_flat(0.30), expiry="261001", ed=td)
    wki = chain(_flat(0.25), expiry="261002", ed=date(2026, 10, 8))
    month = chain(_flat(0.22), expiry="202610", ed=date(2026, 10, 15))
    later = chain(_flat(0.21), expiry="202611", ed=date(2026, 11, 12))
    pts = term_structure(
        [("weekly", wkm), ("weekly", wki), ("monthly", later), ("monthly", month)], td
    )
    assert [p.slot for p in pts] == list(TERM_SLOTS)
    zero, nxt, mon = pts
    assert (zero.expiry, nxt.expiry, mon.expiry) == ("261001", "261002", "202610")
    assert zero.value == pytest.approx(0.30) and nxt.value == pytest.approx(0.25)
    assert mon.value == pytest.approx(0.22) and mon.atm == atm_iv(month)
    assert all(p.quality == "ok" for p in pts)


def test_term_structure_empty_slots_are_null_and_ok() -> None:
    td = date(2026, 10, 6)  # 0DTE 없음, 위클리 없음
    month = chain(_flat(0.22), expiry="202610", ed=date(2026, 10, 15))
    zero, nxt, mon = term_structure([("monthly", month)], td)
    for p in (zero, nxt):
        assert (p.expiry, p.expiry_date, p.atm) == (None, None, None)
        assert (p.value, p.quality) == (None, "ok")
    assert mon.value == pytest.approx(0.22)
    assert all(p.value is None for p in term_structure([], td))


def test_monthly_expiry_day_fills_both_0dte_and_monthly_and_skips_expiring_weekly() -> None:
    td = date(2026, 10, 8)  # 목 — 월물 만기일(위클리 WKI 도 오늘 만기라고 두면 0DTE 는 이른 코드)
    month = chain(_flat(0.40), expiry="202610", ed=td)
    wki = chain(_flat(0.35), expiry="261002", ed=td)
    nxt = chain(_flat(0.30), expiry="261003", ed=date(2026, 10, 15))
    zero, weekly, mon = term_structure([("monthly", month), ("weekly", wki), ("weekly", nxt)], td)
    assert zero.expiry == "202610"  # 같은 만기일이면 만기 코드가 이른 것
    assert weekly.expiry == "261003"  # 오늘 만기 위클리는 차기가 아니다
    assert mon.expiry == "202610"  # 가장 가까운 월물 — 오늘 만기 포함


def test_term_structure_passes_atm_quality_and_rejects_bad_input() -> None:
    td = date(2026, 10, 6)
    no_f = chain(_flat(0.22), F=None, expiry="202610", ed=date(2026, 10, 15))
    (_, _, mon) = term_structure([("monthly", no_f)], td)
    assert (mon.value, mon.quality) == (None, "invalid")
    with pytest.raises(ValueError, match="같은 만기"):
        term_structure([("monthly", no_f), ("weekly", no_f)], td)
    with pytest.raises(ValueError, match="종류"):
        term_structure([("quarterly", no_f)], td)  # pyright: ignore[reportArgumentType]
    with pytest.raises(TypeError):
        term_structure([], datetime(2026, 10, 6, tzinfo=KST))


# ── 25Δ 스큐 (§5.3) ──


def test_flat_smile_has_zero_skew() -> None:
    s = skew_25d(chain(_flat(0.2)))
    assert s.value == pytest.approx(0.0, abs=1e-15)
    assert s.put_iv == pytest.approx(0.2) and s.call_iv == pytest.approx(0.2)
    assert s.quality == "ok" and s.reasons == ()
    assert len(s.put_points) == 2 and len(s.call_points) == 2


def test_split_flat_smile_gives_the_put_minus_call_level() -> None:
    s = skew_25d(chain(lambda _k, cp: 0.28 if cp == "P" else 0.21))
    assert s.value == pytest.approx(0.07, abs=1e-12)


def _solve_linear_in_delta(k: float, cp: CallPut, a: float, b: float, t: float) -> float:
    """σ = a + b·Δ(σ) 를 만족하는 σ(이분법) — 이 체인의 스마일은 델타에 선형이다."""
    lo, hi = 0.01, 3.0

    def gap(s: float) -> float:
        return s - (a + b * greeks("c" if cp == "C" else "p", F0, k, t, s).delta)

    for _ in range(200):
        mid = (lo + hi) / 2
        if (gap(lo) > 0) == (gap(mid) > 0):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def test_smile_linear_in_delta_is_interpolated_exactly() -> None:
    t = time_to_expiry(NOW, expiry_at(EXP))
    a, b = 0.20, -0.16  # 풋: Δ −0.25 → 0.24
    c, d = 0.20, -0.08  # 콜: Δ +0.25 → 0.18
    sigmas = {
        (k, cp): _solve_linear_in_delta(k, cp, *((a, b) if cp == "P" else (c, d)), t)
        for k in STRIKES
        for cp in ("C", "P")
    }
    s = skew_25d(chain(lambda k, cp: sigmas[(k, cp)]))
    assert s.put_iv == pytest.approx(a - 0.25 * b, abs=1e-9)
    assert s.call_iv == pytest.approx(c + 0.25 * d, abs=1e-9)
    assert s.value == pytest.approx((a - 0.25 * b) - (c + 0.25 * d), abs=1e-9)


def test_skew_is_null_outside_the_grid() -> None:
    near = [1090.0, 1095.0, 1100.0, 1105.0, 1110.0]  # 델타 0.3~0.7 — ±0.25 를 못 덮는다
    s = skew_25d(chain(_flat(0.2), strikes=near))
    assert (s.value, s.quality) == (None, "ok")
    assert s.reasons == ("put_out_of_range", "call_out_of_range")
    calls_only = skew_25d(chain(_flat(0.2), sides=("C",)))
    assert calls_only.value is None and calls_only.reasons == ("no_put_points",)
    assert calls_only.call_iv == pytest.approx(0.2)
    no_f = skew_25d(chain(_flat(0.2), F=None))
    assert (no_f.value, no_f.quality, no_f.reasons) == (None, "invalid", ("no_forward",))


def test_skew_quality_is_that_of_the_points_used() -> None:
    base = chain(_flat(0.2))
    s = skew_25d(base)
    used = s.put_points[0]
    est = IvResult(0.2, "estimated", "kis", "below_intrinsic")
    far = D("1000.0")  # 보간에 쓰지 않는 행사가
    assert far not in s.put_points
    worse = skew_25d(chain(_flat(0.2), iv_quality={(float(used), "P"): est}))
    assert worse.quality == "estimated"
    same = skew_25d(chain(_flat(0.2), iv_quality={(float(far), "P"): est}))
    assert same.quality == "ok"


@pytest.mark.parametrize("fq", ["stale", "estimated"])
def test_skew_quality_includes_the_forward_quality(fq: Quality) -> None:
    """§0 합성 — 델타·IV 는 그 만기 F 로 구했다: F 품질이 나쁘면 스큐도 그 이상(§3.7 ATM IV 와
    같게 — 베이시스 이월·선물 교차 확인 estimated F). 값이 없는 칸(구간 밖 null)은 해당 없음이라
    ok 그대로. F 가 있으면 F 품질은 invalid 가 아니다(ForwardResult 규칙)."""

    def with_f_quality(ev: ExpiryEval) -> ExpiryEval:
        return replace(ev, forward=replace(ev.forward, quality=fq, reasons=("few_strikes",)))

    ev = with_f_quality(chain(_flat(0.2)))
    s = skew_25d(ev)
    assert s.value == pytest.approx(0.0, abs=1e-15) and s.quality == fq
    assert atm_iv(ev).quality == fq  # ATM IV 와 같은 규칙
    near = chain(_flat(0.2), strikes=[1090.0, 1095.0, 1100.0, 1105.0, 1110.0])
    out = skew_25d(with_f_quality(near))
    assert (out.value, out.quality) == (None, "ok")


def test_interpolate_at_delta_rules() -> None:
    def p(delta: float, iv: float, k: str = "1100") -> DeltaPoint:
        return DeltaPoint(D(k), "C", delta, iv, "ok")

    pts = [p(0.1, 0.30, "1150"), p(0.4, 0.20, "1110")]
    got = interpolate_at_delta(pts, 0.25)
    assert got is not None and got[0] == pytest.approx(0.25) and len(got[1]) == 2
    exact = interpolate_at_delta([*pts, p(0.25, 0.26, "1130"), p(0.25, 0.24, "1131")], 0.25)
    assert exact is not None and exact[0] == pytest.approx(0.25) and len(exact[1]) == 2
    assert interpolate_at_delta(pts, 0.05) is None and interpolate_at_delta(pts, 0.45) is None
    assert interpolate_at_delta([], 0.25) is None
    with pytest.raises(ValueError):
        interpolate_at_delta(pts, math.nan)
    with pytest.raises(ValueError):
        DeltaPoint(D(1), "C", 0.3, 0.0, "ok")
    with pytest.raises(ValueError):
        skew_25d(chain(_flat(0.2)), delta=1.5)


# ── ATM IV 표 입력·KRX 일별 (§3.7·§5.4 백필) ──


def test_atm_iv_from_points_matches_levels_atm_iv() -> None:
    """만기 평가가 없는 표 입력도 §3.7 규칙이 `core.levels.atm_iv` 와 같다."""
    cases = [
        chain(lambda k, _cp: 0.2 + (1100.0 - k) * 0.001),  # 보간
        chain(_flat(0.2), strikes=[1100.0]),  # F = 행사가
        chain(_flat(0.2), strikes=[1080.0, 1090.0]),  # F 가 행사가 범위 밖 — one_side
        chain(_flat(0.2), sides=("C",)),  # one_leg
        chain(_flat(0.2), F=None),
        chain(_flat(0.2), iv_quality={(1100.0, "C"): IvResult(0.2, "estimated", "kis", "x")}),
    ]
    for ev in cases:
        table: dict[Decimal, list[tuple[float, Quality]]] = {}
        for o in ev.options:
            row = table.setdefault(o.quote.strike, [])
            if o.iv is not None and o.iv.sigma is not None:
                row.append((o.iv.sigma, o.quality))
        assert atm_iv_from_points(ev.forward.F, ev.forward.quality, table) == atm_iv(ev)


def _krx(k: str, cp: CallPut, close: str | None, iv: float | None, vol: int = 10) -> KrxIvRow:
    return KrxIvRow(D(k), cp, None if close is None else D(close), iv, vol)


def test_krx_atm_iv_uses_traded_rows_parity_forward_and_imp_volt() -> None:
    # 종가 패리티 C − P + K = 1101 (1095·1100·1105), 1105 풋은 거래 없음(IV 가 있어도 안 쓴다)
    rows = [
        _krx("1095", "C", "14.00", 21.0),
        _krx("1095", "P", "8.00", 23.0),
        _krx("1100", "C", "11.00", 20.0),
        _krx("1100", "P", "10.00", 22.0),
        _krx("1105", "C", "8.50", 19.0),
        _krx("1105", "P", "12.50", 99.0, vol=0),
        _krx("1110", "C", None, 42.1, vol=0),  # 거래 없는 행 — KRX 가 만기 공통값을 붙인다
    ]
    got = krx_atm_iv(rows, D("1098.0"))
    assert got.forward.F == pytest.approx(1101.0)
    assert list(got.forward.strikes) == [D("1095"), D("1100")]  # 1105 풋 가격이 없다
    assert "no_futures_ref" in got.forward.notes and got.forward.quality == "estimated"
    # F 1101 은 1100(0.21) 과 1105(콜만 0.19 — one_leg) 사이
    assert got.atm.value == pytest.approx(0.21 + (1101 - 1100) / 5 * (0.19 - 0.21))
    assert got.atm.strikes == (D("1100"), D("1105")) and "one_leg" in got.atm.reasons
    assert got.atm.quality == "estimated"


def test_krx_atm_iv_without_trades_is_invalid_and_duplicates_raise() -> None:
    idle = krx_atm_iv([_krx("1100", "C", "11", 20.0, vol=0)], 1100.0)
    assert idle.forward.F is None and (idle.atm.value, idle.atm.quality) == (None, "invalid")
    with pytest.raises(ValueError, match="두 번"):
        krx_atm_iv([_krx("1100", "C", "11", 20.0), _krx("1100", "C", "12", 21.0)], 1100.0)
    with pytest.raises(ValueError):
        _krx("1100", "C", "11", 0.0)


# ── IV 랭크·퍼센타일 (§5.4) ──

CAL = TradingCalendar.default()
TODAY = date(2026, 10, 13)  # 화


def _days(n: int, end: date = TODAY) -> list[date]:
    """end 앞(end 제외) 거래일 n 개, 오래된 것부터."""
    out: list[date] = []
    d = end
    for _ in range(n):
        d = CAL.prev_trading_day(d)
        out.append(d)
    return out[::-1]


def _hist(values: Sequence[float], source: IvSource = "self") -> list[DailyIv]:
    return [DailyIv(d, v, source, "ok") for d, v in zip(_days(len(values)), values, strict=True)]


def test_iv_rank_and_percentile_values() -> None:
    hist = _hist([0.10 + 0.01 * i for i in range(25)])  # 0.10 ~ 0.34
    r = iv_rank(hist, DailyIv(TODAY, 0.22, "self", "ok"), CAL)
    assert r.n == 26 and r.end == TODAY
    assert r.rank == pytest.approx((0.22 - 0.10) / (0.34 - 0.10))
    assert r.percentile == pytest.approx(12 / 26)  # 0.10 ~ 0.21 이 0.22 보다 작다
    assert r.quality == "estimated" and r.reasons == ("short_window",)  # n < 252


def test_iv_rank_window_rules() -> None:
    full = _hist([0.2 + 0.001 * (i % 7) for i in range(251)])
    r = iv_rank(full, DailyIv(TODAY, 0.21, "self", "ok"), CAL)
    assert (r.n, r.quality, r.reasons) == (252, "ok", ())
    assert r.start == _days(251)[0]
    # 창 밖(253 거래일 전)·오늘 날짜의 옛 값은 무시한다
    older = DailyIv(CAL.prev_trading_day(r.start), 9.0, "self", "ok")
    stale_today = DailyIv(TODAY, 5.0, "self", "ok")
    again = iv_rank([older, *full, stale_today], DailyIv(TODAY, 0.21, "self", "ok"), CAL)
    assert again == r
    # n < 20 → null(estimated — 창이 짧다)
    few = iv_rank(_hist([0.2] * 18), DailyIv(TODAY, 0.21, "self", "ok"), CAL)
    assert (few.rank, few.percentile, few.n) == (None, None, 19)
    assert few.reasons == ("short_window", "too_few_days") and few.quality == "estimated"
    # 정확히 20 이면 계산
    twenty = iv_rank(
        _hist([0.2 + 0.01 * i for i in range(19)]), DailyIv(TODAY, 0.3, "self", "ok"), CAL
    )
    assert twenty.n == 20 and twenty.rank is not None
    # max = min → 랭크만 null
    flat = iv_rank(_hist([0.2] * 30), DailyIv(TODAY, 0.2, "self", "ok"), CAL)
    assert flat.rank is None and flat.percentile == 0.0 and "flat_window" in flat.reasons


def test_iv_rank_mixed_sources_and_today_quality() -> None:
    hist = [*_hist([0.2] * 20, "krx")[:10], *_hist([0.21] * 20)[10:]]
    full = iv_rank(
        [*_hist([0.2 + 0.001 * i for i in range(251)], "krx")],
        DailyIv(TODAY, 0.3, "self", "ok"),
        CAL,
    )
    assert full.quality == "estimated" and full.reasons == ("mixed_sources",)
    assert full.sources == ("krx", "self")
    part = iv_rank(hist, DailyIv(TODAY, 0.3, "self", "stale"), CAL)
    assert part.quality == "estimated" and "mixed_sources" in part.reasons
    only_self = iv_rank(
        _hist([0.2 + 0.001 * i for i in range(251)]), DailyIv(TODAY, 0.3, "self", "stale"), CAL
    )
    assert only_self.quality == "stale"
    with pytest.raises(ValueError, match="두 번"):
        iv_rank([*_hist([0.2] * 3), *_hist([0.2] * 3)], DailyIv(TODAY, 0.3, "self", "ok"), CAL)
    with pytest.raises(ValueError):
        DailyIv(TODAY, 0.0, "self", "ok")


# ── HV20·IV − HV (§5.5) ──

FRONT, NEXT = "202612", "202703"
LAST = {FRONT: date(2026, 12, 10), NEXT: date(2027, 3, 11)}


def _settles(
    returns: Sequence[float], end: date, contract: str = FRONT, p0: float = 1000.0
) -> list[Settlement]:
    """end 에서 끝나는 거래일 len(returns) + 1 개의 정산가 — 날마다 주어진 로그수익률."""
    days = [*_days(len(returns), end), end]
    prices = [p0]
    for r in returns:
        prices.append(prices[-1] * math.exp(r))
    return [Settlement(d, contract, p) for d, p in zip(days, prices, strict=True)]


def test_hv20_is_the_sample_stdev_of_log_returns_times_sqrt_252() -> None:
    rets = [0.01 if i % 2 else -0.01 for i in range(20)]
    hv = realized_vol(_settles(rets, TODAY), LAST, CAL)
    assert hv.value == pytest.approx(statistics.stdev(rets) * math.sqrt(252))
    assert hv.end == TODAY and len(hv.returns) == 20 and hv.contracts == (FRONT,) * 20
    assert hv.returns == pytest.approx(rets)
    # 더 오래된 정산가는 창 밖 — 같은 값
    longer = realized_vol(_settles([0.05] * 5 + rets, TODAY), LAST, CAL)
    assert longer.value == pytest.approx(hv.value)


def test_hv20_needs_twenty_returns() -> None:
    short = realized_vol(_settles([0.01] * 19, TODAY), LAST, CAL)
    assert short.value is None and short.reasons == ("missing_returns",)
    assert len(short.missing) == 1
    gap = [s for s in _settles([0.01] * 20, TODAY) if s.trade_date != _days(5)[2]]
    holed = realized_vol(gap, LAST, CAL)
    assert holed.value is None and len(holed.missing) == 2  # 빠진 날과 그다음 날
    assert realized_vol([], LAST, CAL).reasons == ("no_data",)


def test_hv20_end_is_the_latest_settlement_up_to_the_as_of_day() -> None:
    s = _settles([0.01 * (i % 3) for i in range(22)], TODAY)
    hv = realized_vol(s, LAST, CAL, end=CAL.prev_trading_day(TODAY))
    assert hv.end == CAL.prev_trading_day(TODAY) and hv.value is not None


def test_hv20_rolls_to_the_next_contract_the_day_before_the_last_trading_day() -> None:
    """롤 = 최종거래일 직전 거래일 장 마감(PLAN §9.3). 근월물 정산가는 롤 날(12-09)까지만,
    차월물은 롤 날부터 — 최종거래일(12-10) 수익률은 차월물로 잰다. 두 월물 가격 차(+8)는
    수익률에 들지 않는다."""
    roll = CAL.prev_trading_day(LAST[FRONT])
    assert roll == date(2026, 12, 9)
    end = date(2026, 12, 16)
    front_days = [d for d in [*_days(30, end), end] if d <= roll]
    after = [d for d in [*_days(30, end), end] if d >= roll]
    rets = {d: 0.004 * ((i % 5) - 2) for i, d in enumerate([*_days(30, end), end])}
    settles: list[Settlement] = []
    p = 1000.0
    for d in front_days:
        p *= math.exp(rets[d])
        settles.append(Settlement(d, FRONT, p))
    q = 1008.0
    for d in after:
        q *= math.exp(rets[d]) if d != roll else 1.0
        settles.append(Settlement(d, NEXT, q))
    hv = realized_vol(settles, LAST, CAL, end=end)
    assert hv.value is not None, hv.missing
    window = [*_days(19, end), end]
    assert hv.returns == pytest.approx([rets[d] for d in window])
    assert hv.contracts == tuple(FRONT if d <= roll else NEXT for d in window)
    assert held_contract(roll, LAST, CAL) == NEXT
    assert held_contract(CAL.prev_trading_day(roll), LAST, CAL) == FRONT


def test_iv_minus_hv() -> None:
    hv = realized_vol(_settles([0.01 if i % 2 else -0.01 for i in range(20)], TODAY), LAST, CAL)
    assert hv.value is not None
    x = iv_minus_hv(0.25, "estimated", hv)
    assert x.value == pytest.approx(0.25 - hv.value) and x.quality == "estimated"
    none_hv = iv_minus_hv(0.25, "ok", realized_vol([], LAST, CAL))
    assert (none_hv.value, none_hv.quality) == (None, "ok")
    no_iv = iv_minus_hv(None, "ok", hv)
    assert (no_iv.value, no_iv.quality) == (None, "invalid")

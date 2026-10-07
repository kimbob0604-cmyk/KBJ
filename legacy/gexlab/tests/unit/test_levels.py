"""core.levels — 핵심 레벨(docs/metrics.md §3): 콜월·풋월·절대감마, Flip·거리, 0DTE, ATM IV,
기대변동폭, 범위 안 상위 레벨, 만기별 감마.

체인은 그릭스를 직접 넣어 만든다(`make`) — 감마는 Black-76(core.greeks)이고, 동률 시험은 감마를
값으로 덮어 GEX 를 정확히 같게 한다.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from itertools import pairwise

import pytest

from core.calendar import KST, TradingCalendar, expiry_at
from core.forward import ForwardResult, time_to_expiry
from core.gex import (
    OPTION_MULTIPLIER,
    CallPut,
    ExpiryEval,
    OptionEval,
    OptionQuote,
    StrikeGex,
    StrikeGexTable,
    net_gex,
    option_gex,
)
from core.greeks import greeks
from core.iv import IvResult
from core.levels import (
    CALENDAR_MINUTES_PER_YEAR,
    FLIP_STEP,
    FLIP_ZERO_RULE,
    TOP_LEVELS,
    TRADING_MINUTES_PER_YEAR,
    AtmIv,
    ExpectedMove,
    GammaFlip,
    Wall,
    abs_gamma_strike,
    atm_iv,
    call_wall,
    expected_move,
    flip_distance_pct,
    flip_grid,
    gamma_by_expiry,
    gamma_flip,
    next_session,
    put_wall,
    reference_forward,
    scope_strike_gex,
    session_minutes,
    sign_crossings,
    top_levels_in_range,
    total_gex_at,
    zero_dte_levels,
)
from core.preprocess import Quality

D = Decimal
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=KST)
EXP = date(2026, 10, 8)  # 월물 202610
T = time_to_expiry(NOW, expiry_at(EXP))
F0 = 1100.0
UNIT = OPTION_MULTIPLIER * F0 * F0 * 0.01  # Γ·OI = 1 일 때 GEX(원/1%) = 3.025e9
OK_IV = IvResult(0.2, "ok", "model", None)
NO_IV = IvResult(None, "invalid", None, "below_intrinsic/kis_missing")

Leg = tuple[float, CallPut, int, float]  # 행사가, 콜/풋, OI, σ


def kst(*a: int) -> datetime:
    return datetime(*a, tzinfo=KST)  # pyright: ignore[reportArgumentType]


def _quote(k: float, cp: CallPut, oi: int, expiry: str, ed: date) -> OptionQuote:
    return OptionQuote(expiry=expiry, expiry_date=ed, strike=D(repr(k)), cp=cp, last=D(1), oi=oi)


def _forward(F: float | None, quality: Quality = "ok") -> ForwardResult:
    if F is None:
        return ForwardResult(None, "invalid", None, (), None, ("no_parity_strikes",))
    reasons = () if quality == "ok" else ("few_strikes",)
    return ForwardResult(F, quality, D(1100), (D(1100),), None, reasons)


def make(
    F: float | None,
    legs: Sequence[Leg],
    *,
    expiry: str = "202610",
    ed: date = EXP,
    now: datetime = NOW,
    gammas: Mapping[tuple[float, CallPut], float] | None = None,
    excluded: Sequence[tuple[float, CallPut, int]] = (),
    fq: Quality = "ok",
) -> ExpiryEval:
    """만기 하나. legs 는 GEX 에 드는 종목 — 그릭스는 F 에서 Black-76, gammas 로 감마만 덮는다.
    excluded 는 가격 없음으로 빠진 종목(OI 는 제외 비율에만)."""
    t = time_to_expiry(now, expiry_at(ed))
    opts: list[OptionEval] = []
    for k, cp, oi, sigma in legs:
        q = _quote(k, cp, oi, expiry, ed)
        if F is None:
            opts.append(OptionEval(q, q.price(), None, None, True, "no_forward"))
            continue
        g = greeks("c" if cp == "C" else "p", F, k, t, sigma)
        if gammas is not None and (k, cp) in gammas:
            g = replace(g, gamma=gammas[(k, cp)])
        opts.append(OptionEval(q, q.price(), IvResult(sigma, "ok", "model", None), g, False, None))
    for k, cp, oi in excluded:
        q = _quote(k, cp, oi, expiry, ed).model_copy(update={"last": None})
        opts.append(OptionEval(q, q.price(), None, None, True, "no_price"))
    opts.sort(key=lambda o: (o.quote.strike, o.quote.cp))
    return ExpiryEval(expiry, ed, _forward(F, fq), t, tuple(opts))


def fixed(F: float, calls: Mapping[float, float], puts: Mapping[float, float]) -> ExpiryEval:
    """행사가 → Γ·OI(OI 100)로 GEX 를 정확히 정한 만기. GEX_call = +v·UNIT, GEX_put = −v·UNIT."""
    legs: list[Leg] = []
    gammas: dict[tuple[float, CallPut], float] = {}
    sides: tuple[tuple[CallPut, Mapping[float, float]], ...] = (("C", calls), ("P", puts))
    for cp, side in sides:
        for k, v in side.items():
            legs.append((k, cp, 100, 0.2))
            gammas[(k, cp)] = v / 100
    return make(F, legs, gammas=gammas)


# --- 범위 합산 ---


def test_reference_forward_is_nearest_expiry_with_f() -> None:
    near = make(1100.0, [(1100.0, "C", 10, 0.2)], expiry="261001", ed=date(2026, 10, 1))
    mid = make(1104.0, [(1100.0, "C", 10, 0.2)], expiry="261002", ed=date(2026, 10, 6))
    far = make(1110.0, [(1100.0, "C", 10, 0.2)])
    assert reference_forward([far, mid, near]) == 1100.0
    # 최근접에 F 가 없으면 그다음 [확인 필요]
    no_f = make(None, [(1100.0, "C", 10, 0.2)], expiry="261001", ed=date(2026, 10, 1))
    assert reference_forward([far, mid, no_f]) == 1104.0
    assert reference_forward([no_f]) is None
    assert reference_forward([]) is None


def test_scope_strike_gex_sums_expiries() -> None:
    a = fixed(F0, {1100.0: 1.0, 1110.0: 2.0}, {1090.0: 0.5})
    b = replace(fixed(F0, {1100.0: 3.0}, {1100.0: 1.0}), expiry="202611")
    table = scope_strike_gex([a, b])
    assert (table.quality, table.excluded_oi_ratio) == ("ok", 0.0)
    rows = table.rows
    assert [r.strike for r in rows] == [D(1090), D(1100), D(1110)]
    assert rows[0] == StrikeGex(D(1090), 0.0, pytest.approx(-0.5 * UNIT))  # pyright: ignore[reportArgumentType]
    assert rows[1].gex_call == pytest.approx(4.0 * UNIT)
    assert rows[1].gex_put == pytest.approx(-1.0 * UNIT)
    # F 없는 만기는 빠진다, 같은 만기 두 번은 에러
    no_f = make(None, [(1200.0, "C", 10, 0.2)], expiry="261001", ed=date(2026, 10, 1))
    mixed = scope_strike_gex([a, b, no_f])
    assert (mixed.rows, mixed.quality) == (rows, "invalid")  # 품질은 범위 순GEX(§2.2)
    assert mixed.excluded_oi_ratio == net_gex([a, b, no_f]).excluded_oi_ratio
    with pytest.raises(ValueError):
        scope_strike_gex([a, a])


def test_scope_strike_gex_quality_is_scope_wide() -> None:
    # 만기 하나로는 제외 비율 > 10% 지만 범위 전체로는 아니다 — 범위 품질은 범위 비율로 잰다
    a = make(F0, [(1100.0, "C", 100, 0.2)], excluded=[(1200.0, "C", 20)])
    b = make(F0, [(1100.0, "P", 1000, 0.2)], expiry="202611", ed=date(2026, 11, 12))
    assert scope_strike_gex([a]).quality == "estimated"  # 20 / 120
    both = scope_strike_gex([a, b])
    assert (both.quality, both.excluded_oi_ratio) == ("ok", 20 / 1120)
    assert scope_strike_gex([a], max_excluded_ratio=0.2).quality == "ok"
    assert scope_strike_gex([]) == StrikeGexTable((), "ok", 0.0)


# --- 콜월·풋월·절대감마 ---


def test_walls_pick_maximum() -> None:
    ev = fixed(F0, {1100.0: 1.0, 1110.0: 3.0, 1120.0: 2.0}, {1080.0: 4.0, 1090.0: 1.5, 1110.0: 2.0})
    cw, pw, ag = call_wall([ev]), put_wall([ev]), abs_gamma_strike([ev])
    assert (cw.strike, cw.quality) == (D(1110), "ok")
    assert cw.value == option_gex("C", 0.03, 100, F0)
    assert pw.strike == D(1080)
    assert pw.value == -option_gex("P", 0.04, 100, F0)
    # 1110: 3 + 2 = 5 > 1080: 4
    assert ag.strike == D(1110)
    assert ag.value == pytest.approx(5.0 * UNIT)


def test_wall_tie_prefers_strike_nearer_f() -> None:
    ev = fixed(F0, {1090.0: 2.0, 1105.0: 2.0}, {1080.0: 1.0, 1095.0: 1.0})
    assert call_wall([ev]).strike == D(1105)  # |1105 − 1100| = 5 < 10
    assert put_wall([ev]).strike == D(1095)
    assert abs_gamma_strike([ev]).strike == D(1105)
    # 기준 F 를 바꾸면 동률 판정이 따라간다
    assert call_wall([ev], f_ref=1091.0).strike == D(1090)


def test_wall_tie_same_distance_takes_lower_strike() -> None:
    ev = fixed(F0, {1095.0: 2.0, 1105.0: 2.0}, {1095.0: 1.0, 1105.0: 1.0})
    assert call_wall([ev]).strike == D(1095)  # [확인 필요] core.chain ATM 동률 규칙과 같게
    assert put_wall([ev]).strike == D(1095)


def test_call_wall_null_when_call_oi_all_zero() -> None:
    ev = make(F0, [(1100.0, "C", 0, 0.2), (1110.0, "C", 0, 0.2), (1090.0, "P", 500, 0.2)])
    assert call_wall([ev]) == Wall(None, None, "ok")
    pw = put_wall([ev])
    assert pw.strike == D(1090)
    assert abs_gamma_strike([ev]).strike == D(1090)
    puts_zero = make(F0, [(1100.0, "C", 10, 0.2), (1090.0, "P", 0, 0.2)])
    assert put_wall([puts_zero]).strike is None
    assert call_wall([]) == Wall(None, None, "ok")


def test_walls_sum_strikes_across_expiries() -> None:
    a = fixed(F0, {1100.0: 1.0, 1110.0: 1.5}, {})
    b = replace(fixed(F0, {1100.0: 1.0}, {}), expiry="202611", expiry_date=date(2026, 11, 12))
    assert call_wall([a]).strike == D(1110)
    assert call_wall([a, b]).strike == D(1100)  # 1 + 1 = 2 > 1.5


def test_wall_quality_follows_scope() -> None:
    good = fixed(F0, {1100.0: 1.0}, {})
    no_f = make(None, [(1200.0, "C", 10, 0.2)], expiry="261001", ed=date(2026, 10, 1))
    w = call_wall([good, no_f])
    assert (w.strike, w.quality) == (D(1100), "invalid")  # 값은 F 있는 만기만으로
    # 가격 없는 OI 가 10% 초과 → estimated
    heavy = make(F0, [(1100.0, "C", 900, 0.2)], excluded=[(1200.0, "C", 101)])
    assert call_wall([heavy]).quality == "estimated"
    assert call_wall([heavy], max_excluded_ratio=0.2).quality == "ok"


def test_wall_invariants() -> None:
    with pytest.raises(ValueError):
        Wall(D(1100), None, "ok")
    with pytest.raises(ValueError):
        call_wall([fixed(F0, {1100.0: 1.0}, {})], f_ref=0.0)


# --- Flip ---


def _gamma_by_hand(F: float, K: float, t: float, sigma: float) -> float:
    """Black-76 감마(r = 0)를 식으로 — core.greeks 와 독립."""
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (F * sigma * math.sqrt(t))


def test_flip_grid() -> None:
    g = flip_grid(1100.0)
    assert (len(g), g[0], g[-1]) == (441, 1045.0, 1155.0)
    assert all(b - a == pytest.approx(FLIP_STEP) for a, b in pairwise(g))
    # 끝점 1.05F 가 격자에 없으면 덧붙인다 — 마지막 간격만 짧다 [확인 필요]
    h = flip_grid(1101.3)
    assert h[0] == pytest.approx(0.95 * 1101.3)
    assert h[-1] == 1.05 * 1101.3
    assert 0 < h[-1] - h[-2] < FLIP_STEP
    assert all(b - a == pytest.approx(FLIP_STEP) for a, b in pairwise(h[:-1]))
    assert len(flip_grid(1100.0, band=0.01, step=1.0)) == 23
    for bad in ({"f_ref": 0.0}, {"step": 0.0}, {"band": 1.0}, {"step": 1e-6}):
        with pytest.raises(ValueError):
            flip_grid(**({"f_ref": 1100.0} | bad))


def test_flip_single_crossing_interpolation() -> None:
    # 풋 1095(σ 22%)·콜 1110(σ 18%) — 아래는 풋 감마, 위는 콜 감마가 커서 한 번 교차
    legs: list[Leg] = [(1095.0, "P", 1000, 0.22), (1110.0, "C", 1000, 0.18)]
    fl = gamma_flip([make(F0, legs)])
    assert (fl.multi_cross, fl.f_ref, fl.quality, len(fl.crossings)) == (False, F0, "ok", 1)
    assert fl.level is not None
    # 둘러싼 격자점의 총 GEX 를 식으로 다시 계산해 선형보간
    x0 = 1045.0 + FLIP_STEP * math.floor((fl.level - 1045.0) / FLIP_STEP)
    x1 = x0 + FLIP_STEP

    def gex(x: float) -> float:
        c = _gamma_by_hand(x, 1110.0, T, 0.18)
        p = _gamma_by_hand(x, 1095.0, T, 0.22)
        return (c - p) * 1000 * OPTION_MULTIPLIER * x * x * 0.01

    g0, g1 = gex(x0), gex(x1)
    assert g0 < 0 < g1
    assert fl.level == pytest.approx(x0 + (x1 - x0) * g0 / (g0 - g1), rel=1e-12, abs=1e-9)
    assert dict(fl.profile)[x0] == pytest.approx(g0, rel=1e-9)
    assert fl.distance_pct == pytest.approx((F0 - fl.level) / F0 * 100)


def test_flip_matches_analytic_root() -> None:
    # 같은 σ·OI 면 Γ(F′, K_hi) = Γ(F′, K_lo) 는 F′ = √(K_lo·K_hi)·e^(−σ²T/2) 에서
    fl = gamma_flip([make(F0, [(1095.0, "P", 1000, 0.2), (1110.0, "C", 1000, 0.2)])])
    root = math.sqrt(1095 * 1110) * math.exp(-0.04 * T / 2)
    assert fl.level == pytest.approx(root, abs=1e-4)
    assert fl.distance_pct == pytest.approx((F0 - root) / F0 * 100, abs=1e-5)


def test_flip_profile_at_f_is_net_gex() -> None:
    ev = make(F0, [(1095.0, "P", 1000, 0.2), (1100.0, "C", 700, 0.21), (1110.0, "C", 300, 0.18)])
    fl = gamma_flip([ev])
    assert dict(fl.profile)[F0] == pytest.approx(net_gex([ev]).value, rel=1e-12)


def test_flip_all_calls_is_none() -> None:
    ev = make(F0, [(k, "C", 1000, 0.2) for k in (1090.0, 1100.0, 1110.0)])
    fl = gamma_flip([ev])
    assert (fl.level, fl.crossings, fl.multi_cross, fl.quality) == (None, (), False, "ok")
    assert fl.distance_pct is None
    assert all(g > 0 for _, g in fl.profile)
    puts = make(F0, [(k, "P", 1000, 0.2) for k in (1090.0, 1100.0, 1110.0)])
    assert gamma_flip([puts]).level is None


def _root(evs: list[ExpiryEval], lo: float, hi: float) -> float:
    """총 GEX(F′) 이분법 근 — 선형보간 교차점의 기준값."""
    glo = total_gex_at(evs, lo)
    assert glo is not None
    for _ in range(60):
        mid = (lo + hi) / 2
        gm = total_gex_at(evs, mid)
        assert gm is not None
        if (gm > 0) == (glo > 0):
            lo, glo = mid, gm
        else:
            hi = mid
    return (lo + hi) / 2


def test_flip_two_crossings_takes_nearest() -> None:
    legs: list[Leg] = [(1070.0, "C", 1000, 0.2), (1100.0, "P", 2000, 0.2), (1140.0, "C", 1000, 0.2)]
    evs = [make(F0, legs)]
    fl = gamma_flip(evs)
    assert fl.multi_cross is True
    lo_x, hi_x = fl.crossings
    assert lo_x < F0 < hi_x
    assert abs(hi_x - F0) < abs(lo_x - F0)
    assert fl.level == hi_x
    # 각 교차점은 참 근과 0.25pt 격자 보간 오차 안
    for c in fl.crossings:
        assert c == pytest.approx(_root(evs, c - FLIP_STEP, c + FLIP_STEP), abs=1e-3)


def test_flip_grid_edges() -> None:
    # 근 √(1040·1051)·e^(−σ²T/2) ≈ 1045.07 — 첫 격자 간격 [1045, 1045.25] 안
    inside = gamma_flip([make(F0, [(1040.0, "P", 1000, 0.2), (1051.0, "C", 1000, 0.2)])])
    assert inside.level is not None
    assert inside.profile[0][0] <= inside.level <= inside.profile[1][0]
    assert inside.level == pytest.approx(math.sqrt(1040 * 1051) * math.exp(-0.04 * T / 2), abs=1e-4)
    # 근 ≈ 1044.57 < 0.95F — 격자 전체가 같은 부호
    outside = gamma_flip([make(F0, [(1040.0, "P", 1000, 0.2), (1050.0, "C", 1000, 0.2)])])
    assert (outside.level, outside.crossings) == (None, ())
    # 근 ≈ 1154.90 — 마지막 간격 [1154.75, 1155] 안
    upper = gamma_flip([make(F0, [(1150.0, "P", 1000, 0.2), (1160.75, "C", 1000, 0.2)])])
    assert upper.level is not None
    assert upper.profile[-2][0] <= upper.level <= upper.profile[-1][0] == 1155.0
    beyond = gamma_flip([make(F0, [(1160.0, "P", 1000, 0.2), (1170.0, "C", 1000, 0.2)])])
    assert beyond.level is None


def test_flip_zero_run_from_gamma_underflow() -> None:
    # 0DTE 막판(T 하한 5분): 행사가에서 먼 F′ 는 감마가 정확히 0 → 가운데 0 구간
    ed = date(2026, 10, 1)
    now = kst(2026, 10, 1, 15, 16)
    legs: list[Leg] = [(1060.0, "P", 1000, 0.2), (1140.0, "C", 1000, 0.2)]
    fl = gamma_flip([make(F0, legs, expiry="261001", ed=ed, now=now)])
    zeros = [x for x, g in fl.profile if g == 0]
    assert len(zeros) > 10
    assert fl.crossings == ((zeros[0] + zeros[-1]) / 2,)  # 0 구간 가운데 [확인 필요]
    assert fl.multi_cross is False
    # 콜만이면 0 구간이 격자 끝에 붙어 교차 아님
    calls = make(F0, [(1150.0, "C", 1000, 0.2)], expiry="261001", ed=ed, now=now)
    fl2 = gamma_flip([calls])
    assert any(g == 0 for _, g in fl2.profile)
    assert fl2.level is None
    # 글자 그대로 해석(설정)이면 0 인 격자점마다 교차점 — 가짜 Flip 이 생긴다
    lit = gamma_flip([make(F0, legs, expiry="261001", ed=ed, now=now)], zero_rule="every_zero")
    assert (lit.crossings, lit.multi_cross) == (tuple(zeros), True)
    assert lit.level == min(zeros, key=lambda x: (abs(x - F0), x))
    lit2 = gamma_flip([calls], zero_rule="every_zero")
    assert lit2.crossings == tuple(x for x, g in fl2.profile if g == 0)
    z = zero_dte_levels([calls], ed, zero_rule="every_zero")
    assert z is not None and z.flip == lit2


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([1.0, 3.0], ()),
        ([1.0, -3.0], (0.25,)),  # 0 + 1 × 1/(1 + 3)
        ([-2.0, 2.0], (0.5,)),
        ([1.0, 0.0, -1.0], (1.0,)),  # 격자점에서 정확히 0 → 그 점
        ([1.0, 0.0, 0.0, -1.0], (1.5,)),  # 0 이 이어지면 가운데
        ([1.0, 0.0, 1.0], ()),  # 접함 [확인 필요]
        ([0.0, 1.0, -1.0], (1.5,)),  # 격자 끝의 0 은 교차 아님
        ([1.0, -1.0, 0.0], (0.5,)),
        ([0.0, 0.0, 0.0], ()),
        ([1.0, -1.0, 1.0], (0.5, 1.5)),
    ],
)
def test_sign_crossings(values: list[float], expected: tuple[float, ...]) -> None:
    assert sign_crossings([float(i) for i in range(len(values))], values) == expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([1.0, -3.0], (0.25,)),  # 0 이 없으면 기본 규칙과 같다
        ([1.0, 0.0, -1.0], (1.0,)),
        ([1.0, 0.0, 0.0, -1.0], (1.0, 2.0)),  # 0 인 점마다 — 가운데를 따로 넣지 않는다
        ([1.0, 0.0, 1.0], (1.0,)),  # 접함도 교차
        ([0.0, 1.0, -1.0], (0.0, 1.5)),  # 격자 끝의 0 도 교차
        ([0.0, 0.0, 0.0], (0.0, 1.0, 2.0)),
        ([1.0, -1.0, 0.0, 1.0], (0.5, 2.0)),
    ],
)
def test_sign_crossings_every_zero(values: list[float], expected: tuple[float, ...]) -> None:
    # §3.4 대안 해석 "0 인 격자점은 전부 교차점" — 설정으로만 [확인 필요]
    xs = [float(i) for i in range(len(values))]
    assert sign_crossings(xs, values, zero_rule="every_zero") == expected


def test_sign_crossings_default_rule() -> None:
    assert FLIP_ZERO_RULE == "sign_change"
    values = [1.0, 0.0, 1.0, 0.0, 0.0, -1.0]
    xs = [float(i) for i in range(len(values))]
    assert sign_crossings(xs, values) == sign_crossings(xs, values, zero_rule="sign_change")
    assert sign_crossings(xs, values) == (3.5,)


def test_sign_crossings_rejects() -> None:
    with pytest.raises(ValueError):
        sign_crossings([0.0, 1.0], [1.0])
    with pytest.raises(ValueError):
        sign_crossings([0.0, 1.0], [1.0, math.nan])
    with pytest.raises(ValueError):
        sign_crossings([0.0, 1.0], [1.0, -1.0], zero_rule="literal")  # pyright: ignore[reportArgumentType]


def test_flip_multi_expiry_shifts_forwards_by_ratio() -> None:
    near = make(F0, [(1095.0, "P", 1000, 0.2), (1110.0, "C", 1000, 0.2)], expiry="261001")
    near = replace(near, expiry_date=date(2026, 10, 1))
    far = make(1110.0, [(1100.0, "P", 800, 0.25), (1120.0, "C", 1500, 0.22)])
    evs = [far, near]
    fp = 1111.0
    ratio = fp / F0  # 기준 = 최근접 만기 F [확인 필요]
    expected = 0.0
    for ev in evs:
        assert ev.F is not None
        f2 = ev.F * ratio
        for o in ev.options:
            assert o.iv is not None and o.iv.sigma is not None
            g = _gamma_by_hand(f2, float(o.quote.strike), ev.T, o.iv.sigma)
            expected += option_gex(o.quote.cp, g, o.quote.oi, f2)
    assert total_gex_at(evs, fp) == pytest.approx(expected, rel=1e-9)
    assert total_gex_at(evs, F0) == pytest.approx(net_gex(evs).value, rel=1e-12)
    fl = gamma_flip(evs)
    assert fl.f_ref == F0
    assert (fl.profile[0][0], fl.profile[-1][0]) == (1045.0, 1155.0)
    assert fl.level is not None
    # f_ref 를 바꾸면 격자와 비율 기준이 따라간다
    moved = gamma_flip(evs, f_ref=1110.0)
    assert moved.f_ref == 1110.0
    assert moved.profile[0][0] == pytest.approx(0.95 * 1110.0)
    assert total_gex_at(evs, 1110.0, f_ref=1110.0) == pytest.approx(net_gex(evs).value)


def test_flip_without_forward() -> None:
    no_f = make(None, [(1100.0, "C", 10, 0.2)])
    fl = gamma_flip([no_f])
    assert fl == GammaFlip(None, False, (), None, (), "invalid")
    assert fl.distance_pct is None
    assert total_gex_at([no_f], 1100.0) is None
    assert gamma_flip([]) == GammaFlip(None, False, (), None, (), "ok")
    # F 없는 만기가 섞이면 값은 나머지로, 품질 invalid
    good = make(F0, [(1095.0, "P", 1000, 0.2), (1110.0, "C", 1000, 0.2)])
    mixed = gamma_flip([good, replace(no_f, expiry="202611")])
    assert mixed.level == gamma_flip([good]).level
    assert mixed.quality == "invalid"


def test_flip_quality_follows_scope() -> None:
    ev = make(F0, [(1095.0, "P", 1000, 0.2), (1110.0, "C", 1000, 0.2)], fq="estimated")
    assert gamma_flip([ev]).quality == "estimated"


def test_flip_invariants() -> None:
    with pytest.raises(ValueError):
        GammaFlip(1100.0, False, (), 1100.0, (), "ok")
    with pytest.raises(ValueError):
        GammaFlip(1100.0, False, (1100.0, 1101.0), 1100.0, (), "ok")
    with pytest.raises(ValueError):
        GammaFlip(None, False, (), None, ((1.0, 1.0),), "ok")


def test_flip_distance_pct() -> None:
    assert flip_distance_pct(1100.0, 1089.0) == pytest.approx(1.0)
    assert flip_distance_pct(1100.0, 1111.0) == pytest.approx(-1.0)
    assert flip_distance_pct(1100.0, None) is None
    with pytest.raises(ValueError):
        flip_distance_pct(0.0, 1.0)


# --- 0DTE ---


def test_zero_dte_levels_only_on_expiry_date() -> None:
    weekly = make(
        F0,
        [(1095.0, "P", 1000, 0.2), (1110.0, "C", 1000, 0.2), (1120.0, "C", 50, 0.2)],
        expiry="261001",
        ed=date(2026, 10, 1),
    )
    monthly = make(1102.0, [(1080.0, "P", 5000, 0.2), (1130.0, "C", 5000, 0.2)])
    evs = [weekly, monthly]
    z = zero_dte_levels(evs, date(2026, 10, 1))
    assert z is not None
    assert z.expiries == ("261001",)
    assert (z.call_wall.strike, z.put_wall.strike) == (D(1110), D(1095))
    assert z.flip == gamma_flip([weekly])
    assert z.flip.level is not None
    assert zero_dte_levels(evs, date(2026, 10, 2)) is None
    assert zero_dte_levels(evs, date(2026, 9, 30)) is None
    m = zero_dte_levels(evs, EXP)
    assert m is not None and m.expiries == ("202610",)
    assert (m.call_wall.strike, m.put_wall.strike) == (D(1130), D(1080))
    with pytest.raises(ValueError):
        zero_dte_levels(evs, kst(2026, 10, 1))  # datetime 은 거래일이 아니다


# --- ATM IV ---


def iv_expiry(
    F: float | None, ivs: Mapping[tuple[float, CallPut], IvResult], fq: Quality = "ok"
) -> ExpiryEval:
    """행사가·콜풋 → IV 로 만기를 만든다. sigma 없는 IV 는 iv_invalid 제외 종목."""
    opts: list[OptionEval] = []
    for (k, cp), iv in sorted(ivs.items()):
        q = _quote(k, cp, 100, "202610", EXP)
        if iv.sigma is None:
            opts.append(OptionEval(q, q.price(), iv, None, True, "iv_invalid"))
        else:
            g = greeks("c" if cp == "C" else "p", F or F0, k, T, iv.sigma)
            opts.append(OptionEval(q, q.price(), iv, g, False, None))
    fwd = _forward(F, fq)
    return ExpiryEval("202610", EXP, fwd, T, tuple(opts))


def ok(s: float) -> IvResult:
    return IvResult(s, "ok", "model", None)


SMILE: dict[tuple[float, CallPut], IvResult] = {
    (1095.0, "C"): ok(0.23),
    (1095.0, "P"): ok(0.25),
    (1100.0, "C"): ok(0.20),
    (1100.0, "P"): ok(0.22),
    (1105.0, "C"): ok(0.18),
    (1105.0, "P"): ok(0.20),
}


def smile(*over: tuple[float, CallPut, IvResult]) -> dict[tuple[float, CallPut], IvResult]:
    """SMILE 에서 일부 종목 IV 만 바꾼다."""
    out = dict(SMILE)
    for k, cp, iv in over:
        out[(k, cp)] = iv
    return out


def test_atm_iv_interpolates_between_bracketing_strikes() -> None:
    res = atm_iv(iv_expiry(1102.0, SMILE))
    # 1100: 0.21, 1105: 0.19 → 0.21 + (2/5)·(0.19 − 0.21)
    assert res.value == pytest.approx(0.202, abs=1e-12)
    assert (res.quality, res.strikes, res.reasons) == ("ok", (D(1100), D(1105)), ())


def test_atm_iv_on_strike() -> None:
    res = atm_iv(iv_expiry(1100.0, SMILE))
    assert res.value == pytest.approx(0.21)
    assert (res.quality, res.strikes) == ("ok", (D(1100),))
    gone = smile((1100.0, "C", NO_IV), (1100.0, "P", NO_IV))
    assert atm_iv(iv_expiry(1100.0, gone)) == AtmIv(None, "invalid", (), ("no_iv",))


def test_atm_iv_one_side_missing_is_estimated() -> None:
    ivs = smile((1105.0, "C", NO_IV), (1105.0, "P", NO_IV))
    res = atm_iv(iv_expiry(1102.0, ivs))
    assert res.value == pytest.approx(0.21)
    assert (res.quality, res.strikes, res.reasons) == ("estimated", (D(1100),), ("one_side",))


def test_atm_iv_one_leg_is_estimated() -> None:
    ivs = smile((1105.0, "P", NO_IV))
    res = atm_iv(iv_expiry(1102.0, ivs))
    # 1105 는 콜 0.18 만 [확인 필요]
    assert res.value == pytest.approx(0.21 + 0.4 * (0.18 - 0.21), abs=1e-12)
    assert (res.quality, res.reasons) == ("estimated", ("one_leg",))


def test_atm_iv_reasons_are_unique() -> None:
    # 두 행사가 모두 한 다리 — 사유는 한 번만
    both = atm_iv(iv_expiry(1102.0, smile((1100.0, "P", NO_IV), (1105.0, "P", NO_IV))))
    assert both.value == pytest.approx(0.20 + 0.4 * (0.18 - 0.20), abs=1e-12)
    assert (both.quality, both.strikes) == ("estimated", (D(1100), D(1105)))
    assert both.reasons == ("one_leg",)
    # 한 다리 + 한쪽 없음 — 사유 둘, 생긴 순서대로
    ivs = smile((1100.0, "P", NO_IV), (1105.0, "C", NO_IV), (1105.0, "P", NO_IV))
    mixed = atm_iv(iv_expiry(1102.0, ivs))
    assert (mixed.value, mixed.reasons) == (pytest.approx(0.20), ("one_leg", "one_side"))


def test_atm_iv_none_is_invalid() -> None:
    ivs = dict.fromkeys(SMILE, NO_IV)
    assert atm_iv(iv_expiry(1102.0, ivs)) == AtmIv(None, "invalid", (), ("no_iv",))
    assert atm_iv(iv_expiry(None, SMILE)) == AtmIv(None, "invalid", (), ("no_forward",))


def test_atm_iv_outside_strike_range_uses_edge() -> None:
    res = atm_iv(iv_expiry(1107.0, SMILE))
    assert res.value == pytest.approx(0.19)
    assert (res.quality, res.strikes, res.reasons) == ("estimated", (D(1105),), ("one_side",))


def test_atm_iv_quality_composes_inputs() -> None:
    kis = IvResult(0.24, "estimated", "kis", "below_intrinsic")
    res = atm_iv(iv_expiry(1102.0, smile((1100.0, "P", kis))))
    assert res.value == pytest.approx(0.22 + 0.4 * (0.19 - 0.22))
    assert (res.quality, res.reasons) == ("estimated", ())
    assert atm_iv(iv_expiry(1102.0, SMILE, fq="estimated")).quality == "estimated"


def test_atm_iv_prev_session_iv_is_estimated() -> None:
    # 전 세션 last 로 역산한 IV(§1.1)는 역산이 ok 여도 종목 품질 estimated — ATM IV 도 따라간다
    ev = iv_expiry(1102.0, SMILE)
    stale = [
        replace(o, choice=replace(o.choice, prev_session=True)) if o.quote.strike == D(1105) else o
        for o in ev.options
    ]
    res = atm_iv(replace(ev, options=tuple(stale)))
    assert res.value == pytest.approx(0.202, abs=1e-12)
    assert (res.quality, res.reasons) == ("estimated", ())
    # 보간에 안 쓰는 행사가라면 영향 없다
    far = [
        replace(o, choice=replace(o.choice, prev_session=True)) if o.quote.strike == D(1095) else o
        for o in ev.options
    ]
    assert atm_iv(replace(ev, options=tuple(far))).quality == "ok"


def test_atm_iv_invariants() -> None:
    with pytest.raises(ValueError):
        AtmIv(None, "ok", (), ())
    with pytest.raises(ValueError):
        AtmIv(0.0, "ok", (), ())


# --- 기대변동폭 ---


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    return TradingCalendar()


@pytest.mark.parametrize(
    ("now", "minutes"),
    [
        (kst(2026, 10, 1, 13, 45), 120),  # 주간 중 → 15:45 까지
        (kst(2026, 10, 1, 8, 45), 420),
        (kst(2026, 10, 1, 15, 44, 30), 0.5),
        (kst(2026, 9, 30, 22, 0), 480),  # 야간 중 → 익일 06:00 까지
        (kst(2026, 10, 1, 2, 0), 240),  # 전날 밤 야간의 자정 뒤
        (kst(2026, 10, 1, 7, 0), 420),  # 세션 밖 → 다음 세션(주간) 전체
        (kst(2026, 10, 1, 8, 30), 420),
        (kst(2026, 10, 1, 15, 45), 720),  # 주간 끝 → 그날 야간 전체
        (kst(2026, 10, 1, 17, 55), 720),
        (kst(2026, 10, 2, 16, 0), 720),  # 월요일(10-05) 휴장 앞 금요일 — 야간 열림(2026-09-29 실측)
        (kst(2026, 10, 3, 7, 0), 420),  # 금요일 밤 뒤 토요일 아침 → 10-06 주간
        (kst(2026, 10, 3, 12, 0), 420),  # 토요일
        (kst(2026, 9, 23, 17, 55), 420),  # 추석 휴장 전날 저녁 → 09-28 주간
    ],
)
def test_session_minutes(cal: TradingCalendar, now: datetime, minutes: float) -> None:
    assert session_minutes(now, cal) == pytest.approx(minutes)


def test_next_session(cal: TradingCalendar) -> None:
    # 월요일(10-05) 휴장 앞 금요일도 야간이 열린다(2026-09-29 실측) — 그 밤이 다음 세션
    assert next_session(kst(2026, 10, 2, 16, 0), cal) == (
        kst(2026, 10, 2, 18, 0),
        kst(2026, 10, 3, 6, 0),
    )
    # 휴장 전날(수 09-23) 밤은 없다 → 연휴 뒤 09-28 주간
    assert next_session(kst(2026, 9, 23, 16, 0), cal) == (
        kst(2026, 9, 28, 8, 45),
        kst(2026, 9, 28, 15, 45),
    )
    assert next_session(kst(2026, 10, 1, 16, 0), cal) == (
        kst(2026, 10, 1, 18, 0),
        kst(2026, 10, 2, 6, 0),
    )


def test_session_minutes_rejects_naive(cal: TradingCalendar) -> None:
    with pytest.raises(ValueError, match="naive"):
        session_minutes(datetime(2026, 10, 1, 10, 0), cal)  # noqa: DTZ001


def test_expected_move_value(cal: TradingCalendar) -> None:
    atm = AtmIv(0.2, "ok", (D(1100),), ())
    now = kst(2026, 10, 1, 13, 45)
    # 기본은 달력 분 기준(2026-09-28 결정): 1100 × 0.2 × √(120 / (365 × 24 × 60))
    mv = expected_move(F0, atm, now, cal=cal)
    sig = 1100 * 0.2 * math.sqrt(120 / 525_600)
    assert mv.sigma == pytest.approx(sig, rel=1e-12)
    assert (mv.minutes, mv.dt, mv.quality, mv.basis) == (120.0, 120 / 525_600, "ok", "calendar")
    assert CALENDAR_MINUTES_PER_YEAR == 525_600
    assert mv.lower == pytest.approx(F0 - sig)
    assert mv.upper == pytest.approx(F0 + sig)
    # 거래시간 기준은 옵션: 1100 × 0.2 × √(120 / (252 × 420))
    tr = expected_move(F0, atm, now, cal=cal, basis="trading")
    assert tr.sigma == pytest.approx(7.407785326716212, rel=1e-12)
    assert (tr.dt, tr.basis) == (120 / 105_840, "trading")
    assert tr.sigma is not None and mv.sigma is not None and tr.sigma > mv.sigma
    # 세션 밖(야간 전) → 야간 720분 전체
    night = expected_move(F0, atm, kst(2026, 10, 1, 16, 0), cal=cal)
    assert night.sigma == pytest.approx(1100 * 0.2 * math.sqrt(720 / CALENDAR_MINUTES_PER_YEAR))
    night_tr = expected_move(F0, atm, kst(2026, 10, 1, 16, 0), cal=cal, basis="trading")
    assert night_tr.sigma == pytest.approx(1100 * 0.2 * math.sqrt(720 / TRADING_MINUTES_PER_YEAR))
    # 연환산 분모를 직접 줄 수도 있다
    other = expected_move(F0, atm, now, cal=cal, minutes_per_year=250 * 420)
    assert other.dt == 120 / (250 * 420)


def test_expected_move_quality(cal: TradingCalendar) -> None:
    now = kst(2026, 10, 1, 13, 45)
    est = expected_move(F0, AtmIv(0.2, "estimated", (D(1100),), ("one_side",)), now, cal=cal)
    assert est.quality == "estimated" and est.sigma is not None
    bad = expected_move(F0, AtmIv(None, "invalid", (), ("no_iv",)), now, cal=cal)
    assert (bad.sigma, bad.lower, bad.upper, bad.quality) == (None, None, None, "invalid")
    no_f = expected_move(None, AtmIv(0.2, "ok", (D(1100),), ()), now, cal=cal)
    assert (no_f.sigma, no_f.quality) == (None, "invalid")
    with pytest.raises(ValueError):
        expected_move(-1.0, AtmIv(0.2, "ok", (), ()), now, cal=cal)
    with pytest.raises(ValueError):
        ExpectedMove(F0, 1.0, 1.0, 1.0, "invalid")


def test_expected_move_default_calendar() -> None:
    mv = expected_move(F0, AtmIv(0.2, "ok", (), ()), kst(2026, 10, 1, 13, 45))
    assert mv.minutes == 120.0


# --- 기대범위 내 상위 GEX 레벨 ---


def _move(F: float, sigma: float | None, quality: Quality = "ok") -> ExpectedMove:
    return ExpectedMove(F if sigma is not None else None, sigma, 120.0, 120 / 525_600, quality)


def test_top_levels_order_and_truncation() -> None:
    ev = fixed(
        F0,
        {1092.5: 9.0, 1095.0: 1.0, 1100.0: 4.0, 1102.5: 2.0, 1105.0: 0.5, 1107.5: 9.0},
        {1095.0: 4.0, 1097.5: 3.5, 1100.0: 1.0},
    )
    # 범위 [1094, 1106]: 1095 −3, 1097.5 −3.5, 1100 +3, 1102.5 +2, 1105 +0.5 (×UNIT)
    top = top_levels_in_range([ev], _move(F0, 6.0), 3)
    assert [r.strike for r in top.levels] == [D("1097.5"), D(1100), D(1095)]
    assert (top.lower, top.upper, top.quality) == (1094.0, 1106.0, "ok")
    assert top.levels[0].gex == pytest.approx(-3.5 * UNIT)
    full = top_levels_in_range([ev], _move(F0, 6.0))
    assert len(full.levels) == 5 == TOP_LEVELS
    assert [r.strike for r in full.levels][-2:] == [D("1102.5"), D(1105)]
    # 범위 안이 N 개보다 적으면 있는 만큼, 양 끝 포함
    narrow = top_levels_in_range([ev], _move(F0, 2.5), 5)
    assert [r.strike for r in narrow.levels] == [D("1097.5"), D(1100), D("1102.5")]


def test_top_levels_tie_prefers_nearer_f() -> None:
    ev = fixed(F0, {1095.0: 2.0, 1102.5: 2.0, 1105.0: 2.0}, {})
    top = top_levels_in_range([ev], _move(F0, 10.0), 5)
    assert [r.strike for r in top.levels] == [D("1102.5"), D(1095), D(1105)]


def test_top_levels_invalid_move() -> None:
    ev = fixed(F0, {1100.0: 1.0}, {})
    assert top_levels_in_range([ev], _move(F0, None, "invalid")).levels == ()
    assert top_levels_in_range([ev], _move(F0, None, "invalid")).quality == "invalid"
    assert top_levels_in_range([ev], _move(F0, 5.0, "estimated")).quality == "estimated"
    for n in (0, -1, True):
        with pytest.raises(ValueError):
            top_levels_in_range([ev], _move(F0, 5.0), n)


# --- 만기별 감마 ---


def test_gamma_by_expiry() -> None:
    near = make(F0, [(1100.0, "P", 1000, 0.2)], expiry="261001", ed=date(2026, 10, 1))
    far = make(F0, [(1100.0, "C", 1000, 0.2)])
    no_f = make(None, [(1100.0, "C", 10, 0.2)], expiry="261002", ed=date(2026, 10, 6))
    rows = gamma_by_expiry([far, no_f, near])
    assert [r.expiry for r in rows] == ["261001", "261002", "202610"]
    assert rows[0].value == net_gex([near]).value
    assert rows[0].value is not None and rows[0].value < 0
    assert rows[2].value == net_gex([far]).value
    assert (rows[1].value, rows[1].quality, rows[1].excluded_oi_ratio) == (None, "invalid", 1.0)
    assert [r.quality for r in rows] == ["ok", "invalid", "ok"]
    # 만기 목록 품질 연동
    stale = gamma_by_expiry([far, no_f, near], list_quality="stale")
    assert [r.quality for r in stale] == ["stale", "invalid", "stale"]
    assert gamma_by_expiry([]) == ()
    with pytest.raises(ValueError):
        gamma_by_expiry([far], list_quality="bad")  # pyright: ignore[reportArgumentType]

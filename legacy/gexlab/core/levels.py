"""핵심 레벨 (docs/metrics.md §3, PLAN §5.2).

입력은 `core.gex.evaluate_expiry` 의 `ExpiryEval` 들이고 범위(전체·최근접 등)는 호출 쪽이 고른다
(`core.gex.select_scope`). 범위의 행사가별 GEX(`scope_strike_gex`)는 만기별 `strike_gex` 행을
행사가마다 더한 값이며 F 없는 만기는 빠진다. 품질은 그 범위의 순GEX 품질(§2.2 — F 없는 만기
invalid, 제외 OI 비율 > 10% estimated, OI 전부 제외 invalid, 입력 품질 합성)을 기본으로 한다.

- 기준 F(`reference_forward`): F 있는 만기 중 만기일이 가장 이른 만기의 F — 동률 규칙의 현재가,
  Flip 격자 중심, 전환점 거리 분모 [확인 필요]
- 콜월·풋월·절대감마(§3.1~§3.3): 값이 최대인 행사가. 동률이면 기준 F 에 가까운 쪽, 거리도 같으면
  낮은 행사가(`core.chain.by_distance`) [확인 필요]. 0 보다 큰 값이 없으면(OI 전부 0 등) null
- Flip(§3.4): 격자 F′ = 0.95F + 0.25·i, 끝점 1.05F 포함 [확인 필요]. 각 만기 F 를 F′/F_기준 비율로
  옮기고 종목 IV 는 그대로(sticky-strike) `core.greeks.gamma` 로 감마를 다시 계산해 §2.1 식(F 자리에
  옮긴 F)으로 총 GEX(F′). 교차 판정은 `sign_crossings` — 정확히 0 인 격자점은 양옆 부호가 다를
  때만 교차(`FLIP_ZERO_RULE`, 설정으로 0 인 점 전부) [확인 필요]
- ATM IV(§3.7)·기대변동폭(§3.8, Δt 는 `core.calendar` 세션 경계)·범위 안 상위 레벨(§3.9)·만기별
  감마(§3.10)
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal, cast, get_args

from core.calendar import (
    KST,
    NIGHT_START,
    State,
    TradingCalendar,
    night_session_opens,
    session_bounds,
    state_at,
)
from core.chain import by_distance
from core.gex import (
    MAX_EXCLUDED_OI_RATIO,
    CallPut,
    ExpiryEval,
    StrikeGex,
    StrikeGexTable,
    net_gex,
    option_gex,
    select_scope,
    strike_gex,
)
from core.greeks import gamma
from core.preprocess import Quality, worst

FLIP_BAND = 0.05  # 격자 [0.95F, 1.05F] (PLAN §5.2)
FLIP_STEP = 0.25  # pt
# §3.4 정확히 0 인 격자점: sign_change — 양옆 0 아닌 값의 부호가 다를 때만 교차(기본),
# every_zero — 0 인 격자점 전부 교차(끝·접함 포함) [확인 필요]
ZeroRule = Literal["sign_change", "every_zero"]
FLIP_ZERO_RULE: ZeroRule = "sign_change"
TOP_LEVELS = 5  # §3.9 기본 N
DAY_SESSION_MINUTES = 420  # 주간 08:45~15:45
# §3.8 Δt 연환산 기준(2026-09-28 사용자 결정): 기본은 달력 분(IV 가 달력 분 T 로 역산되므로 같은
# 기준), 거래시간 기준(주간 한 번 = 1/252년)은 옵션
DeltaBasis = Literal["calendar", "trading"]
CALENDAR_MINUTES_PER_YEAR = 365 * 24 * 60
TRADING_MINUTES_PER_YEAR = 252 * DAY_SESSION_MINUTES
MAX_GRID_POINTS = 100_000  # 설정 오류(너무 촘촘한 격자) 방어선
_GRID_EPS = 1e-9  # pt — 끝점이 격자에 이미 있다고 볼 거리
_SEARCH_DAYS = 60  # 다음 세션 탐색 상한(core.calendar 의 연속 휴장 상한과 같게)


def _positive(name: str, v: float) -> float:
    if not (math.isfinite(v) and v > 0):
        raise ValueError(f"{name} 는 유한한 양수여야 한다: {v!r}")
    return v


def _scope_quality(evs: Sequence[ExpiryEval], max_excluded_ratio: float) -> Quality:
    """범위의 순GEX 품질(§2.2). 빈 범위는 ok(해당 없음)."""
    return net_gex(evs, max_excluded_ratio=max_excluded_ratio).quality


# ── 범위 합산 ─────────────────────────────────────────────────────────────────


def reference_forward(evals: Iterable[ExpiryEval]) -> float | None:
    """기준 F — F 있는 만기 중 만기일(같으면 코드)이 가장 이른 만기의 F. 없으면 None.

    §3.4 "기준 = 최근접 만기 F" [확인 필요]. 최근접 만기에 F 가 없으면 그다음 만기 F 를 쓴다 —
    그런 범위의 품질은 어차피 invalid(§2.2).
    """
    with_f = sorted(
        (e for e in evals if e.forward.F is not None), key=lambda e: (e.expiry_date, e.expiry)
    )
    return with_f[0].forward.F if with_f else None


def _resolve_ref(evs: Sequence[ExpiryEval], f_ref: float | None) -> float | None:
    return reference_forward(evs) if f_ref is None else _positive("f_ref", f_ref)


def scope_strike_gex(
    evals: Iterable[ExpiryEval], *, max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO
) -> StrikeGexTable:
    """범위 안 만기들의 행사가별 GEX 합(원/1%, 행사가 오름차순)과 그 품질. F 없는 만기는 빠진다.

    quality·excluded_oi_ratio 는 범위 순GEX(§2.2) 것 — 만기별 표 품질의 합성이 아니라 범위 전체의
    제외 비율로 다시 잰다. 같은 만기 코드가 두 번 오면 ValueError(`select_scope`).
    """
    evs = select_scope(evals, "all")
    calls: dict[Decimal, list[float]] = {}
    puts: dict[Decimal, list[float]] = {}
    for e in evs:
        for row in strike_gex(e, max_excluded_ratio=max_excluded_ratio).rows:
            calls.setdefault(row.strike, []).append(row.gex_call)
            puts.setdefault(row.strike, []).append(row.gex_put)
    rows = tuple(StrikeGex(k, math.fsum(calls[k]), math.fsum(puts[k])) for k in sorted(calls))
    scope = net_gex(evs, max_excluded_ratio=max_excluded_ratio)
    return StrikeGexTable(rows, scope.quality, scope.excluded_oi_ratio)


# ── 콜월·풋월·절대감마 (§3.1~§3.3) ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Wall:
    """행사가 레벨. value 는 고른 기준값(원/1%) — 콜월 GEX_call, 풋월 |GEX_put|, 절대감마
    |GEX_call| + |GEX_put|. 0 보다 큰 행사가가 없으면 strike·value 가 None."""

    strike: Decimal | None
    value: float | None
    quality: Quality

    def __post_init__(self) -> None:
        if (self.strike is None) != (self.value is None):
            raise ValueError(f"Wall 필드 조합이 맞지 않는다: {self}")


def _pick(
    rows: Sequence[StrikeGex], metric: Callable[[StrikeGex], float], ref: float | None
) -> tuple[Decimal, float] | None:
    """metric 최대 행사가. 동률이면 ref 에 가까운 쪽, 거리도 같으면 낮은 행사가."""
    scored = [(r.strike, metric(r)) for r in rows]
    best = max((v for _, v in scored), default=0.0)
    if not best > 0:
        return None
    tied = [k for k, v in scored if v == best]
    if len(tied) == 1 or ref is None:
        return tied[0], best
    return by_distance(tied, ref)[0], best


def _wall(
    evals: Iterable[ExpiryEval],
    metric: Callable[[StrikeGex], float],
    f_ref: float | None,
    max_excluded_ratio: float,
) -> Wall:
    evs = select_scope(evals, "all")
    table = scope_strike_gex(evs, max_excluded_ratio=max_excluded_ratio)
    picked = _pick(table.rows, metric, _resolve_ref(evs, f_ref))
    if picked is None:
        return Wall(None, None, table.quality)
    return Wall(picked[0], picked[1], table.quality)


def call_wall(
    evals: Iterable[ExpiryEval],
    *,
    f_ref: float | None = None,
    max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO,
) -> Wall:
    """§3.1 콜월 — 범위 GEX_call 최대 행사가. 콜 GEX 가 0 보다 큰 행사가가 없으면(콜 OI 전부 0
    등) null.

    f_ref: 동률 판정의 현재가. 기본은 `reference_forward`.
    """
    return _wall(evals, lambda r: r.gex_call, f_ref, max_excluded_ratio)


def put_wall(
    evals: Iterable[ExpiryEval],
    *,
    f_ref: float | None = None,
    max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO,
) -> Wall:
    """§3.2 풋월 — 범위 |GEX_put| 최대 행사가. 동률·null 규칙은 콜월과 같다."""
    return _wall(evals, lambda r: abs(r.gex_put), f_ref, max_excluded_ratio)


def abs_gamma_strike(
    evals: Iterable[ExpiryEval],
    *,
    f_ref: float | None = None,
    max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO,
) -> Wall:
    """§3.3 절대감마 행사가 — |GEX_call| + |GEX_put| 최대 행사가. 동률·null 규칙은 콜월과 같다."""
    return _wall(evals, lambda r: abs(r.gex_call) + abs(r.gex_put), f_ref, max_excluded_ratio)


# ── 감마 전환점 (§3.4·§3.5) ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Leg:
    """Flip 재계산용 종목 하나 — GEX 에 들어간 종목(§2.1)의 IV·OI 와 그 만기 F·T."""

    cp: CallPut
    K: float
    T: float
    sigma: float
    oi: int
    F: float


def _legs(evs: Iterable[ExpiryEval]) -> list[_Leg]:
    legs: list[_Leg] = []
    for e in evs:
        F = e.forward.F
        if F is None:
            continue
        for o in e.options:
            # GEX 에 든 종목만(그릭스가 있는 종목). OI 0 은 항상 0 이라 건너뛴다
            if o.greeks is None or o.quote.oi == 0:
                continue
            if o.iv is None or o.iv.sigma is None:
                raise ValueError(f"그릭스가 있는데 IV 가 없다: {o.quote.strike} {o.quote.cp}")
            legs.append(_Leg(o.quote.cp, float(o.quote.strike), e.T, o.iv.sigma, o.quote.oi, F))
    return legs


def _total_gex(legs: Sequence[_Leg], ratio: float, r: float) -> float:
    """모든 만기 F 를 ratio 배 옮긴 총 GEX(원/1%) — sticky-strike IV, 감마 재계산."""
    total = math.fsum(
        option_gex(g.cp, gamma(g.F * ratio, g.K, g.T, g.sigma, r), g.oi, g.F * ratio) for g in legs
    )
    if not math.isfinite(total):
        raise ValueError(f"총 GEX 가 유한하지 않다(ratio={ratio!r}): {total!r}")
    return total


def total_gex_at(
    evals: Iterable[ExpiryEval], f_prime: float, *, f_ref: float | None = None, r: float = 0.0
) -> float | None:
    """§3.4 가상 기초 F′ 에서의 총 GEX(원/1%). 각 만기 F 를 F′/F_기준 비율로 옮긴다.

    F′ = F_기준 이면 §2.2 순GEX(범위 all)와 같다. F 있는 만기가 없으면 None.
    """
    evs = select_scope(evals, "all")
    ref = _resolve_ref(evs, f_ref)
    if ref is None:
        return None
    return _total_gex(_legs(evs), _positive("f_prime", f_prime) / ref, r)


def flip_grid(f_ref: float, band: float = FLIP_BAND, step: float = FLIP_STEP) -> tuple[float, ...]:
    """§3.4 격자: (1 − band)·F 부터 step 간격, (1 + band)·F 를 넘지 않게. 끝점 (1 + band)·F 가
    격자에 없으면 덧붙인다 — 마지막 간격은 step 보다 짧을 수 있다 [확인 필요]."""
    _positive("f_ref", f_ref)
    _positive("step", step)
    if not (math.isfinite(band) and 0 < band < 1):
        raise ValueError(f"band 는 0 과 1 사이: {band!r}")
    lo, hi = f_ref * (1 - band), f_ref * (1 + band)
    n = math.floor((hi - lo) / step + _GRID_EPS)
    if n + 2 > MAX_GRID_POINTS:
        raise ValueError(f"격자가 너무 촘촘하다: {n + 1} 점 (step={step!r})")
    pts = [min(lo + i * step, hi) for i in range(n + 1)]
    if hi - pts[-1] > _GRID_EPS:
        pts.append(hi)
    return tuple(pts)


def sign_crossings(
    xs: Sequence[float], values: Sequence[float], *, zero_rule: ZeroRule = FLIP_ZERO_RULE
) -> tuple[float, ...]:
    """격자 (xs 오름차순, values)에서 값의 부호가 바뀌는 곳(오름차순).

    0 이 아닌 이웃 값끼리 부호가 다르면 교차 하나: 바로 붙어 있으면 선형보간. 정확히 0 인 격자점은
    zero_rule 을 따른다 [확인 필요]
    - sign_change(기본): 사이에 0 인 격자점이 있으면 그 점(0 이 여럿 이어지면 그 구간 가운데).
      같은 부호 사이의 0(접함), 격자 끝의 0, 전부 0 은 교차가 아니다 — 0DTE 막판 감마
      언더플로로 생기는 0 구간을 교차로 세지 않게
    - every_zero: 0 인 격자점 전부(끝·접함 포함)가 교차점. 0 구간 가운데는 따로 넣지 않는다
    """
    if zero_rule not in get_args(ZeroRule):
        raise ValueError(f"zero_rule 은 {get_args(ZeroRule)} 중 하나: {zero_rule!r}")
    if len(xs) != len(values):
        raise ValueError(f"격자와 값의 길이가 다르다: {len(xs)} ≠ {len(values)}")
    out: list[float] = []
    prev: int | None = None  # 직전 0 아닌 점
    for i, g in enumerate(values):
        if not math.isfinite(g):
            raise ValueError(f"값이 유한하지 않다: {g!r} (x={xs[i]!r})")
        if g == 0:
            if zero_rule == "every_zero":
                out.append(xs[i])
            continue
        if prev is not None and (values[prev] > 0) != (g > 0):
            if i - prev == 1:
                x0, x1, g0 = xs[prev], xs[i], values[prev]
                out.append(x0 + (x1 - x0) * g0 / (g0 - g))
            elif zero_rule == "sign_change":
                out.append((xs[prev + 1] + xs[i - 1]) / 2)
        prev = i
    return tuple(out)


def flip_distance_pct(F: float, flip: float | None) -> float | None:
    """§3.5 전환점 거리 `(F − Flip) / F × 100` (%). Flip 없으면 None."""
    if flip is None:
        return None
    return (_positive("F", F) - flip) / F * 100


@dataclass(frozen=True, slots=True)
class GammaFlip:
    """§3.4 결과. level 은 pt — 교차점이 없으면(`none`) None.

    crossings: 모든 교차점(오름차순), level 은 그중 f_ref 에 가장 가까운 것(같으면 낮은 쪽),
    multi_cross 는 2 개 이상. f_ref 가 None 이면 F 있는 만기가 없어 계산하지 못한 것(quality
    invalid, 빈 범위면 ok). profile: 격자 (F′, 총 GEX 원/1%).
    """

    level: float | None
    multi_cross: bool
    crossings: tuple[float, ...]
    f_ref: float | None
    profile: tuple[tuple[float, float], ...]
    quality: Quality

    def __post_init__(self) -> None:
        if (
            (self.level is None) != (not self.crossings)
            or (self.level is not None and self.level not in self.crossings)
            or self.multi_cross != (len(self.crossings) >= 2)
            or (self.f_ref is None and bool(self.profile))
        ):
            raise ValueError(f"GammaFlip 필드 조합이 맞지 않는다: {self}")

    @property
    def distance_pct(self) -> float | None:
        """§3.5 `(F_기준 − Flip) / F_기준 × 100`."""
        return None if self.f_ref is None else flip_distance_pct(self.f_ref, self.level)


def gamma_flip(
    evals: Iterable[ExpiryEval],
    *,
    f_ref: float | None = None,
    band: float = FLIP_BAND,
    step: float = FLIP_STEP,
    r: float = 0.0,
    max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO,
    zero_rule: ZeroRule = FLIP_ZERO_RULE,
) -> GammaFlip:
    """§3.4 감마 전환점. f_ref: 격자 중심·비율 기준(기본 `reference_forward`) [확인 필요].

    r 은 `evaluate_expiry` 에 넘긴 할인율과 같게 준다(기본 0, metrics §0). zero_rule 은 정확히
    0 인 격자점 해석(`sign_crossings`) [확인 필요].
    """
    evs = select_scope(evals, "all")
    quality = _scope_quality(evs, max_excluded_ratio)
    ref = _resolve_ref(evs, f_ref)
    if ref is None:
        return GammaFlip(None, False, (), None, (), quality)
    legs = _legs(evs)
    grid = flip_grid(ref, band, step)
    values = [_total_gex(legs, x / ref, r) for x in grid]
    found = sign_crossings(grid, values, zero_rule=zero_rule)
    level = min(found, key=lambda c: (abs(c - ref), c), default=None)
    profile = tuple(zip(grid, values, strict=True))
    return GammaFlip(level, len(found) >= 2, found, ref, profile, quality)


# ── 0DTE (§3.6) ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ZeroDteLevels:
    """§3.6 당일 만기만으로 계산한 콜월·풋월·Flip. expiries 는 그 만기 코드."""

    expiries: tuple[str, ...]
    call_wall: Wall
    put_wall: Wall
    flip: GammaFlip


def zero_dte_levels(
    evals: Iterable[ExpiryEval],
    trade_date: date,
    *,
    band: float = FLIP_BAND,
    step: float = FLIP_STEP,
    r: float = 0.0,
    max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO,
    zero_rule: ZeroRule = FLIP_ZERO_RULE,
) -> ZeroDteLevels | None:
    """§3.6 — 만기일이 trade_date(귀속 거래일, 야간은 T+1)인 만기가 있을 때만. 없으면 None."""
    chosen = select_scope(evals, "0dte", trade_date)
    if not chosen:
        return None
    flip = gamma_flip(
        chosen,
        band=band,
        step=step,
        r=r,
        max_excluded_ratio=max_excluded_ratio,
        zero_rule=zero_rule,
    )
    return ZeroDteLevels(
        tuple(e.expiry for e in chosen),
        call_wall(chosen, max_excluded_ratio=max_excluded_ratio),
        put_wall(chosen, max_excluded_ratio=max_excluded_ratio),
        flip,
    )


# ── ATM IV (§3.7) ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AtmIv:
    """§3.7 결과. value 는 연율 소수 — invalid 면 None.

    strikes: 값을 낸 행사가(보간이면 K1·K2, F 가 행사가와 같거나 한쪽만 있으면 하나).
    reasons: no_forward·no_iv(invalid), one_side(한쪽 행사가 IV 없음)·one_leg(콜·풋 중 하나만)
    (estimated). 사유마다 한 번, 생긴 순서.
    """

    value: float | None
    quality: Quality
    strikes: tuple[Decimal, ...]
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if (self.value is None) != (self.quality == "invalid") or (
            self.value is not None and not (math.isfinite(self.value) and self.value > 0)
        ):
            raise ValueError(f"AtmIv 필드 조합이 맞지 않는다: {self}")


def atm_iv(ev: ExpiryEval) -> AtmIv:
    """§3.7 만기 하나의 ATM IV — K1 ≤ F ≤ K2 두 행사가의 `(IV_call + IV_put) / 2` 를 F 로 선형보간.

    IV 는 sigma 가 있는 종목(자체 역산 ok·KIS 폴백 estimated)만 쓰고 그 종목 품질
    (`OptionEval.quality` — 전 세션 가격 IV 는 estimated)을 합성한다. 한 행사가에
    콜·풋 중 하나만 있으면 그 값만 쓰고 estimated, 한쪽 행사가에 IV 가 없거나 F 가 행사가 범위
    밖이면 있는 쪽 값만 쓰고 estimated [확인 필요]. 둘 다 없거나 F 가 없으면 invalid. F 가 행사가와
    같으면 그 행사가 값(없으면 invalid).
    """
    F = ev.forward.F
    if F is None:
        return AtmIv(None, "invalid", (), ("no_forward",))
    table: dict[Decimal, list[tuple[float, Quality]]] = {}
    for o in ev.options:
        row = table.setdefault(o.quote.strike, [])
        if o.iv is not None and o.iv.sigma is not None:
            row.append((o.iv.sigma, o.quality))
    k1 = max((k for k in table if k <= F), default=None)
    k2 = min((k for k in table if k >= F), default=None)

    reasons: list[str] = []
    qualities: list[Quality] = [ev.forward.quality]

    def value_at(k: Decimal | None) -> float | None:
        ivs = table.get(k, []) if k is not None else []
        if not ivs:
            return None
        if len(ivs) == 1 and "one_leg" not in reasons:  # 두 행사가가 다 한 다리여도 한 번
            reasons.append("one_leg")
        qualities.extend(q for _, q in ivs)
        return math.fsum(s for s, _ in ivs) / len(ivs)

    if k1 is not None and k1 == k2:
        used: list[tuple[Decimal, float | None]] = [(k1, value_at(k1))]
    else:
        used = [(k, v) for k, v in ((k1, value_at(k1)), (k2, value_at(k2))) if k is not None]
        if any(v is None for _, v in used) or len(used) < 2:
            reasons.append("one_side")
    points = [(k, v) for k, v in used if v is not None]
    if not points:
        return AtmIv(None, "invalid", (), ("no_iv",))
    if len(points) == 1:
        value = points[0][1]
    else:
        (a, va), (b, vb) = points
        value = va + (F - float(a)) / float(b - a) * (vb - va)
    if reasons:
        qualities.append("estimated")
    return AtmIv(value, worst(*qualities), tuple(k for k, _ in points), tuple(reasons))


# ── 기대변동폭 (§3.8) ─────────────────────────────────────────────────────────


def _minutes(start: datetime, end: datetime) -> float:
    # UTC 로 바꿔 뺀다(core.forward.time_to_expiry 와 같은 이유)
    return (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() / 60


def next_session(now: datetime, cal: TradingCalendar) -> tuple[datetime, datetime]:
    """now 뒤에 시작하는 첫 세션의 [시작, 끝) (KST aware). 휴장·야간 미개장은 건너뛴다."""
    d = now.astimezone(KST).date()
    for _ in range(_SEARCH_DAYS):
        if cal.is_trading_day(d):
            day = session_bounds(d, "day")
            if day[0] > now:
                return day
            if night_session_opens(d, cal):
                night = session_bounds(d, "night")
                if night[0] > now:
                    return night
        d += timedelta(days=1)
    raise RuntimeError(f"{_SEARCH_DAYS}일 안에 세션이 없다 — 캘린더를 확인하라 ({now})")


def session_minutes(now: datetime, cal: TradingCalendar | None = None) -> float:
    """§3.8 Δt 의 거래 분 — 세션 중이면 지금부터 그 세션 끝까지, 세션 밖이면 다음 세션 전체 분
    (주간 420·야간 720) [확인 필요].

    세션 경계는 `core.calendar.session_bounds`(주간 15:45, 야간 익일 06:00). 만기일 종목의 15:20
    종료는 반영하지 않는다(기초자산 기준). now 는 aware 여야 한다.
    """
    cal = TradingCalendar.default() if cal is None else cal
    info = state_at(now, cal)
    k = now.astimezone(KST)
    if info.state is State.DAY:
        return _minutes(now, session_bounds(k.date(), "day")[1])
    if info.state is State.NIGHT:
        started = k.date() if k.time() >= NIGHT_START else k.date() - timedelta(days=1)
        return _minutes(now, session_bounds(started, "night")[1])
    start, end = next_session(now, cal)
    return _minutes(start, end)


@dataclass(frozen=True, slots=True)
class ExpectedMove:
    """§3.8 결과. sigma 는 ±1σ 폭(pt) — invalid 면 None. minutes 는 남은 세션 분, dt 는 basis 로
    연환산한 Δt(년)."""

    F: float | None
    sigma: float | None
    minutes: float
    dt: float
    quality: Quality
    basis: DeltaBasis = "calendar"

    def __post_init__(self) -> None:
        if (self.sigma is None) != (self.quality == "invalid") or (
            self.sigma is not None and self.F is None
        ):
            raise ValueError(f"ExpectedMove 필드 조합이 맞지 않는다: {self}")

    @property
    def lower(self) -> float | None:
        return None if self.F is None or self.sigma is None else self.F - self.sigma

    @property
    def upper(self) -> float | None:
        return None if self.F is None or self.sigma is None else self.F + self.sigma


def expected_move(
    F: float | None,
    atm: AtmIv,
    now: datetime,
    *,
    cal: TradingCalendar | None = None,
    basis: DeltaBasis = "calendar",
    minutes_per_year: float | None = None,
) -> ExpectedMove:
    """§3.8 ±1σ = `F × IV_ATM × √Δt`, Δt = `session_minutes(now) / 연환산 분`.

    연환산 분은 basis 로 정한다: calendar(기본) 365×24×60 — IV_ATM 이 달력 분 T 로 역산된 값이라
    같은 기준, trading 252×420(주간 한 번 = 1/252년). minutes_per_year 를 주면 그 값을 쓴다.
    F·atm 은 같은 만기(보통 최근접 — atm 품질에 그 F 품질이 들어 있다)에서 온다. IV_ATM 이 invalid
    이거나 F 가 없으면 invalid, 아니면 품질은 IV_ATM 품질.
    """
    if minutes_per_year is None:
        minutes_per_year = (
            CALENDAR_MINUTES_PER_YEAR if basis == "calendar" else TRADING_MINUTES_PER_YEAR
        )
    _positive("minutes_per_year", minutes_per_year)
    if F is not None:
        _positive("F", F)
    minutes = session_minutes(now, cal)
    dt = minutes / minutes_per_year
    if F is None or atm.value is None:
        return ExpectedMove(F, None, minutes, dt, "invalid", basis)
    return ExpectedMove(F, F * atm.value * math.sqrt(dt), minutes, dt, atm.quality, basis)


# ── 기대범위 내 상위 GEX 레벨 (§3.9) ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TopLevels:
    """§3.9 결과. levels 는 [lower, upper] 안 행사가의 GEX(`StrikeGex.gex`) 절대값 내림차순."""

    levels: tuple[StrikeGex, ...]
    lower: float | None
    upper: float | None
    quality: Quality


def top_levels_in_range(
    evals: Iterable[ExpiryEval],
    move: ExpectedMove,
    n: int = TOP_LEVELS,
    *,
    max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO,
) -> TopLevels:
    """§3.9 `[F − 1σ, F + 1σ]`(양 끝 포함) 안 행사가를 범위 `|GEX(K)|` 내림차순 상위 n.

    동률이면 F 에 가까운 쪽, 거리도 같으면 낮은 행사가 [확인 필요]. 범위 안이 n 개보다 적으면 있는
    만큼. 기대변동폭이 invalid 면 빈 결과·invalid, 아니면 품질은 기대변동폭·범위 순GEX 품질 합성.
    """
    if isinstance(cast(object, n), bool) or not isinstance(cast(object, n), int) or n < 1:
        raise ValueError(f"n 은 1 이상 정수: {n!r}")
    evs = select_scope(evals, "all")
    lo, hi, F = move.lower, move.upper, move.F
    if lo is None or hi is None or F is None:
        return TopLevels((), None, None, "invalid")
    table = scope_strike_gex(evs, max_excluded_ratio=max_excluded_ratio)
    quality = worst(move.quality, table.quality)
    rows = [r for r in table.rows if lo <= r.strike <= hi]
    rows.sort(key=lambda r: (-abs(r.gex), abs(float(r.strike) - F), r.strike))
    return TopLevels(tuple(rows[:n]), lo, hi, quality)


# ── 만기별 감마 소멸액 (§3.10) ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExpiryGamma:
    """§3.10 만기 하나의 순GEX(원/1%) — F 없거나 OI 가 전부 제외면 value None(§2.2)."""

    expiry: str
    expiry_date: date
    value: float | None
    quality: Quality
    excluded_oi_ratio: float


def gamma_by_expiry(
    evals: Iterable[ExpiryEval],
    *,
    list_quality: Quality = "ok",
    max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO,
) -> tuple[ExpiryGamma, ...]:
    """§3.10 만기별 순GEX(§2.2 를 만기 하나 범위로), 만기일 오름차순.

    list_quality: 만기 목록(KIS 월물리스트) 품질 — 모든 만기 품질에 합성한다("만기 목록 품질 연동").
    """
    worst(list_quality)  # 모르는 값이면 ValueError
    out: list[ExpiryGamma] = []
    for e in sorted(select_scope(evals, "all"), key=lambda e: (e.expiry_date, e.expiry)):
        x = net_gex([e], max_excluded_ratio=max_excluded_ratio)
        quality = worst(x.quality, list_quality)
        out.append(ExpiryGamma(e.expiry, e.expiry_date, x.value, quality, x.excluded_oi_ratio))
    return tuple(out)

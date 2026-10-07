"""변동성 지표 (docs/metrics.md §5.2~§5.5, PLAN §5.4).

IV·델타는 §1.5·§1.6 의 자체 값만 쓴다(KIS 그릭스는 어떤 경우에도 쓰지 않는다).

- 기간구조(§5.2): 0DTE(만기일 = 귀속 거래일)·차기 위클리·월물(가장 가까운 월물) 각각의 §3.7 ATM
  IV(`core.levels.atm_iv`). 해당 만기가 없으면 그 칸 null(품질 ok — 해당 없음). 고르는 규칙
  [확인 필요]: 차기 위클리 = 만기일이 귀속 거래일보다 뒤인 가장 이른 위클리(오늘 만기 위클리는 0DTE
  칸), 월물 = 만기일이 귀속 거래일 이후(같은 날 포함)인 가장 이른 월물. 같은 칸 후보가 여럿이면
  만기일·만기 코드가 이른 것
- 25Δ 스큐(§5.3): `IV(Δ_put = −0.25) − IV(Δ_call = +0.25)`. 만기 하나에서 자체 그릭스가 있는 종목
  (GEX 에 든 종목 — sigma·자체 델타가 있다)의 (델타, σ)를 풋·콜 따로 델타 순으로 놓고 선형보간.
  보간 구간 밖(격자가 ±0.25 를 못 덮음)이면 null(PLAN) — 품질은 ok 로 두고 사유를 남긴다
  [확인 필요]. F 가 없으면 invalid(§1.3 — 그 만기 전 지표 invalid). 품질은 만기 F 품질(델타·IV 를
  F 로 구했다 — §0 합성, §3.7 과 같게)과 보간에 쓴 점(풋 둘·콜 둘)의 종목 품질(`OptionEval.quality`
  — IV 품질, 전 세션 가격 estimated) 합성
- IV 랭크·퍼센타일(§5.4, 일별): 오늘 x 와 최근 252거래일(오늘 포함 [확인 필요]) 일별 월물 ATM
  IV x₁…x_n — 랭크 (x − min)/(max − min), 퍼센타일 #{xᵢ < x}/n. n < 20 null, n < 252 estimated,
  자체·KRX 두 원천을 섞은 창이면 estimated [확인 필요]. 과거 일별 값은 KRX `IMP_VOLT` 백필
  (`krx_atm_iv`)
- IV − HV(§5.5, 일별): HV20 = 근월물 연결 KRX 선물 정산가의 일간 로그수익률 20개 표본표준편차 × √252
  [확인 필요: √252·표본(n − 1)]. 롤은 PLAN §9.3(최종거래일 직전 거래일 장 마감에 차월물) — 수익률은
  같은 결제월 안에서만 잰다(가격 조정 연결선물). 20개가 안 되면 null. 품질 = ATM IV 품질
"""

from __future__ import annotations

import math
import re
import statistics
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, get_args

from core.calendar import TradingCalendar
from core.forward import ForwardResult, synthetic_forward
from core.gex import CallPut, ExpiryEval
from core.levels import AtmIv, atm_iv
from core.preprocess import Quality, worst

ExpiryKind = Literal["monthly", "weekly"]
TermSlot = Literal["0dte", "next_weekly", "monthly"]
TERM_SLOTS: tuple[TermSlot, ...] = ("0dte", "next_weekly", "monthly")
SKEW_DELTA = 0.25  # §5.3 — 25Δ
IvSource = Literal["self", "krx"]  # §5.4 일별 값 원천 — 자체 §3.7 · KRX IMP_VOLT 백필
RANK_WINDOW = 252  # §5.4 최근 252거래일
RANK_MIN_DAYS = 20  # §5.4 n < 20 이면 null [확인 필요]
HV_DAYS = 20  # §5.5 HV20
HV_ANNUALIZATION = 252  # §5.5 √252 [확인 필요]
_YYYYMM = re.compile(r"[0-9]{6}")


def _as_date(name: str, d: date) -> date:
    if isinstance(d, datetime):
        raise TypeError(f"{name} 는 date 여야 한다(datetime 받음: {d!r})")
    return d


# ── 기간구조 (§5.2) ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TermPoint:
    """기간구조 한 칸. expiry·atm 이 None 이면 해당 만기가 없다(null, 품질 ok)."""

    slot: TermSlot
    expiry: str | None
    expiry_date: date | None
    atm: AtmIv | None

    def __post_init__(self) -> None:
        if self.slot not in TERM_SLOTS or not (
            (self.expiry is None) == (self.expiry_date is None) == (self.atm is None)
        ):
            raise ValueError(f"TermPoint 필드 조합이 맞지 않는다: {self}")

    @property
    def value(self) -> float | None:
        return None if self.atm is None else self.atm.value

    @property
    def quality(self) -> Quality:
        return "ok" if self.atm is None else self.atm.quality


def term_structure(
    evals: Iterable[tuple[ExpiryKind, ExpiryEval]], trade_date: date
) -> tuple[TermPoint, ...]:
    """§5.2 — (0dte, next_weekly, monthly) 세 칸. evals 는 (만기 종류, 만기 평가) — 만기 지난 만기는
    호출 쪽이 뺀다(§1.4). 같은 만기 코드가 두 번이거나 종류가 틀리면 ValueError."""
    td = _as_date("trade_date", trade_date)
    items = list(evals)
    codes = [e.expiry for _, e in items]
    if len(set(codes)) != len(codes):
        raise ValueError(f"같은 만기가 두 번: {codes}")
    for kind, _ in items:
        if kind not in get_args(ExpiryKind):
            raise ValueError(f"만기 종류는 monthly·weekly: {kind!r}")

    def first(cands: list[ExpiryEval]) -> ExpiryEval | None:
        return min(cands, key=lambda e: (e.expiry_date, e.expiry), default=None)

    picks: dict[TermSlot, ExpiryEval | None] = {
        "0dte": first([e for _, e in items if e.expiry_date == td]),
        "next_weekly": first([e for k, e in items if k == "weekly" and e.expiry_date > td]),
        "monthly": first([e for k, e in items if k == "monthly" and e.expiry_date >= td]),
    }
    out: list[TermPoint] = []
    for slot in TERM_SLOTS:
        e = picks[slot]
        if e is None:
            out.append(TermPoint(slot, None, None, None))
        else:
            out.append(TermPoint(slot, e.expiry, e.expiry_date, atm_iv(e)))
    return tuple(out)


# ── 25Δ 스큐 (§5.3) ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeltaPoint:
    """보간 격자 한 점 — 종목 하나의 자체 델타·σ(연율)·종목 품질."""

    strike: Decimal
    cp: CallPut
    delta: float
    iv: float
    quality: Quality

    def __post_init__(self) -> None:
        if not (math.isfinite(self.delta) and math.isfinite(self.iv) and self.iv > 0):
            raise ValueError(f"DeltaPoint 값이 유한하지 않다: {self}")
        worst(self.quality)


def interpolate_at_delta(
    points: Iterable[DeltaPoint], target: float
) -> tuple[float, tuple[DeltaPoint, ...]] | None:
    """델타 순으로 놓은 점들에서 target 델타의 IV — 선형보간. target 과 같은 델타의 점이 있으면 그
    점들의 IV 평균, 아니면 target 을 사이에 두는 이웃 두 점. 격자가 target 을 못 덮으면 None.

    델타가 같은 점은 행사가 순으로 놓는다(보간에는 이웃 두 점만 쓴다)."""
    if not math.isfinite(target):
        raise ValueError(f"target 이 유한하지 않다: {target!r}")
    pts = sorted(points, key=lambda p: (p.delta, p.strike))
    exact = [p for p in pts if p.delta == target]
    if exact:
        return math.fsum(p.iv for p in exact) / len(exact), tuple(exact)
    i = bisect_left([p.delta for p in pts], target)
    if i == 0 or i == len(pts):
        return None
    a, b = pts[i - 1], pts[i]
    return a.iv + (target - a.delta) / (b.delta - a.delta) * (b.iv - a.iv), (a, b)


@dataclass(frozen=True, slots=True)
class Skew:
    """§5.3 결과. value = put_iv − call_iv(연율 소수, 0.05 = 5%p) — 한쪽이라도 못 구하면 None.

    put_points·call_points: 보간에 쓴 점(행사가). reasons: no_forward(invalid), no_put_points·
    no_call_points·put_out_of_range·call_out_of_range(null — 품질 ok). quality: 값이 있으면 만기 F
    품질 ⊕ 쓴 점의 종목 품질.
    """

    value: float | None
    quality: Quality
    put_iv: float | None
    call_iv: float | None
    put_points: tuple[Decimal, ...]
    call_points: tuple[Decimal, ...]
    reasons: tuple[str, ...]


def delta_points(ev: ExpiryEval) -> tuple[DeltaPoint, ...]:
    """만기의 자체 (델타, σ) 격자 — 자체 그릭스가 있는 종목(GEX 에 든 종목)만."""
    out: list[DeltaPoint] = []
    for o in ev.options:
        if o.greeks is None or o.iv is None or o.iv.sigma is None:
            continue
        out.append(DeltaPoint(o.quote.strike, o.quote.cp, o.greeks.delta, o.iv.sigma, o.quality))
    return tuple(out)


def skew_25d(ev: ExpiryEval, *, delta: float = SKEW_DELTA) -> Skew:
    """§5.3 만기 하나의 25Δ 스큐 `IV(Δ_put = −delta) − IV(Δ_call = +delta)`."""
    if not (math.isfinite(delta) and 0 < delta < 1):
        raise ValueError(f"delta 는 0 과 1 사이: {delta!r}")
    if ev.forward.F is None:
        return Skew(None, "invalid", None, None, (), (), ("no_forward",))
    pts = delta_points(ev)
    reasons: list[str] = []

    def side(cp: CallPut, target: float, name: str) -> tuple[float, tuple[DeltaPoint, ...]] | None:
        grid = [p for p in pts if p.cp == cp]
        got = interpolate_at_delta(grid, target) if grid else None
        if not grid:
            reasons.append(f"no_{name}_points")
        elif got is None:
            reasons.append(f"{name}_out_of_range")
        return got

    put, call = side("P", -delta, "put"), side("C", delta, "call")
    used: list[DeltaPoint] = [*(put[1] if put else ()), *(call[1] if call else ())]
    # 델타·IV 는 그 만기 F 로 구했다 — F 품질도 입력(§0 합성, §3.7 ATM IV 와 같게)
    qualities: list[Quality] = [ev.forward.quality, *(p.quality for p in used)]
    quality = worst(*qualities)
    put_iv = None if put is None else put[0]
    call_iv = None if call is None else call[0]
    value = None if put_iv is None or call_iv is None else put_iv - call_iv
    return Skew(
        value,
        "ok" if value is None else quality,
        put_iv,
        call_iv,
        tuple(p.strike for p in put[1]) if put is not None else (),
        tuple(p.strike for p in call[1]) if call is not None else (),
        tuple(reasons),
    )


# ── ATM IV 보간 (§3.7 — 표 입력) ──────────────────────────────────────────────


def atm_iv_from_points(
    F: float | None,
    f_quality: Quality,
    points: Mapping[Decimal, Sequence[tuple[float, Quality]]],
) -> AtmIv:
    """§3.7 을 행사가 → [(IV, 품질)] 표로 — `core.levels.atm_iv` 와 같은 규칙(K1 ≤ F ≤ K2 두
    행사가의 콜·풋 평균을 F 로 선형보간, 한쪽 행사가만·한 다리만이면 estimated, 없으면 invalid).
    표의 행사가는 IV 가 없는 행사가도 담을 수 있다(빈 목록 — 그 행사가가 이웃이면 one_side). KRX
    일별 백필(§5.4)용 — 만기 평가(`ExpiryEval`)가 없는 입력."""
    worst(f_quality)
    if F is None:
        return AtmIv(None, "invalid", (), ("no_forward",))
    k1 = max((k for k in points if k <= F), default=None)
    k2 = min((k for k in points if k >= F), default=None)
    reasons: list[str] = []
    qualities: list[Quality] = [f_quality]

    def value_at(k: Decimal | None) -> float | None:
        ivs = list(points.get(k, ())) if k is not None else []
        if not ivs:
            return None
        for s, _ in ivs:
            if not (math.isfinite(s) and s > 0):
                raise ValueError(f"IV 는 유한한 양수: {s!r} (행사가 {k})")
        if len(ivs) == 1 and "one_leg" not in reasons:
            reasons.append("one_leg")
        qualities.extend(q for _, q in ivs)
        return math.fsum(s for s, _ in ivs) / len(ivs)

    if k1 is not None and k1 == k2:
        used: list[tuple[Decimal, float | None]] = [(k1, value_at(k1))]
    else:
        used = [(k, v) for k, v in ((k1, value_at(k1)), (k2, value_at(k2))) if k is not None]
        if any(v is None for _, v in used) or len(used) < 2:
            reasons.append("one_side")
    pts = [(k, v) for k, v in used if v is not None]
    if not pts:
        return AtmIv(None, "invalid", (), ("no_iv",))
    if len(pts) == 1:
        value = pts[0][1]
    else:
        (a, va), (b, vb) = pts
        value = va + (F - float(a)) / float(b - a) * (vb - va)
    if reasons:
        qualities.append("estimated")
    return AtmIv(value, worst(*qualities), tuple(k for k, _ in pts), tuple(reasons))


# ── KRX 일별 ATM IV (§5.4 백필) ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class KrxIvRow:
    """KRX 옵션 일별 정규(주간) 행 하나 — 만기 하나(월물). close: 종가(pt, 없으면 None), iv_pct:
    `IMP_VOLT`(%, 없으면 None), volume: 당일 거래량(`ACC_TRDVOL`)."""

    strike: Decimal
    cp: CallPut
    close: Decimal | None
    iv_pct: float | None
    volume: int

    def __post_init__(self) -> None:
        if self.cp not in ("C", "P") or self.volume < 0 or not self.strike > 0:
            raise ValueError(f"KrxIvRow 필드가 맞지 않는다: {self}")
        if self.iv_pct is not None and not (math.isfinite(self.iv_pct) and self.iv_pct > 0):
            raise ValueError(f"IMP_VOLT 는 유한한 양수(없으면 None): {self.iv_pct!r}")
        if self.close is not None and not (self.close.is_finite() and self.close >= 0):
            raise ValueError(f"종가는 0 이상(없으면 None): {self.close}")


@dataclass(frozen=True, slots=True)
class KrxAtm:
    """KRX 일별 월물 ATM IV — forward: §1.3 F(종가 패리티), atm: §3.7(IMP_VOLT)."""

    forward: ForwardResult
    atm: AtmIv


def krx_atm_iv(rows: Iterable[KrxIvRow], s_ref: Decimal | float) -> KrxAtm:
    """§5.4 과거 일별 값 — KRX `IMP_VOLT`(정규 행·당일 거래 있는 종목, probe_results #15)의 ATM IV.

    기본값 [확인 필요]: F 는 §1.3 합성선물(당일 거래 있는 행의 종가를 가격으로, ATM 기준 s_ref =
    그날 근월물 선물 정산가 — 교차 확인 기준가 없음 `no_futures_ref`), ATM IV 는 §3.7 을 당일 거래
    있는 행사가만으로(`atm_iv_from_points` — 거래 있는 ATM 두 행사가). 같은 (행사가, 콜/풋) 이
    두 번이면 ValueError.
    """
    seen: set[tuple[Decimal, CallPut]] = set()
    traded: list[KrxIvRow] = []
    for r in rows:
        if (r.strike, r.cp) in seen:
            raise ValueError(f"같은 종목이 두 번: {r.strike} {r.cp}")
        seen.add((r.strike, r.cp))
        if r.volume > 0:
            traded.append(r)
    listed = sorted({k for k, _ in seen})
    calls: dict[Decimal, Decimal | None] = {}
    puts: dict[Decimal, Decimal | None] = {}
    table: dict[Decimal, list[tuple[float, Quality]]] = {}
    for r in traded:
        price = r.close if r.close is not None and r.close > 0 else None
        (calls if r.cp == "C" else puts)[r.strike] = price
        if r.iv_pct is not None:
            table.setdefault(r.strike, []).append((r.iv_pct / 100, "ok"))
    fwd = synthetic_forward(calls, puts, s_ref, strikes=listed or None)
    return KrxAtm(fwd, atm_iv_from_points(fwd.F, fwd.quality, table))


# ── IV 랭크·퍼센타일 (§5.4) ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DailyIv:
    """일별 월물 ATM IV 한 값 — 그날 주간 마감 기준(§5.4). source: self(자체 §3.7) · krx(IMP_VOLT
    백필)."""

    trade_date: date
    value: float
    source: IvSource
    quality: Quality

    def __post_init__(self) -> None:
        _as_date("trade_date", self.trade_date)
        if not (math.isfinite(self.value) and self.value > 0) or self.source not in get_args(
            IvSource
        ):
            raise ValueError(f"DailyIv 필드가 맞지 않는다: {self}")
        worst(self.quality)


@dataclass(frozen=True, slots=True)
class IvRank:
    """§5.4 결과. rank = (x − min)/(max − min), percentile = #{xᵢ < x}/n — n < min_days 면 둘 다
    None, max = min 이면 rank 만 None. n: 창 안 값 수(오늘 포함). start·end: 창의 첫·끝 거래일.
    reasons: short_window(n < 252 — estimated)·mixed_sources(estimated)·too_few_days(null)·
    flat_window(rank null)."""

    rank: float | None
    percentile: float | None
    quality: Quality
    n: int
    start: date
    end: date
    sources: tuple[IvSource, ...]
    reasons: tuple[str, ...]


def window_start(end: date, days: int, cal: TradingCalendar) -> date:
    """end 를 포함해 거래일 days 개인 창의 첫 거래일(end 가 거래일이 아니어도 end 부터 센다)."""
    if days < 1:
        raise ValueError(f"days 는 1 이상: {days!r}")
    d = _as_date("end", end)
    for _ in range(days - 1):
        d = cal.prev_trading_day(d)
    return d


def iv_rank(
    history: Iterable[DailyIv],
    today: DailyIv,
    cal: TradingCalendar,
    *,
    window: int = RANK_WINDOW,
    min_days: int = RANK_MIN_DAYS,
) -> IvRank:
    """§5.4 IV 랭크·퍼센타일 — 오늘 x 와 최근 window 거래일(오늘 포함 [확인 필요]) 일별 값.

    history: 지난 일별 값(오늘 것·창 밖은 무시). 같은 날이 두 번이면 ValueError. 품질 = 오늘 값
    품질, n < window 면 estimated [확인 필요], 두 원천(self·krx)을 섞었으면 estimated [확인 필요].
    n < min_days 면 null [확인 필요].
    """
    if not (1 <= min_days <= window):
        raise ValueError(f"1 ≤ min_days ≤ window 여야 한다: {min_days}, {window}")
    end = today.trade_date
    start = window_start(end, window, cal)
    xs: dict[date, DailyIv] = {}
    for h in history:
        if h.trade_date in xs:
            raise ValueError(f"같은 날이 두 번: {h.trade_date}")
        if start <= h.trade_date < end:
            xs[h.trade_date] = h
    xs[end] = today
    values = [v.value for v in xs.values()]
    n = len(values)
    sources: tuple[IvSource, ...] = tuple(sorted({v.source for v in xs.values()}))
    mixed = len(sources) > 1
    reasons: list[str] = []
    quality = today.quality
    if n < window:
        reasons.append("short_window")
        quality = worst(quality, "estimated")
    if mixed:
        reasons.append("mixed_sources")
        quality = worst(quality, "estimated")
    if n < min_days:
        reasons.append("too_few_days")
        return IvRank(None, None, quality, n, start, end, sources, tuple(reasons))
    x, lo, hi = today.value, min(values), max(values)
    rank: float | None = None
    if hi > lo:
        rank = (x - lo) / (hi - lo)
    else:
        reasons.append("flat_window")
    percentile = sum(1 for v in values if v < x) / n
    return IvRank(rank, percentile, quality, n, start, end, sources, tuple(reasons))


# ── HV20·IV − HV (§5.5) ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Settlement:
    """KRX 선물 정산가 한 값(주간 행) — contract 는 결제월 YYYYMM."""

    trade_date: date
    contract: str
    price: float

    def __post_init__(self) -> None:
        _as_date("trade_date", self.trade_date)
        if not (_YYYYMM.fullmatch(self.contract) and math.isfinite(self.price) and self.price > 0):
            raise ValueError(f"Settlement 필드가 맞지 않는다: {self}")


def held_contract(
    close_of: date, last_trade: Mapping[str, date], cal: TradingCalendar
) -> str | None:
    """close_of 장 마감 뒤 들고 있는 근월물 — 롤은 최종거래일 직전 거래일 장 마감(PLAN §9.3):
    최종거래일의 직전 거래일이 close_of 보다 뒤인 결제월 중 최종거래일이 가장 이른 것."""
    cands = [(d, c) for c, d in last_trade.items() if cal.prev_trading_day(d) > close_of]
    return min(cands)[1] if cands else None


@dataclass(frozen=True, slots=True)
class RealizedVol:
    """§5.5 HV. value = stdev(ln(Pₜ/Pₜ₋₁)) × √annualization — 수익률이 days 개가 안 되면 None.

    end: 창의 마지막 거래일(정산가가 있는 가장 늦은 날, 인자 end 이하). returns·contracts: 창의 날
    순서대로 수익률과 그날 들고 있던 결제월(연결 규칙). missing: 수익률을 못 만든 날.
    reasons: no_data·missing_returns(null)."""

    value: float | None
    end: date | None
    returns: tuple[float, ...]
    contracts: tuple[str, ...]
    missing: tuple[date, ...]
    reasons: tuple[str, ...]


def realized_vol(
    settles: Iterable[Settlement],
    last_trade: Mapping[str, date],
    cal: TradingCalendar,
    *,
    end: date | None = None,
    days: int = HV_DAYS,
    annualization: float = HV_ANNUALIZATION,
) -> RealizedVol:
    """§5.5 HV20 — 근월물 연결 선물 정산가의 일간 로그수익률 표본표준편차(n − 1) × √252 [확인 필요].

    창 = 정산가가 있는 가장 늦은 거래일(end 이하)까지 거래일 days 개. 날 t 의 수익률은 t 의 앞
    거래일 p 장 마감에 들고 있던 결제월(`held_contract` — PLAN §9.3 롤)의 ln(P(t)/P(p)) — 같은
    결제월 안에서만 재므로 롤 날의 월물 간 가격 차는 수익률에 들지 않는다(가격 조정 연결선물).
    한 날이라도 두 정산가가 없으면 null(`missing_returns` — "20일이 안 되면 null"). last_trade:
    결제월 → 최종거래일. 같은 (날, 결제월) 이 두 번이면 ValueError.
    """
    if days < 2:
        raise ValueError(f"days 는 2 이상(표본표준편차): {days!r}")
    if not (math.isfinite(annualization) and annualization > 0):
        raise ValueError(f"annualization 은 유한한 양수: {annualization!r}")
    prices: dict[tuple[date, str], float] = {}
    for s in settles:
        k = (s.trade_date, s.contract)
        if k in prices:
            raise ValueError(f"같은 정산가가 두 번: {k}")
        if end is None or s.trade_date <= end:
            prices[k] = s.price
    if not prices:
        return RealizedVol(None, None, (), (), (), ("no_data",))
    last = max(d for d, _ in prices)
    t = last
    window: list[date] = []
    for _ in range(days):
        window.append(t)
        t = cal.prev_trading_day(t)
    window.reverse()
    returns: list[float] = []
    contracts: list[str] = []
    missing: list[date] = []
    for t in window:
        p = cal.prev_trading_day(t)
        c = held_contract(p, last_trade, cal)
        a = None if c is None else prices.get((p, c))
        b = None if c is None else prices.get((t, c))
        if c is None or a is None or b is None:
            missing.append(t)
            continue
        returns.append(math.log(b / a))
        contracts.append(c)
    if missing:
        return RealizedVol(
            None, last, tuple(returns), tuple(contracts), tuple(missing), ("missing_returns",)
        )
    value = statistics.stdev(returns) * math.sqrt(annualization)
    return RealizedVol(value, last, tuple(returns), tuple(contracts), (), ())


@dataclass(frozen=True, slots=True)
class IvHv:
    """§5.5 결과 `ATM IV − HV20`(연율 소수). 품질 = ATM IV 품질 — ATM IV 가 없으면 invalid, HV 가
    없으면(20일 안 됨) null."""

    value: float | None
    quality: Quality
    atm_iv: float | None
    hv: RealizedVol


def iv_minus_hv(atm: float | None, atm_quality: Quality, hv: RealizedVol) -> IvHv:
    worst(atm_quality)
    if atm is None:
        return IvHv(None, "invalid", None, hv)
    if not (math.isfinite(atm) and atm > 0):
        raise ValueError(f"ATM IV 는 유한한 양수: {atm!r}")
    if hv.value is None:
        return IvHv(None, atm_quality, atm, hv)
    return IvHv(atm - hv.value, atm_quality, atm, hv)

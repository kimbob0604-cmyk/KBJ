"""GEX 브리핑 — 행사가별 GEX·레벨·옵션 시세로 '오늘의 구조'를 문장과 레벨표로 (순수 함수).

사용자 요청(2026-10-21): 행사가별 콜·풋·순감마 막대 + 레벨마다 '무슨 뜻인지'(감마 국면, 첫 시험대,
막히면/뚫리면) + 옵션 포지셔닝(양매수·양매수+외가 매도·숏 스트랭글·합성선물) 손익분기를 장중에 계속.

- 문장은 규칙(템플릿)으로만 만든다 — LLM 이 아니다(CLAUDE.md §1-3). 값은 입력에서 계산한 것만.
- GEX 단위는 원/기초 1%(콜 +, 풋 −, `core.gex.gex.option_gex`). 레벨·가격은 pt.
- 전략 손익은 만기 손익(pt × 승수, 수수료·증거금·세금 제외) — 매매 권유가 아니다.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

__all__ = [
    "Briefing",
    "ChainQuote",
    "KeyLevel",
    "Leg",
    "Strategy",
    "StrikeBar",
    "StrikeNet",
    "briefing",
    "money",
    "one_day_move",
    "sigma_to_expiry",
    "strategies",
    "strike_table",
    "table_walls",
]

EOK: Final = 1e8
JO: Final = 1e12
SCENARIO_PCT: Final = (-10.0, -7.5, -5.0, -2.5, 0.0, 2.5, 5.0, 7.5, 10.0)
STRANGLE_SIGMA: Final = 1.5  # 숏 스트랭글 행사가 — 만기까지 ±1.5σ(원문 '저는 1.5시그마')
WALL_STRONG: Final = 2.0  # 벽의 콜(풋) GEX 가 표 중앙값의 이 배 이상이면 '강'
SESSIONS_PER_YEAR: Final = 252
# 패리티(C − P ≈ F − K)로 본 내재 선도가 행사가들 중앙값에서 이만큼(pt, 또는 C+P 의 비율 중 큰 값)
# 넘게 벗어나면 그 행사가 시세는 쓰지 않는다 — 장 마감 뒤 한쪽만 남은 호가 등
PARITY_TOL_PT: Final = 1.0
PARITY_TOL_RATIO: Final = 0.15

Kind = Literal[
    "em_up", "call_wall2", "call_wall", "flip", "spot", "put_wall", "put_wall2", "em_down"
]


@dataclass(frozen=True, slots=True)
class StrikeBar:
    """만기 하나·행사가 하나의 GEX(원/1%)."""

    strike: float
    call: float
    put: float


@dataclass(frozen=True, slots=True)
class StrikeNet:
    """행사가 하나(만기 합산) — 콜·풋·순 GEX(원/1%)."""

    strike: float
    call: float
    put: float
    net: float


@dataclass(frozen=True, slots=True)
class KeyLevel:
    kind: Kind
    label: str
    value: float
    dist_pt: float
    dist_pct: float
    lines: tuple[str, ...] = ()
    strength: Literal["강", "약"] | None = None
    gex: float | None = None  # 그 행사가 순 GEX(원/1%)


@dataclass(frozen=True, slots=True)
class Briefing:
    regime: Literal["long", "short"]
    regime_label: str
    net_gex: float  # 원/1%
    net_vs_avg: float | None  # 평소(최근 순GEX 절댓값 평균) 대비 배수
    headline: str
    first_up: float | None
    first_down: float | None
    levels: tuple[KeyLevel, ...]


@dataclass(frozen=True, slots=True)
class ChainQuote:
    """만기 하나의 옵션 한 종목 — 가격은 pt(중간값 또는 현재가)."""

    strike: float
    cp: Literal["C", "P"]
    price: float


@dataclass(frozen=True, slots=True)
class Leg:
    side: Literal["buy", "sell"]
    cp: Literal["C", "P"]
    strike: float
    price: float


@dataclass(frozen=True, slots=True)
class Strategy:
    key: str
    name: str
    idea: str  # 한 줄 — 어떤 전망일 때 쓰는 구조인가
    legs: tuple[Leg, ...]
    net_premium: float  # pt — 양수 = 받음(매도 우위), 음수 = 냄
    breakevens: tuple[float, ...]
    max_profit: float | None  # pt — None = 제한 없음
    max_loss: float | None  # pt(양수 = 손실 크기) — None = 제한 없음
    scenario: tuple[tuple[float, float], ...] = field(default=())  # (등락 %, 만기 손익 pt)
    note: str = ""


# ── 행사가 표 ─────────────────────────────────────────────────────────────────────────────


def strike_table(bars: Iterable[StrikeBar]) -> list[StrikeNet]:
    """만기를 합쳐 행사가별 콜·풋·순 GEX(행사가 내림차순)."""
    acc: dict[float, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for b in bars:
        acc[b.strike][0] += b.call
        acc[b.strike][1] += b.put
    return [StrikeNet(k, c, p, c + p) for k, (c, p) in sorted(acc.items(), reverse=True)]


def table_walls(rows: Sequence[StrikeNet], price: float) -> tuple[float | None, float | None]:
    """행사가 표(만기 합산)에서 콜월(현재가 이상 콜 GEX 최대)·풋월(현재가 이하 풋 GEX 절댓값 최대).

    그쪽에 후보가 없으면 전체에서 고른다.
    """
    calls = [r for r in rows if r.call > 0]
    puts = [r for r in rows if r.put < 0]
    cu = [r for r in calls if r.strike >= price] or calls
    pd = [r for r in puts if r.strike <= price] or puts
    cw = max(cu, key=lambda r: (r.call, -r.strike)).strike if cu else None
    pw = min(pd, key=lambda r: (r.put, -r.strike)).strike if pd else None
    return cw, pw


def money(v: float) -> str:
    """원 → '+5.6조'·'-5,857억'(화면 문장용)."""
    sign = "+" if v > 0 else "-" if v < 0 else ""
    a = abs(v)
    if a >= JO:
        return f"{sign}{a / JO:.1f}조"
    return f"{sign}{a / EOK:,.0f}억"


def _pt(v: float) -> str:
    return f"{v:,.1f}" if v % 1 else f"{v:,.0f}"


def _second(rows: Sequence[StrikeNet], beyond: float, *, up: bool) -> float | None:
    """벽 너머(위면 콜 GEX 최대, 아래면 풋 GEX 절댓값 최대) 다음 벽."""
    if up:
        cand = [r for r in rows if r.strike > beyond and r.call > 0]
        return max(cand, key=lambda r: r.call).strike if cand else None
    cand = [r for r in rows if r.strike < beyond and r.put < 0]
    return min(cand, key=lambda r: r.put).strike if cand else None


def _strength(
    rows: Sequence[StrikeNet], strike: float, *, call: bool
) -> Literal["강", "약"] | None:
    vals = sorted(abs(r.call if call else r.put) for r in rows if (r.call if call else r.put))
    row = next((r for r in rows if r.strike == strike), None)
    if not vals or row is None:
        return None
    mid = vals[len(vals) // 2]
    v = abs(row.call if call else row.put)
    return "강" if mid > 0 and v >= WALL_STRONG * mid else "약"


def briefing(
    rows: Sequence[StrikeNet],
    *,
    price: float,
    flip: float | None,
    call_wall: float | None,
    put_wall: float | None,
    expected_move: float | None,
    recent_net: Sequence[float] = (),
) -> Briefing:
    """레벨표(높은 값부터)와 머리 문장.

    `recent_net`: 최근 세션들의 순GEX(원/1%) — 평소 대비 배수.
    """
    net = sum(r.net for r in rows)
    # 국면은 플립 기준(현재가가 플립 위면 롱감마) — 플립이 없을 때만 순GEX 부호
    above = price >= flip if flip is not None else net >= 0
    regime: Literal["long", "short"] = "long" if above else "short"
    regime_label = "Long γ · 안정 구간" if regime == "long" else "Short γ · 불안 구간"
    base = [abs(x) for x in recent_net if x]
    vs = abs(net) / (sum(base) / len(base)) if base else None
    net_at = {r.strike: r.net for r in rows}

    def lvl(
        kind: Kind, label: str, v: float, lines: Sequence[str] = (),
        strength: Literal["강", "약"] | None = None,
    ) -> KeyLevel:  # fmt: skip
        d = v - price
        return KeyLevel(kind, label, round(v, 2), round(d, 2), round(d / price * 100, 2),
                        tuple(lines), strength, net_at.get(v))  # fmt: skip

    out: list[KeyLevel] = []
    cw2 = _second(rows, call_wall, up=True) if call_wall is not None else None
    pw2 = _second(rows, put_wall, up=False) if put_wall is not None else None
    if expected_move:
        up, dn = price + expected_move, price - expected_move
        out.append(lvl("em_up", f"오늘 예상 상단 — 1일 예상폭 +{_pt(expected_move)}pt", up))
        out.append(lvl("em_down", f"오늘 예상 하단 — 1일 예상폭 -{_pt(expected_move)}pt", dn))
    if call_wall is not None:
        # 되돌림 목표 — 현재가(콜월) 아래 가장 가까운 지지(플립·풋월·2차 풋월)
        floor = min(price, call_wall)
        under = [v for v in (flip, put_wall, pw2) if v is not None and v < floor]
        lines = [f"막히면 → {_pt(max(under))}까지 되돌림." if under
                 else "막히면 → 아래 받쳐 줄 레벨이 멀어 되돌림 폭이 클 수 있습니다."]  # fmt: skip
        if cw2 is not None:
            lines.append(f"{_pt(call_wall)} 위에서 마감하면 → 다음 저항은 {_pt(cw2)}입니다.")
        out.append(lvl("call_wall", "콜월", call_wall, lines,
                       strength=_strength(rows, call_wall, call=True)))  # fmt: skip
    if cw2 is not None and call_wall is not None:
        out.append(lvl("call_wall2", "2차 콜월", cw2, [
            f"콜월 {_pt(call_wall)} 위로 먼저 올라서야 의미가 생기는 자리입니다. "
            "장중에 잠깐 넘는 일은 흔하고 대개 되돌아오기 때문에, "
            f"{_pt(call_wall)} 위에서 마감해야 구조가 위로 옮겨간 것으로 "
            "봅니다."]))  # fmt: skip
    if flip is not None:
        if price < flip:
            fl = ["이미 이 선 아래에 있습니다(숏감마 — 움직임이 커지는 쪽). "
                  "되찾고 마감해야 벽 사이 "
                  "왕복(롱감마)으로 돌아옵니다."]  # fmt: skip
        else:
            fl = ["이 선 위에 있습니다(롱감마 — 딜러 헤지가 움직임을 줄이는 쪽). 아래로 깨지면 "
                  "숏감마로 바뀌어 변동이 커집니다."]  # fmt: skip
        out.append(lvl("flip", "감마 플립 · 레짐선", flip, fl))
    if put_wall is not None:
        lines = [f"무너지면 → {_pt(pw2)} 2차 풋월까지 열립니다." if pw2 is not None
                 else "무너지면 → 아래 받쳐 줄 벽이 얇습니다."]  # fmt: skip
        out.append(lvl("put_wall", "풋월", put_wall, lines,
                       strength=_strength(rows, put_wall, call=False)))  # fmt: skip
    if pw2 is not None:
        out.append(lvl("put_wall2", "2차 풋월", pw2))
    ups = sorted(x.value for x in out if x.value > price and x.kind not in ("em_up",))
    dns = sorted((x.value for x in out if x.value < price and x.kind not in ("em_down",)),
                 reverse=True)  # fmt: skip
    first_up, first_down = (ups[0] if ups else None), (dns[0] if dns else None)
    where = "롱감마 구간" if regime == "long" else "숏감마 구간"
    test = [f"위로 {_pt(first_up)}" if first_up is not None else None,
            f"아래로 {_pt(first_down)}" if first_down is not None else None]  # fmt: skip
    tests = ", ".join(t for t in test if t)
    headline = f"{where}에서 출발합니다." + (f" 오늘 첫 시험대는 {tests}입니다." if tests else "")
    spot = lvl("spot", "현재가", price, [f"첫 반응이 나올 자리는 {tests}입니다."] if tests else [])
    out.append(spot)
    out.sort(key=lambda x: -x.value)
    return Briefing(regime, regime_label, net, vs, headline, first_up, first_down, tuple(out))


# ── 옵션 포지셔닝 ────────────────────────────────────────────────────────────────────────


def _payoff(legs: Sequence[Leg], s: float) -> float:
    """만기 손익(pt) — 매수는 가치 − 가격, 매도는 가격 − 가치."""
    t = 0.0
    for g in legs:
        value = max(s - g.strike, 0.0) if g.cp == "C" else max(g.strike - s, 0.0)
        t += (value - g.price) if g.side == "buy" else (g.price - value)
    return t


def _breakevens(legs: Sequence[Leg], lo: float, hi: float) -> tuple[float, ...]:
    """손익이 0 을 지나는 가격(꺾이는 곳이 행사가뿐이라 구간마다 직선 — 선형 보간으로 정확)."""
    pts = sorted({lo, hi, *(g.strike for g in legs)})
    out: list[float] = []
    for a, b in itertools.pairwise(pts):
        pa, pb = _payoff(legs, a), _payoff(legs, b)
        if pa == 0 and (not out or out[-1] != a):
            out.append(a)
        elif pa * pb < 0:
            out.append(round(a + (b - a) * (-pa) / (pb - pa), 2))
    return tuple(out)


def _bounds(legs: Sequence[Leg], lo: float, hi: float) -> tuple[float | None, float | None]:
    """구간 안 최대 이익·최대 손실(양 끝 기울기가 남으면 그쪽은 제한 없음 = None)."""
    pts = sorted({lo, hi, *(g.strike for g in legs)})
    vals = [_payoff(legs, p) for p in pts]
    slope_lo = _payoff(legs, lo - 1) - vals[0]
    slope_hi = _payoff(legs, hi + 1) - vals[-1]
    unbounded_up = slope_hi > 1e-9 or slope_lo > 1e-9
    unbounded_dn = slope_hi < -1e-9 or slope_lo < -1e-9
    mp = None if unbounded_up else max(vals)
    ml = None if unbounded_dn else -min(vals)
    return mp, ml


def _parity_clean(px: dict[tuple[float, str], float]) -> dict[tuple[float, str], float]:
    """콜·풋이 다 있는 행사가의 내재 선도(K + C − P)가 중앙값에서 크게 벗어나면 그 행사가를 뺀다."""
    fwd = {k: k + px[(k, "C")] - px[(k, "P")] for (k, cp) in px if cp == "C" and (k, "P") in px}
    if len(fwd) < 3:
        return px
    vals = sorted(fwd.values())
    med = vals[len(vals) // 2]
    bad: set[float] = set()
    for k, f in fwd.items():
        tol = max(PARITY_TOL_PT, PARITY_TOL_RATIO * (px[(k, "C")] + px[(k, "P")]))
        if abs(f - med) > tol:
            bad.add(k)
    return {key: v for key, v in px.items() if key[0] not in bad}


def _nearest(strikes: Sequence[float], x: float) -> float:
    return min(strikes, key=lambda k: (abs(k - x), k))


def strategies(
    quotes: Sequence[ChainQuote], *, price: float, sigma_to_expiry: float | None
) -> list[Strategy]:
    """한 만기 옵션 시세로 네 구조의 손익분기·최대 손익·시나리오(만기, pt).

    sigma_to_expiry: 만기까지 ±1σ(pt) — 외가 행사가를 고르는 데 쓴다(없으면 ATM ±5%).
    """
    px = _parity_clean({(q.strike, q.cp): q.price for q in quotes if q.price > 0})
    both = sorted({k for (k, _cp) in px if (k, "C") in px and (k, "P") in px})
    if not both:
        return []
    atm = _nearest(both, price)
    sig = sigma_to_expiry if sigma_to_expiry and sigma_to_expiry > 0 else price * 0.05
    calls = sorted(k for (k, cp) in px if cp == "C")
    puts = sorted(k for (k, cp) in px if cp == "P")

    def leg(side: Literal["buy", "sell"], cp: Literal["C", "P"], k: float) -> Leg:
        return Leg(side, cp, k, px[(k, cp)])

    # 같은 거리면 더 외가(콜은 위, 풋은 아래) — 파는 쪽을 보수적으로
    def otm_call(target: float) -> float | None:
        c = [k for k in calls if k > atm]
        return min(c, key=lambda k: (abs(k - target), -k)) if c else None

    def otm_put(target: float) -> float | None:
        p = [k for k in puts if k < atm]
        return min(p, key=lambda k: (abs(k - target), k)) if p else None

    built: list[tuple[str, str, str, list[Leg], str]] = []
    built.append(("long_straddle", "롱 스트래들(양매수)",
                  "방향은 모르지만 크게 움직일 것 — 위아래 어느 쪽이든 손익분기 밖이면 이익",
                  [leg("buy", "C", atm), leg("buy", "P", atm)], ""))  # fmt: skip
    kc, kp = otm_call(atm + sig), otm_put(atm - sig)
    if kc is not None and kp is not None:
        built.append(("straddle_wings", "양매수 + 외가 매도",
                      "양매수의 손익분기를 좁힌다 — 외가 콜·풋을 팔아 비용을 줄이는 대신 "
                      "이익 상한이 생긴다",
                      [leg("buy", "C", atm), leg("buy", "P", atm), leg("sell", "P", kp),
                       leg("sell", "C", kc)], ""))  # fmt: skip
    sc, sp = otm_call(atm + STRANGLE_SIGMA * sig), otm_put(atm - STRANGLE_SIGMA * sig)
    if sc is not None and sp is not None:
        built.append(("short_strangle", f"숏 스트랭글(±{STRANGLE_SIGMA}σ)",
                      "이 범위를 벗어나지 않을 것 — 프리미엄을 받고, "
                      "벗어나면 손실이 제한 없이 커진다",
                      [leg("sell", "C", sc), leg("sell", "P", sp)],
                      "변동성 예측(IV·HAR 등) 없이 쓰지 않는다 — "
                      "급변 때 손실 제한 없음"))  # fmt: skip
    if kp is not None:
        built.append(("synthetic_long", "합성 매수(콜 매수 + 외가 풋 매도)",
                      "아래로는 외가 풋까지만 버티고 위는 연다 — 하방 제한적·상방 기대일 때",
                      [leg("buy", "C", atm), leg("sell", "P", kp)],
                      "외가 풋 아래로 내려가면 손실이 커진다"))  # fmt: skip
    lo, hi = price * 0.5, price * 1.5
    out: list[Strategy] = []
    for key, name, idea, legs, note in built:
        net = sum(g.price if g.side == "sell" else -g.price for g in legs)
        mp, ml = _bounds(legs, lo, hi)
        if mp is not None and mp <= 0:
            note = "이 시세로는 만기 이익 구간이 없다 — 호가가 비정상일 수 있다" + (
                f" · {note}" if note else "")  # fmt: skip
        scen = tuple((p, round(_payoff(legs, price * (1 + p / 100)), 2)) for p in SCENARIO_PCT)
        out.append(Strategy(key, name, idea, tuple(legs), round(net, 2),
                            _breakevens(legs, lo, hi), mp if mp is None else round(mp, 2),
                            ml if ml is None else round(ml, 2), scen, note))  # fmt: skip
    return out


def sigma_to_expiry(price: float, atm_iv: float | None, t_years: float | None) -> float | None:
    """만기까지 ±1σ(pt) = F × IV × √T."""
    if not atm_iv or not t_years or atm_iv <= 0 or t_years <= 0:
        return None
    return price * atm_iv * math.sqrt(t_years)


def one_day_move(price: float, atm_iv: float | None) -> float | None:
    """1세션 ±1σ(pt) = F × IV × √(1/252)."""
    if not atm_iv or atm_iv <= 0:
        return None
    return price * atm_iv / math.sqrt(SESSIONS_PER_YEAR)

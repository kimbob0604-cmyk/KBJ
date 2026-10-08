"""상단 띠 칩(docs/metrics.md §6.4·§6.5, docs/p3_design.md §4.4, v0.2 plan_kr:60~64).

칩마다 값은 `Sourced`(source·as_of·quality) 이거나 None 이다. 값이 없으면 None 과
사유(`note`)를 싣고 0 으로 그리지 않는다. P3 에 원천이 없는 칩은 `phase_pending`('P5' 등)만
있고 값이 없다.

- 반도체 쏠림(%p): r_semi = 두 종목(`ribbon.semis`) 시총가중 수익률(가중치 = 기간 시작 전일 시총),
  r_ex = (R_코스피·M_코스피 − r_semi·M_semi) ÷ (M_코스피 − M_semi), 쏠림 = r_semi − r_ex.
  입력 하나라도 없으면 None, 시총 합(M_코스피 − M_semi)이 0 이하이면 invalid(값 없음 + 사유).
- 경기민감 대 방어(%p): 경기민감 업종지수 수익률 평균 − 방어 업종지수 수익률 평균. 지수 코드가
  비어 있으면(`ribbon.cyclical`·`defensive` [확인 필요]) 칩은 '준비 중'.
- 세션·야간 카운트다운: `kbj.core.calendar`(코드 계산 — 공개 등급).
- 금통위 D-n: 다음 결정회의까지 남은 **달력일**(당일 D-0) — `config/calendar_events.yaml`
  (공개 등급).

튜닝값(`ribbon.semis`·`cyclical`·`defensive`)과 금통위 날짜는 부르는 쪽이 `kbj.config.markets` 로
읽어 넘긴다(이 모듈은 파일을 읽지 않는다).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal

from kbj.core.calendar import (
    State,
    TradingCalendar,
    night_session_opens,
    session_bounds,
    state_at,
)
from kbj.core.quality import Quality, Sourced
from kbj.core.rows import IndexBar
from kbj.core.time import KST
from kbj.engines.flows.ledger import (
    STOCK_KINDS,
    Ledger,
    close_as_of,
    source_label,
    worst_quality,
)
from kbj.engines.flows.totals import InvestorTotals
from kbj.engines.market.sectors import index_return_pct

__all__ = [
    "CHIP_ORDER",
    "PENDING",
    "Chip",
    "RibbonInputs",
    "SkewResult",
    "cyclical_vs_defensive",
    "ribbon",
    "semi_skew",
    "semi_skew_from_ledger",
    "session_chip",
]

Tier = Literal["public", "login"]
CALENDAR_SOURCE: Final = "KBJ:calendar"
EVENTS_SOURCE: Final = "config:calendar_events(한국은행 공표 일정)"

# P3 에 원천이 없는 칩(키 → 이름·등급·단계) — 값 없이 '준비 중'
PENDING: Final[Mapping[str, tuple[str, Tier, str]]] = {
    "credit_spread": ("신용스프레드 AA-", "public", "P5"),
    "credit_balance": ("신용잔고", "public", "P5"),
    "export_flash": ("수출 속보", "public", "P6"),
    "kr_vs_global": ("한국 대 글로벌", "login", "P5"),
    "gex_flip": ("GEX Flip 거리", "login", "P7"),
}
CHIP_ORDER: Final[tuple[str, ...]] = (
    "session",
    "bok_mpc",
    "credit_spread",
    "credit_balance",
    "export_flash",
    "market_turnover",
    "foreign_spot",
    "inst_spot",
    "newhigh_count",
    "semi_skew",
    "cyc_def",
    "kr_vs_global",
    "gex_flip",
)


@dataclass(frozen=True)
class Chip:
    key: str
    label: str
    value: Sourced[Any] | None
    tier: Tier
    phase_pending: str | None = None
    tags: tuple[str, ...] = ()
    note: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])


# ── 쏠림·경기민감 ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SkewResult:
    """%p 차이 계산 결과. value 가 None 이면 reason 이 있다."""

    value: float | None
    quality: Quality | None
    reason: str | None
    day: date | None
    source: str = ""
    parts: Mapping[str, float] = field(default_factory=dict[str, float])

    def sourced(self) -> Sourced[float] | None:
        if self.value is None or self.day is None or self.quality is None:
            return None
        return Sourced[float](
            value=self.value, source=self.source, as_of=close_as_of(self.day), quality=self.quality
        )


def _no(reason: str, day: date | None, quality: Quality | None = None) -> SkewResult:
    return SkewResult(None, quality, reason, day)


def semi_skew(
    semis: Sequence[tuple[float, int]], kospi_ret_pct: float, kospi_mktcap: int
) -> SkewResult:
    """순수 공식(metrics §6.4). `semis` = [(수익률 %, 가중 시총 원)], 코스피 수익률 %·시총 원."""
    m_semi = sum(m for _, m in semis)
    if not semis or m_semi <= 0:
        return _no("반도체 시총 합이 0 이하", None, Quality.INVALID)
    rest = kospi_mktcap - m_semi
    if rest <= 0:
        return _no("코스피 시총 − 반도체 시총이 0 이하", None, Quality.INVALID)
    r_semi = sum(r * m for r, m in semis) / m_semi
    r_ex = (kospi_ret_pct * kospi_mktcap - r_semi * m_semi) / rest
    return SkewResult(r_semi - r_ex, Quality.OK, None, None, parts={"r_semi": r_semi, "r_ex": r_ex})


def semi_skew_from_ledger(
    ledger: Ledger,
    kospi_bars: Sequence[IndexBar],
    day: date,
    *,
    semis: Sequence[str],
    period: int = 1,
    market: str = "KOSPI",
    kinds: Collection[str] = STOCK_KINDS,
) -> SkewResult:
    """원장 + 코스피 지수 일봉으로 쏠림(1일·5일). 가중치·M 은 기간 시작 전일(base) 시총."""
    if period < 1:
        raise ValueError("period 는 1 이상")
    days = ledger.days_upto(day, period + 1)
    if len(days) < period + 1 or days[-1] != day:
        return _no(f"원장에 {period + 1}영업일이 없다", day)
    base = days[0]
    parts: list[tuple[float, int]] = []
    qs: list[Quality] = []
    srcs: list[str] = []
    for code in semis:
        now_r, base_r = ledger.get(code, day), ledger.get(code, base)
        if (
            now_r is None
            or base_r is None
            or not now_r.usable
            or not base_r.usable
            or now_r.close is None
            or base_r.close is None
            or base_r.close <= 0
            or base_r.mktcap is None
        ):
            return _no(f"{code} 값 없음", day)
        parts.append(((now_r.close / base_r.close - 1) * 100, base_r.mktcap))
        qs += [now_r.quality, base_r.quality]
        srcs += [p for r in (now_r, base_r) for s in r.sources.values() for p in s.split("+")]
    base_rows = [r for r in ledger.day(base) if r.market == market and r.kind in kinds]
    if not base_rows:
        return _no(f"{base} {market} 행 없음", day)
    missing = [r.code for r in base_rows if not r.usable or r.mktcap is None]
    if missing:
        return _no(f"{base} {market} 시총 결측 {len(missing)}종목", day)
    m_kospi = sum(r.mktcap for r in base_rows if r.mktcap is not None)
    r_kospi = index_return_pct(kospi_bars, day, period)
    if r_kospi is None:
        return _no("코스피 지수 수익률 없음", day)
    k_bars = [b for b in kospi_bars if b.date in (day, base)]
    qs += [r.quality for r in base_rows] + [b.quality for b in k_bars]
    srcs += [b.source for b in k_bars]
    res = semi_skew(parts, r_kospi, m_kospi)
    if res.value is None:
        return SkewResult(None, res.quality, res.reason, day)
    return SkewResult(
        res.value,
        worst_quality(qs) or Quality.INVALID,
        None,
        day,
        source=source_label(srcs),
        parts=res.parts,
    )


def cyclical_vs_defensive(
    index_bars: Mapping[str, Sequence[IndexBar]],
    day: date,
    *,
    cyclical: Sequence[str],
    defensive: Sequence[str],
    period: int = 1,
) -> SkewResult:
    """경기민감 평균 수익률 − 방어 평균 수익률(%p). 코드가 비면 '준비 중' 사유."""
    if not cyclical or not defensive:
        return _no("업종 지수 코드 미정 — ribbon.cyclical·defensive [확인 필요]", day)
    rets: dict[str, list[float]] = {"cyclical": [], "defensive": []}
    qs: list[Quality] = []
    srcs: list[str] = []
    for group, codes in (("cyclical", cyclical), ("defensive", defensive)):
        for code in codes:
            bars = sorted(index_bars.get(code, ()), key=lambda b: b.date)
            r = index_return_pct(bars, day, period)
            if r is None:
                return _no(f"업종지수 {code} 수익률 없음", day)
            rets[group].append(r)
            used = [b for b in bars if b.date <= day][-(period + 1) :]
            qs += [b.quality for b in used]
            srcs += [b.source for b in used]
    cyc = sum(rets["cyclical"]) / len(rets["cyclical"])
    dfn = sum(rets["defensive"]) / len(rets["defensive"])
    return SkewResult(
        cyc - dfn,
        worst_quality(qs) or Quality.INVALID,
        None,
        day,
        source=source_label(srcs),
        parts={"cyclical": cyc, "defensive": dfn},
    )


# ── 세션 ────────────────────────────────────────────────────────────────────────────────


def _next_open(d: date, cal: TradingCalendar) -> datetime:
    nd = d if cal.is_trading_day(d) else cal.next_trading_day(d)
    return cal.equity_bounds(nd)[0]


def session_chip(now: datetime, cal: TradingCalendar) -> Chip:
    """세션 상태와 다음 전환까지 남은 시간(정규장 마감·야간 끝·다음 개장)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 는 시간대가 있는 시각")
    k = now.astimezone(KST)
    d = k.date()
    info = state_at(k, cal)
    trading = cal.is_trading_day(d)
    label: str
    until: datetime
    eq = cal.equity_bounds(d)
    if info.state is State.NIGHT:
        label = "야간장"
        start = d if k.time() >= session_bounds(d, "night")[0].time() else d - timedelta(days=1)
        until = session_bounds(start, "night")[1]
    elif trading and k < eq[0]:
        label, until = "장 시작 전", eq[0]
    elif trading and k < eq[1]:
        label, until = "정규장", eq[1]
    elif trading:
        label = "장 마감"
        if night_session_opens(d, cal) and k < session_bounds(d, "night")[0]:
            until = session_bounds(d, "night")[0]
        else:
            until = _next_open(d + timedelta(days=1), cal)
    else:
        label, until = "휴장", _next_open(d, cal)
    remaining = max(0, int((until - k).total_seconds()))
    return Chip(
        key="session",
        label="세션",
        value=Sourced[str](value=label, source=CALENDAR_SOURCE, as_of=k, quality=Quality.OK),
        tier="public",
        detail={
            "until": until.isoformat(),
            "remaining_s": remaining,
            "night_opens_today": trading and night_session_opens(d, cal),
        },
    )


# ── 띠 ──────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RibbonInputs:
    """띠 입력 — 부르는 쪽(API readers)이 원장·보드·설정에서 만든다. 없는 값은 None."""

    turnover: Sourced[int] | None = None  # 확정(ok) 또는 장중 지수 기준(estimated)
    turnover_ratio: float | None = None  # 20일 평균 대비
    turnover_n_prior: int | None = None
    investors: InvestorTotals | None = None  # 시장 투자자 마지막 확정일
    newhigh: Sourced[int] | None = None
    newhigh_universe: int | None = None
    semi_skew: SkewResult | None = None
    cyc_def: SkewResult | None = None
    next_mpc: date | None = None
    nxt_excluded: bool = True  # D-P3-9 — KRX 구분만


def _skew_chip(key: str, label: str, res: SkewResult | None, pending_note: str) -> Chip:
    if res is None:
        return Chip(key, label, None, "login", note=pending_note)
    return Chip(
        key,
        label,
        res.sourced(),
        "login",
        note=res.reason,
        detail=dict(res.parts),
    )


def ribbon(inputs: RibbonInputs, now: datetime, cal: TradingCalendar) -> list[Chip]:
    """칩 목록(`CHIP_ORDER` 순서). 값을 만들지 못한 칩도 자리는 남긴다(사유와 함께)."""
    k = now.astimezone(KST)
    chips: dict[str, Chip] = {"session": session_chip(now, cal)}

    if inputs.next_mpc is None:
        chips["bok_mpc"] = Chip("bok_mpc", "금통위", None, "public", note="다음 일정 없음")
    else:
        dd = (inputs.next_mpc - k.date()).days
        chips["bok_mpc"] = Chip(
            "bok_mpc",
            f"금통위 D-{dd}",
            Sourced[int](value=dd, source=EVENTS_SOURCE, as_of=k, quality=Quality.OK),
            "public",
            detail={"date": inputs.next_mpc.isoformat()},
        )

    t = inputs.turnover
    tags: list[str] = []
    if t is not None:
        tags.append("장중(지수 기준)" if t.quality is Quality.ESTIMATED else "마감")
    if inputs.nxt_excluded:
        tags.append("NXT 미포함")
    chips["market_turnover"] = Chip(
        "market_turnover",
        "시장 거래대금",
        t,
        "login",
        tags=tuple(tags),
        note=None if t is not None else "값 없음",
        detail={"ratio_avg20": inputs.turnover_ratio, "n_prior": inputs.turnover_n_prior},
    )

    inv = inputs.investors
    for key, label, field_name in (
        ("foreign_spot", "외국인 현물", "foreign"),
        ("inst_spot", "기관 현물", "institution"),
    ):
        v = inv.by_investor.get(field_name) if inv is not None else None
        if inv is None or v is None:
            chips[key] = Chip(key, label, None, "login", tags=("마감",), note="값 없음")
            continue
        chips[key] = Chip(
            key,
            label,
            Sourced[int](
                value=v, source=inv.source, as_of=close_as_of(inv.date), quality=inv.quality
            ),
            "login",
            tags=("마감",),
        )

    chips["newhigh_count"] = Chip(
        "newhigh_count",
        "신고가 수",
        inputs.newhigh,
        "login",
        note=None if inputs.newhigh is not None else "보드 없음",
        detail={"universe": inputs.newhigh_universe},
    )
    chips["semi_skew"] = _skew_chip("semi_skew", "반도체 쏠림", inputs.semi_skew, "입력 없음")
    chips["cyc_def"] = _skew_chip(
        "cyc_def", "경기민감 대 방어", inputs.cyc_def, "준비 중 — 업종 코드 [확인 필요]"
    )
    for key, (label, tier, phase) in PENDING.items():
        chips[key] = Chip(key, label, None, tier, phase_pending=phase)
    return [chips[k] for k in CHIP_ORDER]

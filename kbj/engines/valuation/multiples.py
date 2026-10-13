"""배수 계산 — 회사 시가총액·trailing(PER·PBR·PSR·EV/EBITDA)·forward(docs/p4_design.md §4.8,
docs/metrics.md §11).

승격 원본(기능 참고 — 정의는 바꿨다, ADR 0016 §3): SD `valuation_calculator.get_ttm_metrics`:82,
`calculate_current_metrics`:283.

- **회사 시가총액** MC(t) = Σ_{회사의 상장 종류주식 c} 그날 시총_c(원장 krx > kis — 스냅의
  `mktcap`, 없으면 종가 × 상장주식수). 우선주가 정지·결측이면 그 종류를 빼고 estimated + 사유.
  보통주(회사의 대표 종목 — `codes` 의 첫 칸)가 빠지면 회사 시총을 만들지 않는다(None — 우선주만의
  합은 회사 시총의 작은 조각이라 배수가 몇 배 틀린다. §4.8 이 빼도 된다고 한 것은 우선주뿐).
  자기주식은 빼지 않는다 [확인 필요].
- **PER** = MC ÷ TTM 지배순익(R 의 정규화가 지배순익 칸이 없으면 당기순익으로 바꾸고 estimated — 그
  품질을 그대로 잇는다). **PBR** = MC ÷ 최근 분기말 지배자본. **PSR** = MC ÷ TTM 매출. **EV** = MC +
  순차입금(총차입금 − 현금및현금성자산 − 단기금융상품) + 비지배지분 — **오버행 가치는 더하지
  않는다**(CB 는 이미 사채 — SD 는 이중 계산). **EV/EBITDA** = EV ÷ TTM EBITDA(영업이익 + 감가상각비
  + 무형자산상각비 — 없으면 None, 영업이익으로 바꾸지 않는다).
- 분모 ≤ 0·업종 부적합·입력 없음은 값 None + `NaReason` 하나(0 으로 채우지 않는다). 여러 사유가
  겹치면 `NA_PRIORITY` 의 앞의 것(docs/metrics.md §11 끝 — p4_expect.yaml 과 같은 규칙):
  currency_mismatch > no_filings > spac > financial_sector > not_detail_tier > short_history >
  loss·capital_impairment·no_revenue > missing_input.
- forward: PER_fwd = MC ÷ NTM 지배순익 추정(KIS 가 지배순익을 주지 않으면 NTM EPS × 주식 수 — [확인
  필요]), PSR_fwd = MC ÷ NTM 매출, EV/EBITDA_fwd = EV ÷ FY1 EBITDA 추정. 추정 ≤ 0 → None. 컨센서스
  수집 대상이 아니면(코넥스·보통주 아님 — `kbj.engines.consensus.universe`) 그 사유. 추정이
  없으면(대상이지만 KIS 빈 응답·수집 꺼짐) 값 None·사유 없음 — '적용 불가'가 아니라 '자료 없음'이다.
  출처가 단일 증권사(KIS) 추정일 수 있어 forward 품질은 언제나 estimated(ADR 0015 [확인 필요 #29]).

I/O 를 하지 않는다(계약 ⑨). 금액은 원 정수, 배수는 float.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal

from kbj.core.fin_rows import NaReason, ValuationDay
from kbj.core.quality import Quality
from kbj.core.rows import Snap
from kbj.engines.consensus.forward import Ntm

__all__ = [
    "BAND_METRICS_T",
    "FORWARD_METRICS",
    "MULTIPLES",
    "NA_PRIORITY",
    "Balance",
    "Cell",
    "CompanyCap",
    "Facts",
    "Forward",
    "Metric",
    "Trailing",
    "Ttm",
    "blocked_reason",
    "cells_of",
    "company_mktcap",
    "first_reason",
    "forward",
    "trailing",
    "worst",
]

Metric = Literal["per", "pbr", "psr", "ev_ebitda"]
BAND_METRICS_T: Final[tuple[Metric, ...]] = ("per", "pbr", "psr", "ev_ebitda")
FORWARD_METRICS: Final[tuple[str, ...]] = ("per_fwd", "psr_fwd", "ev_ebitda_fwd")
MULTIPLES: Final[tuple[str, ...]] = (*BAND_METRICS_T, *FORWARD_METRICS)
KRW: Final = "KRW"

# 한 칸에 사유는 하나 — 겹치면 앞의 것(docs/metrics.md §11, p4_expect.yaml). 컨센서스 사유
# (konex_no_consensus·not_in_consensus_universe)는 forward 칸에만 쓰고 이 순위 밖이다.
NA_PRIORITY: Final[tuple[NaReason, ...]] = (
    NaReason.CURRENCY_MISMATCH,
    NaReason.NO_FILINGS,
    NaReason.SPAC,
    NaReason.FINANCIAL_SECTOR,
    NaReason.NOT_DETAIL_TIER,
    NaReason.SHORT_HISTORY,
    NaReason.LOSS,
    NaReason.CAPITAL_IMPAIRMENT,
    NaReason.NO_REVENUE,
    NaReason.MISSING_INPUT,
)
_RANK_OF: Final = {r: i for i, r in enumerate(NA_PRIORITY)}
_Q_RANK: Final = {Quality.OK: 0, Quality.STALE: 1, Quality.ESTIMATED: 2, Quality.INVALID: 3}


def worst(*qs: Quality) -> Quality:
    """가장 나쁜 품질(ok < stale < estimated < invalid)."""
    return max(qs, key=lambda q: _Q_RANK[q], default=Quality.OK)


def first_reason(reasons: Iterable[NaReason | None]) -> NaReason | None:
    """사유들 중 `NA_PRIORITY` 의 가장 앞의 것(None 은 무시). 순위 밖 사유는 순위 안 사유보다 뒤."""
    got = [r for r in reasons if r is not None]
    if not got:
        return None
    return min(got, key=lambda r: _RANK_OF.get(r, len(NA_PRIORITY)))


# ── 입력 ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CompanyCap:
    """회사 시가총액 하루(원). `codes` = 더한 종류주식, `missing` = 빠진 종류주식(정지·결측)."""

    day: date
    value: int
    source: str
    quality: Quality
    codes: tuple[str, ...]
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class Ttm:
    """PIT TTM(같은 기준 연속 4분기 합 — 원). 칸 하나라도 None 인 분기가 있으면 그 칸 None."""

    ref: str  # 마지막 분기 '2026Q2:CFS'
    revenue: int | None
    op: int | None
    ni: int | None
    ni_owner: int | None
    ebitda: int | None
    eps: float | None  # Σ 분기 기본주당이익(원/주)
    tier: Literal["main", "full"]  # 하나라도 주요계정 층이면 main
    currency: str
    quality: Quality
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Balance:
    """PIT 최근 분기말 상태표(원)."""

    ref: str
    period_end: date
    equity_owner: int | None
    net_debt: int | None
    minority: int | None
    tier: Literal["main", "full"]
    currency: str
    quality: Quality
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Facts:
    """종목·회사 사실 — 사유 판정에 쓴다.

    `kind`: 유니버스 종류(common·pref·spac·reit — 모르면 None), `is_financial`: KSIC 금융업
    (모르면 None → 금융업 아님으로 본다), `consensus_na`: 컨센서스 수집 대상이 아닌 사유(보통주
    회사 기준), `listed_on`: 상장일(신규상장 판정)."""

    kind: str | None = None
    is_financial: bool | None = None
    consensus_na: NaReason | None = None
    listed_on: date | None = None


@dataclass(frozen=True)
class Cell:
    """배수 한 칸 — 값 또는 적용 불가 사유(둘 다 None 이면 '자료 없음')."""

    value: float | None
    na_reason: NaReason | None = None

    def __post_init__(self) -> None:
        if self.value is not None and self.na_reason is not None:
            raise ValueError("값이 있는 칸에 사유를 달지 않는다")

    @property
    def applicable(self) -> bool:
        return self.na_reason is None


@dataclass(frozen=True)
class Trailing:
    per: Cell
    pbr: Cell
    psr: Cell
    ev_ebitda: Cell
    ev: int | None
    blocked: NaReason | None  # 모든 칸을 막는 사유(currency_mismatch·no_filings)
    quality: Quality
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Forward:
    per_fwd: Cell
    psr_fwd: Cell
    ev_ebitda_fwd: Cell
    used: bool  # 추정값을 하나라도 썼나(출처에 KIS)
    quality: Quality | None


# ── 회사 시가총액 ──────────────────────────────────────────────────────────────────────


def _cap_of(s: Snap) -> int | None:
    if s.quality is Quality.INVALID:
        return None
    if s.mktcap is not None:
        return s.mktcap
    if s.close is not None and s.shares is not None:
        return round(s.close * s.shares)
    return None


def company_mktcap(
    snaps_by_code: Mapping[str, Snap], codes: Sequence[str], day: date
) -> CompanyCap | None:
    """회사의 종류주식 `codes`(보통주 먼저) 그날 시총 합. 보통주(첫 칸)가 없으면 None.

    빠진 우선주가 있으면 estimated + `missing`(그 종류 빼고 더한 값 — 지어낸 가격을 쓰지 않는다).
    보통주가 빠지면 우선주만으로 회사 시총을 세지 않는다(None → 부르는 쪽이 missing_input).
    """
    used: list[str] = []
    missing: list[str] = []
    total = 0
    sources: list[str] = []
    qualities: list[Quality] = []
    for c in codes:
        s = snaps_by_code.get(c)
        v = None if s is None or s.date != day else _cap_of(s)
        if s is None or v is None:
            missing.append(c)
            continue
        used.append(c)
        total += v
        if s.source.lower() not in sources:
            sources.append(s.source.lower())
        qualities.append(s.quality)
    if not used or (codes and codes[0] in missing):
        return None
    q = worst(*qualities)
    if missing:
        q = worst(q, Quality.ESTIMATED)
    return CompanyCap(day, total, "+".join(sources), q, tuple(used), tuple(missing))


# ── trailing ─────────────────────────────────────────────────────────────────────────


def blocked_reason(ttm: Ttm | None, balance: Balance | None) -> NaReason | None:
    """모든 trailing 칸을 막는 사유 — 알려진 재무가 없음(no_filings)·재무 통화 ≠ KRW."""
    src: Ttm | Balance | None = balance if balance is not None else ttm
    if src is None:
        return NaReason.NO_FILINGS
    if src.currency != KRW:
        return NaReason.CURRENCY_MISMATCH
    return None


def trailing(
    mc: int,
    ttm: Ttm | None,
    balance: Balance | None,
    facts: Facts,
    *,
    ttm_gap: NaReason = NaReason.MISSING_INPUT,
) -> Trailing:
    """trailing 네 칸 + EV. `ttm_gap` = TTM 이 없을 때의 사유(신규상장 short_history, 그 밖
    missing_input — `pit.PitView.ttm_gap`)."""
    notes: list[str] = []
    blocked = blocked_reason(ttm, balance)
    spac = facts.kind == "spac"
    qs: list[Quality] = []
    if ttm is not None:
        qs.append(ttm.quality)
        notes.extend(ttm.notes)
    if balance is not None:
        qs.append(balance.quality)
        notes.extend(n for n in balance.notes if n not in notes)

    def flow(col: Literal["ni_owner", "revenue"], nonpos: NaReason) -> Cell:
        if blocked is not None:
            return Cell(None, blocked)
        if spac:
            return Cell(None, NaReason.SPAC)
        if ttm is None:
            return Cell(None, ttm_gap)
        den = getattr(ttm, col)
        if den is None:
            return Cell(None, NaReason.MISSING_INPUT)
        if den <= 0:
            return Cell(None, nonpos)
        return Cell(mc / den)

    per = flow("ni_owner", NaReason.LOSS)
    psr = flow("revenue", NaReason.NO_REVENUE)

    if blocked is not None or balance is None:
        pbr = Cell(None, blocked or NaReason.NO_FILINGS)
    elif balance.equity_owner is None:
        pbr = Cell(None, NaReason.MISSING_INPUT)
    elif balance.equity_owner <= 0:
        pbr = Cell(None, NaReason.CAPITAL_IMPAIRMENT)
    else:
        pbr = Cell(mc / balance.equity_owner)

    ev: int | None = None
    if blocked is None and balance is not None and balance.net_debt is not None:
        minority = balance.minority
        if minority is None:
            minority = 0
            notes.append("비지배지분 줄 없음 — 0 으로 보고 EV 를 셌다")
            qs.append(Quality.ESTIMATED)
        ev = mc + balance.net_debt + minority

    tier = ttm.tier if ttm is not None else (balance.tier if balance is not None else "full")
    if blocked is not None:
        ev_ebitda = Cell(None, blocked)
    elif spac:
        ev_ebitda = Cell(None, NaReason.SPAC)
    elif facts.is_financial:
        ev_ebitda = Cell(None, NaReason.FINANCIAL_SECTOR)
    elif tier == "main":
        ev_ebitda = Cell(None, NaReason.NOT_DETAIL_TIER)
    elif ttm is None:
        ev_ebitda = Cell(None, ttm_gap)
    elif ttm.ebitda is not None and ttm.ebitda <= 0:
        ev_ebitda = Cell(None, NaReason.LOSS)
    elif ttm.ebitda is None or ev is None:
        ev_ebitda = Cell(None, NaReason.MISSING_INPUT)
    else:
        ev_ebitda = Cell(ev / ttm.ebitda)
    return Trailing(per, pbr, psr, ev_ebitda, ev, blocked, worst(*qs), tuple(notes))


# ── forward ──────────────────────────────────────────────────────────────────────────


def forward(
    mc: int,
    ev: int | None,
    ntm: Ntm | None,
    facts: Facts,
    *,
    shares: int | None,
    ev_blocked: NaReason | None = None,
) -> Forward:
    """선행 세 칸. `shares` = NTM EPS → 순이익 환산 주식 수(보통주 발행 − 자기주식, DART — 없으면
    호출자가 KRX 상장주식수를 estimated 로 넘긴다). `ev_blocked` = EV 를 막은 사유(통화·재무
    없음)."""
    if facts.consensus_na is not None:
        c = Cell(None, facts.consensus_na)
        return Forward(c, c, c, False, None)
    if ntm is None:
        empty = Cell(None)
        return Forward(empty, empty, empty, False, None)
    used = False
    ni: float | None = ntm.ni_owner
    if ni is None and ntm.eps is not None and shares is not None and shares > 0:
        ni = ntm.eps * shares
    if ni is None:
        per = Cell(None)
    elif ni <= 0:
        per, used = Cell(None, NaReason.LOSS), True
    else:
        per, used = Cell(mc / ni), True
    if ntm.revenue is None:
        psr = Cell(None)
    elif ntm.revenue <= 0:
        psr, used = Cell(None, NaReason.NO_REVENUE), True
    else:
        psr, used = Cell(mc / ntm.revenue), True
    fy1_ebitda = ntm.fy1.ebitda
    # 순위(NA_PRIORITY)는 trailing 과 같다 — EV 를 막은 사유(currency_mismatch·no_filings)가
    # financial_sector 보다 앞
    if ev_blocked is not None:
        eve = Cell(None, ev_blocked)
    elif facts.is_financial:
        eve = Cell(None, NaReason.FINANCIAL_SECTOR)
    elif fy1_ebitda is None:
        eve = Cell(None)
    elif fy1_ebitda <= 0:
        eve, used = Cell(None, NaReason.LOSS), True
    elif ev is None:
        eve = Cell(None, NaReason.MISSING_INPUT)
    else:
        eve, used = Cell(ev / fy1_ebitda), True
    return Forward(per, psr, eve, used, worst(ntm.quality, Quality.ESTIMATED) if used else None)


# ── 저장 행 → 칸(API·밴드가 같이 쓴다) ─────────────────────────────────────────────────


def cells_of(day: ValuationDay) -> dict[str, Cell]:
    """`prv_fin.valuation_daily` 한 행 → 지표마다 칸(값 또는 `flags['na']` 의 사유)."""
    na_raw: Any = day.flags.get("na", {})
    na: Mapping[str, Any] = na_raw if isinstance(na_raw, Mapping) else {}
    out: dict[str, Cell] = {}
    for m in MULTIPLES:
        v = getattr(day, m)
        r = na.get(m)
        out[m] = Cell(
            None if v is None else float(v),
            None if v is not None or r is None else NaReason(str(r)),
        )
    return out

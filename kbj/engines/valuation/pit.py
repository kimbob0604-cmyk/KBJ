"""시점 일관(PIT) 재무·컨센서스와 하루 배수 행 — docs/p4_design.md §4.8·D-P4-11, ADR 0016.

날짜 t 의 TTM·자본은 **t 까지 공시된**(정기보고서 접수일 `rcept_dt` ≤ t) 분기만 쓴다. 잠정실적은
쓰지 않는다. 미래 분기를 지우거나 바꿔도 t 이전의 값이 바뀌지
않는다(`tests/property/test_valuation_pit.py`). SD `valuation_calculator.calculate_per_band`:122 는
분기말 날짜에 그 분기로 끝나는 TTM 을 써 아직 공시되지 않은 실적을 봤다(미래 참조) — 바꿨다(ADR 0016
§3).

- `PitBook(quarters, share_counts)`: 회사 하나의 분기 행·주식총수를 접수일 순으로 두고 `at(t)` 가
  그날의 `PitView` 를 준다. 같은 '알려진 상태'(마지막 접수일)마다 한 번만 계산한다(5년 일별에서
  빠르게).
- TTM 창은 재무 엔진 `kbj.engines.financials.period.ttm_window`(R — 정본 하나, 두 벌 금지)를 쓴다:
  t 까지 알려진 마지막 분기의 기준(CFS 우선)으로 연속 4분기. 칸 합은 하나라도 None 이면 None.
- TTM 이 없을 때 사유(`PitView.ttm_gap`): 상장 뒤 알려진 정기보고서 분기가 4개 미만이면 신규상장
  `short_history`, 그 밖(반기·연간 보고만 하는 리츠·코넥스 — 분기 단독이 derived)은 `missing_input`
  (docs/metrics.md §11 끝).
- `digest`: 그날까지 알려진 재무(분기·주식총수)의 지문. 저장한 일별 행의 `flags['fin_digest']` 와
  지금 저장소로 다시 만든 지문이 다르면 과거 재무가 바뀐 것(백필·정정) — 처리기가 5년을 다시
  계산한다.
- `ConsensusBook(rows)`: 스냅 날짜별로 두고 `ntm(t)` = t 이하 마지막 스냅의 NTM(컨센서스 엔진 K 의
  `ntm` — 정본 하나).
- `value_day(...)`: 그날 회사 시총 + PIT 재무 + NTM → `ValuationDay`(flags: na·out_of_cap·notes·
  fin_digest). `daily_series(...)`: 여러 날.

I/O 를 하지 않는다(계약 ⑨).
"""

from __future__ import annotations

import bisect
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from kbj.core.fin_rows import ConsensusRow, NaReason, QuarterRow, ShareCount, ValuationDay
from kbj.core.quality import Quality
from kbj.engines.consensus.forward import Ntm, ntm
from kbj.engines.financials.normalize import rcept_date
from kbj.engines.financials.period import ttm_window
from kbj.engines.valuation.multiples import (
    BAND_METRICS_T,
    Balance,
    CompanyCap,
    Facts,
    Metric,
    Ttm,
    forward,
    trailing,
    worst,
)

__all__ = [
    "MIN_TTM_REPORTS",
    "SHARE_DRIFT_MAX",
    "ConsensusBook",
    "DayInput",
    "PitBook",
    "PitView",
    "daily_series",
    "per_share",
    "share_basis",
    "value_day",
]

# 보고된 주당 값·DART 주식 수가 그날 주식 수와 이만큼(비율) 넘게 어긋나면 분할·병합으로 보고
# 다시 센다
SHARE_DRIFT_MAX: Final = 0.3
MIN_TTM_REPORTS: Final = 4  # 상장 뒤 알려진 분기가 이보다 적으면 TTM 없음 = 신규상장(short_history)
_FLOW: Final = ("revenue", "op", "ni", "ni_owner", "ebitda")


def _seq(year: int, q: int) -> int:
    return year * 4 + (q - 1)


def _ref(r: QuarterRow) -> str:
    return f"{r.period}:{r.fs_div}"


def _sum(rows: Sequence[QuarterRow], col: str) -> int | None:
    vals: list[int | None] = [getattr(r, col) for r in rows]
    if any(v is None for v in vals):
        return None
    return sum(v for v in vals if v is not None)


def _ttm(window: Sequence[QuarterRow]) -> Ttm:
    eps_vals = [r.eps_basic for r in window]
    eps = None if any(v is None for v in eps_vals) else sum(v for v in eps_vals if v is not None)
    notes: list[str] = []
    for r in window:
        for n in r.notes:
            if n not in notes:
                notes.append(n)
    last = window[-1]
    return Ttm(
        ref=_ref(last),
        revenue=_sum(window, "revenue"),
        op=_sum(window, "op"),
        ni=_sum(window, "ni"),
        ni_owner=_sum(window, "ni_owner"),
        ebitda=_sum(window, "ebitda"),
        eps=eps,
        tier="main" if any(r.tier == "main" for r in window) else "full",
        currency=last.currency,
        quality=worst(*(r.quality for r in window)),
        notes=tuple(notes),
    )


def _balance(r: QuarterRow) -> Balance:
    return Balance(
        ref=_ref(r),
        period_end=r.period_end,
        equity_owner=r.equity_owner,
        net_debt=r.net_debt,
        minority=r.minority,
        tier=r.tier,
        currency=r.currency,
        quality=r.quality,
        notes=tuple(r.notes),
    )


def _count_day(c: ShareCount) -> date | None:
    return c.rcept_dt if c.rcept_dt is not None else rcept_date(c.rcept_no)


def _digest(quarters: Sequence[QuarterRow], counts: Sequence[ShareCount]) -> str:
    """값에 쓰는 칸만으로 만든 지문(저장소 종류·dict 순서와 무관하게 같다)."""
    q = sorted(
        (
            r.fiscal_year,
            r.fiscal_q,
            r.fs_div,
            r.tier,
            r.currency,
            str(r.rcept_dt),
            *(getattr(r, c) for c in _FLOW),
            r.eps_basic,
            r.equity_owner,
            r.net_debt,
            r.minority,
            str(r.quality),
        )
        for r in quarters
    )
    s = sorted((str(c.period_end), c.rcept_no, c.common_issued, c.treasury_common) for c in counts)
    raw = json.dumps([q, s], ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class PitView:
    """그날 알려진 재무. `ttm`·`balance` 둘 다 None 이면 알려진 정기보고서가 없다(no_filings)."""

    known_through: date | None  # 마지막으로 알려진 접수일
    ttm: Ttm | None
    balance: Balance | None
    shares: ShareCount | None  # 알려진 마지막 주식총수(DART)
    first_seen: tuple[date, ...]  # 알려진 (연도·분기)마다 처음 알려진 날(오름차순)
    digest: str

    def reports_since(self, d: date) -> int:
        """d 이후(포함) 처음 알려진 분기 수."""
        return len(self.first_seen) - bisect.bisect_left(self.first_seen, d)

    def ttm_gap(self, listed_on: date | None) -> NaReason:
        """TTM 이 없을 때의 사유 — 상장 뒤 알려진 분기 < 4 면 short_history, 그 밖 missing_input."""
        if listed_on is not None and self.reports_since(listed_on) < MIN_TTM_REPORTS:
            return NaReason.SHORT_HISTORY
        return NaReason.MISSING_INPUT


class PitBook:
    """회사 하나의 PIT 재무 — `at(t)` 는 접수일 ≤ t 인 행만으로 만든 `PitView`."""

    def __init__(
        self, quarters: Iterable[QuarterRow], share_counts: Iterable[ShareCount] = ()
    ) -> None:
        self._q = sorted(
            (r for r in quarters if r.rcept_dt is not None),
            key=lambda r: (r.rcept_dt, _seq(r.fiscal_year, r.fiscal_q), r.fs_div),
        )
        self._c = sorted(
            ((d, c) for c in share_counts if (d := _count_day(c)) is not None),
            key=lambda dc: (dc[0], dc[1].period_end, dc[1].rcept_no),
        )
        days = {r.rcept_dt for r in self._q if r.rcept_dt is not None} | {d for d, _ in self._c}
        self._days: list[date] = sorted(days)
        self._cache: dict[int, PitView] = {}

    @property
    def empty(self) -> bool:
        return not self._days

    def at(self, t: date) -> PitView:
        i = bisect.bisect_right(self._days, t)
        got = self._cache.get(i)
        if got is None:
            got = self._view(self._days[i - 1] if i else None)
            self._cache[i] = got
        return got

    def _view(self, through: date | None) -> PitView:
        if through is None:
            return PitView(None, None, None, None, (), _digest((), ()))
        known = [r for r in self._q if r.rcept_dt is not None and r.rcept_dt <= through]
        counts = [c for d, c in self._c if d <= through]
        first: dict[tuple[int, int], date] = {}
        for r in known:
            k = (r.fiscal_year, r.fiscal_q)
            if r.rcept_dt is not None and (k not in first or r.rcept_dt < first[k]):
                first[k] = r.rcept_dt
        balance: Balance | None = None
        ttm: Ttm | None = None
        if known:
            last = max(known, key=lambda r: (_seq(r.fiscal_year, r.fiscal_q), r.fs_div == "CFS"))
            balance = _balance(last)
            win = ttm_window(known, through)
            ttm = _ttm(win) if win else None
        share = max(counts, key=lambda c: (c.period_end, c.rcept_no)) if counts else None
        return PitView(
            through, ttm, balance, share, tuple(sorted(first.values())), _digest(known, counts)
        )


class ConsensusBook:
    """종목 하나(보통주)의 추정실적 스냅 — `ntm(t)` 는 t 이하 마지막 스냅의 NTM."""

    def __init__(self, rows: Iterable[ConsensusRow]) -> None:
        by: dict[date, list[ConsensusRow]] = {}
        for r in rows:
            by.setdefault(r.snap_date, []).append(r)
        self._days = sorted(by)
        self._by = by
        self._cache: dict[tuple[int, date], Ntm | None] = {}

    def ntm(self, t: date) -> Ntm | None:
        i = bisect.bisect_right(self._days, t)
        if i == 0:
            return None
        key = (i, t)
        if key not in self._cache:
            self._cache[key] = ntm(self._by[self._days[i - 1]], t)
        return self._cache[key]


def share_basis(view: PitView, krx_common_shares: int | None) -> tuple[int | None, str | None]:
    """주당 값의 분모 — **그날의 주식 수**(§4.8 '분할·병합: 주당 값은 그날의 주식 수로').

    DART 주식총수(보통주 발행 − 자기주식, PIT)가 그날 KRX 보통주 상장주식수와 맞으면(차이 ≤
    `SHARE_DRIFT_MAX`) 그것, 아니면(분할·병합·큰 증자 뒤 — DART 는 분기말 기준이라 늦다) 그날
    KRX 보통주 상장주식수(자기주식 포함 — estimated). (주식 수, 근거 'DART'·'KRX'·None)."""
    dart = view.shares
    if dart is not None:
        n = dart.common_outstanding
        issued = dart.common_issued
        if n is not None and n > 0:
            fresh = (
                krx_common_shares is None
                or issued is None
                or issued <= 0
                or abs(krx_common_shares / issued - 1.0) <= SHARE_DRIFT_MAX
            )
            if fresh:
                return n, "DART"
    if krx_common_shares is not None and krx_common_shares > 0:
        return krx_common_shares, "KRX"
    return None, None


def per_share(
    total: float | None, reported: float | None, shares: int | None
) -> tuple[float | None, bool]:
    """주당 값 — 보고된 값(`reported` — DART 기본주당이익 합·KIS 추정 EPS)이 그날 주식 수와 맞으면
    그것, 아니면 총액 ÷ 그날 주식 수. (값, 다시 셌나).

    '맞다' = 보고 값이 함의하는 주식 수(총액 ÷ 보고 값)가 그날 주식 수와 `SHARE_DRIFT_MAX` 안. 분할
    직후에는 분할 전 주당 값이 남아 있어(분기 EPS·추정 EPS) 그대로 쓰면 적정가가 분할 비율만큼
    틀린다(§8.4 #17). 보고 값이 없으면 총액 ÷ 주식 수, 둘 다 없으면 None."""
    if reported is not None and reported != 0 and total is not None and shares:
        implied = total / reported
        if implied > 0 and abs(implied / shares - 1.0) <= SHARE_DRIFT_MAX:
            return reported, False
        return total / shares, True
    if reported is not None:
        return reported, False
    if total is not None and shares:
        return total / shares, True
    return None, False


@dataclass(frozen=True)
class DayInput:
    """하루 시장 입력 — 회사 시총(없으면 None — 보통주 짝을 못 찾은 우선주 등), 그 종목 종가,
    보통주 상장주식수(KRX — 주당 환산 대체)."""

    day: date
    cap: CompanyCap | None
    price: float | None
    krx_common_shares: int | None = None


def _out_of_cap(
    values: Mapping[str, float | None], caps: Mapping[Metric, tuple[float, float]]
) -> list[str]:
    out: list[str] = []
    for m in BAND_METRICS_T:
        v = values.get(m)
        lo, hi = caps[m]
        if v is not None and not lo < v < hi:
            out.append(m)
    return out


def value_day(
    code: str,
    inp: DayInput,
    view: PitView,
    facts: Facts,
    *,
    caps: Mapping[Metric, tuple[float, float]],
    estimate: Ntm | None = None,
    notes: Sequence[str] = (),
) -> ValuationDay:
    """하루 PIT 배수 행. 회사 시총이 없으면 배수 칸은 모두 None + missing_input(사유는 notes)."""
    flags_na: dict[str, str] = {}
    all_notes: list[str] = list(notes)
    vals: dict[str, float | None] = {}
    ev: int | None = None
    used_fwd = False
    q = Quality.OK
    source = "krx"
    if inp.cap is None:
        for m in (*BAND_METRICS_T, "per_fwd", "psr_fwd", "ev_ebitda_fwd"):
            vals[m] = None
            flags_na[m] = NaReason.MISSING_INPUT.value
        all_notes.append("회사 시가총액을 만들 수 없다(종류주식 짝·시세 없음)")
        q = Quality.ESTIMATED
    else:
        cap = inp.cap
        source = cap.source or "krx"
        if cap.missing:
            all_notes.append("시총에서 뺀 종류주식(정지·결측): " + ",".join(cap.missing))
        tr = trailing(
            cap.value, view.ttm, view.balance, facts, ttm_gap=view.ttm_gap(facts.listed_on)
        )
        shares, basis = share_basis(view, inp.krx_common_shares)
        fw = forward(cap.value, tr.ev, estimate, facts, shares=shares, ev_blocked=tr.blocked)
        ev = tr.ev
        used_fwd = fw.used
        for m, c in (
            ("per", tr.per),
            ("pbr", tr.pbr),
            ("psr", tr.psr),
            ("ev_ebitda", tr.ev_ebitda),
            ("per_fwd", fw.per_fwd),
            ("psr_fwd", fw.psr_fwd),
            ("ev_ebitda_fwd", fw.ev_ebitda_fwd),
        ):
            vals[m] = c.value
            if c.na_reason is not None:
                flags_na[m] = c.na_reason.value
        for n in tr.notes:
            if n not in all_notes:
                all_notes.append(n)
        if used_fwd and basis == "KRX" and estimate is not None and estimate.ni_owner is None:
            all_notes.append("선행 순이익 = NTM EPS × KRX 보통주 상장주식수(자기주식 포함 — 추정)")
        q = worst(cap.quality, tr.quality)
    flags: dict[str, Any] = {"fin_digest": view.digest}
    if flags_na:
        flags["na"] = flags_na
    ooc = _out_of_cap(vals, caps)
    if ooc:
        flags["out_of_cap"] = ooc
    if all_notes:
        flags["notes"] = all_notes
    if used_fwd:
        flags["fwd_quality"] = Quality.ESTIMATED.value
    ref = view.ttm.ref if view.ttm is not None else (view.balance.ref if view.balance else None)
    return ValuationDay(
        code=code,
        trade_date=inp.day,
        mktcap_company=None if inp.cap is None else inp.cap.value,
        price=inp.price,
        ttm_ref=ref,
        per=vals.get("per"),
        pbr=vals.get("pbr"),
        psr=vals.get("psr"),
        ev=ev,
        ev_ebitda=vals.get("ev_ebitda"),
        per_fwd=vals.get("per_fwd"),
        psr_fwd=vals.get("psr_fwd"),
        ev_ebitda_fwd=vals.get("ev_ebitda_fwd"),
        flags=flags,
        source=f"{source}+DART" + ("+KIS" if used_fwd else ""),
        quality=q,
    )


def daily_series(
    code: str,
    days: Iterable[DayInput],
    book: PitBook,
    facts: Facts,
    *,
    caps: Mapping[Metric, tuple[float, float]],
    consensus: ConsensusBook | None = None,
) -> list[ValuationDay]:
    """여러 날의 PIT 배수 행(입력 날짜 순). 날마다 그날까지 공시된 재무만 쓴다."""
    out: list[ValuationDay] = []
    for inp in days:
        view = book.at(inp.day)
        est = consensus.ntm(inp.day) if consensus is not None else None
        out.append(value_day(code, inp, view, facts, caps=caps, estimate=est))
    return out

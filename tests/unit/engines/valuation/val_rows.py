"""밸류에이션 엔진 시험 공용 — 작은 합성 행(분기·스냅·밴드·일별) 만들기. 실데이터 아님.

금액은 원 정수(작은 수로도 배수는 같다). 결산 12월, 분기 기간은 재무 엔진 `fiscal_quarter` 로.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from kbj.core.fin_rows import BandRow, ConsensusRow, NaReason, QuarterRow, ShareCount, ValuationDay
from kbj.core.quality import Quality
from kbj.core.rows import Snap
from kbj.engines.financials.period import fiscal_quarter
from kbj.engines.valuation.multiples import Metric

CORP = "99000001"
CODE = "990010"
PREF = "990015"
# 거름값(config/fin.yaml valuation.caps 와 같은 값 — 시험이 설정 파일에 기대지 않게)
CAPS: dict[Metric, tuple[float, float]] = {
    "per": (0.0, 200.0),
    "pbr": (0.0, 50.0),
    "psr": (0.0, 100.0),
    "ev_ebitda": (0.0, 100.0),
}


def quarter(
    year: int,
    q: int,
    *,
    rcept: date | None,
    fs: Literal["CFS", "OFS"] = "CFS",
    tier: Literal["main", "full"] = "full",
    revenue: int | None = 1_000,
    op: int | None = 100,
    ni: int | None = 80,
    ni_owner: int | None = 75,
    d_and_a: int | None = 20,
    equity_owner: int | None = 2_000,
    net_debt: int | None = 300,
    minority: int | None = 50,
    eps: float | None = 7.5,
    currency: str = "KRW",
    quality: Quality = Quality.OK,
    corp: str = CORP,
    notes: tuple[str, ...] = (),
) -> QuarterRow:
    p = fiscal_quarter(year, q, 12)
    ebitda = None if op is None or d_and_a is None else op + d_and_a
    return QuarterRow(
        corp_code=corp,
        fiscal_year=year,
        fiscal_q=q,
        fs_div=fs,
        period_start=p.start,
        period_end=p.end,
        rcept_dt=rcept,
        tier=tier,
        revenue=revenue,
        op=op,
        ni=ni,
        ni_owner=ni_owner,
        d_and_a=d_and_a,
        ebitda=ebitda,
        total_equity=None if equity_owner is None else equity_owner + (minority or 0),
        equity_owner=equity_owner,
        net_debt=net_debt,
        minority=minority,
        eps_basic=eps,
        currency=currency,
        quality=quality,
        notes=notes,
    )


def year_of_quarters(
    year: int, *, filed: dict[int, date] | None = None, **kw: Any
) -> list[QuarterRow]:
    """한 해 네 분기 — 접수일 기본: 1Q 5/15, 2Q 8/14, 3Q 11/14, 4Q 다음 해 3/20."""
    days = filed or {
        1: date(year, 5, 15),
        2: date(year, 8, 14),
        3: date(year, 11, 14),
        4: date(year + 1, 3, 20),
    }
    return [quarter(year, q, rcept=days[q], **kw) for q in (1, 2, 3, 4)]


def share_count(
    period_end: date, *, rcept: date, common: int, treasury: int = 0, corp: str = CORP
) -> ShareCount:
    return ShareCount(
        corp_code=corp,
        period_end=period_end,
        rcept_no=f"{rcept:%Y%m%d}000001",
        common_issued=common,
        pref_issued=0,
        treasury_common=treasury,
        treasury_pref=0,
        rcept_dt=rcept,
    )


def snap(
    code: str,
    day: date,
    close: float | None,
    shares: int | None,
    *,
    mktcap: int | None = None,
    source: str = "krx",
    quality: Quality = Quality.OK,
    kind: str = "common",
) -> Snap:
    cap = (
        mktcap
        if mktcap is not None
        else (None if close is None or shares is None else round(close * shares))
    )
    return Snap(
        code,
        day,
        "모의",
        "KOSPI",
        kind,
        close,
        0.0,
        100,
        1000,
        False,
        cap,
        shares,
        (),
        source,
        "KRX",
        quality,
    )


def vday(
    day: date,
    *,
    code: str = CODE,
    per: float | None = None,
    pbr: float | None = None,
    psr: float | None = None,
    ev_ebitda: float | None = None,
    na: dict[str, NaReason] | None = None,
    price: float | None = 100.0,
    quality: Quality = Quality.OK,
) -> ValuationDay:
    flags: dict[str, Any] = {}
    if na:
        flags["na"] = {k: v.value for k, v in na.items()}
    return ValuationDay(
        code=code,
        trade_date=day,
        mktcap_company=1_000_000,
        price=price,
        ttm_ref="2025Q4:CFS",
        per=per,
        pbr=pbr,
        psr=psr,
        ev_ebitda=ev_ebitda,
        flags=flags,
        quality=quality,
    )


def band_row(
    metric: str,
    *,
    p: tuple[float, float, float, float, float] | None = (8.0, 10.0, 12.0, 14.0, 16.0),
    na: NaReason | None = None,
    as_of: date = date(2026, 11, 13),
    code: str = CODE,
    quality: Quality = Quality.OK,
) -> BandRow:
    if p is None:
        return BandRow(
            code,
            as_of,
            metric,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            10,
            0,
            None,
            None,
            None,
            na_reason=na or NaReason.SHORT_HISTORY,
        )
    return BandRow(
        code,
        as_of,
        metric,
        *p,
        min=p[0],
        max=p[-1],
        n_days=600,
        n_excluded=0,
        current=p[2],
        current_rank_pct=50.0,
        window_from=date(2021, 10, 1),
        quality=quality,
    )


def estimate(
    code: str,
    snap_date: date,
    fiscal_ym: str,
    *,
    eps: float | None,
    ni_owner: int | None = None,
    revenue: int | None = 4_400,
    ebitda: int | None = 500,
    is_estimate: bool = True,
) -> ConsensusRow:
    return ConsensusRow(
        code, snap_date, fiscal_ym, is_estimate, revenue, 450, ni_owner, ni_owner, eps, ebitda
    )

"""재무 readers — 분기·연간 재무·비율, dart-report 블록·엑셀, 부문 매출(docs/p4_design.md §4.5·§4.6·
§5.3, 공개 원천 DART).

- 분기 행은 `fin.quarterly`·`fin.backfill` 이 쓴 `pub_fin.quarterly`(분기 단독 — dart-report
  `to_quarterly` 하나, D-P4-5)를 읽는다. (연도, 분기)마다 CFS 가 있으면 CFS, 없으면 OFS(재무 엔진
  `ratios.series` — 비율 행과 같은 고르기). 연간(basis=a)은 **같은 기준(fs_div)** 회계연도 네 분기
  단독의 합이고(CFS 네 분기가 다 있으면 CFS, 아니면 OFS 네 분기 — 기준을 섞지 않는다) 하나라도
  없으면 그 칸은 None(지어내지 않는다). 상태표 칸은 싣지 않는다(비율 표에 있다).
- YoY 는 같은 기준의 전년 같은 기간과만 비교한다(재무 엔진 비율 행과 같은 규칙 — docs/metrics.md
  §9). 전년 0·없음 → None, 부호가 바뀌면 None + 그 행 notes('영업이익 흑자전환'·'적자전환').
- 정기보고서가 하나도 없는 회사(재무 수집이 돈 적이 있고 최근 400일 정기보고서 공시도 없다)는 200 +
  `na_reason=no_filings`, 수집 전이면 404 no_data(§8.4 #9).
- 외국 통화 재무는 환산하지 않는다 — `currency` 그대로 + notes(§8.4 #19).
- 리포트·부문은 `reports.dart_excel`(21:00 — 관심종목 ∪ 공개 데모)이 만든 것을 읽는다. 우선주는
  보통주 리포트(우선주 코드로는 만들지 않는다 — 리포트 작업 규칙).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal, cast

from kbj.core.fin_rows import FiscalPeriod, NaReason, QuarterRow, RatioRow, ReportRow
from kbj.core.quality import Quality
from kbj.engines.financials.ratios import series as quarter_series
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.fin import (
    Financials,
    FinChecks,
    FinRow,
    RatioView,
    ReportBlocks,
    ReportView,
    Segments,
    SegmentView,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, worst
from kbj.services.api.readers.stock import FIN_ETA, Lookup, StockRef, company_na, dart_as_of

__all__ = [
    "REPORT_ETA",
    "SEGMENTS_ETA",
    "XLSX_MEDIA_TYPE",
    "XlsxFile",
    "stock_financials",
    "stock_report",
    "stock_report_xlsx",
    "stock_segments",
]

REPORT_ETA: Final = "reports.dart_excel 21:00(관심종목·공개 데모 — 재무가 바뀐 종목)"
SEGMENTS_ETA: Final = "reports.dart_excel 21:00(사업보고서 부문 매출 표 — 없으면 '표 없음')"
XLSX_MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DART: Final = "DART"
Basis = Literal["q", "a"]
# {(기준, 회계연도, 분기 | None(연간)): (매출, 영업이익)} — YoY 의 전년 값. 키 셋째 칸은 분기 행이면
# int, 연간이면 None 이라 Any(dict 키는 불변이라 `tuple[str, int, None]` 사전도 받게 — 공개 내보내기
# 시험이 연간 사전을 그대로 넘긴다)
PrevOf = Mapping[tuple[str, int, Any], tuple[int | None, int | None]]


def _pct(a: int | None, b: int | None) -> float | None:
    """a ÷ b × 100 — 분모 ≤ 0 이거나 값이 없으면 None."""
    if a is None or b is None or b <= 0:
        return None
    return a / b * 100


def _yoy(cur: int | None, base: int | None, what: str, notes: list[str]) -> float | None:
    """(이번 − 전년) ÷ |전년| × 100 — 전년 0·없음은 None. 부호가 바뀌면 비율 대신 None + notes
    ('<항목> 흑자전환'·'적자전환' — metrics §9, 재무 엔진 `ratios` 와 같은 문구)."""
    if cur is None or base is None or base == 0:
        return None
    if base < 0 < cur:
        notes.append(f"{what} 흑자전환")
        return None
    if base > 0 > cur:
        notes.append(f"{what} 적자전환")
        return None
    return (cur - base) / abs(base) * 100


def _label(year: int, q: int, start: date, end: date) -> str:
    try:
        return FiscalPeriod(year, q, start, end).label
    except ValueError:
        return f"{year} {q}Q"


def _sum(rows: Sequence[QuarterRow], col: str) -> int | None:
    vals = [cast(int | None, getattr(r, col)) for r in rows]
    if any(v is None for v in vals):
        return None
    return sum(cast(Sequence[int], vals))


@dataclass(frozen=True)
class _Line:
    period: str
    label: str
    fs_div: str
    fiscal_year: int
    fiscal_q: int | None
    start: date
    end: date
    rcept_dt: date | None
    revenue: int | None
    op: int | None
    ni: int | None
    ni_owner: int | None
    derived: bool
    quality: Quality
    notes: tuple[str, ...]


def _quarter_lines(rows: Sequence[QuarterRow]) -> list[_Line]:
    return [
        _Line(
            period=r.period,
            label=_label(r.fiscal_year, r.fiscal_q, r.period_start, r.period_end),
            fs_div=r.fs_div,
            fiscal_year=r.fiscal_year,
            fiscal_q=r.fiscal_q,
            start=r.period_start,
            end=r.period_end,
            rcept_dt=r.rcept_dt,
            revenue=r.revenue,
            op=r.op,
            ni=r.ni,
            ni_owner=r.ni_owner,
            derived=r.derived,
            quality=r.quality,
            notes=r.notes,
        )
        for r in rows
    ]


def _annual_line(year: int, fs_div: str, qs: Sequence[QuarterRow]) -> _Line:
    first, last = qs[0], qs[-1]
    twelve = last.period_end.month == 12
    lab = (
        f"{year}"
        if twelve
        else (
            f"FY{year % 100:02d} ({first.period_start.year % 100:02d}."
            f"{first.period_start.month:02d}~{last.period_end.year % 100:02d}."
            f"{last.period_end.month:02d})"
        )
    )
    return _Line(
        period=f"{year}FY",
        label=lab,
        fs_div=fs_div,
        fiscal_year=year,
        fiscal_q=None,
        start=first.period_start,
        end=last.period_end,
        rcept_dt=last.rcept_dt,
        revenue=_sum(qs, "revenue"),
        op=_sum(qs, "op"),
        ni=_sum(qs, "ni"),
        ni_owner=_sum(qs, "ni_owner"),
        derived=any(r.derived for r in qs),
        quality=worst(r.quality for r in qs) or Quality.OK,
        notes=tuple(dict.fromkeys(n for r in qs for n in r.notes)),
    )


def _annual_all(quarters: Sequence[QuarterRow]) -> dict[tuple[str, int], _Line]:
    """{(기준, 회계연도): 연간 행} — 같은 기준 네 분기가 다 있는 회계연도만(기준을 섞지 않는다)."""
    by: dict[tuple[str, int], list[QuarterRow]] = {}
    for r in quarters:
        by.setdefault((r.fs_div, r.fiscal_year), []).append(r)
    out: dict[tuple[str, int], _Line] = {}
    for (fs, year), rows in by.items():
        qs = sorted(rows, key=lambda r: r.fiscal_q)
        if [r.fiscal_q for r in qs] == [1, 2, 3, 4]:
            out[(fs, year)] = _annual_line(year, fs, qs)
    return out


def _annual_lines(all_years: dict[tuple[str, int], _Line]) -> list[_Line]:
    """회계연도마다 한 행 — CFS 네 분기가 다 있으면 CFS, 아니면 OFS(§4.5 CFS 우선)."""
    out: list[_Line] = []
    for year in sorted({y for _fs, y in all_years}):
        line = all_years.get(("CFS", year)) or all_years.get(("OFS", year))
        if line is not None:
            out.append(line)
    return out


def _rows(
    lines: Sequence[_Line],
    n: int,
    prev_of: PrevOf,
) -> list[FinRow]:
    """화면 행(최근 n 개). `prev_of` = {(기준, 회계연도, 분기|None): (매출, 영업이익)} — YoY 는 같은
    기준의 전년 같은 기간과만."""
    out: list[FinRow] = []
    for x in lines[-n:]:
        prev = prev_of.get((x.fs_div, x.fiscal_year - 1, x.fiscal_q))
        notes = list(x.notes)
        rev_yoy = _yoy(x.revenue, prev[0] if prev else None, "매출", notes)
        op_yoy = _yoy(x.op, prev[1] if prev else None, "영업이익", notes)
        out.append(
            FinRow(
                period=x.period,
                label=x.label,
                fiscal_year=x.fiscal_year,
                fiscal_q=x.fiscal_q,
                period_start=x.start,
                period_end=x.end,
                rcept_dt=x.rcept_dt,
                revenue=x.revenue,
                op=x.op,
                ni=x.ni,
                ni_owner=x.ni_owner,
                opm=_pct(x.op, x.revenue),
                npm=_pct(x.ni_owner, x.revenue),
                rev_yoy=rev_yoy,
                op_yoy=op_yoy,
                derived=x.derived,
                quality=x.quality,
                notes=notes,
            )
        )
    return out


def _ratio(r: RatioRow) -> RatioView:
    return RatioView(
        period=r.period,
        fs_div=r.fs_div,
        ttm_revenue=r.ttm_revenue,
        ttm_op=r.ttm_op,
        ttm_ni_owner=r.ttm_ni_owner,
        opm=r.opm,
        npm=r.npm,
        roe=r.roe,
        roa=r.roa,
        debt_ratio=r.debt_ratio,
        net_debt_ratio=r.net_debt_ratio,
        rev_yoy=r.rev_yoy,
        op_yoy=r.op_yoy,
        eps_ttm=r.eps_ttm,
        bps=r.bps,
        shares_basis=r.shares_basis,
        na_reasons={k: NaReason(v) for k, v in r.na_reasons.items()},
        quality=r.quality,
        notes=list(r.notes),
    )


def _no_filings(ctx: ReadContext, code: str, corp: str) -> Envelope[Financials]:
    data = Financials(
        applicable=False,
        na_reason=NaReason.NO_FILINGS,
        code=code,
        corp_code=corp,
        basis="q",
        fs_div=None,
        currency=None,
        tier=None,
        rows=[],
        ratios=None,
        checks=FinChecks(diff3m_max_pct=None, n_derived=0, n_estimated=0),
    )
    return envelope(
        data,
        source=_DART,
        as_of=dart_as_of(ctx.today()),
        quality=Quality.OK,
        notes=["정기보고서 없음 — 재무 미제출(최근 400일 정기보고서 공시 없음)"],
        generated_at=ctx.now(),
    )


def stock_financials(ctx: ReadContext, code: str, *, basis: Basis, n: int) -> Envelope[Financials]:
    lk = Lookup(ctx)
    ref = lk.ref(code)
    corp = ref.need_corp()
    quarters = ctx.repos.fin.quarters(corp)
    if not quarters:
        if company_na(lk, ref) is NaReason.NO_FILINGS:
            return _no_filings(ctx, code, corp)
        raise NoData(f"아직 없음 — {FIN_ETA}")
    rows = quarter_series(quarters)
    prev_of: PrevOf
    if basis == "q":
        lines = _quarter_lines(rows)
        prev_of = {(r.fs_div, r.fiscal_year, r.fiscal_q): (r.revenue, r.op) for r in quarters}
    else:
        all_years = _annual_all(quarters)
        lines = _annual_lines(all_years)
        prev_of = {(fs, y, None): (x.revenue, x.op) for (fs, y), x in all_years.items()}
    picked = lines[-n:]
    shown = _rows(picked, n, prev_of)
    fs_divs = {r.fs_div for r in rows}
    fs = "CFS" if "CFS" in fs_divs else "OFS"
    currencies = {r.currency for r in rows}
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    tiers = {r.tier for r in rows}
    tier: Literal["main", "full"] = "full" if "full" in tiers else "main"
    ratios = ctx.repos.fin.ratios(corp)
    # 3개월 대조는 화면에 나온 기간을 만든 분기만(연간은 그 해·그 기준 네 분기)
    shown_keys = {(x.fs_div, x.fiscal_year, x.fiscal_q) for x in picked}
    used = [
        r
        for r in quarters
        if (r.fs_div, r.fiscal_year, r.fiscal_q if basis == "q" else None) in shown_keys
    ]
    diffs = [r.diff3m_max_pct for r in used if r.diff3m_max_pct is not None]
    data = Financials(
        code=code,
        corp_code=corp,
        basis=basis,
        fs_div=fs,
        currency=currency,
        tier=tier,
        rows=shown,
        ratios=_ratio(ratios[-1]) if ratios else None,
        checks=FinChecks(
            diff3m_max_pct=max(diffs) if diffs else None,
            n_derived=sum(1 for x in shown if x.derived),
            n_estimated=sum(1 for x in shown if x.quality is Quality.ESTIMATED),
        ),
    )
    notes = _fin_notes(ref, fs_divs, currencies, tier)
    if basis == "a" and not shown:
        notes.append("같은 기준 네 분기가 다 있는 회계연도가 없다 — 연간 합을 만들지 않았다")
    last_rcept = max((r.rcept_dt for r in rows if r.rcept_dt is not None), default=None)
    q = worst([*(x.quality for x in shown), data.ratios.quality if data.ratios else None])
    return envelope(
        data,
        source=_DART,
        as_of=dart_as_of(last_rcept or max(r.period_end for r in rows)),
        quality=q or Quality.OK,
        notes=notes,
        generated_at=ctx.now(),
    )


def _fin_notes(
    ref: StockRef, fs_divs: set[str], currencies: set[str], tier: Literal["main", "full"]
) -> list[str]:
    notes: list[str] = []
    if fs_divs == {"OFS"}:
        notes.append("별도(OFS) — 연결재무제표 미작성")
    elif len(fs_divs) > 1:
        notes.append("연결(CFS)이 없는 분기는 별도(OFS)로 채웠다")
    if currencies != {"KRW"}:
        notes.append(f"재무 통화 {'·'.join(sorted(currencies))} — 환산하지 않는다")
    if tier == "main":
        notes.append("주요계정 층(전체 재무제표 수집 대상 아님) — 매출총이익·EBITDA 없음")
    if ref.code != ref.common_code and ref.common_code is not None:
        notes.append(f"우선주 — 회사(보통주 {ref.common_code}) 재무")
    return notes


# ── dart-report ───────────────────────────────────────────────────────────────────────


def _report(ctx: ReadContext, code: str) -> tuple[StockRef, ReportRow]:
    lk = Lookup(ctx)
    ref = lk.ref(code)
    common = ref.need_common()
    for c in dict.fromkeys((code, common)):
        row = ctx.repos.fin.report(c)
        if row is not None:
            return ref, row
    detail = common in lk.detail_codes
    why = "" if detail else " — 상세 수집 대상 아님: 관심종목에 넣으면 다음 수집(21:00)"
    raise NoData(f"아직 없음 — {REPORT_ETA}{why}")


def stock_report(ctx: ReadContext, code: str) -> Envelope[ReportView]:
    _ref, row = _report(ctx, code)
    p = row.payload
    data = ReportView(
        code=row.stock_code,
        corp_code=row.corp_code,
        built_on=row.built_on,
        fs_div_used=row.fs_div_used,
        year_from=row.year_from,
        year_to=row.year_to,
        unit=str(p.get("unit") or "억원"),
        blocks=ReportBlocks(
            quarterly=p.get("quarterly"),
            annual_costs=p.get("annual_costs"),
            bridge=p.get("bridge"),
            orders=p.get("orders"),
            quarterly_costs=p.get("quarterly_costs"),
        ),
        warnings=list(row.warnings),
        unmapped=p.get("unmapped"),
        xlsx_available=row.xlsx is not None,
        engine_version=row.engine_version,
        generated_at=row.generated_at,
    )
    notes = ["엑셀과 같은 입력·같은 셈(dart-report — 금액 억원)"]
    if row.stock_code != code:
        notes.append(f"우선주 — 보통주({row.stock_code}) 리포트")
    if row.warnings:
        notes.append(f"경고 {len(row.warnings)}건 — warnings")
    return envelope(
        data,
        source=_DART,
        as_of=row.generated_at,
        quality=row.quality,
        notes=notes,
        generated_at=ctx.now(),
    )


@dataclass(frozen=True)
class XlsxFile:
    body: bytes
    filename: str  # `<회사>_<코드>_재무리포트.xlsx`(DR 이름 규칙)
    sha256: str | None


def stock_report_xlsx(ctx: ReadContext, code: str) -> XlsxFile:
    ref, row = _report(ctx, code)
    if row.xlsx is None:
        raise NoData(f"아직 없음 — {REPORT_ETA}(엑셀 없이 블록만 있다)")
    name = ref.corp_name or ref.name or row.stock_code
    return XlsxFile(row.xlsx, f"{name}_{row.stock_code}_재무리포트.xlsx", row.xlsx_sha256)


# ── 부문 매출 ─────────────────────────────────────────────────────────────────────────


def stock_segments(ctx: ReadContext, code: str) -> Envelope[Segments]:
    lk = Lookup(ctx)
    ref = lk.ref(code)
    corp = ref.need_corp()
    rows = ctx.repos.fin.segments(corp)
    if not rows:
        raise NoData(f"아직 없음 — {SEGMENTS_ETA}")
    year = max(r.bsns_year for r in rows)
    last = sorted((r for r in rows if r.bsns_year == year), key=lambda r: r.segment)
    data = Segments(
        code=code,
        corp_code=corp,
        year=year,
        rcept_no=next((r.rcept_no for r in last if r.rcept_no), None),
        rows=[SegmentView(segment=r.segment, amounts=dict(r.amounts)) for r in last],
        quality=worst(r.quality for r in last) or Quality.ESTIMATED,
    )
    # 부문 행에는 시각이 없다 — 같은 실행이 쓴 리포트의 생성 시각(없으면 지금)
    rep = next(
        (
            r
            for c in dict.fromkeys((code, ref.common_code or code))
            if (r := ctx.repos.fin.report(c))
        ),
        None,
    )
    return envelope(
        data,
        source=_DART,
        as_of=rep.generated_at if rep is not None else ctx.now(),
        quality=data.quality,
        notes=["사업보고서 원문 표 best-effort — 회사마다 서식이 달라 추정(D-P4-22)"],
        generated_at=ctx.now(),
    )

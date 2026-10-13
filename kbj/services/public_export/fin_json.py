"""상장사 DART 재무 공개 파일 `fin/<종목코드>.json` + dart-report 공개 데모 `reports/<코드>.json`(+
`.xlsx`).

docs/p4_design.md §7(`fin/<stock_code>.json` — 분기 12·연간 4·비율·부문·오버행 요약·내부자 창·최근
공시 20·실적 일정, 종목당 약 6 KB × 약 2,600), D-P4-17(공개 내보내기 — DART 만), §6.2(페이지 8
공개판 — 재무·리포트·공시·오버행 주식 수·내부자 주식 수).

규칙
- **원천은 DART 하나**(`pub_fin`·`pub_filings` SQL 만 — 계약 ⑧). 시세·컨센서스·밸류에이션·관심종목은
  없다. 파일 자체가 봉투다(source `DART`·as_of·quality — W 의 `web/src/data/static.ts` 와 약속,
  manifest 에는 폴더 한 항목만).
- 재무 칸의 모양은 로그인 API `Financials`·`FinRow`·`RatioView`(`kbj/services/api/models/fin.py`)와
  같다 — 공개판 화면이 같은 그리기 코드를 쓰게. 분기는 (연도, 분기)마다 CFS 가 있으면 CFS, 없으면
  OFS(재무 엔진 `ratios.series` 와 같은 고르기), 연간은 같은 기준(CFS·OFS — 섞지 않는다) 네 분기
  단독의 합(CFS 네 분기가 다 있으면 CFS, 아니면 OFS — 하나라도 없으면 그 칸 null, 지어내지 않는다).
  YoY 는 같은 기준의 전년 같은 기간과만, 부호가 바뀌면 비율 대신 null + notes
  ('흑자전환'·'적자전환').
  이 고르기·합은 로그인 API `readers/fin.py` 와 같은 결과여야 한다 — 이 패키지는 그 모듈을 import
  하지 못해(계약 ⑧) `tests/unit/public_export/test_fin_json.py` 가 둘을 같은 행으로 대조한다.
- 파일을 만드는 회사 = 상장사(`corp_index.Corps`) 중 분기 재무가 있거나 최근 365일 공시가 있는 곳.
  그 밖은 파일이 없다(프런트는 `corp_index.json` 의 `fin` 칸으로 안다).
- 봉투 as_of = 섹션 날짜 중 가장 늦은 것(정기보고서 접수일·공시일·오버행·내부자 계산일 — 그날 00:00
  KST, 로그인 API `dart_as_of` 와 같은 뜻). quality = 재무·오버행·내부자·공시 행 중 가장 나쁜
  것(부문 매출은 언제나 추정이라 넣지 않고 그 섹션에 따로 적는다).
- 크기: 공백 없는 JSON. 폴더 합계 상한(`config/fin.yaml public_export.fin_max_total_mb`)은
  `export.py` 가 쓰기 전에 본다(넘으면 실패 — 조용히 자르지 않는다).
- 리포트: `config/reports.yaml public_demo` 종목만, `pub_fin.report` 가장 최근 것. 엑셀이 있으면
  `reports/<코드>.xlsx` 도 만들고 JSON 에 `xlsx_sha256`. 저장된 해시와 본문이 다르면 그 종목
  리포트를 만들지 않는다(사유는 manifest.skipped).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

from kbj.core.fin_rows import FiscalPeriod
from kbj.core.time import KST
from kbj.services.public_export.corp_index import Corps
from kbj.services.public_export.filings_json import (
    DART_VIEWER,
    INSIDER_LABEL,
    Q_SCHEDULE,
    day_start,
    schedule_row,
)
from kbj.services.public_export.manifest import (
    BlobFile,
    PublicFile,
    Quality,
    sha256_bytes,
    worst_quality,
)
from kbj.services.public_export.pub_read import (
    DART,
    Query,
    Reader,
    Row,
    canon,
    day,
    num,
    stamp,
    won,
)

__all__ = [
    "ANNUAL_N",
    "FILINGS_N",
    "FILING_COLUMNS",
    "FIN_DIR",
    "FIN_ROW_COLUMNS",
    "QUARTERS_N",
    "Q_FILINGS",
    "Q_INSIDER_WINDOWS",
    "Q_OVERHANG_SUMMARY",
    "Q_QUARTERS",
    "Q_RATIOS",
    "Q_REPORTS",
    "Q_SEGMENTS",
    "REPORT_DIR",
    "FinOut",
    "annual_all",
    "annual_lines",
    "annual_prev_of",
    "build_fin",
    "fin_rows",
    "pick_series",
    "quarter_lines",
    "quarter_prev_of",
    "table",
]

FIN_DIR: Final = "fin"
REPORT_DIR: Final = "reports"
QUARTERS_N: Final = 12
ANNUAL_N: Final = 4
FILINGS_N: Final = 20
FILINGS_DAYS: Final = 365
SCHEDULE_BACK_DAYS: Final = 7
SCHEDULE_AHEAD_DAYS: Final = 180
# 분기 12개 + 그 YoY(4개 앞) = 16분기, 연간 4년 + YoY = 5년 → 회계연도 6개 앞부터(비12월 결산 여유)
YEARS_BACK: Final = 6

Q_QUARTERS: Final = Query(
    "quarters",
    "SELECT corp_code, fiscal_year, fiscal_q, fs_div, period_start, period_end, rcept_dt, tier,"
    " revenue, op, ni, ni_owner, derived, diff3m_max_pct, currency, notes, quality"
    " FROM pub_fin.quarterly WHERE fiscal_year >= %(from_year)s"
    " ORDER BY corp_code, fiscal_year, fiscal_q, fs_div",
)
Q_RATIOS: Final = Query(
    "ratios",
    "SELECT DISTINCT ON (corp_code) corp_code, fiscal_year, fiscal_q, fs_div, ttm_revenue, ttm_op,"
    " ttm_ni_owner, opm, npm, roe, roa, debt_ratio, net_debt_ratio, rev_yoy, op_yoy, eps_ttm, bps,"
    " shares_basis, na_reasons, notes, quality"
    " FROM pub_fin.ratio ORDER BY corp_code, fiscal_year DESC, fiscal_q DESC",
)
Q_SEGMENTS: Final = Query(
    "segments",
    "SELECT s.corp_code, s.bsns_year, s.segment, s.amounts, s.rcept_no, s.quality"
    " FROM pub_fin.segment s"
    " JOIN (SELECT corp_code, max(bsns_year) AS last_year FROM pub_fin.segment"
    "   GROUP BY corp_code) m ON m.corp_code = s.corp_code AND m.last_year = s.bsns_year"
    " ORDER BY s.corp_code, s.segment",
)
Q_OVERHANG_SUMMARY: Final = Query(
    "overhang_summary",
    "SELECT DISTINCT ON (corp_code) corp_code, as_of, potential_new_shares, exercisable_shares,"
    " exchange_shares, denominator, denom_source, ratio_pct, exercisable_ratio_pct, notes, quality"
    " FROM pub_filings.overhang_summary WHERE as_of <= %(today)s"
    " ORDER BY corp_code, as_of DESC",
)
Q_INSIDER_WINDOWS: Final = Query(
    "insider_windows",
    "SELECT w.corp_code, w.as_of, w.days, w.net_shares, w.buy_shares, w.sell_shares, w.n_reports,"
    " w.n_reporters, w.ratio_pct, w.reason_known_pct, w.quality"
    " FROM pub_filings.insider_window w"
    " JOIN (SELECT corp_code, max(as_of) AS last_as_of FROM pub_filings.insider_window"
    "   WHERE as_of <= %(today)s GROUP BY corp_code) m"
    "   ON m.corp_code = w.corp_code AND m.last_as_of = w.as_of"
    " ORDER BY w.corp_code, w.days",
)
Q_FILINGS: Final = Query(
    "recent_filings",
    "SELECT x.corp_code, x.rcept_no, x.rcept_dt, x.title, x.kind, x.is_correction, x.kw_score,"
    " x.quality FROM (SELECT d.corp_code, d.rcept_no, d.rcept_dt, d.title, d.kind,"
    "   d.is_correction, d.kw_score, d.quality, row_number() OVER ("
    "   PARTITION BY d.corp_code ORDER BY d.rcept_dt DESC, d.rcept_no DESC) AS rn"
    "   FROM pub_filings.disclosure d"
    "   WHERE d.stock_code IS NOT NULL AND d.rcept_dt BETWEEN %(since)s AND %(today)s) x"
    " WHERE x.rn <= %(n)s"
    " ORDER BY x.corp_code, x.rcept_dt DESC, x.rcept_no DESC",
)
Q_REPORTS: Final = Query(
    "reports",
    "SELECT DISTINCT ON (stock_code) stock_code, built_on, corp_code, year_from, year_to,"
    " fs_div_used, payload, xlsx, xlsx_sha256, warnings, engine_version, generated_at, quality"
    " FROM pub_fin.report WHERE stock_code = ANY(%(codes)s::text[])"
    " ORDER BY stock_code, built_on DESC",
)


# 표(columns + rows) 칸 순서 — 키 이름을 되풀이하지 않는다(§7 크기 상한). 화면은 columns 로
# 다시 묶는다
FIN_ROW_COLUMNS: Final = (
    "period", "label", "fiscal_year", "fiscal_q", "period_start", "period_end", "rcept_dt",
    "revenue", "op", "ni", "ni_owner", "opm", "npm", "rev_yoy", "op_yoy", "derived", "quality",
    "notes",
)  # fmt: skip
FILING_COLUMNS: Final = (
    "rcept_no", "rcept_dt", "title", "kind", "is_correction", "kw_score", "quality",
)  # fmt: skip


def table(columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """dict 행들 → `{"columns": [...], "rows": [[...], ...]}`(칸 순서 = columns)."""
    return {"columns": list(columns), "rows": [[r[c] for c in columns] for r in rows]}


# ── 분기·연간 ─────────────────────────────────────────────────────────────────────────


def pick_series(rows: Iterable[Row]) -> list[Row]:
    """(연도, 분기)마다 한 행 — CFS 가 있으면 CFS, 없으면 OFS. 오래된 것부터(엔진
    `ratios.series`)."""
    best: dict[tuple[int, int], Row] = {}
    for r in rows:
        k = (int(r["fiscal_year"]), int(r["fiscal_q"]))
        cur = best.get(k)
        if cur is None or (cur["fs_div"] == "OFS" and r["fs_div"] == "CFS"):
            best[k] = r
    return [best[k] for k in sorted(best)]


def _label(year: int, q: int, start: date, end: date) -> str:
    try:
        return FiscalPeriod(year, q, start, end).label
    except ValueError:
        return f"{year} {q}Q"


def _list(v: object) -> list[str]:
    if not isinstance(v, list | tuple):
        return []
    return [str(x) for x in v]  # pyright: ignore[reportUnknownVariableType]


@dataclass(frozen=True)
class Line:
    """화면 한 줄(분기 단독 또는 회계연도 합) — 로그인 API `readers/fin._Line` 과 같은 칸."""

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


PrevOf = dict[tuple[str, int, int | None], tuple[int | None, int | None]]


def quarter_lines(series: Sequence[Row]) -> list[Line]:
    return [
        Line(
            period=f"{r['fiscal_year']}Q{r['fiscal_q']}",
            label=_label(
                int(r["fiscal_year"]), int(r["fiscal_q"]), r["period_start"], r["period_end"]
            ),
            fs_div=str(r["fs_div"]),
            fiscal_year=int(r["fiscal_year"]),
            fiscal_q=int(r["fiscal_q"]),
            start=r["period_start"],
            end=r["period_end"],
            rcept_dt=r.get("rcept_dt"),
            revenue=won(r.get("revenue")),
            op=won(r.get("op")),
            ni=won(r.get("ni")),
            ni_owner=won(r.get("ni_owner")),
            derived=bool(r.get("derived")),
            quality=worst_quality([str(r["quality"])]),
            notes=tuple(_list(r.get("notes"))),
        )
        for r in series
    ]


def quarter_prev_of(rows: Iterable[Row]) -> PrevOf:
    """{(기준, 회계연도, 분기): (매출, 영업이익)} — 분기 YoY 는 같은 기준(CFS·OFS)의 전년 같은
    분기와만."""
    return {
        (str(r["fs_div"]), int(r["fiscal_year"]), int(r["fiscal_q"])): (
            won(r.get("revenue")),
            won(r.get("op")),
        )
        for r in rows
    }


def _sum(vals: Sequence[int | None]) -> int | None:
    if any(v is None for v in vals):
        return None
    return sum(v for v in vals if v is not None)


def _annual_line(year: int, fs_div: str, qs: Sequence[Line]) -> Line:
    first, last = qs[0], qs[-1]
    lab = (
        f"{year}"
        if last.end.month == 12
        else (
            f"FY{year % 100:02d} ({first.start.year % 100:02d}.{first.start.month:02d}~"
            f"{last.end.year % 100:02d}.{last.end.month:02d})"
        )
    )
    return Line(
        period=f"{year}FY",
        label=lab,
        fs_div=fs_div,
        fiscal_year=year,
        fiscal_q=None,
        start=first.start,
        end=last.end,
        rcept_dt=last.rcept_dt,
        revenue=_sum([x.revenue for x in qs]),
        op=_sum([x.op for x in qs]),
        ni=_sum([x.ni for x in qs]),
        ni_owner=_sum([x.ni_owner for x in qs]),
        derived=any(x.derived for x in qs),
        quality=worst_quality(x.quality for x in qs),
        notes=tuple(dict.fromkeys(n for x in qs for n in x.notes)),
    )


def annual_all(rows: Iterable[Row]) -> dict[tuple[str, int], Line]:
    """{(기준, 회계연도): 연간 줄} — 같은 기준 네 분기가 다 있는 회계연도만(기준을 섞지 않는다)."""
    by: dict[tuple[str, int], list[Line]] = defaultdict(list)
    for x in quarter_lines(list(rows)):
        by[(x.fs_div, x.fiscal_year)].append(x)
    out: dict[tuple[str, int], Line] = {}
    for (fs, year), xs in by.items():
        qs = sorted(xs, key=lambda x: x.fiscal_q or 0)
        if [x.fiscal_q for x in qs] == [1, 2, 3, 4]:
            out[(fs, year)] = _annual_line(year, fs, qs)
    return out


def annual_lines(all_years: Mapping[tuple[str, int], Line]) -> list[Line]:
    """회계연도마다 한 줄 — CFS 네 분기가 다 있으면 CFS, 아니면 OFS(§4.5 CFS 우선)."""
    out: list[Line] = []
    for year in sorted({y for _fs, y in all_years}):
        line = all_years.get(("CFS", year)) or all_years.get(("OFS", year))
        if line is not None:
            out.append(line)
    return out


def annual_prev_of(all_years: Mapping[tuple[str, int], Line]) -> PrevOf:
    return {(fs, y, None): (x.revenue, x.op) for (fs, y), x in all_years.items()}


def _ratio_pct(a: int | None, b: int | None) -> float | None:
    """a ÷ b × 100 — 분모 ≤ 0 이거나 값이 없으면 None."""
    if a is None or b is None or b <= 0:
        return None
    return a / b * 100


def yoy(cur: int | None, base: int | None, what: str, notes: list[str]) -> float | None:
    """(이번 − 전년) ÷ |전년| × 100 — 전년 0·없음은 None. 부호가 바뀌면 비율 대신 None + notes
    ('<항목> 흑자전환'·'적자전환' — docs/metrics.md §9, 로그인 API·재무 엔진과 같은 문구)."""
    if cur is None or base is None or base == 0:
        return None
    if base < 0 < cur:
        notes.append(f"{what} 흑자전환")
        return None
    if base > 0 > cur:
        notes.append(f"{what} 적자전환")
        return None
    return (cur - base) / abs(base) * 100


def fin_rows(lines: Sequence[Line], n: int, prev_of: PrevOf) -> list[dict[str, Any]]:
    """마지막 n 줄 → `FinRow` 모양 dict. YoY 는 같은 기준의 전년 같은 기간(`prev_of`)과만."""
    out: list[dict[str, Any]] = []
    for x in lines[-n:] if n > 0 else []:
        prev = prev_of.get((x.fs_div, x.fiscal_year - 1, x.fiscal_q))
        notes = list(x.notes)
        rev_yoy = yoy(x.revenue, prev[0] if prev else None, "매출", notes)
        op_yoy = yoy(x.op, prev[1] if prev else None, "영업이익", notes)
        out.append(
            {
                "period": x.period,
                "label": x.label,
                "fiscal_year": x.fiscal_year,
                "fiscal_q": x.fiscal_q,
                "period_start": day(x.start),
                "period_end": day(x.end),
                "rcept_dt": day(x.rcept_dt),
                "revenue": x.revenue,
                "op": x.op,
                "ni": x.ni,
                "ni_owner": x.ni_owner,
                "opm": _ratio_pct(x.op, x.revenue),
                "npm": _ratio_pct(x.ni_owner, x.revenue),
                "rev_yoy": rev_yoy,
                "op_yoy": op_yoy,
                "derived": x.derived,
                "quality": x.quality,
                "notes": notes,
            }
        )
    return out


def _ratio_view(r: Row) -> dict[str, Any]:
    na = r.get("na_reasons")
    return {
        "period": f"{r['fiscal_year']}Q{r['fiscal_q']}",
        "fs_div": r["fs_div"],
        "ttm_revenue": won(r.get("ttm_revenue")),
        "ttm_op": won(r.get("ttm_op")),
        "ttm_ni_owner": won(r.get("ttm_ni_owner")),
        **{
            k: num(r.get(k))
            for k in (
                "opm",
                "npm",
                "roe",
                "roa",
                "debt_ratio",
                "net_debt_ratio",
                "rev_yoy",
                "op_yoy",
                "eps_ttm",
                "bps",
            )
        },
        "shares_basis": won(r.get("shares_basis")),
        "na_reasons": {str(k): str(v) for k, v in canon(na).items()}  # jsonb — 키 정렬
        if isinstance(na, Mapping)
        else {},
        "quality": worst_quality([str(r["quality"])]),
        "notes": _list(r.get("notes")),
    }


def _fin_notes(fs_divs: set[str], currencies: set[str], tier: str) -> list[str]:
    """로그인 API `readers/fin._fin_notes` 와 같은 문구(우선주 줄 없음 — 공개판은 종목=회사)."""
    notes: list[str] = []
    if fs_divs == {"OFS"}:
        notes.append("별도(OFS) — 연결재무제표 미작성")
    elif len(fs_divs) > 1:
        notes.append("연결(CFS)이 없는 분기는 별도(OFS)로 채웠다")
    if currencies != {"KRW"}:
        notes.append(f"재무 통화 {'·'.join(sorted(currencies))} — 환산하지 않는다")
    if tier == "main":
        notes.append("주요계정 층(전체 재무제표 수집 대상 아님) — 매출총이익·EBITDA 없음")
    return notes


def _fin_section(quarters: Sequence[Row], ratio: Row | None) -> tuple[dict[str, Any], date]:
    series = pick_series(quarters)
    shown = fin_rows(quarter_lines(series), QUARTERS_N, quarter_prev_of(quarters))
    years = annual_all(quarters)
    annual = fin_rows(annual_lines(years), ANNUAL_N, annual_prev_of(years))
    fs_divs = {str(r["fs_div"]) for r in series}
    currencies = {str(r["currency"]) for r in series}
    tier = "full" if any(r["tier"] == "full" for r in series) else "main"
    shown_keys = {(x["fiscal_year"], x["fiscal_q"]) for x in shown}
    used = [r for r in series if (int(r["fiscal_year"]), int(r["fiscal_q"])) in shown_keys]
    diffs = [d for r in used if (d := num(r.get("diff3m_max_pct"))) is not None]
    ratios = _ratio_view(ratio) if ratio is not None else None
    last_rcept = max((r["rcept_dt"] for r in series if r.get("rcept_dt") is not None), default=None)
    as_of_day: date = last_rcept or max(r["period_end"] for r in series)
    quality = worst_quality(
        [*(x["quality"] for x in shown), *([ratios["quality"]] if ratios else [])]
    )
    section: dict[str, Any] = {
        "as_of": day(as_of_day),
        "quality": quality,
        "notes": _fin_notes(fs_divs, currencies, tier),
        "fs_div": "CFS" if "CFS" in fs_divs else "OFS",
        "currency": next(iter(currencies)) if len(currencies) == 1 else None,
        "tier": tier,
        "quarters": table(FIN_ROW_COLUMNS, shown),
        "annual": table(FIN_ROW_COLUMNS, annual),
        "ratios": ratios,
        "checks": {
            "diff3m_max_pct": max(diffs) if diffs else None,
            "n_derived": sum(1 for x in shown if x["derived"]),
            "n_estimated": sum(1 for x in shown if x["quality"] == "estimated"),
        },
    }
    return section, as_of_day


# ── 그 밖의 섹션 ─────────────────────────────────────────────────────────────────────────


def _segments(rows: Sequence[Row]) -> dict[str, Any]:
    return {
        "year": int(rows[0]["bsns_year"]),
        "rcept_no": next((r["rcept_no"] for r in rows if r.get("rcept_no")), None),
        "rows": [
            {
                "segment": r["segment"],
                "amounts": {k: won(v) for k, v in canon(r["amounts"]).items()},  # jsonb — 키 정렬
            }
            for r in rows
        ],
        "quality": worst_quality([str(r["quality"]) for r in rows] or ["estimated"]),
        "notes": ["사업보고서 원문 표 best-effort — 회사마다 서식이 달라 추정(D-P4-22)"],
    }


def _overhang(r: Row) -> dict[str, Any]:
    return {
        "as_of": day(r["as_of"]),
        "potential_new_shares": won(r["potential_new_shares"]),
        "exercisable_shares": won(r["exercisable_shares"]),
        "exchange_shares": won(r["exchange_shares"]),
        "denominator": won(r.get("denominator")),
        "denom_source": r.get("denom_source"),
        "ratio_pct": num(r.get("ratio_pct")),
        "exercisable_ratio_pct": num(r.get("exercisable_ratio_pct")),
        "quality": worst_quality([str(r["quality"])]),
        "notes": _list(r.get("notes")),
    }


def _insider(rows: Sequence[Row]) -> dict[str, Any]:
    return {
        "label": INSIDER_LABEL,
        "as_of": day(rows[0]["as_of"]),
        "windows": [
            {
                "days": int(r["days"]),
                "net_shares": won(r["net_shares"]),
                "buy_shares": won(r["buy_shares"]),
                "sell_shares": won(r["sell_shares"]),
                "n_reports": won(r["n_reports"]),
                "n_reporters": won(r["n_reporters"]),
                "ratio_pct": num(r.get("ratio_pct")),
                "reason_known_pct": num(r.get("reason_known_pct")),
                "quality": worst_quality([str(r["quality"])]),
            }
            for r in sorted(rows, key=lambda x: int(x["days"]))
        ],
    }


def _filing(r: Row) -> dict[str, Any]:
    return {
        "rcept_no": r["rcept_no"],
        "rcept_dt": day(r["rcept_dt"]),
        "title": r["title"],
        "kind": r["kind"],
        "is_correction": bool(r["is_correction"]),
        "kw_score": won(r.get("kw_score")),
        "quality": worst_quality([str(r["quality"])]),
    }


def _group(rows: Iterable[Row]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        out[str(r["corp_code"])].append(r)
    return out


# ── 리포트 ────────────────────────────────────────────────────────────────────────────


def _report(r: Row) -> tuple[PublicFile, BlobFile | None]:
    code = str(r["stock_code"])
    payload: Mapping[str, Any] = r["payload"] if isinstance(r["payload"], Mapping) else {}  # pyright: ignore[reportUnknownVariableType]
    body = r.get("xlsx")
    xlsx = bytes(body) if isinstance(body, bytes | bytearray | memoryview) else None
    sha = sha256_bytes(xlsx) if xlsx is not None else None
    if xlsx is not None and r.get("xlsx_sha256") != sha:
        raise ValueError("저장된 엑셀 해시와 본문이 다르다")
    generated: datetime = r["generated_at"]
    warnings = _list(r.get("warnings"))
    data: dict[str, Any] = {
        "code": code,
        "corp_code": r["corp_code"],
        "built_on": day(r["built_on"]),
        "fs_div_used": r["fs_div_used"],
        "year_from": int(r["year_from"]),
        "year_to": int(r["year_to"]),
        "unit": str(payload.get("unit") or "억원"),
        "blocks": {  # jsonb — 키 정렬(원천에 따라 글자가 달라지지 않게)
            k: canon(payload.get(k))
            for k in ("quarterly", "annual_costs", "bridge", "orders", "quarterly_costs")
        },
        "warnings": warnings,
        "unmapped": canon(payload.get("unmapped")),
        "xlsx_available": xlsx is not None,
        "xlsx_sha256": sha,
        "engine_version": r["engine_version"],
        "generated_at": stamp(generated),
    }
    notes = ["엑셀과 같은 입력·같은 셈(dart-report — 금액 억원)"]
    if warnings:
        notes.append(f"경고 {len(warnings)}건 — warnings")
    f = PublicFile(
        name=f"{REPORT_DIR}/{code}.json",
        source=DART,
        as_of=generated,
        quality=worst_quality([str(r["quality"])]),
        data=data,
        notes=tuple(notes),
    )
    blob = BlobFile(f"{REPORT_DIR}/{code}.xlsx", xlsx) if xlsx is not None else None
    return f, blob


# ── 묶음 ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FinOut:
    files: tuple[PublicFile, ...]  # fin/*.json + reports/*.json
    blobs: tuple[BlobFile, ...]  # reports/*.xlsx
    fin_codes: frozenset[str]
    report_codes: frozenset[str]
    skipped: tuple[str, ...]


def build_fin(read: Reader, corps: Corps, *, now: datetime, demo: Sequence[str]) -> FinOut:
    """상장사 `fin/<코드>.json` 과 공개 데모 `reports/<코드>.*` 를 만든다."""
    if not corps:
        return FinOut((), (), frozenset(), frozenset(), ("fin/: 상장사 표(corp_code)가 비었다",))
    today = now.astimezone(KST).date()
    quarters = _group(read(Q_QUARTERS, {"from_year": today.year - YEARS_BACK}))
    ratios = {c: rs[0] for c, rs in _group(read(Q_RATIOS, {})).items()}
    segments = _group(read(Q_SEGMENTS, {}))
    overhang = {c: rs[0] for c, rs in _group(read(Q_OVERHANG_SUMMARY, {"today": today})).items()}
    insider = _group(read(Q_INSIDER_WINDOWS, {"today": today}))
    filings = _group(
        read(
            Q_FILINGS,
            {"since": today - timedelta(days=FILINGS_DAYS), "today": today, "n": FILINGS_N},
        )
    )
    schedule = _group(
        read(
            Q_SCHEDULE,
            {
                "start": today - timedelta(days=SCHEDULE_BACK_DAYS),
                "end": today + timedelta(days=SCHEDULE_AHEAD_DAYS),
            },
        )
    )
    skipped: list[str] = []
    reports: list[PublicFile] = []
    blobs: list[BlobFile] = []
    if demo:
        got = {str(r["stock_code"]): r for r in read(Q_REPORTS, {"codes": list(demo)})}
        for code in demo:
            row = got.get(code)
            if row is None:
                skipped.append(f"{REPORT_DIR}/{code}: 리포트가 아직 없다(reports.dart_excel)")
                continue
            try:
                f, blob = _report(row)
            except ValueError as e:  # 해시 불일치·크기·시각 — 그 종목만 빼고 이유를 남긴다
                skipped.append(f"{REPORT_DIR}/{code}: {e} — 만들지 않았다")
                continue
            reports.append(f)
            if blob is not None:
                blobs.append(blob)
    report_codes = frozenset(str(f.data["code"]) for f in reports)
    files: list[PublicFile] = []
    for code in sorted(corps.by_stock):
        c = corps.by_stock[code]
        q = quarters.get(c.corp_code, [])
        fl = filings.get(c.corp_code, [])
        if not q and not fl:
            continue
        stamps: list[date] = []
        quals: list[str] = []
        fin: dict[str, Any] | None = None
        if q:
            fin, fin_day = _fin_section(q, ratios.get(c.corp_code))
            stamps.append(fin_day)
            quals.append(fin["quality"])
        oh = overhang.get(c.corp_code)
        ins = insider.get(c.corp_code)
        if oh is not None:
            stamps.append(oh["as_of"])
            quals.append(str(oh["quality"]))
        if ins:
            stamps.append(ins[0]["as_of"])
            quals += [str(r["quality"]) for r in ins]
        if fl:
            stamps.append(fl[0]["rcept_dt"])
            quals += [str(r["quality"]) for r in fl]
        seg = segments.get(c.corp_code)
        data: dict[str, Any] = {
            "code": code,
            "corp_code": c.corp_code,
            "corp_name": c.name,
            "market": c.market,
            "acc_mt": c.acc_mt,
            "induty_code": c.induty_code,
            "financials": fin,
            "segments": _segments(seg) if seg else None,
            "overhang": _overhang(oh) if oh is not None else None,
            "insider": _insider(ins) if ins else None,
            "filings": table(FILING_COLUMNS, (_filing(r) for r in fl)),
            "dart_viewer": DART_VIEWER,
            "schedule": [schedule_row(r) for r in schedule.get(c.corp_code, [])],
            "report": code in report_codes,
        }
        notes = ["DART 공시·재무만(공개) — 시세·컨센서스·밸류에이션은 로그인 화면"]
        if fin is None:
            notes.append("분기 재무 없음 — 정기보고서 재무 수집 전이거나 재무 미제출")
        files.append(
            PublicFile(
                name=f"{FIN_DIR}/{code}.json",
                source=DART,
                as_of=day_start(max(stamps)),
                quality=worst_quality(quals),
                data=data,
                notes=tuple(notes),
            )
        )
    if not files:
        skipped.append("fin/: 분기 재무·공시가 있는 상장사가 없다 — 만들지 않았다")
    return FinOut(
        tuple([*files, *reports]),
        tuple(blobs),
        frozenset(str(f.data["code"]) for f in files),
        report_codes,
        tuple(skipped),
    )

"""재무제표 수집 및 누적→분기 환산.

DART 손익계산서는 **누적** 기준이다. 분기 단독 값은 차분으로 만든다.
    1Q = 1분기보고서 누적
    2Q = 반기 누적       - 1분기 누적
    3Q = 3분기 누적      - 반기 누적
    4Q = 사업보고서(연간) - 3분기 누적
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .client import REPRT, DartClient

EOK = 100_000_000  # 억원

# account_id(IFRS 태그) 우선 매칭. 회사마다 태그가 없으면 account_nm 으로 폴백.
TAGS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # 지표명: (account_id 후보, account_nm 정규화 후보)
    "revenue": (
        ("ifrs-full_Revenue", "ifrs_Revenue", "dart_OperatingRevenue"),
        ("매출액", "수익", "수익(매출액)", "영업수익", "매출"),
    ),
    "cogs": (
        ("ifrs-full_CostOfSales", "ifrs_CostOfSales"),
        ("매출원가", "영업비용"),
    ),
    "gross_profit": (("ifrs-full_GrossProfit",), ("매출총이익",)),
    "sgna": (
        ("dart_TotalSellingGeneralAdministrativeExpenses", "ifrs-full_SellingGeneralAndAdministrativeExpense"),
        ("판매비와관리비", "판매비및관리비"),
    ),
    "operating_income": (
        ("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"),
        ("영업이익", "영업이익(손실)", "영업손익"),
    ),
    "finance_income": (("ifrs-full_FinanceIncome",), ("금융수익", "금융수익(비용)")),
    "finance_cost": (("ifrs-full_FinanceCosts",), ("금융원가", "금융비용")),
    "other_income": (("ifrs-full_OtherIncome",), ("기타수익", "기타영업외수익")),
    "other_expense": (("ifrs-full_OtherExpenses",), ("기타비용", "기타영업외비용")),
    "equity_method": (
        ("ifrs-full_ShareOfProfitLossOfAssociatesAndJointVenturesAccountedForUsingEquityMethod",),
        ("지분법이익", "지분법손익", "관계기업투자손익"),
    ),
    "pretax_income": (
        ("ifrs-full_ProfitLossBeforeTax",),
        ("법인세비용차감전순이익", "법인세비용차감전순이익(손실)", "법인세차감전순이익", "계속영업이익(손실)"),
    ),
    "tax": (
        ("ifrs-full_IncomeTaxExpenseContinuingOperations",),
        ("법인세비용", "법인세수익(비용)"),
    ),
    "net_income": (
        ("ifrs-full_ProfitLoss",),
        ("당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익", "당기순손익"),
    ),
    # 재무상태표 (시점값 — 차분 대상 아님)
    "total_assets": (("ifrs-full_Assets",), ("자산총계",)),
    "total_equity": (("ifrs-full_Equity",), ("자본총계",)),
    "total_liabilities": (("ifrs-full_Liabilities",), ("부채총계",)),
    "cash": (("ifrs-full_CashAndCashEquivalents",), ("현금및현금성자산",)),
}

BS_KEYS = {"total_assets", "total_equity", "total_liabilities", "cash"}
IS_DIV = {"IS", "CIS"}


def _norm(s: str | None) -> str:
    return re.sub(r"[\s\u00a0]+", "", (s or "")).replace("(주)", "")


def _to_num(v: Any) -> float | None:
    if v in (None, "", "-"):
        return None
    s = str(v).replace(",", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


@dataclass
class Period:
    """한 보고서(=한 누적기간)에서 뽑아낸 계정값 모음."""

    year: int
    quarter: str  # '1Q'..'4Q'
    fs_div: str
    cumulative: dict[str, float] = field(default_factory=dict)
    balance: dict[str, float] = field(default_factory=dict)
    raw: list[dict[str, Any]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.quarter}{str(self.year)[2:]}"


def _pick(rows: list[dict[str, Any]], key: str) -> float | None:
    """계정 후보 목록으로 값 하나 뽑기. 누적금액 우선, 없으면 당기금액."""
    ids, names = TAGS[key]
    id_set = {i.lower() for i in ids}
    name_set = {_norm(n) for n in names}
    want_bs = key in BS_KEYS

    def value_of(r: dict[str, Any]) -> float | None:
        if want_bs:
            return _to_num(r.get("thstrm_amount"))
        # 손익: 누적금액 컬럼이 있으면 그것이 정답
        return _to_num(r.get("thstrm_add_amount")) or _to_num(r.get("thstrm_amount"))

    # 1순위: account_id 완전일치
    for r in rows:
        if (r.get("account_id") or "").lower() in id_set:
            v = value_of(r)
            if v is not None:
                return v
    # 2순위: account_nm 완전일치 (재무제표 구분까지 맞을 때)
    for r in rows:
        div = r.get("sj_div")
        ok_div = (div == "BS") if want_bs else (div in IS_DIV)
        if ok_div and _norm(r.get("account_nm")) in name_set:
            v = value_of(r)
            if v is not None:
                return v
    # 3순위: 구분 무시하고 이름만
    for r in rows:
        if _norm(r.get("account_nm")) in name_set:
            v = value_of(r)
            if v is not None:
                return v
    return None


def fetch_periods(
    client: DartClient, corp_code: str, years: list[int], fs_div: str = "CFS"
) -> list[Period]:
    """연도 리스트 × 4개 보고서를 모두 받아 Period 리스트로."""
    periods: list[Period] = []
    for year in years:
        for reprt_code, (quarter, _months) in REPRT.items():
            rows = client.financials(corp_code, year, reprt_code, fs_div)
            used = fs_div
            if not rows and fs_div == "CFS":  # 연결 미작성 → 별도로 폴백
                rows = client.financials(corp_code, year, reprt_code, "OFS")
                used = "OFS"
            if not rows:
                continue
            p = Period(year=year, quarter=quarter, fs_div=used, raw=rows)
            for key in TAGS:
                v = _pick(rows, key)
                if v is None:
                    continue
                (p.balance if key in BS_KEYS else p.cumulative)[key] = v
            periods.append(p)
    periods.sort(key=lambda p: (p.year, p.quarter))
    return periods


def to_quarterly(periods: list[Period]) -> list[dict[str, Any]]:
    """누적 → 분기 단독. 억원 단위 float 로 반환."""
    by_key = {(p.year, p.quarter): p for p in periods}
    order = ["1Q", "2Q", "3Q", "4Q"]
    out: list[dict[str, Any]] = []

    for (year, quarter), p in sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        idx = order.index(quarter)
        prev = by_key.get((year, order[idx - 1])) if idx > 0 else None

        row: dict[str, Any] = {
            "year": year,
            "quarter": quarter,
            "label": p.label,
            "fs_div": p.fs_div,
            # 직전 분기 보고서가 없으면 차분 불가 → 추정치 표시용 플래그
            "derived": bool(idx > 0 and prev is None),
        }
        for key, cum in p.cumulative.items():
            if idx == 0:
                q = cum
            elif prev is not None and key in prev.cumulative:
                q = cum - prev.cumulative[key]
            else:
                q = None
            row[key] = None if q is None else q / EOK
        for key, val in p.balance.items():
            row[key] = val / EOK
        # 파생 지표
        rev, op = row.get("revenue"), row.get("operating_income")
        row["opm"] = (op / rev) if rev not in (None, 0) and op is not None else None
        ni = row.get("net_income")
        row["npm"] = (ni / rev) if rev not in (None, 0) and ni is not None else None
        out.append(row)

    return out


def to_annual(periods: list[Period]) -> list[dict[str, Any]]:
    """연간(사업보고서 누적) + 진행 중 연도의 최신 누적을 함께 반환."""
    out = []
    by_year: dict[int, list[Period]] = {}
    for p in periods:
        by_year.setdefault(p.year, []).append(p)

    for year in sorted(by_year):
        ps = sorted(by_year[year], key=lambda p: p.quarter)
        last = ps[-1]
        months = REPRT[{v[0]: k for k, v in REPRT.items()}[last.quarter]][1]
        label = str(year) if last.quarter == "4Q" else f"{months}M{str(year)[2:]}"
        if last.quarter == "2Q":
            label = f"1H{str(year)[2:]}"
        row: dict[str, Any] = {
            "year": year,
            "label": label,
            "months": months,
            "partial": last.quarter != "4Q",
            "fs_div": last.fs_div,
        }
        for key, cum in last.cumulative.items():
            row[key] = cum / EOK
        for key, val in last.balance.items():
            row[key] = val / EOK
        rev, op = row.get("revenue"), row.get("operating_income")
        row["opm"] = (op / rev) if rev not in (None, 0) and op is not None else None
        out.append(row)
    return out

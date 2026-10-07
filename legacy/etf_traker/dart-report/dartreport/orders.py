"""수주(단일판매·공급계약체결) 공시 수집 및 파싱 — 이미지 4 블록.

거래소공시(pblntf_ty='I') 중 계약체결 공시를 찾아 원문에서
계약금액 / 계약상대 / 계약기간 / 계약내용을 뽑는다.
정정공시는 같은 원계약을 덮어쓴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .client import DartClient

EOK = 100_000_000

CONTRACT_PATTERNS = [
    re.compile(r"단일판매[·ㆍ.]?공급계약"),
    re.compile(r"공급계약\s*체결"),
    re.compile(r"수주"),
]

FIELD_ALIASES: dict[str, list[str]] = {
    "amount": ["계약금액", "공급계약금액", "계약총액", "판매·공급계약금액", "계약금액(원)"],
    "counterparty": ["계약상대", "계약상대방", "발주처", "수요처", "매출처"],
    "content": ["판매·공급계약내용", "계약내용", "공급계약내용", "판매공급계약내용", "계약의내용"],
    "start": ["시작일", "계약시작일", "계약기간시작", "착수일"],
    "end": ["종료일", "계약종료일", "계약기간종료", "완료일", "납기일"],
    "period": ["계약기간"],
    "recent_sales": ["최근매출액", "최근매출액(원)"],
    "ratio": ["매출액대비", "매출액대비(%)"],
}

DATE_RE = re.compile(r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})")


def _norm(s: str) -> str:
    return re.sub(r"[\s\u00a0]+", "", s or "")


def _money(text: str) -> float | None:
    t = re.sub(r"[\s,]", "", text or "")
    m = re.search(r"(\d{4,})", t)
    return float(m.group(1)) if m else None


def _parse_date(text: str) -> date | None:
    m = DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


@dataclass
class Order:
    rcept_no: str
    rcept_dt: str
    report_nm: str
    amount: float | None = None            # 원 단위
    counterparty: str = ""
    content: str = ""
    start: date | None = None
    end: date | None = None
    is_correction: bool = False
    raw_fields: dict[str, str] = field(default_factory=dict)

    @property
    def amount_eok(self) -> float | None:
        return None if self.amount is None else self.amount / EOK

    @property
    def days(self) -> int | None:
        if self.start and self.end and self.end >= self.start:
            return (self.end - self.start).days
        return None

    @property
    def annualized_sales_eok(self) -> float | None:
        """일환산 매출액(억원/년) = 계약금액 / 계약일수 * 365."""
        d, amt = self.days, self.amount_eok
        if not d or amt is None or d == 0:
            return None
        return amt / d * 365


def _extract_kv(html: str) -> dict[str, str]:
    """DART 공시 표는 대부분 [항목명 | 값] 2열 구조. 라벨→값 사전으로 접는다."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    kv: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th", "te", "tu"])]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        label = _norm(cells[0])
        value = " ".join(cells[1:]).strip()
        if label and value and label not in kv:
            kv[label] = value
    return kv


def _lookup(kv: dict[str, str], aliases: list[str]) -> str:
    for alias in aliases:
        n = _norm(alias)
        if n in kv:
            return kv[n]
    for alias in aliases:  # 부분일치 폴백
        n = _norm(alias)
        for k, v in kv.items():
            if n in k:
                return v
    return ""


def fetch_orders(
    client: DartClient,
    corp_code: str,
    bgn_de: str,
    end_de: str,
    verbose: bool = False,
) -> list[Order]:
    rows = client.disclosures(corp_code, bgn_de, end_de, pblntf_ty="I")
    candidates = [
        r for r in rows if any(p.search(r.get("report_nm", "")) for p in CONTRACT_PATTERNS)
    ]
    if verbose:
        print(f"  [수주] 계약 관련 공시 {len(candidates)}건 발견")

    orders: list[Order] = []
    for r in candidates:
        name = r.get("report_nm", "")
        order = Order(
            rcept_no=r["rcept_no"],
            rcept_dt=r.get("rcept_dt", ""),
            report_nm=name,
            is_correction="정정" in name,
        )
        for _fname, html in client.document_texts(r["rcept_no"]):
            kv = _extract_kv(html)
            if not kv:
                continue
            order.raw_fields = kv
            order.amount = _money(_lookup(kv, FIELD_ALIASES["amount"]))
            order.counterparty = _lookup(kv, FIELD_ALIASES["counterparty"])[:60]
            order.content = _lookup(kv, FIELD_ALIASES["content"])[:120]
            order.start = _parse_date(_lookup(kv, FIELD_ALIASES["start"]))
            order.end = _parse_date(_lookup(kv, FIELD_ALIASES["end"]))
            if not (order.start and order.end):
                period = _lookup(kv, FIELD_ALIASES["period"])
                dates = DATE_RE.findall(period)
                if len(dates) >= 2:
                    order.start = _parse_date(period)
                    tail = period[period.rfind(dates[-1][0]) - 2 :]
                    order.end = _parse_date(tail)
            if order.amount:
                break
        orders.append(order)

    orders.sort(key=lambda o: (o.start or date(1900, 1, 1), o.rcept_dt))
    return orders


def orders_table(orders: list[Order], nipa_keyword: str = "NIPA") -> dict[str, Any]:
    """이미지 4 양식용 표 + 상단 요약."""
    valid = [o for o in orders if o.amount_eok is not None]
    total = sum(o.amount_eok or 0 for o in valid)
    nipa = sum(
        o.amount_eok or 0
        for o in valid
        if nipa_keyword.lower() in (o.content + o.counterparty).lower()
    )
    return {
        "total_eok": total,
        "nipa_eok": nipa,
        "nipa_ratio": (nipa / total) if total else None,
        "rows": [
            {
                "시작일": o.start.isoformat() if o.start else "",
                "종료일": o.end.isoformat() if o.end else "",
                "계약일수": o.days,
                "일환산매출액(억원)": o.annualized_sales_eok,
                "고객사": o.counterparty,
                "사업내용": o.content,
                "수주액(억원)": o.amount_eok,
                "NIPA": "Y" if nipa_keyword.lower() in (o.content + o.counterparty).lower() else "",
                "접수번호": o.rcept_no,
                "정정": "정정" if o.is_correction else "",
            }
            for o in valid
        ],
    }

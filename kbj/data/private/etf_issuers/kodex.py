"""삼성자산운용 KODEX(ET `etf_tracker_v9/collectors.py:Kodex`:74 승격).

- 목록: `GET /api/v1/kodex/product.do?ordrColm=NAV&ordrSort=DESC&pageNo=N&srchTerm=w`(JSON 배열,
  빈 배열이면 끝 — 최대 29쪽).
- PDF: `GET /api/v1/kodex/product-pdf/{fId}.do?gijunYMD=YYYY.MM.DD` → `pdf.list[]`
  (`itmNo`·`secNm`·`ratio`·`applyQ`·`evalA`), 실제 기준일 `pdf.gijunYMD`.
- 과거 일자 조회 지원(HISTORY). ET 검증일 2026-08-04 [실측 필요 — 체크리스트 #27].
"""

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar, Final, Literal

from kbj.core.rows import HoldingRow
from kbj.data.private.etf_issuers.base import (
    FundRef,
    IssuerHttp,
    holding,
    is_kr_code,
    parse_num,
    ymd,
)

JSON_HEADERS: Final = {"Accept": "application/json"}


class Kodex:
    KEY: ClassVar[str] = "kodex"
    NAME: ClassVar[str] = "삼성 KODEX"
    DEPTH: ClassVar[Literal["full", "top10"]] = "full"
    HISTORY: ClassVar[bool] = True
    BASE: ClassVar[str] = "https://www.samsungfund.com"
    PAGE_MAX: ClassVar[int] = 29

    def __init__(self, http: IssuerHttp) -> None:
        self.http = http

    def universe(self) -> list[FundRef]:
        out: list[FundRef] = []
        seen: set[str] = set()
        for pg in range(1, self.PAGE_MAX + 1):
            j: Any = self.http.json(
                self.KEY, f"{self.BASE}/api/v1/kodex/product.do",
                params={"ordrColm": "NAV", "ordrSort": "DESC", "pageNo": pg, "srchTerm": "w"},
                headers=JSON_HEADERS,
            )  # fmt: skip
            if not j:
                break
            for x in j:
                fid = str(x.get("fId") or "").strip()
                if not fid or fid in seen:
                    continue
                seen.add(fid)
                out.append(FundRef(fid, x.get("stkTicker"), x.get("fNm")))
        return out

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        j: Any = self.http.json(
            self.KEY, f"{self.BASE}/api/v1/kodex/product-pdf/{fund_key}.do",
            params={"gijunYMD": day.strftime("%Y.%m.%d")}, headers=JSON_HEADERS,
        )  # fmt: skip
        pdf = (j or {}).get("pdf") or {}
        rows: dict[str, HoldingRow] = {}
        for x in pdf.get("list") or []:
            code = str(x.get("itmNo") or "").strip()
            if not is_kr_code(code):
                continue
            rows[code] = holding(self.KEY, code, x.get("secNm"), parse_num(x.get("applyQ")),
                                 parse_num(x.get("ratio")), parse_num(x.get("evalA")))  # fmt: skip
        return rows, (ymd(pdf.get("gijunYMD")) or day)

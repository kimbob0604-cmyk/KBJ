"""신한자산운용 SOL(ET `etf_tracker_v9/collectors.py:Sol`:186 승격).

- 목록: `GET /api/etf/pds?page=N` → `items[]`(`FUND_CD`·`ETF_CD6`·`ETF_NAME`), 빈 목록이면
  끝(최대 9쪽).
- PDF: `GET /api/etf/pds/pdf/{FUND_CD}` → `items[]`(`STOCK_CODE`·`SEC_NM`·`QTY`·`PRICE`(평가금액)·
  `WT_DISP`('12.3%')), 실제 기준일 `workDt`.
- **최신 영업일만 제공**(HISTORY=False) — 날짜 인자를 무시한다. 매일 돌려 자체 이력을 쌓는다.
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


class Sol:
    KEY: ClassVar[str] = "sol"
    NAME: ClassVar[str] = "신한 SOL"
    DEPTH: ClassVar[Literal["full", "top10"]] = "full"
    HISTORY: ClassVar[bool] = False  # 최신 영업일만 제공 → 매일 돌려서 자체 히스토리를 쌓는다
    BASE: ClassVar[str] = "https://www.soletf.com"
    PAGE_MAX: ClassVar[int] = 9

    def __init__(self, http: IssuerHttp) -> None:
        self.http = http

    def universe(self) -> list[FundRef]:
        out: list[FundRef] = []
        seen: set[str] = set()
        for pg in range(1, self.PAGE_MAX + 1):
            j: Any = self.http.json(self.KEY, f"{self.BASE}/api/etf/pds", params={"page": pg},
                                    headers=JSON_HEADERS)  # fmt: skip
            items = (j or {}).get("items") or []
            if not items:
                break
            for x in items:
                fc = str(x.get("FUND_CD") or "").strip()
                if not fc or fc in seen:
                    continue
                seen.add(fc)
                out.append(FundRef(fc, x.get("ETF_CD6"), x.get("ETF_NAME")))
        return out

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        j: Any = self.http.json(self.KEY, f"{self.BASE}/api/etf/pds/pdf/{fund_key}",
                                headers=JSON_HEADERS)  # fmt: skip
        rows: dict[str, HoldingRow] = {}
        for x in (j or {}).get("items") or []:
            code = str(x.get("STOCK_CODE") or "").strip()
            if not is_kr_code(code):
                continue
            wt = parse_num(str(x.get("WT_DISP") or "").replace("%", ""))
            rows[code] = holding(self.KEY, code, x.get("SEC_NM"), parse_num(x.get("QTY")), wt,
                                 parse_num(x.get("PRICE")))  # fmt: skip
        return rows, (ymd((j or {}).get("workDt")) or day)

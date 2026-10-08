"""미래에셋 TIGER(ET `etf_tracker_v9/collectors.py:Tiger`:111 승격).

- 목록: `GET /tigeretf/ko/product/search/list.ajax?listCnt=1000&pageIndex=1`(HTML) 의
  `ksdFund=KR…` ISIN — ISIN = KR7 + 종목코드(6) + 체크(3) → 티커 `isin[3:9]`. 이름은 목록에
  없다(None — 부르는 쪽이 KRX 메타 이름을 쓴다). 응답이 커서 시간 상한 90초(ET 그대로).
- PDF: `GET …/detail/pdfListAjax.ajax?ksdFund=…&listCnt=1000&pageIndex=1&fixDate=YYYY.MM.DD`(HTML
  표 — 코드·이름·수량·평가금액·비중). **데이터가 없는 날은 빈 표**를 주고 직전 영업일로 폴백해 주지
  않는다 → 하루씩 `BACK_DAYS`(7)까지 거슬러 올라가 **실제 데이터가 있는 날**을 기준일로 돌려준다.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import ClassVar, Final, Literal

from kbj.core.rows import HoldingRow
from kbj.data.private.etf_issuers.base import FundRef, IssuerHttp, rows_from_table

_ISIN: Final = re.compile(r"ksdFund=(KR[0-9A-Z]{10})")


class Tiger:
    KEY: ClassVar[str] = "tiger"
    NAME: ClassVar[str] = "미래에셋 TIGER"
    DEPTH: ClassVar[Literal["full", "top10"]] = "full"
    HISTORY: ClassVar[bool] = True
    BASE: ClassVar[str] = "https://investments.miraeasset.com"
    BACK_DAYS: ClassVar[int] = 7  # 주말·공휴일·당일 미공시 시 거슬러 올라갈 최대 일수
    LIST_TIMEOUT_S: ClassVar[float] = 90.0

    def __init__(self, http: IssuerHttp) -> None:
        self.http = http

    def universe(self) -> list[FundRef]:
        t = self.http.text(
            self.KEY, f"{self.BASE}/tigeretf/ko/product/search/list.ajax",
            params={"listCnt": 1000, "pageIndex": 1},
            headers={"X-Requested-With": "XMLHttpRequest",
                     "Referer": f"{self.BASE}/tigeretf/ko/product/search/list.do"},
            total_s=self.LIST_TIMEOUT_S,
        )  # fmt: skip
        out: list[FundRef] = []
        seen: set[str] = set()
        for isin in _ISIN.findall(t):
            if isin in seen:
                continue
            seen.add(isin)
            out.append(FundRef(isin, isin[3:9], None))
        return out

    def _fetch(self, fund_key: str, day: date) -> dict[str, HoldingRow]:
        t = self.http.text(
            self.KEY, f"{self.BASE}/tigeretf/ko/product/search/detail/pdfListAjax.ajax",
            params={"ksdFund": fund_key, "listCnt": 1000, "pageIndex": 1,
                    "fixDate": day.strftime("%Y.%m.%d")},
            headers={"X-Requested-With": "XMLHttpRequest",
                     "Referer": f"{self.BASE}/tigeretf/ko/product/search/detail/index.do"
                                f"?ksdFund={fund_key}"},
        )  # fmt: skip
        return rows_from_table(t, self.KEY)

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        for i in range(self.BACK_DAYS + 1):
            d = day - timedelta(days=i)
            rows = self._fetch(fund_key, d)
            if rows:
                return rows, d  # 요청일이 아니라 '실제 데이터가 있는 날'
        return {}, day

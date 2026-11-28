"""키움투자자산운용 KIWOOM(구 KOSEF) — 2026-11-28 추가(대시보드 커버 넓히기, 국내 주식형 약 2.1조).

사이트 www.kiwoometf.com(서버 렌더 + jQuery AJAX, 실측 2026-11-28).

- 목록: POST `/service/main/productListAjax`(본문 없음) → `products[]`(`gcode` = 6자리 티커,
  `goodsNm`). 펀드 키 = 티커(PDF 가 티커로 조회한다).
- PDF: POST `/service/etf/KO02010200MAjax4` 폼(`schGubun1`=티커, `startDate`=YYYYMMDD) →
  `pdfList[]`(`gcode`·`itemCode` = ISIN, `itemTitle`, `volume` = 1 CU 수량 '9,222',
  `assessment` = 평가금액 '1,579,728,600', `ratio` = '32.55%', `businessDate` = '2026.11.27').
  과거 일자 조회 지원. 공시가 없는 날(주말·휴일)은 `pdfList` 가 빈다 → `BACK_DAYS` 까지 하루씩.
- 거를 행: 현금(`CASH00000001`)·원화예금(`KRD010010001`)·선물·채권 ISIN → `isin_to_code` 가
  국내 주식·ETF ISIN(KR7…)만 남긴다. 비중은 소수 둘째 자리라 작은 종목은 '0.00%' — 수량·금액이 정본.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, ClassVar, Literal

from kbj.core.rows import HoldingRow
from kbj.data.private.etf_issuers.base import (
    FundRef,
    IssuerHttp,
    add_holding,
    holding,
    is_kr_code,
    isin_to_code,
    parse_num,
    ymd,
)


class Kiwoom:
    KEY: ClassVar[str] = "kiwoom"
    NAME: ClassVar[str] = "키움 KIWOOM"
    DEPTH: ClassVar[Literal["full", "top10"]] = "full"
    HISTORY: ClassVar[bool] = True
    BASE: ClassVar[str] = "https://www.kiwoometf.com"
    BACK_DAYS: ClassVar[int] = 5

    def __init__(self, http: IssuerHttp) -> None:
        self.http = http
        self.headers = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
                        "Referer": f"{self.BASE}/service/etf/KO02010100M"}  # fmt: skip

    def _post(self, path: str, data: dict[str, str] | None = None) -> dict[str, Any]:
        j: Any = self.http.json(self.KEY, f"{self.BASE}{path}", method="POST", data=data or {},
                                headers=self.headers)  # fmt: skip
        return j if isinstance(j, dict) else {}

    def universe(self) -> list[FundRef]:
        out: list[FundRef] = []
        seen: set[str] = set()
        for x in self._post("/service/main/productListAjax").get("products") or []:
            tk = str(x.get("gcode") or "").strip().upper()
            if not is_kr_code(tk) or tk in seen:
                continue
            seen.add(tk)
            out.append(FundRef(tk, tk, (str(x.get("goodsNm") or "").strip() or None)))
        return out

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        items: list[Any] = []
        real: date | None = None
        for k in range(self.BACK_DAYS + 1):
            d = day - timedelta(days=k)
            j = self._post("/service/etf/KO02010200MAjax4",
                           {"schGubun1": fund_key, "startDate": d.strftime("%Y%m%d")})  # fmt: skip
            items = j.get("pdfList") or []
            if items:
                real = ymd(items[0].get("businessDate")) or d
                break
        rows: dict[str, HoldingRow] = {}
        for x in items:
            code = isin_to_code(str(x.get("itemCode") or x.get("gcode") or ""))
            if code is None:
                continue  # 현금·원화예금·선물·채권·해외
            wt = parse_num(str(x.get("ratio") or "").replace("%", ""))
            qty, val = parse_num(x.get("volume")), parse_num(x.get("assessment"))
            add_holding(rows, holding(self.KEY, code, x.get("itemTitle"), qty, wt, val))
        return rows, (real or day)

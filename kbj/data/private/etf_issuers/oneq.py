"""하나자산운용 1Q — 2026-11-28 추가(대시보드 커버 넓히기, 국내 주식형 약 0.65조).

사이트 www.hanaam.com/1qetf(Next.js), 데이터는 `api.hanaam.com`(`window.__ENV.HANAAM_API_URL`,
실측 2026-11-28).

- 목록: GET `/api/Etf` → `data[]`(`id` = 사이트 상품 번호, `code` = 6자리 티커, `name`).
  펀드 키 = id.
- PDF: GET `/api/Etf/{id}/holdings` → `data.baseDate`('2026-11-27')·`data.holdings[]`(`code` = ISIN,
  `name`, `quantity` = 1 CU 수량 '6,703', `price` = 평가금액(원, 이름과 달리 단가가 아니다),
  `ratio` = '32.89'). **최신 공시일만** 준다 — 날짜 인자(baseDate·date 등)를 무시한다(HISTORY 아님,
  빈 날 채우기 대상이 아니다). 요청일보다 뒤 기준일이면 수집기가 받지 않는다.
- 거를 행: 현금·선물·채권 ISIN → `isin_to_code` 가 국내 주식·ETF ISIN(KR7…)만 남긴다.
"""

from __future__ import annotations

from datetime import date
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


class OneQ:
    KEY: ClassVar[str] = "oneq"
    NAME: ClassVar[str] = "하나 1Q"
    DEPTH: ClassVar[Literal["full", "top10"]] = "full"
    HISTORY: ClassVar[bool] = False
    BASE: ClassVar[str] = "https://api.hanaam.com"
    SITE: ClassVar[str] = "https://www.hanaam.com"

    def __init__(self, http: IssuerHttp) -> None:
        self.http = http
        self.headers = {"Accept": "application/json", "Origin": self.SITE,
                        "Referer": f"{self.SITE}/1qetf/etf/list"}  # fmt: skip

    def _get(self, path: str) -> Any:
        j: Any = self.http.json(self.KEY, f"{self.BASE}{path}", headers=self.headers,
                                allow_404=True)  # fmt: skip
        return j.get("data") if isinstance(j, dict) else None

    def universe(self) -> list[FundRef]:
        out: list[FundRef] = []
        seen: set[str] = set()
        for x in self._get("/api/Etf") or []:
            key = str(x.get("id") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            tk = str(x.get("code") or "").strip().upper()
            out.append(FundRef(key, tk if is_kr_code(tk) else None,
                               (str(x.get("name") or "").strip() or None)))  # fmt: skip
        return out

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        d = self._get(f"/api/Etf/{fund_key}/holdings")
        d = d if isinstance(d, dict) else {}
        rows: dict[str, HoldingRow] = {}
        for x in d.get("holdings") or []:
            code = isin_to_code(str(x.get("code") or ""))
            if code is None:
                continue
            add_holding(rows, holding(self.KEY, code, x.get("name"), parse_num(x.get("quantity")),
                                      parse_num(str(x.get("ratio") or "").replace("%", "")),
                                      parse_num(x.get("price"))))  # fmt: skip
        return rows, (ymd(d.get("baseDate")) or day)

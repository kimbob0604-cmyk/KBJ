"""한화자산운용 PLUS(구 ARIRANG) — ET `etf_tracker_v9/adapters/plus.py:Plus`:31 승격.

사이트 www.plusetf.co.kr(Spring Boot + jQuery, JSON API 두 개).

- 목록: `POST /api/v1/product/find/list` JSON 본문(`page` 0부터) — 페이지당 10건 고정(size 무시),
  `last` 가 참이 될 때까지(최대 30쪽). `content[]`(`id`·`nameCode`·`displayName`).
- PDF: `GET /api/v1/product/pdf/list?n={id}&d=YYYYMMDD&page=0&pageSize=N`(page 0부터) →
  `content[]`(`wkdate`·`jmCd`·`krJmCd`(ISIN)·`jmNm`·`amount`(수량)·`ratio`(%)). 평가금액 없음(0).

ET 원본의 실측 주의(요약):

1. d 는 반드시 YYYYMMDD — 'YYYY-MM-DD' 는 400 이 아니라 엉뚱한 과거일자를 조용히 준다.
2. 휴장일·미래일자는 직전 영업일 자료로 대체 — 실제 기준일은 행의 `wkdate`.
3. `jmCd` 가 12자리 ISIN 으로 오는 행이 섞인다 → `isin_to_code`(우선주 보정 포함).
4. 해외(US·IE·JP…)·채권(KR1/KR3/KR6)·선물(KR4)·원화예금(KRD)은 거른다. 인버스·레버리지는 비중합이
   100 을 크게 넘을 수 있다(정상).
"""

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar, Literal

from kbj.core.rows import HoldingRow
from kbj.data.private.etf_issuers.base import (
    FundRef,
    IssuerHttp,
    holding,
    is_kr_code,
    isin_to_code,
    parse_num,
    ymd,
)


class Plus:
    KEY: ClassVar[str] = "plus"
    NAME: ClassVar[str] = "한화 PLUS"
    DEPTH: ClassVar[Literal["full", "top10"]] = "full"
    HISTORY: ClassVar[bool] = True
    BASE: ClassVar[str] = "https://www.plusetf.co.kr"
    LIST: ClassVar[str] = BASE + "/api/v1/product/find/list"
    PDF: ClassVar[str] = BASE + "/api/v1/product/pdf/list"
    PAGE: ClassVar[int] = 1000  # PDF 한 쪽 건수(최대 보유 706종목 — 사실상 1회)
    LIST_PAGES: ClassVar[int] = 30
    PDF_PAGES: ClassVar[int] = 20

    def __init__(self, http: IssuerHttp) -> None:
        self.http = http
        self.headers = {"Accept": "application/json", "Referer": self.BASE + "/product/find"}

    def universe(self) -> list[FundRef]:
        out: list[FundRef] = []
        seen: set[str] = set()
        for pg in range(self.LIST_PAGES):
            body = {"searchSortTy": "aum", "searchSort": "DESC", "page": pg,
                    "searchAnnuityOptionTy": None, "searchWord": ""}  # fmt: skip
            j: Any = self.http.json(self.KEY, self.LIST, method="POST", json_body=body,
                                    headers=self.headers)  # fmt: skip
            items = (j or {}).get("content") or []
            if not items:
                break
            for x in items:
                fid = str(x.get("id") or "").strip()
                if not fid or fid in seen:
                    continue
                seen.add(fid)
                out.append(FundRef(fid, str(x.get("nameCode") or "").strip() or None,
                                   str(x.get("displayName") or "").strip() or None))  # fmt: skip
            if j.get("last"):
                break
        return out

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        rows: dict[str, HoldingRow] = {}
        asof: date | None = None
        pg = 0
        while True:
            j: Any = self.http.json(
                self.KEY, self.PDF, headers=self.headers,
                params={"n": fund_key, "d": day.strftime("%Y%m%d"), "page": pg,
                        "pageSize": self.PAGE},
            )  # fmt: skip
            items = (j or {}).get("content") or []
            for x in items:
                asof = asof or ymd(x.get("wkdate"))
                code = self._code(x.get("jmCd"), x.get("krJmCd"))
                if not code:
                    continue
                rows[code] = holding(
                    self.KEY,
                    code,
                    x.get("jmNm"),
                    parse_num(x.get("amount")),
                    parse_num(x.get("ratio")),
                    0.0,
                )  # 평가금액은 응답에 없다
            pg += 1
            total = (j or {}).get("totalPages") or 1
            if len(items) < self.PAGE or pg >= total or pg > self.PDF_PAGES:
                break
        return rows, (asof or day)

    @staticmethod
    def _code(jm: object, isin: object) -> str | None:
        """6자리 단축코드. 없으면 KR7 ISIN 에서 되돌린다(우선주 보정은 isin_to_code 하나로)."""
        c = str(jm or "").strip()
        if is_kr_code(c):
            return c  # 정상 경로(전체 행의 약 99%)
        return isin_to_code(c) or isin_to_code(str(isin or ""))

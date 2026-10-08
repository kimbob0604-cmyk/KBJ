"""한국투자신탁운용 ACE(구 KINDEX) — ET `etf_tracker_v9/adapters/ace.py:Ace`:92 승격.

사이트 www.aceetf.co.kr(Next.js SPA), API 는 프런트 번들에 하드코딩된 별도 호스트
`papi.aceetf.co.kr`
(www 의 `/api/…` 는 Next 라우트라 404).

- 목록: `/api/funds?page=1&size=1000` → `data[]`(`fundCd`·`stockCd`(ISIN)·`fundNm`). 기본 size 가
  10 이라 size 를 꼭 준다. ISIN[3:9] 가 티커(신규 코드 '0221Z0' 도 같은 규칙).
- PDF: `/api/funds/{fundCd}/pdf?page=1&size=5000&std_dt=YYYYMMDD` → `pdfList[]`(`jm_KSC_CD`·
  `sec_NM`·`wg`·`cu_ITEM_CNT`·`val_AM`·`std_DT`), 실제 기준일 `std_DT`. 과거 일자 조회 지원.

ET 원본의 실측 주의(요약 — 원문은 ET 파일 머리말):

1. **페이지네이션을 쓰지 않는다** — 서버 정렬이 비중 동률에서 불안정해 쪽 사이 중복·누락이 난다.
   size 를 크게(5000) 한 번에 받는다.
2. 휴장일·주말은 직전 영업일로 폴백해 주지 않는다(`pdfList=[]`) → `BACK_DAYS`(5)까지 하루씩.
3. `page.totalElements` 가 실제 행 수와 어긋나는 펀드가 있다 — 검증에 쓰지 않는다.
4. 해외 코드('AAPL US'·'9888 HK'·ISIN)가 섞인다 → `is_kr_code`. 4-1: 국내 형태인데 주식이 아닌
   코드(선물 A…·옵션 B…·CD E…·CP F…·전단채 S…·FX스왑 Z…·채권 분류코드 03502G 등)가 대량으로
   섞이고 만기마다 코드가 바뀐다 → `_STOCK`(숫자 6자리 또는 숫자4+영문1+숫자1)로 한 번 더 거른다.
5. 레버리지·인버스는 비중 합계가 100 이 아니다(정상).
6. 채권형은 `jm_KSC_CD` 가 종목분류코드라 한 펀드 안에서 겹친다 → 같은 코드는 수량·비중·금액을 합산.
7. 채권은 수량이 늘 0 — 변동 분석에서 `qty_floor` 로 자연히 빠진다.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, ClassVar, Final, Literal

from kbj.core.rows import HoldingRow
from kbj.data.private.etf_issuers.base import (
    FundRef,
    IssuerHttp,
    add_holding,
    holding,
    is_kr_code,
    parse_num,
    ymd,
)

# KRX 상장 주식·ETF 단축코드는 '숫자6자리'(005930) 아니면 '숫자4 + 영문1 + 숫자1'(0126Z0) 두 형태뿐
_STOCK: Final = re.compile(r"\d{6}|\d{4}[A-Z]\d")


class Ace:
    KEY: ClassVar[str] = "ace"
    NAME: ClassVar[str] = "한투 ACE"
    DEPTH: ClassVar[Literal["full", "top10"]] = "full"
    HISTORY: ClassVar[bool] = True
    BASE: ClassVar[str] = "https://papi.aceetf.co.kr"
    SITE: ClassVar[str] = "https://www.aceetf.co.kr"
    PAGE_SIZE: ClassVar[int] = 5000  # 페이지네이션이 깨져 있어 한 번에(최대 보유 739종목)
    BACK_DAYS: ClassVar[int] = 5

    def __init__(self, http: IssuerHttp) -> None:
        self.http = http
        self.headers = {"Accept": "application/json", "Origin": self.SITE,
                        "Referer": f"{self.SITE}/"}  # fmt: skip

    def _json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        j: Any = self.http.json(self.KEY, f"{self.BASE}{path}", params=params,
                                headers=self.headers, allow_404=True)  # fmt: skip
        return j if isinstance(j, dict) else {}  # 404 = 미존재 펀드코드

    def universe(self) -> list[FundRef]:
        j = self._json("/api/funds", {"page": 1, "size": 1000})
        out: list[FundRef] = []
        seen: set[str] = set()
        for x in j.get("data") or []:
            fc = str(x.get("fundCd") or "").strip()
            isin = str(x.get("stockCd") or "").strip()
            if not fc or fc in seen:
                continue
            seen.add(fc)
            tk = isin[3:9] if len(isin) == 12 else None
            out.append(FundRef(fc, tk if is_kr_code(tk) else None, x.get("fundNm")))
        return out

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        j: dict[str, Any] = {}
        real: date | None = None
        for k in range(self.BACK_DAYS + 1):
            d = day - timedelta(days=k)
            j = self._json(
                f"/api/funds/{fund_key}/pdf",
                {"page": 1, "size": self.PAGE_SIZE, "std_dt": d.strftime("%Y%m%d")},
            )
            if j.get("pdfList"):
                real = ymd(j.get("std_DT")) or d
                break
        rows: dict[str, HoldingRow] = {}
        for x in j.get("pdfList") or []:
            code = str(x.get("jm_KSC_CD") or "").strip()
            if not is_kr_code(code) or not _STOCK.fullmatch(code):
                continue  # 해외·선물·옵션·CD·CP·전단채·FX스왑·채권 분류코드(주의 4·4-1)
            if real is None:
                real = ymd(x.get("std_DT"))
            wt = parse_num(str(x.get("wg") or "").replace("%", ""))
            add_holding(rows, holding(self.KEY, code, x.get("sec_NM"),
                                      parse_num(x.get("cu_ITEM_CNT")), wt,
                                      parse_num(x.get("val_AM"))))  # fmt: skip
        return rows, (real or day)

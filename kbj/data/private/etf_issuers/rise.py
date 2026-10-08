"""KB자산운용 RISE(구 KBSTAR) — ET `etf_tracker_v9/adapters/rise.py:Rise`:27 승격.

사이트 www.riseetf.co.kr(서버 렌더링 + jQuery ajax, JSON API 없음).

- 목록: `POST /prod/finder/listJquery`(form, `page=N` 이 1~N 을 누적으로 돌려주는 '더보기' 방식) —
  충분히 큰 page(50 → 600건)를 한 번 부르고, 응답의 '전체 N 건' 과 대조해 모자라면 page 를 키워
  다시(최대 4번 — 유한 루프). 응답이 커서 시간 상한 180초(ET 그대로).
- PDF: `POST /prod/finder/productViewSearchTabJquery3`(form `searchDate`·`fundCd`) → 표(번호·이름·
  ISIN·수량·비중·평가금액).

ET 원본의 실측 주의(요약):

1. 종목코드가 12자리 ISIN — 국내 상장주식 KR7 + 코드6 + 체크3 만(우선주 보정 `isin_to_code`). KRD·
   KR1/KR3/KR6·KR4·해외 ISIN 은 거른다.
2. **응답 어디에도 기준일이 없고**, 휴장일을 요청하면 직전 영업일 자료로 조용히 대체한다 → 하루씩
   거슬러 올라가며 **내용이 바뀌는 지점**을 찾아 실제 기준일을 확정한다(최대 `LOOKBACK` 12일 —
   설·추석 최장 휴장 + 주말). 사이트 전체가 같은 달력을 쓰므로 요청일 단위로 캐시한다(실패도 캐시).
   요청일이 미래면 시작점을 **KST 오늘**로 당긴다(오늘은 주입 — 벽시계 의존 금지). 못 찾으면 오늘
   이하로 클램프한 요청일.

인스턴스 하나는 한 스레드에서 쓴다(캐시 `_asof`).
"""

from __future__ import annotations

import hashlib
import html as _html
import re
from collections.abc import Callable
from datetime import date, timedelta
from typing import ClassVar, Final, Literal

from kbj.core.rows import HoldingRow
from kbj.data.private.etf_issuers.base import (
    FundRef,
    IssuerHttp,
    holding,
    is_kr_code,
    isin_to_code,
    parse_num,
    table_cells,
    today_kst,
)

_ISIN: Final = re.compile(r"KR7([0-9A-Z]{6})[0-9]{3}")
_ITEM: Final = re.compile(
    r'/prod/finderDetail/([0-9A-Z]{3,8})">([^<]*)</a>\s*</p>\s*'
    r'<span class="code">\(([0-9A-Z]{6})\)',
    re.S,
)
_TOTAL: Final = re.compile(r"전체\s*<span[^>]*>\s*([\d,]+)\s*</span>\s*건")
_WS: Final = re.compile(r"\s+")


class Rise:
    KEY: ClassVar[str] = "rise"
    NAME: ClassVar[str] = "KB RISE"
    DEPTH: ClassVar[Literal["full", "top10"]] = "full"
    HISTORY: ClassVar[bool] = True
    BASE: ClassVar[str] = "https://www.riseetf.co.kr"
    LIST: ClassVar[str] = BASE + "/prod/finder/listJquery"
    PDF: ClassVar[str] = BASE + "/prod/finder/productViewSearchTabJquery3"
    PER_PAGE: ClassVar[int] = 12  # 목록 API 페이지당 건수(page=N 이 1~N 누적)
    PAGE0: ClassVar[int] = 50  # 첫 시도 페이지 — 600건까지
    LOOKBACK: ClassVar[int] = 12  # 기준일 역추적 최대 일수
    LIST_TIMEOUT_S: ClassVar[float] = 180.0

    def __init__(self, http: IssuerHttp, *, today: Callable[[], date] = today_kst) -> None:
        self.http = http
        self.today = today
        self.headers = {"X-Requested-With": "XMLHttpRequest", "Referer": self.BASE + "/prod/finder"}
        self._asof: dict[date, date] = {}  # 요청일 → 실제 기준일

    # ── 목록 ──
    def _list(self, page: int) -> str:
        data = {"searchText": "", "searchType1": "", "searchType2": "", "page": page,
                "searchOrder": "", "searchBoardType": "", "searchFieldType": "list"}  # fmt: skip
        return self.http.request(self.KEY, "POST", self.LIST, data=data, headers=self.headers,
                                 total_s=self.LIST_TIMEOUT_S).text  # fmt: skip

    def universe(self) -> list[FundRef]:
        page = self.PAGE0
        out: list[FundRef] = []
        for _ in range(4):  # 유한 루프 — 무한 재시도 없음
            t = self._list(page)
            out, seen = [], set[str]()
            for fund_key, name, ticker in _ITEM.findall(t):
                if fund_key in seen:
                    continue
                seen.add(fund_key)
                out.append(FundRef(fund_key, ticker, _html.unescape(name).strip()))
            m = _TOTAL.search(t)
            if not m:
                break  # 총건수 표기가 사라졌으면 대조 불가 — 받은 만큼 사용
            total = int(m.group(1).replace(",", ""))
            if len(out) >= total:
                break
            page = max(page * 2, -(-total // self.PER_PAGE) + 2)
        return out

    # ── 구성종목 ──
    def _pdf(self, fund_key: str, day: date) -> str:
        return self.http.request(
            self.KEY, "POST", self.PDF, headers=self.headers,
            data={"searchDate": day.isoformat(), "fundCd": fund_key},
        ).text  # fmt: skip

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        t = self._pdf(fund_key, day)
        rows: dict[str, HoldingRow] = {}
        for c in table_cells(t):
            if len(c) < 6:  # 번호 종목명 종목코드 수량 비중 평가금액
                continue
            code = self._code(c[2])
            if not code:
                continue
            rows[code] = holding(
                self.KEY,
                code,
                _html.unescape(c[1]),
                parse_num(c[3]),
                parse_num(c[4]),
                parse_num(c[5]),
            )  # 비중 25.04, 공란 '-' → 0
        if not rows:  # 순수 해외·채권형이면 국내종목이 하나도 없다
            return {}, self._asof.get(day, day)
        return rows, self._real_date(fund_key, day, t)

    @staticmethod
    def _code(raw: str | None) -> str | None:
        c = (raw or "").strip()
        if _ISIN.fullmatch(c):
            return isin_to_code(c)  # 우선주는 ISIN 본체와 단축코드가 다르다(005382 → 005387)
        return c if is_kr_code(c) else None

    @staticmethod
    def _sig(t: str) -> str:
        return hashlib.md5(_WS.sub("", t).encode(), usedforsecurity=False).hexdigest()

    def _real_date(self, fund_key: str, day: date, text: str) -> date:
        """내용이 바뀌는 지점을 찾아 실제 기준일을 잡는다. 평일 최신일이면 추가 요청 1번."""
        if day in self._asof:
            return self._asof[day]
        today = self.today()
        d0 = min(day, today)  # 사이트 최신 자료는 KST 오늘을 넘을 수 없다
        sig = self._sig(text)
        real = d0  # 못 찾으면 오늘 이하로 클램프한 요청일
        for k in range(1, self.LOOKBACK + 1):
            if self._sig(self._pdf(fund_key, d0 - timedelta(days=k))) != sig:
                real = d0 - timedelta(days=k - 1)
                break
        self._asof[day] = real  # 실패 케이스도 캐시 — 펀드마다 반복 조회 방지
        return real

"""NH-Amundi자산운용 HANARO — ET `etf_tracker_v9/adapters/hanaro.py:Hanaro`:71 승격.

사이트 www.hanaroetf.com(서버사이드 렌더링 + HTML 조각을 돌려주는 AJAX API).

- 목록: `/api/v1/fund/get-fund-search-list?pageNo=N` →
  `<li data-target="fund">…<a href="/fund/{uid}">
  …<h5>이름</h5>…<dt>종목코드</dt><dd>코드</dd>…</li>`(페이지당 10건 — 최대 20쪽). fund_key 는 상세
  URL 의 16자리 대문자 hex uid(data-fund-code 'EPM99' 는 안 먹힌다).
- PDF: `/api/v1/fund/{uid}/get-fund-holdings-list?baseDate=YYYY.MM.DD` →
  `<tr><td>순번<td>ISIN<th>이름
  <td>수량<td>평가금액<td>비중</tr>` 조각(페이지네이션 없음 — 11행부터 CSS 숨김일 뿐 값은 다 있다).

ET 원본의 실측 주의(요약):

1. 종목코드가 12자리 ISIN — **'KR7' 만** 통과(KRD 원화예금·CASH 설정현금·KR1/KR6 채권·KR4 선물·
   KRY 스왑은 [3:9] 를 떼면 국내 코드처럼 보여 위험하다). 우선주는 `isin_to_code` 로 보정.
2. **미래 일자를 요청하면 조용히 최신 기준일 데이터로 대체(클램프)** 한다 — 조각에 기준일이 없다.
   조각(d) 와 조각(d−1) 이 같으면 d 는 실제 기준일이 아니다 → 상세페이지 `/fund/{uid}` 의
   `<input id="pdfDate" value="YYYY.MM.DD">` 로 실제 기준일을 확정하고 그날 조각으로 다시 읽는다.
   상세페이지가 커서(280KB) 의심될 때만 받고 펀드별로 캐시한다(사이트 최신일 `_smax` 로 선클램프).
3. 휴장일·주말은 빈 조각(0행) → `BACK_DAYS`(7)까지 하루씩. 0행(그날 PDF 없음)과 '행은 있는데 국내
   주식이 없음'(채권·해외·합성형)을 구분한다(후자는 더 거슬러 가지 않는다).
4. baseDate 는 'YYYY.MM.DD'·'YYYYMMDD' 만 — 'YYYY-MM-DD' 는 0행 정상 응답으로 조용히 빈다.
5. 비중 합계는 국내주식형 90~100(KR7 아닌 행을 뺀 만큼 모자람 — 정상).
6. 이름에 HTML 엔티티가 그대로 온다 → unescape.
7. 2024년 11월 중순 이전 PDF 는 종목코드 칸이 비어 있다 — 실질 이력 하한.

인스턴스 하나는 한 스레드에서 쓴다(캐시 `_latest`·`_smax`).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from html import unescape
from typing import ClassVar, Final, Literal

from kbj.core.rows import HoldingRow
from kbj.data.private.etf_issuers.base import (
    FundRef,
    IssuerHttp,
    add_holding,
    holding,
    is_kr_code,
    isin_to_code,
    parse_num,
    table_cells,
)

_LI: Final = re.compile(r'<li data-target="fund"(.*?)</li>', re.S)
_UID: Final = re.compile(r"/fund/([0-9A-Fa-f]{16})")
_NAME: Final = re.compile(r"<h5>(.*?)</h5>", re.S)
_TICK: Final = re.compile(r"종목코드\s*</dt>\s*<dd>([^<]+)</dd>", re.S)
_PDFDATE: Final = re.compile(r'id="pdfDate"[^>]*\svalue="(\d{4})\.(\d{2})\.(\d{2})"')
_WS: Final = re.compile(r"\s+")


class Hanaro:
    KEY: ClassVar[str] = "hanaro"
    NAME: ClassVar[str] = "NH HANARO"
    DEPTH: ClassVar[Literal["full", "top10"]] = "full"
    HISTORY: ClassVar[bool] = True
    BASE: ClassVar[str] = "https://www.hanaroetf.com"
    PAGE_MAX: ClassVar[int] = 20  # 목록 안전 상한(페이지당 10건 고정)
    BACK_DAYS: ClassVar[int] = 7

    def __init__(self, http: IssuerHttp) -> None:
        self.http = http
        self.headers = {"Referer": f"{self.BASE}/fund/fund-list",
                        "X-Requested-With": "XMLHttpRequest"}  # fmt: skip
        self._latest: dict[str, date | None] = {}  # uid → 상세페이지에서 확정한 최신 기준일
        self._smax: date | None = None  # 지금까지 확인한 사이트 전체 최신 기준일

    def _get(self, path: str, referer: str | None = None) -> str:
        headers = dict(self.headers)
        if referer:
            headers["Referer"] = referer
        return self.http.text(self.KEY, f"{self.BASE}{path}", headers=headers)

    # ── 목록 ──
    def universe(self) -> list[FundRef]:
        out: list[FundRef] = []
        seen: set[str] = set()
        for pg in range(1, self.PAGE_MAX + 1):
            t = self._get(f"/api/v1/fund/get-fund-search-list?pageNo={pg}")
            items = _LI.findall(t)
            if not items:
                break
            for it in items:
                m = _UID.search(it)
                if not m:
                    continue
                uid = m.group(1).upper()
                if uid in seen:
                    continue
                seen.add(uid)
                tk_m = _TICK.search(it)
                nm_m = _NAME.search(it)
                tk = unescape(tk_m.group(1)).strip().upper() if tk_m else ""
                name = unescape(_WS.sub(" ", nm_m.group(1))).strip() if nm_m else None
                out.append(FundRef(uid, tk if is_kr_code(tk) else None, name))
        return out

    # ── PDF ──
    def _frag(self, uid: str, d: date) -> str:
        return self._get(
            f"/api/v1/fund/{uid}/get-fund-holdings-list?baseDate={d.strftime('%Y.%m.%d')}",
            referer=f"{self.BASE}/fund/{uid}",
        )

    def _parse(self, html: str) -> tuple[dict[str, HoldingRow], int]:
        """(국내주식 행, 조각의 전체 행 수). 0행 = 그날 PDF 없음, 행은 있는데 비면 국내주식 없음."""
        rows: dict[str, HoldingRow] = {}
        n = 0
        for c in table_cells(html):
            if len(c) < 6:
                continue
            n += 1
            isin = c[1].strip().upper()
            if not isin.startswith("KR7") or len(isin) != 12:  # 주의 1
                continue
            code = isin_to_code(isin)  # 우선주 보정 포함 (005931 → 005935)
            if not code:
                continue
            wt = parse_num(c[5].replace("%", ""))
            add_holding(rows, holding(self.KEY, code, unescape(c[2]), parse_num(c[3]), wt,
                                      parse_num(c[4])))  # fmt: skip
        return rows, n

    def _pdf_date(self, uid: str) -> date | None:
        """상세페이지에서 그 펀드의 실제 최신 PDF 기준일(주의 2). 못 읽으면 None(캐시)."""
        if uid in self._latest:
            return self._latest[uid]
        t = self._get(f"/fund/{uid}")
        m = _PDFDATE.search(t)
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
        self._latest[uid] = d
        if d and (self._smax is None or d > self._smax):
            self._smax = d
        return d

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        want = day
        known = self._latest.get(fund_key) or self._smax
        if known and want > known:
            want = known

        body, rows, n, d = "", {}, 0, want
        for k in range(self.BACK_DAYS + 1):  # 휴장일 폴백 (주의 3)
            d = want - timedelta(days=k)
            body = self._frag(fund_key, d)
            rows, n = self._parse(body)
            if n:
                break
        if not n:  # 그 범위에 PDF 자체가 없다
            return {}, day

        # 미래일 클램프 보정(주의 2). 그 펀드의 최신일이 이미 d 로 확정돼 있으면 건너뛴다
        if self._latest.get(fund_key) != d:
            prev = _WS.sub("", self._frag(fund_key, d - timedelta(days=1)))
            if prev == _WS.sub("", body):
                real = self._pdf_date(fund_key)
                if real and real < d:
                    r2, n2 = self._parse(self._frag(fund_key, real))
                    if n2:
                        rows, d = r2, real
        return rows, d

"""타임폴리오 TIME(ET `etf_tracker_v9/collectors.py:TimeFolio`:154 승격).

- 목록: `GET /m11_list.php?cate=001`(해외투자)·`002`(국내투자) HTML 의
  `m11_view.php?idx=N&cate=…` + `<div class="name">` + `<div class="codeNum"><span>코드`.
- PDF: `GET /m11_view.php?idx=N&pdfDate=YYYY-MM-DD` HTML — `id="constituentItems"` 표의 tbody
  (코드·이름·수량·평가금액·비중), 실제 기준일 `<input id="pdfDate" value="YYYY-MM-DD">`.
"""

from __future__ import annotations

import re
from datetime import date
from typing import ClassVar, Final, Literal

from kbj.core.rows import HoldingRow
from kbj.data.private.etf_issuers.base import (
    FundRef,
    IssuerHttp,
    rows_from_table,
    unescape_basic,
    ymd,
)

_ITEM: Final = re.compile(
    r'm11_view\.php\?idx=(\d+)&cate=\d+".*?<div class="name">([^<]+)</div>'
    r'.*?<div class="codeNum"><span>([^<]+)</span>',
    re.S,
)
_BODY: Final = re.compile(r'id="constituentItems".*?<tbody>(.*?)</tbody>', re.S)
_DATE: Final = re.compile(r'id="pdfDate"[^>]*value="([\d\-]+)"')


class TimeFolio:
    KEY: ClassVar[str] = "timefolio"
    NAME: ClassVar[str] = "타임폴리오 TIME"
    DEPTH: ClassVar[Literal["full", "top10"]] = "full"
    HISTORY: ClassVar[bool] = True
    BASE: ClassVar[str] = "https://timeetf.co.kr"
    CATES: ClassVar[tuple[str, ...]] = ("001", "002")  # 001 해외투자 / 002 국내투자

    def __init__(self, http: IssuerHttp) -> None:
        self.http = http

    def universe(self) -> list[FundRef]:
        out: list[FundRef] = []
        seen: set[str] = set()
        for cate in self.CATES:
            t = self.http.text(self.KEY, f"{self.BASE}/m11_list.php", params={"cate": cate})
            for idx, nm, code in _ITEM.findall(t):
                if idx in seen:
                    continue
                seen.add(idx)
                out.append(FundRef(idx, code.strip(), unescape_basic(nm.strip())))
        return out

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        t = self.http.text(self.KEY, f"{self.BASE}/m11_view.php",
                           params={"idx": fund_key, "pdfDate": day.isoformat()})  # fmt: skip
        m = _BODY.search(t)
        if not m:
            return {}, day
        d = _DATE.search(t)
        return rows_from_table(m.group(1), self.KEY), ((ymd(d.group(1)) if d else None) or day)

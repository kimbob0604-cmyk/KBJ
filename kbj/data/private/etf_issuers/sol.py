"""신한자산운용 SOL(ET `etf_tracker_v9/collectors.py:Sol`:186 승격).

- 목록: `GET /api/etf/pds?page=N` → `items[]`(`FUND_CD`·`ETF_CD6`·`ETF_NAME`), 빈 목록이면
  끝(최대 9쪽).
- PDF: `GET /api/etf/pds/pdf/{FUND_CD}` → `items[]`(`STOCK_CODE`·`SEC_NM`·`QTY`·`PRICE`(평가금액)·
  `WT_DISP`('12.3%')), 실제 기준일 `workDt`.
- **최신 영업일만 제공**(HISTORY=False) — 날짜 인자를 무시한다. 매일 돌려 자체 이력을 쌓는다.

**레이트리밋**(2026-10-27 실측): 1/s 로 약 20요청(27초)째에 **403** +
`{"error":"Rate limit exceeded"}`. 10-20 부터 매일 28개 중 15개만 받았다. 호스트 리미터를
`RATE` 로 낮추고 403 을 '요청 과다'로 보아(`limit_status`) `COOLDOWN_S`×n 쉬고 다시 부른다.
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
    HOST: ClassVar[str] = "www.soletf.com"
    BASE: ClassVar[str] = f"https://{HOST}"
    RATE: ClassVar[float] = 0.3  # 실측 1/s 에서 20요청째 막힘 — 분당 18요청
    COOLDOWN_S: ClassVar[float] = 60.0
    LIMIT_STATUS: ClassVar[frozenset[int]] = frozenset({403})  # 이 운용사의 '요청 과다'
    PAGE_MAX: ClassVar[int] = 9

    def __init__(self, http: IssuerHttp) -> None:
        self.http = http
        self.http.cap_rate(self.HOST, self.RATE)

    def _json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self.http.json(self.KEY, url, params=params, headers=JSON_HEADERS,
                              retry_wait_s=self.COOLDOWN_S,
                              limit_status=self.LIMIT_STATUS)  # fmt: skip

    def universe(self) -> list[FundRef]:
        out: list[FundRef] = []
        seen: set[str] = set()
        for pg in range(1, self.PAGE_MAX + 1):
            j: Any = self._json(f"{self.BASE}/api/etf/pds", {"page": pg})
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
        j: Any = self._json(f"{self.BASE}/api/etf/pds/pdf/{fund_key}")
        rows: dict[str, HoldingRow] = {}
        for x in (j or {}).get("items") or []:
            code = str(x.get("STOCK_CODE") or "").strip()
            if not is_kr_code(code):
                continue
            wt = parse_num(str(x.get("WT_DISP") or "").replace("%", ""))
            rows[code] = holding(self.KEY, code, x.get("SEC_NM"), parse_num(x.get("QTY")), wt,
                                 parse_num(x.get("PRICE")))  # fmt: skip
        return rows, (ymd((j or {}).get("workDt")) or day)

"""삼성액티브자산운용 KoAct — ET `etf_tracker_v9/adapters/koact.py:KoAct`:23 승격.

KODEX 와 같은 웹 플랫폼, API 네임스페이스만 다르다.

- 목록: `/api/v1/product/etf.do?pageNo=N`(20건/쪽 고정 — 다른 파라미터를 붙이면 400 을 잘 뱉는다,
  pageNo 만) → `etfs[]`(`fId`·`stkTicker`·`fNm`). 새 펀드가 없는 쪽이 오면 끝.
- PDF: `/api/v1/product/etf-pdf/{fId}.do?gijunYMD=YYYY.MM.DD` →
  `pdf.list[]`(`itmNo`·`secNm`·`ratio`·
  `applyQ`·`evalA`). 휴장일이면 직전 영업일로 자동 폴백하고 실제 기준일은 `pdf.gijunYMD`.

**Cloudflare 레이트리밋**(ET 실측): 약 25요청/35초를 넘기면 429 + "Just a moment…" 챌린지 HTML 이
돌아오고 약 33초 뒤 풀린다. ET 는 클래스 단위 창(20요청/35초 — 한계의 8할)과 35초×n 쿨다운으로
버텼다. 여기서는 호스트 리미터 속도를 0.5/s 로 낮추고(`IssuerHttp.cap_rate` — 한계의 7할),
429·200 인데 JSON 아님(챌린지)은 `COOLDOWN_S`×n 쉬고 다시 부른다. 400·404 처럼 다시 불러도
안 풀리는 응답은 바로 실패(재시도 낭비 금지 — ET 그대로).
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
    parse_num,
    ymd,
)


class KoAct:
    KEY: ClassVar[str] = "koact"
    NAME: ClassVar[str] = "KoAct(삼성액티브)"
    DEPTH: ClassVar[Literal["full", "top10"]] = "full"
    HISTORY: ClassVar[bool] = True
    HOST: ClassVar[str] = "www.samsungactive.co.kr"
    BASE: ClassVar[str] = f"https://{HOST}"
    RATE: ClassVar[float] = 0.5  # 실측 한계 25요청/35초(≈0.71/s)의 7할
    COOLDOWN_S: ClassVar[float] = 35.0
    PAGE_MAX: ClassVar[int] = 19

    def __init__(self, http: IssuerHttp) -> None:
        self.http = http
        self.http.cap_rate(self.HOST, self.RATE)
        self.headers = {"Accept": "application/json", "Referer": f"{self.BASE}/etf/list.do"}

    def _json(self, path: str, params: dict[str, Any]) -> Any:
        return self.http.json(self.KEY, f"{self.BASE}{path}", params=params, headers=self.headers,
                              retry_non_json=True, retry_wait_s=self.COOLDOWN_S)  # fmt: skip

    def universe(self) -> list[FundRef]:
        out: list[FundRef] = []
        seen: set[str] = set()
        for pg in range(1, self.PAGE_MAX + 1):
            j = self._json("/api/v1/product/etf.do", {"pageNo": pg})
            items = (j or {}).get("etfs") or []
            if not items:
                break
            new = 0
            for x in items:
                fid = str(x.get("fId") or "").strip()
                if not fid or fid in seen:
                    continue
                seen.add(fid)
                new += 1
                out.append(FundRef(fid, str(x.get("stkTicker") or "").strip() or None,
                                   x.get("fNm")))  # fmt: skip
            if not new:
                break
        return out

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        j = self._json(f"/api/v1/product/etf-pdf/{fund_key}.do",
                       {"gijunYMD": day.strftime("%Y.%m.%d")})  # fmt: skip
        pdf = (j or {}).get("pdf") or {}
        rows: dict[str, HoldingRow] = {}
        for x in pdf.get("list") or []:
            # itmNo: '005930' 국내주식 / '0162M0' 국내ETF / 'AVGO US Equity' 해외
            #        'KRD010010001' 원화예금 / 'CASH00000001' 설정현금액 / 'KRG…' ETN
            code = str(x.get("itmNo") or "").strip()
            if not is_kr_code(code):
                continue
            wt = parse_num(str(x.get("ratio") or "").replace("%", ""))
            rows[code] = holding(self.KEY, code, x.get("secNm"), parse_num(x.get("applyQ")), wt,
                                 parse_num(x.get("evalA")))  # fmt: skip
        return rows, (ymd(pdf.get("gijunYMD")) or day)

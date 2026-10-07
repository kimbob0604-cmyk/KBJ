"""가짜 KOSIS(`httpx.MockTransport`) — 통계표 선택 방식 자료. 합성 데이터만.

경로 `/openapi/Param/statisticsParameterData.do`(probe_results §5). 응답은 행 목록 JSON, 오류는
`{"err": 코드, "errMsg": …}`(`30` 데이터 없음 → 어댑터는 빈 목록).

- 표(`tblId`)마다 합성 한 행(PRD_DE = `endPrdDe` 또는 `startPrdDe`, 값은 고정 시드).
- `inject(code, count)`: 오류 봉투. 키(`apiKey`)가 다르면 `err=11`(인증키 오류).
- 기록 `seen`(시각·consumer·표·주기 — 키는 뺀다).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import httpx

KEY: Final = "kosis-sim-fake-key-0123456789"


@dataclass(frozen=True)
class KosisSeen:
    at: datetime
    consumer: str
    tbl_id: str
    prd_se: str
    err: str


class FakeKosis:
    def __init__(self, now: Callable[[], datetime], *, key: str = KEY) -> None:
        self._now = now
        self.key = key
        self.seen: list[KosisSeen] = []
        self._inject: list[list[Any]] = []
        self._lock = threading.Lock()

    def transport(self, consumer: str = "unknown") -> httpx.MockTransport:
        def handle(req: httpx.Request) -> httpx.Response:
            return self.handle(req, consumer)

        return httpx.MockTransport(handle)

    def inject(self, err: str, count: int = 1) -> None:
        with self._lock:
            self._inject.append([err, count])

    def handle(self, req: httpx.Request, consumer: str = "unknown") -> httpx.Response:
        now = self._now()
        p = {k: v for k, v in req.url.params.items()}
        tbl, prd = p.get("tblId", ""), p.get("prdSe", "")
        with self._lock:

            def reply(err: str, body: Any) -> httpx.Response:
                self.seen.append(KosisSeen(now, consumer, tbl, prd, err))
                return httpx.Response(200, text=json.dumps(body, ensure_ascii=False))

            if not req.url.path.endswith("/statisticsParameterData.do"):
                return httpx.Response(404, text="not found")
            if p.get("apiKey") != self.key:
                return reply("11", {"err": "11", "errMsg": "인증KEY가 유효하지 않습니다."})
            for rule in self._inject:
                if rule[1] > 0:
                    rule[1] -= 1
                    return reply(str(rule[0]), {"err": str(rule[0]), "errMsg": "합성 오류"})
            prd_de = p.get("endPrdDe") or p.get("startPrdDe") or "202609"
            s = int.from_bytes(hashlib.sha256(f"{tbl}|{prd_de}".encode()).digest()[:4], "big")
            row = {
                "ORG_ID": p.get("orgId", ""),
                "TBL_ID": tbl,
                "TBL_NM": "합성 통계표",
                "C1": p.get("objL1", ""),
                "C1_NM": "합성 분류",
                "ITM_ID": p.get("itmId", ""),
                "ITM_NM": "합성 항목",
                "UNIT_NM": "지수",
                "PRD_SE": prd,
                "PRD_DE": prd_de,
                "DT": f"{(s % 20_000) / 100:.1f}",
            }
            return reply("", [row])

    def calls(self, tbl_id: str | None = None) -> list[KosisSeen]:
        with self._lock:
            return [s for s in self.seen if tbl_id in (None, s.tbl_id)]


__all__ = ["KEY", "FakeKosis", "KosisSeen"]

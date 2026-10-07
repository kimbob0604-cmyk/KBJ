"""가짜 ECOS(한국은행 OpenAPI, `httpx.MockTransport`). 합성 데이터만.

경로 `/api/<서비스>/<키>/json/<언어>/<시작>/<끝>/<표>/<주기>/<기간 시작>/<기간 끝>/…`
(probe_results §4).

- `StatisticSearch`: 표마다 합성 한 행(TIME = 기간 시작, 값은 고정 시드). `no_data` 에 넣은
  표는 `INFO-200`(데이터 없음 — 어댑터는 빈 목록).
- `inject(code, count)`: `ERROR-602`(과도한 호출) 등 결과 봉투 `{"RESULT": {CODE, MESSAGE}}`.
- 키가 다르면 `INFO-100`(인증키 무효). 기록 `seen`(시각·consumer·서비스·표·주기·기간 —
  키는 뺀다).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from urllib.parse import unquote

import httpx

KEY: Final = "ECOSSIMFAKEKEY0123456789"


@dataclass(frozen=True)
class EcosSeen:
    at: datetime
    consumer: str
    service: str
    args: tuple[str, ...]  # 표·주기·기간 …(키 제외)
    code: str  # 결과 코드(정상은 "")


def _result(code: str, msg: str) -> str:
    return json.dumps({"RESULT": {"CODE": code, "MESSAGE": msg}}, ensure_ascii=False)


class FakeEcos:
    def __init__(self, now: Callable[[], datetime], *, key: str = KEY) -> None:
        self._now = now
        self.key = key
        self.no_data: set[str] = set()
        self.seen: list[EcosSeen] = []
        self._inject: list[list[Any]] = []
        self._lock = threading.Lock()

    def transport(self, consumer: str = "unknown") -> httpx.MockTransport:
        def handle(req: httpx.Request) -> httpx.Response:
            return self.handle(req, consumer)

        return httpx.MockTransport(handle)

    def inject(self, code: str, count: int = 1) -> None:
        with self._lock:
            self._inject.append([code, count])

    def handle(self, req: httpx.Request, consumer: str = "unknown") -> httpx.Response:
        now = self._now()
        seg = [unquote(s) for s in req.url.path.split("/") if s]
        # ["api", 서비스, 키, "json", 언어, 시작, 끝, *args]
        if len(seg) < 7 or seg[0] != "api":
            return httpx.Response(404, text="not found")
        service, key, args = seg[1], seg[2], tuple(seg[7:])
        with self._lock:

            def reply(code: str, body: str) -> httpx.Response:
                self.seen.append(EcosSeen(now, consumer, service, args, code))
                return httpx.Response(200, text=body, headers={"content-type": "application/json"})

            if key != self.key:
                return reply("INFO-100", _result("INFO-100", "인증키가 유효하지 않습니다."))
            for rule in self._inject:
                if rule[1] > 0:
                    rule[1] -= 1
                    return reply(str(rule[0]), _result(str(rule[0]), "과도한 OpenAPI호출"))
            if service != "StatisticSearch" or len(args) < 4:
                return reply("", json.dumps({service: {"list_total_count": 0, "row": []}}))
            stat, cycle, start = args[0], args[1], args[2]
            if stat in self.no_data:
                return reply("INFO-200", _result("INFO-200", "해당하는 데이터가 없습니다."))
            s = int.from_bytes(hashlib.sha256(f"{stat}|{start}".encode()).digest()[:4], "big")
            row = {
                "STAT_CODE": stat,
                "STAT_NAME": "합성 통계",
                "ITEM_CODE1": "SIM",
                "ITEM_NAME1": "합성 항목",
                "UNIT_NAME": "%",
                "WGT": "",
                "TIME": start,
                "DATA_VALUE": f"{(s % 10_000) / 100:.2f}",
                "CYCLE": cycle,
            }
            body = {"StatisticSearch": {"list_total_count": 1, "row": [row]}}
            return reply("", json.dumps(body, ensure_ascii=False))

    def calls(self, stat: str | None = None) -> list[EcosSeen]:
        with self._lock:
            return [s for s in self.seen if stat is None or (s.args and s.args[0] == stat)]


__all__ = ["KEY", "EcosSeen", "FakeEcos"]

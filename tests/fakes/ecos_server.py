"""가짜 ECOS(한국은행 OpenAPI, `httpx.MockTransport`). 합성 데이터만.

경로 `/api/<서비스>/<키>/json/<언어>/<시작>/<끝>/<표>/<주기>/<기간 시작>/<기간 끝>/…`
(probe_results §4).

- `StatisticSearch`: 한 기간(시작 = 끝)이면 표마다 합성 한 행(TIME = 기간, 값은 고정 시드).
  기간 범위(시작 < 끝)면 기간마다 한 행 — 지금 기준 공표된 기간까지만(일 D = 오늘, 월 M =
  지난달, 분기 Q = 지난 분기, 연 A = 지난해). 항목 코드를 주면 ITEM_CODE1 에 그대로 돌려준다
  (실제 ECOS 처럼 — 2026-10-20 config/macro.yaml 에 ECOS 항목 코드를 채워 수집기가 범위로
  부른다). `no_data` 에 넣은 표는 `INFO-200`(데이터 없음 — 어댑터는 빈 목록).
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
from datetime import date, datetime, timedelta
from typing import Any, Final
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx

KEY: Final = "ECOSSIMFAKEKEY0123456789"
KST: Final = ZoneInfo("Asia/Seoul")
MAX_ROWS: Final = 1000  # 범위 한 번에 돌려줄 행 상한(합성 — 끝없는 범위 방지)


@dataclass(frozen=True)
class EcosSeen:
    at: datetime
    consumer: str
    service: str
    args: tuple[str, ...]  # 표·주기·기간 …(키 제외)
    code: str  # 결과 코드(정상은 "")


def _published(cycle: str, today: date) -> str:
    """지금 기준 공표된 마지막 기간(합성 규칙 — 일 = 오늘, 월·분기·연 = 직전 기간)."""
    if cycle == "D":
        return f"{today:%Y%m%d}"
    if cycle == "M":
        prev = today.replace(day=1) - timedelta(days=1)
        return f"{prev:%Y%m}"
    if cycle == "Q":
        q = (today.month - 1) // 3 + 1
        return f"{today.year}Q{q - 1}" if q > 1 else f"{today.year - 1}Q4"
    return f"{today.year - 1}"


def _periods(cycle: str, start: str, end: str) -> list[str]:
    """ECOS 기간 표기 start..end(양끝 포함). 모르는 주기·형식이면 [start]."""
    out: list[str] = []
    try:
        if cycle == "D":
            d = date(int(start[:4]), int(start[4:6]), int(start[6:8]))
            last = date(int(end[:4]), int(end[4:6]), int(end[6:8]))
            while d <= last and len(out) < MAX_ROWS:
                out.append(f"{d:%Y%m%d}")
                d += timedelta(days=1)
        elif cycle == "M":
            y, m, ly, lm = int(start[:4]), int(start[4:6]), int(end[:4]), int(end[4:6])
            while (y, m) <= (ly, lm) and len(out) < MAX_ROWS:
                out.append(f"{y:04d}{m:02d}")
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        elif cycle == "Q":
            y, q, ly, lq = int(start[:4]), int(start[5:]), int(end[:4]), int(end[5:])
            while (y, q) <= (ly, lq) and len(out) < MAX_ROWS:
                out.append(f"{y:04d}Q{q}")
                y, q = (y + 1, 1) if q == 4 else (y, q + 1)
        elif cycle == "A":
            out = [str(y) for y in range(int(start), int(end) + 1)][:MAX_ROWS]
    except ValueError:
        return [start]
    return out or [start]


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
            stat, cycle, start, end = args[0], args[1], args[2], args[3]
            items = args[4:]
            if stat in self.no_data:
                return reply("INFO-200", _result("INFO-200", "해당하는 데이터가 없습니다."))
            if start == end:
                times = [start]
            else:
                cut = _published(cycle, now.astimezone(KST).date())
                times = [t for t in _periods(cycle, start, end) if t <= cut]
            if not times:
                return reply("INFO-200", _result("INFO-200", "해당하는 데이터가 없습니다."))
            rows = [self._row(stat, cycle, t, items) for t in times]
            body = {"StatisticSearch": {"list_total_count": len(rows), "row": rows}}
            return reply("", json.dumps(body, ensure_ascii=False))

    @staticmethod
    def _row(stat: str, cycle: str, time: str, items: tuple[str, ...]) -> dict[str, str]:
        s = int.from_bytes(hashlib.sha256(f"{stat}|{time}".encode()).digest()[:4], "big")
        return {
            "STAT_CODE": stat,
            "STAT_NAME": "합성 통계",
            "ITEM_CODE1": items[0] if items else "SIM",
            "ITEM_NAME1": "합성 항목",
            "UNIT_NAME": "%",
            "WGT": "",
            "TIME": time,
            "DATA_VALUE": f"{(s % 10_000) / 100:.2f}",
            "CYCLE": cycle,
        }

    def calls(self, stat: str | None = None) -> list[EcosSeen]:
        with self._lock:
            return [s for s in self.seen if stat is None or (s.args and s.args[0] == stat)]


__all__ = ["KEY", "EcosSeen", "FakeEcos"]

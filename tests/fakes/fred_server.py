"""가짜 FRED API(`httpx.MockTransport`) — 합성 값만(FRED 실응답·그것으로 만든 픽스처는 레포에 넣지
않는다 — 정부 시리즈는 공개 등급이지만 키 수령 전이라 지금은 합성, docs/p5_design.md §1.2).

경로
-
  `/fred/series/observations?series_id=…&observation_start=…&observation_end=…&api_key=…&file_type=json`
  → `{"observations": [{"date", "value"}]}`. 값은 고정 시드(시리즈·날짜 해시) — 같은 입력이면 같은
  값.
  일별(D)은 평일, 월별(M)은 매달 1일, 분기(Q)는 분기 첫날. 끝 = 지금(뉴욕) 전날(공표 지연).
- `/fred/releases/dates?realtime_start=…&realtime_end=…` → `{"release_dates": [...]}` — 합성 일정
  (`RELEASES` — 매달 같은 날 규칙).

- 키가 `key` 와 다르면 400 `{"error_code": 400, "error_message": "Bad Request. The value for
  variable
  api_key is not registered…"}`. 모르는 시리즈 → 400 '… series does not exist.'
- `missing={(시리즈, 'YYYY-MM-DD')}` → 그날 값 '.'(결측). `inject(429, n)`·`inject(500)`·
  `inject("garbage")`(숫자가 아닌 값).
- 기록 `seen`(시각·경로·series_id·상태) — 키는 기록하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

import httpx

NY: Final = ZoneInfo("America/New_York")
FAKE_KEY: Final = "fake-fred-key-0123456789"
# 시리즈 id → (기준값, 주기) — 값은 합성(config/macro.yaml 의 FRED id 그대로)
DEFAULT_SERIES: Final[dict[str, tuple[float, str]]] = {
    "DGS10": (4.20, "D"), "DGS2": (3.90, "D"), "DGS3MO": (4.10, "D"), "T10Y2Y": (0.30, "D"),
    "DFF": (4.33, "D"), "CPIAUCSL": (320.0, "M"), "PCEPILFE": (125.0, "M"), "UNRATE": (4.2, "M"),
    "PAYEMS": (159000.0, "M"), "GDPC1": (23500.0, "Q"),
    "SP500": (5600.0, "D"), "VIXCLS": (16.0, "D"), "BAMLH0A0HYM2": (3.1, "D"),
}  # fmt: skip
# 합성 발표 일정 — (release_id, 이름, 매달 날짜)
RELEASES: Final[tuple[tuple[int, str, int], ...]] = (
    (10, "Consumer Price Index", 13),
    (50, "Employment Situation", 5),
    (53, "Gross Domestic Product", 27),
    (54, "Personal Income and Outlays", 28),
    (9999, "Synthetic Release Without Mapping", 20),
)


class Injector:
    """`inject(무엇, 몇 번)` 대기열(가짜 MAC 서버 넷이 같이 쓴다)."""

    def __init__(self) -> None:
        self._q: list[list[Any]] = []
        self._lock = threading.Lock()

    def inject(self, what: int | str, count: int = 1) -> None:
        with self._lock:
            self._q.append([what, count])

    def take(self) -> int | str | None:
        with self._lock:
            if not self._q:
                return None
            what, n = self._q[0]
            if n <= 1:
                self._q.pop(0)
            else:
                self._q[0][1] = n - 1
            return what


def wiggle(name: str, d: date, scale: float = 0.01) -> float:
    h = hashlib.sha256(f"{name}:{d.isoformat()}".encode()).digest()
    return (h[0] - 128) / 128 * scale


def periods(freq: str, start: date, end: date) -> list[date]:
    out: list[date] = []
    if freq == "D":
        d = start
        while d <= end:
            if d.weekday() < 5:
                out.append(d)
            d += timedelta(days=1)
        return out
    step = 1 if freq == "M" else 3
    y, m = start.year, start.month
    if freq == "Q":
        m = (m - 1) // 3 * 3 + 1
    d = date(y, m, 1)
    while d <= end:
        if d >= start:
            out.append(d)
        m += step
        y, m = (y + 1, m - 12) if m > 12 else (y, m)
        d = date(y, m, 1)
    return out


@dataclass(frozen=True)
class FredSeen:
    at: datetime
    path: str
    series_id: str
    status: int


class FakeFred:
    def __init__(
        self,
        now: Callable[[], datetime],
        *,
        series: Mapping[str, tuple[float, str]] | None = None,
        key: str = FAKE_KEY,
        missing: Iterable[tuple[str, str]] = (),
    ) -> None:
        self._now = now
        self.series = dict(DEFAULT_SERIES if series is None else series)
        self.key = key
        self.missing = set(missing)
        self.seen: list[FredSeen] = []
        self.injector = Injector()
        self.inject = self.injector.inject

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def last_day(self) -> date:
        return self._now().astimezone(NY).date() - timedelta(days=1)

    def values(self, sid: str, start: date, end: date) -> list[tuple[date, float]]:
        base, freq = self.series[sid]
        out: list[tuple[date, float]] = []
        for d in periods(freq, start, min(end, self.last_day())):
            out.append((d, round(base * (1 + wiggle(sid, d)), 4)))
        return out

    def handle(self, req: httpx.Request) -> httpx.Response:
        p = req.url.params
        path = req.url.path
        sid = p.get("series_id", "")
        inj = self.injector.take()

        def reply(status: int, body: Any) -> httpx.Response:
            self.seen.append(FredSeen(self._now().astimezone(UTC), path, sid, status))
            return httpx.Response(status, text=json.dumps(body))

        def bad(msg: str) -> httpx.Response:
            return reply(400, {"error_code": 400, "error_message": msg})

        if inj == 429:
            return reply(429, {"error_code": 429, "error_message": "Too Many Requests."})
        if isinstance(inj, int):
            return reply(inj, {"error_code": inj, "error_message": "Internal Server Error"})
        if p.get("api_key") != self.key:
            return bad(
                "Bad Request.  The value for variable api_key is not registered.  Read "
                "https://fred.stlouisfed.org/docs/api/api_key.html for more information."
            )
        if p.get("file_type") != "json":
            return bad("Bad Request. file_type must be json")
        if path == "/fred/series/observations":
            if sid not in self.series:
                return bad("Bad Request.  The series does not exist.")
            end = date.fromisoformat(p.get("observation_end", "9999-12-31"))
            start = date.fromisoformat(
                p.get("observation_start", (self.last_day() - timedelta(days=400)).isoformat())
            )
            obs = [
                {"realtime_start": "x", "realtime_end": "x", "date": d.isoformat(),
                 "value": "." if (sid, d.isoformat()) in self.missing else f"{v:.4f}"}
                for d, v in self.values(sid, start, end)
            ]  # fmt: skip
            if inj == "garbage" and obs:
                obs[-1]["value"] = "n/a?"
            return reply(200, {"observations": obs})
        if path == "/fred/releases/dates":
            start = date.fromisoformat(p.get("realtime_start", ""))
            end = date.fromisoformat(p.get("realtime_end", ""))
            rows: list[dict[str, Any]] = []
            d = start
            while d <= end:
                for rid, name, dom in RELEASES:
                    if d.day == dom:
                        rows.append(
                            {"release_id": rid, "release_name": name, "date": d.isoformat()}
                        )
                d += timedelta(days=1)
            return reply(200, {"release_dates": rows})
        return reply(404, {"error_code": 404, "error_message": "Not Found"})

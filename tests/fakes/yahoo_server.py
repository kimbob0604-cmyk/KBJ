"""가짜 Yahoo v8 chart(`httpx.MockTransport`). 합성 데이터만(Yahoo 원응답·그것으로 만든 픽스처는
레포에
넣지 않는다 — CLAUDE.md §2). MAC(매크로 12종)과 US(미국 지수)가 같이 쓴다(docs/p5_design.md §1.1).

경로 `/v8/finance/chart/<심볼>?range=…&interval=1d`.

- 아는 심볼(`symbols` — 기본 `DEFAULT_SYMBOLS`)은 고정 시드로 만든 일봉을 `end`(기본 오늘 뉴욕 날짜)
  까지 평일만 준다. 봉 시각은 그날 09:30 ET(= 정규장 시작, Yahoo 일봉과 같은 모양).
- `live=True` 면 정규장이 열려 있는 것처럼 `meta.currentTradingPeriod.regular` 이 오늘을 감싸고
  `regularMarketTime` 이 지금 — 마지막 봉이 **장중 봉**(어댑터는 estimated 로 낸다).
- 모르는 심볼 → 404 `{"chart": {"result": null, "error": {"code": "Not Found", …}}}`.
- `inject(429, n)` → 다음 n 번 429. `inject("empty", n)` → 결과 빈 목록(200). `inject("null_close")`
  → 첫 봉 종가 null(어댑터는 버린다).
- 기록 `seen`(시각·consumer·심볼·range·결과 상태).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx

NY: Final = ZoneInfo("America/New_York")
# 매크로 12종(SD _MACRO_TICKERS 이름 그대로 — 값은 합성) + 미국 지수
DEFAULT_SYMBOLS: Final[dict[str, float]] = {
    "KRW=X": 1380.0, "JPY=X": 150.0, "EURUSD=X": 1.08, "CNY=X": 7.2, "CL=F": 78.0,
    "GC=F": 2400.0, "SI=F": 29.0, "HG=F": 4.3, "NG=F": 2.6, "^TNX": 4.2, "^VIX": 15.0,
    "BTC-USD": 60000.0, "^GSPC": 5600.0, "^IXIC": 17800.0, "^DJI": 41000.0, "^RUT": 2200.0,
}  # fmt: skip
_RANGE_DAYS: Final = {
    "1d": 1, "5d": 5, "1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731, "5y": 1827,
}  # fmt: skip


@dataclass(frozen=True)
class YahooSeen:
    at: datetime
    consumer: str
    symbol: str
    range_: str
    status: int


def _wiggle(symbol: str, d: date) -> float:
    h = hashlib.sha256(f"{symbol}:{d.isoformat()}".encode()).digest()
    return (h[0] - 128) / 128 * 0.01  # ±1% 고정 시드


def _epoch(d: date, t: time) -> int:
    return int(datetime.combine(d, t, tzinfo=NY).timestamp())


class FakeYahoo:
    def __init__(
        self,
        now: Callable[[], datetime],
        *,
        symbols: dict[str, float] | None = None,
        live: bool = False,
    ) -> None:
        self._now = now
        self.symbols = dict(DEFAULT_SYMBOLS if symbols is None else symbols)
        self.live = live
        self.seen: list[YahooSeen] = []
        self._inject: list[list[Any]] = []
        self._lock = threading.Lock()

    def transport(self, consumer: str = "unknown") -> httpx.MockTransport:
        def handle(req: httpx.Request) -> httpx.Response:
            return self.handle(req, consumer)

        return httpx.MockTransport(handle)

    def inject(self, what: int | str, count: int = 1) -> None:
        with self._lock:
            self._inject.append([what, count])

    def _take(self) -> int | str | None:
        with self._lock:
            if not self._inject:
                return None
            what, n = self._inject[0]
            if n <= 1:
                self._inject.pop(0)
            else:
                self._inject[0][1] = n - 1
            return what

    def bars(self, symbol: str, end: date, days: int) -> list[tuple[date, float]]:
        """평일 종가 (날짜, 값) — 같은 심볼·날짜면 언제나 같은 값."""
        base = self.symbols[symbol]
        out: list[tuple[date, float]] = []
        d = end - timedelta(days=days)
        level = base
        while d <= end:
            if d.weekday() < 5:
                level = round(level * (1 + _wiggle(symbol, d)), 4)
                out.append((d, level))
            d += timedelta(days=1)
        return out

    def payload(self, symbol: str, range_: str, *, inject: int | str | None = None) -> Any:
        now = self._now().astimezone(NY)
        end = now.date()
        rows = [] if inject == "empty" else self.bars(symbol, end, _RANGE_DAYS.get(range_, 731))
        ts = [_epoch(d, time(9, 30)) for d, _ in rows]
        closes: list[float | None] = [c for _, c in rows]
        if inject == "null_close" and closes:
            closes[0] = None
        start, close_t = _epoch(end, time(9, 30)), _epoch(end, time(16, 0))
        regular_time = int(now.timestamp()) if self.live else close_t
        meta = {
            "symbol": symbol, "regularMarketTime": regular_time,
            "currentTradingPeriod": {"regular": {"start": start, "end": close_t}},
        }  # fmt: skip
        quote = {
            "open": closes, "high": [None if c is None else c * 1.01 for c in closes],
            "low": [None if c is None else c * 0.99 for c in closes], "close": closes,
            "volume": [1000 + i for i in range(len(closes))],
        }  # fmt: skip
        result = {"meta": meta, "timestamp": ts, "indicators": {"quote": [quote]}}
        return {"chart": {"result": [result], "error": None}}

    def handle(self, req: httpx.Request, consumer: str = "unknown") -> httpx.Response:
        path = unquote(req.url.path)
        symbol = path.rsplit("/", 1)[-1]
        range_ = req.url.params.get("range", "")
        inject = self._take()

        def reply(status: int, body: Any) -> httpx.Response:
            self.seen.append(
                YahooSeen(self._now().astimezone(UTC), consumer, symbol, range_, status)
            )
            return httpx.Response(status, text=json.dumps(body))

        if not path.startswith("/v8/finance/chart/"):
            return reply(404, {"error": "unknown path"})
        if inject == 429:
            return reply(429, {"chart": {"result": None, "error": {"code": "Too Many Requests"}}})
        if symbol not in self.symbols:
            err = {"code": "Not Found", "description": "No data found, symbol may be delisted"}
            return reply(404, {"chart": {"result": None, "error": err}})
        return reply(200, self.payload(symbol, range_, inject=inject))

    def calls(self, symbol: str | None = None) -> list[YahooSeen]:
        return [s for s in self.seen if symbol is None or s.symbol == symbol]

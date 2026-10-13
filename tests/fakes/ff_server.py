"""가짜 ForexFactory 주간 피드(`httpx.MockTransport`) — **합성만**(로그인 등급 원자료·예상치는
레포에
넣지 않는다 — 사용자 결정 2026-10-13 Q3). 경로 `/ff_calendar_thisweek.json` → 배열
`[{"title", "country", "date"(ISO·시간대), "impact", "forecast", "previous"}]` [확인 필요 #49].

이번 주(뉴욕 기준 월~금)에 합성 행을 둔다: 미 CPI(High·예상치 %)·비농업 고용(High·K)·한국 금통위
(KRW·High·예상치 %)·종일 일정(00:00)·Holiday(버려짐)·모르는 통화(GBP — 버려짐)·숫자가 아닌 예상치.
`inject(429)`·`inject(500)`·`inject("not_list")`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx

from tests.fakes.fred_server import NY, Injector


class FakeFf:
    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now
        self.injector = Injector()
        self.inject = self.injector.inject
        self.calls: list[int] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def rows(self) -> list[dict[str, Any]]:
        today = self._now().astimezone(NY).date()
        monday = today - timedelta(days=today.weekday())

        def at(day: int, hh: int, mm: int) -> str:
            d = monday + timedelta(days=day)
            return datetime(d.year, d.month, d.day, hh, mm, tzinfo=NY).isoformat()

        return [
            {"title": "CPI m/m", "country": "USD", "date": at(2, 8, 30), "impact": "High",
             "forecast": "0.3%", "previous": "0.4%"},
            {"title": "Non-Farm Employment Change", "country": "USD", "date": at(4, 8, 30),
             "impact": "High", "forecast": "150K", "previous": "142K"},
            {"title": "BOK Rate Decision", "country": "KRW", "date": at(3, 21, 0), "impact": "High",
             "forecast": "2.50%", "previous": "2.50%"},
            {"title": "Bank Holiday", "country": "USD", "date": at(0, 0, 0), "impact": "Holiday",
             "forecast": "", "previous": ""},
            {"title": "Synthetic All Day Event", "country": "CNY", "date": at(1, 0, 0),
             "impact": "Low", "forecast": "", "previous": ""},
            {"title": "GBP Synthetic", "country": "GBP", "date": at(1, 2, 0), "impact": "High",
             "forecast": "1.0%", "previous": "0.9%"},
            {"title": "Retail Sales m/m", "country": "USD", "date": at(3, 8, 30),
             "impact": "Medium", "forecast": "tbd", "previous": "0.1%"},
        ]  # fmt: skip

    def handle(self, req: httpx.Request) -> httpx.Response:
        inj = self.injector.take()

        def reply(status: int, body: Any) -> httpx.Response:
            self.calls.append(status)
            return httpx.Response(status, text=json.dumps(body))

        if inj == 429:
            return reply(429, {"error": "rate"})
        if isinstance(inj, int):
            return reply(inj, {"error": "server"})
        if req.url.path != "/ff_calendar_thisweek.json":
            return reply(404, {"error": "Not Found"})
        if inj == "not_list":
            return reply(200, {"oops": True})
        return reply(200, self.rows())

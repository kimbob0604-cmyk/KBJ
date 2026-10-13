"""가짜 뉴욕연은 기준금리 API(`httpx.MockTransport`) — 합성 값만.

경로 `/api/rates/<secured|unsecured>/<sofr|effr|…>/search.json?startDate=…&endDate=…` →
`{"refRates": [{"effectiveDate", "type", "percentRate", "volumeInBillions"}]}`(최근 날짜가 위 [실측
필요
#45]). 평일만, 끝 = 지금(뉴욕) 전날. `null_on={'YYYY-MM-DD'}` → percentRate null.
`inject(429)`·`inject(500)`·`inject("garbage")`(percentRate 가 문자열).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from typing import Any, Final

import httpx

from tests.fakes.fred_server import NY, Injector, periods, wiggle

RATES: Final[dict[str, tuple[str, float]]] = {
    "sofr": ("secured", 4.31), "bgcr": ("secured", 4.29), "tgcr": ("secured", 4.28),
    "effr": ("unsecured", 4.33), "obfr": ("unsecured", 4.32),
}  # fmt: skip


class FakeNyfed:
    def __init__(self, now: Callable[[], datetime], *, null_on: Iterable[str] = ()) -> None:
        self._now = now
        self.null_on = set(null_on)
        self.injector = Injector()
        self.inject = self.injector.inject
        self.calls: list[tuple[str, int]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        inj = self.injector.take()

        def reply(status: int, body: Any) -> httpx.Response:
            self.calls.append((path, status))
            return httpx.Response(status, text=json.dumps(body))

        if inj == 429:
            return reply(429, {"error": "Too Many Requests"})
        if isinstance(inj, int):
            return reply(inj, {"error": "Server Error"})
        parts = path.strip("/").split("/")
        if len(parts) != 5 or parts[:2] != ["api", "rates"] or parts[4] != "search.json":
            return reply(404, {"error": "Not Found"})
        group, rate = parts[2], parts[3]
        if rate not in RATES or RATES[rate][0] != group:
            return reply(400, {"error": "unknown rate"})
        start = date.fromisoformat(req.url.params.get("startDate", ""))
        end = min(
            date.fromisoformat(req.url.params.get("endDate", "")),
            self._now().astimezone(NY).date() - timedelta(days=1),
        )
        base = RATES[rate][1]
        rows: list[dict[str, Any]] = []
        for d in reversed(periods("D", start, end)):
            v: Any = (
                None
                if d.isoformat() in self.null_on
                else round(base * (1 + wiggle(rate, d, 0.002)), 2)
            )
            if inj == "garbage" and not rows:
                v = "n/a"
            rows.append({"effectiveDate": d.isoformat(), "type": rate.upper(), "percentRate": v,
                         "volumeInBillions": 2000})  # fmt: skip
        return reply(200, {"refRates": rows})

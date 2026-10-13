"""가짜 미 재무부 일별 수익률 곡선 CSV(`httpx.MockTransport`) — 합성 값만(실응답을 레포에 넣지
않는다). 경로
`/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/<연도>/all`.

- 열 `Date`(MM/DD/YYYY) + `TENORS`. 평일만, 끝 = 지금(뉴욕) 전날. 최근 날짜가 위(실 CSV 와 같은
  내림차순 [실측 필요 #45]). `blank={('MM/DD/YYYY', '열')}` → 빈 칸.
- `inject(429)`·`inject(500)`·`inject("no_date")`(Date 열 없음 — 형식 바뀜).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from typing import Final

import httpx

from tests.fakes.fred_server import NY, Injector, periods, wiggle

TENORS: Final[dict[str, float]] = {
    "1 Mo": 4.30, "3 Mo": 4.20, "6 Mo": 4.10, "1 Yr": 3.95, "2 Yr": 3.80, "5 Yr": 3.85,
    "10 Yr": 4.15, "30 Yr": 4.60,
}  # fmt: skip
PATH_PREFIX: Final = "/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"


class FakeTreasury:
    def __init__(
        self, now: Callable[[], datetime], *, blank: Iterable[tuple[str, str]] = ()
    ) -> None:
        self._now = now
        self.blank = set(blank)
        self.injector = Injector()
        self.inject = self.injector.inject
        self.calls: list[tuple[str, int]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def csv_for(self, year: int, *, with_date: bool = True) -> str:
        end = min(date(year, 12, 31), self._now().astimezone(NY).date() - timedelta(days=1))
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["Date" if with_date else "Day", *TENORS])
        for d in reversed(periods("D", date(year, 1, 1), end)):
            day = d.strftime("%m/%d/%Y")
            w.writerow([day, *("" if (day, t) in self.blank else f"{b * (1 + wiggle(t, d)):.2f}"
                               for t, b in TENORS.items())])  # fmt: skip
        return buf.getvalue()

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        inj = self.injector.take()

        def reply(status: int, text: str) -> httpx.Response:
            self.calls.append((path, status))
            return httpx.Response(status, text=text)

        if inj == 429:
            return reply(429, "Too Many Requests")
        if isinstance(inj, int):
            return reply(inj, "Server Error")
        if not path.startswith(PATH_PREFIX) or not path.endswith("/all"):
            return reply(404, "Not Found")
        if req.url.params.get("type") != "daily_treasury_yield_curve":
            return reply(400, "bad type")
        year = int(path[len(PATH_PREFIX) :].split("/", 1)[0])
        return reply(200, self.csv_for(year, with_date=inj != "no_date"))

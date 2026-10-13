"""미 재무부 일별 수익률 곡선(Daily Treasury Par Yield Curve Rates) — 공개(정부 공표 자료,
DATA_TIERS §1 행 추가 요청 — 사용자 결정 2026-10-13 Q8).

다시 만든 원본 기능: bok `fetch-rates.js`(코드 없음 — docs/inventory.md 기능 기준, U3).

- 주소 `/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/<연도>/all`
  (`type=daily_treasury_yield_curve`·`field_tdr_date_value=<연도>`·`_format=csv`) — CSV 한 해치.
  열 `Date`(MM/DD/YYYY) + 만기 열(`1 Mo`·`3 Mo`·`2 Yr`·`10 Yr`·`30 Yr` …). 경로·열 이름 [실측 필요
  #45].
- **받아서 정규화만**: (날짜, 만기 열 이름, 값). 만기 열 이름이 config/macro.yaml 의 `item`
  이다(실측 뒤
  MAC 이 채움 — 지금 null). 빈 칸 = None(행은 남긴다 — 0 으로 채우지 않는다). 숫자가 아니면 실패.
- 키 없음. 리미터 `treasury`(1/s [제안]). 429 → 감속 + `TreasuryThrottled`.
"""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import httpx
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.quality import Quality
from kbj.data.http import FetchError, get_capped, make_client
from kbj.data.limits import Limits, load_limits
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RedisRateLimiter

__all__ = [
    "TreasuryClient",
    "TreasuryError",
    "TreasuryThrottled",
    "YieldPoint",
    "parse_yield_csv",
]

SOURCE: Final = "TREASURY"
LIMITS_SOURCE: Final = "treasury"
_BASE: Final = "https://home.treasury.gov"
_PATH: Final = (
    "/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all"
)
TR_ID: Final = "treasury:yield_curve"
MAX_BYTES: Final = 4 * 1024 * 1024
TOTAL_S: Final = 60.0
DATE_COL: Final = "Date"


class TreasuryError(FetchError):
    def __init__(self, dataset: str, status: int | None, reason: str) -> None:
        super().__init__(SOURCE, dataset, status, reason)


class TreasuryThrottled(TreasuryError):
    """429 — 감속했다."""


@dataclass(frozen=True)
class YieldPoint:
    date: date
    tenor: str  # 열 이름 그대로(예 '10 Yr')
    value: Decimal | None

    @property
    def quality(self) -> Quality:
        return Quality.OK if self.value is not None else Quality.INVALID


def _num(raw: str) -> Decimal | None:
    s = raw.strip()
    if s in ("", "N/A"):
        return None
    try:
        v = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"수익률 값이 숫자가 아니다: {s[:20]!r}") from None
    if not v.is_finite():
        raise ValueError(f"수익률 값이 유한수가 아니다: {s[:20]!r}")
    return v


def parse_yield_csv(text: str) -> list[YieldPoint]:
    """CSV → (날짜·만기) 오름차순. `Date` 열이 없으면 ValueError(형식 바뀜)."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or DATE_COL not in reader.fieldnames:
        raise ValueError("Date 열이 없다")
    tenors = [f for f in reader.fieldnames if f != DATE_COL]
    out: list[YieldPoint] = []
    for row in reader:
        raw_day = (row.get(DATE_COL) or "").strip()
        if not raw_day:
            continue
        d = datetime.strptime(raw_day, "%m/%d/%Y").date()  # noqa: DTZ007 — 날짜만 쓴다
        for t in tenors:
            out.append(YieldPoint(d, t.strip(), _num(row.get(t) or "")))
    out.sort(key=lambda p: (p.date, p.tenor))
    return out


class TreasuryClient:
    """`TreasuryClient(limiter).yield_curve(2026) -> list[YieldPoint]`. 시험은 `transport=` 에 가짜
    서버."""

    def __init__(
        self,
        limiter: RateLimiter,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = _BASE,
        priority: Priority = Priority.P3,
        acquire_timeout_s: float | None = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limiter = limiter
        self._priority = priority
        self._acquire_timeout = acquire_timeout_s
        self._clock = clock
        self._http = make_client(base_url, transport=transport)

    def __repr__(self) -> str:
        return "TreasuryClient()"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> TreasuryClient:
        src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
        limiter = RedisRateLimiter.scoped(
            redis, LIMITS_SOURCE, "yield_curve", src.rate_config(), clock
        )
        return cls(limiter, **kw)

    def close(self) -> None:
        self._http.close()

    def yield_curve(self, year: int) -> list[YieldPoint]:
        if not 1990 <= year <= 2100:
            raise ValueError(f"연도 범위: {year!r}")
        dataset = f"yield_curve:{year}"
        params = {
            "type": "daily_treasury_yield_curve",
            "field_tdr_date_value": str(year),
            "_format": "csv",
        }
        self._limiter.acquire(self._priority, TR_ID, self._acquire_timeout)
        try:
            status, body = get_capped(
                self._http, _PATH.format(year=year), params=params, max_bytes=MAX_BYTES,
                total_s=TOTAL_S, clock=self._clock, source=SOURCE, dataset=dataset,
            )  # fmt: skip
        except httpx.HTTPError as e:
            raise TreasuryError(dataset, None, f"전송 실패: {type(e).__name__}") from None
        if status == 429:
            self._limiter.on_rate_limited()
            raise TreasuryThrottled(dataset, status, "429 한도 초과 — 감속")
        if status != 200:
            raise TreasuryError(
                dataset, status, f"HTTP {status} {body[:200].decode('utf-8', 'replace')}"
            )
        try:
            return parse_yield_csv(body.decode("utf-8", "replace"))
        except ValueError as e:
            raise TreasuryError(dataset, status, f"응답 형식: {e}") from None

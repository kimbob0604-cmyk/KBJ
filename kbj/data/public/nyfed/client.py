"""뉴욕연은 기준금리(SOFR·EFFR 등) — 공개(정부 기관 공표 자료, DATA_TIERS §1 행 추가 요청 —
사용자 결정 2026-10-13 Q8).

다시 만든 원본 기능: bok `fetch-rates.js`(코드 없음 — docs/inventory.md 기능 기준, U3).

- 주소 `/api/rates/<묶음>/<금리>/search.json?startDate=…&endDate=…` — 묶음은 secured(SOFR·BGCR·TGCR)
  ·unsecured(EFFR·OBFR). 응답 `{"refRates": [{"effectiveDate", "type", "percentRate", …}]}`.
  경로·칸 이름 [실측 필요 #45]. config/macro.yaml `item` 은 금리 코드 소문자(예 `sofr`)다(실측 뒤
  채움).
- 값이 없거나 null → None(행은 남긴다). 숫자가 아니면 실패. 키 없음. 리미터 `nyfed`(1/s [제안]).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import httpx
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.quality import Quality
from kbj.data.http import FetchError, get_capped, make_client
from kbj.data.limits import Limits, load_limits
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RedisRateLimiter

__all__ = ["RATE_GROUPS", "NyfedClient", "NyfedError", "NyfedThrottled", "RatePoint", "parse_rates"]

SOURCE: Final = "NYFED"
LIMITS_SOURCE: Final = "nyfed"
_BASE: Final = "https://markets.newyorkfed.org"
_PATH: Final = "/api/rates/{group}/{rate}/search.json"
TR_ID: Final = "nyfed:rates"
MAX_BYTES: Final = 4 * 1024 * 1024
TOTAL_S: Final = 30.0
RATE_GROUPS: Final[Mapping[str, str]] = {
    "sofr": "secured",
    "bgcr": "secured",
    "tgcr": "secured",
    "effr": "unsecured",
    "obfr": "unsecured",
}


class NyfedError(FetchError):
    def __init__(self, dataset: str, status: int | None, reason: str) -> None:
        super().__init__(SOURCE, dataset, status, reason)


class NyfedThrottled(NyfedError):
    """429 — 감속했다."""


@dataclass(frozen=True)
class RatePoint:
    rate: str  # 소문자 코드(sofr·effr …)
    date: date
    value: Decimal | None  # %

    @property
    def quality(self) -> Quality:
        return Quality.OK if self.value is not None else Quality.INVALID


def _num(raw: object) -> Decimal | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise ValueError("percentRate 가 bool 이다")
    try:
        v = Decimal(str(raw))
    except InvalidOperation:
        raise ValueError(f"percentRate 가 숫자가 아니다: {str(raw)[:20]!r}") from None
    if not v.is_finite():
        raise ValueError("percentRate 가 유한수가 아니다")
    return v


def parse_rates(rate: str, payload: Any) -> list[RatePoint]:
    rows = payload.get("refRates") if isinstance(payload, Mapping) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(rows, list):
        raise ValueError("refRates 배열이 없다")
    out: list[RatePoint] = []
    for r in rows:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(r, Mapping):
            raise ValueError("refRates 항목이 객체가 아니다")
        typ = str(r.get("type", rate)).lower()  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        if typ != rate:
            continue  # 다른 금리 줄은 버린다(같은 묶음 응답에 섞일 수 있다 [확인 필요])
        d = date.fromisoformat(str(r.get("effectiveDate", "")))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        out.append(RatePoint(rate, d, _num(r.get("percentRate"))))  # pyright: ignore[reportUnknownMemberType]
    out.sort(key=lambda p: p.date)
    return out


class NyfedClient:
    """`NyfedClient(limiter).rates('sofr', start, end) -> list[RatePoint]`."""

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
        return "NyfedClient()"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> NyfedClient:
        src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
        limiter = RedisRateLimiter.scoped(redis, LIMITS_SOURCE, "rates", src.rate_config(), clock)
        return cls(limiter, **kw)

    def close(self) -> None:
        self._http.close()

    def rates(self, rate: str, start: date, end: date) -> list[RatePoint]:
        group = RATE_GROUPS.get(rate)
        if group is None:
            raise ValueError(f"모르는 금리 코드: {rate!r} (있는 것: {', '.join(RATE_GROUPS)})")
        if end < start:
            raise ValueError("end 가 start 보다 앞이다")
        dataset = f"rates:{rate}"
        params = {"startDate": start.isoformat(), "endDate": end.isoformat()}
        self._limiter.acquire(self._priority, TR_ID, self._acquire_timeout)
        try:
            status, body = get_capped(
                self._http, _PATH.format(group=group, rate=rate), params=params,
                max_bytes=MAX_BYTES, total_s=TOTAL_S, clock=self._clock, source=SOURCE,
                dataset=dataset,
            )  # fmt: skip
        except httpx.HTTPError as e:
            raise NyfedError(dataset, None, f"전송 실패: {type(e).__name__}") from None
        if status == 429:
            self._limiter.on_rate_limited()
            raise NyfedThrottled(dataset, status, "429 한도 초과 — 감속")
        if status != 200:
            raise NyfedError(
                dataset, status, f"HTTP {status} {body[:200].decode('utf-8', 'replace')}"
            )
        try:
            return parse_rates(rate, json.loads(body))
        except ValueError as e:
            raise NyfedError(dataset, status, f"응답 형식: {e}") from None

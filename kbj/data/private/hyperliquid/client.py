"""Hyperliquid info 클라이언트 — `metaAndAssetCtxs`(dex 하나) 한 건.

- 받아서 정규화만: {이름: PerpCtx}. 판정·환산은 엔진(`kbj.engines.market.global_proxy`).
- 실패를 삼키지 않는다: 200 이 아니거나 모양이 다르면 `HyperliquidError`(사유 포함).
- 못 받은 값은 None. 표시 가격(markPx)이 없거나 0 이하인 행은 버린다.
- 리미터 limits.yaml `hyperliquid`. 키 없는 출처.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any, Final

import httpx
from redis import Redis

from kbj.config.settings import Settings
from kbj.data.http import FetchError, make_client
from kbj.data.limits import Limits, load_limits
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RedisRateLimiter

SOURCE: Final = "HYPERLIQUID"
LIMITS_SOURCE: Final = "hyperliquid"
ROW_SOURCE: Final = "hyperliquid"
_BASE: Final = "https://api.hyperliquid.xyz"
TR_ID: Final = "hyperliquid:info"
DEX: Final = "xyz"
MAX_BYTES: Final = 8 * 1024 * 1024


class HyperliquidError(FetchError):
    def __init__(self, status: int | None, reason: str) -> None:
        super().__init__(SOURCE, "perp_ctx", status, reason)


@dataclass(frozen=True)
class PerpCtx:
    name: str
    mark: float
    prev_day: float | None
    funding_1h: float | None  # 1시간 펀딩률(소수 — 0.0001 = 0.01%)
    open_interest: float | None
    vol_24h: float | None  # 24시간 거래대금(달러)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # NaN 버림


def parse_perp_ctx(payload: Any, names: Collection[str] | None = None) -> dict[str, PerpCtx]:
    """[meta, ctxs] → {이름: PerpCtx}. 모양이 다르면 ValueError."""
    if not isinstance(payload, list) or len(payload) != 2:
        raise ValueError("metaAndAssetCtxs 모양이 아니다")
    meta, ctxs = payload
    universe = (meta or {}).get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(ctxs, list):
        raise ValueError("universe·ctxs 가 목록이 아니다")
    out: dict[str, PerpCtx] = {}
    for a, c in zip(universe, ctxs, strict=False):
        name = (a or {}).get("name") if isinstance(a, dict) else None
        if not name or (names is not None and name not in names) or not isinstance(c, dict):
            continue
        mark = _f(c.get("markPx"))
        if mark is None or mark <= 0:
            continue
        out[name] = PerpCtx(
            name=name,
            mark=mark,
            prev_day=_f(c.get("prevDayPx")),
            funding_1h=_f(c.get("funding")),
            open_interest=_f(c.get("openInterest")),
            vol_24h=_f(c.get("dayNtlVlm")),
        )
    return out


class HyperliquidClient:
    """`HyperliquidClient(limiter).perp_ctx(names) -> {이름: PerpCtx}`. 시험은 `transport=`."""

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
        self._http = make_client(base_url, transport=transport, headers={"User-Agent": "kbj"})

    def __repr__(self) -> str:
        return "HyperliquidClient()"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> HyperliquidClient:
        src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
        limiter = RedisRateLimiter.scoped(redis, LIMITS_SOURCE, "info", src.rate_config(), clock)
        return cls(limiter, **kw)

    def close(self) -> None:
        self._http.close()

    def perp_ctx(self, names: Collection[str] | None = None, dex: str = DEX) -> dict[str, PerpCtx]:
        self._limiter.acquire(self._priority, TR_ID, self._acquire_timeout)
        try:
            r = self._http.post("/info", json={"type": "metaAndAssetCtxs", "dex": dex})
        except httpx.HTTPError as e:
            raise HyperliquidError(None, f"전송 실패: {type(e).__name__}") from None
        if r.status_code == 429:
            self._limiter.on_rate_limited()
            raise HyperliquidError(429, "429 한도 초과 — 감속")
        if r.status_code != 200:
            raise HyperliquidError(r.status_code, f"HTTP {r.status_code}")
        if len(r.content) > MAX_BYTES:
            raise HyperliquidError(r.status_code, f"응답이 {MAX_BYTES}B 보다 크다")
        try:
            return parse_perp_ctx(json.loads(r.content), names)
        except ValueError as e:
            raise HyperliquidError(r.status_code, str(e)[:120]) from None

"""FRED **저작권 시리즈** — 로그인 등급(DATA_TIERS §1, D-P5-6).

FRED 가 다시 내보내는 시리즈 중 원저작권이 제3자인 것(S&P 500·CBOE VIX·ICE BofA 스프레드 등 —
시리즈 notes 의 저작권 표시)은 공개판에 싣지 않는다. 시리즈별 저작권 판정 [실측 필요 #45] — 애매하면
로그인(R10).

- 전송·오류·리미터는 공개 쪽 `kbj.data.public.fred.client.FredTransport` 를 그대로 쓴다(로그인 →
  공개 import 는 계약 ①이 허용, 반대는 금지). 같은 FRED 키라 리미터도 같은 버킷 `rl:fred:<키 해시>`.
- 이 입구는 만들 때 받은 **저작권 시리즈 목록**(`config/macro.yaml` dataset `FRED:restricted_series`
  의 item — 수집기가 넘긴다)만 받는다. 목록 밖(정부 시리즈 포함)은 `TierError` — 정부 시리즈를
  여기로
  받으면 `prv_*` 에 쌓여 공개판에 못 쓰게 된다.
- 저장 표 `prv_macro.series`. 작업 `macro.morning_prv`.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date
from typing import Any, Final

import httpx
from pydantic import SecretStr
from redis import Redis

from kbj.config.settings import Settings
from kbj.data.limits import Limits
from kbj.data.public.fred.client import (
    FredObservation,
    FredTransport,
    TierError,
    limiter_for,
)
from kbj.data.ratelimit import Clock, RateLimiter
from kbj.data.spec import DatasetSpec, Tier

__all__ = ["DATASETS", "FredRestrictedClient"]

SOURCE: Final = "FRED"


class FredRestrictedClient:
    """로그인 등급 FRED — 저작권 시리즈(`restricted`)만 받는다."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        limiter: RateLimiter,
        restricted: Collection[str],
        transport: httpx.BaseTransport | None = None,
        **kw: Any,
    ) -> None:
        if not restricted:
            raise ValueError(
                "저작권 시리즈 목록이 비었다(config/macro.yaml FRED:restricted_series)"
            )
        self.restricted = frozenset(restricted)
        self.transport = FredTransport(api_key, limiter=limiter, transport=transport, **kw)

    def __repr__(self) -> str:
        return f"FredRestrictedClient(key=***, restricted={len(self.restricted)})"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        restricted: Collection[str],
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> FredRestrictedClient:
        key, limiter = limiter_for(settings, redis, limits=limits, clock=clock)
        return cls(key, limiter=limiter, restricted=restricted, **kw)

    def close(self) -> None:
        self.transport.close()

    def require_restricted(self, series_id: str) -> None:
        if series_id not in self.restricted:
            raise TierError(
                f"FRED {series_id}: 저작권 시리즈 목록 밖 — 정부 시리즈는 kbj.data.public.fred 로 "
                "받는다(로그인 쪽에 받으면 prv_* 에 쌓여 공개판에 못 쓴다)"
            )

    def observations(
        self, series_id: str, start: date | None = None, end: date | None = None
    ) -> list[FredObservation]:
        self.require_restricted(series_id)
        return self.transport.observations(series_id, start, end)


# 카탈로그 PLANNED 의 같은 id 를 이리로 옮기는 일은 FND 몫(kbj/data/catalog.py — MAC 요청)
DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec(
        id=f"{SOURCE}:restricted_series",
        source=SOURCE,
        dataset="restricted_series",
        tier=Tier.PRIVATE,
        limiter="fred",
        budget=None,
        published="시리즈마다 다르다 [실측 필요 #45]",
        as_of_kind="us_trade_date",
        store="prv_macro.series",
        notes=(
            "FRED 저작권 시리즈(SP500·VIXCLS·BAMLH0A0HYM2 — config/macro.yaml) — 로그인(D-P5-6). "
            "공개 gov_series 와 같은 키·리미터. 작업 macro.morning_prv"
        ),
    ),
)

"""engine 체인 문맥 — 마스터(scheduler 의 `kis:master`) + poller 문맥(`poller:chain_context`)
(docs/phase3_design.md §1, services/chain_feed.py).

- 근월물 코드: `ChainContext.live_futures_codes(now)` 첫 종목 — poller·ws-gateway 와 같은 규칙(선물
  최종거래일 = 같은 결제월 월물 옵션의 KIS 최종거래일, 없으면 캘린더 — 분기 만기일 15:20 뒤엔 만기
  지난 근월물을 건너뛰고 차월물). 마스터에 선물이 없으면 None(서비스가 선물 행으로 대신한다)
- 시리즈 전 행사가(ATM 선정용, metrics §1.3 — 전광판은 잘릴 수 있다): 마스터의 그 시리즈 행사가
- 선물 결제월(`F 202612` → 202612): 분기 월물의 같은 결제월 선물가를 고르는 데
- Redis 읽기 실패·형식 오류는 직전 문맥을 둔다(로그만)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from redis import Redis
from redis.exceptions import RedisError

from core.calendar import TradingCalendar
from services.bus import ChainContextSnapshot, SeriesKey
from services.chain_feed import MasterWatcher, apply_context, load_context
from services.poller.context import ChainContext, Series, kospi200_futures
from services.runtime import log_event

SERVICE = "engine"
CONTEXT_EVERY_S = 5.0

log = logging.getLogger("services.engine")


class EngineContext:
    """engine 의 ChainContext — `refresh()` 로 Redis 를 다시 본다(`every_s` 마다)."""

    def __init__(
        self,
        redis: Redis,
        calendar: TradingCalendar,
        *,
        masters: MasterWatcher | None = None,
        every_s: float = CONTEXT_EVERY_S,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._r = redis
        self._cal = calendar
        self._clock = clock or time.monotonic
        self._masters = masters or MasterWatcher(redis, SERVICE, clock=self._clock)
        self._every = every_s
        self._next = 0.0
        self.ctx = ChainContext()
        self.poller_context = False  # poller 문맥을 한 번이라도 받았나

    def refresh(self, *, force: bool = False) -> None:
        now = self._clock()
        if not force and now < self._next:
            return
        self._next = now + self._every
        rows = self._masters.poll(force=force)
        if rows is not None:
            self.ctx.set_master(rows)
        try:
            snap = load_context(self._r)
        except RedisError as e:
            log_event(log, logging.WARNING, SERVICE, "context_read_failed", error=type(e).__name__)
            return
        if isinstance(snap, ChainContextSnapshot):
            apply_context(self.ctx, snap)
            self.poller_context = True

    def near_code(self, now: datetime) -> str | None:
        """지금 살아 있는 선물 근월물 코드 — 마스터·문맥으로 모르면 None."""
        try:
            live = self.ctx.live_futures_codes(now, self._cal)
        except Exception as e:  # 문맥 결함 — 선물 행으로 대신한다
            log_event(log, logging.WARNING, SERVICE, "near_code_failed", error=type(e).__name__)
            return None
        return live[0] if live else None

    def strikes(self, key: SeriesKey) -> tuple[Decimal, ...]:
        """마스터의 그 시리즈 전 행사가(오름차순). 모르면 빈 튜플."""
        chain = self.ctx.chain(Series(key[0], key[1]))
        return chain.strikes if chain is not None else ()

    def futures_months(self) -> dict[str, str]:
        """마스터 코스피200 선물 코드 → 결제월 YYYYMM."""
        return {r.code: r.expiry for r in kospi200_futures(self.ctx.master) if r.expiry}

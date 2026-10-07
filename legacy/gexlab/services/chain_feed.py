"""Redis 에 둔 체인 재료를 서비스끼리 나눠 쓴다 (docs/phase1_design.md §2·§4·§5).

- 마스터: scheduler 가 `kis:master`(+`kis:master:sha`)에 둔다 → poller·ws-gateway 는 `MasterWatcher`
  로 sha 만 가끔 보고 바뀌었을 때만 원문을 받아 파싱한다. 파싱 실패·Redis 오류면 직전 것을 둔다
- 체인 문맥: poller 가 월물리스트·KIS 최종거래일·선물 전광판 순서를 알아내면 `poller:chain_context`
  에 둔다(`ContextPublisher`) → ws-gateway 가 같은 최근접 만기·근월물을 고른다(`apply_context`).
  poller 문맥이 없으면 ws-gateway 는 마스터 + 캘린더 계산 최종거래일로 대신한다(`calendar_context`)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime

from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError

from core.calendar import TradingCalendar
from data.kis.master import MRKT_CLS_FAMILY, MasterRow, parse_master
from services.bus import (
    CHAIN_CONTEXT_KEY,
    MASTER_KEY,
    MASTER_SHA_KEY,
    ChainContextSnapshot,
    ExpiryEntry,
    MasterSnapshot,
)
from services.poller.context import ChainContext, ExpiryInfo, Series, estimate_last_trade_date
from services.runtime import log_event

MASTER_CHECK_S = 30.0
CONTEXT_EVERY_S = 5.0
CONTEXT_REFRESH_S = 60.0

log = logging.getLogger("services.chain_feed")


class MasterWatcher:
    """`poll()` — 새 마스터면 행 목록, 그대로거나 못 읽으면 None."""

    def __init__(
        self,
        redis: Redis,
        service: str,
        *,
        every_s: float = MASTER_CHECK_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._r = redis
        self._service = service
        self._every = every_s
        self._clock = clock
        self._next = 0.0
        self.sha: str | None = None
        self.snapshot: MasterSnapshot | None = None

    def poll(self, *, force: bool = False) -> list[MasterRow] | None:
        now = self._clock()
        if not force and now < self._next:
            return None
        self._next = now + self._every
        try:
            sha = self._r.get(MASTER_SHA_KEY)
            if sha is None or sha.decode() == self.sha:  # pyright: ignore[reportAttributeAccessIssue]
                return None
            raw = self._r.get(MASTER_KEY)
            if raw is None:
                return None
            snap = MasterSnapshot.model_validate_json(raw)  # pyright: ignore[reportArgumentType]
            if MasterSnapshot.digest(snap.text) != snap.sha256:
                raise ValueError("마스터 sha 가 원문과 다르다")
            rows = parse_master(snap.text)
        except (RedisError, ValidationError, ValueError) as e:
            err = type(e).__name__
            log_event(log, logging.WARNING, self._service, "master_read_failed", error=err)
            return None
        self.sha = snap.sha256
        self.snapshot = snap
        log_event(
            log,
            logging.INFO,
            self._service,
            "master_loaded",
            (snap.trade_date, snap.session),
            rows=len(rows),
            sha=snap.sha256[:12],
        )
        return rows


# ── 체인 문맥 ────────────────────────────────────────────────────────────────


def context_snapshot(ctx: ChainContext, at: datetime) -> ChainContextSnapshot:
    return ChainContextSnapshot(
        at=at,
        version=ctx.version,
        listed={cls: list(v) for cls, v in ctx.listed.items()},
        expiries=[
            ExpiryEntry(cls=s.cls, mtrt=s.mtrt, last_trade_date=e.last_trade_date, source=e.source)
            for s, e in sorted(ctx.expiries.items())
        ],
        futures_codes=list(ctx.futures_codes),
    )


def apply_context(ctx: ChainContext, snap: ChainContextSnapshot) -> None:
    """poller 문맥을 이 서비스의 ChainContext 에 (마스터는 따로)."""
    for cls, mtrts in snap.listed.items():
        ctx.set_listed(cls, mtrts)
    for e in snap.expiries:
        ctx.set_expiry(Series(e.cls, e.mtrt), ExpiryInfo(e.last_trade_date, e.source))
    ctx.set_futures_codes(snap.futures_codes)


def calendar_context(ctx: ChainContext, cal: TradingCalendar) -> int:
    """poller 문맥이 없을 때: 마스터의 코스피200 시리즈를 상장 목록으로, 최종거래일은 캘린더 계산.
    채운 시리즈 수를 돌려준다(PLAN §2.3 — KIS 값이 원천이고 이것은 대신 쓰는 값)."""
    n = 0
    for cls, family in MRKT_CLS_FAMILY.items():
        mtrts = sorted(
            {r.expiry for r in ctx.master if r.is_option and r.family == family and r.expiry}
        )
        ctx.set_listed(cls, mtrts)
        for m in mtrts:
            s = Series(cls, m)
            if s in ctx.expiries:
                continue
            d = estimate_last_trade_date(s, cal)
            if d is not None:
                ctx.set_expiry(s, ExpiryInfo(d, "calendar"))
                n += 1
    return n


def load_context(redis: Redis) -> ChainContextSnapshot | None:
    """없거나 형식이 틀리면 None. Redis 오류는 그대로 올린다."""
    raw = redis.get(CHAIN_CONTEXT_KEY)
    if raw is None:
        return None
    try:
        return ChainContextSnapshot.model_validate_json(raw)  # pyright: ignore[reportArgumentType]
    except ValidationError:
        return None


class ContextPublisher:
    """poller 문맥이 바뀌면(또는 60초마다) `poller:chain_context` 에 둔다. 최소 간격 5초."""

    def __init__(
        self,
        redis: Redis,
        service: str = "poller",
        *,
        every_s: float = CONTEXT_EVERY_S,
        refresh_s: float = CONTEXT_REFRESH_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._r = redis
        self._service = service
        self._every = every_s
        self._refresh = refresh_s
        self._clock = clock
        self._version: int | None = None
        self._at: float | None = None
        self._failing = False

    def publish(self, ctx: ChainContext, now: datetime) -> bool:
        t = self._clock()
        if self._at is not None:
            age = t - self._at
            if age < self._every or (ctx.version == self._version and age < self._refresh):
                return False
        if not ctx.listed:
            return False  # 아직 월물리스트를 못 받았다 — 빈 문맥으로 덮지 않는다
        try:
            self._r.set(CHAIN_CONTEXT_KEY, context_snapshot(ctx, now).model_dump_json())
        except RedisError as e:
            if not self._failing:
                err = type(e).__name__
                log_event(log, logging.WARNING, self._service, "context_publish_failed", error=err)
            self._failing = True
            self._at = t
            return False
        self._failing = False
        self._version, self._at = ctx.version, t
        return True

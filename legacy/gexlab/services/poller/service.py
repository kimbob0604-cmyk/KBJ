"""poller 서비스 조립 — 진입점 `python -m services.poller` (PLAN §4.1·§4.4, 설계 §2·§3·§5).

- `KisClient`: 토큰은 auth 의 읽기 전용 제공자(`services.auth.service.reader` — 발급하지 않는다,
  PLAN §2.5), 앱키당 `RedisRateLimiter`(REDIS_URL — 모든 KIS 호출자가 같은 버킷, 설계 §3)
- `Collector`(services/poller/collector.py)를 `slice_s` 씩 돌리고 사이사이 마스터 갱신(scheduler 가
  둔 Redis `kis:master`)·체인 문맥 발행(ws-gateway 용 `poller:chain_context`)·스풀 비우기·하트비트
- 저장은 `PostgresSink`(디스크 스풀). REST 원문은 `rest.raw` 로 발행해 recorder 가 적고, 받는 쪽이
  없거나 Redis 오류면 직접 raw_messages 에 쓴다(`RawFanout` — 잃지 않는다)
- 체인 행을 싱크에 넘긴 뒤 사이클이 끝나면 `chain.ready` 를 발행한다(`services/poller/ready.py` —
  Phase 3 설계 §1, engine 입력 계약). 수집 조각이 끝날 때마다 `ReadyNotifier.flush`(추적 시리즈
  전광판이 다 모였거나 30초), 멈출 때 남은 것도. 발행 실패는 로그만 — 수집을 막지 않는다
- 멈춤: SIGTERM → 시계의 sleep·레이트리미터 대기가 `Stopped` 를 올려 루프를 끝낸다(진행 중 호출은
  끝낸 뒤). `Stopped` 는 BaseException — 수집기의 호출 격리(`except Exception`)에 잡히지 않는다
- 레이트리미터(Redis)를 못 쓰면 호출은 실패로 격리된다(설계 §2 — 한도 초과보다 결측이 낫다)
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from redis import Redis

from core.calendar import TradingCalendar, state_at
from data.kis.rest import KisClient
from services.bus import REST_RAW, encode_envelope, publish_or_none
from services.chain_feed import ContextPublisher, MasterWatcher
from services.poller.collector import Collector
from services.poller.config import PollerConfig
from services.poller.ready import ReadyNotifier, ReadyTap
from services.poller.records import (
    ChainRecord,
    ExpiryRecord,
    FuturesRecord,
    HealthEvent,
    InvestorRecord,
    QuarantineRecord,
)
from services.poller.sink import Sink
from services.recorder.envelope import RawEnvelope
from services.runtime import Heartbeater, log_event

SERVICE = "poller"
SLICE_S = 1.0

log = logging.getLogger("services.poller")


class Stopped(BaseException):
    """종료 신호 — 수집기의 `except Exception` 격리를 지나 루프 밖으로 나간다."""


class StopClock:
    """레이트리미터 `Clock` — 호스트 시계, sleep 중 stop 이 오면 `Stopped`."""

    def __init__(self, stop: threading.Event) -> None:
        self._stop = stop

    def now_us(self) -> int:
        return time.time_ns() // 1000

    def sleep(self, seconds: float, /) -> None:
        if self._stop.wait(max(seconds, 0.0)):
            raise Stopped


class RawFanout:
    """poller `Sink` — 원문은 `rest.raw` 로 발행(받는 쪽이 없거나 Redis 오류면 inner 에 직접),
    나머지는 inner 로."""

    def __init__(self, inner: Sink, redis: Redis, channel: str = REST_RAW) -> None:
        self.inner = inner
        self._r = redis
        self._channel = channel
        self.published = 0
        self.direct = 0

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None:
        direct: list[RawEnvelope] = []
        for env in envelopes:
            if publish_or_none(self._r, self._channel, encode_envelope(env)):
                self.published += 1
            else:
                direct.append(env)
        if direct:
            self.direct += len(direct)
            self.inner.write_raw(direct)

    def write_chain(self, rows: Sequence[ChainRecord]) -> None:
        self.inner.write_chain(rows)

    def write_futures(self, rows: Sequence[FuturesRecord]) -> None:
        self.inner.write_futures(rows)

    def write_investor(self, rows: Sequence[InvestorRecord]) -> None:
        self.inner.write_investor(rows)

    def write_expiries(self, rows: Sequence[ExpiryRecord]) -> None:
        self.inner.write_expiries(rows)

    def write_quarantine(self, rows: Sequence[QuarantineRecord]) -> None:
        self.inner.write_quarantine(rows)

    def write_health(self, events: Sequence[HealthEvent]) -> None:
        self.inner.write_health(events)


class PollerService:
    def __init__(
        self,
        collector: Collector,
        *,
        masters: MasterWatcher | None = None,
        context: ContextPublisher | None = None,
        heartbeat: Heartbeater | None = None,
        flush_spool: Callable[[], bool] | None = None,
        ready: ReadyNotifier | None = None,
        slice_s: float = SLICE_S,
    ) -> None:
        if slice_s <= 0:
            raise ValueError("slice_s > 0")
        self.collector = collector
        self._masters = masters
        self._context = context
        self._heartbeat = heartbeat
        self._flush = flush_spool
        self.ready = ready
        self._slice = timedelta(seconds=slice_s)
        self.slices = 0

    def tick(self) -> None:
        """수집 사이 일: 마스터·문맥·스풀·하트비트. 하나가 실패해도 나머지는 한다."""
        col = self.collector
        now = col.now()
        if self._masters is not None:
            rows = self._masters.poll()
            if rows is not None:
                col.set_master(rows)
        if self._context is not None:
            self._context.publish(col.ctx, now)
        if self._flush is not None:
            try:
                self._flush()
            except Exception as e:
                log_event(log, logging.ERROR, SERVICE, "spool_flush_failed", error=type(e).__name__)
        if self._heartbeat is not None:
            s = col.stats
            self._heartbeat.beat(
                state=state_at(now, col.cal).state.value,
                executed=sum(s.executed.values()),
                failed=sum(s.failed.values()),
                sink_errors=s.sink_errors,
                master=len(col.ctx.master),
            )

    def flush_ready(self, *, force: bool = False) -> None:
        """사이클이 끝났으면 `chain.ready` — 추적 시리즈는 지금 최근접·차기·월물. 예외를 올리지
        않는다."""
        if self.ready is None:
            return
        col = self.collector
        try:
            targets = col.ctx.targets(col.now())
            tracked = targets.tracked if targets is not None else None
        except Exception as e:  # 곁일 — 추적 시리즈를 몰라도 30초 규칙으로 낸다
            log_event(log, logging.WARNING, SERVICE, "ready_targets_failed", error=type(e).__name__)
            tracked = None
        self.ready.flush(tracked, force=force)

    def run(self, stop: threading.Event, *, until: datetime | None = None) -> None:
        """stop(또는 until — 가짜 시계 시험용)까지 slice 씩 수집한다."""
        col = self.collector
        log_event(log, logging.INFO, SERVICE, "started", master=len(col.ctx.master))
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:  # 곁일 실패가 수집을 멈추지 않는다
                log_event(log, logging.ERROR, SERVICE, "tick_error", error=type(e).__name__)
            now = col.now()
            if until is not None and now >= until:
                break
            end = now + self._slice if until is None else min(now + self._slice, until)
            try:
                col.run_until(end)
            except Stopped:
                break
            except Exception as e:  # 수집기 결함에도 서비스는 산다 — 1초 쉬고 다시
                log_event(log, logging.ERROR, SERVICE, "collector_error", error=type(e).__name__)
                if stop.wait(1.0):
                    break
            self.flush_ready()
            self.slices += 1
        self.flush_ready(force=stop.is_set())
        s = col.stats
        log_event(
            log,
            logging.INFO,
            SERVICE,
            "stopped",
            executed=sum(s.executed.values()),
            failed=sum(s.failed.values()),
        )


def main() -> int:  # pragma: no cover — compose 진입점 (.env·실제 Redis·DB·KIS)
    from config.settings import Settings
    from data.kis.ratelimit import RedisRateLimiter
    from data.store import PostgresSink
    from services.auth.service import reader
    from services.runtime import connect_redis, install_stop, setup_logging, tagger_for

    setup_logging()
    settings = Settings()
    if not settings.redis_url or settings.kis_app_key is None:
        log_event(log, logging.ERROR, SERVICE, "config_error", error="REDIS_URL·KIS_APP_KEY 필요")
        return 2
    stop = threading.Event()
    install_stop(stop)
    cal = TradingCalendar.default()
    redis = connect_redis(settings.redis_url)
    clock = StopClock(stop)
    store = PostgresSink.from_settings(service=SERVICE, spool=True, tagger=tagger_for(cal))
    limiter = RedisRateLimiter(redis, settings.kis_app_key.get_secret_value(), clock=clock)
    kis = KisClient(settings, token_provider=reader(redis, settings), rate_limiter=limiter)
    try:
        ready = ReadyNotifier(redis)
        sink = ReadyTap(RawFanout(store, redis), ready)
        collector = Collector(kis, sink, calendar=cal, clock=clock, config=PollerConfig())
        svc = PollerService(
            collector,
            masters=MasterWatcher(redis, SERVICE),
            context=ContextPublisher(redis, SERVICE),
            heartbeat=Heartbeater(redis, SERVICE),
            flush_spool=store.flush_spool,
            ready=ready,
        )
        svc.run(stop)
    finally:
        kis.close()
        store.close()
    return 0

"""GEX 수집 작업(분봉·KRX 파생·마스터·개장 확인) 공용 자원 해석(P9 GXI, ADR 0036).

`JobContext.resources` 에 주입된 것이 있으면 그것(시험·시뮬레이션), 없으면 운영 자원을 만든다:

- `kr` → `TradingCalendar.default()`
- `clock` → `utcnow`(받는 시각·미래 입력 판정 시계)
- `redis` → 실행기가 넣는 Redis(필수) — 마스터(`kis:master`)·체인 문맥(`poller:chain_context`)
- `gex_store` → `open_ingest_store(settings, service="scheduler", spool=False)` — 작업은 스풀을 쓰지
  않는다: 실패는 작업 실패로 끝나고 등록부 재시도가 다시 부른다(멱등 쓰기). 스풀 디렉터리를
  여러 작업 스레드가 함께 쓰지 않게 하려는 것이기도 하다.
- `health` → `ServiceHealthSink(gex_store, tagger)`(로그 + `ops.health_events`)
- `kis` → `KisRestClient.for_service(settings, redis)`(auth 토큰 읽기만·앱키 리미터) — 열면 닫는다
- `krx` → `KrxClient.from_settings(settings, redis)`(리미터·`krx:calls` 일 예산)
- `master` → Redis `kis:master` 의 `MasterSnapshot`(없거나 형식이 틀리면 None)
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Final

from pydantic import ValidationError
from redis.exceptions import RedisError

from kbj.core.calendar import TradingCalendar
from kbj.data.private.kis.rest import KisRestClient
from kbj.data.private.krx.client import KrxClient
from kbj.data.ratelimit import Priority
from kbj.services.gex_poller.store import GexIngestStore, open_ingest_store
from kbj.services.runtime import ServiceHealthSink, tagger_for, utcnow
from kbj.services.runtime.bus import MASTER_KEY, ChainContextSnapshot, MasterSnapshot
from kbj.services.runtime.chain_feed import load_context
from kbj.services.runtime.health import HealthSink
from kbj.services.scheduler.handlers import JobContext

__all__ = ["SERVICE", "GexResources", "inline_submit"]

SERVICE: Final = "scheduler"


def inline_submit[T](fn: Callable[[], T], /) -> Future[T]:
    """GX 구동 클래스(`MinuteDaily`·`GapDaily`·`KrxDaily`·`OpenCheck`)의 `submit` 자리 — 작업
    스레드를 띄우지 않고 그 자리에서 부른다(등록부 실행기가 이미 작업 스레드에서 처리기를 부른다).
    예외는 Future 에 담아 돌려준다(GX 클래스가 결과를 모을 때 실패로 다룬다)."""
    fut: Future[T] = Future()
    try:
        fut.set_result(fn())
    except Exception as e:  # 클래스가 fut.result() 에서 받아 실패로 처리한다
        fut.set_exception(e)
    return fut


class GexResources:
    """작업 실행 하나의 자원(만든 것은 실행 안에서만 쓴다)."""

    def __init__(self, ctx: JobContext) -> None:
        self.ctx = ctx
        got = ctx.resources.get("kr")
        self.cal: TradingCalendar = got if got is not None else TradingCalendar.default()
        clock = ctx.resources.get("clock")
        self.now: Callable[[], datetime] = clock if clock is not None else utcnow
        self._store: GexIngestStore | Any = ctx.resources.get("gex_store")
        self._health: HealthSink | None = ctx.resources.get("health")

    @property
    def redis(self) -> Any:
        return self.ctx.resource("redis")

    def store(self) -> Any:
        """수집 저장소(`GexIngestStore` 모양). 없으면 스풀 없이 연다."""
        if self._store is None:
            if self.ctx.settings is None:
                raise RuntimeError("설정이 주입되지 않았다(KBJ_DATABASE_URL)")
            self._store = open_ingest_store(
                self.ctx.settings, service=SERVICE, tagger=tagger_for(self.cal), spool=False
            )
        return self._store

    def health(self) -> HealthSink:
        if self._health is None:
            self._health = ServiceHealthSink(self.store(), tagger_for(self.cal))
        return self._health

    @contextmanager
    def kis_client(self) -> Iterator[Any]:
        """주입된 `kis` 가 있으면 그것(닫지 않는다), 없으면 서비스용 클라이언트를 열고 닫는다."""
        got = self.ctx.resources.get("kis")
        if got is not None:
            yield got
            return
        if self.ctx.settings is None:
            raise RuntimeError("설정이 주입되지 않았다(KBJ_KIS_*)")
        client = KisRestClient.for_service(self.ctx.settings, self.redis)
        try:
            yield client
        finally:
            client.close()

    def krx_client(self) -> Any:
        got = self.ctx.resources.get("krx")
        if got is not None:
            return got
        if self.ctx.settings is None:
            raise RuntimeError("설정이 주입되지 않았다(KBJ_KRX_API_KEY)")
        return KrxClient.from_settings(self.ctx.settings, self.redis, priority=Priority.P3)

    def master(self) -> MasterSnapshot | None:
        """Redis `kis:master`(kis.master 작업이 둔 것). 없거나 형식이 틀리면 None."""
        got = self.ctx.resources.get("master")
        if got is not None:
            return got
        try:
            raw = self.redis.get(MASTER_KEY)
        except RedisError:
            return None
        if raw is None:
            return None
        try:
            return MasterSnapshot.model_validate_json(raw)
        except (ValidationError, ValueError):
            return None

    def context_source(self) -> Callable[[], ChainContextSnapshot | None]:
        """poller 체인 문맥(`poller:chain_context`) 읽기 — 없으면 캘린더 계산으로 대신한다."""
        redis = self.redis
        return lambda: load_context(redis)

"""engine Redis 출력 — `engine.levels`·`engine.metrics` 발행, `engine:latest`·`engine:basis` 키
(docs/phase3_design.md §1·§2, services/bus.py 계약).

- 발행 메시지는 pydantic 모델(`EngineLevels`·`EngineMetrics`) — `quality`·`as_of` 필수, visible 만
  (shadow 레벨·지표는 저장만 하고 내지 않는다 — 설계 §4, 플래그는 행의 `flag`). `engine.levels` 는
  visible 레벨이 없어도 사이클마다 나간다(사이클 품질·as_of)
- `engine:latest`: 마지막 사이클 산출(`EngineLatest`). S_ref 가 없거나 사이클이 실패하면 새로
  계산하지 못한 것이라 직전 값을 `stale` 로 표시해 둔다(`mark_stale` — S_ref·시리즈 품질까지 모든
  품질을 stale 이상으로)
- `engine:basis`: 시리즈별 확정 베이시스(`BasisBook`) — 재기동해도 이어 쓴다. 형식이 틀린 값은
  버린다(빈 장부로 시작 — 베이시스는 다음 ok F 로 다시 확정된다)
- Redis 오류는 로그·통계만 — 예외를 올리지 않는다(DB 저장·계산을 막지 않는다)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError

from core.preprocess import worst
from services.bus import (
    ENGINE_BASIS_KEY,
    ENGINE_LATEST_KEY,
    ENGINE_LEVELS,
    ENGINE_METRICS,
    BasisBook,
    EngineLatest,
    EngineLevels,
    EngineMetrics,
    LevelOut,
    MetricOut,
    Quality,
    SeriesOut,
    SessionName,
    publish_or_none,
)
from services.engine.evaluate import CycleResult, SeriesOutcome
from services.engine.records import LevelRecord, MetricRecord
from services.runtime import log_event

SERVICE = "engine"

log = logging.getLogger("services.engine")


@dataclass
class PublishStats:
    published: int = 0  # 발행한 메시지(수신자 0 포함)
    failed: int = 0  # Redis 오류
    latest: int = 0  # engine:latest 쓰기
    stale_marks: int = 0


def level_out(r: LevelRecord) -> LevelOut:
    return LevelOut(
        scope=r.scope,
        name=r.name,
        value=r.value,
        quality=r.quality,
        detail=r.detail,
        reasons=r.reasons,
    )


def metric_out(r: MetricRecord) -> MetricOut:
    return MetricOut(
        metric=r.metric,
        scope=r.scope,
        key=r.key,
        value=r.value,
        quality=r.quality,
        payload=r.payload,
    )


def series_out(o: SeriesOutcome) -> SeriesOut:
    fwd = o.ev.forward if o.ev is not None else None
    return SeriesOut(
        label=o.label,
        mrkt_cls=o.key[0],
        expiry=o.key[1],
        status=o.status,
        expiry_date=o.expiry.last_trade_date if o.expiry is not None else None,
        forward=fwd.F if fwd is not None else None,
        forward_quality=fwd.quality if fwd is not None else None,
        forward_reasons=fwd.reasons if fwd is not None else (),
        forward_notes=fwd.notes if fwd is not None else (),
        input_quality=o.input_quality,
        basis_age=o.basis_age,
    )


def latest_of(result: CycleResult, computed_at: datetime) -> EngineLatest:
    """사이클 결과 → `engine:latest` (visible 레벨·지표만)."""
    s = result.s_ref
    return EngineLatest(
        as_of=result.as_of,
        trade_date=result.trade_date,
        session=result.session,
        quality=result.quality,
        computed_at=computed_at,
        near_code=result.near_code,
        s_ref=None if s is None else float(s.price),
        s_ref_quality=None if s is None else s.quality,
        series=tuple(series_out(o) for o in result.series),
        levels=tuple(level_out(r) for r in result.levels if r.flag == "visible"),
        metrics=tuple(metric_out(m) for m in result.metrics if m.flag == "visible"),
    )


def _stale_series(s: SeriesOut) -> SeriesOut:
    fq = None if s.forward_quality is None else worst(s.forward_quality, "stale")
    return s.model_copy(
        update={"forward_quality": fq, "input_quality": worst(s.input_quality, "stale")}
    )


def mark_stale(latest: EngineLatest, reason: str, at: datetime) -> EngineLatest:
    """직전 산출을 stale 로 — 값은 그대로, 품질은 모두 stale 이상(사이클·S_ref·시리즈 F·입력·레벨·
    지표 — 없는 품질(None)은 그대로), 사유·시각을 단다."""
    s_ref_q = None if latest.s_ref_quality is None else worst(latest.s_ref_quality, "stale")
    return latest.model_copy(
        update={
            "stale": True,
            "stale_reason": reason,
            "stale_at": at,
            "quality": worst(latest.quality, "stale"),
            "s_ref_quality": s_ref_q,
            "series": tuple(_stale_series(s) for s in latest.series),
            "levels": tuple(
                x.model_copy(update={"quality": worst(x.quality, "stale")}) for x in latest.levels
            ),
            "metrics": tuple(
                x.model_copy(update={"quality": worst(x.quality, "stale")}) for x in latest.metrics
            ),
        }
    )


class EnginePublisher:
    def __init__(self, redis: Redis) -> None:
        self._r = redis
        self.stats = PublishStats()

    # ── 발행·최신 ──

    def publish(self, result: CycleResult, latest: EngineLatest) -> None:
        """레벨·visible 지표를 발행하고 `engine:latest` 를 둔다."""
        head = {
            "as_of": result.as_of,
            "trade_date": result.trade_date,
            "session": result.session,
            "quality": result.quality,
        }
        levels = EngineLevels(**head, levels=latest.levels)
        self._publish(ENGINE_LEVELS, levels.model_dump_json(), result)
        if latest.metrics:
            metrics = EngineMetrics(**head, metrics=latest.metrics)
            self._publish(ENGINE_METRICS, metrics.model_dump_json(), result)
        self.set_latest(latest)

    def publish_metrics(
        self, trade_date: date, session: SessionName, as_of: datetime, rows: Sequence[MetricRecord]
    ) -> None:
        """사이클 밖 지표(일별 — `services/engine/daily.py`)의 visible 행을 `engine.metrics` 로.
        메시지 품질은 그 행들 중 가장 나쁜 것."""
        out = tuple(metric_out(r) for r in rows if r.flag == "visible")
        if not out:
            return
        qualities: list[Quality] = [m.quality for m in out]
        msg = EngineMetrics(
            as_of=as_of,
            trade_date=trade_date,
            session=session,
            quality=worst(*qualities),
            metrics=out,
        )
        self._publish(ENGINE_METRICS, msg.model_dump_json(), (trade_date, session))

    def _publish(self, channel: str, body: str, result: CycleResult | tuple[date, str]) -> None:
        if publish_or_none(self._r, channel, body) is None:
            self.stats.failed += 1
            tag = result if isinstance(result, tuple) else (result.trade_date, result.session)
            log_event(log, logging.WARNING, SERVICE, "publish_failed", tag, channel=channel)
        else:
            self.stats.published += 1

    def set_latest(self, latest: EngineLatest) -> bool:
        try:
            self._r.set(ENGINE_LATEST_KEY, latest.model_dump_json())
        except RedisError as e:
            self.stats.failed += 1
            tag = (latest.trade_date, latest.session)
            err = type(e).__name__
            log_event(log, logging.WARNING, SERVICE, "latest_write_failed", tag, error=err)
            return False
        self.stats.latest += 1
        return True

    def load_latest(self) -> EngineLatest | None:
        """기동 때 — 직전 실행이 둔 마지막 산출(없거나 형식이 틀리면 None)."""
        try:
            raw = self._r.get(ENGINE_LATEST_KEY)
            return None if raw is None else EngineLatest.model_validate_json(raw)  # pyright: ignore[reportArgumentType]
        except (RedisError, ValidationError, ValueError) as e:
            log_event(log, logging.WARNING, SERVICE, "latest_read_failed", error=type(e).__name__)
            return None

    # ── 확정 베이시스 ──

    def load_basis(self) -> BasisBook:
        try:
            raw = self._r.get(ENGINE_BASIS_KEY)
            return BasisBook() if raw is None else BasisBook.model_validate_json(raw)  # pyright: ignore[reportArgumentType]
        except (RedisError, ValidationError, ValueError) as e:
            log_event(log, logging.WARNING, SERVICE, "basis_read_failed", error=type(e).__name__)
            return BasisBook()

    def save_basis(self, book: BasisBook) -> bool:
        try:
            self._r.set(ENGINE_BASIS_KEY, book.model_dump_json())
        except RedisError as e:
            self.stats.failed += 1
            log_event(log, logging.WARNING, SERVICE, "basis_write_failed", error=type(e).__name__)
            return False
        return True

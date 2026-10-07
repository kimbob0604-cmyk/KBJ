"""`python -m kbj.services.scheduler [run | validate [경로] | run-once <작업> [--as-of 값]]`.

- `validate`: 등록부를 카탈로그·limits.yaml·notify.yaml·마이그레이션 표와 대조한다(설계 §6.5 정적
  겹). 오류가 있으면 한 줄씩 찍고 종료 코드 1. Redis·DB·키가 없어도 된다(CI).
- `run`: 서비스(세션 상태 발행 + 실행기). 등록부 검증이 실패하면 시작하지 않는다(종료 코드 2).
- `run-once`: 작업 하나를 지금 한 번(조건 무시, `--as-of` 로 이벤트·백필 날짜). 기록 source=manual.
  인증된 운영 화면은 P3(SD `POST /api/ops/cron/trigger` 폐지 — §6.8).
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from kbj.config.files import config_path
from kbj.config.settings import Settings
from kbj.core.calendar import TradingCalendar, load_override, us_calendar
from kbj.core.time import utcnow
from kbj.data.catalog import all_datasets
from kbj.data.limits import load_limits
from kbj.services.runtime.health import ServiceHealthSink
from kbj.services.runtime.heartbeat import Heartbeater, connect_redis
from kbj.services.runtime.log import log_event, setup_logging
from kbj.services.runtime.tagger import tagger_for
from kbj.services.runtime.threads import install_stop
from kbj.services.scheduler.registry import (
    Registry,
    RegistryError,
    budgets_from_limits,
    migration_tables,
)

SERVICE = "scheduler"
log = logging.getLogger("kbj.services.scheduler")


def validate_file(path: Path, *, settings: Settings | None = None) -> list[str]:
    """등록부 파일 하나를 검증한 오류 목록(파일 형식 오류도 한 줄로)."""
    from kbj.services.notifier.policy import load_notify_config

    try:
        reg = Registry.load(path)
    except RegistryError as e:
        return [str(e)]
    s = settings or Settings(config_dir=path.parent)
    notify = load_notify_config(settings=s)
    return reg.validate(
        all_datasets(),
        budgets=budgets_from_limits(load_limits(settings=s)),
        notify_policies={k: p.dedup for k, p in notify.kinds.items()},
        tables=migration_tables(),
    )


def _calendar(settings: Settings) -> TradingCalendar:
    return TradingCalendar.from_override(
        load_override(config_path("holidays_override.yaml", settings=settings))
    )


def _build(settings: Settings, *, inline: bool = False) -> tuple[Any, Any, Any, Callable[[], None]]:
    """(서비스, 실행기, redis, 닫기) — 운영 진입점 전용(실제 Redis·DB)."""
    from kbj.services.notifier.client import notify
    from kbj.services.scheduler.claims import PgClaimStore, PgRunLog
    from kbj.services.scheduler.runner import JobRunner, inline_submit, thread_submit
    from kbj.services.scheduler.service import PgSessionLogStore, SchedulerService
    from kbj.store.db import PgHealthStore, connect

    if settings.redis_url is None:
        raise SystemExit("KBJ_REDIS_URL 이 없다")
    if settings.database_url is None:
        raise SystemExit("KBJ_DATABASE_URL 이 없다")
    redis = connect_redis(settings.redis_url)

    def connect_fn() -> Any:
        return connect(settings, service=SERVICE)

    kr = _calendar(settings)
    health = ServiceHealthSink(PgHealthStore(connect_fn), tagger_for(kr))
    registry = Registry.load(config_path("jobs.yaml", settings=settings))
    us = us_calendar()
    runs = PgRunLog(connect_fn)
    runner = JobRunner(
        registry,
        None,
        PgClaimStore(connect_fn),
        kr,
        us,
        runs=runs,
        catalog=all_datasets(),
        health=health,
        submit=inline_submit if inline else thread_submit,
        notify=notify,
        settings=settings,
        # 처리기가 쓰는 공용 자원(handlers.JobContext.resources)
        resources={
            "redis": redis,
            "connect": connect_fn,
            "notify": notify,
            "registry": registry,
            "runs": runs,
            "kr": kr,
            "us": us,
        },
    )
    svc = SchedulerService(redis, runner, kr, PgSessionLogStore(connect_fn), health=health)
    return svc, runner, redis, redis.close


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m kbj.services.scheduler")
    sub = p.add_subparsers(dest="cmd")
    v = sub.add_parser("validate", help="등록부 정적 검증")
    v.add_argument("path", nargs="?", default=None)
    sub.add_parser("run", help="서비스 실행")
    o = sub.add_parser("run-once", help="작업 하나를 지금 한 번")
    o.add_argument("job")
    o.add_argument("--as-of", dest="as_of", default=None)
    args = p.parse_args(argv)
    cmd = args.cmd or "run"

    if cmd == "validate":
        settings = Settings()
        path = Path(args.path) if args.path else config_path("jobs.yaml", settings=settings)
        errs = validate_file(path)
        for e in errs:
            print(f"오류: {e}")
        print(f"{path}: {'통과' if not errs else f'오류 {len(errs)}건'}")
        return 1 if errs else 0

    setup_logging()
    settings = Settings()
    errs = validate_file(config_path("jobs.yaml", settings=settings), settings=settings)
    if errs:
        for e in errs:
            log_event(log, logging.ERROR, SERVICE, "registry_invalid", error=e)
        return 2
    if cmd == "run-once":
        _svc, runner, _redis, close = _build(settings, inline=True)
        try:
            try:
                events = runner.run_once(args.job, utcnow(), as_of=args.as_of)
            except (KeyError, ValueError) as e:  # 없는 작업·external·as_of 를 정할 수 없음
                print(f"오류: {e}")
                return 1
            for ev in events:
                print(f"{ev.kind} {ev.job} {ev.as_of} 시도 {ev.attempt}")
            return 0 if all(ev.kind not in ("failed", "timeout") for ev in events) else 1
        finally:
            close()

    from kbj.services.scheduler.service import run  # pragma: no cover — compose 진입점

    svc, _runner, redis, close = _build(settings)  # pragma: no cover
    stop = threading.Event()  # pragma: no cover
    install_stop(stop)  # pragma: no cover
    try:  # pragma: no cover
        log_event(log, logging.INFO, SERVICE, "started")
        run(svc, stop, heartbeat=Heartbeater(redis, SERVICE))
    finally:  # pragma: no cover
        close()
    return 0  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())

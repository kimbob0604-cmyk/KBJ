"""KIS 지수선물옵션 마스터 갱신 — GX `services/scheduler/service.py` 의 마스터 단계(`_master_step`·
`_apply_master`·`_master_failed`) 이식(P9 GXI, ADR 0036).

- 등록부 작업 `kis.master`(PRE_DAY 진입 — 그날 주간)·`kis.master_night`(PRE_NIGHT 진입 — 다음 거래일
  야간)이 부른다. 세션 상태 발행은 KBJ scheduler 서비스가 이미 한다(GX `Scheduler` 의 나머지는
  옮기지
  않았다 — 등록부·상태 발행 한 벌).
- 받은 zip → 파싱 → `prv_gex.master_snapshots`(그 세션 태그 — 받기 시작한 시각의 `session_tag`)와
  Redis `kis:master`·`kis:master:sha`(poller·ws-gateway·분봉 작업이 읽는다).
- 내려받기·파싱이 실패하거나 옵션 행이 없는 마스터면 Redis 를 건드리지 않고(직전 마스터를 계속 쓴다)
  health warning `master_refresh_failed` — 작업은 실패로 끝나 등록부 재시도가 다시 받는다(GX 60초 뒤
  다시 → `config/jobs.yaml retry`).
- DB 쓰기 실패는 health `master_store_failed` 만 남기고 Redis 에는 둔다(수집은 계속돼야 한다 — GX 와
  같다). Redis 쓰기 실패면 작업 실패.
- 내려받기는 주입(`master_downloader` 자원 — zip 바이트). 기본은 KIS 배포 주소(REST 한도 밖) —
  주소는 `kbj.data.private.kis.master` 에만 있다(check_canonical `kis_master`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final

from redis.exceptions import RedisError

from kbj.core.calendar import session_tag
from kbj.data.private.kis.master import http_master_downloader, master_text_from_zip, parse_master
from kbj.services.collectors.gex_resources import SERVICE, GexResources
from kbj.services.runtime import log_event
from kbj.services.runtime.bus import MASTER_KEY, MASTER_SHA_KEY, MasterSnapshot
from kbj.services.runtime.health import HealthEvent, HealthSink, Severity
from kbj.services.scheduler.handlers import JobContext, JobResult

__all__ = ["MasterRefreshFailed", "apply_master", "run", "run_night"]

log = logging.getLogger("kbj.services.collectors.kis_master")
MasterDownloader = Callable[[], bytes]
DETAIL_MAX: Final = 120


class MasterRefreshFailed(RuntimeError):
    """마스터를 갱신하지 못했다 — 직전 마스터를 계속 쓴다(Redis 를 건드리지 않았다)."""


def _emit(health: HealthSink, now: datetime, kind: str, detail: str, severity: Severity) -> None:
    try:
        health.emit(HealthEvent(kind, detail, now, severity, service=SERVICE))
    except Exception as e:  # health 실패가 갱신을 막지 않는다
        log_event(log, logging.ERROR, SERVICE, "health_failed", error=type(e).__name__)


def apply_master(
    data: bytes,
    *,
    store: Any,
    redis: Any,
    cal: Any,
    health: HealthSink,
    now: datetime,
    started: datetime,
) -> MasterSnapshot:
    """받은 zip → master_snapshots·Redis. 세션 태그는 받기 시작한 시각(그 전이의 세션).

    실패(파싱·옵션 없음·Redis)는 health 를 남기고 `MasterRefreshFailed`."""
    try:
        text = master_text_from_zip(data)
        rows = parse_master(text)
    except ValueError as e:
        msg = str(e).splitlines()[0][:DETAIL_MAX] if str(e) else ""
        detail = f"파싱 실패 — 직전 마스터를 계속 쓴다: {msg}"
        _emit(health, now, "master_refresh_failed", detail, "warning")
        raise MasterRefreshFailed(detail) from None
    options = sum(1 for r in rows if r.is_option)
    if options == 0:
        detail = "옵션 행이 없는 마스터 — 직전 마스터를 계속 쓴다"
        _emit(health, now, "master_refresh_failed", detail, "warning")
        raise MasterRefreshFailed(detail)
    tag = session_tag(started, cal)
    if tag is not None:
        try:
            store.write_master(rows, ts=now, trade_date=tag[0], session=tag[1])
        except Exception as e:  # Redis 에는 둔다 — 수집은 계속돼야 한다
            detail = f"master_snapshots 저장 실패: {type(e).__name__}"
            _emit(health, now, "master_store_failed", detail, "warning")
    sha = MasterSnapshot.digest(text)
    snap = MasterSnapshot(
        asof=now,
        trade_date=tag[0] if tag else None,
        session=tag[1] if tag else None,
        rows=len(rows),
        sha256=sha,
        text=text,
    )
    try:
        pipe = redis.pipeline()
        pipe.set(MASTER_KEY, snap.model_dump_json())
        pipe.set(MASTER_SHA_KEY, sha)
        pipe.execute()
    except RedisError as e:
        detail = f"Redis 저장 실패: {type(e).__name__}"
        _emit(health, now, "master_refresh_failed", detail, "warning")
        raise MasterRefreshFailed(detail) from None
    where = f"{tag[0]} {tag[1]}" if tag else "장 밖"
    detail = f"마스터 {len(rows)}행(옵션 {options}) sha {sha[:12]} — {where}"
    _emit(health, now, "master_refreshed", detail, "info")
    return snap


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기 `kbj.services.collectors.kis_master:run`(`kis.master`·`kis.master_night`)."""
    res = GexResources(ctx)
    started = res.now()
    got = ctx.resources.get("master_downloader")
    download: MasterDownloader = got if got is not None else http_master_downloader()
    health = res.health()
    try:
        data = download()
    except Exception as e:
        detail = f"내려받기 실패: {type(e).__name__} — 직전 마스터를 계속 쓴다"
        _emit(health, res.now(), "master_refresh_failed", detail, "warning")
        return JobResult("failed", detail={"error": detail})
    try:
        snap = apply_master(
            data,
            store=res.store(),
            redis=res.redis,
            cal=res.cal,
            health=health,
            now=res.now(),
            started=started,
        )
    except MasterRefreshFailed as e:
        return JobResult("failed", detail={"error": str(e)})
    detail = {
        "rows": snap.rows,
        "sha": snap.sha256[:12],
        "trade_date": snap.trade_date.isoformat() if snap.trade_date else None,
        "session": snap.session,
    }
    return JobResult("ok", collected=tuple(ctx.keys), rows=snap.rows, detail=detail)


def run_night(ctx: JobContext) -> JobResult:
    """`kis.master_night`(PRE_NIGHT 진입 — 그 밤은 다음 거래일 night 태그) — 등록부 작업마다
    owner 가 하나씩이게 나눈 이름. 본문은 `run`(세션 태그는 받기 시작한 시각으로 정한다)."""
    return run(ctx)

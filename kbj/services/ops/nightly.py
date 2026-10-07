"""`ops.nightly`(03:00) — DB 백업 + 보존 기간 정리(설계 §6.7, P2).

- **백업**: `pg_dump --format=custom` 을 `<Settings.data_dir>/backup/kbj-<KST 날짜>.dump` 로,
  7일 보존 [확인 필요 — R23: VM 로컬 디스크. 외부 보관 위치는 결정 전].
  SD `db_backup_hourly`(매시 Gist)를 대체.
  접속 정보는 명령줄이 아니라 환경변수(PGHOST·PGPASSWORD …)로 넘긴다(프로세스 목록에 남지 않게).
  `pg_dump` 가 없거나 실패하면 그 단계 실패(조용히 건너뛰지 않는다).
- **정리**: `prv_alerts.tg_inbox` — 메시지 시각(없으면 받은 시각)이
  `notify.yaml inbox.keep_days`(14일)
  보다 오래된 것, `prv_alerts.notify_message` — 30일 [제안 §5.4] 지난 본문.
- 단계는 서로 막지 않는다(하나가 실패해도 나머지는 한다 — 절대 규칙 4). 하나라도 실패면 작업 실패
  (실행기가 재시도·알림). 사유는 가린다.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import psycopg

from kbj.config.settings import Settings
from kbj.core.masking import mask_text
from kbj.core.time import KST
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.db import StoreError, parse_dsn

__all__ = [
    "BACKUP_KEEP_DAYS",
    "MESSAGE_KEEP_DAYS",
    "pg_dump_backup",
    "prune",
    "prune_backups",
    "run",
]

BACKUP_KEEP_DAYS: Final = 7  # [확인 필요 — R23]
MESSAGE_KEEP_DAYS: Final = 30  # [제안 — §5.4]
DUMP_TIMEOUT_S: Final = 1800
_PREFIX: Final = "kbj-"
_SUFFIX: Final = ".dump"

ConnectFn = Callable[[], psycopg.Connection[Any]]


def _pg_env(settings: Settings) -> dict[str, str]:
    if settings.database_url is None:
        raise StoreError("KBJ_DATABASE_URL 이 없다")
    params = parse_dsn(settings.database_url.get_secret_value())
    if params is None:
        raise StoreError("KBJ_DATABASE_URL 형식 오류")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    names = {
        "host": "PGHOST",
        "port": "PGPORT",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "dbname": "PGDATABASE",
    }
    for key, var in names.items():
        val = params.get(key)
        if val:
            env[var] = str(val)
    return env


def prune_backups(directory: Path, now: datetime, keep_days: int = BACKUP_KEEP_DAYS) -> list[Path]:
    """보존 기간이 지난 백업 파일을 지우고 지운 목록을 돌려준다(파일 이름의 KST 날짜 기준)."""
    today = now.astimezone(KST).date()
    gone: list[Path] = []
    for f in sorted(directory.glob(f"{_PREFIX}*{_SUFFIX}")):
        stamp = f.name[len(_PREFIX) : -len(_SUFFIX)]
        try:
            day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
        except (ValueError, IndexError):
            continue  # 우리가 만들지 않은 파일은 건드리지 않는다
        if (today - day).days >= keep_days:
            f.unlink()
            gone.append(f)
    return gone


def pg_dump_backup(settings: Settings, now: datetime) -> Path:
    """백업 파일 하나를 만들고 경로를 돌려준다. 실패는 `RuntimeError`(가린 사유)."""
    directory = settings.data_dir / "backup"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_PREFIX}{now.astimezone(KST):%Y%m%d}{_SUFFIX}"
    tmp = path.with_suffix(".part")
    try:
        proc = subprocess.run(  # noqa: S603 — 고정 인자, 셸 없음
            ["pg_dump", "--format=custom", "--no-owner", f"--file={tmp}"],  # noqa: S607
            env=_pg_env(settings),
            capture_output=True,
            text=True,
            timeout=DUMP_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("pg_dump 가 없다(postgresql-client 설치 필요)") from None
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump {DUMP_TIMEOUT_S}초 초과") from None
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        first = (proc.stderr or "").strip().splitlines()[:1]
        raise RuntimeError(
            mask_text(f"pg_dump 실패(종료 {proc.returncode}): {' '.join(first)}")[:300]
        )
    tmp.replace(path)
    prune_backups(directory, now)
    return path


def prune(
    connect_fn: ConnectFn, now: datetime, *, inbox_days: int, message_days: int = MESSAGE_KEEP_DAYS
) -> dict[str, int]:
    """보존 기간 정리 — 지운 행 수."""
    inbox_cut = now - timedelta(days=inbox_days)
    msg_cut = now - timedelta(days=message_days)
    try:
        with connect_fn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM prv_alerts.tg_inbox WHERE COALESCE(date, received_at) < %s",
                (inbox_cut,),
            )
            inbox = max(cur.rowcount, 0)
            cur.execute("DELETE FROM prv_alerts.notify_message WHERE created_at < %s", (msg_cut,))
            msgs = max(cur.rowcount, 0)
    except psycopg.Error as e:
        kind = type(e).__name__ + (f"({e.sqlstate})" if e.sqlstate else "")
        raise StoreError(f"보존 정리 실패: {kind}", cause=kind) from None
    return {"inbox_deleted": inbox, "messages_deleted": msgs}


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.ops.nightly:run`). 자원: connect, (선택) backup·inbox_days."""
    detail: dict[str, Any] = {}
    errors: list[str] = []
    backup: Callable[[datetime], Path] | None = ctx.resources.get("backup")
    if backup is None:
        settings = ctx.settings
        if settings is None:
            errors.append("backup: 설정이 주입되지 않았다")
        else:

            def _pg_dump(now: datetime) -> Path:
                return pg_dump_backup(settings, now)

            backup = _pg_dump

    if backup is not None:
        try:
            detail["backup"] = backup(ctx.now).name
        except Exception as e:  # 단계 격리 — 정리는 계속
            errors.append(mask_text(f"backup: {type(e).__name__}: {e}")[:300])
    inbox_days = ctx.resources.get("inbox_days")
    if inbox_days is None:
        from kbj.services.notifier.policy import load_notify_config

        inbox_days = load_notify_config(settings=ctx.settings).inbox.keep_days
    try:
        detail.update(prune(ctx.resource("connect"), ctx.now, inbox_days=int(inbox_days)))
    except Exception as e:
        errors.append(mask_text(f"prune: {type(e).__name__}: {e}")[:300])
    if errors:
        return JobResult("failed", detail={**detail, "reason": "; ".join(errors)})
    return JobResult("ok", detail=detail)

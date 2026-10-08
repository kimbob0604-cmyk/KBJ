"""공개 내보내기 본체 — `export_all`(파일 쓰기)과 등록부 처리기 `run`(`public.export`, 05:30).

docs/p3_design.md §7, DATA_TIERS §3, ADR 0002 §2.2·0011(초안).

- 읽는 것: `pub_*` 만 — P3 에는 읽을 공개 표가 없다(공개 수집은 P5·P6). DB 는 연결 확인만 한다:
  연결이 읽기 전용이고 `kbj_public_export` 역할의 구성원인지(아니면 실패 — 앱 DSN 으로 대신 붙은
  것을 잡는다). 공개 표가 생기면 이 모듈에 파일 생성 함수를 더한다(SQL 은 `pub_` 스키마만 —
  `tests/unit/public_export/test_sql_schemas.py` 가 정적으로 본다).
- 그 밖에 쓰는 것: 거래 캘린더(코드 계산)·`config/calendar_events.yaml`(공개 설정).
- 이 패키지는 로그인 등급 코드(`kbj.data.private`·`kbj.engines`·`kbj.services.{api,collectors,
  engine}`·`kbj.store.repos`)를 import 하지 않는다(계약 ⑧ — S 가 pyproject 에 넣는다).
- 쓰기 순서: 파일마다 내용 검사(`check_payload`) → 임시 파일 → 이름 바꾸기, 마지막에 manifest.
  검사에 하나라도 걸리면 아무 파일도 바꾸지 않는다. manifest 에 없는 옛 `*.json` 은 지운다(남은
  파일이 다음 푸시에 섞이지 않게).
- 푸시·디스패치(`push.py`)는 `KBJ_PUBLIC_PUSH_ENABLED`(기본 false — [사용자 승인 필요]) 일 때만.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import psycopg
from pydantic import SecretStr

from kbj.config.markets import CalendarEvents, load_calendar_events
from kbj.config.settings import Settings
from kbj.core.calendar import TradingCalendar
from kbj.core.masking import mask_text
from kbj.services.public_export.calendar_json import build_calendar
from kbj.services.public_export.events_json import build_events
from kbj.services.public_export.manifest import (
    MANIFEST_NAME,
    NAME_RE,
    FileEntry,
    Manifest,
    PublicFile,
    check_payload,
    iso_kst,
    sha256_text,
)
from kbj.services.public_export.push import (
    PUBLIC_REPO,
    PushError,
    dispatch_pages,
    push_public_data,
    ssh_remote,
)
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.db import StoreError, connect

__all__ = [
    "EXPORT_ROLE",
    "PUBLIC_SUBDIR",
    "PublicExportError",
    "check_public_connection",
    "default_connect",
    "export_all",
    "run",
    "write_files",
]

PUBLIC_SUBDIR: Final = "public"
EXPORT_ROLE: Final = "kbj_public_export"
_SERVICE: Final = "public-export"
# 연결 확인 — 스키마 이름을 쓰지 않는다(이 패키지의 SQL 은 pub_ 만)
_CHECK_SQL: Final = (
    "SELECT current_setting('transaction_read_only'), pg_has_role(current_user, %s, 'MEMBER')"
)

ConnectFn = Callable[[], "psycopg.Connection[Any]"]


class PublicExportError(RuntimeError):
    """공개 내보내기 실패(문구에 값·접속 정보 없음)."""


@dataclass(frozen=True)
class _ExportDsn:
    """`kbj.store.db.connect` 가 읽는 모양 — 공개 내보내기 전용 DSN 을 database_url 자리에."""

    database_url: SecretStr | None


def default_connect(settings: Settings) -> ConnectFn | None:
    """`KBJ_PUBLIC_EXPORT_DATABASE_URL` 연결 공장. 없으면 None — 앱 DSN 으로 대신하지 않는다."""
    url = settings.public_export_database_url
    if url is None:
        return None
    dsn = _ExportDsn(url)

    def _connect() -> psycopg.Connection[Any]:
        try:
            return connect(dsn, service=_SERVICE)
        except StoreError as e:
            # connect 문구는 앱 DSN 이름(KBJ_DATABASE_URL) — 이 연결의 변수 이름으로 바꾼다
            raise PublicExportError(
                str(e).replace("KBJ_DATABASE_URL", "KBJ_PUBLIC_EXPORT_DATABASE_URL")
            ) from None

    return _connect


def check_public_connection(conn: psycopg.Connection[Any]) -> None:
    """읽기 전용 + `kbj_public_export` 구성원인지. 아니면 `PublicExportError`."""
    try:
        with conn.cursor() as cur:
            cur.execute(_CHECK_SQL, (EXPORT_ROLE,))
            row = cur.fetchone()
    except psycopg.Error as e:
        kind = type(e).__name__ + (f"({e.sqlstate})" if e.sqlstate else "")
        raise PublicExportError(f"공개 내보내기 연결 확인 실패: {kind}") from None
    if row is None:
        raise PublicExportError("공개 내보내기 연결 확인: 결과 없음")
    read_only, member = row
    if str(read_only).lower() != "on":
        raise PublicExportError(
            "공개 내보내기 연결이 읽기 전용이 아니다(default_transaction_read_only)"
        )
    if member is not True:
        raise PublicExportError(f"공개 내보내기 연결 역할이 {EXPORT_ROLE} 의 구성원이 아니다")


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_files(files: Sequence[PublicFile], out_dir: Path, *, now: datetime) -> Manifest:
    """봉투 파일들과 manifest 를 쓴다. 검사에 걸리면 아무것도 쓰지 않고 `PublicExportError`."""
    names = [f.name for f in files]
    if len(set(names)) != len(names):
        raise PublicExportError("같은 이름의 공개 파일이 두 번 있다")
    rendered: list[tuple[PublicFile, str]] = []
    problems: list[str] = []
    for f in files:
        text = f.render(now)
        problems += check_payload(f.name, text)
        rendered.append((f, text))
    entries = tuple(
        FileEntry(
            name=f.name,
            source=f.source,
            as_of=iso_kst(f.as_of),
            quality=f.quality,
            sha256=sha256_text(text),
        )
        for f, text in rendered
    )
    manifest = Manifest(generated_at=now, files=entries)
    manifest_text = manifest.render()
    problems += check_payload(MANIFEST_NAME, manifest_text)
    if problems:
        raise PublicExportError("공개 산출물 검사 실패: " + "; ".join(problems[:5]))
    out_dir.mkdir(parents=True, exist_ok=True)
    for f, text in rendered:
        _write_atomic(out_dir / f.name, text)
    keep = {*names, MANIFEST_NAME}
    for old in out_dir.glob("*.json"):
        if old.name not in keep and NAME_RE.fullmatch(old.name):
            old.unlink()
    _write_atomic(out_dir / MANIFEST_NAME, manifest_text)
    return manifest


def export_all(
    conn_pub: psycopg.Connection[Any] | None,
    out_dir: Path,
    *,
    now: datetime,
    cal: TradingCalendar,
    events: CalendarEvents | None = None,
    settings: Settings | None = None,
) -> Manifest:
    """공개 파일 전부를 `out_dir` 에 만든다.

    `conn_pub` 는 공개 내보내기 역할 연결(P3 은 확인만). None 이면 DB 없이 코드 계산·공개
    설정 파일만 — 로컬 빌드(`python -m kbj.services.public_export build`)·CI 용.
    처리기 `run` 은 늘 연결을 준다.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 는 aware datetime")
    if conn_pub is not None:
        check_public_connection(conn_pub)
    evs = events if events is not None else load_calendar_events(settings=settings)
    files: list[PublicFile] = [build_calendar(now, cal), build_events(now, evs)]
    # P5~: credit.json·spread.json·exports.json …
    #      (pub_market_stats·pub_macro·pub_trade 를 conn_pub 로 읽는다)
    return write_files(files, out_dir, now=now)


def _failed(reason: str, settings: Settings | None, **detail: Any) -> JobResult:
    secrets = settings.secret_values() if settings is not None else []
    return JobResult("failed", detail={**detail, "reason": mask_text(reason, secrets)[:300]})


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.public_export:run`).

    자원(선택 — 시험·시뮬레이션이 주입): `public_connect`(연결 공장), `kr`(거래 캘린더),
    `public_out_dir`(산출 디렉터리), `public_remote`(푸시 원격 — 시험은 로컬 bare 저장소),
    `dispatch_client`(httpx.Client — 디스패치 시험용).
    """
    settings = ctx.settings
    if settings is None:
        return _failed("설정이 주입되지 않았다", None)
    out_dir = Path(ctx.resources.get("public_out_dir") or settings.data_dir / PUBLIC_SUBDIR)
    cal: TradingCalendar = ctx.resources.get("kr") or TradingCalendar.default()
    connect_fn: ConnectFn | None = ctx.resources.get("public_connect") or default_connect(settings)
    if connect_fn is None:
        return _failed(
            "공개 내보내기 DSN(KBJ_PUBLIC_EXPORT_DATABASE_URL)이 없다 — "
            "앱 DSN 으로 대신하지 않는다",
            settings,
        )
    try:
        events = load_calendar_events(settings=settings)
        with connect_fn() as conn:
            manifest = export_all(conn, out_dir, now=ctx.now, cal=cal, events=events)
    except Exception as e:  # 실행기가 재시도·알림 — 사유는 가린다
        return _failed(f"내보내기: {type(e).__name__}: {e}", settings)
    detail: dict[str, Any] = {
        "files": list(manifest.names),
        "quality": {f.name: f.quality for f in manifest.files},
    }
    if not settings.public_push_enabled:
        detail["push"] = "disabled"
        return JobResult("ok", rows=len(manifest.files), detail=detail)
    key = settings.public_deploy_key_path
    token = settings.github_dispatch_token
    if key is None or token is None:
        return _failed(
            "푸시가 켜졌는데 배포 키 경로 또는 디스패치 토큰이 없다([사용자 승인 필요] 설정)",
            settings,
            **detail,
        )
    try:
        remote = str(ctx.resources.get("public_remote") or ssh_remote(PUBLIC_REPO))
        pushed = push_public_data(
            out_dir, enabled=True, remote=remote, deploy_key_path=key, now=ctx.now
        )
        detail["push"] = pushed.reason
        if pushed.pushed:
            dispatch_pages(token, client=ctx.resources.get("dispatch_client"))
            detail["dispatch"] = "sent"
    except PushError as e:
        return _failed(f"푸시·디스패치: {e}", settings, **detail)
    return JobResult("ok", rows=len(manifest.files), detail=detail)

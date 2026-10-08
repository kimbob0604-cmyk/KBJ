"""`python -m kbj.services.api [serve | openapi [--check] [--out PATH] | hash-password]`
(docs/p3_design.md §1.6·§5.1·§5.4).

- `serve`: compose 서비스 `api` — `uvicorn.run(create_app(...), host=KBJ_API_HOST,
  port=KBJ_API_PORT)`.
  접근 로그는 끈다(IP·경로를 남기지 않는다 — §5.4 로그 규칙). TLS 는 VM 역방향 프록시(레포 밖).
- `openapi`: 스키마를 `web/src/api/openapi.json` 에 쓴다. `--check` 는 낡았으면 종료 코드 1
  (W2 가 `npm run gen:types` 로 TS 타입을 만든다).
- `hash-password`: `getpass` 로 두 번 입력 → **stdout 에 해시만**(로그·파일 없음). 사용자 이름은
  묻지 않는다(`KBJ_WEB_USER` 는 평문 설정).
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, Any, Final

from kbj.config.files import REPO_ROOT

OPENAPI_PATH: Final = REPO_ROOT / "web" / "src" / "api" / "openapi.json"
SERVICE: Final = "api"


def openapi_text() -> str:
    """현재 코드의 스키마(결정적 — 같은 코드면 같은 글자)."""
    from redis import Redis

    from kbj.config.markets import load_calendar_events, load_markets
    from kbj.config.settings import Settings
    from kbj.core.calendar import TradingCalendar
    from kbj.services.api.app import create_app
    from kbj.store.repos import memory_repos

    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue] — pydantic-settings 인자
    app = create_app(
        settings,
        redis=Redis(),  # 연결하지 않는다(스키마만 — 명령을 보내지 않는다)
        repos=memory_repos(),
        cal=TradingCalendar.default(),
        markets=load_markets(),
        events=load_calendar_events(),
        static_dir=False,
    )
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def _openapi(args: argparse.Namespace, out: IO[str]) -> int:
    path = Path(args.out) if args.out else OPENAPI_PATH
    text = openapi_text()
    if args.check:
        cur = path.read_text(encoding="utf-8") if path.exists() else None
        if cur != text:
            print(f"낡았다: {path} — `python -m kbj.services.api openapi` 로 다시 만든다", file=out)
            return 1
        print(f"최신: {path}", file=out)
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"썼다: {path}", file=out)
    return 0


def _hash_password(out: IO[str], err: IO[str], ask: Callable[[str], str]) -> int:
    from kbj.services.api.auth import hash_password

    first = ask("비밀번호: ")
    if not first:
        print("빈 비밀번호는 받지 않는다", file=err)
        return 1
    if len(first) < 12:
        print("12자 이상으로 정한다", file=err)
        return 1
    if ask("다시: ") != first:
        print("두 입력이 다르다", file=err)
        return 1
    print(hash_password(first), file=out)
    return 0


def _serve() -> int:  # pragma: no cover — 운영 진입점(uvicorn·Postgres·Redis 필요)
    import uvicorn

    from kbj.config.settings import Settings
    from kbj.services.api.app import create_app
    from kbj.services.api.cache import PgDataVersionSource
    from kbj.services.runtime.heartbeat import connect_redis
    from kbj.store.db import connect
    from kbj.store.repos import pg_repos

    settings = Settings()
    if settings.redis_url is None or settings.database_url is None:
        print("KBJ_DATABASE_URL·KBJ_REDIS_URL 이 필요하다", file=sys.stderr)
        return 2

    def conn() -> Any:
        return connect(settings, service=SERVICE)

    app = create_app(
        settings,
        redis=connect_redis(settings.redis_url),
        repos=pg_repos(conn),
        data_versions=PgDataVersionSource(conn),
    )
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        server_header=False,
    )
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
    ask: Callable[[str], str] = getpass.getpass,
) -> int:
    ap = argparse.ArgumentParser(prog="python -m kbj.services.api")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="API 서버(uvicorn)")
    op = sub.add_parser("openapi", help="web/src/api/openapi.json 쓰기·검사")
    op.add_argument("--check", action="store_true", help="낡았으면 종료 코드 1")
    op.add_argument("--out", help="쓸 경로(기본 web/src/api/openapi.json)")
    sub.add_parser("hash-password", help="KBJ_WEB_PASSWORD_HASH 값 만들기(stdout 에만)")
    args = ap.parse_args(argv)
    o = out or sys.stdout
    e = err or sys.stderr
    if args.cmd == "openapi":
        return _openapi(args, o)
    if args.cmd == "hash-password":
        return _hash_password(o, e, ask)
    return _serve()


if __name__ == "__main__":
    raise SystemExit(main())

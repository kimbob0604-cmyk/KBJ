"""P3 데모 서버 — 합성 데이터만(키 불필요, DB·Redis 는 선택). `scripts/demo_p3.sh` 가 띄운다.

- 데이터: `tests/unit/api/api_world.py` 의 합성 세계(고정 시드 합성 원장 `ledger_gen` + 신고가 골든
  입력 — 둘 다 합성)를 저장소에 채운다. 로그인 등급 실데이터는 읽지 않는다.
- 저장소: 기본은 메모리 저장소 + fakeredis(프로세스 안). `KBJ_DEMO_DATABASE_URL`(일회용 Timescale —
  `demo_p3.sh --db`)이 있으면 마이그레이션 뒤 같은 합성 세계를 Pg 저장소에 채워 읽고,
  `KBJ_DEMO_REDIS_URL` 이 있으면 세션·로그인 잠금을 실제 Redis 에 둔다.
- 시계: `--at close`(기본) = 합성 마지막 거래일 18:00 KST,
  `--at intraday` = 다음 거래일 장중 슬롯 10:05 KST 로 고정(화면의 KST 시계는 브라우저 시각).
- 로그인: 사용자 `demo`, 비밀번호는 환경변수 `KBJ_DEMO_PASSWORD`(데모 스크립트가 실행마다 무작위로
  만든다 — 레포·로그에 남지 않는다). 루프백에서만 연다(비보안 쿠키 — 개발 모드).
- 정적 파일: 로그인 SPA 빌드(`web/dist-login`) — 같은 출처 `/api`.

    KBJ_DEMO_PASSWORD=... uv run python scripts/demo_p3_server.py --port 8765
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOOPBACK = "127.0.0.1"
DEMO_USER = "demo"
# 데모 금액 규모 — 시험용 합성 원장(종목 거래대금 중앙값 약 100억·순매수 ±수억)을 실제 시장 규모로
# 키운다(거래대금 상위 수천억~1조대·투자자별 순매수 수백억). 스크리너 기본 필터(일평균 300억 —
# config/markets.yaml)는 그대로 두고 데이터만 키운다. 시험은 원래 규모를 쓴다(이 스크립트만).
TURNOVER_SCALE = 50  # 종목·지수 거래대금·거래량·상장주식수(시총) — 회전율은 그대로
INVESTOR_SCALE = 100  # 투자자별 순매수(정수 배 — 검산 ①② 합 0·7구분 합이 그대로 성립)


def scale_market(m: Any) -> Any:
    """금액 규모를 키운 사본(가격·지수 값·ETF 는 그대로). 정수 배라 검산 ①② 가 유지된다."""
    from dataclasses import replace

    def mul(v: int | None, k: int) -> int | None:
        return None if v is None else v * k

    snaps = tuple(
        replace(
            s,
            turnover=mul(s.turnover, TURNOVER_SCALE),
            volume=mul(s.volume, TURNOVER_SCALE),
            mktcap=mul(s.mktcap, TURNOVER_SCALE),
            shares=mul(s.shares, TURNOVER_SCALE),
        )
        for s in m.snaps
    )
    bars = tuple(
        replace(b, turnover=mul(b.turnover, TURNOVER_SCALE), volume=mul(b.volume, TURNOVER_SCALE))
        for b in m.bars
    )
    idx = tuple(replace(b, turnover=mul(b.turnover, TURNOVER_SCALE)) for b in m.index_bars)
    invs = tuple(replace(r, net_value=mul(r.net_value, INVESTOR_SCALE)) for r in m.investors)
    mkt = tuple(replace(r, net_value=mul(r.net_value, INVESTOR_SCALE)) for r in m.market_investors)
    return replace(m, snaps=snaps, bars=bars, index_bars=idx, investors=invs, market_investors=mkt)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="demo_p3_server")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--dist", default=str(ROOT / "web" / "dist-login"))
    p.add_argument(
        "--at",
        choices=("close", "intraday"),
        default="close",
        help="서버 시계: close = 합성 마지막 거래일 18:00, intraday = 다음 거래일 장중 슬롯 10:05",
    )
    args = p.parse_args(argv)

    password = os.environ.get("KBJ_DEMO_PASSWORD", "")
    if len(password) < 12:
        print("KBJ_DEMO_PASSWORD(12자 이상)가 필요하다 — demo_p3.sh 가 만든다", file=sys.stderr)
        return 2
    dist = Path(args.dist)
    if not (dist / "index.html").is_file():
        print(f"로그인 빌드가 없다: {dist} — cd web && npm run build:login", file=sys.stderr)
        return 2

    import uvicorn
    from pydantic import SecretStr

    from kbj.config.settings import Settings
    from kbj.services.api.auth import hash_password
    from tests.unit.api.api_world import build_world

    world = build_world(transform=scale_market)
    if args.at == "intraday":
        world.clock.set(world.slot + timedelta(minutes=5))
    overrides: dict[str, Any] = {}
    store = "메모리 저장소 + fakeredis"
    db_url = os.environ.get("KBJ_DEMO_DATABASE_URL", "")
    if db_url:
        # 실제 Postgres+Timescale(데모 스크립트가 띄운 일회용 컨테이너): 마이그레이션 → 같은
        # 합성 세계를 Pg 저장소에 채우고 API 가 그 DB 를 읽는다(+ ops 표 기반 응답 캐시 버전)
        import psycopg

        from kbj.services.api.cache import PgDataVersionSource
        from kbj.store.migrate import migrate
        from kbj.store.repos import pg_repos
        from tests.unit.api.api_world import populate

        applied = migrate(db_url)

        def conn() -> Any:
            return psycopg.connect(db_url)

        repos = pg_repos(conn)
        populate(repos, world.market, world.cal)
        overrides["repos"] = repos
        overrides["data_versions"] = PgDataVersionSource(conn)
        store = f"Postgres+Timescale(마이그레이션 {len(applied)}개)"
    redis_url = os.environ.get("KBJ_DEMO_REDIS_URL", "")
    if redis_url:
        from redis import Redis

        overrides["redis"] = Redis.from_url(redis_url)
        store += " + Redis"
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue] — pydantic-settings 인자
        web_user=DEMO_USER,
        web_password_hash=SecretStr(hash_password(password, n=2**14)),
        web_cookie_secure=False,  # 루프백 개발 모드(설정 검증이 루프백 밖이면 거절한다)
        api_host=LOOPBACK,
        api_port=args.port,
        public_base_url=f"http://{LOOPBACK}:{args.port}",
        git_commit="demo",
    )
    app = world.app(settings=settings, static_dir=dist, **overrides)
    at = world.clock().isoformat(timespec="minutes")
    last = world.market.last_day
    print(f"KBJ P3 데모: http://{LOOPBACK}:{args.port}/  (사용자 {DEMO_USER}, 서버 시계 {at})")
    print(f"합성 마지막 거래일 {last} · 저장소 {store}")
    sys.stdout.flush()
    uvicorn.run(app, host=LOOPBACK, port=args.port, access_log=False, server_header=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""KBJ 데모 서버 — 합성 데이터만(키·DB·외부 호출 없음).

`scripts/demo.sh`·`docker-compose.demo.yml` 이 띄운다.

- 데이터: `tests/unit/api/api_world.py` 의 합성 세계(고정 시드 합성 원장 `ledger_gen` + 신고가 골든
  입력 — 둘 다 합성)를 저장소에 채운다. 로그인 등급 실데이터는 읽지 않는다(docs/adr/0034).
- 저장소: 기본은 메모리 저장소 + fakeredis(프로세스 안). `KBJ_DEMO_DATABASE_URL`(일회용 Timescale —
  `demo.sh --db`)이 있으면 마이그레이션 뒤 같은 합성 세계를 Pg 저장소에 채워 읽고,
  `KBJ_DEMO_REDIS_URL` 이 있으면 세션·로그인 잠금을 실제 Redis 에 둔다.
- 시계: `--at close`(기본 — 환경변수 `KBJ_DEMO_AT` 로도) = 합성 마지막 거래일 18:00 KST,
  `--at intraday` = 다음 거래일 장중 슬롯 10:05 KST 로 고정(화면의 KST 시계는 브라우저 시각).
- 로그인: 사용자 `demo`, 비밀번호는 환경변수 `KBJ_DEMO_PASSWORD`(12자 이상). `demo.sh` 는
  실행마다 무작위로 만들고, 데모 compose 는 고정 데모값(README — 합성 데이터 전용, 운영 로그인과
  무관)을 쓴다. 쿠키는 비보안(개발 모드 — `Settings.api_host` 는 루프백 그대로). 공개 주소
  (`public_base_url`)는 두지 않아 Origin 검사가 요청 자신의 출처와 비교한다 — `127.0.0.1`·
  `localhost` 어느 쪽으로 열어도 로그인·쓰기가 된다.
- 묶는 주소(`--bind`): 기본 `127.0.0.1`. `0.0.0.0` 은 `KBJ_DEMO_CONTAINER=1`(데모 컨테이너 안 —
  호스트 쪽 포트는 compose 가 `127.0.0.1:8765` 에만 연다)일 때만 허용하고, 그 밖은 종료 2.
- 정적 파일: 로그인 SPA 빌드(`web/dist-login`) — 같은 출처 `/api`.
- 페이지 6(매크로): 합성 P5 세계(`tests/fixtures/synthetic/p5_world.py` — 고정 시드)의 매크로
  시리즈·경제 캘린더·금투협 통계를 데모 날짜로 옮겨(9주 — 요일 그대로, 월별은 2개월) 싣는다
  (`_load_p6_macro`).
- 페이지 3(P7 — 진단·GEX): 합성 P7 세계(`tests/fixtures/synthetic/p7_world.py` — 고정 시드)의 진단
  입력 시계열을 같은 메모리 저장소에 싣고 데모 시계 그날까지 `diag.public`·`diag.daily` 를 실제
  처리기로 돌려 이력을 만들며, GEX 는 실제 엔진(`EngineService`)이 합성 체인(월물 202610 — Black-76
  이론가)으로 데모 시계 그날 주간 사이클을 돈다(`_load_p7`).
- 페이지 11·12(P8 — 규칙 알림·백테스트·저널): 합성 P8 세계(`tests/fixtures/synthetic/p8_world.py`
  — 고정 시드)로 규칙 둘(E1·E8)을 만들고 `rules.outcome`·`rules.backtest_nightly` 를 실제 처리기로
  돌린 규칙 저장소를 데모 저장소의 규칙 표로 끼우고(`backtest_api_world`), 일지·매매·추천은
  페이지 12 웹 픽스처와 같은 합성 쓰기를 같은 API 로 넣는다(`_load_p8`).
- 페이지 9(테마·밸류체인 — ADR 0039): 합성 테마 마스터(`tests/fixtures/synthetic/theme_master.py`
  — 고정 시드, 실제 stock-dashboard 내용 아님)를 작업과 같은 검증으로 저장소 둘(로그인 편입·공개
  분류)에 싣고, 강도의 시세는 신고가 골든 합성 300종목을 보드 마지막 날 종목 하루로 끼운다
  (`_load_p9` — `tests/unit/api/theme_world.py`).
- Pg 모드(`--db`)에서는 페이지 3·6·9·11·12 를 싣지 않는다 — '아직 없음' 안내.

    KBJ_DEMO_PASSWORD=... uv run python scripts/demo_server.py --port 8765
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOOPBACK = "127.0.0.1"
ANY_ADDR = "0.0.0.0"  # noqa: S104 — 데모 컨테이너 안에서만(KBJ_DEMO_CONTAINER=1)
CONTAINER_ENV = "KBJ_DEMO_CONTAINER"
PASSWORD_ENV = "KBJ_DEMO_PASSWORD"  # noqa: S105 — 환경변수 이름(값 아님)
AT_ENV = "KBJ_DEMO_AT"
AT_CHOICES = ("close", "intraday")
MIN_PASSWORD = 12
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


def _shift(x: Any, *, days: int, months: int, monthly: bool) -> Any:
    """날짜·시각 하나를 옮긴다 — 월별 값은 달 단위(공표일 5일 그대로), 그 밖은 일 단위."""
    from datetime import date, datetime

    if monthly and isinstance(x, (date, datetime)):
        n = x.year * 12 + (x.month - 1) + months
        return x.replace(year=n // 12, month=n % 12 + 1)
    if isinstance(x, (date, datetime)):
        return x + timedelta(days=days)
    return x


def _shift_rows(rows: Any, *, days: int, months: int) -> list[Any]:
    """p5 매크로 행·캘린더·금투협 통계를 데모 날짜로 옮긴 사본(합성 값은 그대로)."""
    import re
    from dataclasses import fields, replace
    from datetime import date

    out: list[Any] = []
    for r in rows:
        period = getattr(r, "period", None)
        monthly = isinstance(period, str) and bool(re.fullmatch(r"\d{4}-\d{2}", period))
        kw: dict[str, Any] = {
            f.name: _shift(getattr(r, f.name), days=days, months=months, monthly=monthly)
            for f in fields(r)
            if isinstance(getattr(r, f.name), date)
        }
        if isinstance(period, str):
            if monthly:
                n = int(period[:4]) * 12 + int(period[5:]) - 1 + months
                kw["period"] = f"{n // 12:04d}-{n % 12 + 1:02d}"
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
                kw["period"] = (date.fromisoformat(period) + timedelta(days=days)).isoformat()
            else:  # 분기·연 단위는 p5 세계에 없다 — 생기면 여기서 멈춘다(조용히 어긋나지 않게)
                raise ValueError(f"옮길 수 없는 period 모양: {period!r}")
        if hasattr(r, "event_id"):  # 경제 캘린더 — 행사 id 의 날짜·제목의 'N월'도 같이 옮긴다
            old, new = r.date.isoformat(), kw["date"].isoformat()
            kw["event_id"] = r.event_id.replace(old, new)
            kw["title"] = re.sub(
                r"(\d{1,2})월", lambda m: f"{(int(m.group(1)) - 1 + months) % 12 + 1}월", r.title
            )
        out.append(replace(r, **kw))
    return out


def _load_p6_macro(world: Any) -> None:
    """페이지 6(매크로)·상단 띠 매크로 칩 — 합성 P5 세계를 데모 날짜로 옮겨 싣는다.

    p5 세계 시계(`CLOCK` 2026-11-17 화)와 데모 마지막 거래일의 차이는 7의 배수여야 한다(요일
    그대로 — 주말 없는 일별 시리즈가 주말로 가지 않게). 아니면 멈춘다."""
    from kbj.services.engine.diag_pub import AA3Y, KTB3Y
    from tests.fixtures.synthetic.p5_world import TODAY as P5_TODAY
    from tests.fixtures.synthetic.p5_world import build_world as build_p5_world

    demo_day = world.market.last_day
    days = (demo_day - P5_TODAY).days
    if days % 7:
        raise RuntimeError(f"p5 세계를 옮길 수 없다: {P5_TODAY} → {demo_day} 는 7일 배수가 아니다")
    months = (demo_day.year * 12 + demo_day.month) - (P5_TODAY.year * 12 + P5_TODAY.month)
    w = build_p5_world()
    repos, now, by = world.repos, world.clock(), "demo"
    pub_meta = [m for m in w.macro_meta if m.tier == "public"]
    prv_meta = [m for m in w.macro_meta if m.tier == "private"]
    repos.macro.upsert_meta(pub_meta, loaded_by=by, now=now)
    repos.macro_prv.upsert_meta(prv_meta, loaded_by=by, now=now)
    # 진단(페이지 3 — _load_p7)이 먼저 실은 시리즈·통계는 건너뛴다(두 합성 세계를 한 시리즈에
    # 섞지 않게 — 진단 이력이 그 값으로 계산됐다)
    taken = {sid for sid in (KTB3Y, AA3Y) if repos.macro.series([sid], "0000").get(sid)}
    pub_rows = [
        r for r in _shift_rows(w.macro_rows("public"), days=days, months=months)
        if r.series_id not in taken
    ]  # fmt: skip
    prv_rows = _shift_rows(w.macro_rows("private"), days=days, months=months)
    repos.macro.upsert_series(pub_rows, loaded_by=by, received_at=now)
    repos.macro_prv.upsert_series(prv_rows, loaded_by=by, received_at=now)
    cal = _shift_rows(w.calendar, days=days, months=months)
    repos.macro.upsert_calendar([e for e in cal if e.is_public], loaded_by=by, received_at=now)
    repos.macro_prv.upsert_calendar(
        [e for e in cal if not e.is_public], loaded_by=by, received_at=now
    )
    if not taken:  # 금투협 통계(신용잔고·예탁금)도 진단이 실었으면 그 값을 쓴다
        stats = _shift_rows(w.market_stats, days=days, months=months)
        repos.market_stats.upsert(stats, loaded_by=by, received_at=now)


def _load_p7(world: Any) -> None:
    """페이지 3 합성 데이터 — 진단 이력(데모 그날까지)과 GEX 엔진 주간 사이클(그날)."""
    from dataclasses import replace
    from datetime import date, datetime, time
    from decimal import Decimal

    import fakeredis

    from kbj.config.diag import load_diag
    from kbj.config.markets import load_markets
    from kbj.core.calendar import TradingCalendar, state_at
    from kbj.core.gex.records import ExpiryRecord
    from kbj.core.time import KST
    from kbj.services.engine import diag_pub
    from kbj.services.engine.diag import run_day
    from kbj.services.engine.gex import EngineContext, EnginePublisher, EngineService
    from kbj.services.runtime import MemoryHealthSink
    from kbj.services.runtime.bus import ChainReady
    from kbj.services.scheduler.handlers import JobContext
    from tests.fakes.ws_server import FakeChain, FakeSeries
    from tests.fixtures.synthetic.p7_world import build_world as build_p7_world
    from tests.unit.api.web_fixtures_p7.diag import HISTORY_DAYS, world_values
    from tests.unit.engines.diagnosis.diag_seed import seed_public
    from tests.unit.engines.gex.engine_inputs import fut_row, seed_master, synthetic_series

    repos = world.repos
    now: datetime = world.clock()
    cal = TradingCalendar.default()
    cfg, mk = load_diag(), load_markets()
    w = build_p7_world()
    # p7 세계는 2026-12-10 까지 있다 — 데모 시계에 이미 공표된 점만 싣는다(미래 공표 값이
    # 저장소·띠에 섞이지 않게. 진단 처리기는 그날까지만 돌린다). 실적 예정은 미래 일정이라 그대로
    seen = {k: tuple(p for p in v if p.published_at <= now) for k, v in w.series.items()}
    seed_public(repos, replace(w, series=seen), received_at=now)
    today = now.astimezone(KST).date()
    values = world_values(w)
    for d in [x for x in w.days if x <= today][-HISTORY_DAYS:]:
        diag_pub.run(
            JobContext(
                job="diag.public", as_of=d.isoformat(), run_id=f"demo:{d}", attempt=1,
                now=datetime.combine(d, time(8, 40), tzinfo=KST),
                resources={"repos": repos, "kr": cal, "diag_config": cfg},
            )
        )  # fmt: skip
        if datetime.combine(d, time(16, 0), tzinfo=KST) <= now:
            run_day(
                repos, day=d, now=datetime.combine(d, time(16, 0), tzinfo=KST), cfg=cfg,
                markets=mk, cal=cal, market_values=values,
            )  # fmt: skip
    # GEX — 실제 엔진이 합성 체인으로 그날 주간 사이클(10분마다, 데모 시계까지)
    month, expiry = "202610", date(2026, 10, 8)
    chain = FakeChain(series=(FakeSeries("", month, expiry, Decimal("745.0"), Decimal("1595.0")),))
    r = fakeredis.FakeRedis()
    seed_master(r, "\n".join(chain.master_lines()) + "\n")
    t = {"now": now, "mono": 0.0}
    repos.gex.seed_expiries(
        [ExpiryRecord(ts=now, trade_date=today, session="day", mrkt_cls="", expiry=month,
                      last_trade_date=expiry, source="kis")]
    )  # fmt: skip
    eng = EngineService(
        repos.gex, repos.gex, EnginePublisher(r), EngineContext(r, cal, clock=lambda: t["mono"]),
        cal, health=MemoryHealthSink(), now=lambda: t["now"], mono=lambda: t["mono"],
    )  # fmt: skip
    at = datetime.combine(today, time(9, 10), tzinfo=KST)
    end = min(now, datetime.combine(today, time(15, 30), tzinfo=KST))
    while at <= end:
        info = state_at(at, cal)
        if info.trade_date is not None and info.session == "day":
            t["now"], t["mono"] = at, t["mono"] + 600
            repos.gex.seed_chain(synthetic_series("", month, expiry, at))
            repos.gex.seed_futures([fut_row(at - timedelta(seconds=5), remaining_days=17)])
            eng.on_ready(
                ChainReady(ts=at, trade_date=info.trade_date, session="day", series=(("", month),))
            )
        at += timedelta(minutes=10)


def _load_p8(world: Any) -> None:
    """페이지 11·12 — 합성 P8 세계(고정 시드, 실데이터 아님).

    규칙 표(규칙·판·신호·성과·백테스트·일치 감시)는 웹 픽스처와 같은 세계(`backtest_api_world`
    — 실제 처리기 rules.outcome·rules.backtest_nightly)의 규칙 저장소를 데모 저장소에 끼운다
    (개인 쓰기 저장소는 같은 메모리 표를 다시 본다). 일지·매매·추천은 페이지 12 웹 픽스처의
    합성 쓰기(`journal._seed` — 쓰기 API 를 그대로 거친다)를 데모 세계에 넣는다."""
    from tests.unit.api.web_fixtures_p8 import journal as jr
    from tests.unit.api.web_fixtures_p8.backtest import backtest_api_world

    bt = backtest_api_world()
    world.repos.rules = bt.repos.rules
    world.repos.__post_init__()  # personal 이 새 규칙 표·기존 일지 표를 같이 보게(MemoryRepos 관례)
    days = jr.add_market(world)
    jr._seed(world, days)  # pyright: ignore[reportPrivateUsage] — 픽스처 합성 쓰기 그대로
    # 픽스처는 일지 종목을 관심종목 main 에도 넣는다 — 데모의 다른 페이지(8 종목 상세의 첫 종목)가
    # 바뀌지 않게 같은 쓰기 API 로 되돌린다(일지·매매·추천은 그대로)
    c, h = jr._login(world)  # pyright: ignore[reportPrivateUsage]
    for code in jr.CODES:
        r = c.delete(f"/api/watchlist/main/{code}", headers=h)
        if r.status_code != 200:
            raise RuntimeError(f"관심종목 되돌리기 실패: HTTP {r.status_code}")


def _load_p9(world: Any) -> None:
    """페이지 9 — 합성 테마 마스터(고정 시드, 실데이터 아님) + 보드 종목 하루(합성 골든 300종목)."""
    from tests.unit.api.theme_world import seed_board, seed_themes

    seed_themes(world.repos, now=world.clock())
    day = world.repos.board.last_day(world.clock().date())
    if day is None:
        raise RuntimeError("보드 마지막 날이 없다(합성 세계)")
    seed_board(world.repos, day)


def build_demo_world(at: str = "close", *, with_extras: bool = True) -> Any:
    """데모 합성 세계(메모리 저장소).

    `with_extras=False` 는 Pg 모드 — 페이지 3·6·9·11·12 를 싣지 않는다."""
    from tests.fixtures.synthetic.p6_world import build_world as build_p6_world
    from tests.unit.api.api_world import build_world
    from tests.unit.api.web_fixtures_p6.explore import run_derive

    world = build_world(transform=scale_market)
    # P6 페이지 7 — 합성 수출입 세계(고정 시드, 실데이터 아님)를 같은 저장소에 싣고 파생까지
    build_p6_world().fill(world.repos.trade)
    run_derive(world.repos.trade)
    if at == "intraday":
        world.clock.set(world.slot + timedelta(minutes=5))
    if with_extras:
        _load_p7(world)  # 페이지 3 — 합성 P7 세계(진단 이력 + GEX 엔진 사이클)
        _load_p6_macro(world)  # 페이지 6 — 합성 P5 매크로(데모 날짜로 옮김 — 진단 입력 다음)
        _load_p8(world)  # 페이지 11·12 — 합성 P8 세계(규칙·신호·성과·백테스트 + 일지·매매)
        _load_p9(world)  # 페이지 9 — 합성 테마 마스터 + 보드 종목 하루(ADR 0039)
    return world


def attach_ops(app: Any, now: datetime) -> None:
    """페이지 13(운영 — ADR 0037)을 데모에 싣는다: 합성 1주 운영 기록(모든 작업 성공·브리핑 매일
    1통·끊김 없음 — `tests/unit/ops/ops_world.week_source`)을 데모 시계까지 7일로 두고, 하트비트는
    모두 5초 전(합성). 운영 DB 가 없는 데모라 이것이 없으면 페이지 13 은 404 no_data(.msg)다."""
    from kbj.core.time import KST
    from kbj.services.api.readers.ops import STATE_ATTR, OpsContext
    from tests.unit.ops.ops_world import EXPECT, fresh_ages, week_source

    end = now.astimezone(KST).date()
    src = week_source(start=end - timedelta(days=6), end=end)
    # 데모 시계 뒤의 실행·발송은 싣지 않는다(미래 기록 금지 — 진단 입력과 같은 규칙)
    src.runs = [r for r in src.runs if r.started_at is None or r.started_at <= now]
    src.notify = [n for n in src.notify if n.requested_at <= now]
    setattr(app.state, STATE_ATTR, OpsContext(src, EXPECT, fresh_ages(now)))


def demo_app(world: Any, settings: Any, **kw: Any) -> Any:
    """데모 앱 = 합성 세계 앱 + 페이지 13 운영 기록(메모리 모드만 — Pg 모드는 ops 표를 읽는다)."""
    app = world.app(settings=settings, **kw)
    if "repos" not in kw:
        attach_ops(app, world.clock())
    return app


def demo_settings(password: str, port: int) -> Any:
    """데모 설정 — 루프백 개발 모드(비보안 쿠키), 공개 주소 없음(Origin = 요청 자신의 출처)."""
    from pydantic import SecretStr

    from kbj.config.settings import Settings
    from kbj.services.api.auth import hash_password

    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue] — pydantic-settings 인자
        web_user=DEMO_USER,
        web_password_hash=SecretStr(hash_password(password, n=2**14)),
        web_cookie_secure=False,  # 루프백 개발 모드(설정 검증이 루프백 밖이면 거절한다)
        api_host=LOOPBACK,
        api_port=port,
        public_base_url=None,
        git_commit="demo",
    )


def parse_args(argv: list[str] | None, env: Mapping[str, str]) -> argparse.Namespace | str:
    """인자 해석 + 검사. 잘못이면 안내 글자(종료 2), 맞으면 Namespace."""
    p = argparse.ArgumentParser(prog="demo_server")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--bind", default=LOOPBACK, help="묶을 주소(0.0.0.0 은 컨테이너 안에서만)")
    p.add_argument("--dist", default=str(ROOT / "web" / "dist-login"))
    p.add_argument(
        "--at",
        choices=AT_CHOICES,
        default=None,
        help="서버 시계(기본 KBJ_DEMO_AT 또는 close): close = 합성 마지막 거래일 18:00, "
        "intraday = 다음 거래일 장중 슬롯 10:05",
    )
    args = p.parse_args(argv)
    if args.at is None:
        args.at = env.get(AT_ENV, "") or "close"
        if args.at not in AT_CHOICES:
            return f"{AT_ENV} 는 {'·'.join(AT_CHOICES)} 중 하나여야 한다"
    if args.bind == ANY_ADDR:
        if env.get(CONTAINER_ENV, "") != "1":
            return f"--bind {ANY_ADDR} 은 데모 컨테이너 안에서만({CONTAINER_ENV}=1)"
    elif args.bind != LOOPBACK:
        return f"--bind 는 {LOOPBACK}(기본) 또는 컨테이너 안의 {ANY_ADDR} 만"
    if len(env.get(PASSWORD_ENV, "")) < MIN_PASSWORD:
        return f"{PASSWORD_ENV}({MIN_PASSWORD}자 이상)가 필요하다 — demo.sh·데모 compose 가 넣는다"
    return args


def main(argv: list[str] | None = None) -> int:
    parsed = parse_args(argv, os.environ)
    if isinstance(parsed, str):
        print(parsed, file=sys.stderr)
        return 2
    args = parsed
    dist = Path(args.dist)
    if not (dist / "index.html").is_file():
        print(f"로그인 빌드가 없다: {dist} — cd web && npm run build:login", file=sys.stderr)
        return 2

    import uvicorn

    db_url = os.environ.get("KBJ_DEMO_DATABASE_URL", "")
    world = build_demo_world(args.at, with_extras=not db_url)
    overrides: dict[str, Any] = {}
    store = "메모리 저장소 + fakeredis"
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
    settings = demo_settings(os.environ[PASSWORD_ENV], args.port)
    app = demo_app(world, settings, static_dir=dist, **overrides)
    at = world.clock().isoformat(timespec="minutes")
    last = world.market.last_day
    # 안내 주소는 늘 루프백 — 컨테이너 안(0.0.0.0)도 호스트 쪽은 compose 가 127.0.0.1:8765 에만 연다
    print(f"KBJ 데모: http://{LOOPBACK}:{args.port}/  (사용자 {DEMO_USER}, 서버 시계 {at})")
    print(f"합성 마지막 거래일 {last} · 저장소 {store} · 묶은 주소 {args.bind}")
    sys.stdout.flush()
    uvicorn.run(app, host=args.bind, port=args.port, access_log=False, server_header=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

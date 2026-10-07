"""하루 통합 시험 — 2026-09-28(월) 07:59 → 09-29(화) 06:05 를 가짜 시계 하나로
(docs/phase1_design.md §10 통합, PLAN §4.6).

스택: compose 시험 스택(docker-compose.yml + tests/integration/docker-compose.test.yml)에 redis·db
만 띄운다 — 프로젝트 이름 gexlab-fd-<무작위>, 호스트 127.0.0.1 임의 포트, 임시 폴더의 .env(무작위
비밀번호 — 저장소 .env 는 읽지 않는다). 마이그레이션은 `db.migrate.migrate`. 끝나면 성공·실패와
상관없이 `down -v` 로 그 프로젝트 것만 지운다.

서비스는 프로세스 안에서 공개 조립 API 로 돌린다:

- scheduler: `Scheduler.step` (마스터 내려받기는 가짜 — 가짜 체인 마스터 zip, 그 자리에서 끝난다)
- poller: `Collector` + `PollerService.run(until=…)` — Redis 레이트리미터(`RedisRateLimiter`)·토큰은
  auth 가 둔 Redis `kis:token` 을 읽기 전용으로(`reader`), 원문은 `RawFanout` 으로 `rest.raw`,
  체인 행은 `ReadyTap` 으로 모아 사이클 끝에 `chain.ready`(`ReadyNotifier` — 30초 규칙도 가짜 시계)
- engine: `EngineService` 의 run 루프 한 번(`on_message` + `tick`)을 걸음마다 — 받은 `chain.ready`
  로 사이클, 10초 따라잡기·문맥 갱신·하트비트도 가짜 시계. poller 가 낸 알림을 engine 구독이 다
  받을 때까지 기다린다(실제 시간 상한). 자정 창(`LOSE`)의 알림은 잃은 것으로 버린다 — 따라잡기만
  남는다
- recorder: `services.recorder.service.run` (스레드) — `ws.raw`·`rest.raw` → raw_messages
- ws-gateway: `build_gateway` + `run_gateway` (세션 클라이언트·구독 컨트롤러·문맥 갱신·작업 스레드)
- 가짜 KIS REST(tests/fakes/kis_server.py — probe 녹화 fixture 재생)와 가짜 웹소켓 서버
  (tests/fakes/ws_server.py — 합성 체결 프레임). 실제 KIS·KRX·네트워크 호출은 없다

시계는 하나(`FakeClock`): 레이트리미터·수집기·scheduler·ws-gateway(now)·recorder·하트비트가 모두
이것을 본다. 서비스 안 묶음 나이(recorder 1초·ws 작업 스레드 0.5초)와 ws 응답 대기만 실제 시간이다 —
저장 묶음만 정하고 기록 시각·태그에는 들어가지 않는다. 하루를 다 걷되 전이·만기 전환 둘레의
창(`LIVE`)만 1초 단위로 모든 서비스를 돌리고, 사이는 60초 보폭으로 시계를 옮기며 scheduler·
ws-gateway 만 돌린다(poller 는 창에서만 — 하루 20만 건을 다 부르지 않게 압축). 체결 프레임은
DAY·NIGHT 창에서 5초마다 보낸다(구독 중인 종목으로).

시계를 옮길 때마다(`pump`) 가짜 시계를 멈춘 채 ws-gateway 가 그 시각을 다 따라잡길 기다린다 —
컨트롤러가 그 시각으로 step 하고, 클라이언트가 구독을 서버와 다 맞추고, 보낸 체결을 다 받을
때까지. 그래서 결과가 실제 CPU 속도에 기대지 않는다(바쁜 기계·느린 서버에서도 가짜 시계가 ws 를
앞지르지 않는다). 가짜 웹소켓 서버는 일부러 해지 응답을 늦게(UNSUB_ACK_DELAY_S) 준다.

실제 시간 상한은 `Watchdog`(RUN_TIMEOUT_S): poller 는 동기라 돌 동안 이벤트 루프가 멈춰
`asyncio.wait_for` 로는 끊지 못한다 — 상한에서 poller 의 stop 을 세워 돌려받고 실패한다(멈춘 채
남지 않고 스택도 내린다).

확인:

- 상태 전이 IDLE(기동)→PRE_DAY→DAY→POST_DAY→PRE_NIGHT→NIGHT→IDLE 가 session_log 와
  `session.events` 에 순서대로, 경계 시각에, 거래일·세션과 함께 (NIGHT 는 2026-09-29 귀속)
- chain_snapshots·fut_board·investor_flow·raw_messages·opt_ticks·fut_ticks 가 세션(주간 09-28·
  야간 09-29)마다 있고, 행의 거래일·세션이 시각의 `session_tag` 와 같다. quality 는 허용 값만
- 야간 분기 B: NIGHT 엔 전광판·선물 전광판·기초자산 REST 가 없고 단건 EU(옵션)·CM(선물)만
- 레이트리미터: 가짜 시계의 어떤 반열린 1초 창에도 KIS 호출 4건 이하
- WKM 260904 만기(15:20): poller 전광판 대상이 15:20 에 바뀌고, ws-gateway 구독은 마감 여유(60초)
  뒤 최근접 WKI 261001 로 옮긴다
- IDLE·POST_DAY 엔 KIS 호출이 없다. ws 구독은 세션이 끝나고 여유 뒤 모두 해지
- engine(Phase 3 설계 §6 통합): 사이클마다 levels 가 세 범위 × 여덟 레벨로 쌓이고(ts 의 거래일·
  세션, 사이클마다 `engine.levels` 하나), 열린 세션 창마다 처음부터 마지막 행까지 끊기지 않는다 —
  알림으로 35초, 잃은 창은 따라잡기로 50초 간격 안. 15:20 WKM 260904 만기는 걸친 사이클까지만
  평가하고 그 뒤 0DTE 는 비고 nearest 는 WKI 261001. 주간→야간: 15:45~18:00 사이 사이클 없음, 야간
  사이클은 야간 행(단건 보강)만, 마지막 `engine:latest` 는 야간 귀속·근월물 S_ref(단건 CM).
  engine health 는 옛 행(`engine_series_stale`)만, 그것도 시계를 건너뛴 창의 첫머리에만 — 건너뛴
  동안 poller 가 돌지 않아 직전 창의 행이 창 시작에 수십 분 묵는다(운영에선 없는 틈 — 15:43 창의
  따라잡기가 15:23 전광판 행을, 05:57 창이 보강 2 를 기다리는 차기 위클리의 00:03 행을 본다).
  확장 지표(Phase 3 항목 2 — vex·cex·gex_pc·iv_term·skew_25d, 항목 3 — pcr_oi·pcr_volume·
  max_pain)는 shadow 로 저장만, cex 는 같은 세션에서 사이클 시각 2분 간격 이상. OI 증감
  (oi_changes)은 세션마다 첫 스냅샷부터. 체결 틱(ticks.opt·ticks.fut)은 engine 이 다 받아 HIRO-lite
  hiro 행을 세션마다(estimated, 야간 첫 행은 세션 전환 리셋), 대량 체결 기준은 세션 거래일마다 한
  번(체결 기록 20거래일 전이라 비활성, block_trade 행 없음). 투자자별 순매수(investor_flow 지표)는
  investor_flow 표의 외국인·개인·기관계·증권 행 그대로, 딜러 가정 점검(dealer_check)은 POST_DAY 에
  그 거래일 한 행. 일별 지표(atm_iv_daily·iv_rank·
  iv_percentile·iv_hv)는 POST_DAY(15:45 창)에 한 번 — 그 거래일 15:45 행, 오늘 값은 마지막 주간
  사이클의 월물 ATM IV
"""

from __future__ import annotations

import asyncio
import io
import logging
import secrets
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from pydantic import SecretStr

from core.calendar import State, TradingCalendar, state_at
from data.kis.auth_client import RedisTokenCache, TokenRecord, token_owner
from data.kis.ratelimit import RedisRateLimiter, limiter_key
from data.kis.rest import KisClient
from data.store import PostgresSink
from db.migrate import migrate
from scripts.probe_common import TR_CALLPUT, TR_FUT_BOARD, TR_OPTION_LIST, TR_PRICE, TR_TOP
from services.auth.health import LogHealthSink
from services.auth.service import TOKEN_KEY, WS_KEY_KEY, reader
from services.bus import (
    CHAIN_READY,
    ENGINE_LATEST_KEY,
    ENGINE_LEVELS,
    RAW_CHANNELS,
    SESSION_EVENTS,
    SESSION_STATE,
    TICKS_FUT,
    TICKS_OPT,
    EngineLatest,
    EngineLevels,
    SessionState,
)
from services.chain_feed import ContextPublisher, MasterWatcher
from services.engine.context import EngineContext
from services.engine.daily import daily_ts
from services.engine.publish import EnginePublisher
from services.engine.service import CATCHUP_GRACE, CATCHUP_S, EngineService
from services.poller.collector import Collector
from services.poller.config import PollerConfig
from services.poller.context import session_tag
from services.poller.planner import from_us
from services.poller.ready import READY_MAX_WAIT_S, ReadyNotifier, ReadyTap
from services.poller.service import PollerService, RawFanout
from services.recorder.service import Recorder
from services.recorder.service import run as run_recorder
from services.runtime import (
    Heartbeater,
    ServiceHealthSink,
    connect_redis,
    heartbeat_age,
    tagger_for,
)
from services.scheduler.service import Scheduler
from services.ws_gateway.service import Gateway, build_gateway, run_gateway
from services.ws_gateway.subscriptions import TR_IDS
from tests.fakes.kis_server import (
    FakeClock,
    FakeKisServer,
    FakeSeries,
    default_chain,
    fake_settings,
)
from tests.fakes.ws_server import FakeKisWsServer
from tests.integration.conftest import _docker, _docker_ready, compose_available

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
OVERRIDE = Path(__file__).with_name("docker-compose.test.yml")
SERVICES = ("redis", "db")  # 앱 서비스는 띄우지 않는다 — 시험이 프로세스 안에서 돌린다
# `up --wait` 상한. 덧씌우기의 healthcheck 는 이보다 먼저 unhealthy 로 판정하지 않는다
# (tests/unit/test_compose_file.py)
STACK_WAIT_S = 240

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
D = date(2026, 9, 28)  # 월 — 주간 거래일
N = date(2026, 9, 29)  # 화 — 09-28 밤 야간장의 귀속 거래일


def kst(d: date, h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=KST)


START = kst(D, 7, 59)
# 모든 서비스를 1초 단위로 돌리는 창. 사이는 SKIP 보폭으로 건너뛴다
LIVE: tuple[tuple[datetime, datetime], ...] = (
    (START, kst(D, 8, 3)),  # IDLE → PRE_DAY: 월물리스트·최종거래일, 주간 선물 구독
    (kst(D, 8, 44), kst(D, 8, 52)),  # PRE_DAY → DAY: 전광판·선물·투자자·보강, 체결
    (kst(D, 15, 17), kst(D, 15, 24)),  # WKM 260904 만기 15:20 (+ ws 마감 여유 60초)
    (kst(D, 15, 43), kst(D, 15, 48)),  # DAY → POST_DAY: 호출 멈춤, 구독 해지
    (kst(D, 17, 48), kst(D, 17, 53)),  # POST_DAY → PRE_NIGHT: 월물리스트·최종거래일, 야간 구독
    (kst(D, 17, 59), kst(D, 18, 8)),  # PRE_NIGHT → NIGHT: 분기 B
    (kst(D, 23, 58), kst(N, 0, 3)),  # 자정 — 귀속 거래일은 그대로 09-29
    (kst(N, 5, 57), kst(N, 6, 5)),  # NIGHT → IDLE
)
END = LIVE[-1][1]
# chain.ready 를 engine 이 잃는 창 — 이 동안 산출은 10초 따라잡기(DB max(ts))로만 난다
LOSE: tuple[tuple[datetime, datetime], ...] = ((kst(D, 23, 58), kst(N, 0, 3)),)
OPEN = ((kst(D, 8, 45), kst(D, 15, 45)), (kst(D, 18), kst(N, 6)))  # 주간·야간 세션
# engine 사이클 간격 상한: 알림으로는 poller 최대 대기(30초) + 여유, 알림을 잃으면 따라잡기
# (알림 여유 35초 + 주기 10초) + 여유
READY_GAP = timedelta(seconds=READY_MAX_WAIT_S + 5)
LOSE_GAP = CATCHUP_GRACE + timedelta(seconds=CATCHUP_S + 5)
# 옛 행 health 가 날 수 있는 창 첫머리 — 건너뛴 시계 뒤 첫 사이클(따라잡기 여유 35초 + 여유)
STALE_AT_START = timedelta(seconds=60)
LEVEL_NAMES = frozenset(
    {
        "call_wall",
        "put_wall",
        "abs_gamma",
        "flip",
        "flip_distance",
        "expected_move_calendar",
        "expected_move_trading",
        "top_levels",
    }
)
LEVEL_KEYS = frozenset((scope, n) for scope in ("all", "nearest", "0dte") for n in LEVEL_NAMES)
STEP = timedelta(seconds=1)
SKIP = timedelta(seconds=60)
TICK_EVERY = timedelta(seconds=5)
PUMP_S = 0.001  # ws-gateway 가 따라잡길 기다리며 이벤트 루프에 한 번에 주는 실제 시간
# 한 시각에서 ws-gateway 가 따라잡길(step·구독 맞추기·체결 수신) 기다리는 실제 시간 상한
SETTLE_S = 10.0
# 가짜 웹소켓 서버의 해지 응답 지연(실제 시간) — 느린 서버·바쁜 기계처럼 클라이언트가 가짜 시계의
# 몇 초 동안 구독을 못 맞춰도 결과가 같아야 한다
UNSUB_ACK_DELAY_S = 0.5
RUN_TIMEOUT_S = 900.0  # 하루 걷기의 실제 시간 상한 (Watchdog — 동기 poller 도 멈춘다)
SHUTDOWN_S = 180.0  # 그 뒤 정리(ws-gateway 60초·recorder 60초 대기)까지 — asyncio.wait_for 상한

EXPIRY_SWITCH = kst(D, 15, 20)
LATE_GRACE = timedelta(seconds=60)  # config/kis_ws.yaml client.late_grace_s
FUT_CODE = "A01612"  # 가짜 체인 근월물 (F 202612)
FUT_PRICE = "1095.10"  # futures_board fixture 근월물 가격 — REST 와 같게
ATM = Decimal("1095.0")
WS_KEY = "fake-approval-key-for-the-full-day"
SETTINGS = fake_settings()
APP_KEY = SETTINGS.kis_app_key.get_secret_value() if SETTINGS.kis_app_key else ""
CHAIN = default_chain()
TRANSITIONS = (  # (상태, 이전 상태, 경계 시각, 거래일, 세션)
    (State.IDLE, None, START, None, None),
    (State.PRE_DAY, State.IDLE, kst(D, 8), None, None),
    (State.DAY, State.PRE_DAY, kst(D, 8, 45), D, "day"),
    (State.POST_DAY, State.DAY, kst(D, 15, 45), None, None),
    (State.PRE_NIGHT, State.POST_DAY, kst(D, 17, 50), None, None),
    (State.NIGHT, State.PRE_NIGHT, kst(D, 18), N, "night"),
    (State.IDLE, State.NIGHT, kst(N, 6), None, None),
)
QUALITIES = {"ok", "stale", "estimated", "invalid"}


# ── compose 시험 스택 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ComposeStack:
    project: str
    files: tuple[Path, ...]
    redis_url: str
    dsn: str


def _compose(
    project: str, files: tuple[Path, ...], *args: str, timeout: float = 120.0
) -> subprocess.CompletedProcess[str]:
    fs = [x for f in files for x in ("-f", str(f))]
    return _docker("compose", "-p", project, *fs, *args, timeout=timeout)


def _port(project: str, files: tuple[Path, ...], service: str, port: int) -> int:
    out = _compose(project, files, "port", service, str(port)).stdout.strip().splitlines()
    for line in out:  # '127.0.0.1:55001'
        host, _, p = line.rpartition(":")
        if host == "127.0.0.1" and p.isdigit():
            return int(p)
    raise RuntimeError(f"{service}:{port} 포트 매핑을 읽지 못했다: {out!r}")


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[ComposeStack]:
    reason = _docker_ready() or compose_available()
    if reason is not None:
        pytest.skip(f"Docker 없음 — {reason}")
    work = tmp_path / "compose"
    work.mkdir()
    shutil.copy(COMPOSE, work / "docker-compose.yml")  # 프로젝트 폴더 = 임시 폴더(.env 도 여기)
    password = secrets.token_hex(16)
    (work / ".env").write_text(f"POSTGRES_PASSWORD={password}\n", encoding="utf-8")
    files = (work / "docker-compose.yml", OVERRIDE)
    project = f"gexlab-fd-{uuid.uuid4().hex[:10]}"
    try:
        args = ("up", "-d", "--wait", "--wait-timeout", str(STACK_WAIT_S), *SERVICES)
        up = _compose(project, files, *args, timeout=STACK_WAIT_S + 120)
        assert up.returncode == 0, f"시험 스택을 띄우지 못했다: {up.stderr[-1500:]}"
        redis_port = _port(project, files, "redis", 6379)
        db_port = _port(project, files, "db", 5432)
        dsn = f"postgresql://gexlab:{password}@127.0.0.1:{db_port}/gexlab"
        _wait_db(dsn)
        yield ComposeStack(project, files, f"redis://127.0.0.1:{redis_port}/0", dsn)
    finally:
        _compose(project, files, "down", "-v", "--remove-orphans", "-t", "5", timeout=240)


def _wait_db(dsn: str, timeout: float = 60.0) -> None:
    """healthcheck(컨테이너 안 pg_isready) 뒤 호스트 포트로도 붙는지."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with psycopg.connect(dsn, connect_timeout=5) as c:
                c.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


# ── 실제 시간 상한 ─────────────────────────────────────────────────────────────


class Watchdog:
    """실제 시간 상한. 동기 `PollerService.run` 은 이벤트 루프를 막아 `asyncio.wait_for` 가 깨지
    못한다 — 수집기가 가짜 시계를 밀지 못한 채 오류를 내면 run 은 1초 쉬고 다시 돌 뿐 until 에
    닿지 않는다. 상한이 지나면 poller 의 stop 을 세워(그 1초 쉬기도 깬다) 돌려받는다."""

    def __init__(self, stop: threading.Event, limit_s: float) -> None:
        self.stop = stop
        self.fired = False
        self._timer = threading.Timer(limit_s, self._fire)
        self._timer.daemon = True

    def _fire(self) -> None:
        self.fired = True
        self.stop.set()

    @property
    def armed(self) -> bool:
        return self._timer.is_alive()

    def __enter__(self) -> Watchdog:
        self._timer.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._timer.cancel()
        self._timer.join()


def poll_until(poller: PollerService, stop: threading.Event, until: datetime) -> None:
    """poller 한 조각 — 가짜 시계를 until 까지. 상한(stop)에 걸려 돌아왔으면 실패."""
    poller.run(stop, until=until)
    if stop.is_set():
        raise TimeoutError(
            f"실제 시간 상한 — poller 가 가짜 시계를 {until.isoformat()} 까지 밀지 못했다"
        )


# ── 조립 ─────────────────────────────────────────────────────────────────────


def master_zip() -> bytes:
    """KIS 배포 형식(zip 안 .mst 하나, cp949) — 가짜 체인(가짜 KIS 서버와 같은 코드·행사가)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        text = "\n".join(CHAIN.master_lines()) + "\n"
        z.writestr("fo_idx_code_mts.mst", text.encode("cp949"))
    return buf.getvalue()


def inline_submit(fn: Any) -> Future[bytes]:
    """마스터 내려받기를 그 자리에서 (서비스 기본은 데몬 스레드 — 가짜 시계에선 결정적이게)."""
    fut: Future[bytes] = Future()
    try:
        fut.set_result(fn())
    except Exception as e:
        fut.set_exception(e)
    return fut


async def _yield(_s: float) -> None:
    """ws 클라이언트의 등록 간격·백오프 — 실제 시간을 쓰지 않는다(가짜 시계로 압축)."""
    await asyncio.sleep(0)


def seed_credentials(r: Any, now: datetime) -> None:
    """auth 가 둔 것처럼 Redis `kis:token`·`kis:ws_key` (발급은 auth 몫 — 여기선 값만 둔다)."""
    owner = token_owner(SETTINGS.kis_base, APP_KEY)
    for key, value in ((TOKEN_KEY, "fake-access-token-for-the-full-day"), (WS_KEY_KEY, WS_KEY)):
        rec = TokenRecord(
            access_token=SecretStr(value),
            expires_at=now + timedelta(hours=30),
            issued_at=now,
            owner=owner,
        )
        RedisTokenCache(r, key).store(rec, now)


@dataclass
class FullDay:
    stack: ComposeStack
    clock: FakeClock = field(default_factory=lambda: FakeClock(START))
    kis: FakeKisServer = field(init=False)
    seen: dict[datetime, frozenset[tuple[str, str]]] = field(default_factory=dict)
    events: list[SessionState] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.kis = FakeKisServer(self.clock, CHAIN)
        self.redis = connect_redis(self.stack.redis_url)
        self.tagger = tagger_for(CAL)
        self.stores: list[PostgresSink] = []
        self.probes = sorted(PROBES)
        self.ticks_sent = 0
        self._next_tick: datetime | None = None
        seed_credentials(self.redis, self.clock.now())

        # scheduler
        ss = self._store("scheduler")
        self.sched_store = ss
        self.scheduler = Scheduler(
            self.redis,
            ss,
            CAL,
            downloader=master_zip,
            health=ServiceHealthSink(ss, self.tagger),
            now=self.clock.now,
            submit=inline_submit,
        )
        self.sched_hb = Heartbeater(self.redis, "scheduler", now=self.clock.now)

        # poller — 레이트리미터·수집기·곁일(마스터·문맥 발행)이 모두 가짜 시계
        ps = self._store("poller")
        limiter = RedisRateLimiter(self.redis, APP_KEY, clock=self.clock)
        tokens = reader(self.redis, SETTINGS, now=self.clock.now)
        self.kis_client = KisClient(
            SETTINGS, token_provider=tokens, rate_limiter=limiter, transport=self.kis.transport
        )
        self.fanout = RawFanout(ps, self.redis)
        # chain.ready — 싱크에 넘긴 체인 행을 모아 사이클 끝에 알린다(30초 규칙도 가짜 시계)
        self.ready = ReadyNotifier(self.redis, clock=self.mono)
        collector = Collector(
            self.kis_client,
            ReadyTap(self.fanout, self.ready),
            calendar=CAL,
            clock=self.clock,
            config=PollerConfig(),
        )
        self.poller = PollerService(
            collector,
            masters=MasterWatcher(self.redis, "poller", clock=self.mono),
            context=ContextPublisher(self.redis, "poller", clock=self.mono),
            heartbeat=Heartbeater(self.redis, "poller", now=self.clock.now),
            flush_spool=ps.flush_spool,
            ready=self.ready,
        )
        self.poller_stop = threading.Event()

        # engine — chain.ready 로 사이클, 10초 따라잡기·문맥 갱신도 가짜 시계
        es = self._store("engine")
        self.engine = EngineService(
            es,
            es,
            EnginePublisher(self.redis),
            EngineContext(self.redis, CAL, clock=self.mono),
            CAL,
            health=ServiceHealthSink(es, self.tagger),
            now=self.clock.now,
            mono=self.mono,
            heartbeat=Heartbeater(self.redis, "engine", now=self.clock.now),
            flush_spool=es.flush_spool,
        )
        self.ready_sub = self.redis.pubsub(ignore_subscribe_messages=True)
        self.ready_sub.subscribe(CHAIN_READY)
        self.ready_got = 0  # engine 구독이 받은 chain.ready
        self.ready_lost = 0  # 그중 LOSE 창에서 잃은 것으로 버린 것
        # 체결 틱(ws-gateway → engine HIRO-lite·대량 체결) — 알림과 따로 구독해 걸음마다 다 넘긴다
        self.tick_sub = self.redis.pubsub(ignore_subscribe_messages=True)
        self.tick_sub.subscribe(TICKS_OPT, TICKS_FUT)
        self.ticks_got = 0
        self.out_sub = self.redis.pubsub(ignore_subscribe_messages=True)  # api·notifier 자리
        self.out_sub.subscribe(ENGINE_LEVELS)
        self.levels_out: list[EngineLevels] = []

        # recorder (스레드)
        rs = self._store("recorder")
        url = self.stack.redis_url
        self.recorder = Recorder(rs, ServiceHealthSink(rs, self.tagger), now=self.clock.now)
        self.rec_stop = threading.Event()
        self.rec_alive: bool | None = None  # 멈춘 뒤에도 recorder 스레드가 살아 있나
        self.rec_thread = threading.Thread(
            target=run_recorder,
            args=(self.recorder, lambda: connect_redis(url), self.rec_stop),
            kwargs={"heartbeat": Heartbeater(connect_redis(url), "recorder", now=self.clock.now)},
            name="recorder",
            daemon=True,
        )

        # ws-gateway — run() 안에서(이벤트 루프가 있어야 한다)
        self.gw_store = self._store("ws-gateway")
        self.srv = FakeKisWsServer(approval_key=WS_KEY, unsub_ack_delay=UNSUB_ACK_DELAY_S)
        self.gw: Gateway | None = None
        self.gw_stop: asyncio.Event | None = None
        self.gw_task: asyncio.Task[bool] | None = None
        self.gw_finished: bool | None = None
        self.ctl_at: datetime | None = None  # 컨트롤러가 마지막으로 step 한 가짜 시각

        self.sub = self.redis.pubsub(ignore_subscribe_messages=True)
        self.sub.subscribe(SESSION_EVENTS)

    def _store(self, service: str) -> PostgresSink:
        s = PostgresSink(self.stack.dsn, service=service, tagger=self.tagger)
        self.stores.append(s)
        return s

    def mono(self) -> float:
        """곁일(마스터 감시·문맥 발행)의 단조 시계 — 가짜 시계에서."""
        return self.clock.now_us() / 1_000_000

    # ── 한 걸음 ──

    def sched(self) -> None:
        """scheduler run 루프의 한 번: step·스풀·하트비트."""
        info = self.scheduler.step()
        self.sched_store.flush_spool()
        self.sched_hb.beat(state=info.state.value)
        while (msg := self.sub.get_message(timeout=0.0)) is not None:
            self.events.append(SessionState.model_validate_json(msg["data"]))

    def eng(self) -> None:
        """engine run 루프의 한 번: 받은 chain.ready 로 사이클, 곁일(10초 따라잡기·스풀·하트비트).
        poller 가 낸 알림을 engine 구독이 다 받을 때까지 기다린다(실제 SETTLE_S 상한 — 받는 속도에
        결과가 기대지 않게). LOSE 창의 알림은 잃은 것으로 버린다 — 따라잡기만 남는다."""
        deadline = time.monotonic() + SETTLE_S
        lose = any(a <= self.clock.now() < b for a, b in LOSE)
        while self.ready_got < self.ready.stats.published:
            msg = self.ready_sub.get_message(timeout=0.05)
            if msg is None:
                if time.monotonic() > deadline:
                    at = self.clock.now().isoformat()
                    raise TimeoutError(
                        f"chain.ready 수신: 실제 {SETTLE_S:.0f}초 안에 못 받았다({at})"
                    )
                continue
            self.ready_got += 1
            if lose:
                self.ready_lost += 1
                continue
            self.engine.on_message(msg)
        while (msg := self.tick_sub.get_message(timeout=0.0)) is not None:
            self.ticks_got += 1
            self.engine.on_message(msg)
        self.engine.tick()
        self._drain_levels(0.0)

    def _drain_levels(self, timeout: float) -> None:
        while (msg := self.out_sub.get_message(timeout=timeout)) is not None:
            self.levels_out.append(EngineLevels.model_validate_json(msg["data"]))

    async def pump(self, *, ticks: bool) -> None:
        """가짜 시계를 멈춘 채 ws-gateway 가 지금 시각을 다 따라잡게 한다: 컨트롤러가 이 시각으로
        step 하고, 클라이언트가 그 구독을 서버와 다 맞추고, (때가 되면) 보낸 체결을 클라이언트가 다
        받을 때까지. 그래서 느린 기계·느린 서버에서도 가짜 시계가 ws 를 앞지르지 않는다 — 결과가
        실제 CPU 속도에 기대지 않는다. 기다림마다 실제 시간 SETTLE_S 상한(넘으면 실패)."""
        now = self.clock.now()
        await self._catch_up(
            lambda: self.ctl_at is not None and self.ctl_at >= now, "컨트롤러 step"
        )
        await self._catch_up(self._subs_settled, "구독 맞추기")
        if ticks and (self._next_tick is None or now >= self._next_tick):
            self._next_tick = now + TICK_EVERY
            before = self._ticks_received()
            sent = await self._send_ticks(now)
            await self._catch_up(lambda: self._ticks_received() >= before + sent, "체결 수신")
        while self.probes and now >= self.probes[0]:
            self.seen[self.probes.pop(0)] = self._server_subs()

    async def _catch_up(self, done: Callable[[], bool], what: str) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_S
        while not done():
            if self.gw_task is not None and self.gw_task.done():
                raise RuntimeError(f"{what}: ws-gateway 가 끝났다 — {self.gw_task!r}")
            if loop.time() > deadline:
                at = self.clock.now().isoformat()
                raise TimeoutError(
                    f"{what}: 실제 {SETTLE_S:.0f}초 안에 가짜 시각 {at} 을 못 따라잡았다"
                )
            await asyncio.sleep(PUMP_S)

    def _controller_now(self) -> datetime:
        """컨트롤러 run 루프의 시계 — 본 시각을 남긴다. 루프는 이 값으로 곧바로(쉬지 않고) step
        하므로, 이 값이 지금이면 지금 시각의 step 은 끝났다."""
        at = self.clock.now()
        self.ctl_at = at
        return at

    def _subs_settled(self) -> bool:
        """클라이언트가 원하는 구독을 서버와 다 맞췄다 — 응답 대기 없고, 서버 쪽 = 클라이언트 쪽."""
        if self.gw is None:
            return True
        st = self.gw.client.status()
        active = {(s.tr_id, s.key) for s in st.active}
        return not st.pending and st.active == st.desired and active == self._server_subs()

    def _ticks_received(self) -> int:
        """클라이언트가 받아 작업 스레드까지 넘긴 체결 수 (수신 시각은 받을 때의 가짜 시각)."""
        return self.gw.worker.stats.ticks if self.gw is not None else 0

    def _server_subs(self) -> frozenset[tuple[str, str]]:
        if not self.srv.connections or self.srv.conn().closed:
            return frozenset()
        return frozenset(self.srv.conn().subs)

    async def _send_ticks(self, now: datetime) -> int:
        """SYNTHETIC: 서버에 걸린 구독 종목으로 선물 1건 + 옵션 2건(돌아가며). 보낸 프레임 수."""
        info = state_at(now, CAL)
        if info.state not in (State.DAY, State.NIGHT) or info.session is None:
            return 0
        subs = sorted(self._server_subs())
        if not subs:
            return 0
        sent = 0
        tr = TR_IDS[info.session]
        hhmmss = now.astimezone(KST).strftime("%H%M%S")
        self.ticks_sent += 1
        n = str(self.ticks_sent)
        for tr_id, code in subs:
            if tr_id == tr["futures"]:
                await self.srv.send_tick(
                    tr_id,
                    futs_shrn_iscd=code,
                    bsop_hour=hhmmss,
                    futs_prpr=FUT_PRICE,
                    last_cnqn="1",
                    acml_vol=n,
                    shnu_cntg_smtn=n,
                    seln_cntg_smtn=n,
                )
                sent += 1
        opts = [code for tr_id, code in subs if tr_id == tr["options"]]
        for i in range(min(2, len(opts))):
            code = opts[(2 * self.ticks_sent + i) % len(opts)]
            await self.srv.send_tick(
                tr["options"],
                optn_shrn_iscd=code,
                bsop_hour=hhmmss,
                optn_prpr="5.20",
                last_cnqn="1",
                shnu_cntg_smtn=str(2 * self.ticks_sent),  # 매수 주도 — 누적은 늘기만 한다
                seln_cntg_smtn=n,
            )
            sent += 1
        return sent

    # ── 하루 ──

    async def run(self) -> None:
        try:
            with Watchdog(self.poller_stop, RUN_TIMEOUT_S):
                self.rec_thread.start()
                _wait_until(lambda: _subscribers(self.redis) >= len(RAW_CHANNELS), "recorder 구독")
                self.sched()  # 07:59:00 기동 — IDLE, 마스터를 곧바로 받아 Redis 에 둔다
                await self._start_gateway()
                for start, end in LIVE:
                    await self._skip_to(start)
                    await self._live(start, end)
        finally:
            await self._shutdown()

    async def _start_gateway(self) -> None:
        await self.srv.start()
        self.gw = build_gateway(
            url=self.srv.url,
            # redis_key_source 와 같다 — 만료 판정만 가짜 시계로
            key_source=reader(self.redis, SETTINGS, "ws_key", now=self.clock.now).get,
            redis=self.redis,
            store=self.gw_store,
            calendar=CAL,
            log_health=LogHealthSink(tagger=self.tagger),
            now=self.clock.now,
            sleep=_yield,
            proxy=None,
            seq_base=0,
        )
        self.gw_stop = asyncio.Event()
        self.gw_task = asyncio.create_task(
            run_gateway(
                self.gw,
                self.gw_stop,
                now=self._controller_now,  # 컨트롤러 루프만 — 본 시각을 pump 가 기다린다
                heartbeat=Heartbeater(self.redis, "ws-gateway", now=self.clock.now),
                context_every_s=0.005,
                controller_tick_s=0.001,
            )
        )

    async def _skip_to(self, t: datetime) -> None:
        """poller 없이 SKIP 보폭으로 — scheduler·ws-gateway 만 돈다."""
        while self.clock.now() < t:
            self.clock.set(min(self.clock.now() + SKIP, t))
            self.sched()
            self.eng()
            await self.pump(ticks=False)

    async def _live(self, start: datetime, end: datetime) -> None:
        """1초마다 scheduler → poller(그 1초를 수집 — 시계는 수집기의 sleep 이 민다) → ws·체결."""
        k = 0
        while self.clock.now() < end:
            self.sched()
            k += 1
            target = min(start + k * STEP, end)
            if self.clock.now() < target:
                poll_until(self.poller, self.poller_stop, target)
            self.eng()
            await self.pump(ticks=True)

    async def _shutdown(self) -> None:
        if self.gw_stop is not None and self.gw_task is not None:
            self.gw_stop.set()
            try:
                self.gw_finished = await asyncio.wait_for(self.gw_task, 60)
            except TimeoutError:
                self.gw_task.cancel()
        await self.srv.stop()
        self.rec_stop.set()
        self.rec_thread.join(60)
        self.rec_alive = self.rec_thread.is_alive()
        while (msg := self.sub.get_message(timeout=0.2)) is not None:  # 늦게 온 발행까지
            self.events.append(SessionState.model_validate_json(msg["data"]))
        self.sub.close()
        self.ready_sub.close()
        self.tick_sub.close()
        self._drain_levels(0.2)  # 늦게 온 발행까지
        self.out_sub.close()
        self.kis_client.close()
        for s in self.stores:
            s.close()


def _subscribers(r: Any) -> int:
    got: Any = r.pubsub_numsub(*RAW_CHANNELS)
    return sum(1 for _, n in got if int(n) > 0)


def _wait_until(pred: Any, what: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise TimeoutError(f"{what}: {timeout:.0f}초 안에 되지 않았다")
        time.sleep(0.05)


# ── ws 구독 기대값 ─────────────────────────────────────────────────────────────


def ws_plan(series: FakeSeries | None, session: str, atm_range: int) -> frozenset[tuple[str, str]]:
    """선물 근월물 1 + 최근접 만기 ATM±atm_range × 콜·풋 (ATM = 선물 체결가에 가까운 행사가)."""
    tr = TR_IDS["day" if session == "day" else "night"]
    out = {(tr["futures"], FUT_CODE)}
    if series is not None:
        near = [k for k in series.strikes if abs(k - ATM) <= atm_range * series.step]
        out |= {(tr["options"], series.code(cp, k)) for k in near for cp in ("C", "P")}
    return frozenset(out)


def _series(cls: str, mtrt: str) -> FakeSeries:
    s = CHAIN.find(cls, mtrt)
    assert s is not None
    return s


WKM0904 = _series("WKM", "260904")
WKI1001 = _series("WKI", "261001")
PROBES: dict[datetime, frozenset[tuple[str, str]]] = {
    kst(D, 8, 1): ws_plan(None, "day", 0),  # PRE_DAY: 체결가가 없다 — 선물만
    kst(D, 8, 50): ws_plan(WKM0904, "day", 9),  # DAY: ATM±9 (38) + 선물
    kst(D, 15, 20, 30): ws_plan(WKM0904, "day", 9),  # 만기 15:20 — 마감 여유 동안 그대로
    kst(D, 15, 23): ws_plan(WKI1001, "day", 9),  # 여유 뒤 차기 최근접으로
    kst(D, 15, 47): frozenset(),  # POST_DAY — 여유 뒤 모두 해지
    kst(D, 17, 52): ws_plan(WKI1001, "night", 8),  # PRE_NIGHT: 야간 TR, ATM±8 (34) + 선물
    kst(D, 18, 7): ws_plan(WKI1001, "night", 8),
    kst(N, 6, 3): frozenset(),  # IDLE — 여유 뒤 모두 해지
}


# ── 시험 ─────────────────────────────────────────────────────────────────────


def test_a_whole_trading_day_from_pre_day_to_idle(
    stack: ComposeStack, caplog: pytest.LogCaptureFixture
) -> None:
    applied = migrate(stack.dsn)
    assert applied, "빈 DB 에 마이그레이션이 적용돼야 한다"
    day = FullDay(stack)
    asyncio.run(asyncio.wait_for(day.run(), RUN_TIMEOUT_S + SHUTDOWN_S))
    assert day.clock.now() >= END

    with psycopg.connect(stack.dsn) as db:
        _check_session_log(db, day)
        _check_rest_calls(day)
        _check_expiry_switch(db, day)
        _check_rows(db)
        _check_ws(day)
        _check_services(db, day)
        _check_engine(db, day)
    # 로그로만 남는 오류도 없다 — poller tick_error·collector_error·스풀 실패, ws 문맥 갱신 실패 등
    errors = [r.getMessage()[:300] for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, errors[:5]


def _q(db: psycopg.Connection[Any], query: str, *params: object) -> list[tuple[Any, ...]]:
    return db.execute(query.encode(), params or None).fetchall()


def _state(t_us: int) -> State:
    return state_at(from_us(t_us), CAL).state


def _check_session_log(db: psycopg.Connection[Any], day: FullDay) -> None:
    rows = _q(
        db,
        "SELECT ts, trade_date, session, state, prev_state, detail FROM session_log "
        "WHERE kind = 'transition' ORDER BY ts",
    )
    assert [(r[3], r[4]) for r in rows] == [
        (s.value, p.value if p else None) for s, p, *_ in TRANSITIONS
    ]
    for (ts, td, ss, *_), (_s, _p, at, want_td, want_ss) in zip(rows, TRANSITIONS, strict=True):
        assert at <= ts < at + timedelta(seconds=2), (ts, at)
        assert (td, ss) == (want_td, want_ss), (ts, td, ss)
    assert rows[0][5] == {"startup": True}
    # 발행: 같은 전이가 같은 순서·시각·태그로 session.events 에, 마지막 키는 IDLE
    got = [(e.state, e.at, e.trade_date, e.session) for e in day.events]
    assert got == [(State(r[3]), r[0], r[1], r[2]) for r in rows]
    last = SessionState.model_validate_json(day.redis.get(SESSION_STATE))  # type: ignore[arg-type]
    assert last.state is State.IDLE and last.trade_date is None


def _check_rest_calls(day: FullDay) -> None:
    calls = day.kis.calls
    assert calls, "KIS 호출이 없다"
    # 레이트리미터(가짜 시계): 어떤 반열린 1초 창에도 4건 이하. Redis 버킷을 썼다
    assert day.kis.max_in_window(1.0) <= 4
    assert day.redis.exists(limiter_key(APP_KEY)) == 1
    # 토큰 발급은 auth 몫 — poller 는 Redis kis:token 을 읽기만 한다(발급 POST 는 calls 밖에서 센다)
    assert day.kis.token_posts == 0
    by_state: dict[State, list[Any]] = {}
    for c in calls:
        by_state.setdefault(_state(c.t_us), []).append(c)
    # IDLE·POST_DAY 엔 한 건도 없다 (창 07:59~08:00·15:45~15:48·17:48~17:50·06:00~06:05 에도
    # poller 는 돌았다 — 할 일이 없었을 뿐)
    assert set(by_state) == {State.PRE_DAY, State.DAY, State.PRE_NIGHT, State.NIGHT}
    assert all(c.status == 200 and c.msg_cd.startswith("MCA") for c in calls), Counter(
        (c.tr_id, c.status, c.msg_cd) for c in calls if c.status != 200 or c.msg_cd[:3] != "MCA"
    )
    day_tr = Counter(c.tr_id for c in by_state[State.DAY])
    assert day_tr[TR_CALLPUT] and day_tr[TR_FUT_BOARD] and day_tr[TR_TOP] and day_tr[TR_PRICE]
    day_markets = {c.params.get("FID_COND_MRKT_DIV_CODE") for c in by_state[State.DAY]}
    assert {"O", "F"} <= day_markets and not {"EU", "CM"} & day_markets
    # 야간 분기 B: 전광판·선물 전광판·기초자산 없이 단건 EU(옵션)·CM(선물)
    night = by_state[State.NIGHT]
    assert not {TR_CALLPUT, TR_FUT_BOARD, TR_TOP} & {c.tr_id for c in night}
    prices = Counter(c.params["FID_COND_MRKT_DIV_CODE"] for c in night if c.tr_id == TR_PRICE)
    assert prices["EU"] > 0 and prices["CM"] > 0 and set(prices) <= {"EU", "CM"}, prices
    # 장 전 준비는 월물리스트·최종거래일(단건)만
    for st in (State.PRE_DAY, State.PRE_NIGHT):
        prep = Counter(c.tr_id for c in by_state[st])
        assert set(prep) == {TR_OPTION_LIST, TR_PRICE}, (st, prep)


def _check_expiry_switch(db: psycopg.Connection[Any], day: FullDay) -> None:
    def boards(cls: str, mtrt: str) -> list[datetime]:
        return [
            from_us(c.t_us)
            for c in day.kis.quote_calls(TR_CALLPUT, FID_COND_MRKT_CLS_CODE=cls, FID_MTRT_CNT=mtrt)
        ]

    old, new = boards("WKM", "260904"), boards("WKM", "261001")
    assert old and max(old) < EXPIRY_SWITCH
    assert any(t >= kst(D, 15, 17) for t in old)  # 만기 창에서도 불렀다
    assert new and min(new) >= EXPIRY_SWITCH  # 15:20 뒤 차기 만기가 추적 대상에 든다
    last = _q(
        db,
        "SELECT max(ts) FROM chain_snapshots WHERE mrkt_cls = 'WKM' AND expiry = '260904'",
    )[0][0]
    assert last is not None and last < EXPIRY_SWITCH
    # ws-gateway: 마감 여유(60초) 뒤 한 번 옮긴다
    moved = _q(
        db,
        "SELECT ts, message FROM health_events WHERE service = 'ws-gateway' "
        "AND kind = 'ws_plan_series' ORDER BY ts",
    )
    assert len(moved) == 1, moved
    ts, message = moved[0]
    assert EXPIRY_SWITCH + LATE_GRACE <= ts < EXPIRY_SWITCH + LATE_GRACE + timedelta(seconds=5)
    assert "WKM:260904" in message and "WKI:261001" in message
    old_ticks = _q(
        db,
        "SELECT max(received_at) FROM opt_ticks WHERE mrkt_cls = 'WKM' AND expiry = '260904'",
    )[0][0]
    assert old_ticks is not None and old_ticks < EXPIRY_SWITCH + LATE_GRACE + timedelta(seconds=5)
    new_ticks = _q(
        db,
        "SELECT min(received_at) FROM opt_ticks WHERE trade_date = %s AND mrkt_cls = 'WKI' "
        "AND expiry = '261001'",
        D,
    )[0][0]
    assert new_ticks is not None and new_ticks >= EXPIRY_SWITCH + LATE_GRACE


def _check_rows(db: psycopg.Connection[Any]) -> None:
    sessions = {(D, "day"), (N, "night")}
    for table in ("chain_snapshots", "fut_board", "investor_flow"):
        rows = _q(db, f"SELECT ts, trade_date, session, quality FROM {table}")  # noqa: S608
        assert {(td, ss) for _, td, ss, _ in rows} == sessions, table
        assert {q for *_, q in rows} <= QUALITIES and any(q == "ok" for *_, q in rows), table
        bad = [(ts, td, ss) for ts, td, ss, _ in rows if session_tag(ts, CAL) != (td, ss)]
        assert not bad, (table, bad[:5])
    # 야간 체인은 단건 보강뿐(전광판 없음), 야간 선물은 단건 CM
    night_chain = _q(
        db,
        "SELECT DISTINCT source FROM chain_snapshots WHERE trade_date = %s AND session = %s",
        N,
        "night",
    )
    assert night_chain == [("fill",)]
    day_chain = {
        r[0] for r in _q(db, "SELECT DISTINCT source FROM chain_snapshots WHERE session = 'day'")
    }
    assert day_chain == {"board", "fill"}
    fut = set(_q(db, "SELECT session, source, market FROM fut_board GROUP BY 1, 2, 3"))
    assert fut == {("day", "board", "F"), ("night", "single", "CM")}
    # 원문: REST·웹소켓 모두 두 세션 (장 밖에서 받은 제어 프레임은 태그 없음)
    raw = set(_q(db, "SELECT trade_date, session, source FROM raw_messages GROUP BY 1, 2, 3"))
    for td, ss in sessions:
        assert (td, ss, "kis_rest") in raw and (td, ss, "kis_ws") in raw, raw
    for ts, td, ss in _q(
        db, "SELECT ts, trade_date, session FROM raw_messages WHERE source = 'kis_rest'"
    ):
        assert session_tag(ts, CAL) == (td, ss), (ts, td, ss)
    # 체결: 수신 시각의 세션 (주간 H0IFCNT0·H0IOCNT0, 야간 H0MFCNT0·H0EUCNT0). 체결 시각(원문
    # HHMMSS)은 수신 시각의 그 초 — 자정 넘은 야간분도 달력일이 맞다
    midnight = False
    for table, trs in (
        ("fut_ticks", {"day": "H0IFCNT0", "night": "H0MFCNT0"}),
        ("opt_ticks", {"day": "H0IOCNT0", "night": "H0EUCNT0"}),
    ):
        rows = _q(db, f"SELECT ts, received_at, trade_date, session, tr_id FROM {table}")  # noqa: S608
        assert {(td, ss) for _, _, td, ss, _ in rows} == sessions, table
        for ts, at, td, ss, tr_id in rows:
            assert session_tag(at, CAL) == (td, ss) and tr_id == trs[ss], (table, at, td, ss)
            assert ts <= at < ts + timedelta(seconds=1), (table, ts, at)
            midnight = midnight or kst(N, 0) <= ts < kst(N, 6)
    assert midnight, "자정 넘은 야간 체결이 없다"
    night_opts = set(
        _q(db, "SELECT mrkt_cls, expiry FROM opt_ticks WHERE session = 'night' GROUP BY 1, 2")
    )
    assert night_opts == {("WKI", "261001")}
    assert _q(db, "SELECT count(*) FROM quarantine")[0][0] == 0


def _check_ws(day: FullDay) -> None:
    assert set(day.seen) == set(PROBES), "구독 확인 시각을 다 지나지 않았다"
    for at, want in PROBES.items():
        assert day.seen[at] == want, (at, sorted(want ^ day.seen[at])[:6])
    assert day.srv.max_subs <= 41 and day.srv.over_limit == 0 and day.srv.errors == []
    assert day.gw is not None and day.gw_finished is True  # 작업 스레드가 큐를 다 비웠다
    assert len(day.srv.connections) == 1  # 하루 내내 한 세션
    w = day.gw.worker.stats
    assert w.raw_published > 0 and w.write_failed == 0 and w.bad_ticks == 0
    assert w.ticks_written == w.ticks - w.untagged > 0
    assert sum(day.gw.outbox.dropped.values()) == 0


def _check_services(db: psycopg.Connection[Any], day: FullDay) -> None:
    # recorder 가 두 채널을 받아 적었다 (발행자가 직접 쓴 것은 없거나 적다)
    rec = day.recorder.stats
    assert rec.written > 0 and rec.bad == 0 and rec.failed == 0
    assert day.rec_alive is False  # stop 뒤 제때 끝났다(남은 묶음을 쓰고)
    assert day.fanout.published > 0
    # poller 수집기: 실패·허가 대기 초과·한도초과 신호·싱크 오류 없이
    col = day.poller.collector.stats
    assert col.executed and not col.failed and not col.skipped, col
    assert col.slowdowns == 0 and col.sink_errors == 0, col
    # 하트비트 — 다섯 서비스 모두 가짜 시계로 60초 안
    for svc in ("scheduler", "poller", "recorder", "ws-gateway", "engine"):
        age = heartbeat_age(day.redis, svc, day.clock.now())
        assert age is not None and 0 <= age <= 60, (svc, age)
    # 치명 health 없음
    bad = _q(
        db,
        "SELECT service, kind, message FROM health_events WHERE level IN ('error', 'critical')",
    )
    assert not bad, bad
    master = _q(
        db,
        "SELECT trade_date, session, count(*) FROM master_snapshots GROUP BY 1, 2 ORDER BY 1",
    )
    n = len(CHAIN.master_rows())
    assert master == [(D, "day", n), (N, "night", n)]


def _open_parts() -> list[tuple[datetime, datetime, bool]]:
    """LIVE 창 중 세션이 열린 부분 (시작, 끝, 알림을 잃는 창인가)."""
    out: list[tuple[datetime, datetime, bool]] = []
    for a, b in LIVE:
        for s, e in OPEN:
            lo, hi = max(a, s), min(b, e)
            if lo < hi:
                out.append((lo, hi, any(x <= lo < y for x, y in LOSE)))
    return out


def _check_engine(db: psycopg.Connection[Any], day: FullDay) -> None:
    """engine: 사이클마다 levels 가 쌓이고(세 범위 × 여덟 레벨), 15:20 만기 전환·주간→야간 전이·
    알림을 잃은 창에서도 끊기지 않는다. 야간은 야간 행(단건 보강)만, S_ref 는 근월물."""
    st = day.engine.stats
    assert st.failures == 0 and st.no_s_ref == 0 and st.write_failures == 0, st
    assert day.ready.stats.published > 0 and day.ready.stats.failed == 0, day.ready.stats
    assert day.ready_lost > 0 and st.ready == day.ready_got - day.ready_lost
    # levels — 사이클(ts)마다 세 범위 × 여덟 레벨, 거래일·세션은 ts 의 태그
    by_ts: dict[datetime, set[tuple[str, str]]] = {}
    for ts, td, ss, scope, name, quality in _q(
        db, "SELECT ts, trade_date, session, scope, name, quality FROM levels"
    ):
        assert session_tag(ts, CAL) == (td, ss) and quality in QUALITIES, (ts, td, ss, quality)
        by_ts.setdefault(ts, set()).add((scope, name))
    assert all(keys == LEVEL_KEYS for keys in by_ts.values())
    cycles = sorted(by_ts)
    assert len(cycles) == st.cycles
    # 사이클은 열린 세션 창에만, 창마다 처음부터 끝(마지막 행 — 창이 닫힌 뒤 따라잡기)까지 끊김 없이
    parts = _open_parts()
    assert all(any(lo <= t < hi for lo, hi, _ in parts) for t in cycles)
    lost = 0
    for lo, hi, lose in parts:
        got = [t for t in cycles if lo <= t < hi]
        gap = LOSE_GAP if lose else READY_GAP
        assert got and got[0] - lo <= gap, (lo, got[:1])
        step = max((b - a for a, b in pairwise(got)), default=timedelta(0))
        assert step <= gap, (lo, step)
        assert hi - got[-1] <= timedelta(seconds=2), (hi, got[-1])
        lost += len(got) if lose else 0
    assert st.catchups >= lost > 0  # 잃은 창의 사이클은 모두 따라잡기
    # 발행: 사이클마다 engine.levels 하나(같은 as_of·레벨)
    assert sorted(m.as_of for m in day.levels_out) == cycles
    assert all({(x.scope, x.name) for x in m.levels} == LEVEL_KEYS for m in day.levels_out)
    metrics = set(_q(db, "SELECT metric, scope, flag FROM metrics GROUP BY 1, 2, 3"))
    scopes = ("all", "nearest", "0dte")
    assert metrics == {
        *((m, s, "visible") for m in ("net_gex", "dex") for s in scopes),
        ("atm_iv", "series", "visible"),
        ("expiry_gamma", "series", "visible"),
        # 확장 지표(Phase 3 항목 2) — 새 지표 기본 shadow: 저장만
        *((m, s, "shadow") for m in ("vex", "cex", "gex_pc") for s in scopes),
        ("iv_term", "all", "shadow"),
        ("skew_25d", "series", "shadow"),
        # 플로우(Phase 3 항목 3) — PCR 은 시리즈마다·전체, 맥스페인은 시리즈마다
        *((m, s, "shadow") for m in ("pcr_oi", "pcr_volume") for s in ("all", "series")),
        ("max_pain", "series", "shadow"),
        # 일별 지표 — POST_DAY 한 번
        *((m, "all", "shadow") for m in ("atm_iv_daily", "iv_rank", "iv_percentile", "iv_hv")),
        # 틱 플로우(항목 3) — HIRO-lite, 대량 체결 기준(체결 기록 20거래일 전이라 비활성)
        ("hiro", "all", "shadow"),
        ("block_trades", "all", "shadow"),
        # 투자자별 순매수(§6.2) — investor_flow 새 행을 그대로, 딜러 가정 점검(§6.3) POST_DAY 한 번
        ("investor_flow", "all", "shadow"),
        ("dealer_check", "all", "shadow"),
    }
    daily = _q(
        db,
        "SELECT ts, trade_date, session, metric, payload FROM metrics WHERE metric = ANY(%s)",
        ["atm_iv_daily", "iv_rank", "iv_percentile", "iv_hv"],
    )
    assert len(daily) == 4 and day.engine.stats.dailies == 1
    assert {(ts, td, ss) for ts, td, ss, _, _ in daily} == {(daily_ts(D), D, "day")}
    (atm,) = [p for *_, m, p in daily if m == "atm_iv_daily"]
    # 딜러 가정 점검 — 그 거래일 한 행(15:45), 그날 주간 증권 계정 콜·풋 조합 여섯이 다 있다
    (dealer,) = _q(
        db, "SELECT ts, trade_date, session, payload FROM metrics WHERE metric = 'dealer_check'"
    )
    assert dealer[:3] == (daily_ts(D), D, "day")
    assert len(dealer[3]["pairs"]) == 6 and None not in dealer[3]["pairs"].values(), dealer
    assert atm["source"] == "self" and atm["series"].startswith("M:")
    cex = [r[0] for r in _q(db, "SELECT DISTINCT ts FROM metrics WHERE metric = 'cex' ORDER BY 1")]
    assert cex and all(
        b - a >= timedelta(seconds=120)
        for a, b in pairwise(cex)
        if session_tag(a, CAL) == session_tag(b, CAL)
    )
    # OI 증감(§6.7): 세션마다 첫 스냅샷부터(세션이 바뀌면 다시), 행 시각 = 체인 행 시각
    oi = _q(db, "SELECT ts, trade_date, session, change, outlier FROM oi_changes")
    assert {(td, ss) for _, td, ss, _, _ in oi} == {(D, "day"), (N, "night")}
    assert all(session_tag(ts, CAL) == (td, ss) and not out for ts, td, ss, _, out in oi)
    assert any(change is None for *_, change, _ in oi)
    _check_engine_ticks(db, day)
    _check_engine_investor(db)
    _check_engine_expiry_switch(db, cycles)
    _check_engine_night(db, day, cycles)
    for kind, ts, message in _q(
        db, "SELECT kind, ts, message FROM health_events WHERE service = 'engine'"
    ):
        assert kind == "engine_series_stale", (kind, message)
        assert any(lo <= ts <= lo + STALE_AT_START for lo, _ in LIVE[1:]), (ts, message)


def _check_engine_ticks(db: psycopg.Connection[Any], day: FullDay) -> None:
    """HIRO-lite(§6.1): ws-gateway 가 낸 체결을 engine 이 다 받아(형식 오류 없이) 세션마다 hiro 행 —
    늘 estimated, 행 시각의 태그가 그 세션, 야간 첫 행은 세션 전환 리셋. 대량 체결 기준(§6.4)은
    세션 거래일마다 한 번, 20거래일 전이라 비활성이고 block_trade 행은 없다."""
    st = day.engine.stats
    assert st.ticks == day.ticks_got > 0 and st.bad_ticks == 0, st
    hiro = _q(
        db,
        "SELECT ts, trade_date, session, quality, payload FROM metrics WHERE metric = 'hiro' "
        "ORDER BY ts",
    )
    assert {(td, ss) for _, td, ss, _, _ in hiro} == {(D, "day"), (N, "night")}
    assert all(q == "estimated" and session_tag(ts, CAL) == (td, ss) for ts, td, ss, q, _ in hiro)
    first_night = next(p for _, _, ss, _, p in hiro if ss == "night")
    assert first_night["reset_reason"] == "session_change"
    assert sum(p["ticks"] + p["unpriced_qty"] for *_, p in hiro) > 0  # 누적이 들었다
    blocks = _q(
        db, "SELECT trade_date, session, payload FROM metrics WHERE metric = 'block_trades'"
    )
    assert sorted((td, ss) for td, ss, _ in blocks) == [(D, "day"), (N, "night")]
    assert not any(p["active"] for *_, p in blocks)
    assert not _q(db, "SELECT 1 FROM metrics WHERE metric = 'block_trade'")


def _check_engine_investor(db: psycopg.Connection[Any]) -> None:
    """투자자별 순매수(§6.2): engine 이 낸 investor_flow 지표 행은 모두 investor_flow 표의 한 행
    (같은 시각·조합·투자자·거래일·세션·순매수) 그대로 — 외국인·개인·기관계·증권만."""
    src = {
        (ts, f"{m}:{sc}:{inv}", td, ss, None if n is None else float(n))
        for ts, m, sc, inv, td, ss, n in _q(
            db,
            "SELECT ts, market_code, sector_code, investor, trade_date, session, net_qty "
            "FROM investor_flow WHERE investor = ANY(%s) AND quality <> 'invalid'",
            ["frgn", "prsn", "orgn", "scrt"],
        )
    }
    got = {
        (ts, key, td, ss, None if v is None else float(v))
        for ts, key, td, ss, v in _q(
            db,
            "SELECT ts, key, trade_date, session, value FROM metrics "
            "WHERE metric = 'investor_flow'",
        )
    }
    assert got and got <= src, sorted(got - src)[:3]
    assert {(td, ss) for _, _, td, ss, _ in got} >= {(D, "day")}


def _scope_expiries(db: psycopg.Connection[Any], scope: str) -> dict[datetime, list[str]]:
    rows = _q(db, "SELECT ts, payload FROM metrics WHERE metric = 'net_gex' AND scope = %s", scope)
    return {ts: payload["expiries"] for ts, payload in rows}


def _check_engine_expiry_switch(db: psycopg.Connection[Any], cycles: list[datetime]) -> None:
    """15:20 WKM 260904 만기: 그 뒤 첫 사이클(걸침 — 행이 온 때만 한 번)까지만 평가하고, 그 뒤엔
    0DTE 가 비고 nearest 는 WKI 261001, all 에 새 추적 시리즈 WKM 261001 이 든다."""
    before = [t for t in cycles if kst(D, 15, 17) <= t < EXPIRY_SWITCH]
    after = [t for t in cycles if EXPIRY_SWITCH <= t < kst(D, 15, 45)]
    assert before and len(after) > 1
    straddle = after[0]
    old = _q(
        db,
        "SELECT max(ts) FROM strike_gex WHERE mrkt_cls = 'WKM' AND expiry = '260904'",
    )[0][0]
    assert old is not None and old <= straddle
    zero, near, every = (_scope_expiries(db, s) for s in ("0dte", "nearest", "all"))
    for t in before:
        assert zero[t] == near[t] == ["WKM:260904"], (t, zero[t], near[t])
        assert "WKM:261001" not in every[t]
    for t in after[1:]:
        assert zero[t] == [] and near[t] == ["WKI:261001"], (t, zero[t], near[t])
        assert "WKM:260904" not in every[t] and "WKM:261001" in every[t], (t, every[t])
    moves = dict(
        _q(
            db,
            "SELECT ts, detail->>'expiry' FROM levels "
            "WHERE scope = 'nearest' AND name = 'expected_move_calendar'",
        )
    )
    assert {moves[t] for t in before} == {"WKM:260904"}
    assert {moves[t] for t in after[1:]} == {"WKI:261001"}


def _check_engine_night(db: psycopg.Connection[Any], day: FullDay, cycles: list[datetime]) -> None:
    """야간(분기 B): 야간 사이클은 야간 행(단건 보강)만 — 주간 전광판 행을 섞지 않는다. 마지막
    산출(engine:latest)은 야간 귀속·근월물 S_ref(단건 CM)."""
    for table in ("strike_gex", "option_iv"):
        rows = _q(db, f"SELECT DISTINCT ts, trade_date, session FROM {table}")  # noqa: S608
        assert {(td, ss) for _, td, ss in rows} == {(D, "day"), (N, "night")}, table
        assert all(session_tag(ts, CAL) == (td, ss) for ts, td, ss in rows), table
    sources = set(_q(db, "SELECT session, quote_source FROM option_iv GROUP BY 1, 2"))
    assert sources == {("day", "board"), ("day", "fill"), ("night", "fill")}
    raw = day.redis.get(ENGINE_LATEST_KEY)
    assert raw is not None
    latest = EngineLatest.model_validate_json(raw)  # type: ignore[arg-type]
    assert (latest.as_of, latest.trade_date, latest.session) == (cycles[-1], N, "night")
    assert not latest.stale and latest.near_code == FUT_CODE
    assert latest.s_ref == float(FUT_PRICE) and latest.s_ref_quality == "ok"
    assert {s.status for s in latest.series} == {"evaluated"}

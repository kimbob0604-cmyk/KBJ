"""scheduler 서비스 — 상태 머신 발행·session_log·마스터 갱신·KRX 전일 적재·KIS 분봉 적재 구동
(PLAN §4.6, 설계 §2·§5·§6·§8).

가짜 시계로 하루(와 추석·금요일 밤)를 돌린다. fakeredis·가짜 저장소·가짜 마스터 내려받기·가짜
KRX(tests/fakes/krx_server.py)·가짜 KIS 분봉(tests/fakes/kis_server.py)만 쓴다.
"""

from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from collections.abc import Sequence
from concurrent.futures import Future
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import httpx
import pytest

from core.calendar import State, TradingCalendar
from data.kis.master import MasterRow, parse_master
from data.kis.rest import MINUTE_TR
from data.krx.eod import OPT_DAILY
from data.store import SessionLogRecord, StoreError
from services.auth.health import MemoryHealthSink
from services.bus import (
    MASTER_KEY,
    MASTER_SHA_KEY,
    SESSION_EVENTS,
    SESSION_STATE,
    MasterSnapshot,
    SessionState,
    heartbeat_key,
)
from services.chain_feed import load_context
from services.runtime import Heartbeater
from services.scheduler.krx import KrxCallBudget, KrxDaily, KrxLoader, krx_thread_submit
from services.scheduler.minute import (
    MinuteDaily,
    MinuteLoader,
    minute_thread_submit,
    reader_kis_client,
)
from services.scheduler.service import Scheduler, run
from tests.fakes.kis_server import (
    FakeClock,
    FakeKisServer,
    MinuteMemoryStore,
    day_bar_times,
    default_chain,
    fake_settings,
    night_bar_times,
    seed_cached_token,
)
from tests.fakes.krx_server import FakeKrx, KrxMemoryStore, fixture_rows

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
MASTER_TEXT = "\n".join(default_chain().master_lines()) + "\n"


def kst(*a: int) -> datetime:
    return datetime(*a, tzinfo=KST)  # type: ignore[misc]


def master_zip(text: str = MASTER_TEXT) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fo_idx_code_mts.mst", text.encode("cp949"))
    return buf.getvalue()


class Store:
    def __init__(self) -> None:
        self.log: list[SessionLogRecord] = []
        self.masters: list[tuple[int, datetime, date, str]] = []
        self.fail_log = False
        self.flushes = 0

    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None:
        if self.fail_log:
            raise StoreError("session_log: 접속 실패")
        self.log.extend(rows)

    def write_master(
        self, rows: Sequence[MasterRow], *, ts: datetime, trade_date: date, session: str
    ) -> None:
        self.masters.append((len(rows), ts, trade_date, session))

    def flush_spool(self) -> bool:
        self.flushes += 1
        return True


class Downloader:
    def __init__(self, *results: bytes | Exception) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self) -> bytes:
        self.calls += 1
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, Exception):
            raise r
        return r


def inline_submit(fn: Any) -> Future[bytes]:
    """내려받기를 그 자리에서(가짜 시계 시험) — 서비스 기본은 스레드(`thread_submit`)."""
    fut: Future[bytes] = Future()
    try:
        fut.set_result(fn())
    except Exception as e:
        fut.set_exception(e)
    return fut


class Now:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class Rig:
    def __init__(
        self,
        start: datetime,
        downloader: Any = None,
        server: Any = None,
        *,
        submit: Any = inline_submit,  # None = 서비스 기본(스레드)
        krx: Any = None,
        minute: Any = None,
    ) -> None:
        self.server = server or fakeredis.FakeServer()
        self.redis = fakeredis.FakeRedis(server=self.server)
        self.sub = self.redis.pubsub(ignore_subscribe_messages=True)
        self.sub.subscribe(SESSION_EVENTS)
        self.sub.get_message(timeout=0.01)
        self.store = Store()
        self.health = MemoryHealthSink()
        self.now = Now(start)
        self.download = downloader or Downloader(master_zip())
        self.sched = Scheduler(
            self.redis,
            self.store,
            CAL,
            downloader=self.download,
            health=self.health,
            now=self.now,
            **({} if submit is None else {"submit": submit}),
            krx=krx,
            minute=minute,
        )

    def run_until(self, end: datetime, every: timedelta = timedelta(seconds=30)) -> None:
        while self.now.t < end:
            self.sched.step()
            self.now.t += every

    def events(self) -> list[SessionState]:
        out: list[SessionState] = []
        while (m := self.sub.get_message(timeout=0.001)) is not None:
            out.append(SessionState.model_validate_json(m["data"]))
        return out

    def state_key(self) -> SessionState:
        raw = self.redis.get(SESSION_STATE)
        assert isinstance(raw, bytes)
        return SessionState.model_validate_json(raw)

    def master(self) -> MasterSnapshot | None:
        raw = self.redis.get(MASTER_KEY)
        return None if raw is None else MasterSnapshot.model_validate_json(raw)  # type: ignore[arg-type]


def test_one_trading_day_publishes_every_transition_once() -> None:
    rig = Rig(kst(2026, 9, 28, 7, 0))  # 월 07:00 — IDLE (금 09-25 추석이라 전날 밤 없음)
    rig.run_until(kst(2026, 9, 29, 7, 0))
    got = [(e.state, e.trade_date, e.session, e.at) for e in rig.events()]
    d28, d29 = date(2026, 9, 28), date(2026, 9, 29)
    assert got == [
        (State.IDLE, None, None, kst(2026, 9, 28, 7, 0)),
        (State.PRE_DAY, None, None, kst(2026, 9, 28, 8, 0)),
        (State.DAY, d28, "day", kst(2026, 9, 28, 8, 45)),
        (State.POST_DAY, None, None, kst(2026, 9, 28, 15, 45)),
        (State.PRE_NIGHT, None, None, kst(2026, 9, 28, 17, 50)),
        (State.NIGHT, d29, "night", kst(2026, 9, 28, 18, 0)),
        (State.IDLE, None, None, kst(2026, 9, 29, 6, 0)),
    ]
    assert rig.state_key().state is State.IDLE
    raw = json.loads(rig.redis.get(SESSION_STATE))  # type: ignore[arg-type]
    assert set(raw) == {"state", "trade_date", "session", "at"}

    log = rig.store.log
    assert [(r.state, r.prev_state) for r in log] == [
        (State.IDLE, None),
        (State.PRE_DAY, State.IDLE),
        (State.DAY, State.PRE_DAY),
        (State.POST_DAY, State.DAY),
        (State.PRE_NIGHT, State.POST_DAY),
        (State.NIGHT, State.PRE_NIGHT),
        (State.IDLE, State.NIGHT),
    ]
    assert log[0].detail == {"startup": True} and log[2].trade_date == d28
    assert (log[5].trade_date, log[5].session) == (d29, "night")

    # 마스터: 기동(Redis 에 없음, 장 밖 → DB 엔 안 씀) + PRE_DAY + PRE_NIGHT
    assert rig.download.calls == 3
    rows = len(parse_master(MASTER_TEXT))
    assert rig.store.masters == [
        (rows, kst(2026, 9, 28, 8, 0), d28, "day"),
        (rows, kst(2026, 9, 28, 17, 50), d29, "night"),
    ]
    snap = rig.master()
    assert snap is not None and (snap.trade_date, snap.session) == (d29, "night")
    assert snap.text == MASTER_TEXT and snap.rows == rows
    assert rig.redis.get(MASTER_SHA_KEY) == snap.sha256.encode()
    assert [e.kind for e in rig.health.events] == ["master_refreshed"] * 3


def test_holiday_eve_has_no_night_session() -> None:
    rig = Rig(kst(2026, 9, 23, 15, 0))  # 수 — 다음 날 추석 전날 휴장
    rig.run_until(kst(2026, 9, 28, 9, 0), every=timedelta(minutes=1))
    states = [(e.state, e.trade_date) for e in rig.events()]
    assert states == [
        (State.DAY, date(2026, 9, 23)),
        (State.POST_DAY, None),
        (State.IDLE, None),  # 17:50 에 PRE_NIGHT 가 아니다
        (State.PRE_DAY, None),
        (State.DAY, date(2026, 9, 28)),
    ]


def test_friday_night_belongs_to_monday() -> None:
    rig = Rig(kst(2026, 9, 18, 17, 0))
    rig.run_until(kst(2026, 9, 19, 7, 0), every=timedelta(minutes=1))
    night = [e for e in rig.events() if e.state is State.NIGHT]
    assert [(e.trade_date, e.session) for e in night] == [(date(2026, 9, 21), "night")]
    assert rig.store.masters[-1][2:] == (date(2026, 9, 21), "night")


def test_startup_skips_the_download_when_the_master_is_for_this_session() -> None:
    rig = Rig(kst(2026, 9, 28, 10, 0))
    snap = MasterSnapshot(
        asof=kst(2026, 9, 28, 8, 0),
        trade_date=date(2026, 9, 28),
        session="day",
        rows=1,
        sha256="x",
        text="t",
    )
    rig.redis.set(MASTER_KEY, snap.model_dump_json())
    rig.sched.step()
    assert rig.download.calls == 0 and rig.sched.master_sha == "x"
    old = snap.model_copy(update={"trade_date": date(2026, 9, 25)})
    rig2 = Rig(kst(2026, 9, 28, 10, 0))
    rig2.redis.set(MASTER_KEY, old.model_dump_json())
    rig2.sched.step()
    assert rig2.download.calls == 1  # 지난 세션 마스터 → 곧바로
    assert rig2.store.masters[0][2:] == (date(2026, 9, 28), "day")


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        (OSError("network down"), "내려받기 실패: OSError"),
        (b"not a zip", "파싱 실패"),
        (master_zip("1|A01612|KR4A01612|F 202612| |00000.00|1|2001|KOSPI200\n"), "옵션 행이 없는"),
    ],
)
def test_failed_refresh_keeps_the_previous_master_and_retries(bad: Any, why: str) -> None:
    dl = Downloader(bad, bad, master_zip())
    rig = Rig(kst(2026, 9, 28, 7, 59), dl)
    prev = MasterSnapshot(
        asof=kst(2026, 9, 27, 17, 50),
        trade_date=date(2026, 9, 28),
        session="day",
        rows=1,
        sha256="prev",
        text="t",
    )
    rig.redis.set(MASTER_KEY, prev.model_dump_json())
    rig.run_until(kst(2026, 9, 28, 8, 0, 30), every=timedelta(seconds=30))  # PRE_DAY 진입
    assert dl.calls == 1
    snap = rig.master()
    assert snap is not None and snap.sha256 == "prev"  # 직전 것을 둔다
    (ev,) = rig.health.of("master_refresh_failed")
    assert why in ev.detail and ev.severity == "warning"
    rig.run_until(kst(2026, 9, 28, 8, 3), every=timedelta(seconds=30))
    assert dl.calls == 3  # 60초마다 다시 — 세 번째에 성공
    snap = rig.master()
    assert snap is not None and snap.sha256 != "prev" and rig.sched.master_due is None


def test_redis_outage_delays_the_event_until_it_comes_back() -> None:
    rig = Rig(kst(2026, 9, 28, 8, 44))
    rig.sched.step()
    rig.events()
    rig.server.connected = False
    rig.now.t = kst(2026, 9, 28, 8, 45)
    rig.sched.step()  # DAY — 발행 실패
    rig.now.t += timedelta(seconds=1)
    rig.sched.step()
    assert rig.health.kinds().count("session_publish_failed") == 1
    rig.server.connected = True
    rig.now.t += timedelta(seconds=1)
    rig.sched.step()
    (ev,) = rig.events()
    assert ev.state is State.DAY and ev.at == kst(2026, 9, 28, 8, 45)  # 바뀐 시각 그대로
    assert rig.state_key() == ev


def test_state_key_is_rewritten_if_redis_lost_it() -> None:
    rig = Rig(kst(2026, 9, 28, 10, 0))
    rig.sched.step()
    rig.redis.delete(SESSION_STATE)
    rig.now.t += timedelta(seconds=31)
    rig.sched.step()
    assert rig.state_key().state is State.DAY


def test_session_log_failure_does_not_block_publishing() -> None:
    rig = Rig(kst(2026, 9, 28, 10, 0))
    rig.store.fail_log = True
    rig.sched.step()
    assert [e.state for e in rig.events()] == [State.DAY]
    assert "session_log_failed" in rig.health.kinds()


def test_run_loop_steps_flushes_the_spool_and_beats() -> None:
    """고정 시각(월 10:00 DAY)·가짜 단조 시계·가짜 대기 — 실제 시계와 날짜에 기대지 않는다."""
    rig = Rig(kst(2026, 9, 28, 10, 0))
    stop = threading.Event()
    hb = Heartbeater(rig.redis, "scheduler", now=rig.now)
    mono = [100.0]
    waits: list[float] = []

    def clock() -> float:
        mono[0] += 0.25  # 한 step 이 0.25초 걸린 것처럼
        return mono[0]

    def wait(s: float) -> bool:
        waits.append(s)
        rig.now.t += timedelta(seconds=1)
        if len(waits) == 3:
            stop.set()
        return stop.is_set()

    run(rig.sched, stop, heartbeat=hb, clock=clock, wait=wait)
    assert rig.store.flushes == 3 and waits == [0.75, 0.75, 0.75]
    assert [e.state for e in rig.events()] == [State.DAY]  # 바뀐 때 한 번만
    assert rig.sched.state is State.DAY and rig.redis.get(heartbeat_key("scheduler")) is not None


class GatedDownloader:
    """gate 가 열릴 때까지(최대 5초) 막히는 내려받기 — 느리거나 멈춘 KIS 파일 서버."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.started = threading.Event()
        self.calls = 0

    def __call__(self) -> bytes:
        self.calls += 1
        self.started.set()
        self.gate.wait(5)
        return master_zip()


def _seed_prev_master(rig: Rig) -> None:
    prev = MasterSnapshot(
        asof=kst(2026, 9, 28, 8, 0),
        trade_date=date(2026, 9, 28),
        session="day",
        rows=1,
        sha256="prev",
        text="t",
    )
    rig.redis.set(MASTER_KEY, prev.model_dump_json())


def test_a_slow_master_download_does_not_hold_up_the_state_loop() -> None:
    """PRE_NIGHT 는 10분뿐 — 내려받기가 걸려 있어도 18:00 NIGHT 는 그 step 에 발행된다.
    내려받기는 스레드에서(서비스 기본), 끝나면 다음 step 이 PRE_NIGHT 태그로 적용한다."""
    dl = GatedDownloader()
    rig = Rig(kst(2026, 9, 28, 17, 49, 59), dl, submit=None)
    _seed_prev_master(rig)
    rig.sched.step()  # POST_DAY — Redis 에 마스터가 있어 기동 때 받지 않는다
    rig.events()
    t0 = time.monotonic()
    rig.now.t = kst(2026, 9, 28, 17, 50)
    rig.sched.step()  # PRE_NIGHT 진입 — 내려받기 시작
    assert dl.started.wait(5)
    rig.now.t = kst(2026, 9, 28, 18, 0)
    rig.sched.step()  # NIGHT — 내려받기는 아직 걸려 있다
    assert time.monotonic() - t0 < 1.0
    got = [(e.state, e.trade_date, e.session) for e in rig.events()]
    d29 = date(2026, 9, 29)
    assert got == [(State.PRE_NIGHT, None, None), (State.NIGHT, d29, "night")]
    snap = rig.master()
    assert snap is not None and snap.sha256 == "prev" and rig.store.masters == []

    dl.gate.set()
    deadline = time.monotonic() + 5
    rig.now.t = kst(2026, 9, 28, 18, 0, 1)
    while rig.store.masters == []:
        assert time.monotonic() < deadline
        rig.sched.step()
        time.sleep(0.01)
    rows = len(parse_master(MASTER_TEXT))
    assert rig.store.masters == [(rows, kst(2026, 9, 28, 18, 0, 1), d29, "night")]
    snap = rig.master()
    assert snap is not None and (snap.trade_date, snap.session) == (d29, "night")
    assert rig.sched.master_due is None and dl.calls == 1


class ManualSubmit:
    """내려받기를 붙잡아 두었다가 시험이 끝낸다 — 스레드 없이 '받는 중' 을 만든다."""

    def __init__(self) -> None:
        self.pending: list[tuple[Any, Future[bytes]]] = []
        self.calls = 0

    def __call__(self, fn: Any) -> Future[bytes]:
        self.calls += 1
        fut: Future[bytes] = Future()
        self.pending.append((fn, fut))
        return fut

    def finish(self) -> None:
        fn, fut = self.pending.pop(0)
        try:
            fut.set_result(fn())
        except Exception as e:
            fut.set_exception(e)


@pytest.mark.parametrize("first", [master_zip(), OSError("network down")])
def test_a_transition_while_downloading_asks_for_another_download(first: Any) -> None:
    """17:49 기동 내려받기가 걸린 채 PRE_NIGHT 가 오면, 그것이 끝난 뒤(성공이든 실패든 60초를
    기다리지 않고) 야간 것을 다시 받는다 — 앞의 결과가 새 요청을 지우지 않는다."""
    sub = ManualSubmit()
    rig = Rig(kst(2026, 9, 28, 17, 49), Downloader(first, master_zip()), submit=sub)
    rig.sched.step()  # POST_DAY 기동 — Redis 에 마스터가 없어 곧바로 (A)
    assert sub.calls == 1
    rig.now.t = kst(2026, 9, 28, 17, 50)
    rig.sched.step()  # PRE_NIGHT — A 가 아직 걸려 있다
    assert sub.calls == 1
    sub.finish()
    rig.now.t += timedelta(seconds=1)
    rig.sched.step()  # A 적용(장 밖 태그 → DB 엔 안 씀) 또는 실패
    rig.now.t += timedelta(seconds=1)
    rig.sched.step()  # 야간 요청이 남아 있다 → B
    assert sub.calls == 2
    sub.finish()
    rig.now.t += timedelta(seconds=1)
    rig.sched.step()
    rows = len(parse_master(MASTER_TEXT))
    assert rig.store.masters == [(rows, kst(2026, 9, 28, 17, 50, 3), date(2026, 9, 29), "night")]
    assert rig.sched.master_due is None


class Chunks(httpx.SyncByteStream):
    def __init__(self, parts: list[bytes]) -> None:
        self.parts = parts

    def __iter__(self) -> Any:
        yield from self.parts


def _transport(status: int, parts: list[bytes], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(status, stream=Chunks(parts))

    return httpx.MockTransport(handle)


# KBJ P2: test_http_master_downloader_reads_the_zip_through_the_transport 는 kbj tests/unit/kis/test_master_download.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_http_master_downloader_gives_up_after_the_total_deadline 는 kbj tests/unit/kis/test_master_download.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# KBJ P2: test_http_master_downloader_refuses_errors_and_oversized_files 는 kbj tests/unit/kis/test_master_download.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


# ── KRX 전일 적재 구동 ──


class KrxRig:
    """가짜 KRX → KrxLoader → 메모리 저장소, KrxDaily 를 Scheduler 에 끼운다."""

    def __init__(
        self, now: Now, *, submit: Any = None, options: list[dict[str, Any]] | None = None
    ) -> None:
        self.fake = FakeKrx()
        self.fake.publish(date(2026, 9, 23), options=options)
        self.store = KrxMemoryStore()
        self.budget = KrxCallBudget(fakeredis.FakeRedis(), 200)
        self.loader = KrxLoader(self.fake.client(), self.store, self.budget, now=now)
        self.submit = submit or inline_krx

    def daily(self, health: Any) -> KrxDaily:
        return KrxDaily(self.loader, CAL, health, submit=self.submit)


def inline_krx(fn: Any) -> Future[Any]:
    fut: Future[Any] = Future()
    try:
        fut.set_result(fn())
    except Exception as e:
        fut.set_exception(e)
    return fut


def krx_rig(
    start: datetime,
    *,
    submit: Any = None,
    options: list[dict[str, Any]] | None = None,
    **kw: Any,
) -> tuple[Rig, KrxRig]:
    """Scheduler 와 같은 시계·health 로 KrxDaily 를 끼운다."""
    rig = Rig(start, **kw)
    k = KrxRig(rig.now, submit=submit, options=options)
    rig.sched.krx = k.daily(rig.health)
    return rig, k


def test_the_previous_trading_day_is_loaded_once_on_a_trading_day() -> None:
    rig, k = krx_rig(kst(2026, 9, 28, 7, 0))
    rig.run_until(kst(2026, 9, 29, 7, 0))
    assert k.fake.requested() == [
        ("/drv/opt_bydd_trd", "20260923"),
        ("/drv/fut_bydd_trd", "20260923"),
    ]
    assert len(k.store.options) == 13 and len(k.store.futures) == 3
    (ev,) = rig.health.of("krx_daily_loaded")
    assert ev.at == kst(2026, 9, 28, 8, 5, 0) and ev.service == "scheduler"
    assert [e.state for e in rig.events()][:3] == [State.IDLE, State.PRE_DAY, State.DAY]
    assert rig.health.kinds().count("master_refreshed") == 3  # 마스터는 그대로


def test_a_slow_krx_load_does_not_hold_up_the_state_loop() -> None:
    """PRE_DAY 08:05 에 시작한 적재가 걸려 있어도 08:45 DAY 는 그 step 에 발행된다."""
    gate, started = threading.Event(), threading.Event()
    rig, k = krx_rig(kst(2026, 9, 28, 8, 44, 58), submit=krx_thread_submit)

    def hold(_: Any) -> None:
        started.set()
        gate.wait(5)

    k.fake.on_request = hold
    _seed_prev_master(rig)
    rig.sched.step()  # PRE_DAY 기동 — KRX 적재 시작(스레드)
    assert started.wait(5) and rig.sched.krx is not None and rig.sched.krx.busy
    rig.events()
    t0 = time.monotonic()
    rig.now.t = kst(2026, 9, 28, 8, 45)
    rig.sched.step()
    assert time.monotonic() - t0 < 1.0
    assert [(e.state, e.trade_date) for e in rig.events()] == [(State.DAY, date(2026, 9, 28))]
    assert rig.health.of("krx_daily_loaded") == []
    gate.set()
    deadline = time.monotonic() + 5
    while rig.sched.krx.busy:  # 적재 → 마스터 대조(둘 다 스레드)
        assert time.monotonic() < deadline
        rig.now.t += timedelta(seconds=1)
        rig.sched.step()
        time.sleep(0.01)
    assert k.fake.calls() == 2 and len(rig.health.of("krx_daily_loaded")) == 1
    assert [r.ok for r in _krx_day(rig).reports] == [True]


class BrokenKrx:
    busy = False

    def step(self, now: datetime, master: MasterSnapshot | None = None) -> None:
        raise RuntimeError("boom")


def test_a_krx_step_error_is_isolated() -> None:
    rig = Rig(kst(2026, 9, 28, 7, 59), krx=BrokenKrx())
    rig.run_until(kst(2026, 9, 28, 8, 1))
    assert [e.state for e in rig.events()] == [State.IDLE, State.PRE_DAY]
    failed = rig.health.of("krx_step_failed")  # 30초마다 4 step, health 는 60초에 한 번
    assert [e.at for e in failed] == [kst(2026, 9, 28, 7, 59), kst(2026, 9, 28, 8, 0)]
    assert "RuntimeError" in rig.health.of("krx_step_failed")[0].detail
    assert "master_refreshed" in rig.health.kinds()


def _krx_day(rig: Rig) -> Any:
    assert rig.sched.krx is not None and rig.sched.krx.day is not None
    return rig.sched.krx.day


def test_the_pre_day_master_is_checked_against_krx_once_both_are_in() -> None:
    """07:00 기동 마스터(PRE_DAY 전)는 대조하지 않고, 08:00 PRE_DAY 마스터 + 08:05 KRX 로 한 번.
    PRE_NIGHT 마스터는 같은 파일(sha)이라 다시 하지 않는다. 가짜 마스터는 KRX 발췌를 덮는다."""
    rig, _ = krx_rig(kst(2026, 9, 28, 7, 0))
    rig.run_until(kst(2026, 9, 29, 7, 0))
    (rep,) = _krx_day(rig).reports
    assert rep.ok and rep.krx_date == date(2026, 9, 23) and rep.compared == 3
    assert "master_krx_missing" not in rig.health.kinds()
    assert "master_krx_check_failed" not in rig.health.kinds()


def test_a_missing_strike_is_one_warning_for_the_day() -> None:
    rows = fixture_rows(OPT_DAILY, date(2026, 9, 23))
    extra = rows[0] | {"ISU_NM": "코스피200 C 202610 1,600.0 (정규)", "ISU_CD": "B016AZZZ"}
    rig, _ = krx_rig(kst(2026, 9, 28, 7, 0), options=[*rows, extra])
    rig.run_until(kst(2026, 9, 29, 7, 0))
    (ev,) = rig.health.of("master_krx_missing")
    assert ev.at == kst(2026, 9, 28, 8, 5) and ev.service == "scheduler"
    assert "코스피200 202610 C 1,600.0" in ev.detail


def test_a_restart_checks_the_master_already_in_redis() -> None:
    """10:00 재기동: Redis 의 PRE_DAY 마스터를 그대로 쓰고(내려받지 않음), KRX 는 DB 에 있다 →
    부르지 않고 대조만 한다."""
    rig, k = krx_rig(kst(2026, 9, 28, 10, 0))
    k.loader.load(date(2026, 9, 23))  # 재기동 전에 받아 둔 것
    requested = k.fake.calls()
    snap = MasterSnapshot(
        asof=kst(2026, 9, 28, 8, 0),
        trade_date=date(2026, 9, 28),
        session="day",
        rows=len(parse_master(MASTER_TEXT)),
        sha256=MasterSnapshot.digest(MASTER_TEXT),
        text=MASTER_TEXT,
    )
    rig.redis.set(MASTER_KEY, snap.model_dump_json())
    rig.run_until(kst(2026, 9, 28, 10, 5))
    assert rig.download.calls == 0 and rig.sched.master == snap
    assert k.fake.calls() == requested
    (rep,) = _krx_day(rig).reports
    assert rep.ok


# ── KIS 분봉 적재 구동 ──

NEAR = "A01612"  # 가짜 체인 근월물


class MinuteRig:
    """가짜 KIS 분봉 → 진짜 MinuteLoader(Redis 레이트리미터·auth 토큰 읽기 — scheduler 조립과 같게)
    → 메모리 저장소. MinuteDaily 를 Scheduler 에 끼우고, 가짜 KIS 시계를 scheduler 시계에 맞춘다."""

    def __init__(self, rig: Rig, *, submit: Any = inline_krx) -> None:
        self.rig = rig
        self.clock = FakeClock(rig.now.t)
        self.server = FakeKisServer(self.clock, default_chain(), CAL)
        seed_cached_token(rig.redis, rig.now.t)
        kis = reader_kis_client(
            fake_settings(),
            rig.redis,
            clock=self.clock,
            now=self.clock.now,
            transport=self.server.transport,
        )
        self.store = MinuteMemoryStore()
        loader = MinuteLoader(
            kis, self.store, CAL, now=self.clock.now, context=lambda: load_context(rig.redis)
        )
        rig.sched.minute = MinuteDaily(loader, CAL, rig.health, submit=submit)

    def run_until(self, end: datetime, every: timedelta = timedelta(seconds=30)) -> None:
        while self.rig.now.t < end:
            self.clock.set(self.rig.now.t)
            self.rig.sched.step()
            self.rig.now.t += every


def test_minute_bars_load_after_the_day_and_after_the_night() -> None:
    rig = Rig(kst(2026, 9, 28, 15, 0))
    m = MinuteRig(rig)
    m.server.add_minute_bars(NEAR, "F", day_bar_times(date(2026, 9, 28)))
    m.server.add_minute_bars(NEAR, "CM", night_bar_times(date(2026, 9, 28)))
    m.run_until(kst(2026, 9, 29, 7, 0))
    assert [c[0] for c in m.server.minute_calls()] == ["F"] * 5 + ["CM"] * 8
    loaded = rig.health.of("minute_bars_loaded")
    assert [(e.at, e.service) for e in loaded] == [
        (kst(2026, 9, 28, 16, 0), "scheduler"),
        (kst(2026, 9, 29, 6, 10), "scheduler"),
    ]
    assert len(m.store.of(NEAR, "F")) == 411 and len(m.store.of(NEAR, "CM")) == 721
    assert m.server.token_posts == 0  # 발급은 auth 만
    assert [e.state for e in rig.events()] == [
        State.DAY,
        State.POST_DAY,
        State.PRE_NIGHT,
        State.NIGHT,
        State.IDLE,
    ]


def test_a_slow_minute_load_does_not_hold_up_the_state_loop() -> None:
    """17:49:58 에 시작한 주간 분봉 적재가 걸려 있어도 17:50 PRE_NIGHT 는 그 step 에 발행된다."""
    gate, started = threading.Event(), threading.Event()
    rig = Rig(kst(2026, 9, 28, 17, 49, 58))
    m = MinuteRig(rig, submit=minute_thread_submit)
    m.server.add_minute_bars(NEAR, "F", day_bar_times(date(2026, 9, 28)))

    def hold(_: Any) -> None:
        started.set()
        gate.wait(5)

    m.server.inject("malformed", tr_id=MINUTE_TR, mutate=hold)
    rig.sched.step()  # POST_DAY 기동 — 마스터(그 자리) → 분봉 적재 시작(스레드)
    assert started.wait(5) and rig.sched.minute is not None and rig.sched.minute.busy
    rig.events()
    t0 = time.monotonic()
    rig.now.t = kst(2026, 9, 28, 17, 50)
    rig.sched.step()
    assert time.monotonic() - t0 < 1.0
    assert [e.state for e in rig.events()] == [State.PRE_NIGHT]
    gate.set()
    deadline = time.monotonic() + 5
    while rig.sched.minute.busy:
        assert time.monotonic() < deadline
        rig.now.t += timedelta(seconds=1)
        rig.sched.step()
        time.sleep(0.01)
    assert len(rig.health.of("minute_bars_loaded")) == 1 and len(m.store.of(NEAR, "F")) == 411


class BrokenMinute:
    busy = False

    def step(self, now: datetime, master: MasterSnapshot | None = None) -> None:
        raise RuntimeError("boom")


def test_a_minute_step_error_is_isolated() -> None:
    rig = Rig(kst(2026, 9, 28, 15, 59), minute=BrokenMinute())
    rig.run_until(kst(2026, 9, 28, 16, 1))
    failed = rig.health.of("minute_step_failed")  # 30초마다 4 step, health 는 60초에 한 번
    assert [e.at for e in failed] == [kst(2026, 9, 28, 15, 59), kst(2026, 9, 28, 16, 0)]
    assert "RuntimeError" in failed[0].detail
    assert [e.state for e in rig.events()] == [State.POST_DAY]
    assert "master_refreshed" in rig.health.kinds()

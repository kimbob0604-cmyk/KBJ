"""scheduler 서비스 — 세션 상태 발행·session_log·실행 루프(설계 §1.7).

GEXLAB `tests/unit/test_scheduler_service.py`(43a9ed1)의 상태 발행 시험 7개(:184·:229·:242·:303·
:321·:330·:338) 승격 — import 경로와 `Rig` 생성자(GX `Scheduler` → kbj `SchedulerService`)만 바꿨다.
시험 본문의 상태·발행·기록 단언은 그대로다. GX 첫 시험(:184)·금요일 밤 시험(:242)의
**마스터** 단언(`rig.download`·`rig.store.masters`·`rig.master()`)은 마스터 갱신이 P7 까지
legacy GX 에 남으므로(D-P2-5, 등록부 `kis.master` runner: external) 이 파일에서 뺐다 —
원본 시험은 legacy GX 에 그대로 있다.

가짜 시계로 하루(와 추석·금요일 밤)를 돌린다. fakeredis·가짜 저장소만 쓴다.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import fakeredis

from kbj.core.calendar import State, TradingCalendar
from kbj.services.runtime.health import MemoryHealthSink
from kbj.services.runtime.heartbeat import Heartbeater
from kbj.services.scheduler.service import (
    SchedulerService,
    SessionLogRecord,
    SessionState,
    run,
)
from kbj.store.db import StoreError
from kbj.store.redis_keys import SESSION_EVENTS, SESSION_STATE, heartbeat_key

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()


def kst(*a: int) -> datetime:
    return datetime(*a, tzinfo=KST)  # type: ignore[misc]


class Store:
    def __init__(self) -> None:
        self.log: list[SessionLogRecord] = []
        self.fail_log = False
        self.flushes = 0

    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None:
        if self.fail_log:
            raise StoreError("session_log: 접속 실패")
        self.log.extend(rows)

    def flush_spool(self) -> bool:
        self.flushes += 1
        return True


class Now:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class Rig:
    def __init__(self, start: datetime, server: fakeredis.FakeServer | None = None) -> None:
        self.server = server or fakeredis.FakeServer()
        self.redis = fakeredis.FakeRedis(server=self.server)
        self.sub = self.redis.pubsub(ignore_subscribe_messages=True)
        self.sub.subscribe(SESSION_EVENTS)
        self.sub.get_message(timeout=0.01)
        self.store = Store()
        self.health = MemoryHealthSink()
        self.now = Now(start)
        self.sched = SchedulerService(
            self.redis, None, CAL, self.store, now=self.now, health=self.health
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
    # (KBJ) GX 의 마스터 단언(`master_refreshed` × 3 health 포함)은 legacy GX 에 남는다.
    # 마스터가 없는 kbj 서비스는 정상 하루에 health 를 하나도 내지 않는다 — 뺀 단언 자리를 채운다.
    assert rig.health.kinds() == []


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


# ── KBJ 추가: 실행기 연결(설계 §6.1) ─────────────────────────────────────────────────────


class FakeRunner:
    def __init__(self, fail: bool = False) -> None:
        self.ticks: list[datetime] = []
        self.states: list[State] = []
        self.fail = fail

    def tick(self, now: datetime) -> list[object]:
        self.ticks.append(now)
        if self.fail:
            raise RuntimeError("실행기 고장")
        return []

    def on_state(self, state: State, now: datetime) -> list[object]:
        self.states.append(state)
        return []


def test_runner_is_ticked_and_told_about_transitions_but_not_startup() -> None:
    rig = Rig(kst(2026, 9, 28, 7, 59))
    runner = FakeRunner()
    rig.sched.runner = runner  # type: ignore[assignment]
    rig.run_until(kst(2026, 9, 28, 8, 46), every=timedelta(minutes=1))
    assert runner.states == [State.PRE_DAY, State.DAY]  # 기동 때 IDLE 은 알리지 않는다
    assert len(runner.ticks) == 47


def test_runner_failure_is_isolated() -> None:
    rig = Rig(kst(2026, 9, 28, 10, 0))
    rig.sched.runner = FakeRunner(fail=True)  # type: ignore[assignment]
    rig.sched.step()
    assert [e.state for e in rig.events()] == [State.DAY]
    assert "job_tick_failed" in rig.health.kinds()


def test_pg_session_log_rows_have_gx_columns_and_digest() -> None:
    from kbj.services.scheduler import service as svc

    assert "ON CONFLICT (ts, digest) DO NOTHING" in svc._INSERT_SESSION  # pyright: ignore[reportPrivateUsage]

    class Cur:
        def __init__(self) -> None:
            self.rows: list[tuple[object, ...]] = []

        def __enter__(self) -> Cur:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def executemany(self, q: str, rows: list[tuple[object, ...]]) -> None:
            self.rows += rows

    class Conn:
        def __init__(self) -> None:
            self.cur = Cur()

        def __enter__(self) -> Conn:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def cursor(self) -> Cur:
            return self.cur

    conn = Conn()
    store = svc.PgSessionLogStore(lambda: conn)  # type: ignore[arg-type, return-value]
    from kbj.core.calendar import SessionInfo

    rec = SessionLogRecord.transition(
        kst(2026, 9, 28, 8, 45), SessionInfo(State.DAY, date(2026, 9, 28), "day"), State.PRE_DAY
    )
    store.write_session_log([rec, rec])
    a, b = conn.cur.rows
    assert a[:7] == b[:7] and a[8] == b[8]  # 같은 전이 → 같은 digest(두 번 써도 한 줄)
    assert a[3:7] == ("scheduler", "transition", "DAY", "PRE_DAY")
    assert isinstance(a[8], bytes) and len(a[8]) == 32

"""서비스 공통 — Redis 계약(services/bus.py)·실행 도구(services/runtime.py)·healthcheck.

fakeredis 만 쓴다. 채널·키 이름은 서비스끼리의 계약이라 값을 그대로 고정한다.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import pytest

from core.calendar import SessionInfo, State, TradingCalendar
from services import bus, healthcheck
from services.auth.health import HealthEvent
from services.recorder.envelope import RawEnvelope
from services.runtime import (
    Backoff,
    Heartbeater,
    ServiceHealthSink,
    heartbeat_age,
    log_event,
    tagger_for,
)

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
T0 = datetime(2026, 9, 28, 1, 0, tzinfo=UTC)  # 10:00 KST 월 — DAY


def _redis() -> tuple[fakeredis.FakeServer, fakeredis.FakeRedis]:
    server = fakeredis.FakeServer()
    return server, fakeredis.FakeRedis(server=server)


def test_channel_and_key_names_are_the_contract() -> None:
    assert (bus.WS_RAW, bus.REST_RAW, bus.TICKS_FUT, bus.TICKS_OPT) == (
        "ws.raw",
        "rest.raw",
        "ticks.fut",
        "ticks.opt",
    )
    assert (bus.SESSION_STATE, bus.SESSION_EVENTS) == ("session:state", "session.events")
    assert bus.RAW_CHANNELS == ("ws.raw", "rest.raw")
    assert bus.heartbeat_key("poller") == "health:heartbeat:poller"
    names = [
        bus.WS_RAW,
        bus.REST_RAW,
        bus.TICKS_FUT,
        bus.TICKS_OPT,
        bus.SESSION_STATE,
        bus.SESSION_EVENTS,
        bus.MASTER_KEY,
        bus.MASTER_SHA_KEY,
        bus.CHAIN_CONTEXT_KEY,
    ]
    assert len(set(names)) == len(names)


def test_session_state_message_is_utc_json() -> None:
    at = datetime(2026, 9, 28, 21, 0, tzinfo=KST)
    msg = bus.SessionState.of(SessionInfo(State.NIGHT, date(2026, 9, 29), "night"), at)
    obj = json.loads(msg.model_dump_json())
    assert obj == {
        "state": "NIGHT",
        "trade_date": "2026-09-29",
        "session": "night",
        "at": "2026-09-28T12:00:00Z",
    }
    assert bus.SessionState.model_validate_json(msg.model_dump_json()) == msg


@pytest.mark.parametrize("payload", ["0|H0IFCNT0|001|A01612^093000", {"rt_cd": "0", "a": [1]}])
def test_envelopes_round_trip_through_the_channel_codec(payload: str | dict[str, Any]) -> None:
    env = RawEnvelope(
        received_at=datetime(2026, 9, 28, 10, 0, 0, 123456, tzinfo=KST),
        source="kis_ws",
        tr_id="H0IFCNT0",
        key="A01612",
        payload=payload,
        trade_date=date(2026, 9, 28),
        session="day",
    )
    back = bus.decode_envelope(bus.encode_envelope(env).encode())
    assert back == env and back.received_at.utcoffset() == timedelta(0)


def test_bad_envelope_json_is_a_validation_error() -> None:
    with pytest.raises(ValueError):
        bus.decode_envelope(b'{"source":"nope"}')


def test_publish_reports_receivers_or_none_when_redis_is_down() -> None:
    server, r = _redis()
    assert bus.publish_or_none(r, bus.WS_RAW, "x") == 0  # 받는 쪽 없음 → 호출자가 직접 저장
    ps = r.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(bus.WS_RAW)
    assert bus.publish_or_none(r, bus.WS_RAW, "x") == 1
    server.connected = False
    assert bus.publish_or_none(r, bus.WS_RAW, "x") is None


# ── 하트비트·healthcheck ──


class Now:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def test_heartbeat_is_throttled_and_expires() -> None:
    _, r = _redis()
    now = Now(T0)
    hb = Heartbeater(r, "poller", ttl_s=60, every_s=10, now=now)
    assert hb.beat(state="DAY") is True
    assert hb.beat() is False  # 10초 안
    now.t = T0 + timedelta(seconds=11)
    assert hb.beat() is True
    ttl = r.ttl(bus.heartbeat_key("poller"))
    assert isinstance(ttl, int) and 0 < ttl <= 60
    raw = r.get(bus.heartbeat_key("poller"))
    assert isinstance(raw, bytes)
    obj = json.loads(raw)
    assert obj["service"] == "poller" and obj["at"] == "2026-09-28T01:00:11Z"
    assert heartbeat_age(r, "poller", T0 + timedelta(seconds=41)) == 30.0


def test_heartbeat_failure_is_logged_once_and_never_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, r = _redis()
    server.connected = False
    now = Now(T0)
    hb = Heartbeater(r, "recorder", every_s=1, ttl_s=5, now=now)
    with caplog.at_level(logging.WARNING):
        assert hb.beat() is False
        now.t += timedelta(seconds=2)
        assert hb.beat() is False
    events = [json.loads(x.getMessage()) for x in caplog.records]
    assert [e["event"] for e in events] == ["heartbeat_failed"]
    assert events[0]["service"] == "recorder" and "trade_date" in events[0]


def test_healthcheck_exit_codes() -> None:
    server, r = _redis()
    assert healthcheck.main(["poller"], redis=r) == 1  # 아직 없다
    Heartbeater(r, "poller").beat()
    assert healthcheck.main(["poller"], redis=r) == 0
    assert healthcheck.main(["poller", "abc"], redis=r) == 2
    assert healthcheck.main([], redis=r) == 2
    old = Now(datetime.now(tz=UTC) - timedelta(seconds=120))
    Heartbeater(r, "scheduler", ttl_s=600, now=old).beat()
    assert healthcheck.main(["scheduler"], redis=r) == 1  # 오래됐다
    assert healthcheck.main(["scheduler", "300"], redis=r) == 0
    server.connected = False
    assert healthcheck.main(["poller"], redis=r) == 1


def test_healthcheck_without_redis_url_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.chdir("/")  # .env 를 읽지 않게
    assert healthcheck.main(["poller"]) == 2
    monkeypatch.setenv("KIS_ENV", "not-a-valid-env")  # 설정 오류도 조용히 2
    assert healthcheck.main(["poller"]) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


# ── 로그·태그·health ──


def test_log_event_is_one_json_line_with_trade_date_and_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("services.test")
    with caplog.at_level(logging.INFO, logger="services.test"):
        log_event(logger, logging.INFO, "poller", "started", (date(2026, 9, 28), "day"), n=3)
        log_event(logger, logging.INFO, "poller", "idle")
    a, b = (json.loads(x.getMessage()) for x in caplog.records)
    assert a == {
        "service": "poller",
        "event": "started",
        "trade_date": "2026-09-28",
        "session": "day",
        "n": 3,
    }
    assert (b["trade_date"], b["session"]) == (None, None)


# KBJ P2: test_tagger_follows_the_session_state_machine 는 kbj tests/unit/runtime/test_runtime.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


class HealthStore:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[Any] = []

    def write_health(self, events: Any, *, tagger: Any = None) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.events.extend((e, tagger(e.at) if tagger else None) for e in events)


def test_service_health_sink_logs_and_stores_and_isolates_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ev = HealthEvent("ws_backoff", "3초 뒤 재연결", T0, "warning", service="ws-gateway")
    store = HealthStore()
    with caplog.at_level(logging.INFO):
        ServiceHealthSink(store, tagger_for(CAL)).emit(ev)
    assert store.events == [(ev, (date(2026, 9, 28), "day"))]
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["service"] == "ws-gateway" and rec["trade_date"] == "2026-09-28"
    bad = HealthStore(fail=True)
    ServiceHealthSink(bad, None).emit(ev)  # 예외가 올라오지 않는다


def test_backoff_doubles_to_the_maximum_and_resets() -> None:
    b = Backoff(1.0, 8.0)
    assert [b.next() for _ in range(5)] == [1.0, 2.0, 4.0, 8.0, 8.0]
    b.reset()
    assert b.next() == 1.0
    with pytest.raises(ValueError):
        Backoff(0, 1)

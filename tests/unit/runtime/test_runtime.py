"""서비스 공통 실행 도구(kbj.services.runtime) — 로그·health·하트비트·healthcheck·백오프·스레드.

승격(GEXLAB, fakeredis 만 — 시험 본문 그대로, 바꾼 것만 적는다):
- `tests/unit/test_services_runtime.py` 13개 중 7개: 하트비트 2·healthcheck 2·log_event 1·
  health sink 1·backoff 1. healthcheck 설정 오류 시험의 환경변수 이름은 KBJ 이름으로
  (`REDIS_URL` → `KBJ_REDIS_URL`, `KIS_ENV` → `KBJ_KIS_ENV`). health sink 시험의 태거는
  `tagger_for(CAL)` 대신 같은 값을 돌려주는 고정 태거 — 캘린더(kbj.core.calendar)가 묶음 B
  라서다(B 뒤에 `tagger_for` 와 함께 원래대로 돌린다). 나머지 6개(GEX 채널·봉투·세션 상태
  메시지·태거)는 P7·B 몫.
- `tests/unit/test_auth_service.py` 의 health 이벤트·로그 싱크 시험 3개(health.py 를 S0 가
  승격했다 — 묶음 A 는 이 3개를 다시 옮기지 않는다).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import pytest
from pydantic import SecretStr
from redis import Redis

from kbj.services.runtime import (
    Backoff,
    HealthEvent,
    Heartbeater,
    LogHealthSink,
    MemoryHealthSink,
    ServiceHealthSink,
    connect_redis,
    healthcheck,
    heartbeat_age,
    log_event,
    run_in_thread,
)
from kbj.store import redis_keys as bus

KST = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 9, 28, 1, 0, tzinfo=UTC)  # 10:00 KST 월 — DAY
NOW = datetime(2026, 9, 28, 0, 30, tzinfo=UTC)  # 09:30 KST (GX test_auth_service)


def _redis() -> tuple[fakeredis.FakeServer, fakeredis.FakeRedis]:
    server = fakeredis.FakeServer()
    return server, fakeredis.FakeRedis(server=server)


def _cal_tagger(t: datetime) -> tuple[date | None, str | None]:
    """`tagger_for(CAL)` 가 2026-09-28 10:00 KST(주간장) 에 돌려주는 값과 같다(B 전 대역)."""
    return (date(2026, 9, 28), "day")


# ── 하트비트·healthcheck (승격) ──


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
    monkeypatch.delenv("KBJ_REDIS_URL", raising=False)
    monkeypatch.chdir("/")  # .env 를 읽지 않게
    assert healthcheck.main(["poller"]) == 2
    monkeypatch.setenv("KBJ_KIS_ENV", "not-a-valid-env")  # 설정 오류도 조용히 2
    assert healthcheck.main(["poller"]) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


# ── 로그·health (승격) ──


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
        ServiceHealthSink(store, _cal_tagger).emit(ev)
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


# ── health 이벤트 (승격 — GX test_auth_service.py) ──


def test_health_event_is_utc_and_bounded() -> None:
    kst = datetime(2026, 9, 28, 9, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    ev = HealthEvent("token_refreshed", "x" * 1000, kst)
    assert ev.at.tzinfo is UTC and ev.at == kst == NOW
    assert len(ev.detail) == 300
    with pytest.raises(ValueError, match="naive"):
        HealthEvent("k", "d", datetime(2026, 9, 28))  # noqa: DTZ001


def test_log_sink_writes_one_json_line(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.auth.health")

    def tagger(t: datetime) -> tuple[date | None, str | None]:
        return date(2026, 9, 28), "day"

    with caplog.at_level(logging.INFO, logger="test.auth.health"):
        ev = HealthEvent("token_expiring", "남은 5분", NOW, "critical")
        LogHealthSink(logger, tagger).emit(ev)
    (r,) = caplog.records
    assert r.levelno == logging.CRITICAL
    body = json.loads(r.getMessage())
    assert body == {
        "service": "auth",
        "kind": "token_expiring",
        "severity": "critical",
        "at": "2026-09-28T00:30:00+00:00",
        "detail": "남은 5분",
        "trade_date": "2026-09-28",
        "session": "day",
    }


def test_log_sink_survives_tagger_failure(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.auth.health2")

    def bad(t: datetime) -> tuple[date | None, str | None]:
        raise KeyError("calendar")

    with caplog.at_level(logging.INFO, logger="test.auth.health2"):
        LogHealthSink(logger, bad).emit(HealthEvent("token_refreshed", "ok", NOW))
    msgs = [r.getMessage() for r in caplog.records]
    assert any("KeyError" in m for m in msgs)
    body = json.loads(msgs[-1])
    assert (body["trade_date"], body["session"]) == (None, None)
    assert body["kind"] == "token_refreshed"


# ── 새로 ──

# 합성 JWT 형태 — 조각을 이어 만든다(공개 안전 검사가 소스의 토큰 형태를 잡는다)
JWT = ".".join(["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiJ0ZXN0In0", "c2lnbmF0dXJl" * 2])


def test_secrets_never_reach_logs_events_or_heartbeats(caplog: pytest.LogCaptureFixture) -> None:
    """만드는 쪽이 실수로 토큰을 넣어도 로그·health·하트비트에는 가린 값만(절대 규칙 5)."""
    logger = logging.getLogger("test.runtime.mask")
    with caplog.at_level(logging.INFO, logger="test.runtime.mask"):
        log_event(logger, logging.INFO, "auth", "issued", note=f"Bearer {JWT}", n=1)
        LogHealthSink(logger).emit(HealthEvent("token_refreshed", f"token={JWT}", NOW))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert JWT not in text and "***" in text
    sink = MemoryHealthSink()
    sink.emit(HealthEvent("x", f"access_token: {JWT}", NOW))
    assert JWT not in sink.events[0].detail and sink.kinds() == ["x"] and len(sink.of("x")) == 1
    _, r = _redis()
    Heartbeater(r, "auth", now=Now(NOW)).beat(token_state="ok", leak=f"Bearer {JWT}")
    raw = r.get(bus.heartbeat_key("auth"))
    assert isinstance(raw, bytes) and JWT.encode() not in raw


def test_heartbeat_age_of_a_missing_key_and_bad_ttl() -> None:
    _, r = _redis()
    assert heartbeat_age(r, "nobody", NOW) is None
    with pytest.raises(ValueError):
        Heartbeater(r, "x", ttl_s=10, every_s=10)
    r.set(bus.heartbeat_key("broken"), b"{not json")
    assert healthcheck.check(r, "broken", now=Now(NOW)) is False  # 형식 오류 → 건강하지 않음


def test_run_in_thread_delivers_results_and_exceptions() -> None:
    gate = threading.Event()

    def slow() -> int:
        gate.wait(5)
        return 7

    fut = run_in_thread(slow, name="t")
    assert not fut.done()
    gate.set()
    assert fut.result(timeout=5) == 7

    def boom() -> int:
        raise RuntimeError("실패를 삼키지 않는다")

    with pytest.raises(RuntimeError, match="삼키지"):
        run_in_thread(boom).result(timeout=5)


def test_connect_redis_takes_a_secret_url() -> None:
    url = SecretStr("redis://:fake-pass-0123@127.0.0.1:6399/0")
    client = connect_redis(url)
    assert isinstance(client, Redis)
    assert "fake-pass-0123" not in repr(client)  # 접속만 만들고(지연 연결) 비밀번호는 보이지 않는다
    kw = client.connection_pool.connection_kwargs
    assert kw["socket_timeout"] == 5.0 and kw["socket_connect_timeout"] == 5.0
    client.close()

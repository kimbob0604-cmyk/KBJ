"""auth 서비스 — 유일한 KIS 발급자 (설계 §3, ADR 0001 U1·ADR 0004).

GEXLAB `tests/unit/test_auth_service.py` 52개를 옮겼다. 시험 본문은 그대로이고 바꾼 것은:
- import 경로(`kbj.services.auth.*`, `kbj.data.private.kis.token`, `kbj.services.runtime`,
  `kbj.store.redis_keys`)와 로거 이름(`kbj.services.auth.service`), 진입점 모듈 이름
  (`kbj.services.auth.__main__`).
- `settings()` 는 KBJ 설정에 GX 이름 `kis_base` 를 더한
  `GxSettings`(`tests/unit/kis/conftest.py`)이고 `service="auth"` 를 준다 — 발급자는 auth
  프로세스에서만 만들어진다(ADR 0004 런타임 가드). 발급자를 바로 만드는 시험은 이 폴더의 conftest 가
  `KBJ_SERVICE=auth` 를 둔다.
- `reader` 는 `kbj.data.private.kis.token.reader`(설정 대신 자격을 받아도 된다 — 설정을 주면 자격을
  읽는다).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import httpx
import pytest
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError

from kbj.config.settings import Settings
from kbj.data.private.kis.token import (
    ISSUE_MIN_GAP,
    CachedTokenProvider,
    IssuedToken,
    RedisTokenCache,
    TokenIssueError,
    TokenIssueThrottled,
    TokenRecord,
    TokenUnavailable,
    reader,
    token_owner,
)
from kbj.data.ratelimit import Priority, RateLimitTimeout, Reason, limiter_key
from kbj.services.auth.issuer import TOKEN_PATH
from kbj.services.auth.service import (
    APPROVAL_PATH,
    TOKEN_KEY,
    WS_KEY_ASSUMED_LIFE,
    WS_KEY_KEY,
    AuthService,
    AuthStatus,
    Credential,
    KisApprovalKeyIssuer,
    build_auth_service,
    calendar_tagger,
    heartbeat_status,
    hide_cut_tail,
    redact,
    serve,
)
from kbj.services.runtime import HealthEvent, LogHealthSink, MemoryHealthSink
from tests.unit.kis.conftest import GxSettings

APP_KEY = "PSappKEY0123456789abcdef"
APP_SECRET = "SECRETvalue9876543210zyx"
BASE = "https://kis.example"
OWNER = token_owner(BASE, APP_KEY)
NOW = datetime(2026, 9, 28, 0, 30, tzinfo=UTC)  # 09:30 KST
GAP = ISSUE_MIN_GAP


class Clock:
    def __init__(self, t: datetime = NOW) -> None:
        self.t = t

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> datetime:
        self.t += timedelta(seconds=seconds)
        return self.t


class FakeIssuer:
    def __init__(self, clock: Clock, life: timedelta = timedelta(hours=24), prefix: str = "tok"):
        self.clock = clock
        self.life = life
        self.prefix = prefix
        self.calls: list[datetime] = []
        self.fail: Exception | None = None
        self.during: Callable[[], None] | None = None  # 발급 도중(자리는 잡았고 저장 전)에 실행

    def issue(self) -> IssuedToken:
        self.calls.append(self.clock.now())
        if self.during is not None:
            self.during()
        if self.fail is not None:
            raise self.fail
        t = self.clock.now()
        value = SecretStr(f"{self.prefix}-{len(self.calls)}-{id(self)}")
        return IssuedToken(value, t + self.life, t)


def cache_on(server: fakeredis.FakeServer, key: str = TOKEN_KEY) -> RedisTokenCache:
    return RedisTokenCache(fakeredis.FakeRedis(server=server), key)


def service(
    server: fakeredis.FakeServer,
    clock: Clock,
    issuer: FakeIssuer,
    sink: MemoryHealthSink | None = None,
    **kw: Any,
) -> tuple[AuthService, MemoryHealthSink]:
    sink = sink if sink is not None else MemoryHealthSink()
    kw.setdefault("now", clock.now)
    svc = AuthService(
        [Credential("token", cache_on(server), issuer, OWNER)],
        sink,
        secrets=[APP_KEY, APP_SECRET],
        **kw,
    )
    return svc, sink


def seed(server: fakeredis.FakeServer, value: str, left: timedelta, owner: str = OWNER) -> None:
    rec = TokenRecord(
        access_token=SecretStr(value),
        expires_at=NOW + left,
        issued_at=NOW + left - timedelta(hours=24),
        owner=owner,
    )
    cache_on(server).store(rec, NOW)


def cached(server: fakeredis.FakeServer, key: str = TOKEN_KEY) -> TokenRecord | None:
    return cache_on(server, key).load()


# ---- 갱신 시점 ---------------------------------------------------------------------------------


def test_fresh_token_is_left_alone() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    seed(server, "old", timedelta(hours=5))
    iss = FakeIssuer(clock)
    svc, sink = service(server, clock, iss)
    st = svc.step(NOW)
    assert st["token"].action == "fresh"
    assert st["token"].expires_at == NOW + timedelta(hours=5)
    assert st.ok
    assert iss.calls == [] and sink.events == []


@pytest.mark.parametrize(
    ("left", "action"), [(timedelta(minutes=59), "refreshed"), (timedelta(minutes=61), "fresh")]
)
def test_refresh_inside_sixty_minute_window(left: timedelta, action: str) -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    seed(server, "old", left)
    iss = FakeIssuer(clock)
    svc, sink = service(server, clock, iss)
    st = svc.step(NOW)
    assert st["token"].action == action
    rec = cached(server)
    assert rec is not None
    if action == "refreshed":
        assert len(iss.calls) == 1
        assert rec.token.startswith("tok-1") and rec.expires_at == NOW + timedelta(hours=24)
        assert rec.owner == OWNER
        assert st["token"].expires_at == rec.expires_at
        (ev,) = sink.events
        assert (ev.kind, ev.severity, ev.at) == ("token_refreshed", "info", NOW)
        assert "09-29 09:30:00 KST" in ev.detail and "24시간" in ev.detail
        assert rec.token not in ev.detail
    else:
        assert iss.calls == [] and rec.token == "old" and sink.events == []


def test_empty_cache_issues_once_then_fresh() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    svc, _ = service(server, clock, iss)
    assert svc.step(NOW)["token"].action == "refreshed"
    for s in (30, 30, 3600):
        assert svc.step(clock.advance(s))["token"].action == "fresh"
    assert len(iss.calls) == 1


def test_other_app_key_record_is_replaced() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    seed(server, "other", timedelta(hours=10), owner=token_owner(BASE, "OTHERKEY"))
    iss = FakeIssuer(clock)
    svc, _ = service(server, clock, iss)
    assert svc.step(NOW)["token"].action == "refreshed"
    rec = cached(server)
    assert rec is not None and rec.owner == OWNER and rec.token != "other"


# ---- 실패·재시도 간격 --------------------------------------------------------------------------


def test_failure_retries_no_sooner_than_61s() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    seed(server, "old", timedelta(minutes=50))
    iss = FakeIssuer(clock)
    iss.fail = TokenIssueError("토큰 발급 실패 HTTP 500: EGW00500")
    svc, sink = service(server, clock, iss)
    st = svc.step(NOW)["token"]
    assert (st.action, st.next_attempt_at, st.expires_at) == (
        "failed",
        NOW + GAP,
        NOW + timedelta(minutes=50),
    )
    assert st.live and not st.expiring  # 기존 토큰은 살아 있다 — 서비스는 계속 돈다
    (ev,) = sink.events
    assert ev.kind == "token_refresh_failed" and "EGW00500" in ev.detail
    for t in (30, 60, 60.999):
        st = svc.step(NOW + timedelta(seconds=t))["token"]
        assert st.action == "waiting" and st.next_attempt_at == NOW + GAP
    assert len(iss.calls) == 1
    iss.fail = None
    clock.t = NOW + GAP
    assert svc.step(clock.t)["token"].action == "refreshed"
    assert iss.calls == [NOW, NOW + GAP]


def test_local_gap_holds_even_if_redis_is_flushed() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    iss.fail = TokenIssueError("x")
    svc, _ = service(server, clock, iss)
    svc.step(NOW)
    fakeredis.FakeRedis(server=server).flushall()  # 공유 간격 기록이 사라져도
    assert svc.step(clock.advance(30))["token"].action == "waiting"
    assert len(iss.calls) == 1


def test_restarted_instance_respects_shared_gap() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    first = FakeIssuer(clock)
    first.fail = TokenIssueError("x")
    service(server, clock, first)[0].step(NOW)
    second = FakeIssuer(clock)
    svc, _ = service(server, clock, second)  # 재기동한 auth
    st = svc.step(clock.advance(30))["token"]
    assert (st.action, st.next_attempt_at) == ("throttled", NOW + GAP)
    assert second.calls == []
    clock.t = NOW + GAP
    assert svc.step(clock.t)["token"].action == "refreshed"
    assert second.calls == [NOW + GAP]


def test_kis_throttle_code_counts_as_an_attempt() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    iss.fail = TokenIssueThrottled(None)
    svc, sink = service(server, clock, iss)
    st = svc.step(NOW)["token"]
    assert (st.action, st.next_attempt_at) == ("failed", NOW + GAP)
    assert "EGW00133" in sink.of("token_refresh_failed")[0].detail
    assert svc.step(clock.advance(45))["token"].action == "waiting"
    assert len(iss.calls) == 1


def test_persistent_failure_raises_expiring() -> None:
    """만료 70분 전부터 계속 실패: 시도는 61초 간격 이상, 경고는 남은 10분부터 1분에 한 번 이하."""
    server, clock = fakeredis.FakeServer(), Clock()
    expiry = NOW + timedelta(minutes=70)
    seed(server, "old", timedelta(minutes=70))
    iss = FakeIssuer(clock)
    iss.fail = TokenIssueError("HTTP 500")
    svc, sink = service(server, clock, iss)
    statuses = [svc.step(NOW)]
    while clock.t < expiry + timedelta(minutes=5):
        statuses.append(svc.step(clock.advance(30)))

    gaps = [b - a for a, b in pairwise(iss.calls)]
    assert iss.calls and all(g >= GAP for g in gaps)
    assert iss.calls[0] == NOW + timedelta(minutes=10)  # 남은 시간이 60분이 된 첫 step
    assert len(sink.of("token_refresh_failed")) == len(iss.calls)

    expiring = sink.of("token_expiring")
    assert expiring and expiring[0].at >= expiry - timedelta(minutes=10)
    assert expiring[0].at < expiry - timedelta(minutes=9)
    assert all(b.at - a.at >= GAP for a, b in pairwise(expiring))
    assert all(e.severity == "critical" for e in expiring)
    assert "남은" in expiring[0].detail and "다음 시도" in expiring[0].detail

    for s in statuses:
        t = s["token"]
        left = expiry - s.at
        assert t.expiring == (left < timedelta(minutes=10))
        assert s.ok == (left >= timedelta(minutes=10) and t.live)
    last = statuses[-1]["token"]
    assert not last.live and last.expiring
    assert "살아 있는 값 없음" in expiring[-1].detail


def test_no_token_and_failing_is_expiring_at_once() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    iss.fail = TokenIssueError("HTTP 500")
    svc, sink = service(server, clock, iss)
    st = svc.step(NOW)
    assert not st.ok and st["token"].expiring
    assert sink.kinds() == ["token_refresh_failed", "token_expiring"]
    svc.step(clock.advance(30))  # 같은 경고를 30초마다 되풀이하지 않는다
    assert sink.kinds() == ["token_refresh_failed", "token_expiring"]


# ---- 여러 인스턴스 -----------------------------------------------------------------------------


def test_concurrent_instances_do_not_double_issue() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    ia, ib = FakeIssuer(clock), FakeIssuer(clock)
    a, _ = service(server, clock, ia)
    b, sink_b = service(server, clock, ib)
    seen: list[AuthStatus] = []
    ia.during = lambda: seen.append(b.step(NOW))  # a 가 발급하는 사이 b 가 같은 순간 step
    assert a.step(NOW)["token"].action == "refreshed"
    (st,) = (s["token"] for s in seen)
    assert (st.action, st.next_attempt_at) == ("throttled", NOW + GAP)
    assert sink_b.events == []  # 다른 인스턴스가 발급 중인 것은 경고가 아니다
    for _ in range(4):
        clock.advance(15)
        assert a.step(clock.t)["token"].action == "fresh"
        assert b.step(clock.t)["token"].action == "fresh"
    assert (len(ia.calls), len(ib.calls)) == (1, 0)


def test_concurrent_refresh_in_window_issues_once() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    seed(server, "old", timedelta(minutes=30))
    ia, ib = FakeIssuer(clock), FakeIssuer(clock)
    a, _ = service(server, clock, ia)
    b, _ = service(server, clock, ib)
    assert b.step(NOW)["token"].action == "refreshed"
    assert a.step(NOW)["token"].action == "fresh"  # b 가 방금 넣은 값을 본다
    assert (len(ia.calls), len(ib.calls)) == (0, 1)


def test_failing_instance_blocks_others_for_61s() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    ia, ib = FakeIssuer(clock), FakeIssuer(clock)
    ia.fail = TokenIssueError("HTTP 500")
    a, _ = service(server, clock, ia)
    b, _ = service(server, clock, ib)
    a.step(NOW)
    for t in (1, 30, 60):
        assert b.step(NOW + timedelta(seconds=t))["token"].action == "throttled"
    assert ib.calls == []
    assert b.step(NOW + GAP)["token"].action == "refreshed"
    assert a.step(NOW + GAP)["token"].action == "fresh"
    assert (len(ia.calls), len(ib.calls)) == (1, 1)


def test_throttled_behind_a_failing_issuer_warns_after_a_gap() -> None:
    """다른 발급자가 자리를 계속 잡고 실패하면, 61초를 기다려 준 뒤부터 이쪽도 경고한다."""
    server, clock = fakeredis.FakeServer(), Clock()
    ia, ib = FakeIssuer(clock), FakeIssuer(clock)
    ia.fail = TokenIssueError("HTTP 500")
    a, _ = service(server, clock, ia)
    b, sink_b = service(server, clock, ib)
    a.step(NOW)
    assert b.step(NOW + timedelta(seconds=30))["token"].action == "throttled"
    clock.t = NOW + GAP
    a.step(clock.t)  # a 가 먼저 다시 자리를 잡는다
    assert b.step(clock.t + timedelta(seconds=1))["token"].action == "throttled"
    assert sink_b.events == []  # 막힌 지 31초 — 아직 기다린다
    clock.t = NOW + 2 * GAP
    a.step(clock.t)
    st = b.step(clock.t + timedelta(seconds=1))["token"]
    assert st.action == "throttled" and st.expiring
    assert sink_b.kinds() == ["token_expiring"]
    assert ib.calls == []


# ---- 격리 -------------------------------------------------------------------------------------


def test_redis_down_never_issues() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    svc, sink = service(server, clock, iss)
    server.connected = False
    st = svc.step(NOW)["token"]
    assert st.action == "error" and "ConnectionError" in st.detail
    svc.step(clock.advance(30))
    assert sink.kinds() == ["token_cache_error"]  # 61초에 한 번
    svc.step(clock.advance(31))
    assert sink.kinds() == ["token_cache_error", "token_cache_error"]
    assert iss.calls == []
    server.connected = True
    assert svc.step(clock.advance(1))["token"].action == "refreshed"


class LosingCache(RedisTokenCache):
    def store(self, rec: TokenRecord, now: datetime) -> None:
        raise RedisConnectionError("gone")


def test_store_failure_still_waits_61s() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    sink = MemoryHealthSink()
    cache = LosingCache(fakeredis.FakeRedis(server=server))
    svc = AuthService([Credential("token", cache, iss, OWNER)], sink, now=clock.now)
    st = svc.step(NOW)["token"]
    assert st.action == "error" and "잃었다" in st.detail
    assert st.next_attempt_at == NOW + GAP
    assert svc.step(clock.advance(30))["token"].action == "waiting"
    assert len(iss.calls) == 1


def test_unexpected_issuer_error_keeps_only_its_type() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    iss.fail = RuntimeError(f"boom {APP_KEY} {APP_SECRET}")
    svc, sink = service(server, clock, iss)
    st = svc.step(NOW)["token"]
    assert st.action == "failed" and st.next_attempt_at == NOW + GAP
    (ev,) = sink.of("token_refresh_failed")
    assert "RuntimeError" in ev.detail and "boom" not in ev.detail


def test_event_details_are_redacted() -> None:
    """발급자가 메시지를 가리지 않아도(가짜 발급자) 서비스가 한 번 더 가린다."""
    server, clock = fakeredis.FakeServer(), Clock()
    seed(server, "LIVETOKENVALUE", timedelta(minutes=30))
    iss = FakeIssuer(clock)
    iss.fail = TokenIssueError(f"echo {APP_KEY} / {APP_SECRET} / LIVETOKENVALUE")
    svc, sink = service(server, clock, iss)
    st = svc.step(NOW)["token"]
    text = " ".join(e.detail for e in sink.events) + st.detail + repr(st)
    assert "echo" in text and "***" in text
    for s in (APP_KEY, APP_SECRET, "LIVETOKENVALUE"):
        assert s not in text


class BrokenSink:
    def __init__(self) -> None:
        self.calls = 0

    def emit(self, event: HealthEvent) -> None:
        self.calls += 1
        raise RuntimeError("db down")


def test_sink_failure_does_not_block_refresh(caplog: pytest.LogCaptureFixture) -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    sink = BrokenSink()
    svc = AuthService([Credential("token", cache_on(server), iss, OWNER)], sink, now=clock.now)
    with caplog.at_level(logging.ERROR, logger="kbj.services.auth.service"):
        assert svc.step(NOW)["token"].action == "refreshed"
    assert sink.calls == 1
    assert cached(server) is not None
    assert "RuntimeError" in caplog.text and "db down" not in caplog.text


def test_guards() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    cred = Credential("token", cache_on(server), iss, OWNER)
    sink = MemoryHealthSink()
    with pytest.raises(ValueError, match="61초"):
        AuthService([cred], sink, retry_gap=timedelta(seconds=30))
    with pytest.raises(ValueError, match="서로 달라야"):
        AuthService([cred, cred], sink)
    with pytest.raises(ValueError, match="expiring_margin"):
        AuthService([cred], sink, expiring_margin=timedelta(minutes=90))
    with pytest.raises(ValueError, match="무기한"):
        AuthService([cred], sink, permit_timeout=0)
    with pytest.raises(ValueError, match="naive"):
        AuthService([cred], sink).step(datetime(2026, 9, 28, 9, 30))  # noqa: DTZ001


# ---- health 이벤트 ------------------------------------------------------------------------------


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


# ---- KIS 발급자 경로 (httpx.MockTransport) ----------------------------------------------------


def settings() -> GxSettings:
    return GxSettings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        kis_app_key=SecretStr(APP_KEY),
        kis_app_secret=SecretStr(APP_SECRET),
        service="auth",
    )


def kis_http(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(base_url=settings().kis_base, transport=httpx.MockTransport(handler))


def test_build_requires_app_key() -> None:
    with pytest.raises(TokenUnavailable):
        build_auth_service(
            Settings(_env_file=None),  # pyright: ignore[reportCallIssue]
            fakeredis.FakeRedis(),
            httpx.Client(),
            MemoryHealthSink(),
        )


def test_kis_issuer_path_fills_redis_for_readers() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert req.url.path == TOKEN_PATH
        return httpx.Response(
            200,
            json={
                "access_token": "eyJKISACCESSTOKEN",
                "token_type": "Bearer",
                "expires_in": 86400,
                "access_token_token_expired": "2026-09-29 09:30:00",
            },
        )

    r = fakeredis.FakeRedis()
    clock = Clock()
    sink = MemoryHealthSink()
    svc = build_auth_service(settings(), r, kis_http(handler), sink, ws_key=False, now=clock.now)
    assert svc.step(NOW)["token"].action == "refreshed"
    assert svc.step(clock.advance(30))["token"].action == "fresh"
    assert len(seen) == 1
    body = json.loads(seen[0].content)
    assert body == {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    owner = token_owner(settings().kis_base, APP_KEY)
    reader = CachedTokenProvider(RedisTokenCache(r, TOKEN_KEY), owner, now=clock.now)  # 읽기만
    assert reader.get() == "eyJKISACCESSTOKEN"
    assert sink.kinds() == ["token_refreshed"]


def test_kis_throttle_and_errors_via_http(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    replies = [
        httpx.Response(403, json={"error_code": "EGW00133", "error_description": "1분당 1회"}),
        httpx.Response(
            403,
            json={"error_code": "EGW00103", "error_description": f"bad {APP_KEY} {APP_SECRET}"},
        ),
        httpx.Response(200, json={"access_token": "eyJSECRETTOKEN", "expires_in": 86400}),
    ]
    calls: list[datetime] = []
    clock = Clock()

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(clock.now())
        return replies[len(calls) - 1]

    logger = logging.getLogger("test.auth.kis")
    svc = build_auth_service(
        settings(),
        fakeredis.FakeRedis(),
        kis_http(handler),
        LogHealthSink(logger),
        ws_key=False,
        now=clock.now,
    )
    stop = ClockedStop(clock)
    statuses: list[AuthStatus] = []

    def on_status(st: AuthStatus) -> None:
        statuses.append(st)
        if len(calls) == 3:
            stop.set()

    with caplog.at_level(logging.DEBUG):
        svc.run_forever(stop, on_status=on_status)
    assert calls == [NOW, NOW + GAP, NOW + 2 * GAP]  # 실패 뒤 61초마다
    assert statuses[-1]["token"].action == "refreshed"
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == "test.auth.kis"]
    kinds = [e["kind"] for e in events]
    # 첫 EGW00133 은 61초 안에 풀려 경고만, 다음 시도가 EGW00103 으로 실패하면 critical
    assert kinds == [
        "token_refresh_failed",
        "token_refresh_failed",
        "token_expiring",
        "token_refreshed",
    ]
    assert "EGW00133" in events[0]["detail"] and "EGW00103" in events[1]["detail"]
    assert (events[2]["at"], events[2]["severity"]) == ((NOW + GAP).isoformat(), "critical")
    out = capsys.readouterr()
    text = caplog.text + out.out + out.err + "".join(repr(s) for s in statuses)
    for s in (APP_KEY, APP_SECRET, "eyJSECRETTOKEN"):
        assert s not in text


# ---- 웹소켓 접속키 (POST /oauth2/Approval — 미실측, KIS 공식 샘플 형식) ------------------------


def approval_issuer(
    handler: Callable[[httpx.Request], httpx.Response], life: timedelta = WS_KEY_ASSUMED_LIFE
) -> KisApprovalKeyIssuer:
    return KisApprovalKeyIssuer(
        kis_http(handler), SecretStr(APP_KEY), SecretStr(APP_SECRET), life=life, now=lambda: NOW
    )


def test_approval_issuer_posts_secretkey_and_assumes_life() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"approval_key": "a1b2-c3d4-WSKEY"})

    got = approval_issuer(handler).issue()
    assert got.access_token.get_secret_value() == "a1b2-c3d4-WSKEY"
    assert (got.issued_at, got.expires_at) == (NOW, NOW + timedelta(hours=12))
    (req,) = seen
    assert (req.method, req.url.path) == ("POST", APPROVAL_PATH)
    body = json.loads(req.content)
    assert body == {"grant_type": "client_credentials", "appkey": APP_KEY, "secretkey": APP_SECRET}
    assert "a1b2-c3d4-WSKEY" not in repr(got)


def test_approval_issuer_errors() -> None:
    throttled = {"error_code": "EGW00133", "error_description": "1분당 1회"}
    with pytest.raises(TokenIssueThrottled):
        approval_issuer(lambda _: httpx.Response(403, json=throttled)).issue()

    echo = {"msg_cd": "EGW00103", "msg1": f"invalid {APP_KEY} {APP_SECRET}"}
    with pytest.raises(TokenIssueError) as ei:
        approval_issuer(lambda _: httpx.Response(403, json=echo)).issue()
    msg = str(ei.value)
    assert "EGW00103" in msg and "403" in msg
    assert APP_KEY not in msg and APP_SECRET not in msg

    for body in ({"approval_key": ""}, {"other": "LEAKYKEY"}, ["LEAKYKEY"]):
        with pytest.raises(TokenIssueError) as ei:
            approval_issuer(lambda _, b=body: httpx.Response(200, json=b)).issue()
        assert "LEAKYKEY" not in str(ei.value)

    with pytest.raises(TokenIssueError, match="HTTP 502"):
        approval_issuer(lambda _: httpx.Response(502, text="<html>bad gateway</html>")).issue()

    def refuse(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {APP_KEY}", request=req)

    with pytest.raises(TokenIssueError, match="ConnectError") as ei:
        approval_issuer(refuse).issue()
    assert APP_KEY not in str(ei.value)
    with pytest.raises(ValueError, match="수명"):
        approval_issuer(refuse, life=timedelta(0))


def straddling(secret: str, inside: int, code: str = "EGW00103") -> dict[str, str]:
    """KIS 오류 본문 — `f"{code} {desc}"` 200자 자르기 선에 비밀값이 걸친다(앞 inside 자만 안쪽)."""
    filler = "x" * (200 - len(code) - 1 - inside)
    return {"error_code": code, "error_description": filler + secret + " tail"}


def test_redact_helpers() -> None:
    assert redact(f"a {APP_KEY} b PS", ["PS", APP_KEY, ""]) == "a *** b ***"  # 긴 값부터
    assert hide_cut_tail("HTTP 403: xxSECRETva", [APP_KEY, APP_SECRET]) == "HTTP 403: xx***"
    assert hide_cut_tail("EGW00103 xxSEC", [APP_SECRET]) == "EGW00103 xx***"
    assert hide_cut_tail("EGW00103 xxSE", [APP_SECRET]) == "EGW00103 xxSE"  # 2자는 우연과 구분 불가
    assert hide_cut_tail("끝 PSappKEY0", [APP_SECRET, APP_KEY]) == "끝 ***"
    assert hide_cut_tail("", [APP_SECRET]) == ""


@pytest.mark.parametrize("inside", [3, 10, 23])
@pytest.mark.parametrize("secret", [APP_KEY, APP_SECRET])
def test_approval_issuer_redacts_before_cutting(secret: str, inside: int) -> None:
    body = straddling(secret, inside)
    with pytest.raises(TokenIssueError) as ei:
        approval_issuer(lambda _: httpx.Response(403, json=body)).issue()
    msg = str(ei.value)
    assert "EGW00103" in msg and "***" in msg
    assert secret[:3] not in msg


def kis_server(
    approval: Callable[[], httpx.Response] | None = None,
) -> tuple[Callable[[httpx.Request], httpx.Response], list[str]]:
    """토큰·접속키 두 경로를 흉내 내는 가짜 KIS. 호출 경로를 기록한다."""
    paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        if req.url.path == TOKEN_PATH:
            n = paths.count(TOKEN_PATH)
            return httpx.Response(200, json={"access_token": f"eyJACCESS{n}", "expires_in": 86400})
        if req.url.path == APPROVAL_PATH:
            if approval is not None:
                return approval()
            return httpx.Response(200, json={"approval_key": f"WSKEY{paths.count(APPROVAL_PATH)}"})
        return httpx.Response(404)

    return handler, paths


def test_service_keeps_both_keys_for_readers() -> None:
    r = fakeredis.FakeRedis()
    clock = Clock()
    handler, paths = kis_server()
    sink = MemoryHealthSink()
    svc = build_auth_service(settings(), r, kis_http(handler), sink, now=clock.now)
    st = svc.step(NOW)
    assert [c.name for c in st.credentials] == ["token", "ws_key"]
    assert st["token"].action == st["ws_key"].action == "refreshed"
    assert st["ws_key"].expires_at == NOW + WS_KEY_ASSUMED_LIFE
    assert sink.kinds() == ["token_refreshed", "ws_key_refreshed"]
    assert reader(r, settings(), now=clock.now).get() == "eyJACCESS1"
    assert reader(r, settings(), "ws_key", now=clock.now).get() == "WSKEY1"
    assert 0 < r.pttl(WS_KEY_KEY) <= 12 * 3600 * 1000
    # 만료 판정은 주입한 시각으로 한다 — 고정 NOW 를 벽시계와 견주지 않는다
    with pytest.raises(TokenUnavailable):
        reader(r, settings(), "ws_key", now=lambda: NOW + WS_KEY_ASSUMED_LIFE).get()

    # 접속키는 11시간 뒤(만료 60분 전) 갱신, 접근토큰은 아직
    clock.t = NOW + timedelta(hours=11) - timedelta(seconds=30)
    assert svc.step(clock.t).credentials[1].action == "fresh"
    clock.advance(30)
    st = svc.step(clock.t)
    assert (st["token"].action, st["ws_key"].action) == ("fresh", "refreshed")
    assert reader(r, settings(), "ws_key", now=clock.now).get() == "WSKEY2"
    assert paths == [TOKEN_PATH, APPROVAL_PATH, APPROVAL_PATH]


def test_ws_key_failure_is_isolated_and_throttled() -> None:
    r = fakeredis.FakeRedis()
    clock = Clock()
    handler, paths = kis_server(lambda: httpx.Response(500, json={"msg1": "internal"}))
    sink = MemoryHealthSink()
    svc = build_auth_service(settings(), r, kis_http(handler), sink, now=clock.now)
    st = svc.step(NOW)
    assert (st["token"].action, st["ws_key"].action) == ("refreshed", "failed")
    assert not st.ok and st["ws_key"].expiring  # 살아 있는 접속키가 없다
    assert sink.kinds() == ["token_refreshed", "ws_key_refresh_failed", "ws_key_expiring"]
    for _ in range(2):
        st = svc.step(clock.advance(30))
        assert (st["token"].action, st["ws_key"].action) == ("fresh", "waiting")
    st = svc.step(clock.advance(1))  # 61초
    assert st["ws_key"].action == "failed"
    assert paths == [TOKEN_PATH, APPROVAL_PATH, APPROVAL_PATH]
    with pytest.raises(TokenUnavailable):
        reader(r, settings(), "ws_key", now=clock.now).get()


def test_ws_key_can_be_switched_off_and_life_is_guarded() -> None:
    handler, paths = kis_server()
    r = fakeredis.FakeRedis()
    svc = build_auth_service(settings(), r, kis_http(handler), MemoryHealthSink(), ws_key=False)
    assert [c.name for c in svc.step(NOW).credentials] == ["token"]
    assert paths == [TOKEN_PATH]
    with pytest.raises(ValueError, match="60분"):
        build_auth_service(
            settings(), r, kis_http(handler), MemoryHealthSink(), ws_key_life=timedelta(minutes=60)
        )


class RecordingLimiter:
    """허가 요청을 기록하는 가짜 레이트리미터. log 를 가짜 KIS 와 함께 쓰면 순서를 본다."""

    def __init__(self, log: list[str], fail: Exception | None = None) -> None:
        self.log = log
        self.fail = fail
        self.calls: list[tuple[Priority, str, float | None]] = []

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        self.calls.append((priority, tr_id, timeout))
        self.log.append(f"permit {tr_id}")
        if self.fail is not None:
            raise self.fail

    def on_rate_limited(self) -> float:
        return 4.0


def test_oauth_calls_take_a_p0_permit_first() -> None:
    """발급 요청도 앱키당 레이트리미터를 지난다(설계 §2) — P0, 발급할 때만."""
    handler, paths = kis_server()
    log: list[str] = []

    def logged(req: httpx.Request) -> httpx.Response:
        log.append(f"post {req.url.path}")
        return handler(req)

    lim = RecordingLimiter(log)
    clock = Clock()
    svc = build_auth_service(
        settings(),
        fakeredis.FakeRedis(),
        kis_http(logged),
        MemoryHealthSink(),
        limiter=lim,
        now=clock.now,
    )
    assert svc.step(NOW).ok
    assert log == [
        f"permit {TOKEN_PATH}",
        f"post {TOKEN_PATH}",
        f"permit {APPROVAL_PATH}",
        f"post {APPROVAL_PATH}",
    ]
    assert [(p, t) for p, t, _ in lim.calls] == [
        (Priority.P0, TOKEN_PATH),
        (Priority.P0, APPROVAL_PATH),
    ]
    assert all(t is not None and 0 < t <= 10 for _, _, t in lim.calls)  # 무기한 대기는 없다
    svc.step(clock.advance(30))  # 갱신이 필요 없으면 허가도 받지 않는다
    assert len(lim.calls) == 2 and paths == [TOKEN_PATH, APPROVAL_PATH]


def test_limiter_timeout_is_a_failed_attempt_without_calling_kis() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    lim = RecordingLimiter([], fail=RateLimitTimeout(Priority.P0, TOKEN_PATH, Reason.PRIORITY))
    svc, sink = service(server, clock, iss, limiter=lim)
    st = svc.step(NOW)["token"]
    assert (st.action, st.next_attempt_at) == ("failed", NOW + GAP)
    assert iss.calls == []
    (ev,) = sink.of("token_refresh_failed")
    assert "레이트리미터" in ev.detail
    assert svc.step(clock.advance(30))["token"].action == "waiting"  # 자리는 잡았으니 61초
    assert len(lim.calls) == 1
    lim.fail = None
    assert svc.step(clock.advance(31))["token"].action == "refreshed"
    assert len(iss.calls) == 1


def test_limiter_redis_failure_never_issues() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    lim = RecordingLimiter([], fail=RedisConnectionError("gone"))
    svc, sink = service(server, clock, iss, limiter=lim)
    st = svc.step(NOW)["token"]
    assert st.action == "error" and "레이트리미터" in st.detail and "ConnectionError" in st.detail
    assert iss.calls == [] and sink.kinds() == ["token_cache_error"]


def test_reader_ignores_other_app_key_and_needs_a_key() -> None:
    r = fakeredis.FakeRedis()
    other = token_owner(BASE, "OTHERKEY")
    rec = TokenRecord(
        access_token=SecretStr("foreign"),
        expires_at=NOW + timedelta(hours=5),
        issued_at=NOW,
        owner=other,
    )
    RedisTokenCache(r, WS_KEY_KEY).store(rec, NOW)
    with pytest.raises(TokenUnavailable):
        reader(r, settings(), "ws_key", now=lambda: NOW).get()
    with pytest.raises(TokenUnavailable):
        reader(r, Settings(_env_file=None))  # pyright: ignore[reportCallIssue]


def test_nothing_secret_leaks_from_a_full_cycle(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """성공·실패·경고가 모두 난 뒤에도 로그·표준출력·이벤트·상태에 비밀이 없다."""
    answers = iter(
        [
            httpx.Response(403, json={"error_code": "EGW00103", "error_description": APP_SECRET}),
            httpx.Response(200, json={"approval_key": "WSKEYSECRETVALUE"}),
        ]
    )
    handler, _ = kis_server(lambda: next(answers))
    clock = Clock()
    logger = logging.getLogger("test.auth.cycle")
    svc = build_auth_service(
        settings(), fakeredis.FakeRedis(), kis_http(handler), LogHealthSink(logger), now=clock.now
    )
    with caplog.at_level(logging.DEBUG):
        statuses = [svc.step(NOW), svc.step(clock.advance(61))]
    assert statuses[-1]["ws_key"].action == "refreshed"
    out = capsys.readouterr()
    text = caplog.text + out.out + out.err + repr(statuses)
    assert "ws_key_refresh_failed" in text and "ws_key_refreshed" in text
    for s in (APP_KEY, APP_SECRET, "eyJACCESS1", "WSKEYSECRETVALUE"):
        assert s not in text


@pytest.mark.parametrize("inside", [3, 10, 23])
@pytest.mark.parametrize("secret", [APP_KEY, APP_SECRET])
def test_secret_cut_at_the_error_limit_never_reaches_events(
    secret: str,
    inside: int,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """KIS 가 비밀값을 되돌려 보내 오류 문구 200자 선에 걸쳐도 앞부분 조각이 새지 않는다.

    접근토큰 발급자(`KisTokenIssuer`)는 자른 뒤에 가려 조각이 남는다 — 서비스가 꼬리 조각을 가린다.
    """
    body = straddling(secret, inside)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json=body)

    logger = logging.getLogger("test.auth.cut")
    clock = Clock()
    svc = build_auth_service(
        settings(), fakeredis.FakeRedis(), kis_http(handler), LogHealthSink(logger), now=clock.now
    )
    with caplog.at_level(logging.DEBUG):
        st = svc.step(NOW)
    assert (st["token"].action, st["ws_key"].action) == ("failed", "failed")
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == "test.auth.cut"]
    failed = [e["detail"] for e in events if e["kind"].endswith("_refresh_failed")]
    assert len(failed) == 2 and all("EGW00103" in d and "***" in d for d in failed)
    out = capsys.readouterr()
    text = caplog.text + out.out + out.err + repr(st)
    assert secret[:3] not in text


# ---- 루프·진입점 -------------------------------------------------------------------------------


class ClockedStop(threading.Event):
    """`wait(timeout)` 가 실제로 자지 않고 가짜 시계를 timeout 만큼 민다. limit 번 기다리면 선다."""

    def __init__(self, clock: Clock, limit: int = 1000) -> None:
        super().__init__()
        self.clock = clock
        self.limit = limit
        self.waits: list[float] = []

    def wait(self, timeout: float | None = None) -> bool:
        assert timeout is not None
        self.waits.append(timeout)
        self.clock.advance(timeout)
        if len(self.waits) >= self.limit:
            self.set()
        return self.is_set()


def test_run_forever_retries_61s_after_a_failure() -> None:
    """평소엔 30초마다 step, 실패 뒤엔 다음 시도 시각(61초)에 맞춰 깬다 — 90초로 밀리지 않는다."""
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    iss.fail = TokenIssueError("HTTP 500")
    svc, sink = service(server, clock, iss)
    stop = ClockedStop(clock, limit=5)
    seen: list[AuthStatus] = []

    def on_status(st: AuthStatus) -> None:
        seen.append(st)
        if len(seen) == 3:
            iss.fail = None

    n = svc.run_forever(stop, interval=30.0, on_status=on_status)
    assert n == 5
    assert stop.waits == [30.0, 30.0, 1.0, 30.0, 30.0]
    assert [s.at - NOW for s in seen] == [timedelta(seconds=x) for x in (0, 30, 60, 61, 91)]
    assert [s["token"].action for s in seen] == [
        "failed",
        "waiting",
        "waiting",
        "refreshed",
        "fresh",
    ]
    assert iss.calls == [NOW, NOW + GAP]
    assert sink.kinds()[-1] == "token_refreshed"


def test_run_forever_wakes_for_the_nearest_retry_but_never_spins() -> None:
    """두 자격 중 가까운 다음 시도에 깬다. 지난 시각(캐시 오류 때의 옛 간격)엔 헛돌지 않는다."""
    server, clock = fakeredis.FakeServer(), Clock()
    tok, ws = FakeIssuer(clock), FakeIssuer(clock, prefix="ws")
    tok.fail = TokenIssueError("HTTP 500")
    creds = [
        Credential("token", cache_on(server), tok, OWNER),
        Credential("ws_key", cache_on(server, WS_KEY_KEY), ws, OWNER),
    ]
    svc = AuthService(creds, MemoryHealthSink(), now=clock.now)
    stop = ClockedStop(clock, limit=2)
    svc.run_forever(stop, interval=30.0)
    assert stop.waits == [30.0, 30.0]  # 토큰 재시도는 61초 뒤 — 30초 주기가 먼저
    assert len(ws.calls) == 1

    server.connected = False  # 캐시를 못 읽으면 옛 간격(이미 지난 시각)만 남는다
    clock.advance(120)
    stop = ClockedStop(clock, limit=2)
    statuses: list[AuthStatus] = []
    svc.run_forever(stop, interval=30.0, on_status=statuses.append)
    assert [s["token"].action for s in statuses] == ["error", "error"]
    assert stop.waits == [30.0, 30.0]


THROTTLED_REPLY = {"error_code": "EGW00133", "error_description": "1분당 1회"}


def test_cold_start_kis_throttle_on_the_ws_key_is_not_critical() -> None:
    """KIS 가 접근토큰·접속키 발급에 1분 1회를 함께 걸어도(미실측) 기동 직후 critical 이 없다.

    같은 step 에 토큰을 받은 뒤 접속키는 EGW00133 — 61초 뒤 받는다. 그 사이는 경고(warning) 하나.
    """
    clock = Clock()
    issued: list[datetime] = []

    def shared_limit(req: httpx.Request) -> httpx.Response:
        if issued and clock.now() - issued[-1] < timedelta(seconds=60):
            return httpx.Response(403, json=THROTTLED_REPLY)
        issued.append(clock.now())
        if req.url.path == TOKEN_PATH:
            return httpx.Response(200, json={"access_token": "eyJACCESS", "expires_in": 86400})
        return httpx.Response(200, json={"approval_key": "WSKEY"})

    sink = MemoryHealthSink()
    svc = build_auth_service(
        settings(), fakeredis.FakeRedis(), kis_http(shared_limit), sink, now=clock.now
    )
    stop = ClockedStop(clock, limit=4)
    seen: list[AuthStatus] = []
    svc.run_forever(stop, on_status=seen.append)
    assert [(s.at - NOW, s["token"].action, s["ws_key"].action) for s in seen] == [
        (timedelta(0), "refreshed", "failed"),
        (timedelta(seconds=30), "fresh", "waiting"),
        (timedelta(seconds=60), "fresh", "waiting"),
        (GAP, "fresh", "refreshed"),
    ]
    assert seen[0]["ws_key"].expiring  # 상태로는 '살아 있는 값 없음'
    assert [(e.kind, e.severity) for e in sink.events] == [
        ("token_refreshed", "info"),
        ("ws_key_refresh_failed", "warning"),
        ("ws_key_refreshed", "info"),
    ]


def test_kis_throttle_that_outlasts_a_gap_is_critical() -> None:
    handler, paths = kis_server(lambda: httpx.Response(403, json=THROTTLED_REPLY))
    clock = Clock()
    sink = MemoryHealthSink()
    svc = build_auth_service(
        settings(), fakeredis.FakeRedis(), kis_http(handler), sink, now=clock.now
    )
    svc.run_forever(ClockedStop(clock, limit=4))
    assert paths == [TOKEN_PATH, APPROVAL_PATH, APPROVAL_PATH]
    (ev,) = sink.of("ws_key_expiring")
    assert (ev.at, ev.severity) == (NOW + GAP, "critical")  # 두 번째 EGW00133 에서


def test_real_failure_after_kis_throttle_is_critical_at_once() -> None:
    """EGW00133 유예는 그 시도에만 — 다음 시도가 다른 이유로 실패하면 유예 없이 경고."""
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    iss.fail = TokenIssueThrottled(None)
    svc, sink = service(server, clock, iss)
    svc.step(NOW)
    assert sink.kinds() == ["token_refresh_failed"]
    iss.fail = TokenIssueError("HTTP 500")
    svc.step(clock.advance(61))
    assert sink.kinds() == ["token_refresh_failed", "token_refresh_failed", "token_expiring"]


def test_run_forever_throttled_waits_for_the_shared_gap() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    first = FakeIssuer(clock)
    first.fail = TokenIssueError("HTTP 500")
    service(server, clock, first)[0].step(NOW)  # 다른 인스턴스가 NOW 에 자리를 잡고 실패
    clock.advance(45)
    iss = FakeIssuer(clock)
    svc, _ = service(server, clock, iss)
    stop = ClockedStop(clock, limit=2)
    seen: list[AuthStatus] = []
    svc.run_forever(stop, interval=30.0, on_status=seen.append)
    assert [s["token"].action for s in seen] == ["throttled", "refreshed"]
    assert stop.waits[0] == 16.0
    assert iss.calls == [NOW + GAP]


def test_run_forever_survives_a_bad_tick(caplog: pytest.LogCaptureFixture) -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    stop = threading.Event()
    ticks = iter([datetime(2026, 9, 28, 9, 30), NOW])  # noqa: DTZ001 — 첫 값은 일부러 naive

    def bad_clock() -> datetime:
        t = next(ticks)
        if t is NOW:
            stop.set()
        return t

    svc, _ = service(server, clock, iss, now=bad_clock)
    seen: list[AuthStatus] = []
    with caplog.at_level(logging.ERROR, logger="kbj.services.auth.service"):
        n = svc.run_forever(stop, interval=0, on_status=seen.append)
    assert n == 2
    assert "ValueError" in caplog.text
    assert [s["token"].action for s in seen] == ["refreshed"]
    with pytest.raises(ValueError, match="interval"):
        svc.run_forever(stop, interval=-1)


def test_run_forever_stops_promptly_in_a_thread() -> None:
    server = fakeredis.FakeServer()
    clock = Clock()
    iss = FakeIssuer(clock)
    svc, sink = service(server, clock, iss)
    stop = threading.Event()
    th = threading.Thread(target=svc.run_forever, args=(stop,), kwargs={"interval": 30.0})
    th.start()
    for _ in range(500):
        if sink.events:
            break
        time.sleep(0.01)
    stop.set()  # 30초 대기 중이어도 바로 깨어난다
    th.join(timeout=2)
    assert not th.is_alive()
    assert sink.kinds() == ["token_refreshed"]


class StopOnEvent(MemoryHealthSink):
    def __init__(self, stop: threading.Event) -> None:
        super().__init__()
        self.stop = stop

    def emit(self, event: HealthEvent) -> None:
        super().emit(event)
        if event.kind == "ws_key_refreshed":
            self.stop.set()


def test_serve_builds_and_runs_until_stopped() -> None:
    r = fakeredis.FakeRedis()
    handler, paths = kis_server()
    stop = threading.Event()
    sink = StopOnEvent(stop)
    assert not r.exists(limiter_key(APP_KEY))
    n = serve(settings(), stop, redis=r, http=kis_http(handler), sink=sink, interval=0)
    assert n == 1
    assert sink.kinds() == ["token_refreshed", "ws_key_refreshed"]
    assert paths == [TOKEN_PATH, APPROVAL_PATH]
    assert r.exists(limiter_key(APP_KEY))  # poller·ws-gateway 와 같은 앱키 버킷에서 허가를 받았다
    assert reader(r, settings()).get() == "eyJACCESS1"
    assert reader(r, settings(), "ws_key").get() == "WSKEY1"


def test_serve_beats_a_heartbeat_without_secret_values() -> None:
    from kbj.services.runtime import Heartbeater
    from kbj.store.redis_keys import heartbeat_key

    r = fakeredis.FakeRedis()
    handler, _ = kis_server()
    stop = threading.Event()
    hb = Heartbeater(r, "auth", ttl_s=180)

    def beat(st: AuthStatus) -> None:
        hb.beat(auth=heartbeat_status(st))

    serve(
        settings(),
        stop,
        redis=r,
        http=kis_http(handler),
        sink=StopOnEvent(stop),
        interval=0,
        on_status=beat,
    )
    raw = r.get(heartbeat_key("auth"))
    assert isinstance(raw, bytes)
    body = json.loads(raw)
    assert body["service"] == "auth" and body["status"]["auth"]["ok"] is True
    assert body["status"]["auth"]["token"]["action"] == "refreshed"
    text = raw.decode()
    assert "eyJACCESS1" not in text and "WSKEY1" not in text and APP_KEY not in text


def test_auth_entry_point_module_exists() -> None:
    import importlib.util

    assert importlib.util.find_spec("kbj.services.auth.__main__") is not None


def test_serve_needs_redis() -> None:
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        serve(settings(), threading.Event())


def test_calendar_tagger_tags_trade_date_and_session() -> None:
    tag = calendar_tagger()
    assert tag(NOW) == (date(2026, 9, 28), "day")  # 월 09:30 KST
    assert tag(datetime(2026, 9, 26, 3, 0, tzinfo=UTC)) == (None, None)  # 토 12:00 KST

"""거절 신고 폭주 가드 — auth 쪽(설계 §3.4, R3).

읽는 쪽이 KIS 의 토큰 거절을 `kis:token:rejected` 에 신고하면 auth 는:
- 같은 토큰에 대한 신고로는 **한 번만** 다시 발급한다(신고는 그 값이 캐시에 있는 동안 걸려 있고, 새
  값이 들어오면 지난 신고가 된다 — 재기동한 auth 도 걸린 신고를 이어서 한 번 처리한다).
- 발급 10분이 지난 토큰이면 warning `token_rejected` 뒤 다시 발급한다(61초 간격 규칙은 그대로).
- 발급 10분 안에 온 신고면 다시 발급하지 않고 critical `token_rejected_after_issue` 로 닫는다(닫힌
  신고는 같은 값의 새 신고로도 다시 열리지 않는다 — 재기동해도 같다).
그래서 늘 거절만 신고하는 고장 난 호출자가 있어도 하루 발급은 '처음 1 + 재발급 1' 에서 멈춘다 (GX
`discard` 방식이면 61초마다 — 하루 최대 약 1,400번).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fakeredis
from pydantic import SecretStr

from kbj.data.private.kis.credentials import KisCredentials
from kbj.data.private.kis.token import (
    IssuedToken,
    RedisTokenCache,
    TokenUnavailable,
    reader,
)
from kbj.services.auth.service import AuthService, Credential
from kbj.services.runtime import MemoryHealthSink

CREDS = KisCredentials(
    app_key=SecretStr("PSappKEY0123456789abcdef"), app_secret=SecretStr("SECRETvalue9876543210zyx")
)
START = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)  # 10-06 05:00 KST


class Clock:
    def __init__(self, t: datetime = START) -> None:
        self.t = t

    def now(self) -> datetime:
        return self.t


class FakeIssuer:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls: list[datetime] = []

    def issue(self) -> IssuedToken:
        self.calls.append(self.clock.now())
        t = self.clock.now()
        return IssuedToken(SecretStr(f"tok-{len(self.calls)}"), t + timedelta(hours=24), t)


def auth(
    server: fakeredis.FakeServer, clock: Clock, iss: FakeIssuer
) -> tuple[AuthService, MemoryHealthSink]:
    sink = MemoryHealthSink()
    cache = RedisTokenCache(fakeredis.FakeRedis(server=server))
    svc = AuthService([Credential("token", cache, iss, CREDS.owner)], sink, now=clock.now)
    return svc, sink


def broken_caller(server: fakeredis.FakeServer, clock: Clock) -> None:
    """KIS 가 늘 거절한다고 믿는 호출자 — 새 reader 로 읽고 곧바로 거절 신고."""
    p = reader(fakeredis.FakeRedis(server=server), CREDS, now=clock.now, by="broken")
    try:
        p.get()
    except TokenUnavailable:
        return
    p.invalidate()


def test_a_broken_caller_cannot_cause_an_issue_storm() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    svc, sink = auth(server, clock, iss)
    svc.step(clock.t)  # 05:00 첫 발급
    clock.t += timedelta(hours=9)  # 14:00 — 고장 난 호출자가 나타난다
    end = START + timedelta(hours=23)
    while clock.t < end:
        broken_caller(server, clock)
        svc.step(clock.t)
        clock.t += timedelta(seconds=60)  # 신고·step 을 1분마다(실제 step 은 30초 — 결과는 같다)
    assert len(iss.calls) == 2  # 처음 1 + 신고 처리 재발급 1
    assert iss.calls[1] - iss.calls[0] == timedelta(hours=9)
    assert len(sink.of("token_rejected")) == 1
    (crit,) = sink.of("token_rejected_after_issue")
    assert crit.severity == "critical" and "broken" in crit.detail
    assert "tok-" not in " ".join(e.detail for e in sink.events)  # 토큰 값은 싣지 않는다


def test_a_pending_report_survives_an_auth_restart_and_is_reissued_once() -> None:
    """신고를 본 auth 가 발급 간격에 막힌 채 재기동해도 새 auth 가 이어서 한 번 다시 발급한다."""
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    first, _ = auth(server, clock, iss)
    first.step(clock.t)
    clock.t += timedelta(hours=2)
    broken_caller(server, clock)
    cache = RedisTokenCache(fakeredis.FakeRedis(server=server))
    assert cache.claim_issue(clock.t, timedelta(seconds=61))  # 다른 발급자가 막 자리를 잡았다
    assert first.step(clock.t)["token"].action == "throttled"
    clock.t += timedelta(seconds=61)
    again, sink = auth(server, clock, iss)  # 재기동한 auth
    assert again.step(clock.t)["token"].action == "refreshed"
    clock.t += timedelta(seconds=30)
    assert again.step(clock.t)["token"].action == "fresh"  # 새 값에는 신고가 없다
    assert len(iss.calls) == 2
    assert sink.kinds() == ["token_rejected", "token_refreshed"]


def test_a_closed_report_stays_closed_across_an_auth_restart() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    first, sink1 = auth(server, clock, iss)
    first.step(clock.t)
    clock.t += timedelta(minutes=3)
    broken_caller(server, clock)  # 발급 3분 만의 신고
    assert first.step(clock.t)["token"].action == "fresh"
    assert sink1.kinds() == ["token_refreshed", "token_rejected_after_issue"]
    clock.t += timedelta(minutes=20)
    broken_caller(server, clock)  # 같은 값 — 닫힌 신고는 다시 열리지 않는다
    again, sink2 = auth(server, clock, iss)
    assert again.step(clock.t)["token"].action == "fresh"
    assert len(iss.calls) == 1 and sink2.events == []


def test_reissue_respects_the_61s_gap_and_readers_recover() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    svc, sink = auth(server, clock, iss)
    svc.step(clock.t)
    clock.t += timedelta(seconds=30)
    svc.step(clock.t)
    clock.t += timedelta(hours=1)
    p = reader(fakeredis.FakeRedis(server=server), CREDS, now=clock.now, by="scheduler")
    assert p.get() == "tok-1"
    p.invalidate()
    st = svc.step(clock.t)["token"]
    assert st.action == "refreshed"
    assert p.get() == "tok-2"  # 거절 신고한 reader 도 새 값을 읽는다
    assert sink.kinds() == ["token_refreshed", "token_rejected", "token_refreshed"]


def test_a_report_for_an_old_token_does_not_force_a_refresh() -> None:
    server, clock = fakeredis.FakeServer(), Clock()
    iss = FakeIssuer(clock)
    svc, _ = auth(server, clock, iss)
    svc.step(clock.t)
    cache = RedisTokenCache(fakeredis.FakeRedis(server=server))
    assert cache.report_rejected("tok-0-already-replaced", clock.t, "late")
    clock.t += timedelta(hours=1)
    assert svc.step(clock.t)["token"].action == "fresh"
    assert len(iss.calls) == 1

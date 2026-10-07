"""KIS 접근토큰 캐시 우선 제공자 (설계 §3.2~§3.5, GX PLAN §2.5·§4.1).

GEXLAB `tests/unit/test_auth_client.py` 의 캐시·제공자 시험 13개를 옮겼다(import 경로만 바꿈). GX 와
다른 점(설계 §1.2 — 토큰은 Redis 에만):
- `caches` 매개변수에서 파일 캐시(`FileTokenCache`)를 뺐다 — Redis 만 남는다.
- 파일 캐시 시험 5개(:238~294)·기본 제공자/폴백 시험 3개(:423~475)는 옮기지 않았다(그 코드가 없다).
- 발급자 시험 7개는 `tests/unit/auth/test_issuer.py` 로(발급자는 auth 에만 있다).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import pytest
from pydantic import SecretStr

from kbj.data.private.kis.token import (
    ISSUE_MIN_GAP,
    CachedTokenProvider,
    IssuedToken,
    RedisTokenCache,
    TokenCache,
    TokenIssueError,
    TokenIssueThrottled,
    TokenRecord,
    TokenUnavailable,
    token_owner,
)

APP_KEY = "PSappKEY0123456789abcdef"
APP_SECRET = "SECRETvalue9876543210zyx"
OWNER = token_owner("https://kis.example", APP_KEY)
NOW = datetime(2026, 9, 28, 0, 30, tzinfo=UTC)  # 09:30 KST


class Clock:
    def __init__(self, t: datetime = NOW) -> None:
        self.t = t
        self.slept: list[float] = []
        self.on_sleep: Callable[[], None] | None = None

    def now(self) -> datetime:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += timedelta(seconds=s)
        if self.on_sleep is not None:
            self.on_sleep()


class FakeIssuer:
    def __init__(self, clock: Clock, life: timedelta = timedelta(hours=24)) -> None:
        self.clock = clock
        self.life = life
        self.calls = 0
        self.fail: Exception | None = None

    def issue(self) -> IssuedToken:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        t = self.clock.now()
        return IssuedToken(SecretStr(f"tok-{self.calls}-{id(self)}"), t + self.life, t)


def rec(token: str, expires_in: timedelta, owner: str = OWNER) -> TokenRecord:
    return TokenRecord(
        access_token=SecretStr(token),
        expires_at=NOW + expires_in,
        issued_at=NOW + expires_in - timedelta(hours=24),
        owner=owner,
    )


CacheFactory = Callable[[], TokenCache]


@pytest.fixture(params=["redis"])
def caches(request: pytest.FixtureRequest) -> CacheFactory:
    """같은 저장소를 보는 캐시를 여러 개(=여러 프로세스) 만든다(Redis 만 — 파일 캐시 없음)."""
    server = fakeredis.FakeServer()

    def _make() -> TokenCache:
        assert request.param == "redis"
        return RedisTokenCache(fakeredis.FakeRedis(server=server))

    return _make


def provider(
    cache: TokenCache, clock: Clock, issuer: FakeIssuer | None, **kw: Any
) -> CachedTokenProvider:
    return CachedTokenProvider(cache, OWNER, issuer, now=clock.now, sleep=clock.sleep, **kw)


def test_cache_hit_does_not_issue(caches: CacheFactory) -> None:
    clock = Clock()
    cache = caches()
    cache.store(rec("cached", timedelta(hours=5)), NOW)
    iss = FakeIssuer(clock)
    p = provider(cache, clock, iss)
    assert p.get() == "cached"
    assert p.get() == "cached"
    assert iss.calls == 0


def test_second_process_reuses_first_token(caches: CacheFactory) -> None:
    clock = Clock()
    ia, ib = FakeIssuer(clock), FakeIssuer(clock)
    a = provider(caches(), clock, ia)
    b = provider(caches(), clock, ib)
    tok = a.get()
    clock.t += timedelta(seconds=5)
    assert b.get() == tok
    assert (ia.calls, ib.calls) == (1, 0)


@pytest.mark.parametrize(
    ("left", "issues"), [(timedelta(minutes=59), 1), (timedelta(minutes=61), 0)]
)
def test_refresh_margin_sixty_minutes(caches: CacheFactory, left: timedelta, issues: int) -> None:
    clock = Clock()
    cache = caches()
    cache.store(rec("old", left), NOW)
    iss = FakeIssuer(clock)
    tok = provider(cache, clock, iss).get()
    assert iss.calls == issues
    assert (tok == "old") == (issues == 0)
    loaded = cache.load()
    assert loaded is not None and loaded.token == tok


def test_throttled_refresh_keeps_live_token(caches: CacheFactory) -> None:
    clock = Clock()
    cache = caches()
    cache.store(rec("old", timedelta(minutes=30)), NOW)
    assert caches().claim_issue(NOW - timedelta(seconds=10), ISSUE_MIN_GAP)  # 다른 프로세스
    iss = FakeIssuer(clock)
    assert provider(cache, clock, iss).get() == "old"
    assert iss.calls == 0


def test_issue_gap_is_shared_between_processes(caches: CacheFactory) -> None:
    clock = Clock()
    ia, ib = FakeIssuer(clock), FakeIssuer(clock)
    ia.fail = TokenIssueError("HTTP 500")
    with pytest.raises(TokenIssueError):
        provider(caches(), clock, ia).get()
    clock.t += timedelta(seconds=30)
    b = provider(caches(), clock, ib)
    with pytest.raises(TokenIssueThrottled) as ei:
        b.get()
    assert ib.calls == 0
    assert ei.value.retry_at == NOW + ISSUE_MIN_GAP
    assert "61초" in str(ei.value)
    clock.t = NOW + ISSUE_MIN_GAP
    assert b.get().startswith("tok-1")
    assert ib.calls == 1


def test_wait_mode_sleeps_until_slot(caches: CacheFactory) -> None:
    clock = Clock()
    assert caches().claim_issue(NOW - timedelta(seconds=1), ISSUE_MIN_GAP)
    iss = FakeIssuer(clock)
    tok = provider(caches(), clock, iss, on_throttle="wait").get()
    assert tok.startswith("tok-1")
    assert sum(clock.slept) == pytest.approx(60.05)
    assert iss.calls == 1


def test_wait_mode_takes_token_issued_meanwhile(caches: CacheFactory) -> None:
    clock = Clock()
    other = caches()
    assert other.claim_issue(NOW, ISSUE_MIN_GAP)
    clock.on_sleep = lambda: other.store(rec("from-auth", timedelta(hours=20)), clock.t)
    iss = FakeIssuer(clock)
    assert provider(caches(), clock, iss, on_throttle="wait").get() == "from-auth"
    assert iss.calls == 0


def test_kis_side_throttle_blocks_for_a_gap(caches: CacheFactory) -> None:
    """EGW00133(같은 앱키를 다른 프로그램이 방금 발급): 자리는 잡은 것으로 치고 61초 뒤에."""
    clock = Clock()
    iss = FakeIssuer(clock)
    iss.fail = TokenIssueThrottled(None)
    with pytest.raises(TokenIssueThrottled) as ei:
        provider(caches(), clock, iss).get()
    assert ei.value.retry_at == NOW + ISSUE_MIN_GAP
    assert iss.calls == 1


def test_read_only_provider_never_issues(caches: CacheFactory) -> None:
    clock = Clock()
    cache = caches()
    p = provider(cache, clock, None)
    with pytest.raises(TokenUnavailable):
        p.get()
    cache.store(rec("soon", timedelta(minutes=10)), NOW)
    assert p.get() == "soon"  # 만료 임박이어도 만료 전까지 쓴다 (갱신은 auth)
    clock.t = NOW + timedelta(minutes=9, seconds=30)  # 남은 30초 < 최소 1분
    with pytest.raises(TokenUnavailable):
        p.get()


def test_other_app_key_token_is_ignored(caches: CacheFactory) -> None:
    clock = Clock()
    cache = caches()
    cache.store(rec("other", timedelta(hours=10), owner=token_owner("x", "OTHERKEY")), NOW)
    iss = FakeIssuer(clock)
    assert provider(cache, clock, iss).get() != "other"
    assert iss.calls == 1


def test_invalidate_discards_only_the_rejected_token(caches: CacheFactory) -> None:
    clock = Clock()
    cache = caches()
    cache.store(rec("t1", timedelta(hours=10)), NOW)
    p = provider(cache, clock, FakeIssuer(clock))
    assert p.get() == "t1"
    caches().store(rec("t2", timedelta(hours=12)), NOW)  # 그 사이 auth 가 갱신
    p.invalidate()
    assert p.get() == "t2"
    p.invalidate()
    assert cache.load() is None
    assert p.get().startswith("tok-1")  # 캐시가 비었으니 새로 받는다


def test_redis_cache_ttls() -> None:
    r = fakeredis.FakeRedis()
    cache = RedisTokenCache(r)
    cache.store(rec("t", timedelta(hours=2)), NOW)
    assert 7_190_000 < r.pttl("kis:token") <= 7_200_000
    cache.store(rec("gone", timedelta(seconds=-5)), NOW)  # 이미 만료 → 쓰지 않는다
    loaded = cache.load()
    assert loaded is not None and loaded.token == "t"
    assert cache.claim_issue(NOW, ISSUE_MIN_GAP)
    assert not cache.claim_issue(NOW + timedelta(seconds=60), ISSUE_MIN_GAP)
    assert 0 < r.pttl("kis:token:issue_blocked_until") <= 61_000
    assert cache.next_issue_at(NOW) == NOW + ISSUE_MIN_GAP
    assert cache.claim_issue(NOW + ISSUE_MIN_GAP, ISSUE_MIN_GAP)


def test_token_never_in_repr_or_errors() -> None:
    r = rec("SUPERSECRETTOKEN", timedelta(hours=1))
    assert "SUPERSECRETTOKEN" not in repr(r) and "SUPERSECRETTOKEN" not in str(r)
    assert "SUPERSECRETTOKEN" not in repr(IssuedToken(r.access_token, NOW, NOW))
    assert json.loads(r.model_dump_json())["access_token"] == "SUPERSECRETTOKEN"  # 저장 형식만

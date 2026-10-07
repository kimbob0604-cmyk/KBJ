"""출처별 리미터(`RedisRateLimiter.scoped`) — 키 분리·같은 범위 공유·KIS 앱키 버킷 호환(§4.4).

fakeredis(Lua) 서버 하나를 여러 "프로세스"(클라이언트)가 나눠 쓴다. 시계는 가짜(tests/fakes/clock).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import fakeredis
import pytest

from kbj.config.settings import Settings
from kbj.data.limits import load_limits
from kbj.data.ratelimit import (
    Priority,
    RateLimitConfig,
    Reason,
    RedisRateLimiter,
    limiter_key,
    scoped_key,
)
from tests.fakes.clock import FakeClock

T0 = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)  # 09:00 KST
APP_KEY = "PSfakeAppKey0123456789"
DART_KEY = "fake-dart-key-0123456789"
CFG2 = RateLimitConfig(rate=2.0, floor_rate=1.0, tr_min_interval_s={})


def _redis(server: fakeredis.FakeServer) -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(server=server)


def test_scoped_kis_is_the_app_key_bucket() -> None:
    """`scoped("kis", 앱키)` 와 GX 식 `RedisRateLimiter(r, 앱키)` 는 같은 키 — legacy GX 와 버킷
    공유."""
    assert scoped_key("kis", APP_KEY) == limiter_key(APP_KEY)
    server = fakeredis.FakeServer()
    clock = FakeClock(T0)
    gx_style = RedisRateLimiter(_redis(server), APP_KEY, clock=clock)
    kbj_style = RedisRateLimiter.scoped(_redis(server), "kis", APP_KEY, RateLimitConfig(), clock)
    assert gx_style.key == kbj_style.key
    assert gx_style.attempt(Priority.P1, "FHKST01010100").granted
    assert kbj_style.attempt(Priority.P1, "FHKST01010100").reason is Reason.BUCKET


def test_sources_and_scopes_get_separate_buckets() -> None:
    server = fakeredis.FakeServer()
    clock = FakeClock(T0)
    a = RedisRateLimiter.scoped(_redis(server), "datago", "15100475", CFG2, clock)
    b = RedisRateLimiter.scoped(_redis(server), "datago", "15101609", CFG2, clock)
    c = RedisRateLimiter.scoped(
        _redis(server), "dart", "15100475", CFG2, clock
    )  # 같은 범위, 다른 출처
    assert len({a.key, b.key, c.key}) == 3
    assert a.key.startswith("rl:datago:") and c.key.startswith("rl:dart:")
    for lim in (a, b, c):
        assert lim.attempt(Priority.P3, "x").granted  # 서로 막지 않는다


def test_same_scope_is_shared_between_processes() -> None:
    """KBJ 수집기와 legacy 브리지(서로 다른 프로세스)가 같은 버킷을 쓴다."""
    server = fakeredis.FakeServer()
    clock = FakeClock(T0)
    collector = RedisRateLimiter.scoped(_redis(server), "dart", DART_KEY, CFG2, clock)
    bridge = RedisRateLimiter.scoped(_redis(server), "dart", DART_KEY, CFG2, clock)
    assert collector.attempt(Priority.P3, "list.json").granted
    assert bridge.attempt(Priority.P3, "list.json").reason is Reason.BUCKET
    clock.advance(0.5)  # 2/s → 0.5초 간격
    assert bridge.attempt(Priority.P3, "list.json").granted
    collector.on_rate_limited()
    assert bridge.current_rate() == 1.0  # 감속도 공유


def test_scope_value_never_appears_in_redis() -> None:
    r = fakeredis.FakeRedis()
    clock = FakeClock(T0)
    lim = RedisRateLimiter.scoped(r, "dart", DART_KEY, CFG2, clock)
    lim.acquire(Priority.P3, "list.json")
    lim.attempt(Priority.P3, "list.json", waiter="w", register=True)
    lim.on_rate_limited()
    keys = [k.decode() if isinstance(k, bytes) else str(k) for k in r.keys("*")]
    assert keys and all(k.startswith(lim.key) for k in keys)
    dump = repr(keys) + repr(r.hgetall(lim.key))
    assert DART_KEY not in dump


def test_default_config_comes_from_limits_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """config 를 안 주면 limits.yaml 의 그 출처 값 — KIS 기본 4/s 를 다른 출처에 쓰지 않는다.

    `scoped` 는 안에서 `Settings()` 를 만든다 — 실제 `.env`(작업 디렉터리 기준)와 바깥 `KBJ_*`
    환경을 읽지 않게 둘 다 치운다(설정 폴더는 레포 루트 기준이라 작업 디렉터리와 무관하다).
    """
    for name in list(os.environ):
        if name.upper().startswith("KBJ_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    clock = FakeClock(T0)
    limits = load_limits(settings=Settings(_env_file=None))  # pyright: ignore[reportCallIssue]
    for source in ("krx", "dart", "datago", "kosis", "ecos", "telegram"):
        lim = RedisRateLimiter.scoped(fakeredis.FakeRedis(), source, "scope", clock=clock)
        assert lim.config == limits.source(source).rate_config()
        assert lim.config.rate == limits.source(source).rate
    ecos = RedisRateLimiter.scoped(fakeredis.FakeRedis(), "ecos", "k", clock=clock)
    assert ecos.config.hold_s == 600  # 602 → 10분 유지


@pytest.mark.parametrize("source", ["", "KIS", "rl:kis", "da go"])
def test_bad_source_names_are_refused(source: str) -> None:
    with pytest.raises(ValueError):
        scoped_key(source, "x")
    with pytest.raises(ValueError):
        scoped_key("kis", "")

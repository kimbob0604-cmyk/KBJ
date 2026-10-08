"""응답 캐시 — 데이터 버전 키(Redis 15초)·프로세스 메모리 본문·주입 시계 만료(§5.3)."""

from __future__ import annotations

from datetime import UTC, datetime

import fakeredis
import pytest

from kbj.services.api.cache import DOMAIN_JOBS, DataVersions, ResponseCache, cache_key
from kbj.store.redis_keys import api_data_version_key
from tests.unit.api.api_world import FakeClock, World


def test_data_version_cached_in_redis() -> None:
    r = fakeredis.FakeRedis()
    calls: list[str] = []

    def src(domain: str) -> str:
        calls.append(domain)
        return f"v{len(calls)}"

    dv = DataVersions(r, src)
    assert dv.get("flows") == "v1"
    assert dv.get("flows") == "v1"
    assert calls == ["flows"]
    assert 0 < r.ttl(api_data_version_key("flows")) <= 15
    with pytest.raises(ValueError, match="영역"):
        dv.get("weather")


def test_domains_cover_routes() -> None:
    assert set(DOMAIN_JOBS) == {"market", "board", "flows", "etf"}


def test_response_cache_expiry_and_bound() -> None:
    clock = FakeClock(datetime(2026, 10, 7, tzinfo=UTC))
    c = ResponseCache(now=clock, max_entries=2)
    a = c.put("a", b"1", 30)
    assert c.get("a") == a
    clock.advance(seconds=31)
    assert c.get("a") is None
    c.put("x", b"1", 30)
    c.put("y", b"2", 30)
    c.put("z", b"3", 30)
    assert len(c) == 2 and c.get("x") is None


def test_cache_key_sorted() -> None:
    assert cache_key("/p", [("b", "2"), ("a", "1")], "v") == cache_key(
        "/p", [("a", "1"), ("b", "2")], "v"
    )
    assert cache_key("/p", [], "v1") != cache_key("/p", [], "v2")


def test_route_uses_version(world: World) -> None:
    versions = {"flows": "v1"}
    world.versions = lambda d: versions.get(d, "none")
    c = world.client()
    world.login(c)
    first = c.get("/api/flows/investors").json()
    # 저장소를 비워도 같은 버전이면 캐시 본문
    from kbj.store.repos import memory_repos

    app_state = c.app.state.kbj  # pyright: ignore[reportFunctionMemberAccess, reportAttributeAccessIssue]
    object.__setattr__(app_state.read, "repos", memory_repos())
    assert c.get("/api/flows/investors").json() == first
    # 버전이 바뀌면(Redis 15초 캐시를 지우고) 다시 계산 → 데이터 없음
    versions["flows"] = "v2"
    world.redis.delete(api_data_version_key("flows"))
    assert c.get("/api/flows/investors").status_code == 404


def test_response_cache_threads() -> None:
    """스레드 풀의 동시 요청 — 만료·LRU 이동·축출이 겹쳐도 예외가 없다."""
    from concurrent.futures import ThreadPoolExecutor

    clock = FakeClock(datetime(2026, 10, 7, tzinfo=UTC))
    rc = ResponseCache(now=clock, max_entries=4)

    def work(i: int) -> None:
        for j in range(400):
            key = f"k{(i + j) % 9}"
            if rc.get(key) is None:
                rc.put(key, b"x", 0 if j % 3 == 0 else 60)

    with ThreadPoolExecutor(8) as ex:
        for f in [ex.submit(work, i) for i in range(8)]:
            f.result()
    assert len(rc) <= 4


def test_ledger_cache_builds_once_per_version(world: World) -> None:
    """같은 데이터 버전의 동시 미적중은 원장을 한 번만 만든다."""
    from concurrent.futures import ThreadPoolExecutor

    from kbj.services.api.readers import _common

    calls: list[object] = []
    real = _common.load_ledger

    def counting(*a: object, **k: object) -> object:
        calls.append(a)
        return real(*a, **k)  # pyright: ignore[reportArgumentType]

    cache = _common.LedgerCache(lambda: "v1")
    end = world.market.last_day
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_common, "load_ledger", counting)
        with ThreadPoolExecutor(4) as ex:
            got = [f.result() for f in [ex.submit(cache.get, world.repos, end) for _ in range(4)]]
    assert len(calls) == 1
    assert all(g is got[0] for g in got)

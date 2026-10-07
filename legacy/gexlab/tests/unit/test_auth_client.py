"""KIS 접근토큰 캐시 우선 제공자 (설계 §2 토큰 경합 방지, PLAN §2.5·§4.1)."""

from __future__ import annotations

import json
import logging
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from config.settings import Settings
from data.kis.auth_client import (
    ISSUE_MIN_GAP,
    TOKEN_PATH,
    CachedTokenProvider,
    FallbackTokenCache,
    FileTokenCache,
    IssuedToken,
    KisTokenIssuer,
    RedisTokenCache,
    TokenCache,
    TokenIssueError,
    TokenIssueThrottled,
    TokenRecord,
    TokenUnavailable,
    default_token_provider,
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


@pytest.fixture(params=["file", "redis"])
def caches(request: pytest.FixtureRequest, tmp_path: Path) -> CacheFactory:
    """같은 저장소를 보는 캐시를 여러 개(=여러 프로세스) 만든다."""
    server = fakeredis.FakeServer()
    path = tmp_path / "state" / "kis.token.json"

    def _make() -> TokenCache:
        if request.param == "redis":
            return RedisTokenCache(fakeredis.FakeRedis(server=server))
        return FileTokenCache(path)

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


def test_file_cache_permissions(tmp_path: Path) -> None:
    path = tmp_path / "state" / "kis.token.json"
    cache = FileTokenCache(path)
    cache.store(rec("t", timedelta(hours=1)), NOW)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    lock = path.with_name(path.name + ".lock")
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert cache.claim_issue(NOW, ISSUE_MIN_GAP)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    names = sorted(p.name for p in path.parent.iterdir())
    assert names == ["kis.token.json", "kis.token.json.lock"]  # 임시 파일이 남지 않는다


def test_file_cache_parent_created_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """state/ 없는 새 체크아웃에서 probe 둘이 동시에 뜨면 한쪽 mkdir 이 늦는다 → 예외 없이 진행."""
    parent = tmp_path / "state"
    real_mkdir = Path.mkdir

    def racing_mkdir(
        self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if self == parent and not parent.exists():
            real_mkdir(parent, mode=0o755)  # 다른 프로세스가 먼저 만들었다
        return real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", racing_mkdir)
    cache = FileTokenCache(parent / "kis.token.json")
    assert cache.load() is None
    cache.store(rec("t", timedelta(hours=1)), NOW)
    assert stat.S_IMODE((parent / "kis.token.json").stat().st_mode) == 0o600
    # 이미 있던 폴더의 권한은 건드리지 않는다 (만든 쪽만 0700)
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755


def test_file_cache_existing_parent_is_left_alone(tmp_path: Path) -> None:
    parent = tmp_path / "state"
    parent.mkdir(mode=0o750)
    parent.chmod(0o750)
    FileTokenCache(parent / "kis.token.json").store(rec("t", timedelta(hours=1)), NOW)
    assert stat.S_IMODE(parent.stat().st_mode) == 0o750


def test_file_cache_corrupt_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "kis.token.json"
    path.write_text("{not json", encoding="utf-8")
    cache = FileTokenCache(path)
    assert cache.load() is None
    assert cache.next_issue_at(NOW) is None
    cache.store(rec("t", timedelta(hours=1)), NOW)
    loaded = cache.load()
    assert loaded is not None and loaded.token == "t"


def test_file_cache_default_path_is_gitignored() -> None:
    assert FileTokenCache().path == Path("state/kis.token.json")
    assert Settings(_env_file=None).kis_token_cache_path == Path("state/kis.token.json")  # pyright: ignore[reportCallIssue]
    root = Path(__file__).resolve().parents[2]
    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "state/" in lines and "*.token.json" in lines


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


# ---- KIS 발급자 (httpx.MockTransport) ----------------------------------------------------------


def issuer_with(handler: Callable[[httpx.Request], httpx.Response]) -> KisTokenIssuer:
    http = httpx.Client(base_url="https://kis.example", transport=httpx.MockTransport(handler))
    return KisTokenIssuer(http, SecretStr(APP_KEY), SecretStr(APP_SECRET), now=lambda: NOW)


def test_issuer_posts_credentials_and_takes_earlier_expiry() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            json={
                "access_token": "eyJTOKEN",
                "token_type": "Bearer",
                "expires_in": 86400,
                "access_token_token_expired": "2026-09-29 08:00:00",  # KST = 전날 23:00 UTC
            },
        )

    got = issuer_with(handler).issue()
    assert got.access_token.get_secret_value() == "eyJTOKEN"
    assert got.expires_at == datetime(2026, 9, 28, 23, 0, tzinfo=UTC)  # 86400초 뒤보다 이르다
    assert got.issued_at == NOW
    req = seen[0]
    assert (req.method, req.url.path) == ("POST", TOKEN_PATH)
    body = json.loads(req.content)
    assert body == {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"expires_in": 3600}, NOW + timedelta(hours=1)),
        (
            {"access_token_token_expired": "2026-09-29 09:30:00"},
            datetime(2026, 9, 29, 0, 30, tzinfo=UTC),
        ),
        (
            {"access_token_token_expired": "garbage", "expires_in": 60},
            NOW + timedelta(seconds=60),
        ),
    ],
)
def test_issuer_expiry_sources(payload: dict[str, Any], expected: datetime) -> None:
    got = issuer_with(lambda _: httpx.Response(200, json={"access_token": "T", **payload})).issue()
    assert got.expires_at == expected


def test_issuer_throttle_code() -> None:
    body = {
        "error_code": "EGW00133",
        "error_description": "접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)",
    }
    with pytest.raises(TokenIssueThrottled):
        issuer_with(lambda _: httpx.Response(403, json=body)).issue()


def test_issuer_errors_are_redacted() -> None:
    echo = {"error_code": "EGW00103", "error_description": f"bad appkey {APP_KEY} / {APP_SECRET}"}
    with pytest.raises(TokenIssueError) as ei:
        issuer_with(lambda _: httpx.Response(403, json=echo)).issue()
    msg = str(ei.value)
    assert "EGW00103" in msg and "403" in msg
    assert APP_KEY not in msg and APP_SECRET not in msg


@pytest.mark.parametrize("secret", [APP_KEY, APP_SECRET])
@pytest.mark.parametrize("inside", [3, 10, 23])
def test_issuer_redacts_before_truncating(secret: str, inside: int) -> None:
    # 200자 자르기 선에 비밀값이 걸쳐도 앞부분이 새지 않는다 — 가린 뒤 자른다
    code = "EGW00103"
    pad = "x" * (200 - len(code) - 1 - inside)  # "코드 설명" 에서 비밀값 앞 inside 글자만 200자 안
    echo = {"error_code": code, "error_description": pad + secret + " tail"}
    with pytest.raises(TokenIssueError) as ei:
        issuer_with(lambda _: httpx.Response(403, json=echo)).issue()
    assert secret[:inside] not in str(ei.value)


@pytest.mark.parametrize(
    "payload", [{"token_type": "Bearer"}, {"access_token": "LEAKYTOKEN", "token_type": "Bearer"}]
)
def test_issuer_bad_body_never_echoes_it(payload: dict[str, Any]) -> None:
    with pytest.raises(TokenIssueError) as ei:
        issuer_with(lambda _: httpx.Response(200, json=payload)).issue()
    assert "LEAKYTOKEN" not in str(ei.value)


def test_issuer_transport_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    with pytest.raises(TokenIssueError, match="ConnectError"):
        issuer_with(handler).issue()


def test_default_provider_picks_cache(tmp_path: Path) -> None:
    http = httpx.Client()
    with pytest.raises(TokenUnavailable):
        default_token_provider(Settings(_env_file=None), http)  # pyright: ignore[reportCallIssue]
    s = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        kis_app_key=SecretStr(APP_KEY),
        kis_app_secret=SecretStr(APP_SECRET),
        kis_token_cache_path=tmp_path / "t.json",
    )
    p = default_token_provider(s, http)
    assert isinstance(p._cache, FileTokenCache) and p._cache.path == tmp_path / "t.json"  # pyright: ignore[reportPrivateUsage]
    # REDIS_URL 이 있으면 Redis 먼저, 연결이 안 되면 같은 파일 캐시로 (생성만으로는 접속하지 않는다)
    s2 = s.model_copy(update={"redis_url": "redis://localhost:6399/0"})
    fb = default_token_provider(s2, http)._cache  # pyright: ignore[reportPrivateUsage]
    assert isinstance(fb, FallbackTokenCache)
    assert isinstance(fb.primary, RedisTokenCache)
    assert isinstance(fb.fallback, FileTokenCache) and fb.fallback.path == tmp_path / "t.json"
    assert not fb.degraded


def test_fallback_cache_switches_to_file_when_redis_down(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """compose 용 REDIS_URL 이 .env 에 남은 채 로컬 probe 를 돌려도 토큰 캐시가 죽지 않는다."""
    server = fakeredis.FakeServer()
    path = tmp_path / "state" / "kis.token.json"
    cache = FallbackTokenCache(
        RedisTokenCache(fakeredis.FakeRedis(server=server)), FileTokenCache(path)
    )
    cache.store(rec("in-redis", timedelta(hours=2)), NOW)
    loaded = cache.load()
    assert loaded is not None and loaded.token == "in-redis"
    assert not path.parent.exists()  # Redis 가 살아 있으면 파일은 건드리지 않는다
    server.connected = False
    with caplog.at_level(logging.WARNING, logger="data.kis.auth_client"):
        assert cache.load() is None  # 파일 캐시는 비어 있다
        assert cache.claim_issue(NOW, ISSUE_MIN_GAP)
        assert not cache.claim_issue(NOW + timedelta(seconds=30), ISSUE_MIN_GAP)
        assert cache.next_issue_at(NOW) == NOW + ISSUE_MIN_GAP
        cache.store(rec("in-file", timedelta(hours=2)), NOW)
        cache.discard("not-this-one")
    assert cache.degraded
    on_disk = FileTokenCache(path).load()
    assert on_disk is not None and on_disk.token == "in-file"
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "파일 캐시" in warnings[0].getMessage()
    server.connected = True  # 한 번 바꾸면 이 인스턴스는 끝까지 파일 캐시 (섞어 쓰지 않는다)
    again = cache.load()
    assert again is not None and again.token == "in-file"


def test_provider_on_unreachable_redis_issues_once_into_file(tmp_path: Path) -> None:
    server = fakeredis.FakeServer()
    server.connected = False
    path = tmp_path / "state" / "kis.token.json"
    clock = Clock()
    iss = FakeIssuer(clock)

    def fresh() -> FallbackTokenCache:  # 실행마다 새 프로세스
        return FallbackTokenCache(
            RedisTokenCache(fakeredis.FakeRedis(server=server)), FileTokenCache(path)
        )

    first = provider(fresh(), clock, iss).get()
    clock.sleep(5)
    assert provider(fresh(), clock, iss).get() == first
    assert iss.calls == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

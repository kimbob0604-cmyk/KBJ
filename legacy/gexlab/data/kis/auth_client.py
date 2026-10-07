"""KIS 접근토큰 — 캐시 우선 제공자 (설계 docs/phase1_design.md §2 토큰 경합 방지, PLAN §2.5·§4.1).

- 캐시(Redis `kis:token` 또는 파일 `state/kis.token.json`)를 먼저 읽고, 없거나 만료가 60분 안으로
  다가왔을 때만 발급한다(PLAN §4.1 auth: 만료 1시간 전 갱신)
- 발급 시도는 61초에 한 번 이하(KIS 1분 1회, `EGW00133` 실측). 다음 발급 가능 시각을 캐시에 두어
  프로세스끼리 공유한다. 발급이 막혀도 기존 토큰이 살아 있으면 그것을 쓴다
- 서비스는 발급자 없이(읽기 전용) 쓰고, 발급은 `auth` 서비스와 auth 기동 전 로컬 probe 만 한다
- probe 기본값(`default_token_provider`)은 REDIS_URL 이 있으면 Redis 를 먼저 보고, 연결이 안 되면
  파일 캐시로 넘어간다(`FallbackTokenCache`) — compose 용 REDIS_URL 이 .env 에 있어도 멈추지 않게
- 토큰·앱키는 어디에도 출력하지 않는다. 예외 메시지도 가리고, 모델 repr 은 `SecretStr` 이 가린다
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from zoneinfo import ZoneInfo

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    SecretStr,
    ValidationError,
    field_serializer,
)
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import WatchError

from config.settings import Settings

KST = ZoneInfo("Asia/Seoul")
TOKEN_PATH = "/oauth2/tokenP"  # noqa: S105 — 경로일 뿐 비밀 아님
ISSUE_THROTTLED_CODE = "EGW00133"  # 접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)
ISSUE_MIN_GAP = timedelta(seconds=61)
REFRESH_MARGIN = timedelta(minutes=60)
MIN_VALID = timedelta(minutes=1)  # 이보다 적게 남은 토큰은 없는 것으로 본다
DEFAULT_TOKEN_FILE = Path("state/kis.token.json")
REDIS_TIMEOUT_S = 2.0  # probe 기본 제공자의 Redis 접속·응답 제한 (넘으면 파일 캐시로)

log = logging.getLogger(__name__)


class TokenError(RuntimeError):
    """토큰 관련 오류. 메시지에 토큰·앱키가 들어가지 않는다."""


class TokenUnavailable(TokenError):
    """쓸 수 있는 토큰이 캐시에 없고 발급자도 없다(읽기 전용 제공자)."""


class TokenIssueError(TokenError):
    """발급 요청이 실패했다."""


class TokenIssueThrottled(TokenIssueError):
    """발급 간격(61초) 안이라 발급하지 않았다."""

    def __init__(self, retry_at: datetime | None) -> None:
        when = retry_at.astimezone(KST).strftime("%H:%M:%S KST") if retry_at else "잠시 뒤"
        super().__init__(f"토큰 발급은 61초에 한 번 — {when} 이후 다시 시도")
        self.retry_at = retry_at


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def token_owner(base_url: str, app_key: str) -> str:
    """캐시된 토큰이 어느 앱키·환경 것인지 (해시만 남긴다)."""
    return hashlib.sha256(f"{base_url}\n{app_key}".encode()).hexdigest()[:16]


def _redact(text: str, secrets: list[str]) -> str:
    """비밀값을 가린다. 긴 값부터 바꿔 한 값이 다른 값의 일부여도 조각이 남지 않게 한다.

    자르기(길이 제한)는 반드시 가린 뒤에 한다 — 자른 뒤 가리면 경계에 걸친 앞부분이 샌다.
    """
    for s in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(s, "***")
    return text


class TokenRecord(BaseModel):
    """캐시에 두는 토큰 한 건. JSON 으로 쓸 때만 토큰 원문이 나온다."""

    model_config = ConfigDict(frozen=True)

    access_token: SecretStr
    expires_at: AwareDatetime
    issued_at: AwareDatetime
    owner: str

    @field_serializer("access_token", when_used="json")
    def _dump_token(self, v: SecretStr) -> str:
        return v.get_secret_value()

    @property
    def token(self) -> str:
        return self.access_token.get_secret_value()


@dataclass(frozen=True)
class IssuedToken:
    access_token: SecretStr
    expires_at: datetime
    issued_at: datetime


class TokenProvider(Protocol):
    def get(self) -> str:
        """지금 쓸 접근토큰."""
        ...

    def invalidate(self) -> None:
        """KIS 가 토큰을 거절했을 때. 다음 get() 은 캐시를 다시 보거나 새로 받는다."""
        ...


class TokenIssuer(Protocol):
    def issue(self) -> IssuedToken: ...


class TokenCache(Protocol):
    def load(self) -> TokenRecord | None: ...

    def store(self, rec: TokenRecord, now: datetime) -> None: ...

    def discard(self, token: str) -> None:
        """캐시의 토큰이 이 값일 때만 지운다 (다른 프로세스가 새로 받은 토큰은 둔다)."""
        ...

    def claim_issue(self, now: datetime, min_gap: timedelta) -> bool:
        """발급 시도 자리를 원자적으로 잡는다. 직전 시도가 min_gap 안이면 False."""
        ...

    def next_issue_at(self, now: datetime) -> datetime | None: ...


# ---- KIS 발급 -------------------------------------------------------------------------------


class _TokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: SecretStr
    token_type: str | None = None
    expires_in: int | None = None
    access_token_token_expired: str | None = None  # 'YYYY-MM-DD HH:MM:SS' (KST)


class KisTokenIssuer:
    """`POST /oauth2/tokenP`.

    만료 시각은 `access_token_token_expired`(KST)와 `expires_in` 중 이른 쪽으로 잡는다.
    """

    def __init__(
        self,
        http: httpx.Client,
        app_key: SecretStr,
        app_secret: SecretStr,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._http = http
        self._key = app_key
        self._secret = app_secret
        self._now = now

    def _secrets(self) -> list[str]:
        return [self._key.get_secret_value(), self._secret.get_secret_value()]

    def issue(self) -> IssuedToken:
        issued_at = self._now()
        try:
            r = self._http.post(
                TOKEN_PATH,
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._key.get_secret_value(),
                    "appsecret": self._secret.get_secret_value(),
                },
            )
        except httpx.HTTPError as e:
            msg = _redact(str(e), self._secrets())
            raise TokenIssueError(f"토큰 발급 요청 실패: {type(e).__name__} {msg}") from None
        try:
            body: Any = r.json()
        except ValueError:
            body = None
        if r.status_code != 200:
            err: dict[str, Any] = cast(dict[str, Any], body) if isinstance(body, dict) else {}
            code = str(err.get("error_code") or err.get("msg_cd") or "")
            desc = str(err.get("error_description") or err.get("msg1") or "")
            if code == ISSUE_THROTTLED_CODE:
                raise TokenIssueThrottled(None)
            detail = _redact(f"{code} {desc}".strip(), self._secrets())[:200]
            raise TokenIssueError(f"토큰 발급 실패 HTTP {r.status_code}: {detail}")
        try:
            parsed = _TokenResponse.model_validate(body)
        except ValidationError:
            # 본문을 싣지 않는다 (토큰이 들어 있을 수 있다)
            raise TokenIssueError("토큰 응답 형식이 다르다 (access_token 없음)") from None
        return IssuedToken(parsed.access_token, _expiry(parsed, issued_at), issued_at)


def _expiry(p: _TokenResponse, issued_at: datetime) -> datetime:
    cands: list[datetime] = []
    if p.access_token_token_expired:
        try:
            kst = datetime.strptime(
                p.access_token_token_expired.strip() + " +0900", "%Y-%m-%d %H:%M:%S %z"
            )
            cands.append(kst.astimezone(UTC))
        except ValueError:
            pass
    if p.expires_in is not None and p.expires_in > 0:
        cands.append(issued_at + timedelta(seconds=p.expires_in))
    if not cands:
        raise TokenIssueError("토큰 응답에 만료 시각이 없다")
    return min(cands)


# ---- 캐시 -----------------------------------------------------------------------------------


class _FileState(BaseModel):
    token: TokenRecord | None = None
    issue_blocked_until: AwareDatetime | None = None


class FileTokenCache:
    """로컬 파일 캐시 (Redis 없는 probe 용). 파일 0600, 새로 만드는 상위 폴더 0700.

    같은 폴더의 `<이름>.lock` 에 flock 을 걸어 여러 프로세스의 읽기-쓰기를 직렬화하고,
    쓰기는 임시 파일 + rename 으로 원자적으로 한다.
    """

    def __init__(self, path: Path = DEFAULT_TOKEN_FILE) -> None:
        self.path = path

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        parent = self.path.parent
        try:
            parent.mkdir(parents=True, mode=0o700)
        except FileExistsError:
            pass  # 있던 폴더(다른 프로세스가 방금 만든 것 포함)는 권한을 건드리지 않는다
        else:
            parent.chmod(0o700)  # 이번에 만든 폴더만. umask 무시
        fd = os.open(self.path.with_name(self.path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(self) -> _FileState:
        try:
            return _FileState.model_validate_json(self.path.read_bytes())
        except (FileNotFoundError, ValidationError, ValueError):
            return _FileState()  # 없거나 깨졌으면 빈 캐시

    def _write(self, st: _FileState) -> None:
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, st.model_dump_json().encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.path)

    def load(self) -> TokenRecord | None:
        with self._locked():
            return self._read().token

    def store(self, rec: TokenRecord, now: datetime) -> None:
        with self._locked():
            st = self._read()
            self._write(_FileState(token=rec, issue_blocked_until=st.issue_blocked_until))

    def discard(self, token: str) -> None:
        with self._locked():
            st = self._read()
            if st.token is not None and st.token.token == token:
                self._write(_FileState(token=None, issue_blocked_until=st.issue_blocked_until))

    def claim_issue(self, now: datetime, min_gap: timedelta) -> bool:
        with self._locked():
            st = self._read()
            if st.issue_blocked_until is not None and now < st.issue_blocked_until:
                return False
            self._write(_FileState(token=st.token, issue_blocked_until=now + min_gap))
            return True

    def next_issue_at(self, now: datetime) -> datetime | None:
        with self._locked():
            until = self._read().issue_blocked_until
        return until if until is not None and until > now else None


_CLAIM_LUA = """
local v = tonumber(redis.call('GET', KEYS[1]))
if v and tonumber(ARGV[1]) < v then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'PX', ARGV[3])
return 1
"""


class RedisTokenCache:
    """Redis 캐시 `kis:token`(TTL = 남은 수명)과 발급 간격 키 `kis:token:issue_blocked_until`.

    간격 판정은 넘겨받은 시각으로 한다(가짜 시계 테스트). Redis TTL 은 청소용이다.
    """

    def __init__(self, redis: Redis, key: str = "kis:token") -> None:
        self._r = redis
        self.key = key
        self.issue_key = key + ":issue_blocked_until"
        self._claim = redis.register_script(_CLAIM_LUA)

    def load(self) -> TokenRecord | None:
        raw: Any = self._r.get(self.key)
        if raw is None:
            return None
        try:
            return TokenRecord.model_validate_json(raw)
        except ValidationError:
            return None

    def store(self, rec: TokenRecord, now: datetime) -> None:
        ttl_ms = int((rec.expires_at - now).total_seconds() * 1000)
        if ttl_ms > 0:
            self._r.set(self.key, rec.model_dump_json(), px=ttl_ms)

    def discard(self, token: str) -> None:
        with self._r.pipeline() as p:
            try:
                p.watch(self.key)
                raw: Any = p.get(self.key)
                if raw is None:
                    return
                try:
                    cur = TokenRecord.model_validate_json(raw)
                except ValidationError:
                    cur = None
                if cur is not None and cur.token != token:
                    return
                p.multi()
                p.delete(self.key)
                p.execute()
            except WatchError:
                return  # 그 사이 다른 프로세스가 바꿨다 → 둔다

    def claim_issue(self, now: datetime, min_gap: timedelta) -> bool:
        until = now + min_gap
        res: Any = self._claim(
            keys=[self.issue_key],
            args=[_us(now), _us(until), max(int(min_gap.total_seconds() * 1000), 1)],
        )
        return int(res) == 1

    def next_issue_at(self, now: datetime) -> datetime | None:
        raw: Any = self._r.get(self.issue_key)
        if raw is None:
            return None
        until = datetime.fromtimestamp(int(raw) / 1_000_000, tz=UTC)
        return until if until > now else None


def _us(t: datetime) -> int:
    return round(t.timestamp() * 1_000_000)


class FallbackTokenCache:
    """Redis 캐시를 먼저 쓰고, 연결이 안 되면(접속 거부·이름 풀이 실패·시간 초과) 그 뒤로는
    이 인스턴스가 파일 캐시만 쓴다. probe 기본값용 — 서비스는 쓰지 않는다(Redis 를 못 쓰면
    멈추는 게 맞다, 설계 §2).

    한 번 넘어가면 되돌아가지 않는다(한 실행 안에서 두 캐시를 섞어 발급 간격 기록이 갈리지 않게).
    경고 로그에는 예외 종류만 남긴다(URL·메시지에 비밀번호가 있을 수 있다).
    """

    def __init__(self, primary: TokenCache, fallback: TokenCache) -> None:
        self.primary = primary
        self.fallback = fallback
        self.degraded = False

    def _run[T](self, op: Callable[[TokenCache], T]) -> T:
        if not self.degraded:
            try:
                return op(self.primary)
            except (RedisConnectionError, RedisTimeoutError) as e:
                self.degraded = True
                log.warning(
                    "토큰 캐시 Redis 에 연결할 수 없다 — 이번 실행은 파일 캐시로 (%s)",
                    type(e).__name__,
                )
        return op(self.fallback)

    def load(self) -> TokenRecord | None:
        return self._run(lambda c: c.load())

    def store(self, rec: TokenRecord, now: datetime) -> None:
        self._run(lambda c: c.store(rec, now))

    def discard(self, token: str) -> None:
        self._run(lambda c: c.discard(token))

    def claim_issue(self, now: datetime, min_gap: timedelta) -> bool:
        return self._run(lambda c: c.claim_issue(now, min_gap))

    def next_issue_at(self, now: datetime) -> datetime | None:
        return self._run(lambda c: c.next_issue_at(now))


# ---- 제공자 ---------------------------------------------------------------------------------


class CachedTokenProvider:
    """캐시 우선 토큰 제공자. 스레드 안전.

    - issuer=None 이면 읽기 전용(서비스용). 캐시에 쓸 토큰이 없으면 `TokenUnavailable`
    - 발급 간격에 막히면 on_throttle="raise" 는 `TokenIssueThrottled`, "wait" 는 풀릴 때까지 잔다
      (max_wait 초까지). 어느 쪽이든 기존 토큰이 살아 있으면 그것을 돌려준다
    """

    def __init__(
        self,
        cache: TokenCache,
        owner: str,
        issuer: TokenIssuer | None = None,
        *,
        refresh_margin: timedelta = REFRESH_MARGIN,
        min_issue_gap: timedelta = ISSUE_MIN_GAP,
        min_valid: timedelta = MIN_VALID,
        on_throttle: Literal["raise", "wait"] = "raise",
        max_wait: timedelta = timedelta(seconds=130),
        now: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cache = cache
        self._owner = owner
        self._issuer = issuer
        self._margin = refresh_margin
        self._gap = min_issue_gap
        self._min_valid = min_valid
        self._on_throttle = on_throttle
        self._max_wait = max_wait
        self._now = now
        self._sleep = sleep
        self._mem: TokenRecord | None = None
        self._lock = threading.Lock()

    def _usable(self, rec: TokenRecord | None, now: datetime) -> TokenRecord | None:
        if rec is None or rec.owner != self._owner or rec.expires_at - now <= self._min_valid:
            return None
        return rec

    def _fresh(self, rec: TokenRecord | None, now: datetime) -> bool:
        return rec is not None and rec.expires_at - now > self._margin

    def get(self) -> str:
        with self._lock:
            now = self._now()
            mem = self._usable(self._mem, now)
            if mem is not None and self._fresh(mem, now):
                return mem.token
            cands = [r for r in (mem, self._usable(self._cache.load(), now)) if r is not None]
            rec = max(cands, key=lambda r: r.expires_at) if cands else None
            self._mem = rec
            if rec is not None and (self._fresh(rec, now) or self._issuer is None):
                return rec.token  # 읽기 전용은 만료 임박이어도 만료 전까지 쓴다 (갱신은 auth 몫)
            if self._issuer is None:
                raise TokenUnavailable("캐시에 쓸 토큰이 없다 — 발급은 auth 서비스가 한다")
            return self._issue(self._issuer, now, rec)

    def _issue(self, issuer: TokenIssuer, now: datetime, fallback: TokenRecord | None) -> str:
        """발급 자리를 잡으면 발급, 못 잡으면(또는 KIS 가 EGW00133) 기존 토큰·대기·예외 중 하나."""
        give_up = now + self._max_wait
        while True:
            if self._cache.claim_issue(now, self._gap):
                try:
                    got = issuer.issue()
                except TokenIssueThrottled:
                    pass  # 같은 앱키를 다른 프로그램이 방금 발급 — 간격 뒤에
                except TokenIssueError:
                    if fallback is not None:
                        return fallback.token
                    raise
                else:
                    rec = TokenRecord(
                        access_token=got.access_token,
                        expires_at=got.expires_at,
                        issued_at=got.issued_at,
                        owner=self._owner,
                    )
                    self._cache.store(rec, self._now())
                    self._mem = rec
                    return rec.token
            if fallback is not None:
                return fallback.token  # 갱신은 다음 기회에
            retry_at = self._cache.next_issue_at(now) or now + self._gap
            if self._on_throttle == "raise" or retry_at > give_up:
                raise TokenIssueThrottled(retry_at)
            self._sleep(max((retry_at - now).total_seconds(), 0.0) + 0.05)
            now = self._now()
            # 그 사이 다른 프로세스가 받았을 수 있다
            rec = self._usable(self._cache.load(), now)
            if rec is not None:
                self._mem = rec
                if self._fresh(rec, now):
                    return rec.token
                fallback = rec

    def invalidate(self) -> None:
        with self._lock:
            if self._mem is not None:
                self._cache.discard(self._mem.token)
                self._mem = None


def default_token_provider(settings: Settings, http: httpx.Client) -> CachedTokenProvider:
    """probe·KisClient 기본값. 캐시에 쓸 토큰이 없을 때만 발급한다.

    REDIS_URL 이 있으면 Redis 캐시(auth 서비스와 공유)를 먼저 보고, 연결이 안 되면 파일 캐시로.
    없으면 파일 캐시만. 생성만으로는 Redis 에 접속하지 않는다.
    """
    key, sec = settings.kis_app_key, settings.kis_app_secret
    if key is None or sec is None:
        raise TokenUnavailable("KIS_APP_KEY / KIS_APP_SECRET 가 없다")
    cache: TokenCache = FileTokenCache(settings.kis_token_cache_path)
    if settings.redis_url:
        r = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=REDIS_TIMEOUT_S,
            socket_timeout=REDIS_TIMEOUT_S,
        )
        cache = FallbackTokenCache(RedisTokenCache(r), cache)
    return CachedTokenProvider(
        cache,
        token_owner(settings.kis_base, key.get_secret_value()),
        KisTokenIssuer(http, key, sec),
    )

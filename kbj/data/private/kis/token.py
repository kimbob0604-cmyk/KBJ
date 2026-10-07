"""KIS 접근토큰·웹소켓 접속키 — Redis 캐시와 **읽기 전용** 제공자(설계 §3.2~§3.5, ADR 0004).

GEXLAB `data/kis/auth_client.py`(`TokenRecord`:97, `IssuedToken`:117, `TokenProvider`:123,
`TokenIssuer`:133, `TokenCache`:137, `_CLAIM_LUA`:318, `RedisTokenCache`:326,
`CachedTokenProvider`:436) + `services/auth/service.py:reader`:564 승격.

- 발급은 auth 서비스 한 곳만 한다(KIS 1분 1회 — 다른 곳이 발급하면 서로의 토큰을 무효화한다). 이
  모듈에는 발급 요청 코드가 없다. 발급자(`TokenIssuer`)는 `kbj/services/auth/issuer.py` 에만 있고,
  다른 프로세스는 `reader()`(발급자 없음)로 읽기만 한다.
- 캐시는 Redis 에만 둔다(`kis:token`·`kis:ws_key` — 키 이름은 GX 와 같아 legacy GX 가 같은 값을
  읽는다). GX 의 파일 캐시(`FileTokenCache`)·폴백(`FallbackTokenCache`)·기본 발급
  제공자(`default_token_provider`)는 옮기지 않았다(secrets.md — `KIS_TOKEN_CACHE_PATH` 삭제).
- 발급 간격은 61초에 한 번 이하(`EGW00133` 실측). 간격 기록(`…:issue_blocked_until`)을 Redis Lua 로
  인스턴스끼리 공유한다. 갱신은 만료 60분 전, 1분 미만 남은 토큰은 없는 것으로 본다.
- **거절 신고(설계 §3.4 가드)**: 읽기 전용 제공자의 `invalidate()` 는 키를 지우지 않는다(GX 는
  지웠다 — 고장 난 호출자 하나가 하루 1,400번 발급을 일으킬 수 있었다). 대신 `kis:token:rejected` 에
  신고만 하고, 그 토큰은 이 프로세스에서 다시 쓰지 않는다. 다시 발급할지는 auth 가 정한다(같은
  토큰은 한 번만 다시 발급 — 새 토큰이 들어오면 신고는 지난 것이 된다. 발급 10분 안의 신고는 다시
  발급하지 않고 critical 로 닫는다).
- Redis 를 못 읽으면 읽는 쪽은 `TokenUnavailable`(메모리에 살아 있는 토큰이 있으면 만료 전까지
  그것). 거절 신고를 Redis 에 못 쓴 `invalidate()` 도 `TokenUnavailable`.
- 토큰·앱키는 어디에도 출력하지 않는다. 예외 메시지에도 싣지 않고, 모델 repr 은 `SecretStr` 이
  가린다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, Protocol, runtime_checkable

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    SecretStr,
    ValidationError,
    field_serializer,
)
from redis import Redis
from redis.exceptions import RedisError, WatchError

from kbj.config.settings import Settings
from kbj.data.private.kis.credentials import KisCredentials, token_owner
from kbj.data.private.kis.errors import (
    TokenError,
    TokenIssueError,
    TokenIssueThrottled,
    TokenUnavailable,
)
from kbj.store.redis_keys import KIS_TOKEN, KIS_WS_KEY, issue_blocked, secret_digest

__all__ = [
    "CACHE_KEYS",
    "ISSUE_MIN_GAP",
    "MIN_VALID",
    "REFRESH_MARGIN",
    "REJECTED_TTL",
    "CachedTokenProvider",
    "CredentialName",
    "IssuedToken",
    "RedisTokenCache",
    "RejectionBoard",
    "RejectionReport",
    "TokenCache",
    "TokenError",
    "TokenIssueError",
    "TokenIssueThrottled",
    "TokenIssuer",
    "TokenProvider",
    "TokenRecord",
    "TokenUnavailable",
    "access_token",
    "reader",
    "rejected_key",
    "token_digest",
    "token_owner",
    "utcnow",
    "ws_approval_key",
]

ISSUE_MIN_GAP: Final = timedelta(seconds=61)  # KIS 발급 1분 1회(EGW00133) + 1초
REFRESH_MARGIN: Final = timedelta(minutes=60)  # 만료 60분 전 갱신
MIN_VALID: Final = timedelta(minutes=1)  # 이보다 적게 남은 토큰은 없는 것으로 본다
REJECTED_TTL: Final = timedelta(hours=1)  # 거절 신고 키 수명(redis_keys 표)
REDIS_TIMEOUT_S: Final = 2.0  # access_token() 이 스스로 여는 Redis 의 접속·응답 제한

CredentialName = Literal["token", "ws_key"]
CACHE_KEYS: Final[dict[CredentialName, str]] = {"token": KIS_TOKEN, "ws_key": KIS_WS_KEY}

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    """기본 시계(UTC aware). 시험·서비스는 `now=` 로 주입한다."""
    return datetime.now(tz=UTC)


def token_digest(token: str) -> str:
    """토큰의 sha256 앞 16자 — 신고·로그에 토큰 원문 대신 쓴다."""
    return secret_digest(token)


def rejected_key(key: str) -> str:
    """거절 신고 키 — `kis:token` → `kis:token:rejected`(`redis_keys.KIS_TOKEN_REJECTED`)."""
    if not key or any(c.isspace() for c in key):
        raise ValueError(f"key 는 공백 없는 비지 않은 문자열이어야 한다: {key!r}")
    return key + ":rejected"


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
        """KIS 가 토큰을 거절했을 때. 다음 get() 은 그 토큰을 다시 주지 않는다."""
        ...


class TokenIssuer(Protocol):
    """발급자 — 구현은 `kbj/services/auth/issuer.py` 에만 있다(ADR 0004)."""

    def issue(self) -> IssuedToken: ...


class TokenCache(Protocol):
    def load(self) -> TokenRecord | None: ...

    def store(self, rec: TokenRecord, now: datetime) -> None: ...

    def discard(self, token: str) -> None:
        """캐시의 토큰이 이 값일 때만 지운다(다른 프로세스가 새로 받은 토큰은 둔다)."""
        ...

    def claim_issue(self, now: datetime, min_gap: timedelta) -> bool:
        """발급 시도 자리를 원자적으로 잡는다. 직전 시도가 min_gap 안이면 False."""
        ...

    def next_issue_at(self, now: datetime) -> datetime | None: ...


@dataclass(frozen=True)
class RejectionReport:
    """거절 신고 한 건(토큰 원문 없음). handled_at 은 auth 가 '다시 발급하지 않기로' 닫은 시각."""

    token_sha16: str
    at: datetime
    by: str
    handled_at: datetime | None = None


@runtime_checkable
class RejectionBoard(Protocol):
    """거절 신고판(설계 §3.4). 읽는 쪽은 `report_rejected`, auth 는 `pending_rejection`·
    `close_rejection`.

    신고는 그 토큰이 캐시에 있는 동안 '걸려 있다' — auth 가 새 토큰으로 바꾸면 저절로 지난 신고가
    된다(재기동한 auth 도 걸린 신고를 다시 본다). 다시 발급하지 않기로 한 신고(발급 10분 안)만
    `close_rejection` 으로 닫는다 — 닫힌 신고는 같은 토큰의 새 신고로도 다시 열리지 않는다.
    """

    def report_rejected(self, token: str, now: datetime, by: str) -> bool:
        """이 토큰이 거절됐다고 신고. 같은 토큰의 신고가 이미 있으면 그대로 두고 False."""
        ...

    def pending_rejection(self, token: str) -> RejectionReport | None:
        """이 토큰에 대한 닫히지 않은 신고(없으면 None)."""
        ...

    def close_rejection(self, token: str, now: datetime) -> bool:
        """이 토큰의 신고를 닫는다(원자적 — 처음 닫은 쪽만 True)."""
        ...


# 발급 간격 자리: '지금 ≥ 기존 값'일 때만 새 값(다음 발급 가능 시각)을 쓴다 — GX 그대로
_CLAIM_LUA = """
local v = tonumber(redis.call('GET', KEYS[1]))
if v and tonumber(ARGV[1]) < v then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'PX', ARGV[3])
return 1
"""

# 거절 신고: 같은 토큰의 신고가 있으면 두고(처리 표시를 지우지 않게), 다른 토큰 것이면 바꾼다
_REPORT_LUA = """
if redis.call('HGET', KEYS[1], 'token_sha16') == ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
redis.call('HSET', KEYS[1], 'token_sha16', ARGV[1], 'at', ARGV[2], 'by', ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return 1
"""

# 신고 닫기: 이 토큰의 신고이고 아직 안 닫혔으면 닫은 시각을 적는다(처음 닫은 쪽만 1)
_CLOSE_LUA = """
if redis.call('HGET', KEYS[1], 'token_sha16') ~= ARGV[1] then return 0 end
if redis.call('HEXISTS', KEYS[1], 'handled_at') == 1 then return 0 end
redis.call('HSET', KEYS[1], 'handled_at', ARGV[2])
return 1
"""


def _text(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def _us(t: datetime) -> int:
    return round(t.timestamp() * 1_000_000)


class RedisTokenCache:
    """Redis 캐시 `kis:token`(TTL = 남은 수명), 발급 간격 키 `kis:token:issue_blocked_until`,
    거절 신고 키 `kis:token:rejected`(hash, 1시간).

    간격 판정은 넘겨받은 시각으로 한다(가짜 시계 시험). Redis TTL 은 청소용이다.
    """

    def __init__(self, redis: Redis, key: str = KIS_TOKEN) -> None:
        self._r = redis
        self.key = key
        self.issue_key = issue_blocked(key)
        self.rejected_key = rejected_key(key)
        self._claim = redis.register_script(_CLAIM_LUA)
        self._report = redis.register_script(_REPORT_LUA)
        self._close = redis.register_script(_CLOSE_LUA)

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

    # ---- 거절 신고(설계 §3.4) ---------------------------------------------------------------

    def report_rejected(self, token: str, now: datetime, by: str) -> bool:
        res: Any = self._report(
            keys=[self.rejected_key],
            args=[
                token_digest(token),
                now.astimezone(UTC).isoformat(),
                by or "unknown",
                int(REJECTED_TTL.total_seconds()),
            ],
        )
        return int(res) == 1

    def pending_rejection(self, token: str) -> RejectionReport | None:
        rep = self.rejection()
        if rep is None or rep.token_sha16 != token_digest(token) or rep.handled_at is not None:
            return None
        return rep

    def close_rejection(self, token: str, now: datetime) -> bool:
        res: Any = self._close(
            keys=[self.rejected_key], args=[token_digest(token), now.astimezone(UTC).isoformat()]
        )
        return int(res) == 1

    def rejection(self) -> RejectionReport | None:
        """지금 있는 신고(닫혔든 아니든) — 운영 화면·시험용. 형식이 깨졌으면 None."""
        raw: Any = self._r.hgetall(self.rejected_key)
        if not raw:
            return None
        got = {_text(k): _text(v) for k, v in raw.items()}
        try:
            handled = got.get("handled_at")
            return RejectionReport(
                got["token_sha16"],
                datetime.fromisoformat(got["at"]),
                got.get("by", ""),
                datetime.fromisoformat(handled) if handled else None,
            )
        except (KeyError, ValueError):
            log.warning("거절 신고(%s) 형식이 깨졌다 — 없는 것으로 본다", self.rejected_key)
            return None


class CachedTokenProvider:
    """캐시 우선 토큰 제공자. 스레드 안전.

    - issuer=None 이면 **읽기 전용**(auth 밖 모든 프로세스). 캐시에 쓸 토큰이 없으면
      `TokenUnavailable`. 만료 임박이어도 만료 1분 전까지는 돌려준다(갱신은 auth 몫)
    - issuer 가 있으면(auth 안에서만 만들 수 있다 — 발급자는 auth 에만 있다) 캐시에 없거나 만료 60분
      안일 때 발급한다. 발급 간격에 막히면 on_throttle="raise" 는 `TokenIssueThrottled`, "wait" 는
      풀릴 때까지 잔다(max_wait 초까지). 어느 쪽이든 기존 토큰이 살아 있으면 그것을 돌려준다
    - `invalidate()`: KIS 가 거절한 토큰은 이 제공자가 다시 주지 않는다. 읽기 전용은 캐시를 지우지
      않고 거절 신고만 한다(설계 §3.4 가드). 발급자가 있는 제공자는 GX 처럼 그 토큰일 때만 지운다
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
        by: str = "",
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
        self._by = by
        self._mem: TokenRecord | None = None
        self._rejected: str | None = None  # KIS 가 거절한 토큰의 해시 — 다시 주지 않는다
        self._lock = threading.Lock()

    @property
    def read_only(self) -> bool:
        return self._issuer is None

    def _refused(self, rec: TokenRecord) -> bool:
        return self._rejected is not None and token_digest(rec.token) == self._rejected

    def _usable(self, rec: TokenRecord | None, now: datetime) -> TokenRecord | None:
        if rec is None or rec.owner != self._owner or rec.expires_at - now <= self._min_valid:
            return None
        if self._refused(rec):
            return None
        return rec

    def _fresh(self, rec: TokenRecord | None, now: datetime) -> bool:
        return rec is not None and rec.expires_at - now > self._margin

    def _load(self, mem: TokenRecord | None) -> TokenRecord | None:
        try:
            return self._cache.load()
        except RedisError as e:
            if self._issuer is not None:
                raise  # 발급하는 쪽(auth)은 캐시 없이 돌지 않는다
            if mem is not None:
                log.warning(
                    "토큰 캐시(Redis) 읽기 실패 — 메모리의 토큰을 만료 전까지 쓴다 (%s)",
                    type(e).__name__,
                )
                return None
            raise TokenUnavailable(f"토큰 캐시(Redis) 읽기 실패: {type(e).__name__}") from None

    def get(self) -> str:
        with self._lock:
            now = self._now()
            mem = self._usable(self._mem, now)
            if mem is not None and self._fresh(mem, now):
                return mem.token
            loaded = self._load(mem)
            cands = [r for r in (mem, self._usable(loaded, now)) if r is not None]
            rec = max(cands, key=lambda r: r.expires_at) if cands else None
            self._mem = rec
            if rec is not None and (self._fresh(rec, now) or self._issuer is None):
                return rec.token  # 읽기 전용은 만료 임박이어도 만료 전까지 쓴다 (갱신은 auth 몫)
            if self._issuer is None:
                if loaded is not None and self._refused(loaded):
                    raise TokenUnavailable(
                        "캐시에는 KIS 가 거절한 토큰뿐이다 — 거절 신고함, auth 재발급 대기"
                    )
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
                    self._rejected = None  # 새로 받은 값은 거절 기억과 무관
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
            mem = self._mem
            if mem is None:
                return
            self._mem = None
            self._rejected = token_digest(mem.token)
            if self._issuer is not None:
                self._cache.discard(mem.token)  # 발급자가 있는 제공자(auth)만 지운다 — GX 그대로
                return
            if isinstance(self._cache, RejectionBoard):
                try:
                    self._cache.report_rejected(mem.token, self._now(), self._by)
                except RedisError as e:
                    # 이 프로세스는 이미 그 토큰을 버렸다. 신고를 못 했으니 auth 는 만료 갱신 때까지
                    # 모른다 — 읽는 쪽 Redis 장애와 같게 TokenUnavailable 로 그 호출을 끝낸다(§3.4)
                    raise TokenUnavailable(
                        f"거절 신고 실패 — 토큰 캐시(Redis) 쓰기 실패: {type(e).__name__}"
                    ) from None


def reader(
    redis: Redis,
    creds: KisCredentials | Settings,
    name: CredentialName = "token",
    *,
    now: Callable[[], datetime] = utcnow,
    by: str | None = None,
) -> CachedTokenProvider:
    """읽기 전용 제공자 — **발급하지 않는다**(발급은 auth 만, ADR 0004).

    `reader(r, creds).get()` 은 접근토큰, `reader(r, creds, "ws_key").get()` 은 웹소켓 접속키. 만료
    임박이어도 만료 1분 전까지 돌려주고, 없으면 `TokenUnavailable`. KIS 가 거절하면 `invalidate()`
    (신고만 — 키는 auth 가 바꾼다). creds 자리에 `Settings` 를 주면 거기서 자격을 읽는다(없으면
    `TokenUnavailable`). now 는 만료 판정 시계 — 시험은 서비스와 같은 가짜 시계를 넘긴다. by 는 거절
    신고에 남길 이름(기본: 설정의 서비스 이름).
    """
    if isinstance(creds, Settings):
        who = by if by is not None else (creds.service or "unknown")
        creds = KisCredentials.from_settings(creds)
    else:
        who = by if by is not None else "unknown"
    return CachedTokenProvider(
        RedisTokenCache(redis, CACHE_KEYS[name]), creds.owner, now=now, by=who
    )


# ---- 프로세스당 reader 하나 (모듈 수준 편의) --------------------------------------------------

_PROCESS_READERS: dict[tuple[int, str, str], CachedTokenProvider] = {}
_PROCESS_REDIS: dict[str, Redis] = {}
_PROCESS_LOCK = threading.Lock()


def _wall_clock() -> datetime:
    """`utcnow` 를 부를 때마다 모듈에서 찾는다(시험이 이 모듈의 `utcnow` 를 바꿔 끼울 수 있게)."""
    return utcnow()


def _process_reader(
    name: CredentialName, settings: Settings | None, redis: Redis | None
) -> CachedTokenProvider:
    s = settings if settings is not None else Settings()
    creds = KisCredentials.from_settings(s)
    with _PROCESS_LOCK:
        r = redis
        if r is None:
            if s.redis_url is None:
                raise TokenUnavailable("KBJ_REDIS_URL 이 없다 — 토큰은 Redis 에서만 읽는다")
            url = s.redis_url.get_secret_value()
            ident = secret_digest(url)  # 접속 문자열(비밀번호 포함)은 키로 남기지 않는다
            r = _PROCESS_REDIS.get(ident)
            if r is None:
                r = Redis.from_url(
                    url, socket_connect_timeout=REDIS_TIMEOUT_S, socket_timeout=REDIS_TIMEOUT_S
                )
                _PROCESS_REDIS[ident] = r
        key = (id(r), creds.owner, name)
        prov = _PROCESS_READERS.get(key)
        if prov is None:
            prov = reader(r, creds, name, now=_wall_clock, by=s.service or "unknown")
            _PROCESS_READERS[key] = prov
        return prov


def access_token(*, settings: Settings | None = None, redis: Redis | None = None) -> str:
    """이 프로세스의 접근토큰(읽기만). 없으면 `TokenUnavailable` — 발급은 auth 가 한다."""
    return _process_reader("token", settings, redis).get()


def ws_approval_key(*, settings: Settings | None = None, redis: Redis | None = None) -> str:
    """이 프로세스의 웹소켓 접속키(읽기만). 없으면 `TokenUnavailable`."""
    return _process_reader("ws_key", settings, redis).get()

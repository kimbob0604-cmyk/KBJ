"""로그인(사용자 1명)·세션·CSRF·무차별 대입 잠금(docs/p3_design.md §5.4, D-P3-4).

- 자격: `KBJ_WEB_USER` + `KBJ_WEB_PASSWORD_HASH`. **기본값 없음** — 둘 중 하나라도 없거나
  해시 형식이
  틀리면 로그인은 503(`login_not_configured`). 형식 오류 문구에 값을 싣지 않는다(설정 검증이 아니라
  여기서 값 없이 실패시킨다 — 묶음 M 요청).
- 해시: 표준 라이브러리 `hashlib.scrypt` — `scrypt$n=<N>$r=<r>$p=<p>$<salt b64>$<dk b64>`
  (솔트 16바이트,
  키 64바이트). 비교는 `hmac.compare_digest`. 이름이 틀려도 같은 scrypt 비용을 쓴다(시간 차 없음).
- 세션: `sid = secrets.token_urlsafe(32)`(쿠키에만), Redis `web:session:<sha256(sid) 32자>` =
  `{user, csrf, created, last_seen}` TTL `web_session_ttl_h`(고정 만료 — 활동해도 늘리지 않는다).
  로그인 때마다 새 sid(고정 방지), 로그아웃 = 삭제.
- 잠금: IP 별 `web:login_fail:<sha256(ip) 16자>` 15분 창 5회 → 그 IP 15분 잠금, 전체 1시간 20회 →
  `web:login_lock` 30분. 실패·잠금 모두 같은 401 문구(+ 잠금이면 Retry-After).
- 로그: 사용자 이름·IP·쿠키·비밀번호·해시를 남기지 않는다 — 실패 수와 IP 해시 앞 8자만.
- 시계: 세션 기록의 시각은 주입한 `now()` — 만료는 Redis TTL 이 맡는다.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import SecretStr
from redis import Redis

from kbj.store.redis_keys import (
    WEB_LOGIN_FAIL_ALL,
    WEB_LOGIN_LOCK,
    web_digest,
    web_login_fail_key,
    web_session_key,
)

__all__ = [
    "COOKIE_DEV",
    "COOKIE_SECURE",
    "CSRF_HEADER",
    "Credentials",
    "LoginGuard",
    "LoginNotConfigured",
    "ScryptParams",
    "SessionData",
    "SessionStore",
    "hash_password",
    "ip_digest",
    "parse_hash",
    "verify_password",
]

log = logging.getLogger(__name__)

COOKIE_SECURE: Final = "__Host-kbj_session"  # Secure·Path=/·Domain 없음(브라우저가 강제)
COOKIE_DEV: Final = "kbj_session"  # web_cookie_secure=false — 루프백 개발에서만(Settings 검증)
CSRF_HEADER: Final = "X-KBJ-CSRF"

SALT_BYTES: Final = 16
DK_BYTES: Final = 64
DEFAULT_N: Final = 2**15
DEFAULT_R: Final = 8
DEFAULT_P: Final = 1
_MIN_N: Final = 2**10  # 시험 픽스처의 낮은 비용까지만(그 아래는 거부)
_MAX_N: Final = 2**20

IP_WINDOW_S: Final = 15 * 60
IP_MAX_FAILS: Final = 5
IP_LOCK_S: Final = 15 * 60
ALL_WINDOW_S: Final = 60 * 60
ALL_MAX_FAILS: Final = 20
ALL_LOCK_S: Final = 30 * 60

_HASH = re.compile(
    r"scrypt\$n=(?P<n>[0-9]{1,8})\$r=(?P<r>[0-9]{1,3})\$p=(?P<p>[0-9]{1,3})"
    r"\$(?P<salt>[A-Za-z0-9+/=]{16,64})\$(?P<dk>[A-Za-z0-9+/=]{16,128})"
)


class LoginNotConfigured(RuntimeError):
    """사용자·해시가 없거나 해시 형식이 틀렸다(문구에 값 없음)."""


@dataclass(frozen=True)
class ScryptParams:
    n: int
    r: int
    p: int
    salt: bytes
    dk: bytes


def _maxmem(n: int, r: int, p: int) -> int:
    return 128 * n * r * (p + 2) + 1024 * 1024


def _derive(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_maxmem(n, r, p), dklen=dklen
    )


def hash_password(
    password: str,
    *,
    n: int = DEFAULT_N,
    r: int = DEFAULT_R,
    p: int = DEFAULT_P,
    salt: bytes | None = None,
) -> str:
    """비밀번호 → `scrypt$n=…$r=…$p=…$<salt>$<dk>`. 비밀번호는 비면 안 된다."""
    if not password:
        raise ValueError("빈 비밀번호는 받지 않는다")
    if n < _MIN_N or n > _MAX_N or n & (n - 1):
        raise ValueError("n 은 2의 거듭제곱(2**10 ~ 2**20)")
    if not 1 <= r <= 64 or not 1 <= p <= 16:
        raise ValueError("r·p 범위 밖")
    s = salt if salt is not None else secrets.token_bytes(SALT_BYTES)
    if len(s) != SALT_BYTES:
        raise ValueError(f"솔트는 {SALT_BYTES}바이트")
    dk = _derive(password, s, n, r, p, DK_BYTES)
    b64 = base64.b64encode
    return f"scrypt$n={n}$r={r}$p={p}${b64(s).decode()}${b64(dk).decode()}"


def parse_hash(encoded: str) -> ScryptParams | None:
    """형식이 맞으면 매개변수, 아니면 None(예외 문구에 값을 싣지 않으려고 None 으로만 알린다)."""
    m = _HASH.fullmatch(encoded.strip())
    if m is None:
        return None
    n, r, p = int(m["n"]), int(m["r"]), int(m["p"])
    if n < _MIN_N or n > _MAX_N or n & (n - 1) or not 1 <= r <= 64 or not 1 <= p <= 16:
        return None
    try:
        salt = base64.b64decode(m["salt"], validate=True)
        dk = base64.b64decode(m["dk"], validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(salt) != SALT_BYTES or len(dk) < 16:
        return None
    return ScryptParams(n, r, p, salt, dk)


def _check(password: str, params: ScryptParams) -> bool:
    got = _derive(password, params.salt, params.n, params.r, params.p, len(params.dk))
    return hmac.compare_digest(got, params.dk)


def verify_password(password: str, encoded: str) -> bool:
    """상수 시간 비교. 형식이 틀린 해시는 False."""
    params = parse_hash(encoded)
    return False if params is None else _check(password, params)


@dataclass(frozen=True)
class Credentials:
    """로그인 자격(설정에서 읽은 사용자 이름·해시 매개변수). repr 에 값을 싣지 않는다."""

    _user: str
    _params: ScryptParams

    def __repr__(self) -> str:
        return "Credentials(***)"

    @classmethod
    def from_settings(cls, user: str | None, password_hash: SecretStr | None) -> Credentials:
        if not user or password_hash is None or not password_hash.get_secret_value():
            raise LoginNotConfigured("KBJ_WEB_USER·KBJ_WEB_PASSWORD_HASH 가 없다")
        params = parse_hash(password_hash.get_secret_value())
        if params is None:
            raise LoginNotConfigured("KBJ_WEB_PASSWORD_HASH 형식 오류(값은 싣지 않는다)")
        return cls(user, params)

    def check(self, username: str, password: str) -> bool:
        """이름이 틀려도 같은 scrypt 계산을 한다(시간으로 이름을 알아내지 못하게)."""
        name_ok = hmac.compare_digest(username.encode("utf-8"), self._user.encode("utf-8"))
        pw_ok = _check(password, self._params)
        return name_ok and pw_ok

    @property
    def user(self) -> str:
        return self._user


def ip_digest(ip: str | None) -> str:
    """IP → sha256 앞 16자(원문은 키·로그에 남기지 않는다). 모르면 'unknown' 의 해시."""
    return web_digest(ip or "unknown", 16)


def _int(v: object) -> int:
    """Redis 응답(bytes·str·int) → int. 동기 클라이언트만 쓴다(Awaitable 이 오면 형 오류)."""
    if isinstance(v, bool):
        raise TypeError("bool 응답")
    if isinstance(v, int):
        return v
    if isinstance(v, bytes | str):
        return int(v)
    raise TypeError(f"Redis 응답 형식: {type(v).__name__}")


class LoginGuard:
    """무차별 대입 잠금 — 카운터는 Redis(만료는 Redis TTL)."""

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    def locked_for(self, ipd: str) -> int | None:
        """잠겨 있으면 남은 초(Retry-After), 아니면 None."""
        if self._r.exists(WEB_LOGIN_LOCK):
            return max(1, _int(self._r.ttl(WEB_LOGIN_LOCK)))
        key = web_login_fail_key(ipd)
        raw = self._r.get(key)
        if raw is not None and _int(raw) >= IP_MAX_FAILS:
            return max(1, _int(self._r.ttl(key)))
        return None

    def failed(self, ipd: str) -> int:
        """실패 하나를 센다. 그 IP 의 창 안 실패 수를 돌려준다."""
        key = web_login_fail_key(ipd)
        n = _int(self._r.incr(key))
        if n == 1:
            self._r.expire(key, IP_WINDOW_S)
        if n >= IP_MAX_FAILS:
            self._r.expire(key, IP_LOCK_S)  # 잠금은 마지막 실패부터 15분
        total = _int(self._r.incr(WEB_LOGIN_FAIL_ALL))
        if total == 1:
            self._r.expire(WEB_LOGIN_FAIL_ALL, ALL_WINDOW_S)
        if total >= ALL_MAX_FAILS:
            self._r.set(WEB_LOGIN_LOCK, "1", ex=ALL_LOCK_S)
            self._r.delete(WEB_LOGIN_FAIL_ALL)
            log.warning("로그인 전체 잠금 %d초(1시간 실패 %d회)", ALL_LOCK_S, total)
        log.warning("로그인 실패 ip=%s… 창 안 %d회", ipd[:8], n)
        return n

    def succeeded(self, ipd: str) -> None:
        self._r.delete(web_login_fail_key(ipd))


@dataclass(frozen=True)
class SessionData:
    user: str
    csrf: str
    created: str
    last_seen: str

    def __repr__(self) -> str:  # csrf 를 로그에 싣지 않는다
        return f"SessionData(user=***, created={self.created})"


class SessionStore:
    """Redis 서버 세션. sid 원문은 키에 넣지 않는다(sha256 앞 32자)."""

    def __init__(self, redis: Redis, *, ttl_s: int, now: Callable[[], datetime]) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s 는 0 보다 커야 한다")
        self._r = redis
        self._ttl = ttl_s
        self._now = now

    @property
    def ttl_s(self) -> int:
        return self._ttl

    @staticmethod
    def _key(sid: str) -> str:
        return web_session_key(web_digest(sid, 32))

    def create(self, user: str) -> tuple[str, SessionData]:
        sid = secrets.token_urlsafe(32)
        ts = self._now().isoformat()
        data = SessionData(user=user, csrf=secrets.token_urlsafe(32), created=ts, last_seen=ts)
        body = json.dumps(data.__dict__, ensure_ascii=False)
        self._r.set(self._key(sid), body, ex=self._ttl)
        return sid, data

    def get(self, sid: str | None) -> SessionData | None:
        if not sid or len(sid) > 256:
            return None
        raw = self._r.get(self._key(sid))
        if raw is None:
            return None
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        try:
            d = json.loads(text)
            return SessionData(
                user=str(d["user"]),
                csrf=str(d["csrf"]),
                created=str(d["created"]),
                last_seen=str(d.get("last_seen", d["created"])),
            )
        except (ValueError, KeyError, TypeError):
            # 깨진 세션 값은 지우고 없는 것으로 — 사유만 남긴다(값은 싣지 않는다)
            log.warning("세션 값 형식 오류 — 지운다")
            self._r.delete(self._key(sid))
            return None

    def drop(self, sid: str | None) -> bool:
        if not sid or len(sid) > 256:
            return False
        return bool(self._r.delete(self._key(sid)))


def csrf_ok(session: SessionData, header: str | None) -> bool:
    """세션 CSRF 토큰과 헤더를 상수 시간으로 비교."""
    if not header:
        return False
    return hmac.compare_digest(header.encode("utf-8"), session.csrf.encode("utf-8"))

"""auth 서비스 — KIS 접근토큰·웹소켓 접속키의 유일한 발급자 (PLAN §2.5·§4.1 auth, 설계 §2).

- Redis `kis:token`(접근토큰)·`kis:ws_key`(웹소켓 접속키)를 보고 만료가 60분 안으로 다가오면
  갱신한다(PLAN: 만료 1시간 전 갱신). 다른 서비스는 `reader()` 로 읽기만 한다
- 발급 시도는 자격마다 61초에 한 번 이하(KIS 1분 1회, `EGW00133` 실측). 간격은 캐시의
  `claim_issue`(Redis Lua)로 인스턴스끼리 공유하고, 이 인스턴스도 직전 시도 시각을 따로 지킨다 —
  Redis 가 비워져도 몰아쳐 발급하지 않는다
- 갱신이 실패해도 기존 값이 살아 있으면 다른 서비스는 그것을 계속 쓴다. 남은 시간이 10분 아래인데도
  실패 중이면 `*_expiring` health(1분에 한 번 이하). 1분 1회 제한에 걸린 것(다른 발급자의 공유
  간격, KIS `EGW00133`)은 61초 안에 풀리므로 한 간격을 기다려 준 뒤에도 못 채웠을 때만 경고한다 —
  KIS 가 접근토큰·접속키에 제한을 함께 건다면(미실측) 기동 직후 접속키가 한 번 막히는 것은 정상이다
- Redis 를 못 쓰면 발급하지 않는다(받아도 둘 곳이 없고 발급 간격만 쓴다)
- 발급 요청도 앱키당 REST 레이트리미터에서 P0 허가를 받는다(설계 §2 "모든 KIS REST 호출은
  레이트리미터를 거친다"). oauth 가 초당 한도에 들어가는지는 **미실측(확인 필요)** — 들어간다고
  보는 쪽이 보수적이고, 발급은 11~23시간에 한 번이라 버킷에 주는 부담이 없다
- 토큰·앱키는 이벤트·로그 어디에도 싣지 않는다. detail 은 마지막에 한 번 더 가린다. 발급자
  오류 문구는 잘린 채 올 수 있어(`KisTokenIssuer` 는 200자에서 자른 뒤 가린다) 끝에 남은
  앱키·시크릿 앞부분도 가린다
- `run_forever` 가 30초마다 step 한다. 갱신을 못 한 자격이 있으면 그 다음 시도 시각(61초 뒤)에
  맞춰 깬다 — 재시도가 61초 이후 첫 주기(90초)로 밀리지 않게. compose 진입점은
  `python -m services.auth` — step 마다 Redis 하트비트(`health:heartbeat:auth`), DATABASE_URL 이
  있으면 health 를 health_events 에도(디스크 스풀 포함)
- 웹소켓 접속키(`POST /oauth2/Approval`)는 **미실측(확인 필요)**: 요청·응답 형식은 KIS 공식 샘플
  기준, 응답에 만료가 없어 수명은 보수적 가정(12시간), 발급 간격은 접근토큰과 같은 61초로 둔다
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal, cast

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError
from redis import Redis
from redis.exceptions import RedisError

from config.settings import Settings
from core.calendar import TradingCalendar, state_at
from data.kis.auth_client import (
    ISSUE_MIN_GAP,
    ISSUE_THROTTLED_CODE,
    KST,
    MIN_VALID,
    REFRESH_MARGIN,
    TOKEN_PATH,
    CachedTokenProvider,
    IssuedToken,
    KisTokenIssuer,
    RedisTokenCache,
    TokenCache,
    TokenIssueError,
    TokenIssuer,
    TokenIssueThrottled,
    TokenRecord,
    TokenUnavailable,
    token_owner,
    utcnow,
)
from data.kis.ratelimit import Priority, RateLimiter, RateLimitTimeout, RedisRateLimiter
from services.auth.health import HealthEvent, HealthSink, LogHealthSink, Severity, Tagger

__all__ = [
    "APPROVAL_PATH",
    "EXPIRING_MARGIN",
    "TOKEN_KEY",
    "WS_KEY_ASSUMED_LIFE",
    "WS_KEY_KEY",
    "Action",
    "AuthService",
    "AuthStatus",
    "Credential",
    "CredentialName",
    "CredentialStatus",
    "KisApprovalKeyIssuer",
    "build_auth_service",
    "calendar_tagger",
    "heartbeat_status",
    "hide_cut_tail",
    "next_wait",
    "reader",
    "redact",
    "serve",
]

TOKEN_KEY = "kis:token"  # noqa: S105 — Redis 키 이름일 뿐 비밀 아님
WS_KEY_KEY = "kis:ws_key"
APPROVAL_PATH = "/oauth2/Approval"
# 확인 필요: 접속키 수명 미실측(응답에 만료 없음). 짧게 잡으면 발급이 잦을 뿐이고, 길게 잡았다가
# 실제가 더 짧으면 ws-gateway 재접속이 막힌다 → 12시간(11시간마다 갱신)
WS_KEY_ASSUMED_LIFE = timedelta(hours=12)
EXPIRING_MARGIN = timedelta(minutes=10)  # 이보다 적게 남았는데 실패 중이면 경고 (설계 §2)
STEP_INTERVAL_S = 30.0
PERMIT_TIMEOUT_S = 5.0  # 레이트리미터 P0 허가 대기 상한. 넘으면 이번 시도는 실패(61초 뒤 다시)
RETRY_WAIT_MIN_S = 0.05  # 다음 시도가 코앞이어도 이만큼은 잔다 (시계가 조금 일찍 깨도 헛돌지 않게)
ERROR_TEXT_MAX = 200  # 발급자 오류 문구에 싣는 KIS 오류 본문 길이 (가린 뒤 자른다)
# 잘린 문구 끝이 비밀값 앞부분과 이만큼 이상 겹치면 가린다. 2자 이하는 우연 일치와 구분할 수 없어
# 두고(오류 코드 끝자리가 가려지는 편이 더 해롭다), 그만큼은 새도 비밀값을 짐작할 수 없다
CUT_TAIL_MIN = 3

CredentialName = Literal["token", "ws_key"]
_CACHE_KEYS: dict[CredentialName, str] = {"token": TOKEN_KEY, "ws_key": WS_KEY_KEY}
_OAUTH_PATHS: dict[CredentialName, str] = {"token": TOKEN_PATH, "ws_key": APPROVAL_PATH}
Action = Literal["fresh", "refreshed", "failed", "throttled", "waiting", "error"]
# fresh      갱신 필요 없음
# refreshed  이번 step 에 새로 받아 캐시에 넣었다
# failed     이번 step 에 발급을 시도했고 실패했다 (KIS 쪽 EGW00133 포함)
# throttled  다른 인스턴스·프로세스가 61초 안에 발급 자리를 잡았다 (공유 간격)
# waiting    이 인스턴스의 직전 시도가 61초 안이다
# error      캐시(Redis)를 못 썼거나 예상 밖 예외 — 발급하지 않았거나 받은 값을 잃었다

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Credential:
    """발급·캐시 한 쌍. owner 는 `token_owner(base_url, app_key)` — 다른 앱키 값은 쓰지 않는다."""

    name: CredentialName
    cache: TokenCache
    issuer: TokenIssuer
    owner: str


@dataclass(frozen=True)
class CredentialStatus:
    """step 한 번의 자격별 결과. 값(토큰)은 담지 않는다."""

    name: CredentialName
    action: Action
    expires_at: datetime | None  # 캐시에 살아 있는 값의 만료 (없으면 None)
    next_attempt_at: datetime | None = None  # 갱신을 못 했을 때 다음 시도 가능 시각
    expiring: bool = False  # 갱신을 못 했고 남은 시간이 경고 여유(10분)보다 적다
    detail: str = ""  # 가린 사유

    @property
    def live(self) -> bool:
        return self.expires_at is not None


@dataclass(frozen=True)
class AuthStatus:
    at: datetime
    credentials: tuple[CredentialStatus, ...]

    def __getitem__(self, name: str) -> CredentialStatus:
        for c in self.credentials:
            if c.name == name:
                return c
        raise KeyError(name)

    @property
    def ok(self) -> bool:
        """모든 자격이 살아 있고 만료 임박 경고가 없다."""
        return all(c.live and not c.expiring for c in self.credentials)


def next_wait(status: AuthStatus, interval: float) -> float:
    """다음 step 까지 잘 시간(초). 갱신을 못 한 자격의 다음 시도 시각이 interval 보다 가까우면 그때.

    이미 지난 시각(캐시 오류가 들고 온 옛 간격 등)은 보지 않는다 — interval 로 쉰다.
    step 에 걸린 시간만큼 늦게 깨므로 61초보다 이르게 시도하는 일은 없다.
    """
    wait = interval
    for c in status.credentials:
        t = c.next_attempt_at
        if t is not None and t > status.at:
            wait = min(wait, max((t - status.at).total_seconds(), RETRY_WAIT_MIN_S))
    return wait


def _kst(t: datetime) -> str:
    return t.astimezone(KST).strftime("%m-%d %H:%M:%S KST")


def _left(d: timedelta) -> str:
    s = max(int(d.total_seconds()), 0)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}시간 {m}분" if h else f"{m}분 {sec}초"


class AuthService:
    """자격마다 step 한 번에 많아야 한 번 발급한다. 시각은 밖에서 넣는다(가짜 시계 테스트).

    다른 서비스는 발급자 없는 `CachedTokenProvider(RedisTokenCache(r, key), owner)` 로 읽기만 한다.
    """

    def __init__(
        self,
        credentials: Sequence[Credential],
        sink: HealthSink,
        *,
        refresh_margin: timedelta = REFRESH_MARGIN,
        retry_gap: timedelta = ISSUE_MIN_GAP,
        expiring_margin: timedelta = EXPIRING_MARGIN,
        min_valid: timedelta = MIN_VALID,
        secrets: Iterable[str] = (),
        limiter: RateLimiter | None = None,
        permit_timeout: float = PERMIT_TIMEOUT_S,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        names = [c.name for c in credentials]
        if not names or len(set(names)) != len(names):
            raise ValueError("자격 이름은 하나 이상, 서로 달라야 한다")
        if retry_gap < ISSUE_MIN_GAP:
            raise ValueError("발급 재시도 간격은 61초 이상 (KIS 1분 1회)")
        if not timedelta(0) < expiring_margin < refresh_margin:
            raise ValueError("0 < expiring_margin < refresh_margin 이어야 한다")
        if permit_timeout <= 0:
            raise ValueError("permit_timeout 은 0보다 커야 한다 (무기한 대기 금지)")
        self._creds = tuple(credentials)
        self._sink = sink
        self._margin = refresh_margin
        self._gap = retry_gap
        self._expiring = expiring_margin
        self._min_valid = min_valid
        self._secrets = [s for s in secrets if s]
        self._limiter = limiter
        self._permit_timeout = permit_timeout
        self._now = now
        self._next_attempt: dict[str, datetime] = {}
        self._last_emit: dict[str, datetime] = {}
        self._behind_since: dict[str, datetime] = {}  # 갱신이 필요한데 못 한 상태가 시작된 시각
        self._rate_limited: set[str] = set()  # 직전 시도가 1분 1회 제한에 걸렸다(곧 풀린다)
        self._seen: dict[str, list[str]] = {}  # 자격별 최근 값 (detail 가리기용)

    # ---- 한 번의 tick ---------------------------------------------------------------------

    def step(self, now: datetime) -> AuthStatus:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("naive datetime 금지")
        return AuthStatus(now, tuple(self._tick(c, now) for c in self._creds))

    def _tick(self, c: Credential, now: datetime) -> CredentialStatus:
        stage = "캐시 읽기"
        expires_at: datetime | None = None
        try:
            rec = self._live(c, c.cache.load(), now)
            if rec is not None and rec.expires_at - now > self._margin:
                self._behind_since.pop(c.name, None)
                self._rate_limited.discard(c.name)
                return CredentialStatus(c.name, "fresh", rec.expires_at)
            self._behind_since.setdefault(c.name, now)
            expires_at = rec.expires_at if rec is not None else None
            gate = self._next_attempt.get(c.name)
            if gate is not None and now < gate:
                return self._not_refreshed(
                    c, now, "waiting", expires_at, gate, "직전 발급 시도가 61초 안"
                )
            stage = "발급 자리"
            if not c.cache.claim_issue(now, self._gap):
                self._rate_limited.add(c.name)
                retry_at = c.cache.next_issue_at(now) or now + self._gap
                return self._not_refreshed(
                    c, now, "throttled", expires_at, retry_at, "다른 발급자가 61초 안에 시도"
                )
            self._next_attempt[c.name] = now + self._gap  # 이후 무슨 일이 나도 61초는 쉰다
            self._rate_limited.discard(c.name)
            if self._limiter is not None:
                stage = "레이트리미터"
                path = _OAUTH_PATHS[c.name]
                try:
                    self._limiter.acquire(Priority.P0, path, self._permit_timeout)
                except RateLimitTimeout:
                    reason = f"레이트리미터 P0 허가 {self._permit_timeout:g}초 초과 — 안 보냄"
                    return self._failed(c, now, expires_at, reason)
            try:
                got = c.issuer.issue()
            except TokenIssueThrottled:
                self._rate_limited.add(c.name)
                reason = (
                    f"KIS {ISSUE_THROTTLED_CODE}(발급 1분 1회) — 같은 앱키로 다른 곳이 방금 발급"
                )
                return self._failed(c, now, expires_at, reason)
            except TokenIssueError as e:
                return self._failed(c, now, expires_at, self._redact(str(e), cut_tail=True))
            except Exception as e:
                # 예상 밖 예외는 종류만 남긴다 (메시지에 무엇이 들어 있을지 모른다)
                return self._failed(c, now, expires_at, f"예상 밖 발급 오류: {type(e).__name__}")
            self._remember(c, got.access_token.get_secret_value())
            new = TokenRecord(
                access_token=got.access_token,
                expires_at=got.expires_at,
                issued_at=got.issued_at,
                owner=c.owner,
            )
            stage = "캐시 저장"
            c.cache.store(new, now)
            self._behind_since.pop(c.name, None)
        except (RedisError, OSError) as e:
            detail = f"Redis({stage}) 실패: {type(e).__name__}"
            if stage == "캐시 저장":
                detail += " — 받은 값을 잃었다, 61초 뒤 다시"
            return self._error(c, now, f"{c.name}_cache_error", expires_at, detail)
        except Exception as e:
            detail = f"예상 밖 오류({stage}): {type(e).__name__}"
            return self._error(c, now, f"{c.name}_refresh_failed", expires_at, detail)
        self._emit(
            f"{c.name}_refreshed",
            f"만료 {_kst(new.expires_at)} (남은 {_left(new.expires_at - now)})",
            now,
            "info",
        )
        return CredentialStatus(c.name, "refreshed", new.expires_at)

    def _error(
        self, c: Credential, now: datetime, kind: str, expires_at: datetime | None, detail: str
    ) -> CredentialStatus:
        """캐시·예상 밖 오류. 같은 경고는 61초에 한 번. 발급 간격은 그대로 지킨다."""
        self._emit(kind, detail, now, "warning", every=self._gap)
        return CredentialStatus(
            c.name, "error", expires_at, self._next_attempt.get(c.name), detail=detail
        )

    def _live(self, c: Credential, rec: TokenRecord | None, now: datetime) -> TokenRecord | None:
        if rec is None or rec.owner != c.owner or rec.expires_at - now <= self._min_valid:
            return None
        self._remember(c, rec.token)
        return rec

    def _failed(
        self, c: Credential, now: datetime, expires_at: datetime | None, reason: str
    ) -> CredentialStatus:
        retry_at = now + self._gap
        detail = f"{reason} — 다음 시도 {_kst(retry_at)}"
        self._emit(f"{c.name}_refresh_failed", detail, now, "warning")
        return self._not_refreshed(c, now, "failed", expires_at, retry_at, detail)

    def _not_refreshed(
        self,
        c: Credential,
        now: datetime,
        action: Action,
        expires_at: datetime | None,
        next_at: datetime,
        detail: str,
    ) -> CredentialStatus:
        left = expires_at - now if expires_at is not None else timedelta(0)
        expiring = left < self._expiring
        # 직전 시도가 1분 1회 제한에 걸린 것 — 다른 발급자가 막 자리를 잡았거나(throttled, 그쪽이
        # 채울 수 있다) KIS 가 EGW00133 으로 돌려보낸 것(다음 시도에 풀린다) — 은 61초를 기다려 준
        # 뒤에도 못 채웠을 때만 경고한다(그 사이 waiting 도). 다른 실패는 바로 경고
        behind = now - self._behind_since.get(c.name, now)
        grace = c.name in self._rate_limited and behind < self._gap
        if expiring and not grace:
            state = f"남은 {_left(left)}" if expires_at is not None else "살아 있는 값 없음"
            self._emit(
                f"{c.name}_expiring",
                f"{state} — 갱신 실패 중, 다음 시도 {_kst(next_at)}",
                now,
                "critical",
                every=self._gap,
            )
        return CredentialStatus(
            c.name, action, expires_at, next_at, expiring=expiring, detail=self._redact(detail)
        )

    # ---- 루프 -----------------------------------------------------------------------------

    def run_forever(
        self,
        stop: threading.Event,
        *,
        interval: float = STEP_INTERVAL_S,
        on_status: Callable[[AuthStatus], None] | None = None,
    ) -> int:
        """stop 이 설정될 때까지 주입한 시계로 step. 돈 주기 수를 돌려준다.

        쉬는 시간은 `next_wait` — 평소 interval 초, 갱신을 못 한 자격이 있으면 그 다음 시도 시각까지
        (실패 뒤 61초 간격 재시도, 설계 §2). step 이 예외를 던져도(시계 오류 등) 종류만 로그하고
        interval 뒤 다시 — 발급자가 죽지 않게. 대기는 `stop.wait` 라 stop 이 오면 바로 깬다.
        """
        if interval < 0:
            raise ValueError("interval 은 0 이상")
        n = 0
        while not stop.is_set():
            n += 1
            wait = interval
            try:
                status = self.step(self._now())
            except Exception as e:
                log.error("auth step 실패: %s", type(e).__name__)
            else:
                wait = next_wait(status, interval)
                if on_status is not None:
                    try:
                        on_status(status)
                    except Exception as e:
                        log.error("auth on_status 실패: %s", type(e).__name__)
            if stop.wait(wait):
                break
        return n

    # ---- 이벤트 ---------------------------------------------------------------------------

    def _remember(self, c: Credential, value: str) -> None:
        seen = self._seen.setdefault(c.name, [])
        if value and value not in seen:
            seen.append(value)
            del seen[:-4]

    def _redact(self, text: str, *, cut_tail: bool = False) -> str:
        """앱키·시크릿·최근 값을 가린다. cut_tail 은 앞에서 잘렸을 수 있는 발급자 오류 문구용."""
        text = redact(text, [*self._secrets, *(v for vs in self._seen.values() for v in vs)])
        return hide_cut_tail(text, self._secrets) if cut_tail else text

    def _emit(
        self,
        kind: str,
        detail: str,
        now: datetime,
        severity: Severity,
        *,
        every: timedelta | None = None,
    ) -> None:
        """every 를 주면 같은 kind 는 그 간격에 한 번만. 싱크 실패는 발급을 막지 않는다."""
        last = self._last_emit.get(kind)
        if every is not None and last is not None and now - last < every:
            return
        self._last_emit[kind] = now
        try:
            self._sink.emit(HealthEvent(kind, self._redact(detail), now, severity))
        except Exception as e:
            log.error("health 싱크 실패: %s", type(e).__name__)


# ---- 웹소켓 접속키 발급 (미실측) --------------------------------------------------------------


class _ApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approval_key: SecretStr


def _secret_list(*xs: SecretStr) -> list[str]:
    return [x.get_secret_value() for x in xs]


def redact(text: str, values: Iterable[str]) -> str:
    """values 를 모두 `***` 로. 긴 값부터 — 짧은 값이 긴 값 안에 있어도 긴 값의 조각이 남지 않게."""
    for v in sorted({v for v in values if v}, key=len, reverse=True):
        text = text.replace(v, "***")
    return text


def hide_cut_tail(text: str, values: Iterable[str]) -> str:
    """text 끝이 어떤 값의 앞부분(`CUT_TAIL_MIN` 자 이상)이면 그 꼬리를 `***` 로.

    자른 뒤에 가린 문구는 자르기 선에 걸친 비밀값이 통째로 맞지 않아 앞부분이 남는다 — 그 조각.
    """
    cut = 0
    for v in values:
        for j in range(min(len(text), len(v) - 1), cut, -1):
            if text.endswith(v[:j]):
                cut = j
                break
    return text[:-cut] + "***" if cut >= CUT_TAIL_MIN else text


class KisApprovalKeyIssuer:
    """`POST /oauth2/Approval` — 웹소켓 접속키 (확인 필요: 미실측).

    요청 `{grant_type, appkey, secretkey}`(앱시크릿을 `secretkey` 이름으로 보낸다 — KIS 공식 샘플),
    응답 `{approval_key}`. 만료 시각이 오지 않아 `issued_at + life` 로 둔다. `TokenIssuer` 와 같은
    모양이라 캐시·간격·health 는 접근토큰과 같은 길을 탄다.
    """

    def __init__(
        self,
        http: httpx.Client,
        app_key: SecretStr,
        app_secret: SecretStr,
        *,
        life: timedelta = WS_KEY_ASSUMED_LIFE,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        if life <= timedelta(0):
            raise ValueError("접속키 수명은 0보다 커야 한다")
        self._http = http
        self._key = app_key
        self._secret = app_secret
        self._life = life
        self._now = now

    def issue(self) -> IssuedToken:
        secrets = _secret_list(self._key, self._secret)
        issued_at = self._now()
        try:
            r = self._http.post(
                APPROVAL_PATH,
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._key.get_secret_value(),
                    "secretkey": self._secret.get_secret_value(),
                },
            )
        except httpx.HTTPError as e:
            msg = redact(str(e), secrets)
            raise TokenIssueError(f"접속키 발급 요청 실패: {type(e).__name__} {msg}") from None
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
            # 가린 뒤 자른다 — 자른 뒤 가리면 자르기 선에 걸친 비밀값의 앞부분이 남는다
            detail = redact(f"{code} {desc}".strip(), secrets)[:ERROR_TEXT_MAX]
            raise TokenIssueError(f"접속키 발급 실패 HTTP {r.status_code}: {detail}")
        try:
            parsed = _ApprovalResponse.model_validate(body)
        except ValidationError:
            # 본문을 싣지 않는다 (접속키가 들어 있을 수 있다)
            raise TokenIssueError("접속키 응답 형식이 다르다 (approval_key 없음)") from None
        if not parsed.approval_key.get_secret_value().strip():
            raise TokenIssueError("접속키 응답의 approval_key 가 비었다")
        return IssuedToken(parsed.approval_key, issued_at + self._life, issued_at)


# ---- 조립 ------------------------------------------------------------------------------------


def build_auth_service(
    settings: Settings,
    redis: Redis,
    http: httpx.Client,
    sink: HealthSink,
    *,
    ws_key: bool = True,
    ws_key_life: timedelta = WS_KEY_ASSUMED_LIFE,
    limiter: RateLimiter | None = None,
    now: Callable[[], datetime] = utcnow,
) -> AuthService:
    """설정으로 auth 서비스를 만든다. http 는 `base_url=settings.kis_base` 인 클라이언트.

    ws_key=False 면 접근토큰만 다룬다(접속키 발급 실측 전 끄는 스위치). limiter 는 앱키당 REST
    레이트리미터(`serve` 는 같은 Redis 의 `RedisRateLimiter`) — None 이면 허가 없이 보낸다.
    """
    key, sec = settings.kis_app_key, settings.kis_app_secret
    if key is None or sec is None:
        raise TokenUnavailable("KIS_APP_KEY / KIS_APP_SECRET 가 없다")
    if ws_key and ws_key_life <= REFRESH_MARGIN:
        raise ValueError("접속키 수명이 갱신 여유(60분)보다 길어야 한다 — 아니면 61초마다 발급한다")
    owner = token_owner(settings.kis_base, key.get_secret_value())
    creds = [
        Credential(
            "token", RedisTokenCache(redis, TOKEN_KEY), KisTokenIssuer(http, key, sec, now), owner
        )
    ]
    if ws_key:
        issuer = KisApprovalKeyIssuer(http, key, sec, life=ws_key_life, now=now)
        creds.append(Credential("ws_key", RedisTokenCache(redis, WS_KEY_KEY), issuer, owner))
    return AuthService(creds, sink, secrets=_secret_list(key, sec), limiter=limiter, now=now)


def reader(
    redis: Redis,
    settings: Settings,
    name: CredentialName = "token",
    *,
    now: Callable[[], datetime] = utcnow,
) -> CachedTokenProvider:
    """다른 서비스용 읽기 전용 제공자 — 발급하지 않는다(발급은 auth 만, PLAN §2.5).

    `reader(r, s).get()` 은 접근토큰, `reader(r, s, "ws_key").get()` 은 웹소켓 접속키.
    만료 임박이어도 만료 전까지 돌려주고, 없으면 `TokenUnavailable`. KIS 가 거절하면 `invalidate()`.
    now 는 만료 판정 시계 — 테스트는 서비스와 같은 가짜 시계를 넘긴다(실제 날짜에 따라 깨지지 않게).
    """
    key = settings.kis_app_key
    if key is None:
        raise TokenUnavailable("KIS_APP_KEY 가 없다")
    owner = token_owner(settings.kis_base, key.get_secret_value())
    return CachedTokenProvider(RedisTokenCache(redis, _CACHE_KEYS[name]), owner, now=now)


# ---- 진입점 ----------------------------------------------------------------------------------


def calendar_tagger(cal: TradingCalendar | None = None) -> Tagger:
    """health 로그용 (거래일, 세션) 태거. 장이 닫힌 시각은 (None, None)."""
    c = cal if cal is not None else TradingCalendar.default()

    def tag(t: datetime) -> tuple[date | None, str | None]:
        info = state_at(t, c)
        return info.trade_date, info.session

    return tag


def serve(
    settings: Settings,
    stop: threading.Event,
    *,
    redis: Redis | None = None,
    http: httpx.Client | None = None,
    sink: HealthSink | None = None,
    interval: float = STEP_INTERVAL_S,
    ws_key: bool = True,
    limiter: RateLimiter | None = None,
    on_status: Callable[[AuthStatus], None] | None = None,
) -> int:
    """설정으로 조립해 stop 까지 돈다. Redis 없이는 돌지 않는다(설계 §2 — 파일 캐시 없음).

    limiter 를 주지 않으면 같은 Redis 의 앱키당 `RedisRateLimiter`(poller·ws-gateway 와 같은 버킷).
    on_status 는 step 마다 부른다(하트비트 — 값은 싣지 않는다).
    """
    if redis is None:
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL 이 없다 — auth 는 Redis 없이 돌지 않는다 (설계 §2)")
        redis = Redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5)
    if sink is None:
        try:
            tagger: Tagger | None = calendar_tagger()
        except Exception as e:  # 캘린더가 없어도 발급은 한다
            log.warning("캘린더 태거 실패 — 거래일·세션 없이 기록: %s", type(e).__name__)
            tagger = None
        sink = LogHealthSink(tagger=tagger)
    if limiter is None and settings.kis_app_key is not None:
        limiter = RedisRateLimiter(redis, settings.kis_app_key.get_secret_value())
    client = http if http is not None else httpx.Client(base_url=settings.kis_base, timeout=10.0)
    try:
        svc = build_auth_service(settings, redis, client, sink, ws_key=ws_key, limiter=limiter)
        return svc.run_forever(stop, interval=interval, on_status=on_status)
    finally:
        if http is None:
            client.close()


def heartbeat_status(status: AuthStatus) -> dict[str, object]:
    """하트비트에 싣는 상태 — 자격별 동작·만료·경고만(값·앱키는 없다)."""
    return {
        "ok": status.ok,
        **{
            c.name: {
                "action": c.action,
                "expires_at": c.expires_at.isoformat() if c.expires_at else None,
                "expiring": c.expiring,
            }
            for c in status.credentials
        },
    }


def main() -> int:  # pragma: no cover — compose 진입점 (.env·실제 Redis·DB·KIS)
    from data.store import PostgresSink
    from services.runtime import (
        Heartbeater,
        ServiceHealthSink,
        connect_redis,
        install_stop,
        log_event,
        setup_logging,
    )

    setup_logging()
    settings = Settings()
    stop = threading.Event()
    install_stop(stop)
    if not settings.redis_url:
        log_event(log, logging.ERROR, "auth", "config_error", error="REDIS_URL 이 없다")
        return 2
    redis = connect_redis(settings.redis_url)
    hb = Heartbeater(redis, "auth", ttl_s=180)
    tagger = calendar_tagger()
    store = (
        PostgresSink.from_settings(service="auth", spool=True, tagger=tagger)
        if settings.database_url
        else None
    )

    def beat(st: AuthStatus) -> None:
        hb.beat(auth=heartbeat_status(st))

    try:
        sink = ServiceHealthSink(store, tagger)
        serve(settings, stop, redis=redis, sink=sink, on_status=beat)
    finally:
        if store is not None:
            store.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

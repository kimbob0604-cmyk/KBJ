"""KIS 접근토큰·웹소켓 접속키 **발급자** — 레포에서 발급 요청을 보내는 유일한 파일(ADR 0004).

GEXLAB `data/kis/auth_client.py`(`TOKEN_PATH`:46, `ISSUE_THROTTLED_CODE`:47, `_TokenResponse`:156,
`KisTokenIssuer`:165, `_expiry`:220)와 `services/auth/service.py`(`APPROVAL_PATH`:91,
`KisApprovalKeyIssuer`:465) 승격.

발급은 세 겹으로 auth 에만 둔다(ADR 0004 §2.2):

1. 발급 클래스는 이 파일에만 있고, import-linter 계약이 auth 밖에서 `kbj.services.auth` 를 import
   하면 실패시킨다(설계 §9.6 ④ — 묶음 I).
2. KIS 발급 경로 문자열은 `kbj/services/auth/**` 에만 있다(`scripts/check_canonical.py` 그룹
   `kis_oauth`).
3. **런타임 가드**: 이 프로세스의 `Settings.service`(`KBJ_SERVICE`)가 `auth` 가 아니면 발급자
   생성자가 `RuntimeError` 로 거부한다. 시험·스크립트처럼 `KBJ_SERVICE` 가 없는 프로세스도 발급할 수
   없다.

- 접근토큰 만료는 응답의 `access_token_token_expired`(KST)와 `expires_in` 중 이른 쪽.
- 웹소켓 접속키(`POST` 접속키 경로)는 **미실측(확인 필요)**: 요청 `{grant_type, appkey, secretkey}`
  (KIS 공식 샘플 — 앱시크릿을 `secretkey` 이름으로), 응답 `{approval_key}`. 만료가 오지 않아 수명은
  보수적 가정 12시간(11시간마다 갱신).
- 오류 문구는 비밀값을 **가린 뒤** 200자로 자른다. 토큰·접속키는 오류에 싣지 않는다(본문 미기재).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from kbj.config.settings import Settings
from kbj.core.masking import redact
from kbj.data.private.kis.errors import TokenIssueError, TokenIssueThrottled
from kbj.data.private.kis.token import IssuedToken, utcnow

__all__ = [
    "APPROVAL_PATH",
    "ERROR_TEXT_MAX",
    "ISSUER_SERVICE",
    "ISSUE_THROTTLED_CODE",
    "TOKEN_PATH",
    "WS_KEY_ASSUMED_LIFE",
    "KisApprovalKeyIssuer",
    "KisTokenIssuer",
    "TokenIssueError",
    "TokenIssueThrottled",
    "require_issuer_process",
]

TOKEN_PATH: Final = "/oauth2/tokenP"  # noqa: S105 — 경로일 뿐 비밀 아님
APPROVAL_PATH: Final = "/oauth2/Approval"
ISSUE_THROTTLED_CODE: Final = "EGW00133"  # 접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)
# 확인 필요: 접속키 수명 미실측(응답에 만료 없음). 짧게 잡으면 발급이 잦을 뿐이고, 길게 잡았다가
# 실제가 더 짧으면 ws-gateway 재접속이 막힌다 → 12시간(11시간마다 갱신)
WS_KEY_ASSUMED_LIFE: Final = timedelta(hours=12)
ERROR_TEXT_MAX: Final = 200  # 오류 문구에 싣는 KIS 오류 본문 길이 (가린 뒤 자른다)
ISSUER_SERVICE: Final = "auth"  # 발급자를 만들 수 있는 유일한 서비스(KBJ_SERVICE)


def require_issuer_process(settings: Settings | None = None) -> None:
    """이 프로세스가 auth 서비스가 아니면 `RuntimeError`(ADR 0004 런타임 가드).

    settings 를 주지 않으면 이 프로세스의 설정(`Settings()` — 환경변수·`.env`)을 읽는다.
    """
    service = (settings if settings is not None else Settings()).service
    if service != ISSUER_SERVICE:
        shown = service if service is not None else "없음"
        raise RuntimeError(
            f"KIS 발급은 auth 서비스만 한다(KBJ_SERVICE={shown}) — 다른 프로세스는 Redis 의 토큰을 "
            "읽기만 한다(kbj.data.private.kis.token.reader, ADR 0004)"
        )


def _error_detail(r: httpx.Response, secrets: list[str]) -> tuple[str, str]:
    """(오류 코드, 가린 뒤 자른 '코드 설명'). 본문이 JSON 객체가 아니면 코드·설명 없이."""
    try:
        body: Any = r.json()
    except ValueError:
        body = None
    err: dict[str, Any] = cast(dict[str, Any], body) if isinstance(body, dict) else {}
    code = str(err.get("error_code") or err.get("msg_cd") or "")
    desc = str(err.get("error_description") or err.get("msg1") or "")
    # 가린 뒤 자른다 — 자른 뒤 가리면 자르기 선에 걸친 비밀값의 앞부분이 남는다
    return code, redact(f"{code} {desc}".strip(), secrets)[:ERROR_TEXT_MAX]


class _TokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: SecretStr
    token_type: str | None = None
    expires_in: int | None = None
    access_token_token_expired: str | None = None  # 'YYYY-MM-DD HH:MM:SS' (KST)


class KisTokenIssuer:
    """접근토큰 발급(`POST` 토큰 경로). http 는 `base_url` 이 KIS REST 주소인 클라이언트.

    만료 시각은 `access_token_token_expired`(KST)와 `expires_in` 중 이른 쪽으로 잡는다.
    """

    def __init__(
        self,
        http: httpx.Client,
        app_key: SecretStr,
        app_secret: SecretStr,
        now: Callable[[], datetime] = utcnow,
        *,
        settings: Settings | None = None,
    ) -> None:
        require_issuer_process(settings)
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
            msg = redact(str(e), self._secrets())
            raise TokenIssueError(f"토큰 발급 요청 실패: {type(e).__name__} {msg}") from None
        if r.status_code != 200:
            code, detail = _error_detail(r, self._secrets())
            if code == ISSUE_THROTTLED_CODE:
                raise TokenIssueThrottled(None)
            raise TokenIssueError(f"토큰 발급 실패 HTTP {r.status_code}: {detail}")
        try:
            parsed = _TokenResponse.model_validate(r.json())
        except (ValidationError, ValueError):
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
            pass  # 형식이 틀린 만료 문자열은 버리고 expires_in 으로(둘 다 없으면 아래에서 오류)
    if p.expires_in is not None and p.expires_in > 0:
        cands.append(issued_at + timedelta(seconds=p.expires_in))
    if not cands:
        raise TokenIssueError("토큰 응답에 만료 시각이 없다")
    return min(cands)


class _ApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approval_key: SecretStr


class KisApprovalKeyIssuer:
    """웹소켓 접속키 발급(`POST` 접속키 경로 — 확인 필요: 미실측).

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
        settings: Settings | None = None,
    ) -> None:
        if life <= timedelta(0):
            raise ValueError("접속키 수명은 0보다 커야 한다")
        require_issuer_process(settings)
        self._http = http
        self._key = app_key
        self._secret = app_secret
        self._life = life
        self._now = now

    def issue(self) -> IssuedToken:
        secrets = [self._key.get_secret_value(), self._secret.get_secret_value()]
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
        if r.status_code != 200:
            code, detail = _error_detail(r, secrets)
            if code == ISSUE_THROTTLED_CODE:
                raise TokenIssueThrottled(None)
            raise TokenIssueError(f"접속키 발급 실패 HTTP {r.status_code}: {detail}")
        try:
            parsed = _ApprovalResponse.model_validate(r.json())
        except (ValidationError, ValueError):
            # 본문을 싣지 않는다 (접속키가 들어 있을 수 있다)
            raise TokenIssueError("접속키 응답 형식이 다르다 (approval_key 없음)") from None
        if not parsed.approval_key.get_secret_value().strip():
            raise TokenIssueError("접속키 응답의 approval_key 가 비었다")
        return IssuedToken(parsed.approval_key, issued_at + self._life, issued_at)

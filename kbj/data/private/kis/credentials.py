"""KIS 자격 — 앱키·시크릿·환경(real·vts)과 그 환경의 REST 주소(설계 §1.2, ADR 0004).

GEXLAB `config/settings.py`(`KIS_REAL_BASE`·`KIS_VTS_BASE`:11~12, `kis_base`:64)와
`data/kis/auth_client.py:token_owner`:82 승격.

- 앱키·시크릿은 KIS REST 를 부르는 프로세스에도 들어간다 — 모든 요청 헤더에 필요하다(ADR 0004 안 A).
  이 객체가 있어도 토큰을 **발급**할 수는 없다: 발급 클래스는 auth 의 `issuer.py` 에만 있다.
- 주소는 비공개 상수다. 밖(legacy GX 설정 등)에서는 `base_url(env)` 로만 얻는다 — 상수를 가져다
  `requests.get(상수 + 경로)` 로 직접 부르는 길을 막는다(설계 §9.5 AST 규칙).
- `owner` 는 GEXLAB 과 같은 식(sha256(주소 + "\\n" + 앱키) 앞 16자)이다. 전환 기간 legacy GX 가 KBJ
  auth 가 넣은 토큰을 같은 `owner` 로 알아보고 읽는다(설계 §3.9 `test_owner_compat`).
- repr·직렬화에 값이 나오지 않는다(`SecretStr`).
"""

from __future__ import annotations

import hashlib
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, SecretStr

from kbj.config.settings import Settings
from kbj.data.private.kis.errors import TokenUnavailable

KisEnv = Literal["real", "vts"]

_REAL_BASE: Final = "https://openapi.koreainvestment.com:9443"
_VTS_BASE: Final = "https://openapivts.koreainvestment.com:29443"


def base_url(env: KisEnv) -> str:
    """환경의 KIS REST 주소(실전 `real`·모의 `vts`)."""
    if env == "real":
        return _REAL_BASE
    if env == "vts":
        return _VTS_BASE
    raise ValueError(f"KIS 환경은 real·vts 중 하나: {env!r}")


def token_owner(base_url: str, app_key: str) -> str:
    """캐시된 토큰이 어느 앱키·환경 것인지(해시만 남긴다). GEXLAB 과 같은 값."""
    return hashlib.sha256(f"{base_url}\n{app_key}".encode()).hexdigest()[:16]


class KisCredentials(BaseModel):
    """앱키·시크릿·환경. 값은 `SecretStr` — 쓰는 곳에서만 꺼낸다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    app_key: SecretStr
    app_secret: SecretStr
    env: KisEnv = "real"

    @property
    def base_url(self) -> str:
        return base_url(self.env)

    @property
    def owner(self) -> str:
        """`token_owner(base_url, app_key)` — 다른 앱키·환경의 토큰은 쓰지 않는다."""
        return token_owner(self.base_url, self.app_key.get_secret_value())

    def secrets(self) -> list[str]:
        """가릴 값(앱키·시크릿) — `kbj.core.masking.redact` 용."""
        return [
            v for v in (self.app_key.get_secret_value(), self.app_secret.get_secret_value()) if v
        ]

    @classmethod
    def from_settings(cls, settings: Settings) -> KisCredentials:
        """`KBJ_KIS_APP_KEY`·`KBJ_KIS_APP_SECRET`·`KBJ_KIS_ENV`. 키나 시크릿이 없으면
        `TokenUnavailable`(값 없이 이름만 알린다)."""
        key, sec = settings.kis_app_key, settings.kis_app_secret
        if key is None or sec is None:
            raise TokenUnavailable("KBJ_KIS_APP_KEY / KBJ_KIS_APP_SECRET 가 없다")
        return cls(app_key=key, app_secret=sec, env=settings.kis_env)

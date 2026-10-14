"""전환 기간 legacy GX 가 KBJ auth 의 토큰을 그대로 읽는다(설계 §3.2 끝·§3.9 `test_owner_compat`).

P2~P7 동안 legacy GX(poller·ws-gateway·scheduler 분봉)는 같은 Redis 의 `kis:token`·`kis:ws_key` 를
자기 `CachedTokenProvider`(발급자 없음)로 읽는다. 그러려면 ① 키 이름 ② 값 JSON 모양(`TokenRecord`)
③ `owner`(sha256(주소 + "\\n" + 앱키) 앞 16자 — 다른 앱키·환경 값은 무시)가 GX 와 같아야 한다.

- 고정값 대조: GX 설정의 주소(`legacy/gexlab/config/settings.py:11~12` 값)로 계산한 owner.
- (P9 GXI 에 지움) GX 코드 대조 `test_gexlab_reader_code_reads_what_kbj_auth_stored` — KBJ auth 가
  넣은 값을 GX 코드(하위 프로세스)가 읽는지 보던 전환 대조. GX 수집 경로를 KBJ 로 옮겨(ADR 0036)
  gex-poller·분봉 작업은 KBJ `reader` 로 읽고(tests/unit/kis/test_reader_never_issues.py 등),
  legacy GX 는 WRAP 이 지운다 — 남겨 두면 legacy 가 없어진 뒤 조용히 건너뛴다. 전환 전 VM 의 legacy
  GX
  는 커밋 f6aeebd 코드이고 그 커밋에서 이 대조는 녹색이었다.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.data.private.kis.credentials import KisCredentials, token_owner

APP_KEY = "PSappKEY0123456789abcdef"
APP_SECRET = "SECRETvalue9876543210zyx"
NOW = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)  # 09:00 KST
# GX config/settings.py:11~12 (스냅샷 43a9ed1) — 공개 문서에 있는 KIS 주소
GX_REAL_BASE = "https://openapi.koreainvestment.com:9443"
GX_VTS_BASE = "https://openapivts.koreainvestment.com:29443"


def settings(env: str = "real") -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        kis_app_key=SecretStr(APP_KEY),
        kis_app_secret=SecretStr(APP_SECRET),
        kis_env=env,  # pyright: ignore[reportArgumentType]
        service="auth",
    )


@pytest.mark.parametrize(("env", "gx_base"), [("real", GX_REAL_BASE), ("vts", GX_VTS_BASE)])
def test_owner_matches_the_gexlab_value(env: str, gx_base: str) -> None:
    c = KisCredentials.from_settings(settings(env))
    assert c.base_url == gx_base
    gx_owner = hashlib.sha256(f"{gx_base}\n{APP_KEY}".encode()).hexdigest()[:16]
    assert c.owner == gx_owner == token_owner(gx_base, APP_KEY)

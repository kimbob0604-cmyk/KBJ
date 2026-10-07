"""전환 기간 legacy GX 가 KBJ auth 의 토큰을 그대로 읽는다(설계 §3.2 끝·§3.9 `test_owner_compat`).

P2~P7 동안 legacy GX(poller·ws-gateway·scheduler 분봉)는 같은 Redis 의 `kis:token`·`kis:ws_key` 를
자기 `CachedTokenProvider`(발급자 없음)로 읽는다. 그러려면 ① 키 이름 ② 값 JSON 모양(`TokenRecord`)
③ `owner`(sha256(주소 + "\\n" + 앱키) 앞 16자 — 다른 앱키·환경 값은 무시)가 GX 와 같아야 한다.

- 고정값 대조: GX 설정의 주소(`legacy/gexlab/config/settings.py:11~12` 값)로 계산한 owner.
- GX 코드 대조: KBJ auth 가 Redis 에 넣은 값을 **GX 코드 그대로**(GX 루트에서 하위 프로세스 —
  `scripts`·`config` 패키지 이름이 KBJ 와 겹쳐 같은 프로세스에 올리지 않는다) 읽게 한다.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.data.private.kis.credentials import KisCredentials, token_owner
from kbj.services.auth.issuer import APPROVAL_PATH, TOKEN_PATH
from kbj.services.auth.service import TOKEN_KEY, WS_KEY_KEY, build_auth_service
from kbj.services.runtime import MemoryHealthSink

APP_KEY = "PSappKEY0123456789abcdef"
APP_SECRET = "SECRETvalue9876543210zyx"
NOW = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)  # 09:00 KST
GX_ROOT = Path(__file__).resolve().parents[3] / "legacy" / "gexlab"
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


def _auth_fills(r: fakeredis.FakeRedis, env: str) -> None:
    def kis(req: httpx.Request) -> httpx.Response:
        if req.url.path == TOKEN_PATH:
            return httpx.Response(200, json={"access_token": "eyJKBJTOKEN", "expires_in": 86400})
        assert req.url.path == APPROVAL_PATH
        return httpx.Response(200, json={"approval_key": "KBJWSKEY"})

    s = settings(env)
    http = httpx.Client(
        base_url=KisCredentials.from_settings(s).base_url, transport=httpx.MockTransport(kis)
    )
    svc = build_auth_service(s, r, http, MemoryHealthSink(), now=lambda: NOW)
    assert svc.step(NOW).ok


_GX_READ = """
import json, sys
from datetime import datetime
sys.path.insert(0, ".")
from pydantic import SecretStr
from config.settings import Settings
from data.kis.auth_client import CachedTokenProvider, TokenRecord, token_owner

args = json.loads(sys.argv[1])
now = datetime.fromisoformat(args["now"])

class DictCache:  # GX RedisTokenCache.load 와 같은 해석(TokenRecord.model_validate_json)
    def __init__(self, raw):
        self.raw = raw
    def load(self):
        return TokenRecord.model_validate_json(self.raw)

out = {}
for env, values in args["values"].items():
    s = Settings(_env_file=None, kis_app_key=SecretStr(args["key"]), kis_env=env)
    owner = token_owner(s.kis_base, args["key"])
    row = {}
    for name, raw in values.items():
        rec = TokenRecord.model_validate_json(raw)
        reader = CachedTokenProvider(DictCache(raw), owner, now=lambda: now)
        row[name] = {"owner_ok": rec.owner == owner, "value": reader.get()}
    out[env] = row
print(json.dumps(out))
"""


@pytest.mark.skipif(
    not (GX_ROOT / "data" / "kis" / "auth_client.py").exists(),
    reason="legacy GX 가 없다(P9 정리 뒤)",
)
def test_gexlab_reader_code_reads_what_kbj_auth_stored() -> None:
    values: dict[str, dict[str, str]] = {}
    for env in ("real", "vts"):
        r = fakeredis.FakeRedis()
        _auth_fills(r, env)
        raw: Any = {
            name: r.get(key) for name, key in (("token", TOKEN_KEY), ("ws_key", WS_KEY_KEY))
        }
        values[env] = {k: v.decode() for k, v in raw.items()}
    args = {"key": APP_KEY, "now": NOW.isoformat(), "values": values}
    proc = subprocess.run(  # noqa: S603 — 이 레포의 파이썬으로 고정 문자열 스크립트를 돌린다
        [sys.executable, "-c", _GX_READ, json.dumps(args)],
        cwd=GX_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    got = json.loads(proc.stdout)
    for env in ("real", "vts"):
        assert got[env]["token"] == {"owner_ok": True, "value": "eyJKBJTOKEN"}
        assert got[env]["ws_key"] == {"owner_ok": True, "value": "KBJWSKEY"}
    assert (TOKEN_KEY, WS_KEY_KEY) == (
        "kis:token",
        "kis:ws_key",
    )  # GX services/auth/service.py:89·90

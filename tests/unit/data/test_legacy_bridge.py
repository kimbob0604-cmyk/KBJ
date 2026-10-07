"""legacy 논리 URL 브리지(kbj.data.legacy_bridge) — 설계 §1.9·§3.7.

가짜 출처는 `httpx.MockTransport`, 토큰은 fakeredis 에 auth 가 둔 모양(`TokenRecord`)으로 넣는다.
시계는 고정(`NOW`) — 브리지·리더·예산이 모두 이 시계를 읽는다(CLAUDE.md §4).
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.data import legacy_bridge as bridge
from kbj.data.http import get as et_get
from kbj.data.private.kis.credentials import KisCredentials
from kbj.data.private.kis.rest import KisRestClient
from kbj.data.private.kis.token import RedisTokenCache, TokenRecord, reader
from kbj.data.private.krx.client import KrxClient
from kbj.data.public.dart.client import DartClient
from kbj.data.ratelimit import Priority
from kbj.store.redis_keys import KIS_TOKEN

NOW = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)  # 10:00 KST
APP_KEY = "PSbridgeTestAppKey0123456789"
APP_SECRET = "bridge-test-secret-abcdefghijklmnopqrstuvwxyz0123456789"
TOKEN = "eyJbridge.token.value.0123456789abcdef"
KRX_KEY = "KRXBRIDGEKEY0123456789"
DART_KEY = "d" * 40
CALLER_KEY = "old-env-key-should-not-be-sent"


class Limiter:
    def __init__(self) -> None:
        self.acquired: list[tuple[Priority, str, float | None]] = []

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        self.acquired.append((priority, tr_id, timeout))

    def on_rate_limited(self) -> float:
        return 1.0


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "kis_app_key": SecretStr(APP_KEY),
        "kis_app_secret": SecretStr(APP_SECRET),
        "krx_api_key": SecretStr(KRX_KEY),
        "dart_api_key": SecretStr(DART_KEY),
        "service": "legacy-sd",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)  # pyright: ignore[reportCallIssue]


def _creds() -> KisCredentials:
    return KisCredentials(app_key=SecretStr(APP_KEY), app_secret=SecretStr(APP_SECRET))


def _seed_token(r: fakeredis.FakeRedis) -> None:
    rec = TokenRecord(
        access_token=SecretStr(TOKEN),
        expires_at=NOW + timedelta(hours=20),
        issued_at=NOW - timedelta(hours=4),
        owner=_creds().owner,
    )
    RedisTokenCache(r, KIS_TOKEN).store(rec, NOW)


@pytest.fixture(autouse=True)
def _fresh_bridge() -> Iterator[None]:
    bridge.reset()
    yield
    bridge.reset()


def _kis_client(
    r: fakeredis.FakeRedis, handler: httpx.MockTransport, limiter: Limiter
) -> KisRestClient:
    provider = reader(r, _creds(), now=lambda: NOW, by="legacy-sd")
    return KisRestClient(_creds(), provider, limiter, transport=handler)


# ── 직접 호출 금지 ──


def test_plain_urls_and_post_are_refused() -> None:
    with pytest.raises(ValueError, match="논리 URL"):
        bridge.get("https://example.invalid/uapi/x")
    with pytest.raises(ValueError, match="POST"):
        bridge.post("kis:/oauth")
    with pytest.raises(ValueError, match="POST"):
        bridge.session().post("kis:/x")


# ── kis: ──


def test_kis_uses_kbj_credentials_and_reader_token_not_caller_headers() -> None:
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200, json={"rt_cd": "0", "output": [{"a": "1"}]}, headers={"tr_cont": "M"}
        )

    r = fakeredis.FakeRedis()
    _seed_token(r)
    lim = Limiter()
    bridge.configure(settings=_settings(), redis=r, now=lambda: NOW)
    bridge.configure(kis=_kis_client(r, httpx.MockTransport(handle), lim))
    resp = bridge.get(
        "kis:/uapi/domestic-stock/v1/quotations/inquire-investor",
        headers={
            "authorization": "Bearer caller-token",
            "appkey": CALLER_KEY,
            "appsecret": CALLER_KEY,
            "tr_id": "FHKST01010900",
        },
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"},
        timeout=7,
    )
    assert resp.status_code == 200 and resp.ok
    assert resp.json() == {"rt_cd": "0", "output": [{"a": "1"}]}
    assert resp.headers["TR_CONT"] == "M"  # 대소문자 무관
    (req,) = seen
    assert req.method == "GET"
    assert req.url.path == "/uapi/domestic-stock/v1/quotations/inquire-investor"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["appkey"] == APP_KEY and req.headers["appsecret"] == APP_SECRET
    assert CALLER_KEY not in str(req.headers) and "caller-token" not in str(req.headers)
    # 기본 우선순위는 P3, 허가 대기 상한은 호출자의 timeout
    assert lim.acquired == [(Priority.P3, "FHKST01010900", 7.0)]


def test_kis_priority_header_and_session_headers_merge() -> None:
    def handle(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "output": []})

    r = fakeredis.FakeRedis()
    _seed_token(r)
    lim = Limiter()
    bridge.configure(settings=_settings(), redis=r, now=lambda: NOW)
    bridge.configure(kis=_kis_client(r, httpx.MockTransport(handle), lim))
    s = bridge.session()
    s.headers.update({"tr_id": "FHPTJ04400000", bridge.PRIORITY_HEADER: "P2"})
    # ET http.get(재시도 포함) 이 브리지 세션 위에서 그대로 돈다
    js = et_get(s, "kis:/uapi/domestic-stock/v1/quotations/foreign-institution-total", params={})
    assert js == {"rt_cd": "0", "output": []}
    assert lim.acquired[0][:2] == (Priority.P2, "FHPTJ04400000")
    with pytest.raises(ValueError, match="P0~P4"):
        s.get("kis:/x", headers={bridge.PRIORITY_HEADER: "P9"})
    with pytest.raises(ValueError, match="tr_id"):
        bridge.get("kis:/uapi/x")


def test_kis_without_token_returns_503_envelope_and_never_issues() -> None:
    calls: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={"access_token": "x"})

    r = fakeredis.FakeRedis()  # auth 가 아직 토큰을 두지 않았다
    bridge.configure(settings=_settings(), redis=r, now=lambda: NOW)
    bridge.configure(kis=_kis_client(r, httpx.MockTransport(handle), Limiter()))
    resp = bridge.get("kis:/uapi/x", headers={"tr_id": "FHKST01010100"})
    assert resp.status_code == 503 and not resp.ok
    body = resp.json()
    assert body["rt_cd"] == "1" and body["msg_cd"] == bridge.TOKEN_UNAVAILABLE
    assert "auth 대기" in body["msg1"]
    assert calls == []  # 발급 POST 도, 조회 GET 도 없다
    with pytest.raises(bridge.BridgeHTTPError):
        resp.raise_for_status()


def test_kis_without_redis_or_app_key_is_token_unavailable() -> None:
    bridge.configure(settings=_settings())  # Redis 주소 없음
    resp = bridge.get("kis:/uapi/x", headers={"tr_id": "T"})
    assert resp.status_code == 503 and resp.json()["msg_cd"] == bridge.TOKEN_UNAVAILABLE
    bridge.reset()
    bridge.configure(settings=_settings(kis_app_key=None), redis=fakeredis.FakeRedis())
    resp = bridge.get("kis:/uapi/x", headers={"tr_id": "T"})
    assert resp.status_code == 503 and resp.json()["msg_cd"] == bridge.TOKEN_UNAVAILABLE
    assert APP_SECRET not in resp.text


def test_kis_transport_error_is_connection_error_without_secrets() -> None:
    def handle(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {req.headers['appkey']}")

    r = fakeredis.FakeRedis()
    _seed_token(r)
    bridge.configure(settings=_settings(), redis=r, now=lambda: NOW)
    bridge.configure(kis=_kis_client(r, httpx.MockTransport(handle), Limiter()))
    with pytest.raises(bridge.BridgeConnectionError) as e:
        bridge.get("kis:/uapi/x", headers={"tr_id": "T"})
    assert APP_KEY not in str(e.value) and isinstance(e.value, ConnectionError)


def test_access_token_or_none_reads_only() -> None:
    r = fakeredis.FakeRedis()
    bridge.configure(settings=_settings(), redis=r, now=lambda: NOW)
    assert bridge.access_token_or_none() is None
    _seed_token(r)
    bridge.reset()
    bridge.configure(settings=_settings(), redis=r, now=lambda: NOW)
    assert bridge.access_token_or_none() == TOKEN
    bridge.reset()
    bridge.configure(settings=_settings())  # Redis 없음
    assert bridge.access_token_or_none() is None


# ── kis-master: ──


def test_kis_master_downloads_through_kbj() -> None:
    bridge.configure(master=lambda: b"PK\x03\x04zip")
    resp = bridge.get("kis-master:fo_idx_code_mts.mst.zip", timeout=20)
    assert resp.status_code == 200 and resp.content == b"PK\x03\x04zip"
    resp.raise_for_status()
    with pytest.raises(ValueError, match="fo_idx_code_mts"):
        bridge.get("kis-master:other.zip")


def test_kis_master_failure_is_a_status_not_silence() -> None:
    def boom() -> bytes:
        raise TimeoutError("마스터 내려받기 30초 초과")

    bridge.configure(master=boom)
    resp = bridge.get("kis-master:fo_idx_code_mts.mst.zip")
    assert resp.status_code == 502 and "30초" in resp.text
    with pytest.raises(bridge.BridgeHTTPError):
        resp.raise_for_status()


# ── krx: ──


def _krx(handler: httpx.MockTransport) -> KrxClient:
    return KrxClient(SecretStr(KRX_KEY), transport=handler, now=lambda: NOW)


def test_krx_sends_kbj_key_and_returns_outblock() -> None:
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"OutBlock_1": [{"ISU_CD": "005930"}]})

    bridge.configure(krx=_krx(httpx.MockTransport(handle)))
    resp = bridge.get(
        "krx:/sto/stk_bydd_trd",
        headers={"AUTH_KEY": CALLER_KEY},
        params={"basDd": "20261006", "AUTH_KEY": CALLER_KEY},
    )
    assert resp.status_code == 200
    assert resp.json() == {"OutBlock_1": [{"ISU_CD": "005930"}]}
    (req,) = seen
    assert req.url.path.endswith("/sto/stk_bydd_trd")
    assert req.headers["AUTH_KEY"] == KRX_KEY
    assert CALLER_KEY not in str(req.url) and CALLER_KEY not in str(req.headers)
    with pytest.raises(ValueError, match="basDd"):
        bridge.get("krx:/sto/stk_bydd_trd")


def test_krx_not_subscribed_and_key_errors_keep_status_and_hide_key() -> None:
    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("etf_bydd_trd"):
            return httpx.Response(401, text="Unauthorized API Call")
        return httpx.Response(401, text=f"bad key {KRX_KEY}")

    bridge.configure(krx=_krx(httpx.MockTransport(handle)))
    a = bridge.get("krx:/etp/etf_bydd_trd", params={"basDd": "20261006"})
    assert a.status_code == 401 and "Unauthorized API Call" in a.text
    b = bridge.get("krx:/sto/stk_bydd_trd", params={"basDd": "20261006"})
    assert b.status_code == 401 and KRX_KEY not in b.text


def test_krx_missing_key_is_401_not_an_unkeyed_call() -> None:
    bridge.configure(settings=_settings(krx_api_key=None))
    resp = bridge.get("krx:/sto/stk_bydd_trd", params={"basDd": "20261006"})
    assert resp.status_code == 401 and "KBJ_KRX_API_KEY" in resp.text


# ── dart: ──


def _dart(handler: httpx.MockTransport) -> DartClient:
    return DartClient(
        DART_KEY, transport=handler, limiter=Limiter(), now=lambda: NOW, sleep=lambda _s: None
    )


def test_dart_json_swaps_key_and_keeps_dart_status_shapes() -> None:
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        corp = req.url.params.get("corp_code")
        if corp == "empty":
            return httpx.Response(
                200, json={"status": "013", "message": "조회된 데이타가 없습니다."}
            )
        if corp == "quota":
            return httpx.Response(
                200, json={"status": "020", "message": "요청 제한을 초과하였습니다."}
            )
        return httpx.Response(200, json={"status": "000", "message": "정상", "list": [{"x": 1}]})

    bridge.configure(dart=_dart(httpx.MockTransport(handle)))
    ok = bridge.get("dart:/list.json", params={"crtfc_key": CALLER_KEY, "corp_code": "00126380"})
    assert ok.status_code == 200 and ok.json()["list"] == [{"x": 1}]
    assert seen[0].url.params["crtfc_key"] == DART_KEY
    assert CALLER_KEY not in str(seen[0].url)
    empty = bridge.get("dart:/list.json", params={"corp_code": "empty"})
    assert empty.json()["status"] == "013" and empty.json()["list"] == []
    quota = bridge.get("dart:/api/list.json", params={"corp_code": "quota"})
    assert quota.status_code == 200 and quota.json()["status"] == "020"
    assert DART_KEY not in quota.text


def test_dart_zip_endpoint_returns_bytes_or_status_xml() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("0001.xml", "<doc/>")
    payload = buf.getvalue()

    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.params.get("rcept_no") == "none":
            return httpx.Response(
                200, text="<result><status>014</status><message>파일이 없다</message></result>"
            )
        return httpx.Response(200, content=payload)

    bridge.configure(dart=_dart(httpx.MockTransport(handle)))
    got = bridge.get("dart:/document.xml", params={"rcept_no": "1", "crtfc_key": CALLER_KEY})
    assert got.status_code == 200 and got.content == payload
    miss = bridge.get("dart:/document.xml", params={"rcept_no": "none"})
    assert miss.status_code == 200 and "<status>014</status>" in miss.text


def test_dart_missing_key_is_503_with_reason() -> None:
    bridge.configure(settings=_settings(dart_api_key=None))
    resp = bridge.get("dart:/list.json", params={})
    assert resp.status_code == 503 and "KBJ_DART_API_KEY" in resp.text


def test_response_shape_matches_requests() -> None:
    resp = bridge.BridgeResponse(200, json.dumps({"a": 1}).encode())
    assert resp.json() == {"a": 1} and resp.text == '{"a": 1}' and resp.ok
    assert b"".join(resp.iter_content(2)) == resp.content
    resp.raise_for_status()
    assert "KBJ" in bridge.BridgeResponse(500, b"").reason

"""거절 신고 가드 — 읽는 쪽 `invalidate()` 는 키를 지우지 않고 신고만 한다(설계 §3.4).

GX 는 `invalidate()` 가 `kis:token` 을 지웠다(`discard`) — 고장 난 호출자 하나가 auth 의 재발급을
하루 1,400번 일으킬 수 있었다(86,400 ÷ 61). KBJ 의 읽기 전용 제공자는:
- `kis:token` 을 그대로 두고 `kis:token:rejected`(hash, 1시간)에 토큰 해시·시각·신고자를 남긴다.
- 같은 토큰의 신고가 이미 있으면 덮어쓰지 않는다(처리 표시를 지우지 않게).
- 거절된 토큰은 이 프로세스에서 다시 쓰지 않는다 — auth 가 새 값을 넣을 때까지 `TokenUnavailable`.
- REST 클라이언트는 거절 응답 뒤 한 번만 다시 시도하고, 새 토큰이 없으면 발급하지 않고 실패한다.
- Redis 장애로 신고를 못 쓰면 `TokenUnavailable`(읽는 쪽 Redis 장애와 같게).
auth 쪽 처리(한 번만, 발급 10분 안이면 재발급 안 함)는 `tests/unit/auth/test_rejected_storm.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from kbj.data.private.kis.credentials import KisCredentials
from kbj.data.private.kis.rest import KisRestClient
from kbj.data.private.kis.token import (
    RedisTokenCache,
    TokenRecord,
    TokenUnavailable,
    reader,
    rejected_key,
    token_digest,
)
from kbj.store.redis_keys import KIS_TOKEN, KIS_TOKEN_REJECTED

NOW = datetime(2026, 10, 6, 5, 0, tzinfo=UTC)  # 14:00 KST
CREDS = KisCredentials(
    app_key=SecretStr("PSappKEY0123456789abcdef"), app_secret=SecretStr("SECRETvalue9876543210zyx")
)
P_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-price"


def seed(r: fakeredis.FakeRedis, value: str, issued_ago: timedelta = timedelta(hours=9)) -> None:
    rec = TokenRecord(
        access_token=SecretStr(value),
        expires_at=NOW - issued_ago + timedelta(hours=24),
        issued_at=NOW - issued_ago,
        owner=CREDS.owner,
    )
    RedisTokenCache(r).store(rec, NOW)


def test_rejected_key_name_is_the_contract() -> None:
    assert rejected_key(KIS_TOKEN) == KIS_TOKEN_REJECTED == "kis:token:rejected"
    assert RedisTokenCache(fakeredis.FakeRedis()).rejected_key == KIS_TOKEN_REJECTED


def test_invalidate_reports_instead_of_deleting() -> None:
    r = fakeredis.FakeRedis()
    seed(r, "BADTOKEN")
    p = reader(r, CREDS, now=lambda: NOW, by="scheduler")
    assert p.get() == "BADTOKEN"
    p.invalidate()
    assert r.exists(KIS_TOKEN)  # 키는 그대로 — 바꾸는 것은 auth 몫
    got: dict[bytes, bytes] = r.hgetall(KIS_TOKEN_REJECTED)  # pyright: ignore[reportAssignmentType]
    raw = {k.decode(): v.decode() for k, v in got.items()}
    assert raw == {
        "token_sha16": token_digest("BADTOKEN"),
        "at": NOW.isoformat(),
        "by": "scheduler",
    }
    assert "BADTOKEN" not in str(raw)  # 토큰 원문은 남기지 않는다
    assert 0 < r.ttl(KIS_TOKEN_REJECTED) <= 3600
    report = RedisTokenCache(r).rejection()
    assert report is not None and report.by == "scheduler"


def test_rejected_token_is_not_served_again_until_auth_replaces_it() -> None:
    r = fakeredis.FakeRedis()
    seed(r, "BADTOKEN")
    p = reader(r, CREDS, now=lambda: NOW)
    p.get()
    p.invalidate()
    with pytest.raises(TokenUnavailable, match="거절"):
        p.get()
    seed(r, "NEWTOKEN", issued_ago=timedelta(0))  # auth 가 새로 넣었다
    assert p.get() == "NEWTOKEN"


def test_same_token_report_is_kept_and_a_new_token_report_replaces_it() -> None:
    r = fakeredis.FakeRedis()
    cache = RedisTokenCache(r)
    assert cache.pending_rejection("T1") is None
    assert cache.report_rejected("T1", NOW, "a")
    assert not cache.report_rejected("T1", NOW + timedelta(seconds=5), "b")  # 같은 토큰 — 그대로
    pending = cache.pending_rejection("T1")
    assert pending is not None and (pending.by, pending.at) == ("a", NOW)
    assert cache.pending_rejection("T2") is None  # 다른 값에는 걸려 있지 않다
    assert cache.close_rejection("T1", NOW + timedelta(seconds=10))  # auth 가 닫는다(한 번만)
    assert not cache.close_rejection("T1", NOW + timedelta(seconds=20))
    assert cache.pending_rejection("T1") is None
    assert not cache.report_rejected("T1", NOW + timedelta(seconds=30), "c")  # 닫힌 신고는 그대로
    assert cache.pending_rejection("T1") is None
    closed = cache.rejection()
    assert closed is not None and closed.handled_at == NOW + timedelta(seconds=10)
    assert cache.report_rejected("T2", NOW + timedelta(minutes=1), "d")  # 다른 토큰 — 새 신고
    assert cache.pending_rejection("T1") is None
    got = cache.pending_rejection("T2")
    assert got is not None and got.by == "d" and got.handled_at is None
    assert not cache.close_rejection("T1", NOW + timedelta(minutes=2))  # 지난 값은 닫지 못한다


def test_broken_report_is_ignored() -> None:
    r = fakeredis.FakeRedis()
    r.hset(KIS_TOKEN_REJECTED, mapping={"token_sha16": token_digest("T1"), "at": "not-a-time"})
    assert RedisTokenCache(r).pending_rejection("T1") is None


def test_rest_client_retries_once_then_fails_without_issuing() -> None:
    r = fakeredis.FakeRedis()
    seed(r, "BADTOKEN")
    seen: list[httpx.Request] = []

    def kis(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00123"})

    c = KisRestClient(
        CREDS, reader(r, CREDS, now=lambda: NOW), None, transport=httpx.MockTransport(kis)
    )
    with pytest.raises(TokenUnavailable):
        c.get(P_PRICE, "FHKST01010100", {"FID_INPUT_ISCD": "005930"})
    assert [q.method for q in seen] == [
        "GET"
    ]  # 같은 토큰으로 헛되이 다시 보내지도, 발급하지도 않는다
    assert r.exists(KIS_TOKEN) and r.exists(KIS_TOKEN_REJECTED)


def test_rest_client_recovers_when_auth_already_replaced_the_token() -> None:
    r = fakeredis.FakeRedis()
    seed(r, "OLDTOKEN")
    seen: list[str] = []

    def kis(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers["authorization"])
        if req.headers["authorization"] == "Bearer OLDTOKEN":
            seed(r, "NEWTOKEN", issued_ago=timedelta(0))  # 그 사이 auth 가 갱신했다
            return httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00121"})
        return httpx.Response(200, json={"rt_cd": "0", "msg_cd": "MCA00000"})

    c = KisRestClient(
        CREDS, reader(r, CREDS, now=lambda: NOW), None, transport=httpx.MockTransport(kis)
    )
    assert c.get(P_PRICE, "FHKST01010100", {"FID_INPUT_ISCD": "005930"}).ok
    assert seen == ["Bearer OLDTOKEN", "Bearer NEWTOKEN"]


def test_report_failure_on_redis_is_token_unavailable_and_the_token_stays_refused() -> None:
    """Redis 장애로 신고를 못 쓰면 읽는 쪽 Redis 장애와 같게 `TokenUnavailable`(설계 §3.4 표)."""
    server = fakeredis.FakeServer()
    r = fakeredis.FakeRedis(server=server)
    seed(r, "BADTOKEN")
    p = reader(r, CREDS, now=lambda: NOW, by="scheduler")
    assert p.get() == "BADTOKEN"
    server.connected = False
    with pytest.raises(TokenUnavailable, match="거절 신고 실패") as ei:
        p.invalidate()
    assert "BADTOKEN" not in str(ei.value)
    server.connected = True
    with pytest.raises(TokenUnavailable, match="거절"):
        p.get()  # 신고는 못 했어도 이 프로세스는 그 토큰을 다시 쓰지 않는다

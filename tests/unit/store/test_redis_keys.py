"""Redis 키 이름은 서비스끼리의 계약이다 — 값을 그대로 고정한다(GX test_services_runtime:40 식).

legacy GX(poller·ws-gateway·scheduler)가 P7 까지 같은 Redis 를 쓰므로 GX 와 같은 이름이어야 하는
키(`kis:token`·`rl:kis:<해시>`·`krx:calls:<날짜>`·`session:state`·`health:heartbeat:<서비스>`)는 GX
식과 나란히 비교한다.
"""

from __future__ import annotations

import hashlib
from datetime import date

import pytest

from kbj.store import redis_keys as k

D = date(2026, 10, 6)
APP_KEY = "PSfakeAppKey0123456789"


def test_constant_names_are_the_contract() -> None:
    assert (k.KIS_TOKEN, k.KIS_WS_KEY, k.KIS_TOKEN_REJECTED) == (
        "kis:token",
        "kis:ws_key",
        "kis:token:rejected",
    )
    assert (k.SESSION_STATE, k.SESSION_EVENTS) == ("session:state", "session.events")
    assert (k.NOTIFY_OUTBOX, k.NOTIFY_INBOUND, k.NOTIFY_WEBHOOK_INFO) == (
        "notify:outbox",
        "notify:inbound",
        "notify:webhook_info",
    )
    names = [
        k.KIS_TOKEN,
        k.KIS_WS_KEY,
        k.KIS_TOKEN_REJECTED,
        k.SESSION_STATE,
        k.SESSION_EVENTS,
        k.NOTIFY_OUTBOX,
        k.NOTIFY_INBOUND,
        k.NOTIFY_WEBHOOK_INFO,
    ]
    assert len(set(names)) == len(names)


def test_function_keys_are_the_contract() -> None:
    assert k.issue_blocked(k.KIS_TOKEN) == "kis:token:issue_blocked_until"  # GX RedisTokenCache
    assert k.issue_blocked(k.KIS_WS_KEY) == "kis:ws_key:issue_blocked_until"
    assert k.heartbeat_key("auth") == "health:heartbeat:auth"
    assert k.krx_calls_key(D) == "krx:calls:20261006"
    assert k.budget_key("dart", None, D) == "budget:dart:20261006"
    assert k.budget_key("datago", "15100475", D) == "budget:datago:15100475:20261006"
    assert k.budget_closed_key("budget:dart:20261006") == "budget:dart:20261006:closed"
    assert k.notify_dedup_key("brief.closing:20261006") == "notify:dedup:brief.closing:20261006"
    assert k.tg_update_key(912345) == "tg:update:912345"
    assert k.sched_run_lock("filings.corp_code", "2026-10-06") == (
        "sched:run:filings.corp_code:2026-10-06"
    )


def test_rate_limit_key_matches_gx_and_hides_the_scope() -> None:
    gx = "rl:kis:" + hashlib.sha256(APP_KEY.encode()).hexdigest()[:16]  # GX limiter_key 식
    assert k.rate_limit_key("kis", APP_KEY) == gx
    assert k.rate_limit_wait_key(gx) == gx + ":wait"
    dart = k.rate_limit_key("dart", "fake-dart-key")
    assert dart.startswith("rl:dart:") and "fake-dart-key" not in dart and len(dart) == 8 + 16
    assert k.rate_limit_key("datago", "15100475") != k.rate_limit_key("datago", "15101609")


@pytest.mark.parametrize(
    "call",
    [
        lambda: k.rate_limit_key("KIS", "x"),
        lambda: k.rate_limit_key("kis", ""),
        lambda: k.budget_key("Dart", None, D),
        lambda: k.budget_key("datago", "", D),
        lambda: k.budget_key("datago", "a b", D),
        lambda: k.heartbeat_key(""),
        lambda: k.notify_dedup_key(""),
        lambda: k.tg_update_key(-1),
        lambda: k.tg_update_key(True),  # pyright: ignore[reportArgumentType]
        lambda: k.sched_run_lock("job", ""),
        lambda: k.issue_blocked(""),
    ],
)
def test_malformed_parts_are_refused(call: object) -> None:
    with pytest.raises(ValueError):
        call()  # pyright: ignore[reportCallIssue]


def test_secret_digest_is_sha256_prefix() -> None:
    assert k.secret_digest("abc") == hashlib.sha256(b"abc").hexdigest()[:16]


# ── P3 웹 로그인·API 캐시 키(docs/p3_design.md §5.3·§5.4) ─────────────────────────────────────


def test_web_keys_take_digests_only() -> None:
    sid = "synthetic-session-id-0123456789"
    digest = k.web_digest(sid)
    assert digest == hashlib.sha256(sid.encode()).hexdigest()[:32]
    assert k.web_session_key(digest) == f"web:session:{digest}"
    ip16 = k.web_digest("192.0.2.10", 16)
    assert k.web_login_fail_key(ip16) == f"web:login_fail:{ip16}"
    assert (k.WEB_LOGIN_LOCK, k.WEB_LOGIN_FAIL_ALL) == ("web:login_lock", "web:login_fail:all")
    for bad in (sid, digest[:16], digest.upper(), ""):
        with pytest.raises(ValueError):
            k.web_session_key(bad)  # 원문·길이 다름·대문자는 거부 — 키에 원문이 남지 않게
    with pytest.raises(ValueError):
        k.web_login_fail_key("192.0.2.10")
    with pytest.raises(ValueError):
        k.web_digest("")
    assert k.WEB_LOGIN_FAIL_ALL != k.web_login_fail_key(ip16)


def test_api_data_version_key() -> None:
    assert k.api_data_version_key("flows") == "api:data_version:flows"
    for bad in ("Flows", "", "a b", "board:x"):
        with pytest.raises(ValueError):
            k.api_data_version_key(bad)

"""`POST /telegram/webhook` — P2 처리기를 감싼 경로(docs/p3_design.md §5.5)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pydantic import SecretStr

from kbj.services.notifier.webhook import SECRET_HEADER, WEBHOOK_PATH
from kbj.store.redis_keys import NOTIFY_INBOUND
from tests.unit.api.api_world import World, build_world

SECRET = "webhook-secret-for-tests"


def _update(uid: int) -> dict[str, object]:
    return {"update_id": uid, "message": {"message_id": 1, "chat": {"id": 1}, "text": "/상태"}}


def _xlen(world: World) -> int:
    return int(world.redis.xlen(NOTIFY_INBOUND)) if world.redis.exists(NOTIFY_INBOUND) else 0


def test_wrong_secret_403(world: World, client: TestClient) -> None:
    r = client.post(WEBHOOK_PATH, json=_update(1), headers={SECRET_HEADER: "wrong"})
    assert r.status_code == 403
    assert r.json() == {"ok": False}
    assert _xlen(world) == 0
    assert client.post(WEBHOOK_PATH, json=_update(1)).status_code == 403  # 헤더 없음


def test_right_secret_queues_once(world: World, client: TestClient) -> None:
    r = client.post(WEBHOOK_PATH, json=_update(42), headers={SECRET_HEADER: SECRET})
    again = client.post(WEBHOOK_PATH, json=_update(42), headers={SECRET_HEADER: SECRET})
    for x in (r, again):  # 같은 update_id 두 번 → 한 번만
        assert x.status_code == 200 and x.json() == {"ok": True}
    assert _xlen(world) == 1
    assert "set-cookie" not in r.headers  # 세션·CSRF·Origin 면제 — 쿠키 없이 동작


def test_unset_secret_never_accepts() -> None:
    w = build_world(intraday=False, telegram_webhook_secret=None)
    c = w.client()
    r = c.post(WEBHOOK_PATH, json=_update(3), headers={SECRET_HEADER: SECRET})
    assert r.status_code == 403 and r.json() == {"ok": False}  # §5.5 "늘 403"
    assert _xlen(w) == 0
    w2 = build_world(intraday=False, telegram_webhook_secret=SecretStr(""))
    r2 = w2.client().post(WEBHOOK_PATH, json=_update(4), headers={SECRET_HEADER: ""})
    assert r2.status_code == 403 and r2.json() == {"ok": False}
    assert _xlen(w2) == 0


def test_large_body_413(world: World, client: TestClient) -> None:
    big = json.dumps({"update_id": 9, "pad": "x" * (1024 * 1024 + 10)})
    r = client.post(
        WEBHOOK_PATH,
        content=big,
        headers={SECRET_HEADER: SECRET, "content-type": "application/json"},
    )
    assert r.status_code == 413
    assert _xlen(world) == 0


def test_get_not_allowed(client: TestClient) -> None:
    assert client.get(WEBHOOK_PATH).status_code == 405

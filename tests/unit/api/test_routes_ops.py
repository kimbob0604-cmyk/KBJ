"""`/api/ops/*` — 페이지 13 '운영' 최소판(로그인 전용·읽기만·짧은 캐시, ADR 0037)."""

from __future__ import annotations

import json
from datetime import time

import pytest
from fastapi.testclient import TestClient

from kbj.services.api.models.common import Envelope
from kbj.services.api.models.ops import OpsJobs, OpsNotify, OpsStatus
from kbj.services.api.readers.ops import STATE_ATTR, OpsContext
from kbj.services.ops.source import NotifyRow
from tests.unit.api.api_world import BASE, PASSWORD, USER
from tests.unit.api.web_fixtures_p13.ops import (
    DAY,
    NOW,
    OUT,
    files_on_disk,
    make_app,
    render,
    source,
)
from tests.unit.ops.ops_world import EXPECT, fresh_ages, kst

PATHS = ("/api/ops/status", "/api/ops/jobs", "/api/ops/notify")


def _client(**kw: object) -> TestClient:
    c = TestClient(make_app(**kw), base_url=BASE)  # pyright: ignore[reportArgumentType]
    r = c.post("/api/auth/login", json={"username": USER, "password": PASSWORD},
               headers={"Origin": BASE})  # fmt: skip
    assert r.status_code == 200
    return c


@pytest.mark.parametrize("path", PATHS)
def test_login_required(path: str) -> None:
    with TestClient(make_app(), base_url=BASE) as c:
        r = c.get(path)
    assert r.status_code == 401


def test_status_same_as_check() -> None:
    from kbj.core.calendar import TradingCalendar
    from kbj.services.ops.check import run_check

    body = _client().get("/api/ops/status").json()
    env = Envelope[OpsStatus].model_validate(body)
    want = run_check(
        source(), EXPECT, TradingCalendar.default(), fresh_ages(NOW, stale={"notifier": 75.0}), NOW
    )
    assert env.data.level == want.level == "WARN"
    assert [(s.name, s.level) for s in env.data.services] == [
        (s.name, s.level) for s in want.services
    ]
    assert [i.key for i in env.data.items] == [i.key for i in want.items]
    assert env.source and env.quality == "ok" and env.as_of == NOW


def test_jobs_for_day_and_default_today() -> None:
    c = _client()
    env = Envelope[OpsJobs].model_validate(c.get(f"/api/ops/jobs?date={DAY}").json())
    assert env.data.date == DAY and env.data.total == 4
    assert env.data.retried_ok == 1 and env.data.failed == 0
    board = next(r for r in env.data.rows if r.job == "board.daily")
    assert board.attempts == 2 and board.status == "ok" and board.retried_ok
    today = Envelope[OpsJobs].model_validate(c.get("/api/ops/jobs").json())
    assert today.data.date == DAY  # 시계 기준 오늘(KST)


def test_notify_no_body_and_masked_reason() -> None:
    src = source()
    token = "bot" + "1234567890" + ":" + "AAH" + "x" * 32  # 토큰 모양 — 응답에 남으면 안 된다
    src.notify.append(
        NotifyRow("brief.closing", "본문 제목", "failed", kst(DAY, time(16, 3)), None,
                  as_of=DAY, reason=f"전송 실패 {token}")
    )  # fmt: skip
    c = _client(ops=OpsContext(src, EXPECT, fresh_ages(NOW)))
    r = c.get(f"/api/ops/notify?date={DAY}")
    env = Envelope[OpsNotify].model_validate(r.json())
    assert env.data.failed == 2 and env.data.sent >= 2
    assert token not in r.text and "본문 제목" not in r.text
    assert "subject" not in r.json()["data"]["rows"][0]


def test_bad_date_is_422() -> None:
    r = _client().get("/api/ops/jobs?date=2026-13-01")
    assert r.status_code == 422
    assert "2026-13-01" not in r.text


def test_no_source_is_404_no_data() -> None:
    app = make_app()
    delattr(app.state, STATE_ATTR)
    with TestClient(app, base_url=BASE) as c:
        c.post("/api/auth/login", json={"username": USER, "password": PASSWORD},
               headers={"Origin": BASE})  # fmt: skip
        r = c.get("/api/ops/status")
    assert r.status_code == 404 and r.json()["code"] == "no_data"


def test_etag_and_short_cache() -> None:
    c = _client()
    r1 = c.get("/api/ops/status")
    etag = r1.headers["etag"]
    assert r1.headers["cache-control"].startswith("private")
    r2 = c.get("/api/ops/status", headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_fixtures_are_fresh() -> None:
    """다르면 `uv run python -m tests.unit.api.web_fixtures_p13.ops` 로 다시 쓴다."""
    want = render()
    assert set(want) == files_on_disk(), "ops_*.json 목록이 낡았다"
    for name, text in want.items():
        assert (OUT / name).read_text(encoding="utf-8") == text, name


def test_fixtures_valid_envelopes() -> None:
    models = {"ops_status": OpsStatus, "ops_jobs": OpsJobs, "ops_notify": OpsNotify}
    for name in files_on_disk():
        body = json.loads((OUT / name).read_text(encoding="utf-8"))
        if name == "ops_status_none.json":
            assert body["code"] == "no_data"
            continue
        key = next(k for k in models if name.startswith(k))
        Envelope[models[key]].model_validate(body)  # pyright: ignore[reportUnknownMemberType]

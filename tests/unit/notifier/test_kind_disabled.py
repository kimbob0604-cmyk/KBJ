"""종류별 끄기(`notify.yaml` 의 `enabled: false`) — 전체 스위치가 켜져도 그 종류만 안 보낸다.

기대: 운영 알림 3종(ops.watchdog·ops.job_failed·ops.universe)은 `suppressed`(사유에 enabled: false),
다른 종류(brief.morning)는 그대로 나간다. 발송 요청 수로 확인한다(외부 HTTP 는 가짜 전송).
"""

from __future__ import annotations

import fakeredis
import httpx

from kbj.services.notifier.policy import NotifyConfig, parse_notify_config
from kbj.services.notifier.service import KIND_DISABLED_REASON, build_service
from kbj.services.notifier.store import MemoryInboxStore, MemoryNotifyLogStore
from tests.fakes.clock import FakeClock
from tests.unit.notifier.conftest import make_settings

OPS_OFF = ("ops.watchdog", "ops.job_failed", "ops.universe")


def test_real_config_turns_the_three_ops_kinds_off(real_config: NotifyConfig) -> None:
    for kind in OPS_OFF:
        assert real_config.policy(kind).enabled is False
    assert real_config.policy("brief.morning").enabled is True
    assert real_config.policy("alert.rule").enabled is True


def test_enabled_must_be_a_bool() -> None:
    base = {
        "version": 1,
        "topics": {"market": None, "newhigh": None, "alert": None, "ops": None},
        "kinds": {"ops.x": {"topic": "ops", "dedup": "body", "enabled": "아니오"}},
    }
    try:
        parse_notify_config(base)
    except Exception as e:
        assert "enabled" in str(e)
    else:
        raise AssertionError("bool 이 아닌 enabled 가 받아졌다")


def test_switch_on_but_kind_off_sends_only_the_other_kinds(
    real_config: NotifyConfig, clock: FakeClock, no_real_http: list[str]
) -> None:
    calls: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    t = httpx.MockTransport(answer)
    log = MemoryNotifyLogStore()
    svc = build_service(
        make_settings(notify_enabled=True),
        config=real_config,
        redis=fakeredis.FakeRedis(),
        log_store=log,
        inbox_store=MemoryInboxStore(),
        now=clock,
        telegram_transport=t,
        oembed_transport=t,
        limiter_clock=clock,
        sleep=clock.sleep,
    )
    assert svc.router is not None
    client = svc.router.client

    off = client.notify("[워치독] 시험", kind="ops.watchdog", source="t", subject="missed:x")
    assert off.ok and off.reason == "suppressed"
    client.notify("[작업 실패] 시험", kind="ops.job_failed", source="t", subject="job:1")
    client.notify("<b>아침</b>", kind="brief.morning", source="t")
    svc.step()

    sends = [p for p in calls if p.endswith("sendMessage")]
    assert len(sends) == 1  # brief.morning 한 통만
    by_kind = {r.kind: r for r in log.rows}
    assert by_kind["brief.morning"].status == "sent"
    for kind in ("ops.watchdog", "ops.job_failed"):
        assert by_kind[kind].status == "suppressed"
        assert by_kind[kind].reason == KIND_DISABLED_REASON
    assert no_real_http == []

"""`/gex` 조립 — build_service 의 `gex` 읽기 함수가 명령표까지 닿는다(P7 WRAP, OUT 요청).

진입점(`__main__`)은 `command_reader(PgGexRepo(...), utcnow)` 를 넘긴다. 여기서는 같은 조립 길에
가짜 읽기 함수를 끼워 '준비 중(P7)' 이 아니라 그 답이 나오는지, 안 주면 여전히 '준비 중' 인지 본다.
"""

from __future__ import annotations

from collections.abc import Callable

import fakeredis
from pydantic import SecretStr

from kbj.services.notifier.policy import NotifyConfig
from kbj.services.notifier.service import NotifierService, build_service
from kbj.services.notifier.store import MemoryInboxStore, MemoryNotifyLogStore
from kbj.services.runtime import MemoryHealthSink
from tests.fakes.clock import FakeClock
from tests.unit.notifier.conftest import CHAT, make_settings


def _svc(
    config: NotifyConfig, clock: FakeClock, gex: Callable[[], str] | None = None
) -> NotifierService:
    return build_service(
        make_settings(telegram_inbox_chat_ids=SecretStr(CHAT)),
        config=config,
        redis=fakeredis.FakeRedis(),
        log_store=MemoryNotifyLogStore(),
        inbox_store=MemoryInboxStore(),
        now=clock,
        health=MemoryHealthSink(),
        limiter_clock=clock,
        sleep=clock.sleep,
        gex=gex,
    )


def test_gex_reader_reaches_command_registry(config: NotifyConfig, clock: FakeClock) -> None:
    svc = _svc(config, clock, gex=lambda: "📐 GEX 합성 요약")
    assert svc.router is not None
    r = svc.router.commands.dispatch("/gex", chat_id=1)
    assert r is not None and r.text == "📐 GEX 합성 요약"


def test_without_reader_gex_is_not_ready(config: NotifyConfig, clock: FakeClock) -> None:
    svc = _svc(config, clock)
    assert svc.router is not None
    r = svc.router.commands.dispatch("/gex", chat_id=1)
    assert r is not None and "준비 중" in r.text

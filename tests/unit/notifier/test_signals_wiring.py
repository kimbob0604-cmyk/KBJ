"""`/시그널` 조립 — build_service 의 `signals` 읽기 함수가 명령표까지 닿는다(P8 WRAP, EVL 요청).

진입점(`__main__`)은 `kbj.services.engine.rules.command_reader(pg_repos(...), utcnow)` 를
넘긴다. 여기서는 같은 조립 길에 가짜 읽기 함수를 끼워 그 답이 나오는지, 안 주면 '준비 중'
인지 본다(test_gex_wiring 과 같은 꼴).
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
    config: NotifyConfig, clock: FakeClock, signals: Callable[[], str] | None = None
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
        signals=signals,
    )


def test_signals_reader_reaches_command_registry(config: NotifyConfig, clock: FakeClock) -> None:
    svc = _svc(config, clock, signals=lambda: "🔔 오늘 규칙 신호 합성 2건")
    assert svc.router is not None
    for cmd in ("/시그널", "/signals"):
        r = svc.router.commands.dispatch(cmd, chat_id=1)
        assert r is not None and r.text == "🔔 오늘 규칙 신호 합성 2건", cmd


def test_without_reader_signals_is_not_ready(config: NotifyConfig, clock: FakeClock) -> None:
    svc = _svc(config, clock)
    assert svc.router is not None
    r = svc.router.commands.dispatch("/시그널", chat_id=1)
    assert r is not None and "준비 중" in r.text

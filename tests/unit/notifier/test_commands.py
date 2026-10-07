"""명령 분배(kbj.services.notifier.commands) — 설계 §5.7, SD `_handle_telegram_command` 동작."""

from __future__ import annotations

import pytest

from kbj.services.notifier.commands import (
    CommandContext,
    CommandRegistry,
    Reply,
    default_registry,
    parse_command,
)


@pytest.mark.parametrize("text", ["/도움", "/help", "/START", "/명령", "/도움@kbj_bot", " /help "])
def test_help_and_aliases_work_in_p2(text: str) -> None:
    r = default_registry().dispatch(text, chat_id=1)
    assert r is not None and "명령어" in r.text and r.parse_mode == "HTML"
    assert "/시황" in r.text and "&lt;종목&gt;" in r.text  # HTML 이스케이프


@pytest.mark.parametrize(
    ("text", "phase"),
    [
        ("/시황", "P3"),
        ("/summary", "P3"),
        ("/수급 삼성전자", "P3"),
        ("/가격 005930", "P3"),
        ("/price", "P3"),
        ("/신고가", "P3"),
        ("/gex", "P7"),
        ("/시그널", "P8"),
        ("/signals@kbj_bot", "P8"),
    ],
)
def test_not_yet_connected_commands_say_when(text: str, phase: str) -> None:
    r = default_registry().dispatch(text, chat_id=1)
    assert r is not None and "준비 중" in r.text and phase in r.text


def test_unknown_command_lists_help() -> None:
    r = default_registry().dispatch("/모르는것", chat_id=1)
    assert r is not None and "알 수 없는 명령" in r.text and "명령어" in r.text


def test_not_a_command_is_none() -> None:
    reg = default_registry()
    assert reg.dispatch("안녕", chat_id=1) is None
    assert reg.dispatch("/", chat_id=1) is None
    assert reg.dispatch(None, chat_id=1) is None


def test_bot_suffix_and_argument_parsing() -> None:
    assert parse_command("/수급@kbj_bot  삼성전자 우") == ("수급", "삼성전자 우")
    assert parse_command("/GEX") == ("gex", "")
    assert parse_command("메모 /수급") is None


def test_missing_argument_gives_usage_when_connected() -> None:
    reg = default_registry()
    seen: list[CommandContext] = []

    def flow(ctx: CommandContext) -> Reply:
        seen.append(ctx)
        return Reply(f"{ctx.arg} 수급")

    reg.register("수급", ("flow",), flow, phase="P3", summary="수급", usage="/수급 <종목>")
    r = reg.dispatch("/flow", chat_id=7)
    assert r is not None and r.text == "사용법: /수급 &lt;종목&gt;"
    r2 = reg.dispatch("/flow 삼성전자", chat_id=7, thread_id=3, update_id=99)
    assert r2 is not None and r2.text == "삼성전자 수급"
    assert seen[0].name == "수급" and seen[0].called == "flow" and seen[0].thread_id == 3
    assert seen[0].update_id == 99


def test_handler_error_is_reported_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    reg = CommandRegistry()

    def boom(ctx: CommandContext) -> Reply:
        raise RuntimeError("비밀 token=abcdef 가 든 오류")

    reg.register("x", handler=boom)
    r = reg.dispatch("/x", chat_id=1)
    assert r is not None and "처리 중 오류" in r.text and "RuntimeError" in r.text
    assert "abcdef" not in r.text  # 예외 문구는 답에 싣지 않는다
    assert any("RuntimeError" in m for m in caplog.messages)


def test_alias_collision_is_refused() -> None:
    reg = CommandRegistry()
    reg.register("a", ("b",))
    with pytest.raises(ValueError):
        reg.register("c", ("b",))

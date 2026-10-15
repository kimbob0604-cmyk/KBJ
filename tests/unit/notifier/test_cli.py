"""`python -m kbj.services.notifier` 하위 명령 — setup-webhook(확인 없이는 안 건다)·
webhook-info·check.

출력에 토큰·시크릿·chat id 가 없는지도 본다. 실제 텔레그램 대신 FakeTelegram transport.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import SecretStr

from kbj.services.notifier import __main__ as cli
from kbj.services.notifier.telegram_api import TelegramApi
from tests.unit.notifier.conftest import CHAT, FAKE_TOKEN, SECRET, FakeTelegram, make_settings, ok


@pytest.fixture
def tg(monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    fake = FakeTelegram()

    def api(token: SecretStr | None, **kw: Any) -> TelegramApi:
        return TelegramApi(token, transport=fake.transport, **kw)

    monkeypatch.setattr(cli, "TelegramApi", api)
    return fake


def _out(capsys: pytest.CaptureFixture[str]) -> str:
    out = capsys.readouterr().out
    for secret in (FAKE_TOKEN, SECRET, CHAT):
        assert secret not in out
    return out


def test_setup_webhook_without_confirm_only_shows_the_plan(
    tg: FakeTelegram, capsys: pytest.CaptureFixture[str]
) -> None:
    s = make_settings(public_base_url="https://kbj.example")
    assert cli.cmd_setup_webhook(s, confirm=False) == 0
    out = _out(capsys)
    assert "미실행" in out and "kbj.example/telegram/webhook" in out and "P3" in out
    assert tg.calls == []


def test_setup_webhook_with_confirm_calls_set_webhook(
    tg: FakeTelegram, capsys: pytest.CaptureFixture[str]
) -> None:
    s = make_settings(public_base_url="https://kbj.example/")
    assert cli.cmd_setup_webhook(s, confirm=True) == 0
    (d,) = tg.sent("setWebhook")
    assert d["url"] == "https://kbj.example/telegram/webhook" and d["secret_token"] == SECRET
    assert json.loads(d["allowed_updates"]) == ["message", "edited_message", "channel_post"]
    assert d["drop_pending_updates"] == "false"
    assert "완료" in _out(capsys)


@pytest.mark.parametrize(
    "kw",
    [
        {"public_base_url": None},
        {"public_base_url": "http://plain.example"},
        {"public_base_url": "https://kbj.example", "telegram_webhook_secret": None},
    ],
)
def test_setup_webhook_config_errors(tg: FakeTelegram, kw: dict[str, Any]) -> None:
    assert cli.cmd_setup_webhook(make_settings(**kw), confirm=True) == 2
    assert tg.calls == []


def test_webhook_info_prints_host_only(
    tg: FakeTelegram, capsys: pytest.CaptureFixture[str]
) -> None:
    tg.webhook = {"url": "https://kbj.example/telegram/webhook", "pending_update_count": 3}
    assert cli.cmd_webhook_info(make_settings()) == 0
    info = json.loads(_out(capsys))
    assert info == {
        "url_set": True,
        "host": "kbj.example",
        "pending_update_count": 3,
        "last_error_date": None,
        "last_error_message": None,
        "allowed_updates": None,
    }


def test_check_does_not_send(tg: FakeTelegram, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.cmd_check(make_settings()) == 0
    assert tg.methods() == ["getMe", "getChat"]
    assert "리서치방" in _out(capsys)


def test_main_dispatches_and_maps_config_errors(
    tg: FakeTelegram, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "Settings", lambda: make_settings())
    assert cli.main(["setup-webhook"]) == 2  # KBJ_PUBLIC_BASE_URL 없음
    assert cli.main(["check"]) == 0

    def broken() -> Any:
        raise ValueError("KBJ_API_PORT=abc")

    monkeypatch.setattr(cli, "Settings", broken)
    assert cli.main(["webhook-info"]) == 2
    assert "abc" not in capsys.readouterr().out


def test_run_refuses_without_service_name() -> None:
    assert cli.cmd_run(make_settings()) == 2  # KBJ_SERVICE 가 notifier 가 아니면 돌지 않는다


def test_list_chats_prints_title_and_id_lines_without_sending(
    tg: FakeTelegram, capsys: pytest.CaptureFixture[str]
) -> None:
    tg.script["getUpdates"] = [
        ok(
            [
                {"message": {"chat": {"id": -1001, "type": "supergroup", "title": "리서치\t방"}}},
                {"channel_post": {"chat": {"id": -1002, "type": "channel", "title": "알림"}}},
                {"message": {"chat": {"id": 7, "type": "private", "first_name": "김"}}},
                {"message": {"text": "chat 없음"}},
            ]
        )
    ]
    assert cli.cmd_list_chats(make_settings()) == 0
    assert tg.methods() == ["getUpdates"]
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["리서치 방 [슈퍼그룹]\t-1001", "알림 [채널]\t-1002", "김 [1:1]\t7"]


def test_list_chats_exit_3_when_bot_saw_no_chat(tg: FakeTelegram) -> None:
    tg.script["getUpdates"] = [ok([])]
    assert cli.cmd_list_chats(make_settings()) == 3


def test_list_chats_api_failure_is_exit_1(
    tg: FakeTelegram, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.cmd_list_chats(make_settings()) == 1  # 가짜에 getUpdates 응답이 없으면 404
    assert "getUpdates 실패" in _out(capsys)

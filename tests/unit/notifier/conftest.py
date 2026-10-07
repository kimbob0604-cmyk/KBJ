"""notifier 시험 공통 — 가짜 텔레그램(httpx MockTransport)·fakeredis·고정 시계·설정.

- 실제 네트워크는 쓰지 않는다. 모든 `TelegramApi` 는 `FakeTelegram.transport` 를 받는다.
- 시계는 `FakeClock`(tests/fakes/clock.py) — 벽시계에 기대지 않는다(CLAUDE.md §4).
- 설정은 `Settings(_env_file=None, …)` — 실제 `.env` 를 읽지 않는다. 봇 토큰·chat id 는 형태만
  흉내 낸 가짜 값이다.
- 하루 시뮬레이션용 가짜 텔레그램 서버(`tests/fakes/telegram_server.py`)는 묶음 I 몫이다 — 여기
  것은 단위 시험용이다.

`from tests.unit.notifier.conftest import …` 로 쓴다.
"""

from __future__ import annotations

import email.parser
import email.policy
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.services.notifier.policy import NotifyConfig, load_notify_config
from tests.fakes.clock import FakeClock

KST = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 10, 6, 8, 10, tzinfo=KST)  # 화 08:10 KST — brief.morning 시각
# 봇 토큰 형태의 가짜 값 — 한 덩어리로 두면 공개 안전 검사(telegram_bot_token 형태)에 걸려 잇는다
FAKE_TOKEN = "123456789:" + "AAFakeTokenForTestsOnly" + "_abcdefghijk0123"
CHAT = "-1001234567890"
SECRET = "webhook-secret-for-tests"


def make_settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "telegram_bot_token": SecretStr(FAKE_TOKEN),
        "telegram_chat_id": SecretStr(CHAT),
        "telegram_webhook_secret": SecretStr(SECRET),
    }
    base.update(kw)
    return Settings(_env_file=None, **base)  # pyright: ignore[reportCallIssue]


def form_of(request: httpx.Request) -> dict[str, Any]:
    """요청 본문 → {필드: 값}. urlencoded 와 multipart 둘 다(파일 필드는 (이름, 바이트))."""
    ctype = request.headers.get("content-type", "")
    body = request.read()
    if request.method == "GET":
        return dict(parse_qsl(request.url.query.decode()))
    if ctype.startswith("application/x-www-form-urlencoded"):
        return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
    if ctype.startswith("multipart/form-data"):
        raw = f"Content-Type: {ctype}\r\n\r\n".encode() + body
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(raw)
        out: dict[str, Any] = {}
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            payload = part.get_payload(decode=True)
            assert isinstance(payload, bytes)
            out[str(name)] = (filename, payload) if filename else payload.decode("utf-8")
        return out
    return {}


@dataclass
class FakeTelegram:
    """Bot API 가짜. `script[메서드]` 에 응답(httpx.Response 또는 예외)을 넣으면 차례로 쓴다."""

    calls: list[tuple[str, dict[str, Any]]] = field(
        default_factory=list[tuple[str, dict[str, Any]]]
    )
    urls: list[str] = field(default_factory=list[str])
    script: dict[str, list[Any]] = field(default_factory=dict[str, list[Any]])
    next_id: int = 1000
    me: str = "kbj_test_bot"
    chat_title: str = "리서치방"
    webhook: dict[str, Any] = field(default_factory=dict[str, Any])

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        self.urls.append(str(request.url))
        data = form_of(request)
        self.calls.append((method, data))
        queue = self.script.get(method)
        if queue:
            nxt = queue.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            if isinstance(nxt, httpx.Response):
                return nxt
            if callable(nxt):
                return cast(httpx.Response, nxt(request))
        return self.default(method, data)

    def default(self, method: str, data: dict[str, Any]) -> httpx.Response:
        if method == "sendMessage" or method == "sendDocument":
            self.next_id += 1
            return ok({"message_id": self.next_id})
        if method == "sendMediaGroup":
            n = len(json.loads(data.get("media", "[]")))
            ids = list(range(self.next_id + 1, self.next_id + 1 + n))
            self.next_id += n
            return ok([{"message_id": i} for i in ids])
        if method == "getMe":
            return ok({"id": 1, "is_bot": True, "username": self.me})
        if method == "getChat":
            return ok({"id": -1, "type": "supergroup", "title": self.chat_title})
        if method == "getWebhookInfo":
            return ok(self.webhook or {"url": "", "pending_update_count": 0})
        if method == "setWebhook":
            return ok(True)
        return httpx.Response(
            404, json={"ok": False, "error_code": 404, "description": "Not Found"}
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]

    def sent(self, method: str = "sendMessage") -> list[dict[str, Any]]:
        return [d for m, d in self.calls if m == method]


def ok(result: Any) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


def refused(status: int, description: str, **params: Any) -> httpx.Response:
    body: dict[str, Any] = {"ok": False, "error_code": status, "description": description}
    if params:
        body["parameters"] = params
    return httpx.Response(status, json=body)


@pytest.fixture
def tg() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


@pytest.fixture
def config() -> NotifyConfig:
    return load_notify_config(settings=Settings(_env_file=None))  # pyright: ignore[reportCallIssue]


@pytest.fixture
def no_real_http(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """실제 HTTP 전송이 일어나면 시험을 깨뜨린다(가짜 transport 를 안 넣은 클라이언트 잡기)."""
    tried: list[str] = []

    def refuse(self: object, request: httpx.Request) -> httpx.Response:
        tried.append(str(request.url.host))
        raise AssertionError(f"실제 HTTP 호출 시도: {request.url.host}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    yield tried


Now = Callable[[], datetime]

"""가짜 텔레그램 Bot API(`httpx.MockTransport`) — 하루 시뮬레이션용. 실제 네트워크 없음.

묶음 E 의 단위 시험용 가짜(tests/unit/notifier/conftest.py)와 같은 응답 모양에, 시뮬레이션이 쓰는
것을 더했다: 받은 시각(가짜 시계), 메서드별 기록, 429 주입(`retry_after`).

- `sendMessage`·`sendDocument`·`sendMediaGroup` → `message_id` 를 하나씩 늘려 준다.
- `getMe`·`getChat`·`getWebhookInfo`·`setWebhook` 정상 응답.
- `inject_429(count, retry_after=1, *, method="sendMessage")`: 다음 count 번을 429 로 거절.
- 기록 `calls`(시각·메서드·폼 필드) — 봇 토큰(경로)은 남기지 않는다.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl

import httpx


@dataclass(frozen=True)
class TgCall:
    at: datetime
    method: str
    data: dict[str, Any]
    status: int


def _form(request: httpx.Request) -> dict[str, Any]:
    ctype = request.headers.get("content-type", "")
    body = request.read()
    if request.method == "GET":
        return dict(parse_qsl(request.url.query.decode()))
    if ctype.startswith("application/x-www-form-urlencoded"):
        return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
    if ctype.startswith("application/json"):
        try:
            js: Any = json.loads(body or b"{}")
        except ValueError:
            return {}
        return dict(js) if isinstance(js, dict) else {}
    return {"_multipart": True} if ctype.startswith("multipart/form-data") else {}


def _ok(result: Any) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


class FakeTelegram:
    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now
        self.calls: list[TgCall] = []
        self.next_id = 1000
        self._inject: list[list[Any]] = []  # [method, count, retry_after]
        self._lock = threading.Lock()

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def inject_429(
        self, count: int = 1, retry_after: int = 1, *, method: str = "sendMessage"
    ) -> None:
        with self._lock:
            self._inject.append([method, count, retry_after])

    def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        data = _form(request)
        now = self._now()
        with self._lock:
            for rule in self._inject:
                if rule[0] == method and rule[1] > 0:
                    rule[1] -= 1
                    self.calls.append(TgCall(now, method, data, 429))
                    return httpx.Response(
                        429,
                        json={
                            "ok": False,
                            "error_code": 429,
                            "description": f"Too Many Requests: retry after {rule[2]}",
                            "parameters": {"retry_after": rule[2]},
                        },
                    )
            self.calls.append(TgCall(now, method, data, 200))
            if method in ("sendMessage", "sendDocument"):
                self.next_id += 1
                return _ok({"message_id": self.next_id})
            if method == "sendMediaGroup":
                n = len(json.loads(str(data.get("media", "[]")))) if "media" in data else 1
                ids = list(range(self.next_id + 1, self.next_id + 1 + n))
                self.next_id += n
                return _ok([{"message_id": i} for i in ids])
            if method == "getMe":
                return _ok({"id": 1, "is_bot": True, "username": "kbj_sim_bot"})
            if method == "getChat":
                return _ok({"id": -1, "type": "supergroup", "title": "시뮬레이션"})
            if method == "getWebhookInfo":
                return _ok(
                    {"url": "https://example.invalid/telegram/webhook", "pending_update_count": 0}
                )
            if method == "setWebhook":
                return _ok(True)
        return httpx.Response(
            404, json={"ok": False, "error_code": 404, "description": "Not Found"}
        )

    # ── 조회(시험) ───────────────────────────────────────────────────────────────────
    def methods(self) -> list[str]:
        with self._lock:
            return [c.method for c in self.calls]

    def sent(self, method: str = "sendMessage", *, ok_only: bool = True) -> list[TgCall]:
        with self._lock:
            return [
                c for c in self.calls if c.method == method and (c.status == 200 or not ok_only)
            ]


__all__ = ["FakeTelegram", "TgCall"]

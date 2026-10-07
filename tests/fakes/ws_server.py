"""가짜 KIS 웹소켓 서버(127.0.0.1 임의 포트) — 접속키로 등록·해제만 받는다. 합성 문구만.

GEXLAB `tests/fakes/ws_server.py`(`FakeKisWsServer`)의 KBJ 판. GX 판은 GX 설정(config/kis_ws.yaml)·
필드 표에 묶여 있어 그대로 옮기지 않고, 하루 시뮬레이션이 보는 것만 같은 이름으로 다시 만들었다:
**접속키가 auth 가 발급한 값인지**와 등록·해제 순서. 체결 프레임·PINGPONG·구독 한도는 ws-gateway 를
승격하는 P7 에 GX 판을 옮긴다(메인 결정 D2).

- 요청 형식(KIS 공식 샘플): header `approval_key`·`custtype`·`tr_type`(1 등록·2 해제)·
  `content-type`, body `{"input": {"tr_id", "tr_key"}}`. 어긋나면 `errors` 에 남기고 오류 응답.
- 접속키 판정은 `key_ok(접속키)` 콜백(가짜 KIS 의 `valid_approval_key`). 틀리면
  `invalid approval : NOT FOUND`(미실측 가정 문구 — GX 와 같다).
- 서버는 백그라운드 스레드의 이벤트 루프에서 돈다(`start()`·`stop()`). 동기 시험은
  `subscribe_once(url, 접속키, tr_id, tr_key)` 로 등록→해제 한 번을 주고받는다.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

BAD_KEY: Final = "invalid approval : NOT FOUND"
HEADER_KEYS: Final = frozenset({"approval_key", "custtype", "tr_type", "content-type"})


@dataclass(frozen=True)
class Received:
    """받은 등록·해제 요청 한 건."""

    conn: int
    tr_type: str
    tr_id: str
    tr_key: str
    approval_key: str
    accepted: bool


class FakeKisWsServer:
    def __init__(self, key_ok: Callable[[str], bool]) -> None:
        self.key_ok = key_ok
        self.received: list[Received] = []
        self.errors: list[str] = []
        self._conns = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Server | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    # ── 수명 ─────────────────────────────────────────────────────────────────────────
    def start(self) -> FakeKisWsServer:
        if self._thread is not None:
            return self
        loop = asyncio.new_event_loop()
        self._loop = loop

        def run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._start())
            self._ready.set()
            loop.run_forever()

        self._thread = threading.Thread(target=run, name="fake-kis-ws", daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("가짜 웹소켓 서버가 10초 안에 뜨지 않았다")
        return self

    async def _start(self) -> None:
        self._server = await serve(self._handler, "127.0.0.1", 0, ping_interval=None)

    def stop(self) -> None:
        loop, server = self._loop, self._server
        if loop is None or server is None:
            return

        async def close() -> None:
            server.close()
            await server.wait_closed()

        asyncio.run_coroutine_threadsafe(close(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(10)
        self._thread = None
        self._loop = None
        self._server = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("start() 전")
        port = next(iter(self._server.sockets)).getsockname()[1]
        return f"ws://127.0.0.1:{port}"

    # ── 처리 ─────────────────────────────────────────────────────────────────────────
    async def _handler(self, ws: ServerConnection) -> None:
        with self._lock:
            self._conns += 1
            conn = self._conns
        try:
            async for msg in ws:
                text = msg if isinstance(msg, str) else bytes(msg).decode("utf-8", "replace")
                await ws.send(json.dumps(self._on_message(conn, text), ensure_ascii=False))
        except ConnectionClosed:
            pass

    def _on_message(self, conn: int, text: str) -> dict[str, Any]:
        try:
            req: Any = json.loads(text)
            header: dict[str, Any] = dict(req["header"])
            inp: dict[str, Any] = dict(req["body"]["input"])
            tr_id, tr_key = str(inp["tr_id"]), str(inp["tr_key"])
        except (ValueError, KeyError, TypeError):
            with self._lock:
                self.errors.append("형식 오류")
            return {"header": {}, "body": {"rt_cd": "1", "msg1": "JSON PARSING ERROR"}}
        if set(header) != HEADER_KEYS or header.get("tr_type") not in ("1", "2"):
            with self._lock:
                self.errors.append(f"header 키 {sorted(header)}")
        key = str(header.get("approval_key", ""))
        ok = self.key_ok(key)
        tr_type = str(header.get("tr_type", ""))
        with self._lock:
            self.received.append(Received(conn, tr_type, tr_id, tr_key, key, ok))
        head = {"tr_id": tr_id, "tr_key": tr_key, "encrypt": "N"}
        if not ok:
            return {"header": head, "body": {"rt_cd": "1", "msg_cd": "OPSP0011", "msg1": BAD_KEY}}
        msg1 = "SUBSCRIBE SUCCESS" if tr_type == "1" else "UNSUBSCRIBE SUCCESS"
        return {"header": head, "body": {"rt_cd": "0", "msg_cd": "OPSP0000", "msg1": msg1}}


def ws_request(approval_key: str, tr_type: str, tr_id: str, tr_key: str) -> str:
    """KIS 공식 샘플 모양의 등록(1)·해제(2) 요청."""
    return json.dumps(
        {
            "header": {
                "approval_key": approval_key,
                "custtype": "P",
                "tr_type": tr_type,
                "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
        }
    )


def subscribe_once(
    url: str, approval_key: str, tr_id: str, tr_key: str, *, timeout: float = 10.0
) -> tuple[dict[str, Any], dict[str, Any]]:
    """등록 → 응답 → 해제 → 응답(동기). 두 응답을 돌려준다."""
    with connect(url, open_timeout=timeout, close_timeout=timeout) as ws:
        ws.send(ws_request(approval_key, "1", tr_id, tr_key))
        sub = json.loads(ws.recv(timeout))
        ws.send(ws_request(approval_key, "2", tr_id, tr_key))
        unsub = json.loads(ws.recv(timeout))
    return sub, unsub


__all__ = ["BAD_KEY", "FakeKisWsServer", "Received", "subscribe_once", "ws_request"]

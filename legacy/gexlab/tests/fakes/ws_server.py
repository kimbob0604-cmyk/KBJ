"""가짜 KIS 웹소켓 서버 — ws-gateway 세션 클라이언트 테스트용 (127.0.0.1 임의 포트).

실제 KIS 에 붙지 않는다. 주고받는 문구는 **합성(SYNTHETIC)** 이다.

- 등록·해제 요청을 `config/kis_ws.yaml` 형식(공식 `data_fetch`)으로 검사한다: header 키가 정확히
  approval_key·custtype·tr_type·content-type, body 가 `{"input": {"tr_id", "tr_key"}}`. 어긋나면
  `errors` 에 남기고 오류 응답을 보낸다
- 응답: 성공 `rt_cd "0"`, 해제 응답 msg1 은 `UNSUB…`(공식 `system_resp` 가 가르는 방식). 오류 문구
  (`MAX SUBSCRIBE OVER`·`ALREADY IN SUBSCRIBE`·`UNSUBSCRIBE ERROR(not found!)`·
  `invalid approval : NOT FOUND`)는 공식 샘플에 없는 가정이다 — 설정 responses 와 같은 확인 필요 값
- 연결마다 서버 쪽 구독 집합을 따로 두고 `limit`(기본 41)을 넘는 등록은 최대 구독 초과로 거절한다.
  `unsub_ack_delay` 를 주면 해제는 그만큼 뒤에 풀리고 응답한다(그 사이 들어온 등록은 풀리기 전
  셈으로 판정 — 해지 응답을 기다리지 않는 클라이언트를 잡는다)
- `reject`(거절), `silent`(응답 안 함), `refuse_next`(핸드셰이크 503), `drop()`(연결 끊기).
  `reject_gate` 를 주면 거절 응답은 그 이벤트가 설 때까지 붙잡아 둔다(한 버스트로 보낸 요청의 오류가
  모두 감속 뒤에 도착하는 경우)
- PINGPONG: `send_pingpong()` 이 보내고, 클라이언트의 PONG 제어 프레임 payload 는 `pongs`,
  텍스트로 되돌린 것은 `echoes` 에 쌓인다
- 데이터 프레임은 `config/kis_ws_fields.yaml` 컬럼 순서로 만든다(`synth_record`·`synth_frame`)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from data.kis.ws import load_fields
from services.ws_gateway.client import WsConfig, load_config

SubKey = tuple[str, str]  # (tr_id, tr_key)

MAX_OVER = "MAX SUBSCRIBE OVER"
ALREADY = "ALREADY IN SUBSCRIBE"
NOT_FOUND = "UNSUBSCRIBE ERROR(not found!)"
BAD_KEY = "invalid approval : NOT FOUND"
PINGPONG_TEXT = '{"header":{"tr_id":"PINGPONG","datetime":"20260928093000"}}'


def synth_record(tr_id: str, **values: str) -> list[str]:
    """SYNTHETIC: 컬럼 순서대로 값을 채운 레코드 한 건 (지정 안 한 칸은 '0')."""
    cols = load_fields()[tr_id].columns
    unknown = set(values) - set(cols)
    if unknown:
        raise KeyError(f"{tr_id} 에 없는 컬럼: {sorted(unknown)}")
    return [values.get(c, "0") for c in cols]


def synth_frame(tr_id: str, *records: list[str], flag: str = "0") -> str:
    """SYNTHETIC: `flag|TR|건수|v^v^...` — 레코드를 `^` 로 이어 붙인다."""
    payload = "^".join(v for r in records for v in r)
    return f"{flag}|{tr_id}|{len(records):03d}|{payload}"


@dataclass(frozen=True)
class Received:
    """받은 등록·해제 요청 한 건."""

    conn: int  # 1부터
    tr_type: str
    tr_id: str
    tr_key: str
    approval_key: str

    @property
    def sub(self) -> SubKey:
        return self.tr_id, self.tr_key


@dataclass
class FakeConn:
    index: int
    ws: ServerConnection
    subs: set[SubKey] = field(default_factory=set[SubKey])
    closed: bool = False


class FakeKisWsServer:
    def __init__(
        self,
        *,
        approval_key: str | None = None,
        limit: int = 41,
        unsub_ack_delay: float = 0.0,
        config: WsConfig | None = None,
    ) -> None:
        self.cfg = config if config is not None else load_config()
        self.expected_key = approval_key
        self.limit = limit
        self.unsub_ack_delay = unsub_ack_delay
        self.reject: dict[SubKey, str] = {}  # 등록을 거절할 구독 → msg1
        self.reject_gate: asyncio.Event | None = None  # 서면 붙잡아 둔 거절 응답을 보낸다
        self.silent: set[SubKey] = set()  # 응답하지 않을 구독
        self.refuse_next = 0  # 다음 핸드셰이크 n 번을 503 으로
        self.handshakes = 0  # 거절 포함 핸드셰이크 시도
        self.received: list[Received] = []
        self.errors: list[str] = []  # 형식 위반
        self.pongs: list[str] = []
        self.echoes: list[str] = []
        self.connections: list[FakeConn] = []
        self.max_subs = 0  # 한 연결에서 동시에 걸려 있던 구독 수의 최댓값
        self.over_limit = 0  # 최대 구독 초과로 거절한 횟수
        self._server: Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    # ---- 수명 ----------------------------------------------------------------------------

    async def __aenter__(self) -> FakeKisWsServer:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def start(self) -> None:
        self._server = await serve(
            self._handler,
            "127.0.0.1",
            0,
            process_request=self._process_request,
            create_connection=_recording_connection(self),
            ping_interval=None,
        )

    async def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("start() 전")
        port = next(iter(self._server.sockets)).getsockname()[1]
        return f"ws://127.0.0.1:{port}"

    # ---- 조회 ----------------------------------------------------------------------------

    def conn(self, i: int = -1) -> FakeConn:
        return self.connections[i]

    def requests(self, conn: int | None = None) -> list[Received]:
        return [r for r in self.received if conn is None or r.conn == conn]

    async def wait_for(self, pred: Callable[[], bool], timeout: float = 5.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not pred():
            if loop.time() > deadline:
                raise TimeoutError("가짜 서버: 조건이 시간 안에 참이 되지 않았다")
            await asyncio.sleep(0.005)

    # ---- 보내기 --------------------------------------------------------------------------

    async def send(self, raw: str, conn: int = -1) -> None:
        await self.connections[conn].ws.send(raw)

    async def send_tick(self, tr_id: str, *, conn: int = -1, **values: str) -> str:
        raw = synth_frame(tr_id, synth_record(tr_id, **values))
        await self.send(raw, conn)
        return raw

    async def send_pingpong(self, conn: int = -1) -> str:
        await self.send(PINGPONG_TEXT, conn)
        return PINGPONG_TEXT

    async def drop(self, conn: int = -1, *, abrupt: bool = False) -> None:
        c = self.connections[conn]
        if abrupt:
            c.ws.transport.abort()
        else:
            await c.ws.close(code=1011, reason="fake drop")

    # ---- 처리 ----------------------------------------------------------------------------

    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        self.handshakes += 1
        if self.refuse_next > 0:
            self.refuse_next -= 1
            return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "busy\n")
        return None

    async def _handler(self, ws: ServerConnection) -> None:
        c = FakeConn(len(self.connections) + 1, ws)
        self.connections.append(c)
        try:
            async for msg in ws:
                text = msg if isinstance(msg, str) else bytes(msg).decode("utf-8", "replace")
                await self._on_message(c, text)
        except ConnectionClosed:
            pass
        finally:
            c.closed = True

    def _check(self, obj: object) -> list[str]:
        if not isinstance(obj, dict):
            return ["객체가 아니다"]
        h, b = obj.get("header"), obj.get("body")
        if set(obj) != {"header", "body"} or not isinstance(h, dict) or not isinstance(b, dict):
            return [f"최상위 키: {sorted(obj)}"]
        errs: list[str] = []
        if set(h) != {"approval_key", "custtype", "tr_type", "content-type"}:
            errs.append(f"header 키: {sorted(h)}")
        if h.get("custtype") != self.cfg.header.custtype:
            errs.append(f"custtype: {h.get('custtype')!r}")
        if h.get("content-type") != self.cfg.header.content_type:
            errs.append(f"content-type: {h.get('content-type')!r}")
        if h.get("tr_type") not in (self.cfg.tr_type.subscribe, self.cfg.tr_type.unsubscribe):
            errs.append(f"tr_type: {h.get('tr_type')!r}")
        if not isinstance(h.get("approval_key"), str) or not h.get("approval_key"):
            errs.append("approval_key 없음")
        inp = b.get("input")
        if set(b) != {"input"} or not isinstance(inp, dict) or set(inp) != {"tr_id", "tr_key"}:
            errs.append(f"body: {b!r}")
        elif not all(isinstance(v, str) and v for v in inp.values()):
            errs.append(f"input 값: {inp!r}")
        return errs

    async def _on_message(self, c: FakeConn, text: str) -> None:
        try:
            obj: Any = json.loads(text)
        except ValueError:
            self.errors.append(f"JSON 아님: {text[:40]!r}")
            await self._reply(c, ("", ""), "9", "OPSP9999", "JSON PARSING ERROR")
            return
        header = obj.get("header") if isinstance(obj, dict) else None
        if isinstance(header, dict) and header.get("tr_id") == "PINGPONG":
            self.echoes.append(text)
            return
        errs = self._check(obj)
        if errs:
            self.errors.extend(errs)
            await self._reply(c, ("", ""), "9", "OPSP9999", "INVALID REQUEST")
            return
        h, inp = obj["header"], obj["body"]["input"]
        req = Received(c.index, h["tr_type"], inp["tr_id"], inp["tr_key"], h["approval_key"])
        self.received.append(req)
        sub = req.sub
        if self.expected_key is not None and req.approval_key != self.expected_key:
            await self._reply(c, sub, "1", "OPSP0011", BAD_KEY)
            return
        if sub in self.silent:
            return
        if req.tr_type == self.cfg.tr_type.subscribe:
            await self._subscribe(c, sub)
        elif self.unsub_ack_delay > 0:
            t = asyncio.create_task(self._unsubscribe_later(c, sub))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        else:
            await self._unsubscribe(c, sub)

    async def _subscribe(self, c: FakeConn, sub: SubKey) -> None:
        if sub in self.reject and self.reject_gate is not None:
            t = asyncio.create_task(self._reject_later(c, sub, self.reject[sub], self.reject_gate))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        elif sub in self.reject:
            await self._reply(c, sub, "1", "OPSP9990", self.reject[sub])
        elif sub in c.subs:
            await self._reply(c, sub, "1", "OPSP0002", ALREADY)
        elif len(c.subs) >= self.limit:
            self.over_limit += 1
            await self._reply(c, sub, "1", "OPSP0008", MAX_OVER)
        else:
            c.subs.add(sub)
            self.max_subs = max(self.max_subs, len(c.subs))
            # 체결 TR 응답에도 output(iv·key)이 온다고 본다 (공식 system_resp 가 읽는 자리)
            out = {"iv": "SYNTHETICIV00000", "key": "SYNTHETICKEY" + "0" * 20}
            await self._reply(c, sub, "0", "OPSP0000", "SUBSCRIBE SUCCESS", out)

    async def _reject_later(self, c: FakeConn, sub: SubKey, msg1: str, gate: asyncio.Event) -> None:
        await gate.wait()
        if not c.closed:
            await self._reply(c, sub, "1", "OPSP9990", msg1)

    async def _unsubscribe_later(self, c: FakeConn, sub: SubKey) -> None:
        await asyncio.sleep(self.unsub_ack_delay)
        if not c.closed:
            await self._unsubscribe(c, sub)

    async def _unsubscribe(self, c: FakeConn, sub: SubKey) -> None:
        if sub in c.subs:
            c.subs.discard(sub)
            await self._reply(c, sub, "0", "OPSP0001", "UNSUBSCRIBE SUCCESS")
        else:
            await self._reply(c, sub, "1", "OPSP0003", NOT_FOUND)

    async def _reply(
        self,
        c: FakeConn,
        sub: SubKey,
        rt_cd: str,
        msg_cd: str,
        msg1: str,
        output: dict[str, str] | None = None,
    ) -> None:
        body: dict[str, Any] = {"rt_cd": rt_cd, "msg_cd": msg_cd, "msg1": msg1}
        if output is not None:
            body["output"] = output
        msg = {"header": {"tr_id": sub[0], "tr_key": sub[1], "encrypt": "N"}, "body": body}
        try:
            await c.ws.send(json.dumps(msg))
        except ConnectionClosed:
            pass


def _recording_connection(server: FakeKisWsServer) -> type[ServerConnection]:
    """받은 PONG 제어 프레임의 payload 를 server.pongs 에 남기는 연결 클래스."""

    class _Conn(ServerConnection):
        def acknowledge_pings(self, data: bytes) -> None:
            server.pongs.append(data.decode("utf-8", "replace"))
            super().acknowledge_pings(data)

    return _Conn


__all__ = [
    "ALREADY",
    "BAD_KEY",
    "MAX_OVER",
    "NOT_FOUND",
    "PINGPONG_TEXT",
    "FakeConn",
    "FakeKisWsServer",
    "Received",
    "synth_frame",
    "synth_record",
]

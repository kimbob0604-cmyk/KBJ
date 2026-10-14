"""가짜 KIS 웹소켓 서버(127.0.0.1 임의 포트) — 접속키로 등록·해제만 받는다. 합성 문구만.

GEXLAB `tests/fakes/ws_server.py`(`FakeKisWsServer`)의 KBJ 판. GX 판은 GX 설정(config/kis_ws.yaml)·
필드 표에 묶여 있어 그대로 옮기지 않고, 하루 시뮬레이션이 보는 것만 같은 이름으로 다시 만들었다:
**접속키가 auth 가 발급한 값인지**와 등록·해제 순서. 체결 프레임·PINGPONG·구독 한도는 ws-gateway 를
승격하는 P7 에 GX 판을 옮겼다 — 아래 `AsyncKisWsServer`(메인 결정 D2).

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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from http import HTTPStatus
from typing import Any, Final

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response
from websockets.sync.client import connect

from kbj.data.private.kis.master import MasterRow, parse_master
from kbj.data.private.kis.ws import load_fields
from kbj.services.ws_gateway.client import WsConfig, load_config

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


# ═══ GX 판(P7 ING) — ws-gateway 세션 클라이언트·서비스 시험용 비동기 가짜 서버 ════════════════════
#
# GX `tests/fakes/ws_server.py`(`FakeKisWsServer`)를 이름만 바꿔 옮겼다(`AsyncKisWsServer` — 위의
# 동기 판은 하루 시뮬레이션이 같은 이름으로 쓴다). 승격한 시험은
# `AsyncKisWsServer as FakeKisWsServer` 로 import 한다(import 줄만 바뀜). 주고받는 문구는
# **합성(SYNTHETIC)** 이다.
#
# - 등록·해제 요청을 `config/kis_ws.yaml` 형식(공식 `data_fetch`)으로 검사한다: header 키가 정확히
#   approval_key·custtype·tr_type·content-type, body 가 `{"input": {"tr_id", "tr_key"}}`. 어긋나면
#   `errors` 에 남기고 오류 응답을 보낸다
# - 응답: 성공 `rt_cd "0"`, 해제 응답 msg1 은 `UNSUB…`. 오류 문구(`MAX SUBSCRIBE OVER`·
#   `ALREADY IN SUBSCRIBE`·`UNSUBSCRIBE ERROR(not found!)`·`invalid approval : NOT FOUND`)는 공식
#   샘플에 없는 가정이다 — 설정 responses 와 같은 확인 필요 값
# - 연결마다 서버 쪽 구독 집합을 따로 두고 `limit`(기본 41)을 넘는 등록은 최대 구독 초과로 거절한다.
#   `unsub_ack_delay` 를 주면 해제는 그만큼 뒤에 풀리고 응답한다
# - `reject`(거절), `silent`(응답 안 함), `refuse_next`(핸드셰이크 503), `drop()`(연결 끊기),
#   `reject_gate`(거절 응답을 이벤트가 설 때까지 붙잡기)
# - PINGPONG: `send_pingpong()` 이 보내고, PONG 제어 프레임 payload 는 `pongs`, 텍스트로 되돌린 것은
#   `echoes` 에 쌓인다
# - 데이터 프레임은 `config/kis_ws_fields.yaml` 컬럼 순서로 만든다(`synth_record`·`synth_frame`)
#
# 가짜 체인(`FakeChain`·`default_chain`)은 GX `tests/fakes/kis_server.py` 의 마스터 부분이다 — KBJ
# 가짜 KIS(`tests/fakes/kis_server.py`)는 마스터를 합성 줄 파일로 주어 GX 체인 모양이 없다.
# ws-gateway 서비스 시험이 마스터 행·옵션 코드를 만드는 데만 쓴다.

SubKey = tuple[str, str]  # (tr_id, tr_key)

MAX_OVER = "MAX SUBSCRIBE OVER"
ALREADY = "ALREADY IN SUBSCRIBE"
NOT_FOUND = "UNSUBSCRIBE ERROR(not found!)"
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
class GxReceived:
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


class AsyncKisWsServer:
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
        self.received: list[GxReceived] = []
        self.errors: list[str] = []  # 형식 위반
        self.pongs: list[str] = []
        self.echoes: list[str] = []
        self.connections: list[FakeConn] = []
        self.max_subs = 0  # 한 연결에서 동시에 걸려 있던 구독 수의 최댓값
        self.over_limit = 0  # 최대 구독 초과로 거절한 횟수
        self._server: Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    # ---- 수명 ----------------------------------------------------------------------------

    async def __aenter__(self) -> AsyncKisWsServer:
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

    def requests(self, conn: int | None = None) -> list[GxReceived]:
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
        req = GxReceived(c.index, h["tr_type"], inp["tr_id"], inp["tr_key"], h["approval_key"])
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


def _recording_connection(server: AsyncKisWsServer) -> type[ServerConnection]:
    """받은 PONG 제어 프레임의 payload 를 server.pongs 에 남기는 연결 클래스."""

    class _Conn(ServerConnection):
        def acknowledge_pings(self, data: bytes) -> None:
            server.pongs.append(data.decode("utf-8", "replace"))
            super().acknowledge_pings(data)

    return _Conn


# ── 가짜 체인 (마스터·전광판·단건이 같은 행사가·코드를 쓴다) ─────────────────

_CLS_TAG = {"": "0", "WKM": "M", "WKI": "I"}
_KIND = {
    ("", "C"): "5",
    ("", "P"): "6",
    ("WKI", "C"): "L",
    ("WKI", "P"): "M",
    ("WKM", "C"): "N",
    ("WKM", "P"): "O",
}
# 마스터 종목명 표기 (data/kis/master.py)
_NAME_PREFIX = {"": "", "WKI": "위클리", "WKM": "위클리M "}


@dataclass(frozen=True)
class FakeSeries:
    """옵션 한 시리즈. cls 는 월물리스트·전광판 시장분류('' 월물, WKM, WKI), mtrt 는 6자리."""

    cls: str
    mtrt: str
    last_tr_date: date
    lo: Decimal
    hi: Decimal
    step: Decimal = Decimal("2.5")

    @property
    def strikes(self) -> tuple[Decimal, ...]:
        n = int((self.hi - self.lo) / self.step)
        return tuple(self.lo + self.step * i for i in range(n + 1))

    @property
    def token(self) -> str:
        """마스터 종목명의 만기 표기 (월물 `202610`, 위클리 `2609W4`)."""
        return self.mtrt if self.cls == "" else f"{self.mtrt[:4]}W{int(self.mtrt[4:])}"

    def name(self, cp: str, strike: Decimal) -> str:
        return f"{_NAME_PREFIX[self.cls]}{cp} {self.token} {strike:,.1f}"

    def code(self, cp: str, strike: Decimal) -> str:
        """가짜 단축코드 — 시리즈·콜풋·행사가마다 다르다."""
        i = self.strikes.index(strike)
        return f"{'B' if cp == 'C' else 'C'}{_CLS_TAG[self.cls]}{self.mtrt}{i:03d}"

    def master_line(self, cp: str, strike: Decimal) -> str:
        code = self.code(cp, strike)
        kind = _KIND[(self.cls, cp)]
        return f"{kind}|{code}|KR4{code}|{self.name(cp, strike)}|3|{strike:08.2f}| |2001|KOSPI200"


@dataclass(frozen=True)
class FakeFutures:
    code: str
    name: str
    last_tr_date: date
    remaining_days: int


DEFAULT_FUTURES = (
    FakeFutures("A01612", "F 202612", date(2026, 12, 10), 74),  # fixture futures_board 첫 행
    FakeFutures("A01703", "F 202703", date(2027, 3, 11), 165),
)


@dataclass
class FakeChain:
    series: tuple[FakeSeries, ...]
    board_rows: int = 100  # 전광판 콜·풋 각 행 수 상한 (#11)
    # 전광판 atm_cls_name 이 'ATM' 인 행 — 선물가 기준 ATM 과 다르다(#11)
    display_atm: Decimal = Decimal("1125.0")
    futures: tuple[FakeFutures, ...] = DEFAULT_FUTURES
    # 월물리스트 시장분류('' WKM WKI) → 6자리 만기 목록. None 이면 fixture(2026-09-28 13:33)
    listed: Mapping[str, tuple[str, ...]] | None = None
    _by_code: dict[str, tuple[FakeSeries, str, Decimal]] = field(
        default_factory=dict[str, tuple[FakeSeries, str, Decimal]], repr=False
    )

    def __post_init__(self) -> None:
        for s in self.series:
            for k in s.strikes:
                for cp in ("C", "P"):
                    self._by_code[s.code(cp, k)] = (s, cp, k)

    def find(self, cls: str, mtrt: str) -> FakeSeries | None:
        return next((s for s in self.series if (s.cls, s.mtrt) == (cls, mtrt)), None)

    def lookup(self, code: str) -> tuple[FakeSeries, str, Decimal] | None:
        return self._by_code.get(code)

    def master_lines(self) -> list[str]:
        out = [
            f"1|{f.code}|KR4{f.code}|{f.name}| |00000.00|{i}|2001|KOSPI200"
            for i, f in enumerate(self.futures, start=1)
        ]
        for s in self.series:
            out += [s.master_line(cp, k) for k in s.strikes for cp in ("C", "P")]
        return out

    def master_rows(self) -> list[MasterRow]:
        return parse_master("\n".join(self.master_lines()))


def default_chain() -> FakeChain:
    """2026-09-28 상장 구성 (#11a·#12b·#19 2회차: 최종거래일 09-28·10-01·10-06·10-08).

    행사가 범위는 실측(2609W4 970.0~1245.0 111개, 202610 745.0~1595.0 341개)을 따르고, 나머지
    위클리는 같은 범위로 둔다. 202611 은 계산값(11월 둘째 목요일).
    """
    wk_lo, wk_hi = Decimal("970.0"), Decimal("1245.0")
    m_lo, m_hi = Decimal("745.0"), Decimal("1595.0")
    return FakeChain(
        series=(
            FakeSeries("WKM", "260904", date(2026, 9, 28), wk_lo, wk_hi),
            FakeSeries("WKI", "261001", date(2026, 10, 1), wk_lo, wk_hi),
            FakeSeries("WKM", "261001", date(2026, 10, 6), wk_lo, wk_hi),
            FakeSeries("", "202610", date(2026, 10, 8), m_lo, m_hi),
            FakeSeries("", "202611", date(2026, 11, 12), m_lo, m_hi),
        )
    )


__all__ = [
    "ALREADY",
    "BAD_KEY",
    "DEFAULT_FUTURES",
    "MAX_OVER",
    "NOT_FOUND",
    "PINGPONG_TEXT",
    "AsyncKisWsServer",
    "FakeChain",
    "FakeConn",
    "FakeFutures",
    "FakeKisWsServer",
    "FakeSeries",
    "GxReceived",
    "Received",
    "default_chain",
    "subscribe_once",
    "synth_frame",
    "synth_record",
    "ws_request",
]

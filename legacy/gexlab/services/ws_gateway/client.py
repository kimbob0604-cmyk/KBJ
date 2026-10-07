"""KIS 웹소켓 세션 클라이언트 (PLAN §4.1 ws-gateway·§4.3·§6.5, docs/phase1_design.md §2·§4).

프로토콜은 `config/kis_ws.yaml`(KIS 공식 샘플 커밋·파일 기록)에서 읽는다.

- 등록·해제: `{"header": {approval_key, custtype "P", tr_type "1"|"2", content-type "utf-8"},
  "body": {"input": {tr_id, tr_key}}}` — 공식 `data_fetch`
- 응답: 제어 JSON 의 `body.rt_cd == "0"` 이 성공, `msg1` 이 `UNSUB` 로 시작하면 해제 응답(공식
  `system_resp`). 오류 문구(최대 구독 초과 등)는 공식 샘플에 없어 설정의 문구로 가른다 — 확인 필요
- PINGPONG: 공식대로 받은 원문을 PONG 제어 프레임에 실어 되돌린다(`ws.pong(raw)`)

세션 클라이언트(`KisWsClient`):

- 접속키는 Redis `kis:ws_key` 를 auth 의 읽기 전용 제공자(`services.auth.service.reader`)로 읽는다.
  없으면 health(`ws_key_missing`)를 남기고 기다린다 — 여기서는 발급하지 않는다(PLAN §2.5)
- 원하는 구독 집합은 밖(컨트롤러, `services/ws_gateway/session.py`)이 `set_desired` 로 준다.
  바뀌면 `subscriptions.changes()` 순서(해지 먼저)로 한 건씩 `pacing_s` 간격으로 보낸다
- 감속(설계 §4 '오류 응답이면 감속하고 health 기록'): 등록·해제 오류 응답이면 간격을 `pacing_factor`
  배로(상한 `pacing_max_s`) 늘리고 health `ws_pacing_slowdown`. 감속 전에 보낸 요청의 오류는 같은
  버스트로 보고 더 늘리지 않는다. 성공 응답 `pacing_recover_after` 건이 이어지면 한 단계씩
  `pacing_s` 까지 되돌린다(`ws_pacing_recovered`). 이미 등록됨·등록 안 됨(상태 차이)과 접속키 거절
  (끊고 다시 붙는다)은 속도 탓이 아니라 세지 않는다. 재연결해도 늘린 간격은 둔다. 속도 한도·오류
  문구는 미실측 — 확인 필요
- 예산: 서버에 걸려 있다고 보는 구독(등록 확인 + 등록 응답 대기 + 해제 응답 대기)이
  `Budget.futures + Budget.options` 를 넘지 않는다. 등록 전에 자리가 없으면 해제 응답을 기다린다.
  응답 없는 요청은 등록·해제 모두 서버에 걸려 있다고 보고(해제를 풀린 것으로 보지 않는다), 그래도
  자리가 없으면 health 를 남기고 끊는다 — 새 세션은 서버 쪽 구독이 없어 원하는 집합만 다시 건다.
  보낸 뒤 셈이 넘으면 `BudgetExceeded` — 버그다
- 끊기면 `Backoff`(1초 → 60초)로 다시 붙고, 붙으면 서버 쪽 구독은 없다고 보고 원하는 집합 전부를
  다시 등록한다. 연결이 `stable_s` 이상 살았을 때만 백오프를 처음부터 센다(등록 성공 응답만으로는
  세지 않는다 — 받아 주고 곧 끊는 서버에 1초마다 다시 붙지 않게).
  접속키 거절 응답이면 스스로 끊는다 — 재연결 때 Redis 에서 접속키를 다시 읽는다(발급은 auth)
- 받은 프레임은 파싱 전에 전부 `RawEnvelope`(거래일·세션 태그)로 raw 콜백에 넘기고(녹화는 파서와
  분리), 체결 틱은 `TickEvent`(수신 순번 seq, 태그)로 tick 콜백에 넘긴다. seq 는 한 연결 안에서
  이어지고 재연결마다 하나를 건너뛴다 — 끊긴 동안의 체결이 몰린 재연결 뒤 첫 틱을 받는 쪽(engine
  HIRO-lite)이 순번 공백으로 본다. 암호화 프레임(체결통보)은 녹화만 한다(복호화는 Phase 8)
- 콜백·파서·태거의 예외는 프레임 단위로 격리한다 — 세션을 끊지 않는다
- health 는 `services.auth.health.HealthEvent(service="ws-gateway")`. 접속키는 어디에도 싣지 않는다
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from redis import Redis
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from config.settings import Settings
from core.calendar import TradingCalendar
from data.kis.auth_client import TokenUnavailable, utcnow
from data.kis.ws import (
    Tick,
    TrSpec,
    WsControl,
    WsData,
    WsEncrypted,
    WsFrame,
    load_fields,
    parse_frame,
    ticks_from,
)
from services.auth.health import HealthEvent, HealthSink, Severity
from services.auth.service import reader, redact
from services.poller.context import session_tag
from services.recorder.envelope import RawEnvelope, SessionName, Tagger, wrap
from services.ws_gateway.backoff import Backoff
from services.ws_gateway.budget import Budget
from services.ws_gateway.subscriptions import Action, Subscription, changes

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "kis_ws.yaml"
SERVICE = "ws-gateway"
PONG_MAX_BYTES = 125  # RFC 6455 §5.5 제어 프레임 payload 한도

Env = Literal["real", "vts"]
AckKind = Literal[
    "ok",
    "key_rejected",
    "key_in_use",
    "max_subscriptions",
    "already_subscribed",
    "not_subscribed",
    "rejected",
]
# 속도 탓이 아닌 오류 응답 — 감속하지 않는다: 이미 등록됨·등록 안 됨은 서버와 셈이 달랐을 뿐이고
# (등록된·풀린 것으로 본다), 접속키 거절은 끊고 다시 붙는다
PACING_NEUTRAL: frozenset[AckKind] = frozenset(
    {"already_subscribed", "not_subscribed", "key_rejected"}
)
KeySource = Callable[[], str]  # 접속키 원문. 없으면 TokenUnavailable
Sleep = Callable[[float], Awaitable[None]]
Tag = tuple[date | None, SessionName | None]

log = logging.getLogger(__name__)


# ── 설정 ─────────────────────────────────────────────────────────────────────


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WsSourceInfo(_Frozen):
    repo: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    fetched: date
    files: tuple[str, ...]


class WsUrls(_Frozen):
    real: str = Field(pattern=r"^wss?://")
    vts: str = Field(pattern=r"^wss?://")


class WsHeader(_Frozen):
    custtype: str
    content_type: str


class WsTrType(_Frozen):
    subscribe: str
    unsubscribe: str


class WsPingPong(_Frozen):
    tr_id: str
    reply: Literal["pong", "echo"]


class WsResponses(_Frozen):
    ok_rt_cd: str
    unsub_prefix: str
    key_rejected: str
    key_in_use: str
    max_subscriptions: str
    already_subscribed: str
    not_subscribed: str


class WsClientParams(_Frozen):
    pacing_s: float = Field(gt=0)  # 속도 한도 미실측 — 간격 없음(0)은 두지 않는다
    pacing_max_s: float = Field(gt=0)
    pacing_factor: float = Field(gt=1)
    pacing_recover_after: int = Field(ge=1)
    ack_timeout_s: float = Field(gt=0)
    stable_s: float = Field(gt=0)
    key_wait_s: float = Field(gt=0)
    late_grace_s: float = Field(ge=0)
    ping_interval_s: float | None = Field(default=None, gt=0)
    open_timeout_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _pacing_range(self) -> WsClientParams:
        if self.pacing_max_s < self.pacing_s:
            raise ValueError(f"pacing_max_s {self.pacing_max_s} < pacing_s {self.pacing_s}")
        return self


class WsBackoffParams(_Frozen):
    initial: float = Field(gt=0)
    factor: float = Field(ge=1)
    maximum: float = Field(gt=0)
    jitter: float = Field(ge=0, lt=1)


class WsConfig(_Frozen):
    source: WsSourceInfo
    url: WsUrls
    header: WsHeader
    tr_type: WsTrType
    pingpong: WsPingPong
    responses: WsResponses
    client: WsClientParams
    backoff: WsBackoffParams

    def url_for(self, env: Env) -> str:
        return self.url.real if env == "real" else self.url.vts

    def make_backoff(self) -> Backoff:
        b = self.backoff
        return Backoff(initial=b.initial, factor=b.factor, maximum=b.maximum, jitter=b.jitter)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> WsConfig:
    return WsConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# ── 메시지 ───────────────────────────────────────────────────────────────────


def build_message(cfg: WsConfig, approval_key: str, action: Action, sub: Subscription) -> str:
    """등록·해제 요청 한 건 (공식 `data_fetch` 모양). 접속키가 들어 있어 로그에 싣지 않는다."""
    if not approval_key.strip():
        raise ValueError("접속키가 비었다")
    tr_type = cfg.tr_type.subscribe if action == "subscribe" else cfg.tr_type.unsubscribe
    msg = {
        "header": {
            "approval_key": approval_key,
            "custtype": cfg.header.custtype,
            "tr_type": tr_type,
            "content-type": cfg.header.content_type,
        },
        "body": {"input": {"tr_id": sub.tr_id, "tr_key": sub.key}},
    }
    return json.dumps(msg, ensure_ascii=False)


def is_unsub_reply(cfg: WsConfig, msg1: str | None) -> bool:
    """공식 `system_resp`: `msg1[:5] == "UNSUB"` 이면 해제 응답."""
    return (msg1 or "").strip().upper().startswith(cfg.responses.unsub_prefix.upper())


def classify_reply(cfg: WsConfig, rt_cd: str | None, msg1: str | None) -> AckKind:
    """등록·해제 응답 분류. 성공은 공식 기준(rt_cd), 오류 종류는 설정 문구(확인 필요)로 가른다."""
    if rt_cd == cfg.responses.ok_rt_cd:
        return "ok"
    text = (msg1 or "").upper()
    r = cfg.responses
    order: tuple[tuple[AckKind, str], ...] = (
        ("key_rejected", r.key_rejected),
        ("key_in_use", r.key_in_use),
        ("max_subscriptions", r.max_subscriptions),
        ("already_subscribed", r.already_subscribed),
        ("not_subscribed", r.not_subscribed),
    )
    for kind, token in order:
        if token and token.upper() in text:
            return kind
    return "rejected"


# ── 접속키·태그 ───────────────────────────────────────────────────────────────


def redis_key_source(redis: Redis, settings: Settings) -> KeySource:
    """Redis `kis:ws_key` 를 auth 읽기 전용 제공자로 읽는다. 부를 때마다 캐시를 새로 본다
    (auth 가 갈아 끼운 접속키를 재연결 때 바로 쓰게). 발급하지 않는다."""

    def get() -> str:
        return reader(redis, settings, "ws_key").get()

    return get


def default_tagger(cal: TradingCalendar | None = None) -> Tagger:
    """수신 시각 → (거래일, 세션). poller 와 같은 규칙(`services.poller.context.session_tag`):
    DAY·NIGHT 는 그 세션, 장 전 준비(PRE_DAY·PRE_NIGHT)는 곧 열릴 세션, 나머지는 (None, None)."""
    c = cal if cal is not None else TradingCalendar.default()

    def tag(t: datetime) -> tuple[date | None, SessionName | None]:
        got = session_tag(t, c)
        return (None, None) if got is None else got

    return tag


# ── 세션 클라이언트 ───────────────────────────────────────────────────────────


class BudgetExceeded(RuntimeError):
    """보낸 뒤 서버 쪽 구독 셈이 예산을 넘었다 — 계산 버그. 연결을 끊고 다시 붙는다."""


@dataclass(frozen=True)
class TickEvent:
    """체결 틱 + 수신 순번(`fut_ticks`·`opt_ticks` 의 seq) + 수신 시각의 거래일·세션."""

    tick: Tick
    seq: int
    received_at: datetime
    trade_date: date | None
    session: SessionName | None


@dataclass(frozen=True)
class _Pending:
    action: Action
    sent_at: float  # 이벤트 루프 시각


@dataclass(frozen=True)
class ClientStatus:
    connected: bool
    connections: int  # 붙은 횟수 (재연결 포함)
    desired: frozenset[Subscription]
    active: frozenset[Subscription]  # 서버에 걸려 있다고 보는 구독 (해제 응답 대기 포함)
    pending: int
    rejected: Mapping[Subscription, AckKind]
    backoff_attempt: int
    max_occupancy: int  # 지금까지 서버 쪽 구독 셈의 최댓값
    pacing_s: float  # 지금 등록·해제 간격 (오류 응답으로 늘었으면 pacing_s 보다 크다)


class KisWsClient:
    """KIS 웹소켓 단일 세션 (ws-gateway 만 소유, PLAN §2.5). `run(stop)` 이 stop 까지 돈다."""

    def __init__(
        self,
        url: str,
        key_source: KeySource,
        *,
        on_raw: Callable[[RawEnvelope], None],
        on_tick: Callable[[TickEvent], None],
        tagger: Tagger,
        health: HealthSink,
        config: WsConfig | None = None,
        specs: Mapping[str, TrSpec] | None = None,
        backoff: Backoff | None = None,
        now: Callable[[], datetime] = utcnow,
        sleep: Sleep = asyncio.sleep,
        on_connected: Callable[[int], None] | None = None,
        on_parse_error: Callable[[RawEnvelope, str], None] | None = None,
        proxy: str | Literal[True] | None = True,
    ) -> None:
        self._cfg = config if config is not None else load_config()
        self._url = url
        self._key_source = key_source
        self._on_raw = on_raw
        self._on_tick = on_tick
        self._tagger = tagger
        self._health = health
        self._specs = specs if specs is not None else load_fields()
        self._backoff = backoff if backoff is not None else self._cfg.make_backoff()
        self._now = now
        self._sleep = sleep
        self._on_connected = on_connected
        self._on_parse_error = on_parse_error
        self._proxy: str | Literal[True] | None = proxy

        self._desired: frozenset[Subscription] = frozenset()
        self._budget: Budget | None = None
        self._active: set[Subscription] = set()
        self._pending: dict[Subscription, _Pending] = {}
        self._rejected: dict[Subscription, AckKind] = {}
        self._wake: asyncio.Event | None = None
        self._cond: asyncio.Condition | None = None
        self._key: str | None = None
        self._seq = 0
        self._connections = 0
        self._connected = False
        self._last_emit: dict[str, datetime] = {}
        self.max_occupancy = 0
        self._pacing = self._cfg.client.pacing_s
        self._pace_ok = 0  # 마지막 오류·간격 변경 뒤 이어진 성공 응답 수
        self._slowed_at: float | None = None  # 마지막 감속 시각(이벤트 루프 시계)

    # ---- 밖에서 부르는 것 ----------------------------------------------------------------

    def set_desired(self, desired: frozenset[Subscription], budget: Budget) -> None:
        """원하는 구독 집합과 그 세션 예산. 예산(선물 + 옵션 자리)을 넘으면 ValueError."""
        cap = _cap(budget)
        if len(desired) > cap:
            raise ValueError(f"구독 {len(desired)}건이 예산 {cap}건을 넘는다 ({budget.session})")
        if desired == self._desired and budget == self._budget:
            return
        self._desired = frozenset(desired)
        self._budget = budget
        self.resync()

    def resync(self) -> None:
        """다음 기회에 원하는 집합과 서버 쪽을 다시 맞춘다(거절됐던 구독 재시도 포함)."""
        if self._wake is not None:
            self._wake.set()

    def _sync_prims(self) -> tuple[asyncio.Event, asyncio.Condition]:
        if self._wake is None or self._cond is None:
            raise RuntimeError("run() 안에서만 쓴다")
        return self._wake, self._cond

    def status(self) -> ClientStatus:
        return ClientStatus(
            connected=self._connected,
            connections=self._connections,
            desired=self._desired,
            active=frozenset(self._active),
            pending=len(self._pending),
            rejected=dict(self._rejected),
            backoff_attempt=self._backoff.attempt,
            max_occupancy=self.max_occupancy,
            pacing_s=self._pacing,
        )

    async def run(self, stop: asyncio.Event) -> None:
        self._wake = asyncio.Event()
        self._cond = asyncio.Condition()
        while not stop.is_set():
            key = await self._read_key()
            if key is None:
                await self._pause(self._cfg.client.key_wait_s, stop)
                continue
            healthy = await self._connect_once(key, stop)
            if stop.is_set():
                break
            if healthy:
                self._backoff.reset()
            delay = self._backoff.next_delay()
            self._emit(
                "ws_backoff",
                f"{delay:.1f}초 뒤 재연결 (연속 {self._backoff.attempt}번째 대기)",
                "info" if self._backoff.attempt == 1 else "warning",
            )
            await self._pause(delay, stop)

    # ---- 연결 ----------------------------------------------------------------------------

    async def _read_key(self) -> str | None:
        try:
            key = await asyncio.to_thread(self._key_source)
        except TokenUnavailable:
            key = ""
        except Exception as e:
            self._emit(
                "ws_key_error",
                f"접속키를 읽지 못했다: {type(e).__name__}"
                f" — {self._cfg.client.key_wait_s:g}초 뒤 다시",
                "warning",
                every=60,
            )
            return None
        if not key.strip():
            self._emit(
                "ws_key_missing",
                "Redis kis:ws_key 에 쓸 접속키가 없다 — auth 가 발급할 때까지 기다린다"
                "(ws-gateway 는 발급하지 않는다)",
                "critical",
                every=60,
            )
            return None
        self._key = key
        return key

    async def _connect_once(self, key: str, stop: asyncio.Event) -> bool:
        """한 번 붙어 끊길 때까지. `stable_s` 이상 살았으면(백오프를 처음부터 셀 만큼 건강) True."""
        loop = asyncio.get_running_loop()
        c = self._cfg.client
        started = loop.time()
        opened = False
        try:
            async with connect(
                self._url,
                open_timeout=c.open_timeout_s,
                ping_interval=c.ping_interval_s,
                proxy=self._proxy,
            ) as ws:
                opened = True
                started = loop.time()
                self._connections += 1
                if self._connections > 1:
                    # 재연결 — 수신 순번 하나를 건너뛴다. 끊긴 동안의 체결은 재연결 뒤 첫 틱의 누적
                    # 매수·매도에 몰리므로, 받는 쪽(engine HIRO-lite)이 그 틱을 순번 공백으로 보고
                    # 반영 전에 리셋하게(metrics §6.1)
                    self._seq += 1
                self._connected = True
                self._reset_server_view()
                again = " (재연결)" if self._connections > 1 else ""
                self._emit("ws_connected", f"{self._connections}번째 연결{again}", "info")
                if self._on_connected is not None:
                    self._call("on_connected", self._on_connected, self._connections)
                await self._serve(ws, key, stop)
                if not stop.is_set():
                    self._emit_disconnected(ws, loop.time() - started)
        except (OSError, TimeoutError, WebSocketException) as e:
            if opened:
                self._emit(
                    "ws_disconnected",
                    f"연결 {loop.time() - started:.0f}초 만에 끊김: {type(e).__name__}",
                    "warning",
                )
            else:
                self._emit("ws_connect_failed", f"{type(e).__name__}: {e}"[:200], "warning")
        finally:
            self._connected = False
            self._reset_server_view()
        return opened and loop.time() - started >= c.stable_s

    def _emit_disconnected(self, ws: ClientConnection, up: float) -> None:
        self._emit(
            "ws_disconnected",
            f"연결 {up:.0f}초 만에 끊김 (code={ws.close_code}, reason={ws.close_reason!r})",
            "warning",
        )

    async def _serve(self, ws: ClientConnection, key: str, stop: asyncio.Event) -> None:
        self._sync_prims()[0].set()  # 새 연결 — 서버 쪽 구독은 없다. 원하는 집합 전부 등록
        recv = asyncio.create_task(self._receive(ws))
        sync = asyncio.create_task(self._sync_loop(ws, key))
        halt = asyncio.create_task(stop.wait())
        tasks = (recv, sync, halt)
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for t in (sync, recv):
            if t in done and not t.cancelled():
                exc = t.exception()
                if isinstance(exc, BudgetExceeded):
                    self._emit("ws_budget_violation", str(exc), "critical")
                elif exc is not None and not isinstance(exc, ConnectionClosed):
                    self._emit("ws_internal_error", f"{type(exc).__name__}", "critical")
        await ws.close()

    async def _pause(self, delay: float, stop: asyncio.Event) -> None:
        """delay 초 쉬되 stop 이 오면 바로 깬다."""
        if delay <= 0 or stop.is_set():
            return
        sleeper = asyncio.ensure_future(self._sleep(delay))
        halt = asyncio.ensure_future(stop.wait())
        try:
            await asyncio.wait((sleeper, halt), return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            halt.cancel()
            await asyncio.gather(sleeper, halt, return_exceptions=True)

    # ---- 구독 맞추기 -----------------------------------------------------------------------

    def _reset_server_view(self) -> None:
        self._active.clear()
        self._pending.clear()
        self._rejected.clear()

    def _occupancy(self) -> int:
        """서버에 걸려 있다고 보는 구독 수: 확인된 것(해제 응답 대기 포함) + 등록 응답 대기."""
        return len(self._active) + sum(
            1 for s, p in self._pending.items() if p.action == "subscribe" and s not in self._active
        )

    async def _sync_loop(self, ws: ClientConnection, key: str) -> None:
        wake = self._sync_prims()[0]
        while True:
            await wake.wait()
            wake.clear()
            await self._apply(ws, key)

    async def _apply(self, ws: ClientConnection, key: str) -> None:
        budget = self._budget
        if budget is None:
            return
        cap = _cap(budget)
        desired = self._desired
        loop = asyncio.get_running_loop()
        for action, sub in changes(frozenset(self._active), desired):
            if action == "subscribe":
                if not await self._room(cap):
                    # 해제가 확인되지 않은 자리를 비었다고 보지 않는다(보면 서버가 41건을 넘는다).
                    # 새 세션은 서버 쪽 구독이 없으니 끊고 다시 붙어 원하는 집합만 건다
                    self._emit(
                        "ws_budget_full",
                        f"서버 쪽 구독 {self._occupancy()}건이 예산 {cap}건에 차 등록할 자리가"
                        " 없다 — 해제가 확인되지 않았다. 끊고 다시 붙는다",
                        "critical",
                        every=60,
                    )
                    await ws.close(code=1000, reason="subscription slots not released")
                    return
                if self._occupancy() + 1 > cap:
                    raise BudgetExceeded(f"등록하면 {self._occupancy() + 1}건 > 예산 {cap}건")
            self._pending[sub] = _Pending(action, loop.time())
            await ws.send(build_message(self._cfg, key, action, sub))
            if action == "subscribe":
                self.max_occupancy = max(self.max_occupancy, self._occupancy())
            await self._sleep(self._pacing)  # 오류 응답이 오면 다음 간격부터 늘어난다
        await self._settle()

    async def _wait_until(self, done: Callable[[], bool]) -> bool:
        """응답이 올 때마다 done 을 다시 본다. ack_timeout_s 안에 참이 되면 True."""
        cond = self._sync_prims()[1]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._cfg.client.ack_timeout_s
        async with cond:
            while not done():
                left = deadline - loop.time()
                if left <= 0:
                    return False
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(cond.wait(), left)
        return True

    async def _room(self, cap: int) -> bool:
        """등록 한 건 넣을 자리. 해제 응답을 기다린다. 끝내 안 오면 응답 없는 요청을 정리하고
        False — 정리해도 셈은 줄지 않는다(해제는 풀리지 않은 것으로 본다)."""
        if await self._wait_until(lambda: self._occupancy() < cap):
            return True
        self._expire_pending()
        return False

    async def _settle(self) -> None:
        if not await self._wait_until(lambda: not self._pending):
            self._expire_pending()

    def _expire_pending(self) -> None:
        """응답 없는 요청은 둘 다 서버에 걸려 있다고 본다(자리 차지 — 예산 보수적): 등록은 된
        것으로, 해제는 풀리지 않은 것으로. 풀리지 않은 해제는 다음 맞추기에서 다시 보낸다."""
        if not self._pending:
            return
        subs = [s for s, p in self._pending.items() if p.action == "subscribe"]
        unsubs = [s for s, p in self._pending.items() if p.action == "unsubscribe"]
        self._active.update(subs)
        self._active.update(unsubs)  # 해제를 보내기 전부터 _active 에 있다 — 그대로 둔다
        self._pending.clear()
        self._emit(
            "ws_ack_timeout",
            f"{self._cfg.client.ack_timeout_s:g}초 안에 응답 없음"
            f" — 등록 {len(subs)}건은 된 것으로, 해제 {len(unsubs)}건은 풀리지 않은 것으로 본다",
            "warning",
            every=60,
        )

    def _on_reply(self, c: WsControl) -> AckKind:
        """등록·해제 응답을 서버 쪽 구독 셈에 반영한다. 분류를 돌려준다."""
        sub = Subscription(c.tr_id, c.tr_key or "")
        kind = classify_reply(self._cfg, c.rt_cd, c.msg1)
        pend = self._pending.pop(sub, None)
        unsub = is_unsub_reply(self._cfg, c.msg1)
        if pend is None and sub not in self._desired and sub not in self._active:
            # 보낸 적 없는 구독에 대한 응답(형식 오류 응답 등) — 상태는 두고 알리기만
            if kind != "ok":
                self._reply_health("해제" if unsub else "요청", sub, kind, c)
            return kind
        self._adjust_pacing(kind, pend.sent_at if pend is not None else None)
        # 응답 기다림이 끝난 뒤 늦게 온 응답은 문구로 가른다
        action: Action = (
            pend.action if pend is not None else ("unsubscribe" if unsub else "subscribe")
        )
        if action == "subscribe":
            if kind in ("ok", "already_subscribed"):
                self._active.add(sub)
                self._rejected.pop(sub, None)
            else:
                self._active.discard(sub)
                self._rejected[sub] = kind
                self._reply_health("등록", sub, kind, c)
        elif kind in ("ok", "not_subscribed"):
            self._active.discard(sub)
        else:  # 해제 실패 — 서버에 남아 있다고 본다(자리 차지)
            self._active.add(sub)
            self._reply_health("해제", sub, kind, c)
        return kind

    def _adjust_pacing(self, kind: AckKind, sent_at: float | None) -> None:
        """등록·해제 간격 (설계 §4: 오류 응답이면 감속하고 health 기록).

        오류 응답이면 간격 × pacing_factor(상한 pacing_max_s). 마지막 감속 전에 보낸 요청의 오류는
        (늦게 온 응답은 보낸 시각을 모르니 이것으로 친다) 같은 버스트라 더 늘리지 않는다. 성공이
        pacing_recover_after 건 이어지면 ÷ pacing_factor 로 한 단계씩 pacing_s 까지 되돌린다.
        """
        c = self._cfg.client
        if kind in PACING_NEUTRAL:
            return
        if kind == "ok":
            self._pace_ok += 1
            if self._pacing > c.pacing_s and self._pace_ok >= c.pacing_recover_after:
                old, self._pacing = self._pacing, max(c.pacing_s, self._pacing / c.pacing_factor)
                self._pace_ok = 0
                self._emit(
                    "ws_pacing_recovered",
                    f"등록·해제 성공 {c.pacing_recover_after}건 연속 — 간격 {old:g} → "
                    f"{self._pacing:g}초 (기본 {c.pacing_s:g}초)",
                    "info",
                )
            return
        self._pace_ok = 0
        if self._slowed_at is not None and (sent_at is None or sent_at < self._slowed_at):
            return  # 감속 전 간격으로 보낸 요청 — 이미 늘렸다
        self._slowed_at = asyncio.get_running_loop().time()
        old, self._pacing = self._pacing, min(c.pacing_max_s, self._pacing * c.pacing_factor)
        if self._pacing > old:
            self._emit(
                "ws_pacing_slowdown",
                f"등록·해제 오류 응답({kind}) — 간격 {old:g} → {self._pacing:g}초"
                f" (상한 {c.pacing_max_s:g}초)",
                "warning",
            )
        else:
            self._emit(
                "ws_pacing_slowdown",
                f"등록·해제 오류 응답({kind}) — 간격이 상한 {c.pacing_max_s:g}초인데도 온다",
                "warning",
                every=60,
                key="ws_pacing_at_max",
            )

    def _reply_health(self, what: str, sub: Subscription, kind: AckKind, c: WsControl) -> None:
        kinds: dict[AckKind, tuple[str, Severity]] = {
            "key_rejected": ("ws_key_rejected", "critical"),
            "key_in_use": ("ws_key_in_use", "critical"),
            "max_subscriptions": ("ws_max_subscriptions", "critical"),
        }
        hk, sev = kinds.get(kind, ("ws_subscribe_rejected", "warning"))
        detail = f"{what} 거절 {sub.tr_id} {sub.key}: {c.msg_cd or ''} {c.msg1 or ''}".strip()
        self._emit(hk, detail[:200], sev, every=60, key=f"{hk}:{sub.tr_id}:{sub.key}")

    # ---- 수신 ----------------------------------------------------------------------------

    async def _receive(self, ws: ClientConnection) -> None:
        async for msg in ws:
            raw = msg if isinstance(msg, str) else bytes(msg).decode("utf-8", "replace")
            await self._on_frame(ws, raw)

    async def _on_frame(self, ws: ClientConnection, raw: str) -> None:
        at = self._now()
        frame: WsFrame | None = None
        error = ""
        try:
            frame = parse_frame(raw, self._specs)
        except Exception as e:  # 파서 오류가 녹화를 막지 않는다
            error = f"{type(e).__name__}: {e}"[:200]
        tr_id, key, hint = self._meta(frame, raw)
        tag = self._tag(at, hint)
        env = wrap(
            raw, source="kis_ws", tr_id=tr_id, received_at=at, tagger=lambda _t: tag, key=key
        )
        self._call("on_raw", self._on_raw, env)
        if frame is None:
            self._parse_failed(env, error)
        elif isinstance(frame, WsControl):
            if frame.tr_id == self._cfg.pingpong.tr_id:
                await self._pingpong(ws, raw)
            else:
                cond = self._sync_prims()[1]
                async with cond:
                    kind = self._on_reply(frame)
                    cond.notify_all()
                if kind == "key_rejected":
                    # auth 가 접속키를 갈았을 수 있다 — 끊고 백오프 뒤 Redis 에서 다시 읽어 붙는다
                    await ws.close(code=1000, reason="approval key rejected")
        elif isinstance(frame, WsData):
            self._on_data(frame, env, tag)
        # WsEncrypted(체결통보): 녹화만 — 복호화는 Phase 8

    async def _pingpong(self, ws: ClientConnection, raw: str) -> None:
        if self._cfg.pingpong.reply == "pong" and len(raw.encode()) <= PONG_MAX_BYTES:
            await ws.pong(raw)  # 공식 샘플
            return
        if self._cfg.pingpong.reply == "pong":  # 제어 프레임에 못 싣는 길이 — 텍스트로 되돌린다
            self._emit(
                "ws_pingpong_too_long",
                f"PINGPONG {len(raw.encode())}바이트 > PONG 한도 {PONG_MAX_BYTES} — 텍스트로 응답",
                "warning",
                every=3600,
            )
        await ws.send(raw)

    def _meta(self, frame: WsFrame | None, raw: str) -> tuple[str, str, SessionName | None]:
        """녹화 봉투의 (tr_id, key)와 세션 힌트(체결 TR 의 주간·야간)."""
        if isinstance(frame, WsData):
            spec = self._specs.get(frame.tr_id)
            key = frame.rows[0][0] if frame.rows and frame.rows[0] else ""
            return frame.tr_id, key, spec.session if spec is not None else None
        if isinstance(frame, WsEncrypted):
            return frame.tr_id, "", None
        if isinstance(frame, WsControl):
            return frame.tr_id, frame.tr_key or "", None
        parts = raw.split("|", 3)
        return (parts[1] if len(parts) > 1 and parts[0] in ("0", "1") else ""), "", None

    def _tag(self, at: datetime, hint: SessionName | None) -> Tag:
        """수신 시각의 (거래일, 세션). 체결 TR 인데 장이 막 닫혔으면 late_grace_s 전으로 본다."""
        try:
            td, s = self._tagger(at)
            if hint is not None and s != hint and self._cfg.client.late_grace_s > 0:
                td2, s2 = self._tagger(at - timedelta(seconds=self._cfg.client.late_grace_s))
                if s2 == hint:
                    return td2, s2
            return td, s
        except Exception as e:
            detail = f"거래일·세션 태깅 실패: {type(e).__name__}"
            self._emit("ws_tag_error", detail, "warning", every=60)
            return None, None

    def _on_data(self, frame: WsData, env: RawEnvelope, tag: Tag) -> None:
        if frame.width_mismatch:
            self._emit(
                "ws_width_mismatch",
                f"{frame.tr_id}: 레코드 폭 {frame.width} ≠ 설정 컬럼 {len(frame.columns)}"
                " — KIS 필드 변경 의심, 틱을 만들지 않는다(원문은 녹화)",
                "warning",
                every=300,
                key=f"ws_width_mismatch:{frame.tr_id}",
            )
            self._report_parse_error(env, "width_mismatch")
            return
        try:
            ticks = ticks_from(frame, self._specs)
        except Exception as e:
            self._parse_failed(env, f"{type(e).__name__}: {e}"[:200])
            return
        for t in ticks:
            self._seq += 1
            self._call("on_tick", self._on_tick, TickEvent(t, self._seq, env.received_at, *tag))

    def _parse_failed(self, env: RawEnvelope, error: str) -> None:
        self._emit(
            "ws_parse_error",
            f"{env.tr_id or '?'}: {error}",
            "warning",
            every=60,
            key=f"ws_parse_error:{env.tr_id}",
        )
        self._report_parse_error(env, error)

    def _report_parse_error(self, env: RawEnvelope, error: str) -> None:
        cb = self._on_parse_error
        if cb is not None:
            try:
                cb(env, error)
            except Exception as e:
                self._callback_failed("on_parse_error", e)

    # ---- 공통 ----------------------------------------------------------------------------

    def _call[T](self, name: str, fn: Callable[[T], None], arg: T) -> None:
        try:
            fn(arg)
        except Exception as e:
            self._callback_failed(name, e)

    def _callback_failed(self, name: str, e: Exception) -> None:
        self._emit(
            "ws_callback_error",
            f"{name} 실패: {type(e).__name__}",
            "warning",
            every=60,
            key=f"cb:{name}",
        )

    def _emit(
        self,
        kind: str,
        detail: str,
        severity: Severity,
        *,
        every: float | None = None,
        key: str | None = None,
    ) -> None:
        """health 한 건. every 초 안의 같은 key 는 건너뛴다. 싱크 실패는 세션을 막지 않는다."""
        at = self._now()
        k = key or kind
        last = self._last_emit.get(k)
        if every is not None and last is not None and (at - last).total_seconds() < every:
            return
        self._last_emit[k] = at
        if self._key:
            detail = redact(detail, [self._key])
        try:
            self._health.emit(HealthEvent(kind, detail, at, severity, service=SERVICE))
        except Exception as e:
            log.error("ws-gateway health 싱크 실패: %s", type(e).__name__)


def _cap(budget: Budget) -> int:
    """구독에 쓸 수 있는 자리: 선물 + 옵션 (체결통보 자리·여유는 비워 둔다)."""
    return budget.futures + budget.options

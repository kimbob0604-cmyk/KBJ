"""서비스 사이 Redis 계약 — 채널·키 이름과 메시지 모양은 여기 한 곳에만 (docs/phase1_design.md §2).

- `ws.raw` 채널 (ws-gateway → recorder): `RawEnvelope` JSON — 웹소켓 원문 프레임
- `rest.raw` 채널 (poller → recorder): `RawEnvelope` JSON — REST 응답 본문
- `ticks.fut`·`ticks.opt` 채널 (ws-gateway → engine, Phase 2): `fut_ticks`·`opt_ticks` 한 행 JSON
- `chain.ready` 채널 (poller → engine, Phase 3 설계 §1): `ChainReady` JSON — 한 사이클(전광판
  3만기 + 보강)의 체인 행을 저장 싱크에 넘긴 뒤. 알림일 뿐이다(잃을 수 있다) — engine 은 10초마다
  DB `max(ts)` 로 따라잡는다
- `engine:basis` 키 (engine, TTL 없음): `BasisBook` JSON — 시리즈별 확정 베이시스(metrics §1.3 선물
  교차 확인). 재기동해도 이어 쓴다, 근월물이 바뀌면 비운다(Phase 3 설계 §1)
- `engine.levels`·`engine.metrics` 채널 (engine → api·notifier, Phase 3 설계 §1·§2): `EngineLevels`·
  `EngineMetrics` JSON — 사이클마다 visible 산출(shadow 는 내지 않는다). `quality`·`as_of` 필수
- `engine:latest` 키 (engine, TTL 없음): `EngineLatest` JSON — 마지막 사이클 산출. S_ref 가 없거나
  사이클이 실패하면 직전 것을 `stale` 로 표시해 둔다
- `session:state` 키 (scheduler → 모두, TTL 없음): `SessionState` JSON — 마지막 상태
- `session.events` 채널 (scheduler → 모두): `SessionState` JSON — 상태가 바뀔 때
- `kis:master`·`kis:master:sha` 키 (scheduler → poller·ws-gateway): `MasterSnapshot` JSON·sha256
- `poller:chain_context` 키 (poller → ws-gateway): `ChainContextSnapshot` JSON — 월물리스트·
  최종거래일·선물 코드
- `health:heartbeat:<서비스>` 키(TTL) (각 서비스 → compose healthcheck): `Heartbeat` JSON
- `krx:calls:<YYYYMMDD>` 키(TTL 3일) (scheduler): 그날(KST) KRX Open API 호출 수(정수) — 재기동해도
  하루 상한이 이어진다(services/scheduler/krx.py)

- pub/sub 은 받는 쪽이 없으면 사라진다. 원문(`ws.raw`·`rest.raw`)을 내는 쪽은 `PUBLISH` 가 돌려준
  수신자 수가 0 이거나 Redis 오류면 직접 DB 에 쓴다(`publish_or_none`) — recorder 가 죽어도 잃지
  않는다. 둘 다 써도 raw_messages 는 내용 digest 가 키라 한 행이다
- 토큰·접속키(`kis:token`·`kis:ws_key`)와 레이트리미터 키는 auth·ratelimit 모듈이 정한다
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from redis import Redis
from redis.exceptions import RedisError

from core.calendar import SessionInfo, State
from services.recorder.envelope import RawEnvelope

WS_RAW = "ws.raw"
REST_RAW = "rest.raw"
RAW_CHANNELS: tuple[str, ...] = (WS_RAW, REST_RAW)
TICKS_FUT = "ticks.fut"
TICKS_OPT = "ticks.opt"
CHAIN_READY = "chain.ready"
ENGINE_BASIS_KEY = "engine:basis"
ENGINE_LEVELS = "engine.levels"
ENGINE_METRICS = "engine.metrics"
ENGINE_LATEST_KEY = "engine:latest"
SESSION_STATE = "session:state"
SESSION_EVENTS = "session.events"
MASTER_KEY = "kis:master"
MASTER_SHA_KEY = "kis:master:sha"
CHAIN_CONTEXT_KEY = "poller:chain_context"
HEARTBEAT_PREFIX = "health:heartbeat:"
KRX_CALLS_PREFIX = "krx:calls:"


def heartbeat_key(service: str) -> str:
    return HEARTBEAT_PREFIX + service


def krx_calls_key(day: date) -> str:
    """KST 날짜 하루의 KRX 호출 수 키."""
    return f"{KRX_CALLS_PREFIX}{day:%Y%m%d}"


class _Msg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _utc(v: datetime) -> datetime:
    return v.astimezone(UTC)


class SessionState(_Msg):
    """`session:state`·`session.events` — `core.calendar.state_at` 결과와 판정 시각."""

    state: State
    trade_date: date | None
    session: Literal["day", "night"] | None
    at: AwareDatetime

    _at = field_validator("at")(_utc)

    @classmethod
    def of(cls, info: SessionInfo, at: datetime) -> SessionState:
        return cls(state=info.state, trade_date=info.trade_date, session=info.session, at=at)


class MasterSnapshot(_Msg):
    """KIS 지수선물옵션 마스터 원문(`.mst`, cp949 를 푼 글) — 읽는 쪽은 `parse_master(text)`.

    trade_date·session 은 이 마스터를 받은 세션(PRE_DAY → 그날 주간, PRE_NIGHT → 다음 거래일 야간).
    장 밖에서 받았으면 None.
    """

    asof: AwareDatetime
    trade_date: date | None
    session: Literal["day", "night"] | None
    rows: int
    sha256: str
    text: str

    _asof = field_validator("asof")(_utc)

    @staticmethod
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ExpiryEntry(_Msg):
    cls: str  # '' 월물 · WKM · WKI
    mtrt: str
    last_trade_date: date
    source: Literal["kis", "calendar"]


class ChainContextSnapshot(_Msg):
    """poller 가 알아낸 체인 문맥 — ws-gateway 가 같은 최근접 만기를 고르게."""

    at: AwareDatetime
    version: int
    listed: dict[str, list[str]]
    expiries: list[ExpiryEntry]
    futures_codes: list[str]

    _at = field_validator("at")(_utc)


class Heartbeat(_Msg):
    service: str
    at: AwareDatetime
    pid: int
    status: dict[str, Any] = {}

    _at = field_validator("at")(_utc)


# ── 체인 사이클 알림 (poller → engine) ───────────────────────────────────────

SessionName = Literal["day", "night"]
MrktCls = Literal["", "WKM", "WKI"]  # 월물리스트 시장분류 — '' 월물 · WKM · WKI
ExpiryCode = Annotated[str, Field(pattern=r"^[0-9]{6}$")]  # 6자리 mtrt_yymm
SeriesKey = tuple[MrktCls, ExpiryCode]


def series_label(key: SeriesKey) -> str:
    """poller `Series.label` 과 같은 표기 — '' 월물은 `M:202610`, 위클리는 `WKM:261001`."""
    cls, expiry = key
    return f"{cls or 'M'}:{expiry}"


class ChainReady(_Msg):
    """`chain.ready` — poller 가 한 사이클의 체인 행을 저장 싱크에 넘긴 뒤 (Phase 3 설계 §1).

    ts: 이 사이클에 넘긴 행 중 가장 늦은 수신 시각(UTC). engine 은 시리즈마다 이 ts 까지의 최신
    행을 DB 에서 읽는다. series: 이 사이클에 행이 온 시리즈 (시장분류, 6자리 만기) — WKM·WKI 가 같은
    만기값(예 261001)을 쓸 수 있어 쌍이 키다. 정렬·중복 없음.
    """

    ts: AwareDatetime
    trade_date: date
    session: SessionName
    series: tuple[SeriesKey, ...] = Field(min_length=1)

    _ts = field_validator("ts")(_utc)

    @field_validator("series")
    @classmethod
    def _sorted_unique(cls, v: tuple[SeriesKey, ...]) -> tuple[SeriesKey, ...]:
        return tuple(sorted(set(v)))


# ── engine 상태 (engine:basis) ───────────────────────────────────────────────


class BasisEntry(_Msg):
    """시리즈 하나의 확정 베이시스(pt) — 그 만기의 마지막 품질 ok F − 그때 근월물 선물가
    (`core.forward.confirm_basis`). trade_date·session 은 확정한 세션 — 나이는
    `core.forward.basis_age`(같은 저녁 야간 0, 다음 주간 1), 이월 상한 2거래일(metrics §1.3)."""

    basis: Decimal
    trade_date: date
    session: SessionName
    confirmed_at: AwareDatetime

    _at = field_validator("confirmed_at")(_utc)

    @field_validator("basis")
    @classmethod
    def _finite(cls, v: Decimal) -> Decimal:
        if not v.is_finite():
            raise ValueError(f"베이시스는 유한해야 한다: {v}")
        return v


class BasisBook(_Msg):
    """`engine:basis` — 근월물 코드와 시리즈 라벨(`series_label`) → 확정 베이시스.

    near_code 가 지금 근월물과 다르면(분기 만기일 15:20 뒤 차월물로 롤) 옛 근월물 기준이라 비운다.
    """

    near_code: str | None = None
    entries: dict[str, BasisEntry] = Field(default_factory=dict[str, BasisEntry])


# ── engine 산출 (engine.levels·engine.metrics·engine:latest) ────────────────

Quality = Literal["ok", "stale", "estimated", "invalid"]
LevelScope = Literal["all", "nearest", "0dte"]
MetricScope = Literal["all", "nearest", "0dte", "series"]


class LevelOut(_Msg):
    """레벨 하나 — `levels` 행과 같은 뜻(services/engine/records.py `LevelRecord`)."""

    scope: LevelScope
    name: str
    value: float | None
    quality: Quality
    detail: dict[str, Any] = Field(default_factory=dict[str, Any])
    reasons: tuple[str, ...] = ()


class MetricOut(_Msg):
    """지표 하나 — `metrics` 행과 같은 뜻. visible 만 발행한다."""

    metric: str
    scope: MetricScope
    key: str = ""
    value: float | None
    quality: Quality
    payload: dict[str, Any] = Field(default_factory=dict[str, Any])


class SeriesOut(_Msg):
    """시리즈 하나의 평가 요약 — F·품질·사유(`core.forward.ForwardResult`)."""

    label: str
    mrkt_cls: MrktCls
    expiry: ExpiryCode
    status: Literal["evaluated", "expired", "no_expiry", "failed"]
    expiry_date: date | None = None
    forward: float | None = None
    forward_quality: Quality | None = None
    forward_reasons: tuple[str, ...] = ()
    forward_notes: tuple[str, ...] = ()
    input_quality: Quality = "ok"
    basis_age: int | None = None


class _EngineOut(_Msg):
    as_of: AwareDatetime  # 사이클 기준 시각(chain.ready ts)
    trade_date: date
    session: SessionName
    quality: Quality  # 사이클 품질 — S_ref ⊕ 범위 all 순GEX

    _as_of = field_validator("as_of")(_utc)


class EngineLevels(_EngineOut):
    """`engine.levels` — 사이클 하나의 레벨(범위 all·nearest·0dte)."""

    levels: tuple[LevelOut, ...]


class EngineMetrics(_EngineOut):
    """`engine.metrics` — 사이클 하나의 visible 지표."""

    metrics: tuple[MetricOut, ...]


class EngineLatest(_EngineOut):
    """`engine:latest` — 마지막 사이클 산출. stale: S_ref 가 없거나 사이클이 실패해 이 값이
    새로 계산되지 않았다(stale_reason — 품질도 모두 stale 이상으로 내린다)."""

    computed_at: AwareDatetime
    stale: bool = False
    stale_reason: str | None = None
    stale_at: AwareDatetime | None = None
    near_code: str | None = None
    s_ref: float | None = None
    s_ref_quality: Quality | None = None
    series: tuple[SeriesOut, ...] = ()
    levels: tuple[LevelOut, ...] = ()
    metrics: tuple[MetricOut, ...] = ()

    _computed = field_validator("computed_at")(_utc)

    @field_validator("stale_at")
    @classmethod
    def _stale_utc(cls, v: datetime | None) -> datetime | None:
        return None if v is None else v.astimezone(UTC)


# ── 원문 발행 ────────────────────────────────────────────────────────────────


def encode_envelope(env: RawEnvelope) -> str:
    return env.model_dump_json()


def decode_envelope(data: bytes | str) -> RawEnvelope:
    """pydantic 검증. 틀리면 ValidationError·ValueError."""
    return RawEnvelope.model_validate_json(data)


def publish_or_none(redis: Redis, channel: str, message: str) -> int | None:
    """발행하고 받은 구독자 수. Redis 오류면 None — 호출자는 0·None 이면 직접 저장한다."""
    try:
        return int(redis.publish(channel, message))  # pyright: ignore[reportArgumentType]
    except RedisError:
        return None

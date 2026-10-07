"""engine 서비스 — 진입점 `python -m services.engine` (docs/phase3_design.md §1·§2·§7-1).

Phase 1 수집물(DB·Redis)을 읽어 core 로 계산하고(`services/engine/evaluate.py`) 산출을
저장·발행한다.

- 입력: poller 가 한 사이클을 싱크에 넘긴 뒤 내는 `chain.ready{ts, trade_date, session, series}` 를
  받으면 그 ts 까지의 그 세션 시리즈별 최신 행(`chain_snapshots`)·선물 행(`fut_board`)·최종거래일
  (`series_expiries`)을 DB 에서 읽는다. 마스터·poller 문맥은 Redis(`EngineContext` — 근월물 코드·전
  행사가·선물 결제월). 알림은 잃을 수 있어 10초마다 그 세션 체인 행을 보고 따라잡는다(지금이 장
  밖이면 5분 전 세션 — 마감 직후 마지막 행): 알림 여유(poller 최대 대기 30초 + 5초)보다 오래된 행이
  마지막 as_of 뒤에 있으면 그 알림을 놓친 것이라 그 세션 `max(ts)` 까지 한 사이클. 여유 안의 행은
  알림이 오는 중이다 — 따라잡기가 알림을 앞질러 반쪽 사이클을 돌지 않는다(실패한 사이클은 여유 없이
  다음 따라잡기에서 다시). 이미 계산한 as_of 이하는 다시 계산하지 않는다
- 저장: `levels`·`metrics`·`strike_gex`·`option_iv`(003 — DO UPDATE, 같은 사이클 재계산 멱등).
  표 하나의 쓰기 실패는 health 만 — 나머지 쓰기·발행은 한다
- 발행: `engine.levels`·`engine.metrics`(visible 만)와 `engine:latest`(`services/engine/publish.py`)
- S_ref 가 없으면(`no_s_ref`) 그 사이클은 F 를 만들지 않는다 — 직전 산출을 `engine:latest` 에서
  stale 로 표시하고 health. 사이클 전체가 실패하면(입력 읽기 등) 같게 stale + health, 다음 따라잡기
  (10초)에서 다시 한다
- 확정 베이시스는 Redis `engine:basis` 에 둔다 — 재기동해도 이어 쓴다(근월물 롤이면 비운다)
- health 는 같은 (종류, 대상)을 10분에 한 번(사이클마다 되풀이하지 않게 — 사이클 실패의 대상은
  세션·예외 종류), 로그는 구조화 JSON
- 하트비트 `health:heartbeat:engine`(compose healthcheck). 멈춤: SIGTERM → 지금 사이클을 끝내고
  끝난다
- 일별 지표(IV 랭크·퍼센타일·IV − HV — `services/engine/daily.py`): `POST_DAY`(15:45~17:50) 첫
  곁일에 그 거래일 한 번 — 입력은 DB(마지막 주간 사이클의 월물 ATM IV·일별 이력·KRX 일별).
  읽기·계산이 통째로 실패하면 health(`engine_daily_failed`)와 1분 뒤 다시(POST_DAY 동안). 딜러
  가정 점검(§6.3 — 그날 주간 증권 계정 콜·풋 순매수, 연속 불일치 5거래일 경고)도 POST_DAY 한 번,
  일별 지표와 따로(실패도 따로 1분 뒤 다시)
- 플로우(`services/engine/flow.py`): 사이클마다 PCR·맥스페인(등록부)·OI 증감(`oi_changes`).
  `ticks.opt`·`ticks.fut`(ws-gateway 체결 행)도 구독해 HIRO-lite 누적(옵션 틱)·수신 순번 공백
  리셋(둘 다)·대량 체결 판정 — 틱이 쏟아지는 동안 곁일은 1초에 한 번. 곁일: ws-gateway 연결 사건
  (health_events `ws_connected`·`ws_disconnected`·`ws_connect_failed` — 5초마다, 새 사건이면 HIRO
  리셋 `ws_disconnect`. 그 사건 뒤에 재연결 뒤 첫 틱의 순번 공백으로 이미 리셋했으면 사유만),
  hiro 행(10초마다 바뀐 것만), block_trade 행(1초), 대량 체결 기준(지금 세션 거래일마다 한 번 —
  opt_ticks 20거래일 전엔 비활성, 읽기 실패는 1분 뒤 다시). 틱 하나·곁일
  하나의 예외는 그것만 health(`engine_flow_failed`). 투자자별 순매수(§6.2)는 30초마다 지금 세션의
  새 investor_flow 행을 그대로 `investor_flow` 지표 행으로
- 선물(`services/engine/futures.py`, §7): 1분마다 새로 녹화된 분봉 조회 원문 output1(KIS 선물
  필드) → 베이시스·이론 베이시스·괴리율·OI 증감·체결강도 행, 자체 선물가 − 지수 대 KIS basis
  0.05pt 교차검증 실패는 health(`engine_futures_basis_check`)
- 기능 플래그(설계 §4, `core.features`): 기동 때 `config/features.yaml` 을 읽는다 — 모르는 이름·값
  이면 기동하지 않는다(종료 코드 2). 곁일이 30초마다 파일 서명(mtime·크기)을 보고 바뀌었으면 다시
  읽어 다음 계산부터 쓴다. 다시 읽다 틀리면 직전 플래그를 그대로 쓰고 health(`engine_flags_invalid`
  — 같은 파일은 한 번). off 는 계산 안 함, shadow 는 저장만(행의 flag), visible 만 발행
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, cast, get_args

from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError

from core.calendar import KST, State, TradingCalendar, state_at
from core.features import FeatureError, FeatureWatcher
from core.metrics.flow import BLOCK_DAYS, BlockThresholds, block_thresholds, moneyness_bucket
from core.metrics.futures import futures_metrics
from core.metrics.vol import RANK_WINDOW, Settlement, window_start
from data.store import FuturesTickRecord, OptionTickRecord
from services.auth.health import HealthEvent, HealthSink
from services.bus import (
    CHAIN_READY,
    TICKS_FUT,
    TICKS_OPT,
    ChainReady,
    MrktCls,
    SeriesKey,
    SessionName,
)
from services.engine.context import EngineContext
from services.engine.daily import (
    DAILY_ATM,
    DailyInput,
    DailyResult,
    KrxDay,
    daily_settled,
    daily_ts,
    evaluate_daily,
    krx_rows,
    monthly_last_trade,
    near_settle,
    nearest_monthly,
)
from services.engine.evaluate import (
    CycleInput,
    CycleResult,
    EngineHealth,
    ExpiryInfo,
    evaluate_cycle,
    near_code_from_quotes,
)
from services.engine.extended import REGISTRY
from services.engine.flow import (
    BLOCK_F_LOOKBACK,
    BLOCK_STATUS,
    BLOCK_TRADE,
    DEALER_CHECK,
    DEALER_LOOKBACK_DAYS,
    FLOW_REGISTRY,
    HIRO,
    HIRO_EVERY_S,
    INVESTOR_EVERY_S,
    INVESTOR_FLOW,
    OI_FLAG,
    InvestorFeed,
    OiTracker,
    TickFlow,
    block_status,
    dealer_record,
)
from services.engine.futures import (
    FUTURES_EVERY_S,
    FUTURES_FLAG,
    FUTURES_LOOKBACK,
    FUTURES_TR,
    basis_health,
    futures_rows,
    quote_of,
)
from services.engine.publish import EnginePublisher, latest_of, mark_stale
from services.engine.records import (
    LevelRecord,
    MetricRecord,
    OiChangeRecord,
    OptionIvRecord,
    StrikeGexRecord,
)
from services.engine.registry import Flag, MetricPlugin, resolve_flag
from services.poller.context import session_tag
from services.poller.ready import READY_MAX_WAIT_S
from services.poller.records import ChainRecord, FuturesRecord, InvestorRecord
from services.runtime import Backoff, Heartbeater, log_event, utcnow

SERVICE = "engine"
CATCHUP_S = 10.0  # 알림을 놓쳤을 때 max(ts) 를 보는 주기 (설계 §1)
# 알림 여유 — 이보다 오래된 행이 as_of 뒤에 있으면 알림을 놓쳤다(poller 최대 대기 + 5초) [확인 필요]
CATCHUP_GRACE = timedelta(seconds=READY_MAX_WAIT_S + 5.0)
CATCHUP_LOOKBACK = timedelta(minutes=5)  # 장 밖이면 이만큼 앞 시각의 세션을 본다 [확인 필요]
POLL_S = 1.0  # chain.ready 대기 한 번
EXPIRY_LOOKBACK_DAYS = 14  # series_expiries 를 이만큼 앞 거래일 기록부터 읽는다
HEALTH_REPEAT_S = 600.0  # 같은 (종류, 대상) health 는 10분에 한 번 [확인 필요]
DAILY_RETRY_S = 60.0  # 일별 지표가 통째로 실패하면 이만큼 뒤 다시(POST_DAY 동안) [확인 필요]
WS_EVENTS_EVERY_S = 5.0  # ws-gateway 연결 사건(HIRO 리셋)을 보는 주기 [확인 필요]
# HIRO 리셋 사유가 되는 ws-gateway 연결 사건(health_events) — 끊김·재연결·연결 실패
WS_EVENTS = ("ws_connected", "ws_disconnected", "ws_connect_failed")
FLOW_RETRY_S = 60.0  # 대량 체결 기준 읽기가 실패하면 이만큼 뒤 다시 [확인 필요]
_CLASSES = frozenset(get_args(MrktCls))
# 사이클마다 부르는 확장 지표 — 익스포저·변동성(항목 2) + 플로우 PCR·맥스페인(항목 3)
ENGINE_REGISTRY: tuple[MetricPlugin, ...] = (*REGISTRY, *FLOW_REGISTRY)

log = logging.getLogger("services.engine")

Tag = tuple[date, str]


class EngineReader(Protocol):
    def chain_max_ts(
        self, trade_date: date, session: str, upto: datetime | None = None
    ) -> datetime | None: ...

    def chain_latest(self, trade_date: date, session: str, upto: datetime) -> list[ChainRecord]: ...

    def futures_latest(
        self, trade_date: date, session: str, upto: datetime
    ) -> list[FuturesRecord]: ...

    def expiry_dates(self, since: date) -> list[tuple[str, str, date, str]]: ...

    def metric_last(
        self, trade_date: date, session: str, metric: str, scope: str, key_prefix: str = ""
    ) -> list[MetricRecord]: ...

    def metric_history(
        self, metric: str, scope: str, key: str, first: date, last: date
    ) -> list[MetricRecord]: ...

    def krx_option_days(self, first: date, last: date) -> list[date]: ...

    def krx_iv_rows(
        self, trade_date: date, expiry: str
    ) -> list[tuple[Decimal, str, Decimal | None, Decimal | None, int | None]]: ...

    def krx_futures_settles(self, first: date, last: date) -> list[tuple[date, str, Decimal]]: ...

    def ws_connection_events(
        self, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> tuple[str | None, list[tuple[datetime, str]]]: ...

    def opt_tick_days(self, before: date, limit: int) -> list[date]: ...

    def investor_latest(
        self, trade_date: date, session: str, upto: datetime
    ) -> list[InvestorRecord]: ...

    def kis_rest_outputs(
        self, tr_id: str, after: datetime, upto: datetime, output: str = "output1"
    ) -> list[tuple[datetime, date | None, str | None, dict[str, Any]]]: ...

    def opt_tick_history(
        self, first: date, last: date, lookback: timedelta
    ) -> list[tuple[date, str, Decimal, int, float]]: ...


class EngineSink(Protocol):
    def write_levels(self, rows: Sequence[LevelRecord]) -> None: ...

    def write_metrics(self, rows: Sequence[MetricRecord]) -> None: ...

    def write_strike_gex(self, rows: Sequence[StrikeGexRecord]) -> None: ...

    def write_option_iv(self, rows: Sequence[OptionIvRecord]) -> None: ...

    def write_oi_changes(self, rows: Sequence[OiChangeRecord]) -> None: ...


@dataclass
class EngineStats:
    cycles: int = 0  # 산출을 낸 사이클
    no_s_ref: int = 0  # S_ref 가 없어 F 를 만들지 않은 사이클
    failures: int = 0  # 사이클 전체 실패(입력 읽기 등)
    ready: int = 0  # 받은 chain.ready
    bad_ready: int = 0  # 형식이 틀린 알림
    catchups: int = 0  # 따라잡기로 돈 사이클
    write_failures: int = 0
    dailies: int = 0  # 일별 지표를 낸 거래일
    oi_rows: int = 0  # 쓴 oi_changes 행
    ticks: int = 0  # 받은 체결 틱(ticks.opt·ticks.fut)
    bad_ticks: int = 0  # 형식이 틀리거나 반영하지 못한 틱
    hiro_rows: int = 0
    block_trades: int = 0
    investor_rows: int = 0  # investor_flow 에서 옮긴 지표 행
    futures_rows: int = 0  # 선물 지표 행(§7)
    bad_futures: int = 0  # 태그가 없거나 검증에 실패한 분봉 원문 output1
    last_as_of: datetime | None = None


class EngineService:
    def __init__(
        self,
        reader: EngineReader,
        sink: EngineSink,
        publisher: EnginePublisher,
        context: EngineContext,
        calendar: TradingCalendar,
        *,
        health: HealthSink,
        now: Callable[[], datetime] = utcnow,
        mono: Callable[[], float] = time.monotonic,
        heartbeat: Heartbeater | None = None,
        flush_spool: Callable[[], bool] | None = None,
        catchup_s: float = CATCHUP_S,
        catchup_grace: timedelta = CATCHUP_GRACE,
        registry: Sequence[MetricPlugin] = ENGINE_REGISTRY,
        flags: Mapping[str, Flag] | None = None,
        features: FeatureWatcher | None = None,
    ) -> None:
        """flags: 기능 플래그 표(없으면 카탈로그 기본값). features: 플래그 파일 감시자 — 주면 그
        파일의 플래그로 시작하고 곁일마다(30초) 다시 읽는다(flags 보다 먼저)."""
        if catchup_s <= 0:
            raise ValueError("catchup_s > 0")
        self.reader = reader
        self.sink = sink
        self.publisher = publisher
        self.context = context
        self.cal = calendar
        self._health_sink = health
        self._now = now
        self._mono = mono
        self._heartbeat = heartbeat
        self._flush = flush_spool
        self._catchup_s = catchup_s
        self._grace = catchup_grace
        self.registry = tuple(registry)
        self.features = features
        self.flags = features.features.resolved() if features is not None else flags
        self.stats = EngineStats()
        self.basis = publisher.load_basis()
        self.latest = publisher.load_latest()
        self.last_as_of: datetime | None = self.latest.as_of if self.latest else None
        self.fresh_since: datetime | None = None  # 직전 사이클 as_of — 재기동 뒤엔 없다
        self._next_catchup = 0.0
        self._retry = False  # 직전 사이클이 통째로 실패했다 — 따라잡기가 여유 없이 다시 한다
        self._emitted: dict[tuple[str, str], datetime] = {}
        # 주기 있는 확장 지표(Charm 2분)를 마지막으로 부른 (세션, 사이클 시각)
        self._plugin_runs: dict[str, tuple[Tag, datetime]] = {}
        self._daily_done: set[date] = set()  # 일별 지표를 낸 거래일(최근 것만)
        self._daily_retry_at = 0.0  # 일별 지표 실패 뒤 다시 할 단조 시각
        self._dealer_done: set[date] = set()  # 딜러 가정 점검을 낸 거래일(최근 것만)
        self._dealer_retry_at = 0.0
        self.oi = OiTracker()  # §6.7 종목별 직전 OI 스냅샷(세션이 바뀌면 비운다)
        self.flow = TickFlow()  # §6.1·§6.4 틱 스트림 상태
        self.investors = InvestorFeed()  # §6.2 investor_flow 에서 옮긴 마지막 행 시각
        self._fut_seen = now() - FUTURES_LOOKBACK  # §7 이 시각 뒤의 분봉 원문 output1 만 본다
        self._ws_seen = now()  # 이 시각 뒤의 ws-gateway 연결 사건만 리셋 사유로 본다
        self._next_job: dict[str, float] = {}  # 곁일(플로우) 이름 → 다음 단조 시각
        self._next_side = 0.0  # run 루프 — 틱이 쏟아질 때 곁일(tick)을 부를 다음 단조 시각

    # ── 사이클 ──

    def on_ready(self, msg: ChainReady) -> CycleResult | None:
        """알림 한 건 — 이미 계산한 as_of 이하면 건너뛴다."""
        self.stats.ready += 1
        if self.last_as_of is not None and msg.ts <= self.last_as_of:
            return None
        return self.run_cycle(msg.trade_date, msg.session, msg.ts)

    def catch_up(self) -> CycleResult | None:
        """알림을 놓쳤을 때 — 여유보다 오래된 체인 행이 마지막 as_of 뒤에 있으면 그 세션 max(ts)
        까지 사이클(여유 안의 행은 알림이 오는 중). 직전 사이클이 실패했으면 여유 없이."""
        tag = self._catchup_tag()
        if tag is None:
            return None
        cutoff = None if self._retry else self._now() - self._grace
        try:
            missed = self.reader.chain_max_ts(*tag, upto=cutoff)
            if missed is None or (self.last_as_of is not None and missed <= self.last_as_of):
                return None
            max_ts = missed if cutoff is None else self.reader.chain_max_ts(*tag)
        except Exception as e:
            self._health(
                EngineHealth(
                    "engine_read_failed", "warning", f"체인 max(ts) 읽기 실패: {type(e).__name__}"
                ),
                tag,
            )
            return None
        if max_ts is None:  # 그 사이 행이 사라졌다(보존 정리 등) — 다음에
            return None
        self.stats.catchups += 1
        return self.run_cycle(tag[0], cast(Any, tag[1]), max_ts)

    def _catchup_tag(self) -> Tag | None:
        now = self._now()
        tag = session_tag(now, self.cal) or session_tag(now - CATCHUP_LOOKBACK, self.cal)
        return None if tag is None else (tag[0], tag[1])

    def run_cycle(self, trade_date: date, session: Any, as_of: datetime) -> CycleResult | None:
        """사이클 하나 — 읽기 → 평가 → 저장 → 발행. 예외를 올리지 않는다."""
        now = self._now()
        tag: Tag = (trade_date, session)
        try:
            inp = self._inputs(trade_date, session, as_of)
            result = evaluate_cycle(
                inp,
                self.basis,
                cal=self.cal,
                registry=self.registry,
                flags=self.flags,
                due=self._due(tag, as_of),
            )
        except Exception as e:  # 사이클 전체 — 직전 산출을 stale 로, 다음 따라잡기에서 다시
            self.stats.failures += 1
            self._retry = True
            err = type(e).__name__
            self._health(  # 대상은 세션·예외 종류 — as_of 는 detail·로그에만(10분에 한 번 묶이게)
                EngineHealth(
                    "engine_cycle_failed",
                    "warning",
                    f"사이클 실패({as_of.isoformat()}): {err}: {e}"[:250],
                    subject=f"{trade_date.isoformat()}/{session}/{err}",
                ),
                tag,
            )
            self._stale("cycle_failed", now)
            return None
        self.last_as_of = self.stats.last_as_of = as_of
        self.fresh_since = as_of
        self._retry = False
        for name in result.plugins_run:
            self._plugin_runs[name] = (tag, as_of)
        if result.basis != self.basis:
            self.basis = result.basis
            self.publisher.save_basis(result.basis)
        for h in result.health:
            self._health(h, tag)
        if result.status == "no_s_ref":
            self.stats.no_s_ref += 1
            self._stale("no_s_ref", now)
            log_event(log, logging.WARNING, SERVICE, "cycle_no_s_ref", tag, as_of=as_of.isoformat())
            return result
        self._write(result, tag)
        latest = latest_of(result, now)
        self.publisher.publish(result, latest)
        self.latest = latest
        self.stats.cycles += 1
        self._flow_cycle(inp, result, tag)
        log_event(
            log,
            logging.INFO,
            SERVICE,
            "cycle",
            tag,
            as_of=as_of.isoformat(),
            quality=result.quality,
            s_ref=None if result.s_ref is None else str(result.s_ref.price),
            series={o.label: o.status for o in result.series},
            levels=len(result.levels),
            metrics=len(result.metrics),
            strike_gex=len(result.strike_gex),
            option_iv=len(result.option_iv),
        )
        return result

    def _flow_cycle(self, inp: CycleInput, result: CycleResult, tag: Tag) -> None:
        """사이클 뒤 플로우 — HIRO·대량 체결의 자체 델타·F, OI 증감(`oi_changes`). 예외는 그것만
        health(사이클은 끝났다)."""
        try:
            self.flow.learn(result)
        except Exception as e:
            self._flow_failed(HIRO, e, tag)
        oi_flag = resolve_flag(OI_FLAG, self.flags)
        if oi_flag == "off":
            return
        try:
            rows, health = self.oi.cycle(inp, result, oi_flag)
        except Exception as e:
            self._health(
                EngineHealth(
                    "engine_metric_failed",
                    "warning",
                    f"{OI_FLAG}: {type(e).__name__}: {e}"[:250],
                    subject=OI_FLAG,
                ),
                tag,
            )
            return
        for h in health:
            self._health(h, tag)
        self.stats.oi_rows += len(rows)
        self._write_one("oi_changes", lambda: self.sink.write_oi_changes(rows), len(rows), tag)

    def _due(self, tag: Tag, as_of: datetime) -> Callable[[MetricPlugin], bool]:
        """확장 지표를 이 사이클에 부를지 — 주기(`every_s`)가 없으면 늘, 있으면 같은 세션에서 마지막
        으로 부른 사이클 시각부터 every_s 가 지났을 때(세션이 바뀌면 바로)."""

        def due(p: MetricPlugin) -> bool:
            if p.every_s is None:
                return True
            last = self._plugin_runs.get(p.name)
            if last is None or last[0] != tag:
                return True
            return (as_of - last[1]).total_seconds() >= p.every_s

        return due

    def _inputs(self, trade_date: date, session: Any, as_of: datetime) -> CycleInput:
        self.context.refresh()
        chain = self.reader.chain_latest(trade_date, session, as_of)
        futures = self.reader.futures_latest(trade_date, session, as_of)
        since = trade_date - timedelta(days=EXPIRY_LOOKBACK_DAYS)
        expiries: dict[SeriesKey, ExpiryInfo] = {}
        for cls, expiry, d, src in self.reader.expiry_dates(since):
            if cls in _CLASSES and src in ("kis", "calendar"):
                key = cast(SeriesKey, (cls, expiry))
                expiries[key] = ExpiryInfo(d, cast(Any, src))
        near = self.context.near_code(as_of)
        if near is None:
            near = near_code_from_quotes(futures, session)
            if near is not None:
                self._health(
                    EngineHealth(
                        "engine_near_code_fallback",
                        "info",
                        f"마스터·poller 문맥이 없어 선물 행의 잔존일수로 근월물 {near} 을 골랐다",
                        subject=near,
                    ),
                    (trade_date, session),
                )
        keys = {cast(SeriesKey, (r.mrkt_cls, r.expiry)) for r in chain if r.mrkt_cls in _CLASSES}
        strikes = {k: s for k in keys if (s := self.context.strikes(k))}
        return CycleInput(
            as_of=as_of,
            trade_date=trade_date,
            session=session,
            chain=chain,
            futures=futures,
            expiries=expiries,
            near_code=near,
            strikes=strikes,
            futures_months=self.context.futures_months(),
            fresh_since=self.fresh_since,
        )

    def _write(self, result: CycleResult, tag: Tag) -> None:
        sink = self.sink
        self._write_one("levels", lambda: sink.write_levels(result.levels), len(result.levels), tag)
        self._write_one(
            "metrics", lambda: sink.write_metrics(result.metrics), len(result.metrics), tag
        )
        self._write_one(
            "strike_gex",
            lambda: sink.write_strike_gex(result.strike_gex),
            len(result.strike_gex),
            tag,
        )
        self._write_one(
            "option_iv", lambda: sink.write_option_iv(result.option_iv), len(result.option_iv), tag
        )

    def _write_one(self, table: str, write: Callable[[], None], n: int, tag: Tag) -> None:
        """표 하나 — 실패해도 나머지 쓰기·발행은 한다."""
        if not n:
            return
        try:
            write()
        except Exception as e:
            self.stats.write_failures += 1
            self._health(
                EngineHealth(
                    "engine_write_failed",
                    "warning",
                    f"{table} {n}행 저장 실패: {type(e).__name__}",
                    subject=table,
                ),
                tag,
            )

    def _stale(self, reason: str, now: datetime) -> None:
        if self.latest is None:
            return
        self.latest = mark_stale(self.latest, reason, now)
        if self.publisher.set_latest(self.latest):
            self.publisher.stats.stale_marks += 1

    # ── 곁일 ──

    def _health(self, h: EngineHealth, tag: Tag | None = None) -> None:
        """같은 (종류, 대상)은 HEALTH_REPEAT_S 에 한 번 — 로그는 매번."""
        now = self._now()
        key = (h.kind, h.subject or h.detail)
        level = {"info": logging.INFO, "warning": logging.WARNING}.get(h.severity, logging.CRITICAL)
        log_event(log, level, SERVICE, h.kind, tag, detail=h.detail)
        last = self._emitted.get(key)
        if last is not None and (now - last).total_seconds() < HEALTH_REPEAT_S:
            return
        # 10분 지난 기록은 다음 health 를 막지 않으니 버린다 — 대상이 바뀌는 health 로 늘지 않게
        self._emitted = {
            k: t for k, t in self._emitted.items() if (now - t).total_seconds() < HEALTH_REPEAT_S
        }
        self._emitted[key] = now
        try:
            self._health_sink.emit(HealthEvent(h.kind, h.detail, now, h.severity, service=SERVICE))
        except Exception as e:
            log_event(log, logging.ERROR, SERVICE, "health_failed", error=type(e).__name__)

    def tick(self) -> None:
        """사이 일: 플래그 파일(30초)·따라잡기(10초)·일별 지표(POST_DAY)·스풀·하트비트. 하나가
        실패해도 나머지는 한다."""
        try:
            self._reload_flags()
        except Exception as e:  # 감시자는 올리지 않지만 방어선 — 직전 플래그 그대로
            log_event(log, logging.ERROR, SERVICE, "flags_error", error=type(e).__name__)
        if self._mono() >= self._next_catchup:
            self._next_catchup = self._mono() + self._catchup_s
            try:
                self.catch_up()
            except Exception as e:  # catch_up 은 올리지 않지만 방어선
                log_event(log, logging.ERROR, SERVICE, "catchup_error", error=type(e).__name__)
        try:
            self._daily_tick()
        except Exception as e:  # run_daily 는 올리지 않지만 방어선
            log_event(log, logging.ERROR, SERVICE, "daily_error", error=type(e).__name__)
        self._flow_jobs()
        if self._flush is not None:
            try:
                self._flush()
            except Exception as e:
                log_event(log, logging.ERROR, SERVICE, "spool_flush_failed", error=type(e).__name__)
        if self._heartbeat is not None:
            s = self.stats
            self._heartbeat.beat(
                cycles=s.cycles,
                no_s_ref=s.no_s_ref,
                failures=s.failures,
                last_as_of=None if s.last_as_of is None else s.last_as_of.isoformat(),
            )

    def _reload_flags(self) -> None:
        """플래그 파일이 바뀌었으면 다시 읽은 표로 — 틀리면 직전 표 그대로 + health."""
        if self.features is None:
            return
        r = self.features.poll()
        if r is None:
            return
        if r.error is not None:
            self._health(
                EngineHealth(
                    "engine_flags_invalid",
                    "warning",
                    f"기능 플래그 파일을 다시 읽지 못해 직전 플래그를 쓴다: {r.error}"[:250],
                    subject=r.error[:120],
                )
            )
            return
        self.flags = r.features.resolved()
        log_event(
            log,
            logging.INFO,
            SERVICE,
            "flags_reloaded",
            changes={k: list(v) for k, v in r.changes.items()},
        )

    # ── 플로우 (틱·곁일) ──

    def on_tick(self, channel: str, data: Any) -> None:
        """체결 틱 한 건 — 옵션은 HIRO-lite·대량 체결, 선물은 수신 순번만. 형식이 틀리면 센다.
        플래그 off 는 계산하지 않는다 — hiro off 면 누적을 버리고(`TickFlow.stop_hiro`, 다시 켜면
        처음부터) 대량 체결만 켜졌으면 순번·대량 체결 판정만."""
        hiro, blocks = (resolve_flag(n, self.flags) for n in (HIRO, BLOCK_STATUS))
        if hiro == "off" and blocks == "off":
            self.flow.stop_hiro()
            return
        try:
            if channel == TICKS_FUT:
                self.flow.on_futures(FuturesTickRecord.model_validate_json(data))
                self.stats.ticks += 1
                return
            rec = OptionTickRecord.model_validate_json(data)
        except (ValidationError, ValueError, TypeError) as e:
            self.stats.bad_ticks += 1
            log_event(
                log, logging.WARNING, SERVICE, "bad_tick", channel=channel, error=type(e).__name__
            )
            return
        self.stats.ticks += 1
        tag: Tag = (rec.trade_date, rec.session)
        try:
            self.flow.on_option(rec, hiro=hiro != "off", blocks=blocks != "off")
        except Exception as e:  # 한 틱의 결함 — 그 틱만(누적은 그대로)
            self.stats.bad_ticks += 1
            self._flow_failed(HIRO, e, tag)
        for h in self.flow.drain_health():
            self._health(h, tag)

    def _flow_failed(self, what: str, e: Exception, tag: Tag | None) -> None:
        self._health(
            EngineHealth(
                "engine_flow_failed",
                "warning",
                f"{what}: {type(e).__name__}: {e}"[:250],
                subject=what,
            ),
            tag,
        )

    def _due_job(self, name: str, every_s: float) -> bool:
        now = self._mono()
        if now < self._next_job.get(name, 0.0):
            return False
        self._next_job[name] = now + every_s
        return True

    def _flow_jobs(self) -> None:
        """곁일: ws-gateway 연결 사건(HIRO 리셋)·hiro 행·대량 체결 행·그 거래일 대량 체결 기준·
        투자자별 순매수·선물 지표. 하나가 실패해도 나머지는 한다."""
        jobs: tuple[tuple[str, float, Callable[[], None]], ...] = (
            ("ws_events", WS_EVENTS_EVERY_S, self._ws_events),
            (HIRO, HIRO_EVERY_S, self._hiro_rows),
            (BLOCK_TRADE, POLL_S, self._block_rows),
            (BLOCK_STATUS, POLL_S, self._block_thresholds),
            (INVESTOR_FLOW, INVESTOR_EVERY_S, self._investor_rows),
            (FUTURES_FLAG, FUTURES_EVERY_S, self._futures_rows),
        )
        for name, every, job in jobs:
            if not self._due_job(name, every):
                continue
            try:
                job()
            except Exception as e:
                self._flow_failed(name, e, None)

    def _ws_events(self) -> None:
        """ws-gateway 연결 사건(끊김·재연결·연결 실패)이 새로 났으면 HIRO 리셋(`ws_disconnect`) —
        그 사건 뒤에 이미 순번 공백(재연결 뒤 첫 틱)으로 리셋했으면 사유만
        (`TickFlow.disconnected`)."""
        now = self._now()
        _, events = self.reader.ws_connection_events(self._ws_seen, now, WS_EVENTS)
        new = [(ts, kind) for ts, kind in events if ts > self._ws_seen]
        if not new:
            return
        self._ws_seen = max(ts for ts, _ in new)
        done = self.flow.disconnected(self._ws_seen, now)
        if done is not None:
            st = self.flow.hiro
            log_event(
                log,
                logging.INFO,
                SERVICE,
                "hiro_reset",
                (st.trade_date, st.session) if st.trade_date else None,
                reason="ws_disconnect",
                action=done,
                reset_at=None if st.reset_at is None else st.reset_at.isoformat(),
                events=[k for _, k in new],
            )

    def _hiro_rows(self) -> None:
        """바뀐 HIRO-lite 상태(리셋·세션 전환 직전 상태 포함) → hiro 행(세션마다 묶어). off 면
        틱이 오지 않아도 누적을 버린다 — 다시 켰을 때 끄기 전 상태를 새 플래그로 내지 않게."""
        flag = resolve_flag(HIRO, self.flags)
        if flag == "off":
            self.flow.stop_hiro()
            return
        rows = self.flow.rows(flag)
        self.stats.hiro_rows += len(rows)
        self._metric_rows_by_tag(rows)

    def _metric_rows_by_tag(self, rows: Sequence[MetricRecord]) -> None:
        for tag in sorted({(r.trade_date, r.session) for r in rows}):
            self._metric_rows([r for r in rows if (r.trade_date, r.session) == tag], tag)

    def _block_rows(self) -> None:
        flag = resolve_flag(BLOCK_STATUS, self.flags)
        rows = self.flow.drain_blocks(flag) if flag != "off" else []
        self.stats.block_trades += len(rows)
        self._metric_rows_by_tag(rows)

    def _block_thresholds(self) -> None:
        """지금 세션 거래일의 대량 체결 기준을 한 번 — opt_ticks 20거래일(그 전엔 비활성). 실패하면
        FLOW_RETRY_S 뒤 다시."""
        flag = resolve_flag(BLOCK_STATUS, self.flags)
        tag = self._catchup_tag()
        if flag == "off" or tag is None or self.flow.thresholds_for == tag[0]:
            return
        d = tag[0]
        try:
            th = self._load_thresholds(d)
        except Exception:
            self._next_job[BLOCK_STATUS] = self._mono() + FLOW_RETRY_S
            raise
        self.flow.thresholds, self.flow.thresholds_for = th, d
        row = block_status(th, self._now(), d, tag[1], flag, BLOCK_DAYS)
        log_event(
            log, logging.INFO, SERVICE, "block_thresholds", tag, active=th.active, days=len(th.days)
        )
        self._metric_rows([row], tag)

    def _investor_rows(self) -> None:
        """지금 세션 투자자별 새 행 → `investor_flow` 지표 행(§6.2 — 그대로)."""
        flag = resolve_flag(INVESTOR_FLOW, self.flags)
        tag = self._catchup_tag()
        if flag == "off" or tag is None:
            return
        records = self.reader.investor_latest(tag[0], tag[1], self._now())
        rows = self.investors.rows(tag, records, flag)
        self.stats.investor_rows += len(rows)
        self._metric_rows_by_tag(rows)

    def _futures_rows(self) -> None:
        """새로 녹화된 분봉 원문 output1 → 선물 지표 행(§7)과 교차검증 health. 태그가 없거나 검증에
        실패한 응답은 그것만 건너뛴다(센다)."""
        flag = resolve_flag(FUTURES_FLAG, self.flags)
        if flag == "off":
            return
        now = self._now()
        outs = self.reader.kis_rest_outputs(FUTURES_TR, self._fut_seen, now)
        rows: list[MetricRecord] = []
        for ts, td, ss, out in outs:
            self._fut_seen = max(self._fut_seen, ts)
            if td is None or ss not in ("day", "night"):
                self.stats.bad_futures += 1
                log_event(log, logging.WARNING, SERVICE, "futures_untagged", ts=ts.isoformat())
                continue
            tag: Tag = (td, ss)
            try:
                fm = futures_metrics(quote_of(out))
            except (ValidationError, ValueError, TypeError) as e:
                self.stats.bad_futures += 1
                log_event(
                    log, logging.WARNING, SERVICE, "futures_invalid", tag, error=type(e).__name__
                )
                continue
            rows += futures_rows(ts, td, ss, fm, flag)
            if (h := basis_health(fm)) is not None:
                self._health(h, tag)
        self.stats.futures_rows += len(rows)
        self._metric_rows_by_tag(rows)

    def _load_thresholds(self, d: date) -> BlockThresholds:
        days = self.reader.opt_tick_days(d, BLOCK_DAYS)
        if len(days) < BLOCK_DAYS:
            return BlockThresholds(tuple(days), False, {}, 0)
        hist = self.reader.opt_tick_history(days[0], days[-1], BLOCK_F_LOOKBACK)
        return block_thresholds(
            (day, cp, moneyness_bucket(k, f), q) for day, cp, k, q, f in hist if f > 0
        )

    def _metric_rows(self, rows: Sequence[MetricRecord], tag: Tag) -> None:
        """사이클 밖 지표 행(한 세션) — 저장하고 visible 이면 `engine.metrics` 로(as_of = 가장 늦은
        행)."""
        self._write_one("metrics", lambda: self.sink.write_metrics(rows), len(rows), tag)
        visible = [r for r in rows if r.flag == "visible"]
        if visible:
            as_of = max(r.ts for r in visible)
            self.publisher.publish_metrics(tag[0], cast(SessionName, tag[1]), as_of, visible)

    # ── 일별 지표 (POST_DAY) ──

    def _daily_tick(self) -> None:
        """POST_DAY 면 그 거래일 일별 지표와 딜러 가정 점검을 한 번씩 — 따로(하나가 실패해도 다른
        것은 한다), 실패하면 그것만 DAILY_RETRY_S 뒤 다시."""
        now = self._now()
        if state_at(now, self.cal).state is not State.POST_DAY:
            return
        d = now.astimezone(KST).date()
        if d not in self._daily_done and self._mono() >= self._daily_retry_at:
            self.run_daily(d)
        if d not in self._dealer_done and self._mono() >= self._dealer_retry_at:
            self.run_dealer(d)

    def run_dealer(self, trade_date: date) -> MetricRecord | None:
        """거래일 하나의 딜러 가정 점검(§6.3) — 그날 주간 증권 계정 콜·풋 순매수, 앞 거래일 이
        지표 행으로 연속 불일치. 예외를 올리지 않는다(실패하면 1분 뒤 다시)."""
        tag: Tag = (trade_date, "day")
        flag = resolve_flag(DEALER_CHECK, self.flags)
        if flag == "off":
            self._dealer_done.add(trade_date)
            return None
        try:
            days: list[date] = []
            d = trade_date
            for _ in range(DEALER_LOOKBACK_DAYS):
                d = self.cal.prev_trading_day(d)
                days.append(d)
            days.reverse()
            records = self.reader.investor_latest(trade_date, "day", self._now())
            history = self.reader.metric_history(DEALER_CHECK, "all", "", days[0], days[-1])
            row = dealer_record(trade_date, daily_ts(trade_date), records, history, days, flag)
        except Exception as e:  # 읽기·계산 — 1분 뒤 다시
            self._dealer_retry_at = self._mono() + DAILY_RETRY_S
            err = type(e).__name__
            self._health(
                EngineHealth(
                    "engine_daily_failed",
                    "warning",
                    f"딜러 가정 점검 실패({trade_date.isoformat()}): {err}: {e}"[:250],
                    subject=f"{trade_date.isoformat()}/{DEALER_CHECK}/{err}",
                ),
                tag,
            )
            return None
        recent = trade_date - timedelta(days=7)
        self._dealer_done = {x for x in self._dealer_done if x > recent} | {trade_date}
        self._metric_rows([row], tag)
        p = row.payload
        log_event(
            log,
            logging.WARNING if p["warning"] else logging.INFO,
            SERVICE,
            DEALER_CHECK,
            tag,
            consistent=p["consistent"],
            streak=p["mismatch_streak"],
            warning=p["warning"],
            quality=row.quality,
        )
        return row

    def run_daily(self, trade_date: date) -> DailyResult | None:
        """거래일 하나의 일별 지표 — 읽기 → 계산 → 저장·발행(visible). 예외를 올리지 않는다."""
        tag: Tag = (trade_date, "day")
        try:
            result = evaluate_daily(self._daily_inputs(trade_date), cal=self.cal, flags=self.flags)
        except Exception as e:  # 읽기·계산 통째로 — 1분 뒤 다시
            self._daily_retry_at = self._mono() + DAILY_RETRY_S
            err = type(e).__name__
            self._health(
                EngineHealth(
                    "engine_daily_failed",
                    "warning",
                    f"일별 지표 실패({trade_date.isoformat()}): {err}: {e}"[:250],
                    subject=f"{trade_date.isoformat()}/{err}",
                ),
                tag,
            )
            return None
        recent = trade_date - timedelta(days=7)
        self._daily_done = {d for d in self._daily_done if d > recent} | {trade_date}
        for h in result.health:
            self._health(h, tag)
        self._write_one(
            "metrics", lambda: self.sink.write_metrics(result.metrics), len(result.metrics), tag
        )
        visible = [m for m in result.metrics if m.flag == "visible" and m.trade_date == trade_date]
        if visible:
            self.publisher.publish_metrics(trade_date, "day", result.ts, visible)
        self.stats.dailies += 1
        log_event(
            log,
            logging.INFO,
            SERVICE,
            "daily",
            tag,
            rows=len(result.metrics),
            today=None if result.today is None else result.today.value,
        )
        return result

    def _daily_inputs(self, trade_date: date) -> DailyInput:
        """일별 지표 입력 — 오늘 포함 252거래일 창의 일별 이력, 값이 정해지지 않은 날
        (`daily_settled` — 행이 없거나 자체 값이 invalid)의 KRX 일별, 선물 정산가."""
        cal, r = self.cal, self.reader
        start = window_start(trade_date, RANK_WINDOW, cal)
        prev = cal.prev_trading_day(trade_date)
        close = r.metric_last(trade_date, "day", "atm_iv", "series", "M:")
        history = r.metric_history(DAILY_ATM, "all", "", start, prev)
        # 값이 정해진 날만 — 자체 값이 invalid 였던 날은 KRX 가 그날 것을 내면 백필(검토 E4)
        have = {h.trade_date for h in history if daily_settled(h)}
        kis = {e: d for c, e, d, _src in r.expiry_dates(start) if c == ""}
        prices = {(d, c): p for d, c, p in r.krx_futures_settles(start, trade_date) if p > 0}
        last_trade = {
            c: last
            for c in sorted({c for _, c in prices})
            if (last := monthly_last_trade(c, kis, cal)) is not None
        }
        settles = [
            Settlement(d, c, float(p)) for (d, c), p in sorted(prices.items()) if c in last_trade
        ]
        krx_days: list[KrxDay] = []
        for d in r.krx_option_days(start, prev):
            if d in have:
                continue
            expiry = nearest_monthly(d, kis, cal)
            rows = krx_rows(r.krx_iv_rows(d, expiry))
            krx_days.append(KrxDay(d, expiry, rows, near_settle(d, prices, last_trade)))
        return DailyInput(trade_date, close, history, krx_days, settles, last_trade, kis)

    def on_message(self, msg: Mapping[str, Any]) -> CycleResult | None:
        """구독 메시지 하나 — 체결 틱(`ticks.opt`·`ticks.fut`)은 플로우로, 그 밖은 `chain.ready`
        (형식이 틀리면 로그만)."""
        if msg.get("type") != "message":
            return None
        channel = _channel(msg)
        if channel in (TICKS_OPT, TICKS_FUT):
            self.on_tick(channel, msg.get("data"))
            return None
        try:
            ready = ChainReady.model_validate_json(msg["data"])
        except (ValidationError, ValueError, KeyError) as e:
            self.stats.bad_ready += 1
            log_event(log, logging.WARNING, SERVICE, "bad_ready", error=type(e).__name__)
            return None
        return self.on_ready(ready)

    def run(self, stop: threading.Event, redis: Callable[[], Redis]) -> None:
        """stop 까지: chain.ready 를 기다리며(1초) 사이클, 사이사이 곁일. Redis 가 끊기면 백오프 뒤
        다시 구독한다(그동안도 따라잡기·하트비트는 돈다 — DB 로 산출은 계속)."""
        log_event(log, logging.INFO, SERVICE, "started", basis=len(self.basis.entries))
        back = Backoff(1.0, 30.0)
        pubsub: Any = None
        while not stop.is_set():
            try:
                if pubsub is None:
                    pubsub = redis().pubsub(ignore_subscribe_messages=True)
                    pubsub.subscribe(CHAIN_READY, TICKS_OPT, TICKS_FUT)
                    back.reset()
                msg = pubsub.get_message(timeout=POLL_S)
                if msg is not None:
                    self.on_message(msg)
                    # 틱이 쏟아지는 동안 곁일은 POLL_S 에 한 번 — 알림·빈 대기 뒤엔 늘
                    if _channel(msg) in (TICKS_OPT, TICKS_FUT) and self._mono() < self._next_side:
                        continue
                self._next_side = self._mono() + POLL_S
            except RedisError as e:
                _close(pubsub)
                pubsub = None
                delay = back.next()
                err = type(e).__name__
                log_event(
                    log, logging.WARNING, SERVICE, "subscribe_failed", error=err, retry_s=delay
                )
                self.tick()
                stop.wait(delay)
                continue
            self.tick()
        _close(pubsub)
        log_event(log, logging.INFO, SERVICE, "stopped", cycles=self.stats.cycles)


def _channel(msg: Mapping[str, Any]) -> str | None:
    ch = msg.get("channel")
    if isinstance(ch, bytes):
        return ch.decode("utf-8", "replace")
    return ch if isinstance(ch, str) else None


def _close(pubsub: Any) -> None:
    if pubsub is None:
        return
    try:
        pubsub.close()
    except (RedisError, OSError):
        pass


def main() -> int:  # pragma: no cover — compose 진입점 (.env·실제 Redis·DB)
    from config.settings import Settings
    from data.store import PostgresSink
    from services.runtime import (
        ServiceHealthSink,
        connect_redis,
        install_stop,
        setup_logging,
        tagger_for,
    )

    setup_logging()
    settings = Settings()
    if not settings.redis_url:
        log_event(log, logging.ERROR, SERVICE, "config_error", error="REDIS_URL 이 없다")
        return 2
    try:  # 모르는 이름·값이면 기동하지 않는다(설계 §4)
        features = FeatureWatcher(mono=time.monotonic)
    except FeatureError as e:
        log_event(log, logging.ERROR, SERVICE, "config_error", error=str(e)[:300])
        return 2
    log_event(log, logging.INFO, SERVICE, "flags_loaded", flags=features.features.resolved())
    stop = threading.Event()
    install_stop(stop)
    cal = TradingCalendar.default()
    tagger = tagger_for(cal)
    url = settings.redis_url
    redis = connect_redis(url)
    store = PostgresSink.from_settings(service=SERVICE, spool=True, tagger=tagger)
    try:
        svc = EngineService(
            store,
            store,
            EnginePublisher(redis),
            EngineContext(redis, cal),
            cal,
            health=ServiceHealthSink(store, tagger),
            heartbeat=Heartbeater(redis, SERVICE),
            flush_spool=store.flush_spool,
            features=features,
        )
        svc.run(stop, lambda: connect_redis(url))
    finally:
        store.close()
    return 0

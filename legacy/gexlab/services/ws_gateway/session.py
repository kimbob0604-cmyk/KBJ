"""ws-gateway 구독 컨트롤러 — 세션 전환·만기 전환·ATM 회전 (PLAN §4.3·§4.6, 설계 §4·§6).

`step(now)` 이 원하는 구독 집합을 정해 세션 클라이언트(`KisWsClient.set_desired`)에 넘긴다.
등록·해제 순서와 예산 안 보내기는 클라이언트 몫이다.

- 세션: `core.calendar.state_at` — DAY·PRE_DAY 는 주간 TR, NIGHT·PRE_NIGHT 는 야간 TR(PLAN §4.6
  `PRE_NIGHT` 야간 구독 전환). POST_DAY·IDLE 은 구독 없음(전부 해지)
- 마감 여유(`grace_s`, 기본 클라이언트 `late_grace_s` 60초): 세션이 끝난 뒤(15:45·06:00)와 만기로
  시리즈·선물이 바뀐 뒤(15:20) 그만큼 이전 구독을 둔다(reason "grace"). 마감 시각의 체결(15:45:00
  종가 단일가 등)은 그 시각 직후에 오는데, 곧바로 해지하면(선물이 먼저 나간다) 놓친다
- 대상 만기: 최근접(0DTE 포함). 체인 원천(`ChainSource`)이 시각마다 준다 — 만기일 15:20 이 지나면
  차기 만기가 온다(`services.poller.context.select_targets`, 설계 §4). 선물도 최종거래일 15:20 이
  지난 종목(분기 만기일의 근월물)은 건너뛴다
- ATM: 구독하는 선물 종목의 최근 체결가(주입 — 보통 ws-gateway 가 받은 선물 체결,
  `LatestFuturesPrice`)에 가장 가까운 행사가(`core.chain.atm_strike`, 전광판 ATM 표시는 쓰지 않는다
  #11a). 다른 종목의 가격은 쓰지 않는다. 가격이 아직 없으면 선물만 구독하고, 가격이 생기면 바로
  옵션을 붙인다
- 회전: `rotate_every_s`(60초)마다 ATM 을 다시 보고 `should_rotate`(2행사가 이상)일 때만.
  세션·만기·선물 종목이 바뀌면 바로 다시 짠다
- 예산: `budget.derive(설정, 세션)` → `subscriptions.plan()`
- 닫힌 세션 확인(`watch` — ws_gateway/watch.py `ClosedWatch`, 설계 §6 이중 확인): 구독 세션이
  없는(POST_DAY·IDLE) 시각이 캘린더상 닫힌 날·밤의 개장 창(개장 − 60초 ~ 개장 + 10분)이면 그
  세션의 선물 체결 TR 로 최근접 체인의 선물 근월물 1건만 구독한다(reason "watch"). 체인이 없으면
  health `ws_closed_watch_unavailable` 을 남기고 구독하지 않는다. 창 안 step 마다 그 구독이 실제로
  걸려 있는지(`active` — 클라이언트가 연결 중이고 서버 쪽 구독에 있다)를 `ClosedWatch.armed` 로
  알린다 — 걸려 있지 않았던 창은 '닫힘 확인'이 아니다. `active` 가 없으면 늘 걸려 있지 않다고 본다
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol

from core.calendar import Session, State, TradingCalendar, state_at
from core.chain import atm_strike
from data.kis.auth_client import utcnow
from data.kis.ws import FuturesTick
from services.auth.health import HealthEvent, HealthSink, Severity
from services.poller.context import ChainContext
from services.ws_gateway.budget import Budget, BudgetConfig, derive, load
from services.ws_gateway.client import SERVICE, TickEvent, load_config
from services.ws_gateway.subscriptions import (
    TR_IDS,
    StrikeCodes,
    Subscription,
    plan,
    should_rotate,
)
from services.ws_gateway.watch import ClosedWatch

ROTATE_EVERY_S = 60.0  # PLAN §4.3: ATM 재계산 1분마다

Reason = Literal[
    "closed",
    "session",
    "series",
    "futures",
    "price",
    "rotate",
    "hold",
    "grace",
    "no_chain",
    "watch",
]

log = logging.getLogger(__name__)


class DesiredTarget(Protocol):
    def set_desired(self, desired: frozenset[Subscription], budget: Budget) -> None: ...


@dataclass(frozen=True)
class ChainView:
    """최근접 만기 한 시리즈의 행사가별 콜·풋 코드(오름차순)와 구독할 선물 근월물 코드."""

    series: str  # 예: 'WKM:260904'
    futures_code: str
    strikes: tuple[StrikeCodes, ...]


class ChainSource(Protocol):
    def nearest(self, now: datetime) -> ChainView | None: ...


PriceSource = Callable[[str], Decimal | None]  # 선물 종목코드 → 그 종목의 최근 체결가 (없으면 None)
# 지금 서버에 걸려 있는 구독 (연결이 끊겼으면 빈 집합 — ws-gateway 는 KisWsClient.status())
ActiveSource = Callable[[], frozenset[Subscription]]


def subscription_session(now: datetime, cal: TradingCalendar) -> Session | None:
    """이 시각에 걸어 둘 구독의 세션. 장 전 준비 상태는 곧 열릴 세션, 장 뒤·휴장은 None."""
    st = state_at(now, cal).state
    if st in (State.DAY, State.PRE_DAY):
        return "day"
    if st in (State.NIGHT, State.PRE_NIGHT):
        return "night"
    return None


@dataclass(frozen=True)
class Decision:
    at: datetime
    reason: Reason
    session: Session | None
    series: str | None
    center: Decimal | None  # 옵션 구독 중심 행사가 (선물만이면 None)
    desired: frozenset[Subscription]
    budget: Budget


class ContextChainSource:
    """poller 와 같은 `ChainContext`(마스터·월물리스트·최종거래일)에서 최근접 만기 체인을 만든다.

    선물 코드는 선물 전광판 근월물부터(없으면 마스터 선물 월물 순 — `all_futures_codes`) 최종거래일
    15:20 KST 가 지나지 않은 첫 종목. 선물 최종거래일은 마스터 이름의 결제월(YYYYMM)과 같은 달 월물
    옵션의 최종거래일(월물리스트·KIS `futs_last_tr_date`)이고, 없으면 캘린더 계산(둘째 목요일)이다.
    마스터에 코스피200 선물이 있는데 없는 종목(만기 지나 새 마스터에서 빠진 근월물)은 건너뛰고,
    마스터에 선물이 하나도 없을 때만 모르는 종목을 살아 있다고 본다.
    콜·풋 코드가 둘 다 있는 행사가만 쓴다.
    """

    def __init__(self, ctx: ChainContext, calendar: TradingCalendar | None = None) -> None:
        self._ctx = ctx
        self._cal = calendar if calendar is not None else TradingCalendar.default()

    def nearest(self, now: datetime) -> ChainView | None:
        t = self._ctx.targets(now)
        if t is None:
            return None
        chain = self._ctx.chain(t.nearest)
        futures = self.futures_code(now)
        if chain is None or futures is None:
            return None
        rows: list[StrikeCodes] = []
        for k in chain.strikes:
            call, put = chain.code(k, "C"), chain.code(k, "P")
            if call is not None and put is not None:
                rows.append(StrikeCodes(k, call, put))
        return ChainView(t.nearest.label, futures, tuple(rows)) if rows else None

    def futures_code(self, now: datetime) -> str | None:
        """구독할 선물 근월물 — 최종거래일 15:20 이 지난 종목(분기 만기일의 근월물)은 건너뛴다
        (`ChainContext.live_futures_codes` — poller 와 같은 규칙)."""
        live = self._ctx.live_futures_codes(now, self._cal)
        return live[0] if live else None


class LatestFuturesPrice:
    """ATM 기준가 — ws-gateway 가 받은 선물 체결의 마지막 가격. `observe` 를 tick 콜백에 끼운다.

    종목코드로 묻는다: 마지막 체결이 다른 종목이면(분기 만기로 근월물이 바뀐 직후 등) None.
    """

    def __init__(self) -> None:
        self.price: Decimal | None = None
        self.code: str | None = None

    def observe(self, ev: TickEvent) -> None:
        if isinstance(ev.tick, FuturesTick):
            self.price, self.code = ev.tick.price, ev.tick.code

    def __call__(self, code: str) -> Decimal | None:
        return self.price if code == self.code else None


class SubscriptionController:
    def __init__(
        self,
        target: DesiredTarget,
        chain: ChainSource,
        price: PriceSource,
        *,
        health: HealthSink,
        calendar: TradingCalendar | None = None,
        budget_config: BudgetConfig | None = None,
        rotate_every_s: float = ROTATE_EVERY_S,
        grace_s: float | None = None,
        watch: ClosedWatch | None = None,
        active: ActiveSource | None = None,
    ) -> None:
        if rotate_every_s <= 0:
            raise ValueError("rotate_every_s 는 0보다 커야 한다")
        grace = grace_s if grace_s is not None else load_config().client.late_grace_s
        if grace < 0:
            raise ValueError("grace_s 는 0 이상이어야 한다")
        self._target = target
        self._chain = chain
        self._price = price
        self._health = health
        self._cal = calendar if calendar is not None else TradingCalendar.default()
        self._cfg = budget_config if budget_config is not None else load()
        self._every = rotate_every_s
        self._grace = timedelta(seconds=grace)
        self._session: Session | None = None
        self._series: str | None = None
        self._futures: str | None = None
        self._center: Decimal | None = None
        self._checked_at: datetime | None = None
        self._desired: frozenset[Subscription] = frozenset()
        self._budget: Budget = derive(self._cfg, "day")
        self._last_emit: dict[str, datetime] = {}
        self._watch = watch
        self._active = active

    @property
    def desired(self) -> frozenset[Subscription]:
        return self._desired

    def step(self, now: datetime) -> Decision:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("naive datetime 금지")
        if self._watch is not None:
            self._watch.step(now)
        session = subscription_session(now, self._cal)
        if session is None:
            if self._closing(now):
                return self._hold(now, "grace")  # 마감 시각의 체결을 받고 해지한다
            watched = self._watch_plan(now)
            if watched is not None:
                return watched
            return self._set(now, "closed", None, None, None, frozenset(), self._budget)
        budget = derive(self._cfg, session)
        view = self._chain.nearest(now)
        if view is None or not view.strikes:
            self._emit(now, "ws_chain_missing", "최근접 만기 체인·선물 코드가 없다", "warning")
            if session != self._session:  # 새 세션 TR 을 짤 수 없다 — 이전 세션 구독은 내린다
                return self._set(now, "no_chain", session, None, None, frozenset(), budget)
            return self._hold(now, "no_chain")
        if session == self._session and self._expiring(now, view):
            return self._hold(now, "grace")  # 만기 시리즈·선물의 15:20 마감 체결을 받고 바꾼다
        reason: Reason | None = None
        if session != self._session:
            reason = "session"
        elif view.series != self._series:
            reason = "series"
        elif view.futures_code != self._futures:
            reason = "futures"
        # 구독하는 종목의 가격만 — 옛 근월물(분기 만기) 가격으로 새 체인의 ATM 을 잡지 않는다
        price = self._price(view.futures_code)
        if price is not None and price <= 0:
            price = None
        strikes = [sc.strike for sc in view.strikes]
        if price is None:
            if reason is None:
                return self._hold(now, "hold")  # 가격이 사라져도 지금 구독을 둔다
            tr = TR_IDS[session]["futures"]
            futures_only = (
                frozenset({Subscription(tr, view.futures_code)}) if budget.futures else frozenset()
            )
            return self._set(now, reason, session, view, None, futures_only, budget)
        center = atm_strike(strikes, price)
        if reason is None and self._center is None:
            reason = "price"  # 처음으로 가격이 생겼다
        if reason is None:
            if self._checked_at is not None and (now - self._checked_at).total_seconds() < (
                self._every
            ):
                return self._hold(now, "hold")
            self._checked_at = now
            if not should_rotate(self._center, center, view.strikes):
                return self._hold(now, "hold")
            reason = "rotate"
        desired = plan(budget, view.futures_code, view.strikes, strikes.index(center))
        return self._set(now, reason, session, view, center, desired, budget)

    def _watch_plan(self, now: datetime) -> Decision | None:
        """캘린더상 닫힌 세션의 개장 창이면 그 세션 선물 체결 TR 로 선물 근월물 1건. 그 구독이
        지금 걸려 있는지를 watch 에 표본으로 남긴다(못 걸었으면 거짓)."""
        watch = self._watch
        w = watch.window(now) if watch is not None else None
        if watch is None or w is None:
            return None
        view = self._chain.nearest(now)
        if view is None:
            self._emit(
                now,
                "ws_closed_watch_unavailable",
                f"캘린더상 닫힌 {w.label()} 확인 구독을 못 한다 — 선물 근월물 코드(체인)가 없다",
                "warning",
            )
            watch.armed(now, False)
            return None
        budget = derive(self._cfg, w.session)
        sub = Subscription(w.tr_id, view.futures_code)
        decision = self._set(now, "watch", None, view, None, frozenset({sub}), budget)
        watch.armed(now, self._is_active(sub))
        return decision

    def _is_active(self, sub: Subscription) -> bool:
        if self._active is None:
            return False
        try:
            return sub in self._active()
        except Exception as e:  # 모르면 걸려 있지 않다고 본다(확인이 아니다)
            log.error("ws-gateway 구독 상태 읽기 실패: %s", type(e).__name__)
            return False

    def _closing(self, now: datetime) -> bool:
        """세션이 막 끝났다 — grace 전에는 지금 구독한 세션이었다."""
        return (
            self._grace > timedelta(0)
            and self._session is not None
            and subscription_session(now - self._grace, self._cal) == self._session
        )

    def _expiring(self, now: datetime, view: ChainView) -> bool:
        """만기로 시리즈·선물이 막 바뀌었다 — grace 전 시각의 체인이 지금 구독한 것이다.
        (문맥이 바뀌어 달라진 것이면 grace 전 체인도 새것이라 곧바로 바꾼다.)"""
        current = (self._series, self._futures)
        if self._grace <= timedelta(0) or self._series is None:
            return False
        if (view.series, view.futures_code) == current:
            return False
        before = self._chain.nearest(now - self._grace)
        return before is not None and (before.series, before.futures_code) == current

    def _hold(self, now: datetime, reason: Reason) -> Decision:
        return Decision(
            now, reason, self._session, self._series, self._center, self._desired, self._budget
        )

    def _set(
        self,
        now: datetime,
        reason: Reason,
        session: Session | None,
        view: ChainView | None,
        center: Decimal | None,
        desired: frozenset[Subscription],
        budget: Budget,
    ) -> Decision:
        old_session, old_series, old_center = self._session, self._series, self._center
        self._session = session
        self._series = view.series if view is not None else None
        self._futures = view.futures_code if view is not None else None
        self._center = center
        if center is not None:
            self._checked_at = now
        changed = desired != self._desired or budget != self._budget
        self._desired, self._budget = desired, budget
        self._target.set_desired(desired, budget)
        if changed:
            if reason == "rotate":
                detail = f"ATM {old_center} → {center} ({self._series})"
            elif reason == "watch":
                detail = f"캘린더상 닫힌 세션 개장 확인 — {budget.session} 선물 체결"
            elif reason in ("session", "closed", "no_chain"):
                detail = f"세션 {old_session or '-'} → {session or '-'}"
            else:
                detail = f"{reason}: 만기 {old_series or '-'} → {self._series or '-'}"
            detail += f", 구독 {len(desired)}건 (ATM {center if center is not None else '-'})"
            self._emit(now, f"ws_plan_{reason}", detail, "info", every=None)
        return Decision(now, reason, session, self._series, center, desired, budget)

    def _emit(
        self, now: datetime, kind: str, detail: str, severity: Severity, every: float | None = 60
    ) -> None:
        last = self._last_emit.get(kind)
        if every is not None and last is not None and (now - last).total_seconds() < every:
            return
        self._last_emit[kind] = now
        try:
            self._health.emit(HealthEvent(kind, detail, now, severity, service=SERVICE))
        except Exception as e:
            log.error("ws-gateway health 싱크 실패: %s", type(e).__name__)

    async def run(
        self,
        stop: asyncio.Event,
        *,
        now: Callable[[], datetime] = utcnow,
        tick_s: float = 1.0,
    ) -> None:
        """stop 까지 tick_s 마다 step. step 의 예외는 health 로 남기고 계속 돈다."""
        while not stop.is_set():
            at = now()
            try:
                self.step(at)
            except Exception as e:
                self._emit(at, "ws_controller_error", f"{type(e).__name__}: {e}"[:200], "warning")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), tick_s)

"""scheduler 개장 이중 확인 — 캘린더상 열린 세션인데 선물 체결이 없으면 (docs/phase1_design.md §6).

PLAN §4.6 '상태 전이는 캘린더 + 실제 수신 데이터로 이중 확인'. 캘린더상 닫힌 세션의 반대쪽 확인
(예상 밖 개장)은 ws-gateway 몫이다(services/ws_gateway/watch.py).

- 캘린더상 열리는 세션(주간 08:45, 그날 밤이 열리면 야간 18:00)마다 한 번: 개장 뒤 `window_s`
  (3분 — 설계 §6: 주간 08:48, 야간 18:03)에 `settle_s` 를 더한 때부터 fut_ticks 에서 체결 시각이
  [개장, 개장 + 3분) 인 웹소켓 선물 체결을 센다. 0건이면 health warning `calendar_mismatch` +
  session_log `calendar_mismatch`(그 세션의 귀속 거래일·세션, ts = 개장 + 3분 — 다시 판정해도 한
  행). 있으면 구조화 로그 `open_check_ok` 만
- `settle_s`(15초 **[확인 필요]**): ws-gateway 가 체결을 0.5초 묶음으로 쓰고 DB 가 잠깐 밀릴 수 있어
  3분 끝의 체결까지 들어오길 기다린다
- 늦게 기동해도(세션 중 재기동) 그 세션이 끝나기 전이면 한 번 본다. 세는 구간은 늘 [개장, 개장 +
  3분)이다 — 늦게 온 첫 체결은 캘린더 불일치가 아니라 수집 공백이다(무결측 판정, services/gaps.py)
- 읽기는 작업 스레드에서(상태 루프를 막지 않게), 스풀 없는 저장소로. 실패하면 warning
  `open_check_failed`(5분에 한 번) + `retry_s` 뒤 다시, 세션이 끝나면 그만
- 체결 0건은 캘린더가 틀렸거나(임시 휴장) 수집이 멈춘 것이다 — 어느 쪽인지 여기서는 가리지 않는다.
  개장 시각이 늦는 날(수능일·연초 첫 거래일 10:00)은 캘린더에 시각 오버라이드가 아직 없어 거짓
  경고가 난다(설계 §6·§12)
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from core.calendar import KST, TradingCalendar, night_session_opens, session_bounds
from data.store import SessionLogRecord
from services.auth.health import HealthEvent, HealthSink, Severity
from services.runtime import log_event, run_in_thread

SERVICE = "scheduler"
Session = Literal["day", "night"]

log = logging.getLogger("services.scheduler.open_check")


class OpenCheckConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    window_s: float = Field(default=180.0, gt=0)  # 설계 §6: 개장 뒤 3분
    settle_s: float = Field(default=15.0, ge=0)  # 확인 필요: 체결 묶음·DB 쓰기 여유
    retry_s: float = Field(default=30.0, gt=0)
    fail_every_s: float = Field(default=300.0, ge=0)  # open_check_failed 를 이만큼에 한 번


class OpenCountStore(Protocol):
    def fut_tick_count(self, start: datetime, end: datetime) -> int: ...


class SessionLogStore(Protocol):
    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None: ...


class Submit(Protocol):
    def __call__(self, fn: Callable[[], int], /) -> Future[int]: ...


def open_check_submit(fn: Callable[[], int], /) -> Future[int]:
    return run_in_thread(fn, name="open-check")


@dataclass(frozen=True)
class OpenSession:
    """캘린더상 열리는 세션 하나."""

    session: Session
    start_date: date  # 세션이 시작하는 달력일
    trade_date: date  # 귀속 거래일 (야간은 next_trading_day)
    opens_at: datetime  # KST
    ends_at: datetime

    @property
    def key(self) -> tuple[Session, date]:
        return self.session, self.start_date

    def label(self) -> str:
        return f"{'주간' if self.session == 'day' else '야간'} {self.opens_at:%m-%d %H:%M}"


def open_sessions(d: date, cal: TradingCalendar) -> list[OpenSession]:
    """달력일 d 에 시작하는, 캘린더상 열리는 세션 (주간·야간)."""
    out: list[OpenSession] = []
    if cal.is_trading_day(d):
        a, b = session_bounds(d, "day")
        out.append(OpenSession("day", d, d, a, b))
    if night_session_opens(d, cal):
        a, b = session_bounds(d, "night")
        out.append(OpenSession("night", d, cal.next_trading_day(d), a, b))
    return out


class OpenCheck:
    """캘린더상 열린 세션의 개장 3분 체결 확인 — scheduler 가 1초마다 `step(now)`."""

    def __init__(
        self,
        counts: OpenCountStore,
        log_store: SessionLogStore,
        calendar: TradingCalendar,
        health: HealthSink,
        *,
        config: OpenCheckConfig | None = None,
        submit: Submit = open_check_submit,
    ) -> None:
        self._counts = counts
        self._log_store = log_store
        self._cal = calendar
        self._health = health
        self.cfg = config or OpenCheckConfig()
        self._submit = submit
        self.done: set[tuple[Session, date]] = set()
        self.results: dict[tuple[Session, date], int] = {}  # 세션별 센 체결 수 (시험·진단)
        self._next_try: dict[tuple[Session, date], datetime] = {}
        self._inflight: tuple[OpenSession, Future[int]] | None = None
        self._failed_at: datetime | None = None

    def step(self, now: datetime) -> None:
        self._collect(now)
        if self._inflight is not None:
            return
        s = self._due(now)
        if s is None:
            return
        window = timedelta(seconds=self.cfg.window_s)
        counts = self._counts
        try:
            fut = self._submit(lambda: counts.fut_tick_count(s.opens_at, s.opens_at + window))
        except Exception as e:  # 스레드를 못 띄웠다 — 실패처럼 다시
            self._failed(now, s, f"시작 실패: {type(e).__name__}")
            return
        self._inflight = (s, fut)
        self._collect(now)  # 그 자리에서 끝났으면(시험의 submit) 이 step 에 적용

    def _due(self, now: datetime) -> OpenSession | None:
        """확인할 때가 됐고 아직 안 본 세션 — 그 세션이 끝나기 전까지."""
        k = now.astimezone(KST)
        wait = timedelta(seconds=self.cfg.window_s + self.cfg.settle_s)
        for d in (k.date() - timedelta(days=1), k.date()):
            for s in open_sessions(d, self._cal):
                if s.key in self.done or not (s.opens_at + wait <= now < s.ends_at):
                    continue
                nxt = self._next_try.get(s.key)
                if nxt is None or now >= nxt:
                    return s
        return None

    def _collect(self, now: datetime) -> None:
        if self._inflight is None:
            return
        s, fut = self._inflight
        if not fut.done():
            return
        self._inflight = None
        try:
            n = fut.result()
        except Exception as e:
            self._failed(
                now, s, f"{type(e).__name__}: {str(e).splitlines()[0][:120] if str(e) else ''}"
            )
            return
        self.done.add(s.key)
        self.results[s.key] = n
        self._next_try.pop(s.key, None)
        tag = (s.trade_date, s.session)
        if n > 0:
            log_event(
                log,
                logging.INFO,
                SERVICE,
                "open_check_ok",
                tag,
                trades=n,
                opens_at=s.opens_at.isoformat(),
            )
            return
        self._mismatch(now, s)

    def _mismatch(self, now: datetime, s: OpenSession) -> None:
        window = timedelta(seconds=self.cfg.window_s)
        minutes = self.cfg.window_s / 60
        reason = f"캘린더상 열린 {s.label()} — 개장 뒤 {minutes:g}분 동안 선물 체결 0건"
        detail = {
            "calendar": "open",
            "observed": "no_futures_trades",
            "window_start": s.opens_at.isoformat(),
            "window_end": (s.opens_at + window).isoformat(),
            "reason": "임시 휴장(캘린더 오류) 또는 웹소켓 수집 멈춤",
        }
        rec = SessionLogRecord(
            ts=s.opens_at + window,
            trade_date=s.trade_date,
            session=s.session,
            kind="calendar_mismatch",
            detail=detail,
        )
        try:
            self._log_store.write_session_log([rec])
        except Exception as e:  # 기록 실패가 경고를 막지 않는다
            self._emit(
                now, "session_log_failed", f"calendar_mismatch 기록 실패: {type(e).__name__}"
            )
        log_event(
            log, logging.WARNING, SERVICE, "calendar_mismatch", (s.trade_date, s.session), **detail
        )
        self._emit(now, "calendar_mismatch", f"{reason} — 임시 휴장인지 수집이 멈췄는지 확인")

    def _failed(self, now: datetime, s: OpenSession, detail: str) -> None:
        self._next_try[s.key] = now + timedelta(seconds=self.cfg.retry_s)
        last = self._failed_at
        if last is None or (now - last).total_seconds() >= self.cfg.fail_every_s:
            self._failed_at = now
            again = f"{self.cfg.retry_s:g}초 뒤 다시(세션 끝까지)"
            self._emit(
                now, "open_check_failed", f"{s.label()} 개장 체결 확인 실패: {detail} — {again}"
            )

    def _emit(self, now: datetime, kind: str, detail: str, severity: Severity = "warning") -> None:
        try:
            self._health.emit(HealthEvent(kind, detail, now, severity, service=SERVICE))
        except Exception as e:
            log_event(log, logging.ERROR, SERVICE, "health_failed", error=type(e).__name__)

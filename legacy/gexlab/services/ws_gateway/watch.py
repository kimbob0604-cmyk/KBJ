"""ws-gateway 닫힌 세션 확인 구독 — 캘린더상 닫힌 날·밤에 예상 밖 개장을 잡는다 (설계 §6 이중 확인).

닫힌 밤엔 PRE_NIGHT·NIGHT 가 없어 구독이 없으므로, 야간장 열림 규칙(휴장 전날 밤 없음 — 실측 1건,
월요일 휴장 앞 금요일 밤 없음 — 미실측 가정, core/calendar.py)이 틀려도 체결이 들어올 길이 없다.
그래서 캘린더상 닫힌 세션에도 개장 시각부터 10분간 선물 체결 1건을 구독한다. 반대쪽(열린 세션인데
체결 없음)은 scheduler 몫이다(services/scheduler/open_check.py).

- 대상: 평일 D 의 주간(D 가 휴장일 — 08:45)과 밤(그 밤이 열리지 않을 때 — 18:00: 휴장 전날 밤,
  휴장일 밤, 월요일 휴장 앞 금요일 밤). 주말은 보지 않는다(KRX 주말 세션 없음 **[확인 필요]**)
- 창(`closed_window`): [개장 − `lead_s`(60초 **[확인 필요]** — 개장 체결 전에 등록이 끝나게),
  개장 + `duration_s`(600초 — 설계 §6 10분)). 구독: 주간 `H0IFCNT0`·야간 `H0MFCNT0`, 종목은 최근접
  체인의 선물 근월물(컨트롤러 `ChainSource` — 휴장일엔 전 거래일 마스터 + 캘린더). 1건이라 41건
  예산 안이다(`budget.derive(설정, 세션)` 의 선물 + 옵션 자리)
- 창 안에(창 끝 뒤 마감 여유 `grace_s` 까지) 그 TR 의 선물 체결이 오면 창마다 한 번: health warning
  `ws_unexpected_open` + session_log `unexpected_open`(service ws-gateway, 열렸다면 귀속됐을 거래일·
  세션 — 주간 D, 야간 next_trading_day(D)). 이어 오는 체결은 센다(`seen`)
- 창이 끝났는데 체결이 없으면: 확인 구독이 [개장, 창 끝)의 `armed_ratio`(80% **[확인 필요]**) 이상
  서버에 걸려 있고 연결 중이었을 때만 info `ws_closed_watch_quiet` — 가정(닫힘)이 맞았다는 기록.
  모자라면(체인이 없어 구독을 못 함·연결 끊김·등록 거절·늦은 기동) warning
  `ws_closed_watch_unverified` — 확인하지 못한 것을 확인했다고 남기지 않는다. 구독 여부는 컨트롤러가
  창 안 step 마다 `armed(시각, 걸려 있나)` 표본으로 알리고, 이웃한 두 표본이 모두 참인 사이만 센다
- 체결 행은 만들지 않는다(장 밖 수신은 거래일 태그가 없다 — 원문은 raw_messages 에 남는다)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from core.calendar import (
    DAY_START,
    KST,
    NIGHT_START,
    TradingCalendar,
    night_session_opens,
)
from data.kis.ws import FuturesTick
from data.store import SessionLogRecord
from services.auth.health import HealthEvent, HealthSink, Severity
from services.runtime import log_event
from services.ws_gateway.client import SERVICE, TickEvent
from services.ws_gateway.subscriptions import TR_IDS

WATCH_LEAD_S = 60.0  # 확인 필요: 개장 체결 전에 등록이 끝나게
WATCH_S = 600.0  # 설계 §6: 개장 시각부터 10분
WATCH_ARMED_RATIO = 0.8  # 확인 필요: 창(개장 뒤)의 이만큼 구독이 걸려 있어야 '닫힘 확인'

Session = Literal["day", "night"]
SessionLogSink = Callable[[SessionLogRecord], None]

log = logging.getLogger("services.ws_gateway.watch")


@dataclass(frozen=True)
class WatchWindow:
    """캘린더상 닫힌 세션 하나의 확인 구독 창."""

    session: Session
    start_date: date  # 세션이 시작했을 달력일 D
    trade_date: date  # 열렸다면 귀속됐을 거래일 (주간 D, 야간 next_trading_day(D))
    opens_at: datetime  # 정상 개장 시각 (KST)
    start: datetime  # 구독 시작 = 개장 − lead
    end: datetime  # 구독 끝 = 개장 + duration

    @property
    def key(self) -> tuple[Session, date]:
        return self.session, self.start_date

    @property
    def tr_id(self) -> str:
        return TR_IDS[self.session]["futures"]

    def label(self) -> str:
        return f"{'주간' if self.session == 'day' else '야간'} {self.opens_at:%m-%d %H:%M}"


def closed_window(
    now: datetime,
    cal: TradingCalendar,
    *,
    lead_s: float = WATCH_LEAD_S,
    duration_s: float = WATCH_S,
) -> WatchWindow | None:
    """now 가 캘린더상 닫힌 세션의 확인 구독 창 안이면 그 창."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("naive datetime 금지")
    d = now.astimezone(KST).date()
    if d.weekday() >= 5:
        return None
    lead, span = timedelta(seconds=lead_s), timedelta(seconds=duration_s)
    candidates: list[tuple[Session, datetime, date]] = []
    if not cal.is_trading_day(d):
        candidates.append(("day", datetime.combine(d, DAY_START, tzinfo=KST), d))
    if not night_session_opens(d, cal):
        opens = datetime.combine(d, NIGHT_START, tzinfo=KST)
        candidates.append(("night", opens, cal.next_trading_day(d)))
    for session, opens, trade_date in candidates:
        if opens - lead <= now < opens + span:
            return WatchWindow(session, d, trade_date, opens, opens - lead, opens + span)
    return None


@dataclass
class _Seen:
    window: WatchWindow
    trades: int = 0
    first_at: datetime | None = None
    reported: bool = False
    armed_s: float = 0.0  # [개장, 창 끝) 안에서 확인 구독이 걸려 있던 초
    last: tuple[datetime, bool] | None = None  # 마지막 armed 표본 (시각, 걸려 있나)


class ClosedWatch:
    """확인 구독 창과 예상 밖 개장 판정. 컨트롤러가 `window`·`armed`·`step`, tick 콜백이
    `observe` 를 부른다(둘 다 이벤트 루프 스레드)."""

    def __init__(
        self,
        calendar: TradingCalendar,
        health: HealthSink,
        session_log: SessionLogSink,
        *,
        lead_s: float = WATCH_LEAD_S,
        duration_s: float = WATCH_S,
        grace_s: float = 60.0,
        armed_ratio: float = WATCH_ARMED_RATIO,
    ) -> None:
        if lead_s < 0 or duration_s <= 0 or grace_s < 0:
            raise ValueError("lead_s >= 0, duration_s > 0, grace_s >= 0")
        if not 0 < armed_ratio <= 1:
            raise ValueError("armed_ratio 는 (0, 1]")
        self._cal = calendar
        self._health = health
        self._session_log = session_log
        self._lead = lead_s
        self._duration = duration_s
        self._grace = timedelta(seconds=grace_s)
        self._need_s = duration_s * armed_ratio
        self.seen: dict[tuple[Session, date], _Seen] = {}

    def window(self, now: datetime) -> WatchWindow | None:
        w = closed_window(now, self._cal, lead_s=self._lead, duration_s=self._duration)
        if w is not None and w.key not in self.seen:
            self.seen[w.key] = _Seen(w)
            log_event(
                log,
                logging.INFO,
                SERVICE,
                "ws_closed_watch_start",
                session=w.session,
                opens_at=w.opens_at.isoformat(),
                tr_id=w.tr_id,
            )
        return w

    def armed(self, at: datetime, subscribed: bool) -> None:
        """확인 구독 표본 — at 에 그 창의 구독이 서버에 걸려 있고 연결 중인가. 이웃한 두 표본이
        모두 참이면 그 사이를 [개장, 창 끝)으로 잘라 구독 시간(`armed_s`)에 더한다. 창 밖 표본은
        버린다."""
        w = closed_window(at, self._cal, lead_s=self._lead, duration_s=self._duration)
        if w is None:
            return
        st = self.seen.setdefault(w.key, _Seen(w))
        if st.last is not None and at < st.last[0]:
            return  # 시계가 뒤로 — 세지 않는다
        if st.last is not None and st.last[1] and subscribed:
            lo, hi = max(st.last[0], w.opens_at), min(at, w.end)
            if hi > lo:
                st.armed_s += (hi - lo).total_seconds()
        st.last = (at, subscribed)

    def observe(self, ev: TickEvent) -> None:
        """선물 체결이 닫힌 세션의 창(마감 여유 포함) 안에 왔으면 예상 밖 개장."""
        tick = ev.tick
        if not isinstance(tick, FuturesTick):
            return
        at = ev.received_at
        w = closed_window(at, self._cal, lead_s=self._lead, duration_s=self._duration)
        if w is None:
            w = closed_window(
                at - self._grace, self._cal, lead_s=self._lead, duration_s=self._duration
            )
        if w is None or tick.tr_id != w.tr_id:
            return
        st = self.seen.setdefault(w.key, _Seen(w))
        st.trades += 1
        if st.trades > 1:
            return
        st.first_at = at
        detail = {
            "calendar": "closed",
            "observed": "futures_trade",
            "tr_id": tick.tr_id,
            "code": tick.code,
            "price": str(tick.price),
            "trade_time": tick.hhmmss,
            "received_at": at.isoformat(),
            "opens_at": w.opens_at.isoformat(),
        }
        rec = SessionLogRecord(
            ts=at,
            trade_date=w.trade_date,
            session=w.session,
            service=SERVICE,
            kind="unexpected_open",
            detail=detail,
        )
        try:
            self._session_log(rec)
        except Exception as e:  # 기록 실패가 경고를 막지 않는다
            self._emit(at, "session_log_failed", f"unexpected_open 기록 실패: {type(e).__name__}")
        log_event(
            log, logging.WARNING, SERVICE, "unexpected_open", (w.trade_date, w.session), **detail
        )
        self._emit(
            at,
            "ws_unexpected_open",
            f"캘린더상 닫힌 {w.label()}에 선물 체결 {tick.code} {tick.price} — 임시 개장인지 확인"
            "(야간장 열림 규칙·override 점검)",
        )

    def step(self, now: datetime) -> None:
        """끝난 창(마감 여유 뒤)을 한 번 정리한다 — 체결이 없었으면 구독이 창 대부분 걸려 있었을
        때만 info(닫힘 확인), 아니면 warning(확인 못 함)."""
        for st in self.seen.values():
            w = st.window
            if st.reported or now < w.end + self._grace:
                continue
            st.reported = True
            tag = (w.trade_date, w.session)
            log_event(
                log,
                logging.INFO,
                SERVICE,
                "ws_closed_watch_end",
                tag,
                trades=st.trades,
                armed_s=round(st.armed_s, 1),
            )
            if st.trades > 0:
                continue
            if st.armed_s >= self._need_s:
                minutes = self._duration / 60
                self._emit(
                    now,
                    "ws_closed_watch_quiet",
                    f"캘린더상 닫힌 {w.label()} — {minutes:g}분간 선물 체결 없음(닫힘 확인)",
                    "info",
                )
            else:
                self._emit(
                    now,
                    "ws_closed_watch_unverified",
                    f"캘린더상 닫힌 {w.label()} — 확인 구독을 못 해 닫힘을 확인하지 못했다"
                    f"(구독 {st.armed_s:.0f}초/{self._duration:.0f}초, 필요 {self._need_s:.0f}초"
                    " — 체인·연결·등록 점검)",
                )

    def _emit(self, at: datetime, kind: str, detail: str, severity: Severity = "warning") -> None:
        try:
            self._health.emit(HealthEvent(kind, detail, at, severity, service=SERVICE))
        except Exception as e:
            log.error("ws-gateway health 싱크 실패: %s", type(e).__name__)

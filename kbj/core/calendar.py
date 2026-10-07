"""거래 캘린더와 세션 상태 머신 — KBJ 정본 (docs/p2_design.md §1.3·§7, PLAN §2 정본 표).

GEXLAB `core/calendar.py`(43a9ed1) 승격. GX 이름·동작은 그대로이고(GX 시험을 import 경로만 바꿔
옮겼다), KBJ 에서 더한 것은 다음뿐이다.

- `TradingCalendar(exchange=...)`: 기본 `"XKRX"`(GX 와 같다). `"XNYS"` 는 `us_calendar()` —
  미국 일정(`us.eod`·아침 브리핑 조건) 판정용이고 덮어쓰기 파일을 쓰지 않는다. 야간장·상태 머신·
  만기·분봉·주식 정규장 함수는 KRX 규칙이라 XKRX 캘린더만 받는다(아니면 `ValueError`)
- 주식 정규장 09:00~15:30 KST(`equity_session_bounds`·`is_equity_regular_hours`). GX 상태 머신은
  **파생** 세션(08:45~15:45)이다 — 둘을 섞지 않는다
- `session_tag`: GX `services/poller/context.py:49` 에서 옮겼다(시각 → 귀속 거래일·세션)
- `OVERRIDE_PATH` 는 레포 루트 `config/holidays_override.yaml`(이 파일 기준 두 단계 위 — GX 는 한
  단계 위였다). 다른 폴더(`Settings.config_dir`)의 파일을 쓰려면
  `TradingCalendar.from_override(load_override(path))`
- `KST` 는 `kbj.core.time.KST` 를 다시 내보낸다(정의는 한 곳)
- legacy 의 휴장·장중 판정 대체는 `kbj.core.calendar_compat`(설계 §7.2)

아래는 GX 머리말 그대로다(실측 근거의 docs 경로는 GX 레포 기준).

- 휴장일: `exchange_calendars` XKRX + `config/holidays_override.yaml`. XKRX 를 쓰는 범위는
  `XKRX_START`~`XKRX_END`(라이브러리 사전계산 상한 2050-12-31). 범위 밖은 **평일 규칙 + override**
  로 판정하고(공휴일을 모름) `TradingCalendar.covered()` 가 False 를 돌려준다.
- 야간장 열림 규칙: 거래일 D 의 밤은 **금요일이면 늘**, 월~목이면 **다음 날(D+1)이 거래일일 때만**
  열린다(밤이 D+1 06:00 에 끝나므로 — 금요일 밤은 토 06:00 에 끝나 월요일 휴장과 무관). 귀속은
  언제나 `next_trading_day(D)`
  - 실측(2026-09-28 CM 분봉, docs/probe_results.md 실행 기록 14:08): 금요일 09-18 밤 **열림**,
    토 06:00까지. 휴장 전날인 수요일 09-23 밤 **안 열림**
  - 실측(2026-09-29 15:52 CM 분봉, probe `minute_paging`): **월요일 휴장 앞 금요일 밤도 열림** —
    05-22(월 05-25 휴장)·08-14(토 08-15 광복절, 월 08-17 휴장) 모두 22:19~24:00 봉, 대조군 목요일과
    같다. 그 전 가정(안 열림)을 고쳤다. 예: 2026-10-02 밤(월 10-05 휴장) 열림 → 10-06 귀속
  - 미실측 [확인 필요]: 여러 날 연휴(설·추석이 월요일부터) 앞 금요일 밤 — 같은 규칙(열림)을 쓴다.
    틀리면 scheduler 의 이중 확인(PLAN §4.6 — 개장 뒤 체결 없음/휴장인데 체결)이 잡는다
- 상태 구간은 모두 [시작, 끝) 반열림이고 KST 벽시계로 비교한다. 입력은 어느 시간대든 aware 면 된다.
- 반영하지 않은 것: 수능일·연초 개장일의 지연 개장(시각 고정 08:45~15:45), 야간장 도입(2025-06)
  이전 날짜(규칙상 열림으로 나온다 — 실시간 운영용이지 과거 세션 판정용이 아니다)
  - KBJ 주식 정규장은 지연 개장을 반영한다(설계 R24, 메인 결정 2026-10-07): 그해 첫 거래일은
    10:00 개장(XKRX `session_open` 과 같다), 수능일 등은 `holidays_override.yaml` 의 `late_open:`
    (예: 10:00~16:30). `TradingCalendar.equity_bounds`·`equity_session_bounds(d, cal)` 가 쓴다.
    참고 실측(exchange_calendars 4.13.2, 2026-10-07): XKRX 는 연초 첫 거래일(2026-01-02)은
    10:00 으로 알지만 수능일(2025-11-13·2026-11-19)은 09:00 으로 본다 — 그래서 수능일은 덮어쓰기
    파일에 둔다.
    파생 세션(`state_at`, 08:45~15:45)의 지연은 아직 반영하지 않는다 [확인 필요]
- 만기 계산(`monthly_expiry` 등)은 KIS 월물리스트·`futs_last_tr_date` 교차검증용 보조다(§2.3).
"""

from __future__ import annotations

import datetime as dt
import functools
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kbj.core.time import KST

__all__ = [
    "DAY_END",
    "DAY_START",
    "EQUITY_CLOSE",
    "EQUITY_OPEN",
    "EXPIRY_TIME",
    "KST",
    "NIGHT_END",
    "NIGHT_START",
    "OVERRIDE_PATH",
    "PRE_DAY_START",
    "PRE_NIGHT_START",
    "SUPPORTED_EXCHANGES",
    "XKRX_END",
    "XKRX_START",
    "HolidayOverride",
    "OverrideDay",
    "Session",
    "SessionInfo",
    "SessionTag",
    "State",
    "TradingCalendar",
    "WeeklyListing",
    "day_bar_time",
    "equity_session_bounds",
    "expiry_at",
    "is_equity_regular_hours",
    "load_override",
    "monthly_expiry",
    "night_bar_time",
    "night_session_opens",
    "session_bounds",
    "session_tag",
    "state_at",
    "us_calendar",
    "weeklies_listed",
    "weekly_monday_expiry",
    "weekly_thursday_expiry",
]

Session = Literal["day", "night"]
SessionTag = tuple[date, Session]  # (귀속 거래일, 세션) — `session_tag` 결과

# 레포 루트(이 파일 기준 두 단계 위)의 config — kbj.config.files.REPO_ROOT 와 같은 곳
OVERRIDE_PATH = Path(__file__).resolve().parents[2] / "config" / "holidays_override.yaml"
XKRX_START = date(2000, 1, 1)
XKRX_END = date(2050, 12, 31)  # exchange_calendars XKRX bound_max
# 받는 거래소. XNYS 는 사전계산 상한이 없지만 같은 기본 범위(2000~2050)를 쓴다 — 범위 밖은 평일 규칙
SUPPORTED_EXCHANGES: Final = ("XKRX", "XNYS")
_KRX: Final = "XKRX"

# 상태 경계 (KST, PLAN §4.6). NIGHT_END 는 다음 달력일 06:00
PRE_DAY_START = time(8, 0)
DAY_START = time(8, 45)
DAY_END = time(15, 45)
PRE_NIGHT_START = time(17, 50)
NIGHT_START = time(18, 0)
NIGHT_END = time(6, 0)
EXPIRY_TIME = time(15, 20)  # 만기일 종료 시각 (PLAN §2.3)

# 주식 정규장 (KST, KBJ 추가 — 설계 §7.1). [EQUITY_OPEN, EQUITY_CLOSE) 반열림. 평소 시각이고, 지연
# 개장일은 `TradingCalendar.equity_bounds` 가 바꾼다(그해 첫 거래일 10:00, override `late_open`)
EQUITY_OPEN = time(9, 0)
EQUITY_CLOSE = time(15, 30)
NEW_YEAR_OPEN = time(10, 0)  # 그해 첫 거래일(KRX 관례 — XKRX session_open 과 같다)

# 다음·앞 거래일을 찾을 때 이만큼 연속 휴장이면 캘린더·override 설정 오류로 본다
_MAX_CLOSED_RUN = 60
_MONDAY, _THURSDAY = 0, 3


# ── override 파일 ─────────────────────────────────────────────────────────────


class OverrideDay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    date: dt.date
    reason: str = Field(min_length=1)


class LateOpenDay(BaseModel):
    """주식 정규장 시각이 평소와 다른 날(수능일 등). [open, close) KST."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    date: dt.date
    open: time
    close: time
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> LateOpenDay:
        if not self.open < self.close:
            raise ValueError(
                f"{self.date}: open({self.open}) 이 close({self.close}) 보다 앞이어야 한다"
            )
        return self


class HolidayOverride(BaseModel):
    """`config/holidays_override.yaml`. closed: XKRX 에 없는 임시 휴장, open: XKRX 오판 개장,
    late_open: 주식 정규장 시각이 다른 날(수능일 등)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    closed: tuple[OverrideDay, ...] = ()
    open: tuple[OverrideDay, ...] = ()
    late_open: tuple[LateOpenDay, ...] = ()

    @field_validator("closed", "open", "late_open", mode="before")
    @classmethod
    def _none_is_empty(cls, v: object) -> object:
        """`closed:` 처럼 값 없이 둔 키는 빈 목록으로 본다."""
        return () if v is None else v

    @model_validator(mode="after")
    def _no_conflict(self) -> HolidayOverride:
        both = {x.date for x in self.closed} & {x.date for x in self.open}
        if both:
            raise ValueError(f"같은 날이 closed·open 양쪽에 있다: {sorted(both)}")
        late = [x.date for x in self.late_open]
        if len(late) != len(set(late)):
            raise ValueError("late_open 에 같은 날이 두 번 있다")
        shut = set(late) & {x.date for x in self.closed}
        if shut:
            raise ValueError(f"휴장(closed)인 날에 late_open 이 있다: {sorted(shut)}")
        return self


def load_override(path: Path = OVERRIDE_PATH) -> HolidayOverride:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return HolidayOverride.model_validate(raw if raw is not None else {})


# ── 거래일 ────────────────────────────────────────────────────────────────────


@functools.cache
def _exchange_sessions(exchange: str, start: date, end: date) -> frozenset[date]:
    """거래소 거래일 집합. 타입이 느슨한 exchange_calendars·pandas 는 여기서만 만진다.

    GX `_xkrx_sessions` 에 거래소 이름 인자만 더했다(XKRX 2000~2050 은 만드는 데 수 초 — 캐시).
    """
    # 무거운 import 라 처음 쓸 때만. 스텁이 없어 타입은 소스에서 추론된다
    import exchange_calendars as xc  # pyright: ignore[reportMissingTypeStubs]

    cal = xc.get_calendar(exchange, start=start.isoformat(), end=end.isoformat())
    return frozenset(date(int(ts.year), int(ts.month), int(ts.day)) for ts in cal.sessions)


def _as_date(d: date) -> date:
    # datetime 은 date 의 하위 클래스지만 date 와 같다고 비교되지 않아 조용히 틀린다
    if isinstance(d, datetime):
        raise TypeError(f"date 를 넘겨야 한다(datetime 받음: {d!r})")
    return d


class TradingCalendar:
    """거래일 판정. 기본은 KRX 파생(XKRX), `exchange="XNYS"` 면 뉴욕증권거래소.

    우선순위: override closed > override open > 거래소 캘린더(범위 안) > 평일 규칙(범위 밖).
    날짜는 그 거래소 현지 달력일이다(XNYS 면 뉴욕 날짜).
    """

    def __init__(
        self,
        *,
        extra_closed: Iterable[date] = (),
        extra_open: Iterable[date] = (),
        start: date = XKRX_START,
        end: date = XKRX_END,
        exchange: str = _KRX,
        late_open: Mapping[date, tuple[time, time]] | None = None,
    ) -> None:
        if exchange not in SUPPORTED_EXCHANGES:
            raise ValueError(f"지원하지 않는 거래소: {exchange!r} (받는 값 {SUPPORTED_EXCHANGES})")
        if start > end:
            raise ValueError(f"{exchange} 범위가 거꾸로다: {start} > {end}")
        closed = frozenset(_as_date(d) for d in extra_closed)
        opened = frozenset(_as_date(d) for d in extra_open)
        both = closed & opened
        if both:
            raise ValueError(f"같은 날이 휴장·개장 양쪽에 있다: {sorted(both)}")
        self._exchange = exchange
        self._start = start
        self._end = end
        self._sessions = _exchange_sessions(exchange, start, end)
        self._closed = closed
        self._open = opened
        self._late_open: dict[date, tuple[time, time]] = dict(late_open or {})

    @classmethod
    def from_override(
        cls, override: HolidayOverride, *, start: date = XKRX_START, end: date = XKRX_END
    ) -> TradingCalendar:
        return cls(
            extra_closed=[x.date for x in override.closed],
            extra_open=[x.date for x in override.open],
            start=start,
            end=end,
            late_open={x.date: (x.open, x.close) for x in override.late_open},
        )

    @classmethod
    def default(cls) -> TradingCalendar:
        """XKRX + `config/holidays_override.yaml`. 프로세스당 한 번 만든다."""
        return _default_calendar()

    @property
    def exchange(self) -> str:
        """거래소 이름(`XKRX`·`XNYS`)."""
        return self._exchange

    @property
    def coverage(self) -> tuple[date, date]:
        return self._start, self._end

    def covered(self, d: date) -> bool:
        """XKRX 로 판정하는 날인가. False 면 평일 규칙 + override 로 판정한 값이다."""
        d = _as_date(d)
        return self._start <= d <= self._end

    def is_trading_day(self, d: date) -> bool:
        d = _as_date(d)
        if d in self._closed:
            return False
        if d in self._open:
            return True
        if self._start <= d <= self._end:
            return d in self._sessions
        return d.weekday() < 5

    def equity_bounds(self, d: date) -> tuple[datetime, datetime]:
        """달력일 d 의 주식 정규장 [시작, 끝) KST(aware) — 지연 개장 반영(KRX 만).

        override `late_open` > 그해 첫 거래일 10:00 개장 > 평소 09:00~15:30. 휴장 여부는 보지 않는다
        (`is_trading_day` 로 따로) — 휴장일에는 평소 시각을 돌려준다.
        """
        _require_krx(self, "equity_bounds")
        d = _as_date(d)
        if d in self._late_open:
            o, c = self._late_open[d]
            return _kst(d, o), _kst(d, c)
        if self.is_trading_day(d) and self.prev_trading_day(d).year < d.year:
            return _kst(d, NEW_YEAR_OPEN), _kst(d, EQUITY_CLOSE)
        return _kst(d, EQUITY_OPEN), _kst(d, EQUITY_CLOSE)

    def next_trading_day(self, d: date) -> date:
        """d 보다 뒤(d 제외)의 첫 거래일."""
        return self._step(d, 1)

    def prev_trading_day(self, d: date) -> date:
        """d 보다 앞(d 제외)의 마지막 거래일."""
        return self._step(d, -1)

    def trading_days_between(self, a: date, b: date) -> int:
        """(a, b] 안의 거래일 수 — a == b 면 0, a > b 면 ValueError.

        확정 베이시스 이월 나이(GX metrics §1.3)는 `session_day` 로 옮긴 두 날 사이를 이것으로 센다
        (GX `core.forward.basis_age` — P7 까지 legacy).
        """
        a, b = _as_date(a), _as_date(b)
        if a > b:
            raise ValueError(f"a 가 b 보다 앞이어야 한다: {a} > {b}")
        n, d = 0, a
        while True:
            d = self.next_trading_day(d)
            if d > b:
                return n
            n += 1

    def session_day(self, trade_date: date, session: Session) -> date:
        """그 세션이 속한 거래일의 '하루' — 주간은 귀속 거래일 그대로, 야간은 그 밤이 이어지는
        주간의 거래일(귀속 거래일의 앞 거래일 — 금요일 밤(월 귀속)은 금요일).

        주간 세션과 바로 이어지는 야간을 한 단위로 본다(베이시스 나이, 2026-09-29 사용자 결정).
        trade_date 가 거래일이 아니면 ValueError.
        """
        d = _as_date(trade_date)
        if not self.is_trading_day(d):
            raise ValueError(f"귀속 거래일이 거래일이 아니다: {d}")
        if session == "day":
            return d
        if session == "night":
            return self.prev_trading_day(d)
        raise ValueError(f"세션은 day·night: {session!r}")

    @staticmethod
    def next_weekday(d: date) -> date:
        """d 보다 뒤의 첫 월~금(휴장 무관)."""
        d = _as_date(d) + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    def _step(self, d: date, sign: int) -> date:
        d = _as_date(d)
        for _ in range(_MAX_CLOSED_RUN):
            d += timedelta(days=sign)
            if self.is_trading_day(d):
                return d
        raise RuntimeError(f"{_MAX_CLOSED_RUN}일 연속 휴장 — 캘린더·override 를 확인하라 ({d})")


@functools.cache
def _default_calendar() -> TradingCalendar:
    return TradingCalendar.from_override(load_override())


@functools.cache
def _us_calendar() -> TradingCalendar:
    return TradingCalendar(exchange="XNYS")


def us_calendar() -> TradingCalendar:
    """XNYS(뉴욕증권거래소) 거래일 — 덮어쓰기 없음. 날짜는 뉴욕 날짜. 프로세스당 한 번 만든다.

    미국 일정(`us.eod` 의 `us_trading_day`, 아침 브리핑의 `after_us_session`)을 판정한다(설계 §6.4).
    조기 폐장(추수감사절 다음 날 등 13:00)은 거래일로만 본다 — 시각은 보지 않는다.
    """
    return _us_calendar()


def _require_krx(cal: TradingCalendar, what: str) -> None:
    # 야간장·파생 상태 머신·만기·분봉·주식 정규장은 KRX 규칙이다. XNYS 캘린더를 넣으면 조용히 틀린다
    if cal.exchange != _KRX:
        raise ValueError(f"{what} 는 XKRX 캘린더만 받는다({cal.exchange} 받음)")


def night_session_opens(d: date, cal: TradingCalendar) -> bool:
    """거래일 d 의 18:00~익일 06:00 야간장이 열리는가 (모듈 docstring 의 실측·가정 참고).

    금요일 밤은 늘 열리고(월요일 휴장이어도 — 2026-09-29 실측), 월~목 밤은 다음 날이 거래일일 때만.
    """
    _require_krx(cal, "night_session_opens")
    d = _as_date(d)
    if not cal.is_trading_day(d):
        return False
    return d.weekday() == 4 or cal.is_trading_day(d + timedelta(days=1))


# ── 세션 상태 머신 ────────────────────────────────────────────────────────────


class State(StrEnum):
    PRE_DAY = "PRE_DAY"  # 08:00~08:45, 거래일
    DAY = "DAY"  # 08:45~15:45, 거래일
    POST_DAY = "POST_DAY"  # 15:45~17:50, 거래일
    PRE_NIGHT = "PRE_NIGHT"  # 17:50~18:00, 그날 밤 야간장이 열릴 때
    NIGHT = "NIGHT"  # 18:00~익일 06:00, 그날 밤 야간장이 열릴 때
    IDLE = "IDLE"  # 그 외, 휴장일


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """`state_at` 결과. trade_date·session 은 장이 열린 DAY·NIGHT 에만 있고 나머지는 None."""

    state: State
    trade_date: date | None = None
    session: Session | None = None


_IDLE = SessionInfo(State.IDLE)
_PRE_DAY = SessionInfo(State.PRE_DAY)
_POST_DAY = SessionInfo(State.POST_DAY)
_PRE_NIGHT = SessionInfo(State.PRE_NIGHT)


def state_at(ts: datetime, cal: TradingCalendar) -> SessionInfo:
    """시각 ts(aware, 시간대 무관)의 상태·귀속 거래일·세션.

    DAY → trade_date = D, session = day. D 에 시작한 NIGHT → trade_date = next_trading_day(D),
    session = night (자정 뒤 새벽분 포함 — 금요일 밤의 토요일 새벽은 보통 월요일 귀속).
    """
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"naive datetime 은 받지 않는다: {ts!r}")
    _require_krx(cal, "state_at")
    k = ts.astimezone(KST)
    d, t = k.date(), k.time()
    if t < NIGHT_END:  # 00:00~06:00 은 전날 밤 야간장의 뒷부분이거나 IDLE
        started = d - timedelta(days=1)
        if night_session_opens(started, cal):
            return SessionInfo(State.NIGHT, cal.next_trading_day(started), "night")
        return _IDLE
    if not cal.is_trading_day(d) or t < PRE_DAY_START:
        return _IDLE
    if t < DAY_START:
        return _PRE_DAY
    if t < DAY_END:
        return SessionInfo(State.DAY, d, "day")
    if t < PRE_NIGHT_START:
        return _POST_DAY
    if not night_session_opens(d, cal):
        return _IDLE
    if t < NIGHT_START:
        return _PRE_NIGHT
    return SessionInfo(State.NIGHT, cal.next_trading_day(d), "night")


def session_tag(ts: datetime, cal: TradingCalendar) -> SessionTag | None:
    """레코드에 붙일 (귀속 거래일, 세션) — `state_at` 기준 (GX `services/poller/context.py:49`).

    DAY·NIGHT 는 state_at 값 그대로. 장 전 준비 상태(PRE_DAY·PRE_NIGHT)는 곧 열릴 세션으로 본다
    (그 세션 시작 시각의 state_at). POST_DAY·IDLE 은 None — poller 는 이때 일하지 않는다.
    """
    info = state_at(ts, cal)
    if info.trade_date is not None and info.session is not None:
        return info.trade_date, info.session
    d = ts.astimezone(KST).date()
    if info.state is State.PRE_DAY:
        start = session_bounds(d, "day")[0]
    elif info.state is State.PRE_NIGHT:
        start = session_bounds(d, "night")[0]
    else:
        return None
    nxt = state_at(start, cal)
    if nxt.trade_date is None or nxt.session is None:
        return None
    return nxt.trade_date, nxt.session


def _kst(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=KST)


def session_bounds(d: date, session: Session) -> tuple[datetime, datetime]:
    """달력일 d 에 **시작하는** 세션의 [시작, 끝) (KST aware). 야간의 d 는 귀속 거래일이 아니다.

    휴장 여부는 보지 않는다 — 열리는지는 `is_trading_day`·`night_session_opens` 로 따로 판정.
    """
    d = _as_date(d)
    if session == "day":
        return _kst(d, DAY_START), _kst(d, DAY_END)
    if session == "night":
        return _kst(d, NIGHT_START), _kst(d + timedelta(days=1), NIGHT_END)
    raise ValueError(f"session 은 day·night 중 하나: {session!r}")


def expiry_at(d: date) -> datetime:
    """만기일 d 의 만기 시각 15:20 KST (PLAN §2.3)."""
    return _kst(_as_date(d), EXPIRY_TIME)


# ── 주식 정규장 (KBJ 추가 — 설계 §7.1) ────────────────────────────────────────


def equity_session_bounds(d: date, cal: TradingCalendar | None = None) -> tuple[datetime, datetime]:
    """달력일 d 의 주식 정규장 [시작, 끝) KST(aware).

    `cal` 을 주면 지연 개장(그해 첫 거래일 10:00, override `late_open` 수능일 등)을 반영한다
    (`TradingCalendar.equity_bounds`). 생략하면 평소 시각 [09:00, 15:30) — 운영 코드는 `cal` 을
    넘긴다. `session_bounds` 처럼 휴장 여부는 보지 않는다(`is_trading_day` 로 따로).
    """
    d = _as_date(d)
    if cal is not None:
        return cal.equity_bounds(d)
    return _kst(d, EQUITY_OPEN), _kst(d, EQUITY_CLOSE)


def is_equity_regular_hours(ts: datetime, cal: TradingCalendar) -> bool:
    """시각 ts(aware, 시간대 무관)가 KRX 거래일의 주식 정규장 안인가(지연 개장 반영).

    파생 주간 세션(08:45~15:45, `state_at`)보다 좁다 — 장전 동시호가(08:30~09:00)·장후 시간외는
    정규장이 아니다. naive 는 `ValueError`.
    """
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"naive datetime 은 받지 않는다: {ts!r}")
    _require_krx(cal, "is_equity_regular_hours")
    k = ts.astimezone(KST)
    if not cal.is_trading_day(k.date()):
        return False
    start, end = cal.equity_bounds(k.date())
    return start <= k < end


# ── KIS 분봉 시각 (phase1_design §8) ──────────────────────────────────────────

_YYYYMMDD = re.compile(r"[0-9]{8}")
_HHMMSS = re.compile(r"[0-9]{6}")


def _parse_yyyymmdd(s: str) -> date:
    if not _YYYYMMDD.fullmatch(s):
        raise ValueError(f"날짜가 YYYYMMDD 가 아니다: {s!r}")
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))  # 없는 날짜(13월 등)는 ValueError


def _parse_hhmmss(s: str) -> timedelta:
    if not _HHMMSS.fullmatch(s):
        raise ValueError(f"시각이 HHMMSS 가 아니다: {s!r}")
    h, m, sec = int(s[:2]), int(s[2:4]), int(s[4:])
    if m > 59 or sec > 59:
        raise ValueError(f"분·초 범위 밖: {s!r}")
    return timedelta(hours=h, minutes=m, seconds=sec)


def night_bar_time(date_yyyymmdd: str, hhmmss: str, cal: TradingCalendar) -> tuple[datetime, date]:
    """KIS 야간(`CM`) 분봉 (야간 시작일, 18~30시 확장 표기) → (UTC 시각, 귀속 거래일).

    예: ('20260922', '300000') = 2026-09-23 06:00 KST, trade_date 2026-09-23.
    허용 범위 18:00:00~30:00:00. 그 밖이나 형식 오류는 ValueError.
    """
    _require_krx(cal, "night_bar_time")
    start = _parse_yyyymmdd(date_yyyymmdd)
    offset = _parse_hhmmss(hhmmss)
    if not timedelta(hours=18) <= offset <= timedelta(hours=30):
        raise ValueError(f"야간 봉 시각은 180000~300000: {hhmmss!r}")
    ts = datetime.combine(start, time(0), tzinfo=KST) + offset
    return ts.astimezone(UTC), cal.next_trading_day(start)


def day_bar_time(date_yyyymmdd: str, hhmmss: str) -> tuple[datetime, date]:
    """KIS 주간(`F`) 분봉 (날짜 D, HHMMSS) → (UTC 시각, trade_date = D).

    허용 범위는 PRE_DAY~POST_DAY(08:00:00 이상 17:50:00 미만) — 주간 첫 봉 표기는 미실측이라
    세션(08:45~15:45)보다 넓게 받되, 야간 시각 봉이 주간으로 잘못 귀속되는 것은 막는다.
    """
    d = _parse_yyyymmdd(date_yyyymmdd)
    offset = _parse_hhmmss(hhmmss)
    lo = timedelta(hours=PRE_DAY_START.hour, minutes=PRE_DAY_START.minute)
    hi = timedelta(hours=PRE_NIGHT_START.hour, minutes=PRE_NIGHT_START.minute)
    if not lo <= offset < hi:
        raise ValueError(f"주간 봉 시각은 080000 이상 175000 미만: {hhmmss!r}")
    ts = datetime.combine(d, time(0), tzinfo=KST) + offset
    return ts.astimezone(UTC), d


# ── 만기 계산 (교차검증용, PLAN §2.3·§6.3) ────────────────────────────────────


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)  # 없는 달이면 ValueError
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _require_weekday(d: date, weekday: int, name: str) -> date:
    d = _as_date(d)
    if d.weekday() != weekday:
        raise ValueError(f"{name}이 아니다: {d} ({d:%a})")
    return d


def monthly_expiry(year: int, month: int, cal: TradingCalendar) -> date:
    """월물 최종거래일: 둘째 목요일, 휴장이면 **앞** 거래일로 당긴다 (PLAN §2.1·§6.3)."""
    _require_krx(cal, "monthly_expiry")
    nominal = _nth_weekday(year, month, _THURSDAY, 2)
    return nominal if cal.is_trading_day(nominal) else cal.prev_trading_day(nominal)


def weekly_monday_expiry(monday: date, cal: TradingCalendar) -> date:
    """월요일 위클리 최종거래일: 그 월요일, 휴장이면 **다음** 거래일로 순연 (PLAN §6.3).

    실측: KIS 단건 `futs_last_tr_date` 가 10-05(대체공휴일) 주 월요일 위클리를 2026-10-06 으로 줌.
    """
    _require_krx(cal, "weekly_monday_expiry")
    monday = _require_weekday(monday, _MONDAY, "월요일")
    return monday if cal.is_trading_day(monday) else cal.next_trading_day(monday)


def weekly_thursday_expiry(thursday: date, cal: TradingCalendar) -> date:
    """목요일 위클리 최종거래일: 그 목요일, 휴장이면 **앞** 거래일로 당긴다.

    [확인 필요] PLAN 에 목요일 위클리의 휴장 규칙이 없어 월물과 같게 둔다.
    """
    _require_krx(cal, "weekly_thursday_expiry")
    thursday = _require_weekday(thursday, _THURSDAY, "목요일")
    return thursday if cal.is_trading_day(thursday) else cal.prev_trading_day(thursday)


@dataclass(frozen=True, slots=True)
class WeeklyListing:
    """한 주(월~일)의 만기일 계산값. 교차검증용 — 원천은 KIS 월물리스트다(PLAN §2.3).

    - monday: 월요일 위클리 만기일(순연 반영). 순연이 그 주를 넘기면 None
    - thursday: 목요일 위클리 만기일. 월물 만기 주면 None(월요일 위클리만 상장), 당긴 날이
      그 주 월요일보다 앞이면 None
    - monthly: 그 주에 든 월물 만기일(휴장 조정 뒤 날짜 기준), 없으면 None
    """

    monday: date | None
    thursday: date | None
    monthly: date | None


def weeklies_listed(week_monday: date, cal: TradingCalendar) -> WeeklyListing:
    week_monday = _require_weekday(week_monday, _MONDAY, "월요일")
    week_end = week_monday + timedelta(days=6)
    monthlies = {monthly_expiry(x.year, x.month, cal) for x in (week_monday, week_end)}
    monthly = next((e for e in sorted(monthlies) if week_monday <= e <= week_end), None)
    postponed = weekly_monday_expiry(week_monday, cal)
    mon = postponed if postponed <= week_end else None
    thu: date | None = None
    if monthly is None:
        thu = weekly_thursday_expiry(week_monday + timedelta(days=3), cal)
        if thu < week_monday:
            thu = None
    return WeeklyListing(monday=mon, thursday=thu, monthly=monthly)

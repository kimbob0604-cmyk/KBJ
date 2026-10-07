"""거래 캘린더와 세션 상태 머신 (PLAN §2.2·§2.3·§4.6, docs/phase1_design.md §6·§8).

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
- 만기 계산(`monthly_expiry` 등)은 KIS 월물리스트·`futs_last_tr_date` 교차검증용 보조다(§2.3).
"""

from __future__ import annotations

import datetime as dt
import functools
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KST = ZoneInfo("Asia/Seoul")

Session = Literal["day", "night"]

OVERRIDE_PATH = Path(__file__).resolve().parents[1] / "config" / "holidays_override.yaml"
XKRX_START = date(2000, 1, 1)
XKRX_END = date(2050, 12, 31)  # exchange_calendars XKRX bound_max

# 상태 경계 (KST, PLAN §4.6). NIGHT_END 는 다음 달력일 06:00
PRE_DAY_START = time(8, 0)
DAY_START = time(8, 45)
DAY_END = time(15, 45)
PRE_NIGHT_START = time(17, 50)
NIGHT_START = time(18, 0)
NIGHT_END = time(6, 0)
EXPIRY_TIME = time(15, 20)  # 만기일 종료 시각 (PLAN §2.3)

# 다음·앞 거래일을 찾을 때 이만큼 연속 휴장이면 캘린더·override 설정 오류로 본다
_MAX_CLOSED_RUN = 60
_MONDAY, _THURSDAY = 0, 3


# ── override 파일 ─────────────────────────────────────────────────────────────


class OverrideDay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    date: dt.date
    reason: str = Field(min_length=1)


class HolidayOverride(BaseModel):
    """`config/holidays_override.yaml`. closed: XKRX 에 없는 임시 휴장, open: XKRX 오판 개장."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    closed: tuple[OverrideDay, ...] = ()
    open: tuple[OverrideDay, ...] = ()

    @field_validator("closed", "open", mode="before")
    @classmethod
    def _none_is_empty(cls, v: object) -> object:
        """`closed:` 처럼 값 없이 둔 키는 빈 목록으로 본다."""
        return () if v is None else v

    @model_validator(mode="after")
    def _no_conflict(self) -> HolidayOverride:
        both = {x.date for x in self.closed} & {x.date for x in self.open}
        if both:
            raise ValueError(f"같은 날이 closed·open 양쪽에 있다: {sorted(both)}")
        return self


def load_override(path: Path = OVERRIDE_PATH) -> HolidayOverride:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return HolidayOverride.model_validate(raw if raw is not None else {})


# ── 거래일 ────────────────────────────────────────────────────────────────────


@functools.cache
def _xkrx_sessions(start: date, end: date) -> frozenset[date]:
    """XKRX 거래일 집합. 타입이 느슨한 exchange_calendars·pandas 는 여기서만 만진다."""
    # 무거운 import 라 처음 쓸 때만. 스텁이 없어 타입은 소스에서 추론된다
    import exchange_calendars as xc  # pyright: ignore[reportMissingTypeStubs]

    cal = xc.get_calendar("XKRX", start=start.isoformat(), end=end.isoformat())
    return frozenset(date(int(ts.year), int(ts.month), int(ts.day)) for ts in cal.sessions)


def _as_date(d: date) -> date:
    # datetime 은 date 의 하위 클래스지만 date 와 같다고 비교되지 않아 조용히 틀린다
    if isinstance(d, datetime):
        raise TypeError(f"date 를 넘겨야 한다(datetime 받음: {d!r})")
    return d


class TradingCalendar:
    """KRX 파생 거래일 판정.

    우선순위: override closed > override open > XKRX(범위 안) > 평일 규칙(범위 밖).
    """

    def __init__(
        self,
        *,
        extra_closed: Iterable[date] = (),
        extra_open: Iterable[date] = (),
        start: date = XKRX_START,
        end: date = XKRX_END,
    ) -> None:
        if start > end:
            raise ValueError(f"XKRX 범위가 거꾸로다: {start} > {end}")
        closed = frozenset(_as_date(d) for d in extra_closed)
        opened = frozenset(_as_date(d) for d in extra_open)
        both = closed & opened
        if both:
            raise ValueError(f"같은 날이 휴장·개장 양쪽에 있다: {sorted(both)}")
        self._start = start
        self._end = end
        self._sessions = _xkrx_sessions(start, end)
        self._closed = closed
        self._open = opened

    @classmethod
    def from_override(
        cls, override: HolidayOverride, *, start: date = XKRX_START, end: date = XKRX_END
    ) -> TradingCalendar:
        return cls(
            extra_closed=[x.date for x in override.closed],
            extra_open=[x.date for x in override.open],
            start=start,
            end=end,
        )

    @classmethod
    def default(cls) -> TradingCalendar:
        """XKRX + `config/holidays_override.yaml`. 프로세스당 한 번 만든다."""
        return _default_calendar()

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

    def next_trading_day(self, d: date) -> date:
        """d 보다 뒤(d 제외)의 첫 거래일."""
        return self._step(d, 1)

    def prev_trading_day(self, d: date) -> date:
        """d 보다 앞(d 제외)의 마지막 거래일."""
        return self._step(d, -1)

    def trading_days_between(self, a: date, b: date) -> int:
        """(a, b] 안의 거래일 수 — a == b 면 0, a > b 면 ValueError.

        확정 베이시스 이월 나이(metrics §1.3)는 `session_day` 로 옮긴 두 날 사이를 이것으로 센다
        (`core.forward.basis_age`).
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


def night_session_opens(d: date, cal: TradingCalendar) -> bool:
    """거래일 d 의 18:00~익일 06:00 야간장이 열리는가 (모듈 docstring 의 실측·가정 참고).

    금요일 밤은 늘 열리고(월요일 휴장이어도 — 2026-09-29 실측), 월~목 밤은 다음 날이 거래일일 때만.
    """
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
    nominal = _nth_weekday(year, month, _THURSDAY, 2)
    return nominal if cal.is_trading_day(nominal) else cal.prev_trading_day(nominal)


def weekly_monday_expiry(monday: date, cal: TradingCalendar) -> date:
    """월요일 위클리 최종거래일: 그 월요일, 휴장이면 **다음** 거래일로 순연 (PLAN §6.3).

    실측: KIS 단건 `futs_last_tr_date` 가 10-05(대체공휴일) 주 월요일 위클리를 2026-10-06 으로 줌.
    """
    monday = _require_weekday(monday, _MONDAY, "월요일")
    return monday if cal.is_trading_day(monday) else cal.next_trading_day(monday)


def weekly_thursday_expiry(thursday: date, cal: TradingCalendar) -> date:
    """목요일 위클리 최종거래일: 그 목요일, 휴장이면 **앞** 거래일로 당긴다.

    [확인 필요] PLAN 에 목요일 위클리의 휴장 규칙이 없어 월물과 같게 둔다.
    """
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

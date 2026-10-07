"""캘린더 조건·as_of 규칙 — 작업 등록부의 `when`·`as_of` 를 판정한다(설계 §6.4).

판정은 모두 `kbj.core.calendar` 하나에 맡긴다(정본 — PLAN §2). 한국은 `TradingCalendar.default()`
(XKRX + 덮어쓰기), 미국은 `us_calendar()`(XNYS, 날짜는 뉴욕 날짜). KRX 전용 함수
(`night_session_opens`)에 XNYS 캘린더를 넣으면 `ValueError` 다(묶음 B).

- `holds(cond, local_date, kr, us)`: 작업 시각대(`schedule.tz`)의 날짜로 판정한다. 한국 조건은 KST
  날짜, `us_trading_day` 는 뉴욕 날짜(`us.eod` 는 tz 가 America/New_York).
- `resolve_as_of(kind, now, kr, us)`: 실행 시각 → as_of 문자열(데이터 키·`ops.job_run.as_of`).
  거래일이 아닌 날의 `trade_date`, 시각으로 정할 수 없는 `event` 는 `AsOfUnavailable`.
- 장중 작업 시각은 주식 정규장(`TradingCalendar.equity_bounds` — 지연 개장 반영)을 따른다:
  `equity_anchor`(등록부 `schedule.equity`).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Final, Literal, get_args
from zoneinfo import ZoneInfo

from kbj.core.calendar import TradingCalendar, night_session_opens
from kbj.core.time import KST

__all__ = [
    "DATE_KINDS",
    "WHEN",
    "AsOfUnavailable",
    "EquityAnchor",
    "When",
    "equity_anchor",
    "holds",
    "parse_anchor",
    "prev_as_of",
    "resolve_as_of",
]

NEW_YORK: Final = ZoneInfo("America/New_York")
US_CLOSE: Final = time(16, 0)  # 미국 정규장 마감(뉴욕 벽시계)

When = Literal[
    "always",  # 매일
    "weekday",  # 월~금
    "trading_day",  # KRX 거래일(KST 날짜)
    "us_trading_day",  # XNYS 거래일(뉴욕 날짜)
    "after_us_session",  # KST 날짜의 간밤에 끝난 미국 정규장이 있다(XNYS 전날) — 화~토
    "kr_or_after_us",  # trading_day 또는 after_us_session(아침 브리핑)
    "night_session",  # 그날(KST) 밤 야간장이 열린다
    "after_night_session",  # 전날 밤 야간장이 열렸다(야간 분봉 06:10~)
    "month_days",  # schedule.month_days 의 날짜(관세청 10일 잠정치 1·11·21일 등)
]
WHEN: Final[tuple[str, ...]] = get_args(When)

# 날짜 하나로 정해지는 as_of — `depends_on.as_of: prev` 를 쓸 수 있는 종류
DATE_KINDS: Final = frozenset({"trade_date", "prev_trading_day", "us_trade_date", "run_date"})


class AsOfUnavailable(ValueError):
    """그 시각에는 그 종류의 as_of 가 없다(휴장일의 trade_date, 시각으로 못 정하는 event)."""


def holds(
    cond: str,
    local_date: date,
    kr: TradingCalendar,
    us: TradingCalendar,
    *,
    month_days: Sequence[int] = (),
) -> bool:
    """조건 `cond` 가 그 날짜(작업 시각대 기준)에 참인가. 모르는 조건은 `ValueError`."""
    if cond == "always":
        return True
    if cond == "weekday":
        return local_date.weekday() < 5
    if cond == "trading_day":
        return kr.is_trading_day(local_date)
    if cond == "us_trading_day":
        return us.is_trading_day(local_date)
    if cond == "after_us_session":
        return us.is_trading_day(local_date - timedelta(days=1))
    if cond == "kr_or_after_us":
        return kr.is_trading_day(local_date) or us.is_trading_day(local_date - timedelta(days=1))
    if cond == "night_session":
        return night_session_opens(local_date, kr)
    if cond == "after_night_session":
        return night_session_opens(local_date - timedelta(days=1), kr)
    if cond == "month_days":
        if not month_days:
            raise ValueError("when: month_days 는 schedule.month_days 가 있어야 한다")
        return local_date.day in month_days
    raise ValueError(f"모르는 when 조건: {cond!r} (받는 값 {WHEN})")


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"naive datetime 은 받지 않는다: {now!r}")
    return now


def last_us_session(now: datetime, us: TradingCalendar) -> date:
    """`now` 까지 끝난 마지막 미국 정규장의 뉴욕 날짜(16:00 ET 마감 — 조기 폐장은 보지 않는다)."""
    ny = _aware(now).astimezone(NEW_YORK)
    d = ny.date()
    if us.is_trading_day(d) and ny.time() >= US_CLOSE:
        return d
    return us.prev_trading_day(d)


def _month_back(d: date, n: int = 1) -> tuple[int, int]:
    y, m = d.year, d.month - n
    while m < 1:
        y, m = y - 1, m + 12
    return y, m


def resolve_as_of(kind: str, now: datetime, kr: TradingCalendar, us: TradingCalendar) -> str:
    """실행 시각 → as_of 문자열(설계 §6.4 표). 형식:

    날짜 `YYYY-MM-DD` · 분 `YYYY-MM-DDTHH:MM`(KST) · 10일 `YYYYMM-1|2|3` · 월 `YYYY-MM` ·
    분기 `YYYYQn`.
    """
    k = _aware(now).astimezone(KST)
    d = k.date()
    if kind == "trade_date":
        if not kr.is_trading_day(d):
            raise AsOfUnavailable(f"{d} 는 거래일이 아니다(trade_date)")
        return d.isoformat()
    if kind == "prev_trading_day":
        return kr.prev_trading_day(d).isoformat()
    if kind == "us_trade_date":
        return last_us_session(now, us).isoformat()
    if kind == "run_date":
        return d.isoformat()
    if kind == "minute":
        return k.strftime("%Y-%m-%dT%H:%M")
    if kind == "slot10m":
        return k.replace(minute=k.minute - k.minute % 10).strftime("%Y-%m-%dT%H:%M")
    if kind == "ten_day":
        # 공표된 마지막 10일 구간: 1~10일 → 전달 -3(말일까지), 11~20일 → 그달 -1, 21일~ → 그달 -2
        if d.day <= 10:
            y, m = _month_back(d)
            return f"{y:04d}{m:02d}-3"
        return f"{d:%Y%m}-{1 if d.day <= 20 else 2}"
    if kind == "month":
        y, m = _month_back(d)  # 공표 기준 월 = 지난달 [확인 필요 — 지표마다 공표 지연이 다르다]
        return f"{y:04d}-{m:02d}"
    if kind == "quarter":
        q = (d.month - 1) // 3  # 지난 분기(0 이면 작년 4분기)
        return f"{d.year - 1}Q4" if q == 0 else f"{d.year}Q{q}"
    if kind == "event":
        raise AsOfUnavailable("event as_of 는 시각으로 정하지 않는다 — 이벤트 id 를 준다(run-once)")
    raise ValueError(f"모르는 as_of 종류: {kind!r}")


def prev_as_of(kind: str, as_of: str, kr: TradingCalendar, us: TradingCalendar) -> str:
    """같은 종류의 바로 앞 as_of(`depends_on.as_of: prev`). 날짜 종류만."""
    if kind not in DATE_KINDS:
        raise ValueError(f"prev 는 날짜 as_of 만: {kind!r}")
    d = date.fromisoformat(as_of)
    if kind in ("trade_date", "prev_trading_day"):
        return kr.prev_trading_day(d).isoformat()
    if kind == "us_trade_date":
        return us.prev_trading_day(d).isoformat()
    return (d - timedelta(days=1)).isoformat()


# ── 주식 정규장 기준 시각(지연 개장 반영) ──────────────────────────────────────────────────

_ANCHOR = re.compile(r"(?P<base>open|close)(?:(?P<sign>[+-])(?P<min>[0-9]{1,3}))?")
EquityAnchor = tuple[Literal["open", "close"], int]


def parse_anchor(text: str) -> EquityAnchor:
    """`open`·`close`·`open+30`·`close-10` → (기준, 분)."""
    m = _ANCHOR.fullmatch(text.strip())
    if m is None:
        raise ValueError(f"정규장 기준 시각은 open·close[±분]: {text!r}")
    base: Literal["open", "close"] = "open" if m["base"] == "open" else "close"
    minutes = int(m["min"] or 0) * (-1 if m["sign"] == "-" else 1)
    return base, minutes


def equity_anchor(d: date, anchor: EquityAnchor, kr: TradingCalendar) -> datetime:
    """그날 정규장 시작·끝(`TradingCalendar.equity_bounds` — 연초·수능일 지연 개장 반영) ± 분."""
    start, end = kr.equity_bounds(d)
    base, minutes = anchor
    return (start if base == "open" else end) + timedelta(minutes=minutes)

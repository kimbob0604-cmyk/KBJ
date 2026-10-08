"""`calendar.json` — 오늘부터 60일의 거래일·휴장·지연 개장·만기·야간 세션(docs/p3_design.md §7.2).

- 원천은 데이터가 아니라 코드 계산이다: 거래 캘린더 정본 `kbj.core.calendar`(거래소 달력 +
  `config/holidays_override.yaml` 덮어쓰기). 공개 등급(시세가 아니다 — DATA_TIERS §2 '세션·야간
  카운트다운'은 공개 띠).
- 날짜는 KST 달력일. 주식 정규장은 지연 개장(그해 첫 거래일 10:00, 수능일 등)을 반영한
  `TradingCalendar.equity_bounds`. 만기는 `weeklies_listed`(월물 > 위클리 — 교차검증용 계산값이고
  원천은 KIS 월물리스트다, PLAN §2.3) — 그래서 만기 표시는 '계산값' 꼬리표를 notes 에 단다.
- 품질: 모든 날이 거래소 달력 범위 안이면 ok, 범위 밖 날(평일 규칙으로 판정)이 섞이면 estimated.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Final, Literal

from kbj.core.calendar import (
    DAY_END,
    DAY_START,
    EQUITY_CLOSE,
    EQUITY_OPEN,
    EXPIRY_TIME,
    NIGHT_END,
    NIGHT_START,
    TradingCalendar,
    night_session_opens,
    weeklies_listed,
)
from kbj.core.time import KST
from kbj.services.public_export.manifest import PublicFile, Quality

__all__ = ["CALENDAR_DAYS", "NAME", "SOURCE", "build_calendar", "expiries_between"]

NAME: Final = "calendar.json"
SOURCE: Final = "KBJ 거래 캘린더(코드 계산)"
CALENDAR_DAYS: Final = 60

Expiry = Literal["monthly", "weekly"]


def _hhmm(t: time) -> str:
    return t.strftime("%H:%M")


def expiries_between(start: date, end: date, cal: TradingCalendar) -> dict[date, Expiry]:
    """[start, end] 안의 만기일 → 종류. 같은 날이 월물·위클리면 월물."""
    out: dict[date, Expiry] = {}
    monday = start - timedelta(days=start.weekday())
    while monday <= end:
        w = weeklies_listed(monday, cal)
        for d in (w.monday, w.thursday):
            if d is not None and start <= d <= end:
                out.setdefault(d, "weekly")
        if w.monthly is not None and start <= w.monthly <= end:
            out[w.monthly] = "monthly"
        monday += timedelta(days=7)
    return out


def _day(d: date, cal: TradingCalendar, expiry: Expiry | None) -> dict[str, Any]:
    trading = cal.is_trading_day(d)
    row: dict[str, Any] = {
        "date": d.isoformat(),
        "weekday": d.isoweekday(),  # 1=월 … 7=일
        "trading": trading,
        "late_open": False,
        "open": None,
        "close": None,
        "expiry": expiry if trading else None,
        "night_session": night_session_opens(d, cal),
    }
    if trading:
        o, c = cal.equity_bounds(d)
        row["open"], row["close"] = _hhmm(o.time()), _hhmm(c.time())
        row["late_open"] = (o.time(), c.time()) != (EQUITY_OPEN, EQUITY_CLOSE)
    return row


def build_calendar(now: datetime, cal: TradingCalendar, *, days: int = CALENDAR_DAYS) -> PublicFile:
    """now(aware)의 KST 날짜부터 `days` 일."""
    if days < 1:
        raise ValueError("days >= 1")
    today = now.astimezone(KST).date()
    end = today + timedelta(days=days - 1)
    expiries = expiries_between(today, end, cal)
    rows = [
        _day(today + timedelta(days=i), cal, expiries.get(today + timedelta(days=i)))
        for i in range(days)
    ]
    outside = [r["date"] for r in rows if not cal.covered(date.fromisoformat(r["date"]))]
    quality: Quality = "estimated" if outside else "ok"
    notes = [
        "휴장일은 거래소 달력과 수기 덮어쓰기(config/holidays_override.yaml)로 계산한 값",
        "만기는 규칙 계산값(교차검증용) — 원천 공지와 다를 수 있다",
    ]
    if outside:
        notes.append(f"거래소 달력 범위 밖 {len(outside)}일은 평일 규칙으로만 판정(공휴일 모름)")
    data: dict[str, Any] = {
        "from": today.isoformat(),
        "to": end.isoformat(),
        "next_trading_day": cal.next_trading_day(today).isoformat(),
        "session": {
            "open": _hhmm(EQUITY_OPEN),
            "close": _hhmm(EQUITY_CLOSE),
            "tz": "Asia/Seoul",
            "derivatives_day": {"open": _hhmm(DAY_START), "close": _hhmm(DAY_END)},
            "night": {
                "open": _hhmm(NIGHT_START),
                "close": _hhmm(NIGHT_END),
                "next_day_close": True,
            },
            "expiry_time": _hhmm(EXPIRY_TIME),
        },
        "days": rows,
    }
    return PublicFile(
        name=NAME, source=SOURCE, as_of=now, quality=quality, data=data, notes=tuple(notes)
    )

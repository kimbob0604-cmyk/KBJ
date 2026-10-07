"""legacy 휴장·장중 판정의 대체 (docs/p2_design.md §1.3·§7.2) — 정본은 `kbj.core.calendar`.

legacy 함수는 이름(호출부)을 두고 본문만 이 함수 한 줄로 바꾼다(묶음 H). 여기에는 판정 규칙을
새로 두지 않는다 — `TradingCalendar.default()`(XKRX + 덮어쓰기)와 `equity_session_bounds` 만 부른다.

| legacy(원본 줄, SD f46178c) | 대체 |
|---|---|
| SD `server.py:_is_kr_holiday(dt)`:17987 | `is_kr_holiday(dt)` |
| SD `server.py:_next_trading_open_kst(now)`:17994 | `next_trading_open(now)` |
| SD `server.py:is_market_hours()`:174 | `is_kr_regular_hours(now_kst())` |
| SD `kis_api.py:_is_kr_market_hours()`:146 | `is_kr_regular_hours(now_kst())` |

legacy 와 결과가 달라지는 곳(의도 — 시험으로 고정):

- 휴장: SD 의 2026년 하드코딩(`_KR_HOLIDAYS_2026`:17971) → XKRX + `config/holidays_override.yaml`.
  2026년 평일에서 다섯 날이 달라진다(설계 §7.3, `tests/unit/core/test_legacy_holiday_parity.py`).
  2027년부터는 SD 가 모든 공휴일을 개장으로 봤다
- 장중: SD 는 휴장일을 몰랐다(평일이면 장중) → 휴장일은 장중이 아니다. 끝 경계: SD 는 15:30 분
  전체(15:30:00~15:30:59)를 장중으로 봤다(`900 <= t <= 1530`) → 정본은 [09:00, 15:30) 반열림이라
  15:30:00 부터 장 밖이다(GX 상태 경계와 같은 규칙)
- 시각: SD `kis_api._is_kr_market_hours` 는 naive `datetime.now()` 를 읽었다(UTC 서버에서 9시간
  어긋남) → aware 시각만 받는다(naive 는 `ValueError`). 벽시계는 부르는 쪽이
  `kbj.core.time.now_kst()` 로 읽는다
- 지연 개장(연초 첫 거래일·수능일 10:00)은 반영하지 않는다 — SD 도 몰랐다([확인 필요] 설계 R24)
"""

from __future__ import annotations

from datetime import date, datetime

from kbj.core.calendar import TradingCalendar, equity_session_bounds, is_equity_regular_hours
from kbj.core.time import kst_date, to_kst

__all__ = ["is_kr_holiday", "is_kr_regular_hours", "next_trading_open"]


def _cal(cal: TradingCalendar | None) -> TradingCalendar:
    return TradingCalendar.default() if cal is None else cal


def is_kr_holiday(ts: datetime | date, *, cal: TradingCalendar | None = None) -> bool:
    """그날(KST)이 KRX 휴장일(주말 포함)인가. SD `_is_kr_holiday` 대체.

    `datetime` 은 aware 만 받아 KST 날짜로 바꾼다. `date` 는 KST 날짜로 그대로 쓴다.
    `cal` 은 시험용 — 생략하면 `TradingCalendar.default()`.
    """
    d = kst_date(ts) if isinstance(ts, datetime) else ts
    return not _cal(cal).is_trading_day(d)


def next_trading_open(ts: datetime, *, cal: TradingCalendar | None = None) -> datetime:
    """ts 보다 **뒤**(같은 시각 제외)의 첫 주식 정규장 시작 KST(aware) — 보통 09:00, 지연 개장일은
    그 시각(그해 첫 거래일 10:00, 수능일 등 override `late_open`).

    SD `_next_trading_open_kst` 대체 — 그 함수도 오늘 09:00 이 now 이하면 다음 날부터 찾았다.
    """
    c = _cal(cal)
    k = to_kst(ts)
    d = k.date()
    if c.is_trading_day(d):
        opens = equity_session_bounds(d, c)[0]
        if opens > k:
            return opens
    return equity_session_bounds(c.next_trading_day(d), c)[0]


def is_kr_regular_hours(ts: datetime, *, cal: TradingCalendar | None = None) -> bool:
    """KRX 거래일의 주식 정규장 안인가(평소 [09:00, 15:30) KST, 지연 개장 반영).

    SD `is_market_hours` 계열 대체.
    """
    return is_equity_regular_hours(ts, _cal(cal))

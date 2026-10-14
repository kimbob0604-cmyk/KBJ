"""이벤트 표시(docs/metrics.md §D9 — 공개).

- 만기(`expiry`): `kbj.core.calendar` 정본 — 월물(둘째 목요일, 휴장이면 앞당김)·위클리 월(휴장이면
  순연)·
  위클리 목(월물 주에는 없음). 주마다 `weeklies_listed`. source `KBJ:calendar`, quality ok(코드
  계산).
- 금통위(`mpc`): `config/calendar_events.yaml` 의 `bok_mpc`(부르는 쪽이 날짜·제목을 넘긴다).
  quality 는
  설정 날짜를 한국은행 원 일정과 대조하지 않았으므로 estimated(공개 내보내기 events.json 과 같은
  규칙).
- 수출 속보(`export_flash`): 관세청 10일 잠정치 공표일 1·11·21일(P6 D-P6-6 — 휴일 규칙 미상 [실측
  필요 #55]
  → 달력일 그대로, estimated). 수출 속보 표가 없으면(P6 미수집) 만들지 않는다. 이미 본 구간은
  detail 에
  처음 본 시각(`seen_at`).
- 실적 집중(`earnings_cluster`): 그날 실적 일정 종목 수 ≥ `earnings_cluster_min`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Final

from kbj.config.diag import EventsCfg
from kbj.core.calendar import TradingCalendar, weeklies_listed
from kbj.core.p7_rows import EventMarker
from kbj.core.quality import Quality

__all__ = [
    "CALENDAR_SOURCE",
    "EXPORT_DAYS",
    "caution_days",
    "earnings_events",
    "expiry_events",
    "export_flash_events",
    "mpc_events",
]

CALENDAR_SOURCE: Final = "KBJ:calendar"
EXPORT_SOURCE: Final = "관세청 10일 잠정치 공표 일정(1·11·21일)"
EXPORT_DAYS: Final[tuple[int, ...]] = (1, 11, 21)


def _mondays(start: date, end: date) -> list[date]:
    d = start - timedelta(days=start.weekday())
    out: list[date] = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def expiry_events(
    start: date, end: date, cal: TradingCalendar, *, as_of: datetime
) -> list[EventMarker]:
    out: dict[tuple[date, str], EventMarker] = {}
    for mon in _mondays(start, end):
        w = weeklies_listed(mon, cal)
        for d, title, series in (
            (w.monthly, "월물 만기", "monthly"),
            (w.monday, "위클리(월) 만기", "weekly_mon"),
            (w.thursday, "위클리(목) 만기", "weekly_thu"),
        ):
            if d is None or not start <= d <= end:
                continue
            out[(d, title)] = EventMarker(
                date=d, kind="expiry", title=title, source=CALENDAR_SOURCE, as_of=as_of,
                quality=Quality.OK, detail={"series": series},
            )  # fmt: skip
    return [out[k] for k in sorted(out)]


def mpc_events(
    items: Sequence[tuple[date, str, str]], start: date, end: date, *, as_of: datetime
) -> list[EventMarker]:
    """items = (날짜, 제목, 출처) — `config/calendar_events.yaml` 의 bok_mpc."""
    return [
        EventMarker(
            date=d, kind="mpc", title=title, source=src, as_of=as_of, quality=Quality.ESTIMATED
        )
        for d, title, src in sorted(items)
        if start <= d <= end
    ]


def export_flash_events(
    start: date, end: date, *, seen: Mapping[date, datetime], available: bool, as_of: datetime
) -> list[EventMarker]:
    """seen = 공표일 → 그 구간을 처음 본 시각(KST 날짜 기준). available False 면 빈 목록(P6
    없음)."""
    if not available:
        return []
    out: list[EventMarker] = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        for dd in EXPORT_DAYS:
            d = date(y, m, dd)
            if not start <= d <= end:
                continue
            detail: dict[str, object] = {"seg": EXPORT_DAYS.index(dd)}
            if d in seen:
                detail["seen_at"] = seen[d].isoformat()
            out.append(
                EventMarker(
                    date=d, kind="export_flash", title="수출 속보(10일 잠정)", source=EXPORT_SOURCE,
                    as_of=as_of, quality=Quality.ESTIMATED, detail=detail,
                )
            )  # fmt: skip
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def earnings_events(
    counts: Mapping[date, int],
    cfg: EventsCfg,
    start: date,
    end: date,
    *,
    source: str,
    as_of: datetime,
) -> list[EventMarker]:
    return [
        EventMarker(
            date=d, kind="earnings_cluster", title=f"실적 몰림 {n}종목", source=source,
            as_of=as_of, quality=Quality.ESTIMATED, detail={"count": n},
        )
        for d, n in sorted(counts.items())
        if start <= d <= end and n >= cfg.earnings_cluster_min
    ]  # fmt: skip


def caution_days(day: date, cfg: EventsCfg, cal: TradingCalendar) -> list[date]:
    """이벤트 주의 창 — 그날과 그 뒤 `caution_days` 거래일(달력일 사이 휴일도 포함)."""
    end = day
    for _ in range(cfg.caution_days):
        end = cal.next_trading_day(end)
    return [day + timedelta(days=i) for i in range((end - day).days + 1)]

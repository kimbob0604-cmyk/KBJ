"""경제 캘린더 합치기(D-P5-8 — 순수).

- 공개 행 = 수기 공식 일정(`config/macro.yaml calendar.manual`) + FRED 발표일(`FRED_RELEASES` 에
  대응이
  있는 발표만 — 모르는 발표는 버린다 [확인 필요 #45]). source 는 공개
  출처(bok·fomc·fred·kosis·ecos·manual).
- '발표 결과'(`attach_actuals`) = 같은 시리즈가 **그 날짜 이후 공표된 값**(released_at 의 KST 날짜 ≥
  일정
  날짜, 그리고 지금 이전) 중 가장 먼저 공표된 값. 없으면 None — 지어내지 않는다.
- 예상치(ForexFactory — 로그인)는 `prv_macro.calendar` 에만 있다. `merge` 가 로그인 화면에서만 같은
  (나라·종류·날짜) 공개 행에 덧붙이고, 짝이 없는 FF 행은 따로 싣는다. 공개 빌드는
  `include_private=False`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from typing import Final

from kbj.config.macro import ManualEvent
from kbj.core.p5_rows import CalendarEvent, MacroSeriesRow, event_id
from kbj.core.quality import Quality
from kbj.core.time import KST

__all__ = [
    "FRED_RELEASES",
    "FredRelease",
    "attach_actuals",
    "fred_release_events",
    "manual_events",
    "merge",
    "window",
]


@dataclass(frozen=True)
class FredRelease:
    country: str
    kind: str
    title: str
    importance: int
    actual_series: str | None
    unit: str | None


# FRED release_name → 캘린더 행(공개). 실 이름 대조 [확인 필요 #45] — 모르는 발표는 싣지 않는다
FRED_RELEASES: Final[Mapping[str, FredRelease]] = {
    "Consumer Price Index": FredRelease(
        "US", "cpi", "미 소비자물가(CPI)", 3, "FRED:CPIAUCSL", "1982-84=100"
    ),
    "Employment Situation": FredRelease(
        "US", "payrolls", "미 고용보고서", 3, "FRED:PAYEMS", "천명"
    ),
    "Gross Domestic Product": FredRelease("US", "gdp", "미 GDP", 3, "FRED:GDPC1", "십억달러(2017)"),
    "Personal Income and Outlays": FredRelease(
        "US", "pce", "미 개인소득·지출(PCE)", 2, "FRED:PCEPILFE", "2017=100"
    ),
}


def fred_release_events(
    rows: Iterable[tuple[str, date]], *, quality: Quality = Quality.OK
) -> list[CalendarEvent]:
    """(발표 이름, 날짜) → 공개 캘린더 행(시각 모름 — None). 같은 행이 겹치면 하나만."""
    out: dict[str, CalendarEvent] = {}
    for name, d in rows:
        rel = FRED_RELEASES.get(name)
        if rel is None:
            continue
        eid = event_id(rel.country, rel.kind, d, "fred")
        out[eid] = CalendarEvent(
            eid, rel.kind, d, None, rel.title, rel.country, rel.importance, "fred", quality,
            actual_series=rel.actual_series, unit=rel.unit,
        )  # fmt: skip
    return sorted(out.values(), key=lambda e: (e.date, e.event_id))


def manual_events(items: Sequence[ManualEvent]) -> list[CalendarEvent]:
    """수기 공식 일정 → 공개 캘린더 행."""
    out: list[CalendarEvent] = []
    for m in items:
        d = date.fromisoformat(m.date)
        t = time.fromisoformat(m.time_kst) if m.time_kst else None
        out.append(
            CalendarEvent(
                event_id(m.country, m.kind, d, m.code),
                m.kind,
                d,
                t,
                m.title,
                m.country,
                m.importance,
                m.source,
                Quality.OK,
            )
        )
    return out


def attach_actuals(
    events: Sequence[CalendarEvent],
    series: Mapping[str, Sequence[MacroSeriesRow]],
    now: datetime,
) -> list[CalendarEvent]:
    """`actual_series` 가 있는 행에 그날 이후 처음 공표된 값을 붙인다(없으면 그대로 — None)."""
    if now.tzinfo is None:
        raise ValueError("now 는 시간대가 있는 시각")
    out: list[CalendarEvent] = []
    for e in events:
        rows = series.get(e.actual_series or "", ())
        hit: tuple[datetime, MacroSeriesRow] | None = None
        for r in rows:
            at = r.released_at
            if at is None or r.value is None or at > now or at.astimezone(KST).date() < e.date:
                continue
            if hit is None or at < hit[0]:
                hit = (at, r)
        if hit is None:
            out.append(e)
        else:
            out.append(replace(e, actual=hit[1].value, unit=e.unit or hit[1].unit))
    return out


def _key(e: CalendarEvent) -> tuple[str, str, date]:
    return (e.country, e.kind, e.date)


def merge(
    public: Sequence[CalendarEvent],
    private: Sequence[CalendarEvent] = (),
    *,
    include_private: bool,
) -> list[tuple[CalendarEvent, str | None]]:
    """공개 행 + (로그인이면) 예상치. 결과는 (행, 예상치 출처 또는 None) — 날짜·시각·id 순.

    로그인: 같은 (나라·종류·날짜)의 로그인 행이 있으면 그 예상치(와 단위가 비었으면 단위)를 공개
    행에 붙인다. 짝이 없는 로그인 행은 따로 싣는다. 공개(`include_private=False`)는 로그인 행을 보지
    않고, 공개 행에 예상치가 붙어 있으면 ValueError(공개 표에 예상치 칸이 없다 — 오염)."""
    if any(e.forecast is not None for e in public):
        raise ValueError("공개 캘린더 행에 예상치가 있다(D-P5-8)")
    rows: list[tuple[CalendarEvent, str | None]] = []
    used: set[str] = set()
    priv = {_key(p): p for p in private} if include_private else {}
    for e in public:
        p = priv.get(_key(e))
        if p is not None and p.forecast is not None:
            used.add(p.event_id)
            rows.append((replace(e, forecast=p.forecast, unit=e.unit or p.unit), p.source))
        else:
            rows.append((e, None))
    if include_private:
        rows += [
            (p, p.source if p.forecast is not None else None)
            for p in private
            if p.event_id not in used
        ]
    rows.sort(key=lambda x: (x[0].date, x[0].time_kst or time(0), x[0].event_id))
    return rows


def window(events: Iterable[CalendarEvent], start: date, end: date) -> list[CalendarEvent]:
    return [e for e in events if start <= e.date <= end]

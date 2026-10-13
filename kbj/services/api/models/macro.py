"""매크로·경제 캘린더 응답 모델(docs/p5_design.md §5 — `MacroTabs`·`MacroSeriesSet`·`MacroSeries`·
`EconCalendar`). 모양은 웨이브 1(FND)이 고정, 본문은 MAC 묶음(이 파일은 MAC 만 고친다 — §1.10).

- 값은 출처 공표 값(float — 표시용). 없는 값은 None(0 으로 채우지 않는다). 시리즈마다 source·as_of·
  quality(절대 규칙 1). 로그인 탭·예상치는 로그인 API 에만(공개판은 정적 JSON — §7).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "CalendarRow",
    "EconCalendar",
    "MacroPoint",
    "MacroSeries",
    "MacroSeriesInfo",
    "MacroSeriesSet",
    "MacroTab",
    "MacroTabs",
]


class MacroSeriesInfo(ApiModel):
    series_id: str
    title: str
    unit: str
    freq: Literal["D", "W", "M", "Q", "A"]
    tier: Literal["public", "private"]
    source: str  # 출처 이름(ECOS·KOSIS·FRED·TREASURY·NYFED·YAHOO)
    dataset: str  # 카탈로그 데이터셋 id
    ready: bool  # 항목 코드가 확인됐는가(아니면 '확인 전' — probe)
    probe: str | None  # 실측 체크리스트 번호(#43~#46)


class MacroTab(ApiModel):
    id: str
    title: str
    public: bool
    series: list[MacroSeriesInfo]


class MacroTabs(ApiModel):
    tabs: list[MacroTab]


class MacroPoint(ApiModel):
    period: str  # YYYY·YYYY-MM·YYYY-Qn·YYYY-MM-DD
    value: float | None
    released_at: AwareDatetime | None


class MacroSeries(ApiModel):
    series_id: str
    title: str
    unit: str
    freq: Literal["D", "W", "M", "Q", "A"]
    tier: Literal["public", "private"]
    source: str
    as_of: AwareDatetime | None
    quality: Quality | None
    latest: MacroPoint | None
    change: float | None  # 직전 공표 대비 차(단위 그대로)
    change_pct: float | None
    points: list[MacroPoint]
    derived: bool = False  # 파생 지표(metrics §16)
    na_reason: str | None = None  # '확인 전(#43)'·'공표 전' 등(값을 지어내지 않는다)


class MacroSeriesSet(ApiModel):
    tab: str
    series: list[MacroSeries]


class CalendarRow(ApiModel):
    event_id: str
    date: date
    time_kst: str | None  # HH:MM
    kind: str
    title: str
    country: str
    importance: int
    source: str
    quality: Quality
    actual: float | None  # 수집된 시리즈 값만(지어내지 않는다)
    actual_series: str | None
    unit: str | None
    forecast: float | None = None  # 로그인만(ForexFactory — 기본 꺼짐 §10 Q3)


class EconCalendar(ApiModel):
    start: date
    end: date
    rows: list[CalendarRow]

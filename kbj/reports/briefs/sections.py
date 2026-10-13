"""브리핑 섹션의 공통 틀 — 입력 묶음(`BriefCtx`)·재료 상태(`Material`)·섹션 결과(`SectionOut`)·
글자 도구.

규칙(metrics §20, docs/p5_design.md §4.5)
- 섹션 함수는 **저장소만 읽는다**(계약 ⑯ — 어댑터·수집기·API·다른 묶음 코드 import 없음, 발송 시각
  외부 호출 0). 수치는 저장소 값 그대로 — 반올림은 글자로 바꿀 때만(metrics §0).
- 재료가 없으면 지어내지 않는다: `SectionOut.not_ready` → '미준비 — <작업>(<예정 시각>)'.
- 재료마다 `Material`(기준일·quality·미준비)을 남기고 품질 줄(`quality.py`)이 모은다.
- 문장은 템플릿(LLM 없음 — 절대 규칙 3). 본문은 텔레그램 HTML(notifier 기본) — 글자는 `esc` 로
  막는다.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from kbj.config.briefs import BriefsConfig
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.time import to_kst

__all__ = [
    "WEEKDAY_KO",
    "BriefCtx",
    "Material",
    "SectionOut",
    "esc",
    "fmt_num",
    "fmt_pct",
    "mmdd",
    "worst",
]

WEEKDAY_KO = "월화수목금토일"

MaterialStatus = Literal["ok", "late", "not_ready"]

_Q_ORDER = {Quality.OK: 0, Quality.ESTIMATED: 1, Quality.STALE: 2, Quality.INVALID: 3}


@dataclass(frozen=True)
class Material:
    """브리핑 재료 하나의 상태(품질 줄 입력 — metrics §20 ⑤).

    status: ok · late(기준일이 기대보다 이르다 — 그 줄에 실제 기준일을 적었다) ·
    not_ready(시한까지 없음).
    """

    name: str
    status: MaterialStatus
    quality: Quality | None = None
    basis: date | None = None
    expected: date | None = None
    job: str | None = None
    due: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class SectionOut:
    """섹션 함수 하나의 결과. 본문이 있으면 source·as_of·quality 를 함께 준다(절대 규칙 1)."""

    lines: tuple[str, ...] = ()
    source: str | None = None
    as_of: datetime | None = None
    quality: Quality | None = None
    materials: tuple[Material, ...] = ()
    not_ready: bool = False


@dataclass(frozen=True)
class BriefCtx:
    """섹션 함수의 입력. `now` 는 주입한 시계(UTC aware), `day` 는 그 KST 날짜(실행일)."""

    repos: Any
    now: datetime
    day: date
    cfg: BriefsConfig
    kr: TradingCalendar
    us: TradingCalendar | None = None
    indices: dict[str, str] = field(default_factory=dict[str, str])  # KIS 업종 코드 → 이름
    bok_meetings: tuple[tuple[date, str], ...] = ()  # (날짜, 제목) — config/calendar_events.yaml
    importance_high: int = 6  # 공시 중요도 '높음' 경계(config/fin.yaml importance_levels.high)
    us_indexes: tuple[tuple[str, str], ...] = ()  # 간밤 미국 지수(심볼, 이름) — 비면 morning 기본값

    @property
    def now_kst(self) -> datetime:
        return to_kst(self.now)

    def prev_kr_day(self) -> date:
        return self.kr.prev_trading_day(self.day)

    def prev_us_day(self) -> date:
        """간밤 미국 거래일(뉴욕 날짜) — 실행일(KST) 직전의 미국 거래일."""
        if self.us is not None:
            return self.us.prev_trading_day(self.day)
        d = self.day - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d


def worst(qs: Sequence[Quality | None]) -> Quality | None:
    """가장 나쁜 quality(None 은 건너뛴다). 하나도 없으면 None."""
    known = [q for q in qs if q is not None]
    if not known:
        return None
    return max(known, key=lambda q: _Q_ORDER.get(q, 3))


def esc(s: object) -> str:
    """텔레그램 HTML 글자 막기(&·<·>)."""
    return html.escape(str(s), quote=False)


def mmdd(d: date) -> str:
    return f"{d.month:02d}/{d.day:02d}"


def day_label(d: date) -> str:
    return f"{d.isoformat()}({WEEKDAY_KO[d.weekday()]})"


def fmt_num(v: float | Decimal | int | None, digits: int = 2) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.{digits}f}"


def fmt_pct(v: float | None, digits: int = 2) -> str:
    if v is None:
        return ""
    return f"{v:+.{digits}f}%"


def eok(won: int | float | None) -> str:
    """원 → '+1,234억'(부호 포함). None 은 '—'."""
    if won is None:
        return "—"
    return f"{won / 100_000_000:+,.0f}억"

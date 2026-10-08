"""readers 공용 — 읽기 문맥·봉투 만들기·원장 읽기(docs/p3_design.md §1.6·§3.5·§5.2).

readers 는 **저장소 → 순수 엔진 → 응답 모델** 만 한다. 외부 원천을 부르지 않는다(D-P3-3, 계약 ⑩ —
`kbj.data.private`·`kbj.services.collectors` 를 import 하지 않는다). 계산은 엔진이 하고 여기서는 원
단위 정수로 반올림·모양 맞추기만 한다(절대 규칙 3 — 숫자를 지어내지 않는다).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Final, Protocol

from kbj.config.markets import CalendarEvents, MarketsConfig
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.flows.checks import apply_checks
from kbj.engines.flows.ledger import Ledger, build_ledger, source_label, worst_quality
from kbj.services.api.models.common import Envelope
from kbj.store.repos import BoardRepo, EtfRepo, FlowsRepo, MarketRepo

__all__ = [
    "LEDGER_WINDOW",
    "NXT_NOTE",
    "ApiRepos",
    "LedgerCache",
    "NoData",
    "ReadContext",
    "day_start",
    "envelope",
    "label",
    "ledger_days",
    "load_ledger",
    "won",
    "worst",
]

# 원장 창 — 연속 순매수를 거슬러 보는 한도(kbj.engines.flows.screen.STREAK_LOOKBACK 60)가 가장 길다
LEDGER_WINDOW: Final = 60
NXT_NOTE: Final = "NXT 미포함(KRX 구분만 — D-P3-9)"
_FALLBACK_SOURCE: Final = "KBJ"


class ApiRepos(Protocol):
    """저장소 묶음 — `kbj.store.repos.Repos`(운영)·`MemoryRepos`(시험) 둘 다 맞는다."""

    @property
    def market(self) -> MarketRepo: ...

    @property
    def flows(self) -> FlowsRepo: ...

    @property
    def board(self) -> BoardRepo: ...

    @property
    def etf(self) -> EtfRepo: ...


class NoData(Exception):
    """데이터가 아직 없다 → 404 `no_data`(화면 '아직 없음 — <작업> <예정 시각>')."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LedgerCache:
    """원장 캐시 — 키 (끝 날, 데이터 버전 토큰).

    원장을 만드는 데 전 종목 × 60영업일 행을 고르므로(2,700종목이면 수 초) 같은 데이터 버전
    안에서는 한 번만 만든다. 토큰이 없으면 캐시하지 않는다. 동시에 들어온 미적중 요청이 원장을
    두 번 만들지 않게(그리고 dict 축출이 겹치지 않게) 잠금 하나로 지킨다.
    """

    def __init__(self, token: Callable[[], str | None], max_entries: int = 4) -> None:
        self._token = token
        self._max = max_entries
        self._items: dict[tuple[date, str], Ledger] = {}
        self._lock = threading.Lock()

    def get(self, repos: ApiRepos, end: date) -> Ledger:
        tok = self._token()
        if tok is None:
            return load_ledger(repos, end, LEDGER_WINDOW)
        key = (end, tok)
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                hit = load_ledger(repos, end, LEDGER_WINDOW)
                if len(self._items) >= self._max:
                    self._items.pop(next(iter(self._items)))
                self._items[key] = hit
            return hit


@dataclass(frozen=True)
class ReadContext:
    repos: ApiRepos
    now: Callable[[], datetime]
    cal: TradingCalendar
    markets: MarketsConfig
    events: CalendarEvents
    ledgers: LedgerCache | None = None

    def ledger(self, end: date) -> Ledger:
        """end 이하 최근 `LEDGER_WINDOW` 영업일 원장(검산 적용). 모든 readers 가 같은 창을 쓴다 —
        기간 값(1·5·20일·연속일 60일)은 엔진이 원장 안에서 자른다."""
        if self.ledgers is None:
            return load_ledger(self.repos, end, LEDGER_WINDOW)
        return self.ledgers.get(self.repos, end)

    def now_kst(self) -> datetime:
        n = self.now()
        if n.tzinfo is None or n.utcoffset() is None:
            raise ValueError("now() 는 시간대가 있는 시각이어야 한다")
        return n.astimezone(KST)

    def today(self) -> date:
        return self.now_kst().date()

    def venue_notes(self) -> list[str]:
        return [NXT_NOTE] if tuple(self.markets.kis.venues) == ("KRX",) else []


def day_start(d: date) -> datetime:
    """그날 00:00 KST(장중 표 조회의 시작)."""
    return datetime.combine(d, time(0, 0), tzinfo=KST)


def won(v: float | None) -> int | None:
    """원 단위 정수(반올림). 화면은 억·조로만 바꾼다."""
    return None if v is None else round(v)


def worst(qs: Iterable[Quality | None]) -> Quality | None:
    return worst_quality(q for q in qs if q is not None)


def label(sources: Iterable[str]) -> str:
    """원천 표기(`KRX+KIS` 등). 비면 'KBJ'(계산값 — 구성 원천이 없을 때만)."""
    return source_label(sources) or _FALLBACK_SOURCE


def envelope[T](
    data: T,
    *,
    source: str,
    as_of: datetime,
    quality: Quality | None,
    notes: Iterable[str],
    generated_at: datetime,
) -> Envelope[T]:
    """봉투. quality 를 정할 값이 하나도 없으면 invalid(지어내지 않는다)."""
    return Envelope(
        source=source or _FALLBACK_SOURCE,
        as_of=as_of,
        quality=quality if quality is not None else Quality.INVALID,
        notes=list(dict.fromkeys(n for n in notes if n)),
        generated_at=generated_at,
        data=data,
    )


def ledger_days(repos: ApiRepos, end: date, n: int) -> list[date]:
    """end 이하 원장 영업일 n 개(오름차순) — 일봉이 있는 날 ∪ 마지막 스냅 날(오늘 KIS 마감)."""
    if n < 1:
        raise ValueError("n 은 1 이상")
    days = set(repos.market.trading_days(end, n))
    _, snap_day = repos.market.snapshot(end)
    if snap_day is not None:
        days.add(snap_day)
    return sorted(days)[-n:]


def load_ledger(repos: ApiRepos, end: date, n: int) -> Ledger:
    """원장(검산 ①② 적용) — 저장소에서 스냅·일봉·투자자·유니버스를 읽어 엔진이 고른다(D-P3-7)."""
    days = ledger_days(repos, end, n)
    if not days:
        raise NoData("아직 없음 — krx.daily 08:05 · market.close_collect 15:35")
    start, last = days[0], days[-1]
    snaps = [s for d in days for s in repos.market.snapshots(d)]
    bars = [b for rows in repos.market.series(None, last, n).values() for b in rows]
    bars = [b for b in bars if start <= b.date <= last]
    invs = [r for rows in repos.flows.days(None, start, last).values() for r in rows]
    uni = repos.market.universe(last)
    ledger, _ = apply_checks(build_ledger(snaps, bars, invs, uni, days))
    return ledger


def ledger_quality(ledger: Ledger, day: date) -> Quality | None:
    """그날 쓸 수 있는 원장 행 중 가장 나쁜 품질."""
    return worst(r.quality for r in ledger.day(day) if r.usable)


def slot_end(ts: datetime, minutes: int = 10) -> datetime:
    return ts + timedelta(minutes=minutes)

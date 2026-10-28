"""readers 공용 — 읽기 문맥·봉투 만들기·원장 읽기(docs/p3_design.md §1.6·§3.5·§5.2).

readers 는 **저장소 → 순수 엔진 → 응답 모델** 만 한다. 외부 원천을 부르지 않는다(D-P3-3, 계약 ⑩ —
`kbj.data.private`·`kbj.services.collectors` 를 import 하지 않는다). 계산은 엔진이 하고 여기서는 원
단위 정수로 반올림·모양 맞추기만 한다(절대 규칙 3 — 숫자를 지어내지 않는다).

P4(docs/p4_design.md §5 — 묶음 A): 저장소 묶음에 `filings`·`fin`·`consensus`·`journal`, 읽기 문맥에
공시·재무 설정(`config/fin.yaml`·`config/reports.yaml`)을 더했다. 모르는 종목코드는 `UnknownCode`
(404 `unknown_code` — 유니버스에도 DART 고유번호 표에도 없다, §5.2).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Final, Protocol

from kbj.config.fin import FinConfig, ReportsConfig
from kbj.config.markets import CalendarEvents, MarketsConfig
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.flows.checks import apply_checks
from kbj.engines.flows.ledger import Ledger, build_ledger, source_label, worst_quality
from kbj.services.api.models.common import Envelope
from kbj.store.repos import (
    BoardRepo,
    ConsensusRepo,
    DiagPublicRepo,
    DiagRepo,
    EtfRepo,
    FilingsRepo,
    FinRepo,
    FlowsRepo,
    GexRepo,
    IndexReviewRepo,
    JournalRepo,
    MacroPrivateRepo,
    MacroPublicRepo,
    MarketRepo,
    MarketStatsRepo,
    OptPosRepo,
    PersonalRepo,
    ProgramRepo,
    RulesRepo,
    ThemePublicRepo,
    ThemeRepo,
    TradeRepo,
    UsRepo,
)

__all__ = [
    "LEDGER_WINDOW",
    "NXT_NOTE",
    "ApiRepos",
    "LedgerCache",
    "NoData",
    "ReadContext",
    "UnknownCode",
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

    # P4(묶음 M 이 `Repos`·`MemoryRepos` 에 더한 것 — 공시·재무는 공개 표, 컨센서스·관심종목은
    # 로그인)
    @property
    def filings(self) -> FilingsRepo: ...

    @property
    def fin(self) -> FinRepo: ...

    @property
    def consensus(self) -> ConsensusRepo: ...

    @property
    def journal(self) -> JournalRepo: ...

    # P5(docs/p5_design.md §1.1·§5 — 웨이브 1 FND 가 `Repos`·`MemoryRepos` 에 더한 것).
    # 공개(macro·market_stats)·로그인(macro_prv·us·program) — API 는 로그인이라 둘 다 읽는다
    @property
    def macro(self) -> MacroPublicRepo: ...

    @property
    def macro_prv(self) -> MacroPrivateRepo: ...

    @property
    def market_stats(self) -> MarketStatsRepo: ...

    @property
    def us(self) -> UsRepo: ...

    @property
    def program(self) -> ProgramRepo: ...

    # P5 2차 묶음 IDX(0018 — 지수 정기변경 예측, 로그인)
    @property
    def index_review(self) -> IndexReviewRepo: ...

    # P6 묶음 FND(0019 — 수출입·매핑, 공개)
    @property
    def trade(self) -> TradeRepo: ...

    # P7 묶음 FND-B(0006 prv_gex — GEX, 0020 prv_diag·pub_diag — 진단). API 는 로그인이라
    # 둘 다 읽는다
    @property
    def gex(self) -> GexRepo: ...

    @property
    def diag(self) -> DiagRepo: ...

    @property
    def diag_pub(self) -> DiagPublicRepo: ...

    # P8 묶음 FND(0021 prv_alerts·prv_journal — 로그인). 규칙·신호·성과·백테스트 읽기는 rules,
    # 일지·매매·추천 읽기는 journal. 개인 기록 쓰기는 personal(쓰기 모듈 하나가 부른다 — D-P8-15)
    @property
    def rules(self) -> RulesRepo: ...

    @property
    def personal(self) -> PersonalRepo: ...
    # 페이지 9(0023 — 대테마 분류·편입·종목 마스터, ADR 0039)
    @property
    def theme(self) -> ThemeRepo: ...
    @property
    def theme_pub(self) -> ThemePublicRepo: ...
    # 옵션 행사가별 추정 포지션(0030 — ADR 0044)
    @property
    def opt_pos(self) -> OptPosRepo: ...


class NoData(Exception):
    """데이터가 아직 없다 → 404 `no_data`(화면 '아직 없음 — <작업> <예정 시각>')."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnknownCode(Exception):
    """모르는 종목코드 → 404 `unknown_code`(유니버스(전 거래일)에도 DART 고유번호 표에도 없다 —
    §5.2).

    문구에 코드를 싣지 않는다(입력값을 응답에 되돌리지 않는다 — P3 422 규칙과 같다)."""


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
    # P4 — 공시·재무·컨센서스·밸류 튜닝값(config/fin.yaml)·dart-report 설정(config/reports.yaml).
    # create_app 이 읽어 넣는다(시험은 덮어쓴 값 — 완료 시험의 consensus.top_n=0 등)
    fin: FinConfig | None = None
    reports: ReportsConfig | None = None

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

    def fin_config(self) -> FinConfig:
        if self.fin is None:
            raise RuntimeError(
                "공시·재무 설정(config/fin.yaml)이 주입되지 않았다 — create_app 이 넣는다"
            )
        return self.fin

    def reports_config(self) -> ReportsConfig:
        if self.reports is None:
            raise RuntimeError(
                "리포트 설정(config/reports.yaml)이 주입되지 않았다 — create_app 이 넣는다"
            )
        return self.reports


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

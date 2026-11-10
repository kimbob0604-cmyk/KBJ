"""첫 화면 묶음 reader — `/api/home`(docs/metrics.md §32, ADR 0043).

기존 읽기 함수를 불러 카드마다 필요한 몇 줄만 고른다 — 값을 다시 계산하지 않는다(두 벌 금지).
새 값은 급등·급락(metrics §6.8 — `/api/market/movers` 와 같은 함수 `market_movers`), 시장별 상승·
하락·N일선 위 비율(§6.2 — 시장폭 엔진 `market_breadth`·`ma_breadth`), 선물 전일 대비 등락률(§6.9 —
`futures_prev_settle`)이다. 장중 프로그램 누적은 `/api/program/intraday` 와 같은 함수다.

- **카드 격리**(절대 규칙 4): 카드마다 따로 부르고 따로 잡는다. `NoData` → 그 카드 `no_data`(서버
  안내 문구 그대로), 그 밖의 오류 → 그 카드 `error`(사유 코드 — 본문·내부 문구 없음) + 로그. 다른
  카드는 그대로 만든다. 카드 안 보조 값(예: 지수 카드의 장중 투자자)이 실패하면 그 칸만 비우고
  notes·로그에 남긴다.
- 여러 카드가 쓰는 읽기(market/summary·gex/levels)는 한 요청 안에서 한 번만 부른다(`_Reads` — 실패도
  기억해 카드마다 다시 올린다).
- **개인 데이터 없음**: 관심종목·규칙 신호·공시 중요도(importance·login_score — 관심종목 가산)·
  잠정실적 alerted 는 고르지 않는다. 공시는 키워드 점수(kw_score — 공개 규칙)로 고른다.
- 캐시 영역 `HOME_DOMAINS` — 묶음이 부르는 경로들의 영역 전부(라우트가 버전을 이어 붙인다 —
  `deps.cached_json`). 시험이 `CARD_KEYS` 경로의 영역이 모두 들어 있는지 본다.
"""

from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final, Literal, NoReturn, cast

from redis.exceptions import RedisError

from kbj.core.masking import mask_text
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.filings.earnings import SignalSide, signal_side
from kbj.engines.flows.ledger import close_as_of
from kbj.engines.market.breadth import (
    MA_HISTORY_DAYS,
    NEWHIGH_KINDS,
    MarketMaBreadth,
    ma_breadth,
    market_breadth,
)
from kbj.engines.market.movers import MIN_MKTCAP_WON
from kbj.engines.market.ribbon import EXPORT_FLASH_NONE
from kbj.services.api.models.common import FUTURE_AS_OF_NOTE, Envelope
from kbj.services.api.models.filings import PrelimView
from kbj.services.api.models.flows import (
    IntradayRow,
    InvestorTotals,
    MarketMinute,
    ScreenResponse,
)
from kbj.services.api.models.gex import GexLevels
from kbj.services.api.models.home import (
    Home,
    HomeBreadth,
    HomeCard,
    HomeCards,
    HomeDiag,
    HomeEtf,
    HomeEtfRow,
    HomeExport,
    HomeFilingRow,
    HomeFilings,
    HomeFlowRow,
    HomeForeignInst,
    HomeFutures,
    HomeGex,
    HomeIndex,
    HomeIndexDay,
    HomeIndexPoint,
    HomeIndices,
    HomeInvestorNet,
    HomeMaBreadth,
    HomeMaShare,
    HomeNewhigh,
    HomeNewhighRow,
    HomePrelimRow,
    HomeProgram,
    HomeProgramNow,
    HomeSpike,
    HomeSpikeRow,
    HomeThemes,
    HomeUpDown,
)
from kbj.services.api.models.market import IndexTile, MarketMovers, MarketSummary
from kbj.services.api.models.program import ProgramDaily, ProgramDayBar, ProgramIntraday
from kbj.services.api.readers._common import NoData, ReadContext, UnknownCode, envelope, worst
from kbj.services.api.readers.board import EOK_NOTE, board_newhigh, row_label
from kbj.services.api.readers.diag import diag_summary
from kbj.services.api.readers.etf import etf_flows, etf_types
from kbj.services.api.readers.filings import filings_earnings, filings_feed
from kbj.services.api.readers.flows import flows_intraday, flows_screen
from kbj.services.api.readers.flows_minute import market_minute
from kbj.services.api.readers.gex import futures_prev_settle, gex_levels
from kbj.services.api.readers.gex_briefing import gex_briefing
from kbj.services.api.readers.market import (
    NIGHT_CODE,
    export_flash,
    market_movers,
    market_summary,
)
from kbj.services.api.readers.program import NO_DAILY, program_daily, program_intraday
from kbj.services.api.readers.theme import theme_strength
from kbj.store.db import StoreError

__all__ = ["CARD_KEYS", "CARD_PAGES", "HOME_DOMAINS", "HOME_WORKERS", "home", "home_has_error"]

log = logging.getLogger(__name__)

# 묶음이 부르는 경로들의 캐시 영역(라우트 `cached_json` 이 버전을 이어 붙인다)
HOME_DOMAINS: Final[tuple[str, ...]] = (
    "market", "flows", "flows_minute", "board", "gex", "diag", "filings", "etf", "themes",
    "program",
)  # fmt: skip
# 카드 → '더 보기' 페이지 번호
CARD_PAGES: Final[Mapping[str, int]] = {
    "indices": 1, "futures": 1, "filings": 5, "breadth": 1, "spike": 4, "movers": 4,
    "foreign_inst": 4, "newhigh": 2, "gex": 3, "diag": 3, "program": 1, "etf": 10, "themes": 9,
    "export": 7,
}  # fmt: skip
# 카드 → 같은 값을 주는 기존 경로('/api/' 뒤 — 쿼리 포함). 시험이 이 경로들의 캐시 영역을 모은다
CARD_KEYS: Final[Mapping[str, tuple[str, ...]]] = {
    "indices": (
        "market/summary", "flows/market-minute?market=kospi",
        "flows/market-minute?market=kosdaq", "flows/investors",
    ),
    "futures": ("gex/levels", "flows/market-minute?market=k200_fut", "market/summary"),
    "filings": ("filings/feed", "filings/earnings"),
    "breadth": ("market/summary",),
    "spike": ("flows/screen?mode=spike&period=1",),
    "movers": ("market/movers",),
    "foreign_inst": (
        "flows/intraday", "flows/screen?mode=foreign&period=1&min_avg_turnover=0",
        "flows/screen?mode=inst&period=1&min_avg_turnover=0",
    ),
    "newhigh": ("board/newhigh",),
    "gex": ("gex/levels", "gex/briefing"),
    "diag": ("diag/summary",),
    "program": (
        "program/daily?market=K", "program/daily?market=Q", "program/intraday?market=K",
        "program/intraday?market=Q",
    ),
    "etf": ("etf/types?period=5", "etf/flows?mode=in&period=5"),
    "themes": ("themes/strength?period=1",),
    "export": ("market/ribbon",),  # 수출 속보 칩과 같은 함수(상세는 trade/flash — 페이지 7)
}  # fmt: skip

# 카드를 나눠 만드는 일꾼 수 — 읽기마다 DB 연결을 새로 열어 기다림이 섞인다(맥 실측 2026-11-10
# 원장 캐시 뒤: 1개 3.5초·4개 3.3초·8개 2.7초 — docs/probe_results.md). 공유 읽기는 `_Reads` 가
# 키마다 한 번
HOME_WORKERS: Final = 6
TOP_FILINGS: Final = 8
TOP_PRELIMS: Final = 4
TOP_SPIKE: Final = 10
TOP_FLOWS: Final = 5
TOP_NEWHIGH: Final = 8
TOP_ETF: Final = 3
TOP_THEMES: Final = 5
ETF_PERIOD: Final = 5  # 페이지 10 기본 기간
THEME_PERIOD: Final = 1  # 페이지 9 기본 기간
PROGRAM_DAYS: Final = 5  # 마지막 날이 검산 실패면 그 앞날
ALL_ROWS: Final = 1_000_000  # 거르기 전 전부(라우트 상한은 화면용 — 묶음은 고르기 전 모집단이 필요)
EOK: Final = 100_000_000  # 억원 → 원
# KIS 1분 투자자 카드 → 투자자 구분(metrics §21)
MINUTE_WHO: Final[Mapping[str, str]] = {
    "individual": "prsn", "foreign": "frgn", "institution": "orgn", "other_corp": "etc_corp",
}  # fmt: skip
# (탭, 지수 코드, 마감 일별 by_market 키 — KIS 업종 코드 또는 시장 이름)
SPOT: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("kospi", "0001", ("0001", "KOSPI")),
    ("kosdaq", "1001", ("1001", "KOSDAQ")),
)
FUT_TAB: Final = "k200_fut"
FUT_NO_SETTLE_NOTE: Final = (
    "선물 전일 대비 등락률 없음 — 직전 거래일 KRX 정산가가 아직 없다(gex.krx_derivatives)"
)
FUT_CHG_NOTE: Final = (
    "선물 등락률 = 근월물 직전 거래일 KRX 정산가 대비(야간 타일은 주간 마지막 값 대비)"
)
UPDOWN_NOTE: Final = "상승·보합·하락(마감 확정) — 띠는 코스피+코스닥 합산, 지수 카드는 그 시장만"
MA_NOTE: Final = "N일선 위 비율 = 종가가 최근 N영업일 평균보다 높은 종목 ÷ 평균을 낼 수 있는 종목"
MA_MARKETS: Final[tuple[Literal["KOSPI", "KOSDAQ"], ...]] = ("KOSPI", "KOSDAQ")
INDEX_SOURCE: Final = "KIS"  # 장중 지수 10분 값(market.intraday — flows/market-minute 겹침)
NO_INDICES: Final = "아직 없음 — krx.daily 08:05 · market.intraday(장중 10분)"
NO_LEDGER: Final = "아직 없음 — market.close_collect 15:35 · krx.daily 08:05(원장)"
NO_FUTURES: Final = "아직 없음 — gex.engine(장중 사이클)·flows.market_minute(정규장 매분)"
ERROR_NOTE: Final[Mapping[str, str]] = {
    "unavailable": "이 카드를 만들지 못했다 — 저장소 장애(다시 불러오면 될 수 있다)",
    "unknown_code": "이 카드를 만들지 못했다 — 설정에 없는 시장",
    "internal": "이 카드를 만들지 못했다 — 서버 내부 오류(로그에 남겼다)",
}


# ── 공용 ───────────────────────────────────────────────────────────────────────────────────


@dataclass
class _Meta:
    """카드 봉투 재료 — 고른 값들의 원천·기준 시각·품질과 메모."""

    sources: list[str] = field(default_factory=list[str])
    as_ofs: list[datetime] = field(default_factory=list[datetime])
    qualities: list[Quality | None] = field(default_factory=list[Quality | None])
    notes: list[str] = field(default_factory=list[str])

    def env(self, e: Envelope[Any], *, notes: bool = True) -> None:
        self.value(e.source, e.as_of, e.quality)
        if notes:
            self.notes.extend(e.notes)

    def value(self, source: str | None, as_of: datetime, quality: Quality | None) -> None:
        if source:
            self.sources.append(source)
        self.as_ofs.append(as_of)
        self.qualities.append(quality)


def _join(sources: Iterable[str]) -> str:
    """원천 표기 합치기('+' 로 나눈 조각을 처음 나온 순서대로 한 번씩). 계산값('KBJ')은 다른
    원천이 있으면 뺀다."""
    parts = list(dict.fromkeys(p for s in sources for p in s.split("+") if p))
    real = [p for p in parts if p != "KBJ"]
    return "+".join(real or parts) or "KBJ"


def _error_code(e: BaseException) -> str:
    if isinstance(e, StoreError | RedisError):
        return "unavailable"
    if isinstance(e, UnknownCode):
        return "unknown_code"
    return "internal"


def _log_error(what: str, e: BaseException) -> None:
    """오류를 삼키지 않는다 — 종류·가린 문구(비밀 형태 마스킹)·호출 위치만 남긴다(절대 규칙 5)."""
    if isinstance(e, StoreError):
        log.error("첫 화면 %s — 저장소 오류: %s", what, e.cause or type(e).__name__)
    elif isinstance(e, RedisError):
        log.error("첫 화면 %s — Redis 오류: %s", what, type(e).__name__)
    else:
        where = mask_text("".join(traceback.format_tb(e.__traceback__)[-4:]))
        log.error("첫 화면 %s — %s\n%s", what, mask_text(f"{type(e).__name__}: {e}")[:300], where)


@dataclass
class _Parts:
    """카드 안 여러 값(부분)의 결과 — 없으면(NoData) None + 안내, 오류면 None + 메모·로그(그
    칸만 비운다). 부분이 하나도 없으면 `nothing()` — 오류가 있었으면 그 오류(카드 error), 아니면
    NoData."""

    notes: list[str] = field(default_factory=list[str])
    errors: list[Exception] = field(default_factory=list[Exception])

    def get[T](self, what: str, fn: Callable[[], T], *, no_data_note: bool = True) -> T | None:
        try:
            return fn()
        except NoData as e:
            if no_data_note:
                self.notes.append(f"{what}: {e.message}")
            return None
        except Exception as e:  # 격리 — 삼키지 않는다(메모·로그)
            _log_error(what, e)
            self.errors.append(e)
            self.notes.append(f"{what} 오류 — 그 칸만 비웠다({_error_code(e)})")
            return None

    def nothing(self, message: str) -> NoReturn:
        if self.errors:
            raise self.errors[0]
        raise NoData(message)


class _Reads:
    """한 요청 안에서 여러 카드가 쓰는 읽기는 한 번만. 실패(NoData·오류)도 기억해 쓰는 카드마다 다시
    올린다 — 카드마다 따로 no_data·error 가 된다. 카드를 여러 일꾼이 함께 만들어 키마다 잠금 하나로
    지킨다(같은 읽기를 두 번 하지 않게)."""

    def __init__(self, ctx: ReadContext) -> None:
        self.ctx = ctx
        self._memo: dict[str, object] = {}
        self._fail: dict[str, Exception] = {}
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _once[T](self, key: str, fn: Callable[[], T]) -> T:
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            if key in self._fail:
                raise self._fail[key]
            if key not in self._memo:
                try:
                    self._memo[key] = fn()
                except Exception as e:
                    self._fail[key] = e
                    raise
            return cast(T, self._memo[key])

    def summary(self) -> Envelope[MarketSummary]:
        return self._once("market/summary", lambda: market_summary(self.ctx))

    def levels(self) -> Envelope[GexLevels]:
        return self._once("gex/levels", lambda: gex_levels(self.ctx, scope="all"))


def _card[T](
    cls: type[HomeCard[T]], name: str, fn: Callable[[], tuple[T, _Meta]], ctx: ReadContext
) -> HomeCard[T]:
    base: dict[str, Any] = {"page": CARD_PAGES[name], "keys": list(CARD_KEYS[name])}
    try:
        data, meta = fn()
    except NoData as e:
        return cls(status="no_data", message=e.message, **base)
    except Exception as e:  # 카드 격리 — 이 카드만 error(코드), 내용은 로그(절대 규칙 4)
        code = _error_code(e)
        _log_error(f"카드 {name}", e)
        return cls(status="error", error=code, notes=[ERROR_NOTE[code]], **base)
    now = ctx.now()  # 카드를 다 만든 뒤 — 읽기 함수가 쓴 시각보다 앞서지 않게
    as_of = max(meta.as_ofs, default=now)
    notes = list(meta.notes)
    if as_of > now:  # 봉투와 같은 규칙 — 장중 슬롯 끝(아직 오지 않은 시각)은 응답 시각으로
        as_of = now.astimezone(as_of.tzinfo)
        notes.append(FUTURE_AS_OF_NOTE)
    return cls(
        status="ok",
        source=_join(meta.sources),
        as_of=as_of,
        quality=worst(meta.qualities) or Quality.INVALID,
        notes=list(dict.fromkeys(n for n in notes if n)),
        data=data,
        **base,
    )


def _net(m: Mapping[str, int | None], key: str) -> int | None:
    v = m.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _minute_net(env: Envelope[MarketMinute]) -> HomeInvestorNet | None:
    """1분 투자자 응답 → 순매수(마지막 분 누적). 꺼진 탭·카드 없음이면 None. 장중 잠정은
    estimated."""
    d = env.data
    if d.status == "not_confirmed" or not d.cards:
        return None
    by = {c.investor: c.net_value for c in d.cards}
    q = worst([env.quality, Quality.ESTIMATED if d.provisional else None]) or Quality.INVALID
    return HomeInvestorNet(
        basis="minute",
        date=d.trade_date,
        source=env.source,
        as_of=env.as_of,
        quality=q,
        **{k: _net(by, who) for k, who in MINUTE_WHO.items()},
    )


def _daily_net(t: InvestorTotals, keys: Sequence[str]) -> HomeInvestorNet | None:
    """마감 일별 시장 투자자 → 그 시장 순매수. by_market 에 그 시장이 없으면 None."""
    m = next((t.by_market[k] for k in keys if k in t.by_market), None)
    if m is None:
        return None
    return HomeInvestorNet(
        basis="daily",
        date=t.date,
        individual=_net(m, "individual"),
        foreign=_net(m, "foreign"),
        institution=_net(m, "institution"),
        other_corp=_net(m, "other_corp"),
        source=t.source,
        as_of=close_as_of(t.date),
        quality=t.quality,
    )


def _breadth_notes(notes: Iterable[str]) -> list[str]:
    """시장폭 메모 중 이 카드와 상관있는 것 — 신고가 비율 메모는 뺀다(신고가는 '신고가' 카드)."""
    return [n for n in notes if "신고가" not in n]


def _newer(minute: HomeInvestorNet | None, daily: HomeInvestorNet | None) -> HomeInvestorNet | None:
    """1분 누적이 마감 일별보다 새 날이면 그것, 아니면 마감 일별(같은 날이면 확정을 쓴다)."""
    if minute is not None and (daily is None or minute.date > daily.date):
        return minute
    return daily


def _index_day(env: Envelope[MarketMinute]) -> HomeIndexDay | None:
    """1분 투자자 응답의 지수 겹침(10분 슬롯 자리에만 값) → 값이 있는 점만. 없으면 None."""
    d = env.data
    idx = d.index
    if idx is None or idx.as_of is None or idx.quality is None:
        return None
    pts = [
        HomeIndexPoint(t=t, v=v) for t, v in zip(d.axis, idx.values, strict=False) if v is not None
    ]
    if not pts:
        return None
    return HomeIndexDay(
        date=d.trade_date, points=pts, source=INDEX_SOURCE, as_of=idx.as_of, quality=idx.quality
    )


def _updown_by_market(ctx: ReadContext) -> dict[str, HomeUpDown]:
    """시장별 상승·보합·하락(metrics §6.2) — market/summary 와 같은 원장·같은 함수(`market_breadth`
    의 markets 인자). 신고가는 여기서 쓰지 않아 보드 라벨을 넘기지 않는다."""
    ledger = ctx.ledger(ctx.today())
    day = ledger.last_day
    if day is None:
        return {}
    out: dict[str, HomeUpDown] = {}
    for mk in MA_MARKETS:
        b = market_breadth(
            ledger, day, (), limit_move_pct=ctx.markets.market.limit_move_pct, markets=(mk,)
        )
        out[mk] = HomeUpDown(
            date=b.date, up=b.up, flat=b.flat, down=b.down, unknown=b.unknown, total=b.total,
            source=b.source or "KBJ", quality=b.quality,
        )  # fmt: skip
    return out


# ── 카드 ───────────────────────────────────────────────────────────────────────────────────


def _indices(r: _Reads) -> tuple[HomeIndices, _Meta]:
    ctx = r.ctx
    summ = r.summary()
    d = summ.data
    m = _Meta(notes=[*ctx.venue_notes()])
    parts = _Parts(notes=m.notes)
    tiles = {t.code: t for t in d.indices}
    items: dict[str, HomeIndex | None] = {}
    by_market = (
        parts.get("시장별 상승·하락", lambda: _updown_by_market(ctx), no_data_note=False)
        if d.breadth is not None
        else None
    ) or {}
    for (tab, code, keys), mk in zip(SPOT, MA_MARKETS, strict=True):
        t = tiles.get(code)
        if t is None:
            items[tab] = None
            m.notes.append(f"지수 {code} 자료 없음")
            continue
        m.value(t.source, t.as_of, t.quality)
        minute = parts.get(
            f"{t.name or code} 장중 투자자",
            lambda tab=tab: market_minute(ctx, market=tab, venue="KRX"),
            no_data_note=False,
        )
        daily = _daily_net(d.investors, keys) if d.investors is not None else None
        inv = _newer(_minute_net(minute) if minute is not None else None, daily)
        if inv is None:
            m.notes.append(f"{t.name or code} 투자자 순매수 없음")
        else:
            m.value(inv.source, inv.as_of, inv.quality)
            if inv.basis == "daily" and d.investors is not None:
                m.notes.extend(d.investors.notes)
        items[tab] = HomeIndex(
            tile=t,
            investors=inv,
            today=_index_day(minute) if minute is not None else None,
            updown=by_market.get(mk),
        )
    b = d.breadth
    updown: HomeUpDown | None = None
    if b is not None:
        updown = HomeUpDown(
            date=b.date, up=b.up, flat=b.flat, down=b.down, unknown=b.unknown, total=b.total,
            source=b.source, quality=b.quality,
        )  # fmt: skip
        m.value(b.source, close_as_of(b.date), b.quality)
        m.notes += [UPDOWN_NOTE, *_breadth_notes(b.notes)]
    if items["kospi"] is None and items["kosdaq"] is None and updown is None:
        raise NoData(NO_INDICES)
    return HomeIndices(kospi=items["kospi"], kosdaq=items["kosdaq"], breadth=updown), m


def _futures(r: _Reads) -> tuple[HomeFutures, _Meta]:
    ctx = r.ctx
    parts = _Parts()
    lv = parts.get("선물 가격(GEX)", r.levels)
    minute = parts.get("선물 투자자", lambda: market_minute(ctx, market=FUT_TAB, venue="KRX"))
    summ = parts.get("야간선물", r.summary, no_data_note=False)
    night: IndexTile | None = None
    if summ is not None:
        night = next((t for t in summ.data.indices if t.code == NIGHT_CODE), None)
    inv = _minute_net(minute) if minute is not None else None
    if lv is None and inv is None and night is None:
        parts.nothing(NO_FUTURES)
    m = _Meta(notes=list(parts.notes))
    if lv is not None:
        m.env(lv)
    if inv is not None and minute is not None:
        m.value(inv.source, inv.as_of, inv.quality)
        m.notes.extend(minute.notes)
    if night is not None:
        m.value(night.source, night.as_of, night.quality)
    g = lv.data if lv is not None else None
    settle = None
    if g is not None and g.price is not None:
        settle = parts.get(
            "선물 전일 정산가", lambda: futures_prev_settle(ctx, g.trade_date), no_data_note=False
        )
        if settle is None:
            m.notes.append(FUT_NO_SETTLE_NOTE)
        else:
            m.value("KRX", close_as_of(settle.date), Quality.OK)
            m.notes.append(FUT_CHG_NOTE)
    m.notes.extend(n for n in parts.notes if n not in m.notes)
    chg = (
        (g.price / settle.price - 1) * 100
        if g is not None and g.price is not None and settle is not None and settle.price > 0
        else None
    )
    data = HomeFutures(
        trade_date=g.trade_date if g else None,
        session=g.session if g else None,
        session_open=g.session_open if g else None,
        cycle_at=g.cycle_at if g else None,
        price=g.price if g else None,
        price_quality=g.price_quality if g else None,
        stale=g.stale if g else None,
        prev_settle=settle.price if settle is not None else None,
        prev_settle_date=settle.date if settle is not None else None,
        chg_pct=chg,
        night=night,
        investors=inv,
    )
    return data, m


def _filings_day(ctx: ReadContext) -> date:
    """오늘이 거래일이면 오늘, 아니면 직전 거래일(주말·휴일 첫 화면이 비지 않게)."""
    today = ctx.today()
    return today if ctx.cal.is_trading_day(today) else ctx.cal.prev_trading_day(today)


def _latest_prelims(rows: Sequence[PrelimView]) -> tuple[list[PrelimView], int]:
    """정정공시는 원 공시를 대체 — 같은 (회사, 기간, 연결/별도) 는 가장 늦은 접수번호만.

    §4.1 — 페이지 5·8 화면의 `latestPrelims` 와 같은 규칙(안 거르면 원 공시와 정정이 두 줄로
    세어졌다). 돌려주는 것: (남은 행, 뺀 수).
    """
    last: dict[tuple[str, str, str], str] = {}
    for p in rows:
        k = (p.corp_code, p.period, p.scope)
        if k not in last or p.rcept_no > last[k]:
            last[k] = p.rcept_no
    kept = [p for p in rows if last[(p.corp_code, p.period, p.scope)] == p.rcept_no]
    return kept, len(rows) - len(kept)


def _filings(r: _Reads) -> tuple[HomeFilings, _Meta]:
    ctx = r.ctx
    day = _filings_day(ctx)
    feed = filings_feed(
        ctx, day=day, kind=None, level=None, market=None, watch_only=False, limit=ALL_ROWS,
        listed_only=True,
    )  # fmt: skip
    m = _Meta()
    m.env(feed)
    scored = [v for v in feed.data.rows if not v.is_correction and (v.kw_score or 0) > 0]
    scored.sort(key=lambda v: (v.kw_score or 0, v.first_seen_at, v.rcept_no), reverse=True)
    top = [
        HomeFilingRow(
            rcept_no=v.rcept_no, first_seen_at=v.first_seen_at, corp_name=v.corp_name,
            stock_code=v.stock_code, title=v.title, kind=v.kind, kw_score=v.kw_score or 0,
            kw_matched=list(v.kw_matched), link=v.link,
        )
        for v in scored[:TOP_FILINGS]
    ]  # fmt: skip
    earn = filings_earnings(ctx, start=day, end=day)
    prelims, superseded = _latest_prelims(earn.data.rows)
    if prelims:
        m.env(earn)
    if superseded:
        m.notes.append(
            f"정정으로 대체된 원 공시 {superseded}건은 세지 않는다"
            "(같은 회사·기간·범위는 마지막 접수만)"
        )
    sided: list[tuple[SignalSide, PrelimView]] = []
    for p in prelims:
        side = signal_side(p.signal) if p.scope != "subsidiary" else None
        if side is not None:
            sided.append((side, p))

    def mag(p: PrelimView) -> float:
        v = p.op_yoy if p.op_yoy is not None else p.rev_yoy
        return abs(v) if v is not None else -1.0

    picked = sorted(sided, key=lambda x: x[1].rcept_no, reverse=True)  # 같으면 최근 접수 먼저
    picked.sort(key=lambda x: (x[1].priority if x[1].priority is not None else 9, -mag(x[1])))
    rows = [
        HomePrelimRow(
            rcept_no=p.rcept_no, corp_name=p.corp_name, stock_code=p.stock_code, label=p.label,
            scope=p.scope, signal=p.signal or "", side=side, priority=p.priority,
            revenue_cur=p.revenue_cur, op_cur=p.op_cur, rev_yoy=p.rev_yoy, op_yoy=p.op_yoy,
            is_correction=p.is_correction, quality=p.quality, link=p.link,
        )
        for side, p in picked[:TOP_PRELIMS]
    ]  # fmt: skip
    if day < ctx.today():
        m.notes.append(f"오늘은 휴장 — {day.month}/{day.day} 공시")
    if sided:
        m.notes.append("서프·쇼크 = 전년동기 대비 방향(컨센서스 대비 아님 — 분기 컨센서스 없음)")
    data = HomeFilings(
        date=day,
        n_total=feed.data.n_total,
        by_kind=dict(feed.data.by_kind),
        top=top,
        n_prelim=len(prelims),
        n_surprise=sum(1 for s, _ in sided if s == "surprise"),
        n_shock=sum(1 for s, _ in sided if s == "shock"),
        prelims=rows,
    )
    return data, m


def _ma_by_market(ctx: ReadContext) -> list[MarketMaBreadth]:
    """시장별 20·60·120일선 위 비율(metrics §6.2) — 원장 + 121일 일봉 이력. 원장 데이터 버전 안에서
    한 번만 계산한다(일봉 이력 읽기가 수 초 — 결과만 둔다)."""
    ledger = ctx.ledger(ctx.today())
    day = ledger.last_day
    if day is None:
        return []

    def build() -> list[MarketMaBreadth]:
        hist = ctx.repos.market.series(None, day, MA_HISTORY_DAYS)
        return ma_breadth(ledger, day, hist, markets=MA_MARKETS)

    return ctx.memo("home.ma_breadth", day, build)


def _breadth(r: _Reads) -> tuple[HomeBreadth, _Meta]:
    b = r.summary().data.breadth
    if b is None:
        raise NoData(NO_LEDGER)
    m = _Meta(notes=[*_breadth_notes(b.notes)])
    m.value(b.source, close_as_of(b.date), b.quality)
    got = _Parts(notes=m.notes).get("시장별 N일선", lambda: _ma_by_market(r.ctx)) or []
    rows: list[HomeMaBreadth] = []
    for x in got:
        if x.date != b.date or x.market not in MA_MARKETS:
            continue
        m.value(x.source, close_as_of(x.date), x.quality)
        m.notes.extend(x.notes)
        rows.append(
            HomeMaBreadth(
                market=cast(Literal["KOSPI", "KOSDAQ"], x.market),
                date=x.date,
                shares=[
                    HomeMaShare(ma=s.ma, above=s.above, base=s.base, pct=s.pct) for s in x.shares
                ],
                source=x.source or "KBJ",
                quality=x.quality,
            )
        )
    m.notes.append(MA_NOTE)
    data = HomeBreadth(
        date=b.date, above_ma20_pct=b.above_ma20_pct, above_ma20_n=b.above_ma20_n,
        ma20_base=b.ma20_base, by_market=rows,
    )  # fmt: skip
    return data, m


def _close_note(ctx: ReadContext, day: date | None, what: str) -> list[str]:
    """마감 값이 오늘보다 앞이면 기준일 — 오늘이 거래일(장중·마감 수집 전)이면 사유도."""
    today = ctx.today()
    if day is None or day >= today:
        return []
    why = f" — {what}" if ctx.cal.is_trading_day(today) else ""
    return [f"{day.month}/{day.day} 마감 기준{why}"]


def _spike(r: _Reads) -> tuple[HomeSpike, _Meta]:
    ctx = r.ctx
    env = flows_screen(
        ctx, mode="spike", market="all", period=1, min_avg_turnover=None, include_flagged=False,
        limit=ALL_ROWS, share_class="common",
    )  # fmt: skip
    d = env.data
    m = _Meta()
    m.env(env, notes=False)
    m.notes += [n for n in env.notes if not n.startswith("검산")]  # 투자자 검산은 이 카드 밖
    m.notes += _close_note(ctx, d.date, "장중 급증은 아직 없다(확정 원장)")
    rows = [
        HomeSpikeRow(
            code=x.code, name=x.name, market=x.market, spike_mult=x.spike_mult,
            turnover=x.turnover_sum, chg_pct=x.chg_pct, mktcap=x.mktcap, quality=x.quality,
        )
        for x in d.rows[:TOP_SPIKE]
    ]  # fmt: skip
    data = HomeSpike(
        date=d.date, min_turnover=d.min_avg_turnover, spike_min=ctx.markets.screen.spike_min,
        n_hits=len(d.rows), rows=rows,
    )  # fmt: skip
    return data, m


def _movers(r: _Reads) -> tuple[MarketMovers, _Meta]:
    env = market_movers(r.ctx, min_mktcap=MIN_MKTCAP_WON)
    m = _Meta()
    m.env(env)
    return env.data, m


def _foreign_inst(r: _Reads) -> tuple[HomeForeignInst, _Meta]:
    """마감 원장 날이 오늘이면 마감, 아니면 오늘 장중 가집계가 있으면 그것, 없으면 마감(직전)."""
    ctx = r.ctx
    today = ctx.today()
    parts = _Parts()

    def screen(mode: Literal["foreign", "inst"]) -> Envelope[ScreenResponse]:
        return flows_screen(
            ctx, mode=mode, market="all", period=1, min_avg_turnover=0, include_flagged=False,
            limit=TOP_FLOWS, share_class="common",
        )  # fmt: skip

    close_f = parts.get("외국인(마감)", lambda: screen("foreign"))
    close_i = parts.get("기관(마감)", lambda: screen("inst"))
    days = [e.data.date for e in (close_f, close_i) if e is not None and e.data.date is not None]
    close_day = max(days, default=None)
    if close_day is None or close_day < today:
        live = parts.get("장중 가집계", lambda: flows_intraday(ctx), no_data_note=False)
        if live is not None and live.data.slot.astimezone(KST).date() == today:
            m = _Meta()
            m.env(live)

            def lrows(rows: Iterable[IntradayRow]) -> list[HomeFlowRow]:
                return [
                    HomeFlowRow(code=x.code, name=x.name, market=None, value=x.value,
                                chg_pct=None, mktcap=x.mktcap)
                    for x in rows
                    if x.value is not None and x.value > 0
                ][:TOP_FLOWS]  # fmt: skip

            d = live.data
            data = HomeForeignInst(
                basis="intraday", date=today, foreign=lrows(d.top_foreign), inst=lrows(d.top_inst)
            )
            return data, m
    if close_day is None:
        parts.nothing(NO_LEDGER)

    def crows(env: Envelope[ScreenResponse] | None, who: str) -> list[HomeFlowRow]:
        if env is None:
            return []
        out: list[HomeFlowRow] = []
        for x in env.data.rows:
            v = x.foreign if who == "foreign" else x.inst
            if v is not None and v > 0:
                out.append(HomeFlowRow(code=x.code, name=x.name, market=x.market, value=v,
                                       chg_pct=x.chg_pct, mktcap=x.mktcap))  # fmt: skip
        return out

    m = _Meta(notes=list(parts.notes))
    for e in (close_f, close_i):
        if e is not None:
            m.env(e)
    m.notes += _close_note(ctx, close_day, "오늘 장중 가집계가 아직 없다")
    data = HomeForeignInst(
        basis="close",
        date=close_day,
        foreign=crows(close_f, "foreign"),
        inst=crows(close_i, "inst"),
    )
    return data, m


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _newhigh(r: _Reads) -> tuple[HomeNewhigh, _Meta]:
    env = board_newhigh(r.ctx, day=None, basis="close", kind=None, min_turnover_eok=None)
    d = env.data
    m = _Meta()
    m.env(env, notes=False)
    m.notes += [n for n in env.notes if n != EOK_NOTE]  # 억원 값은 원으로 바꿨다
    rows: list[tuple[float, str, HomeNewhighRow]] = []
    for x in d.achieved:
        lab = row_label(x, "close")
        code = x.get("code")
        if lab not in NEWHIGH_KINDS or not isinstance(code, str):
            continue
        cap = _num(x.get("mktcap"))
        name, market = x.get("name"), x.get("market")
        row = HomeNewhighRow(
            code=code,
            name=name if isinstance(name, str) else None,
            market=market if isinstance(market, str) else None,
            label=lab,
            chg_pct=_num(x.get("chg_pct")),
            mktcap=None if cap is None else round(cap * EOK),
        )
        rows.append((cap if cap is not None else -1.0, code, row))
    rows.sort(key=lambda t: (-t[0], t[1]))  # 시총 큰 순(모르면 뒤) → 코드
    data = HomeNewhigh(
        date=env.as_of.astimezone(KST).date(),
        counts=dict(d.counts_close),
        labels=dict(d.labels),
        rows=[t[2] for t in rows[:TOP_NEWHIGH]],
    )
    return data, m


def _level(levels: Iterable[Any], name: str) -> float | None:
    return next((x.value for x in levels if x.name == name), None)


def _gex(r: _Reads) -> tuple[HomeGex, _Meta]:
    lv = r.levels()
    g = lv.data
    m = _Meta()
    m.env(lv)
    br = _Parts(notes=m.notes).get("감마 국면", lambda: gex_briefing(r.ctx, scope="all"))
    b = br.data if br is not None else None
    if br is not None:
        m.value(br.source, br.as_of, br.quality)
    data = HomeGex(
        trade_date=g.trade_date,
        session=g.session,
        session_open=g.session_open,
        cycle_at=g.cycle_at,
        stale=g.stale,
        price=g.price,
        regime=b.regime if b is not None else None,
        regime_label=b.regime_label if b is not None else None,
        headline=b.headline if b is not None else None,
        flip=_level(g.levels, "flip"),
        flip_distance=_level(g.levels, "flip_distance"),
        expected_move=_level(g.levels, "expected_move_calendar"),
    )
    return data, m


def _diag(r: _Reads) -> tuple[HomeDiag, _Meta]:
    env = diag_summary(r.ctx, day=None)
    d = env.data
    m = _Meta()
    m.env(env)
    data = HomeDiag(
        trade_date=d.trade_date, verdict=d.verdict, fear_greed=d.fear_greed, caution=d.caution
    )
    return data, m


def _last_bar(env: Envelope[ProgramDaily] | None) -> ProgramDayBar | None:
    """마지막 날(검산 ④ 실패 — invalid — 면 그 앞날)."""
    if env is None:
        return None
    return next((b for b in reversed(env.data.rows) if b.quality is not Quality.INVALID), None)


def _program_now(
    bar: ProgramDayBar | None, live: Envelope[ProgramIntraday] | None
) -> HomeProgramNow | None:
    """장중 마지막 분 누적이 마감 일별보다 새 날이면 그것, 아니면 마감 일별(metrics §22)."""
    p = live.data.last if live is not None else None
    if live is not None and p is not None and (bar is None or live.data.trade_date > bar.date):
        return HomeProgramNow(
            basis="intraday", date=live.data.trade_date, as_of=p.ts, net=p.net,
            arbt_net=p.arbt_net, nabt_net=p.nabt_net, quality=p.quality,
        )  # fmt: skip
    if bar is None:
        return None
    return HomeProgramNow(
        basis="daily", date=bar.date, as_of=close_as_of(bar.date), net=bar.net,
        arbt_net=bar.arbt_net, nabt_net=bar.nabt_net, quality=bar.quality,
    )  # fmt: skip


def _program(r: _Reads) -> tuple[HomeProgram, _Meta]:
    ctx = r.ctx
    parts = _Parts()
    k = parts.get("코스피 프로그램", lambda: program_daily(ctx, market="K", days=PROGRAM_DAYS))
    q = parts.get("코스닥 프로그램", lambda: program_daily(ctx, market="Q", days=PROGRAM_DAYS))
    kl = parts.get("코스피 장중 프로그램", lambda: program_intraday(ctx, market="K"))
    ql = parts.get("코스닥 장중 프로그램", lambda: program_intraday(ctx, market="Q"))
    kb, qb = _last_bar(k), _last_bar(q)
    kn, qn = _program_now(kb, kl), _program_now(qb, ql)
    if kn is None and qn is None:
        parts.nothing(NO_DAILY)
    m = _Meta(notes=list(parts.notes))
    for env, bar in ((k, kb), (q, qb)):
        if env is not None and bar is not None:
            m.value(env.source, close_as_of(bar.date), bar.quality)
            m.notes.extend(env.notes)
    for env, now in ((kl, kn), (ql, qn)):
        if env is not None and now is not None and now.basis == "intraday":
            m.value(env.source, now.as_of, now.quality)
            m.notes.extend(env.notes)
    last = max(n.date for n in (kn, qn) if n is not None)
    m.notes += _close_note(ctx, last, "오늘 장중 프로그램 누적이 아직 없다")
    return HomeProgram(kospi=kb, kosdaq=qb, kospi_now=kn, kosdaq_now=qn), m


def _etf(r: _Reads) -> tuple[HomeEtf, _Meta]:
    ctx = r.ctx
    types = etf_types(ctx, period=ETF_PERIOD)
    m = _Meta()
    m.env(types)
    flows = _Parts(notes=m.notes).get(
        "ETF 종목 순유입",
        lambda: etf_flows(ctx, mode="in", etf_type=None, period=ETF_PERIOD, limit=TOP_ETF),
    )
    top: list[HomeEtfRow] = []
    if flows is not None:
        m.env(flows)
        top = [
            HomeEtfRow(
                code=x.code, name=x.name, etf_type_label=x.etf_type_label,
                net_inflow=x.net_inflow, ret_pct=x.ret_pct, quality=x.quality,
            )
            for x in flows.data.rows[:TOP_ETF]
        ]  # fmt: skip
    d = types.data
    data = HomeEtf(
        period=d.period,
        start=d.start,
        end=d.end,
        types=sorted(d.rows, key=lambda t: (-t.net_inflow, t.etf_type)),
        top=top,
    )
    return data, m


def _export(r: _Reads) -> tuple[HomeExport, _Meta]:
    fc = export_flash(r.ctx, r.ctx.now_kst())
    if fc is None:
        raise NoData(EXPORT_FLASH_NONE)
    m = _Meta(notes=["관세청 10일 잠정치 — 발표 당시 값(전년 같은 구간 대비)"])
    m.value(fc.source, fc.first_seen_at, fc.quality)
    if fc.yoy is None and fc.yoy_reason:
        m.notes.append(fc.yoy_reason)
    data = HomeExport(
        ym=fc.ym, seg=fc.seg, span_text=fc.span_text, span_label=fc.label, usd=fc.usd,
        yoy=fc.yoy, yoy_reason=fc.yoy_reason, first_seen_at=fc.first_seen_at,
    )  # fmt: skip
    return data, m


def _themes(r: _Reads) -> tuple[HomeThemes, _Meta]:
    env = theme_strength(r.ctx, period=THEME_PERIOD)
    m = _Meta()
    m.env(env)
    d = env.data
    return HomeThemes(trade_date=d.trade_date, period=d.period, rows=d.rows[:TOP_THEMES]), m


# ── 묶음 ───────────────────────────────────────────────────────────────────────────────────


def home_has_error(model: object) -> bool:
    """오류 카드가 있는 묶음인가(라우트가 그 본문을 캐시에 두지 않는다 — 다음 요청이 다시
    만든다)."""
    data = getattr(model, "data", None)
    return isinstance(data, Home) and data.n_error > 0


def home(ctx: ReadContext) -> Envelope[Home]:
    """카드 14장 — 여러 일꾼이 나눠 만들고(읽기마다 DB 연결을 새로 열어 기다림이 길다) 순서는
    `HomeCards` 칸 순서 그대로다."""
    r = _Reads(ctx)
    specs: list[tuple[str, type[HomeCard[Any]], Callable[[], tuple[Any, _Meta]]]] = [
        ("indices", HomeCard[HomeIndices], lambda: _indices(r)),
        ("futures", HomeCard[HomeFutures], lambda: _futures(r)),
        ("filings", HomeCard[HomeFilings], lambda: _filings(r)),
        ("breadth", HomeCard[HomeBreadth], lambda: _breadth(r)),
        ("spike", HomeCard[HomeSpike], lambda: _spike(r)),
        ("movers", HomeCard[MarketMovers], lambda: _movers(r)),
        ("foreign_inst", HomeCard[HomeForeignInst], lambda: _foreign_inst(r)),
        ("newhigh", HomeCard[HomeNewhigh], lambda: _newhigh(r)),
        ("gex", HomeCard[HomeGex], lambda: _gex(r)),
        ("diag", HomeCard[HomeDiag], lambda: _diag(r)),
        ("program", HomeCard[HomeProgram], lambda: _program(r)),
        ("etf", HomeCard[HomeEtf], lambda: _etf(r)),
        ("themes", HomeCard[HomeThemes], lambda: _themes(r)),
        ("export", HomeCard[HomeExport], lambda: _export(r)),
    ]
    with ThreadPoolExecutor(max_workers=HOME_WORKERS, thread_name_prefix="kbj-home") as ex:
        futs = {name: ex.submit(_card, cls, name, fn, ctx) for name, cls, fn in specs}
        built: dict[str, HomeCard[Any]] = {name: f.result() for name, f in futs.items()}
    cards = HomeCards(**built)
    every: list[tuple[str, HomeCard[Any]]] = [
        (k, getattr(cards, k)) for k in HomeCards.model_fields
    ]
    now = ctx.now()  # 카드를 다 만든 뒤(응답 시각)
    ok = [c for _, c in every if c.status == "ok"]
    no_data = [k for k, c in every if c.status == "no_data"]
    errors = [k for k, c in every if c.status == "error"]
    notes: list[str] = []
    if no_data:
        notes.append(f"아직 없는 카드 {len(no_data)}개: {', '.join(no_data)}")
    if errors:
        notes.append(f"오류 카드 {len(errors)}개: {', '.join(errors)} — 그 카드만 비웠다")
    data = Home(
        date=now.astimezone(KST).date(),
        cards=cards,
        n_ok=len(ok),
        n_no_data=len(no_data),
        n_error=len(errors),
    )
    return envelope(
        data,
        source=_join(c.source for c in ok if c.source),
        as_of=max((c.as_of for c in ok if c.as_of is not None), default=now),
        quality=worst(c.quality for c in ok),
        notes=notes,
        generated_at=now,
    )

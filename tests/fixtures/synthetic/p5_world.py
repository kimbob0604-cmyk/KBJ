"""합성 P5 세계 `p5_world` — P5 묶음(MAC·ETF·BRF·US·F2·WRAP)이 같이 쓰는 고정 시드 합성 데이터.

근거: docs/p5_design.md §1.1(FND 표 `p5_world` 행)·§8·부록 A(metrics §16~§23).
로그인 등급(KIS·KRX·운용사·Yahoo·Nasdaq) 실데이터 fixture 는 쓰지 않는다(CLAUDE.md 절대 규칙) — 이
생성기가 `random.Random(f"{seed}:{이름}")` 으로 결정적으로 만든다(같은 시드 → 같은 `digest()`).
**실데이터 아님** — `records()` 가 내는 모든 기록의 머리 칸 `_source` 가 `SYNTHETIC` 으로 시작한다.
행 객체(`kbj.core.p5_rows`·`kbj.core.rows`)의 `source` 칸은 저장소·DB 제약이 받는 출처 이름(ECOS·KIS
…)을 쓰지만 값은 전부 합성이다.

쓰는 법
    from tests.fixtures.synthetic.p5_world import build_world
    w = build_world()                       # 시드 20261113(캐시 — 같은 객체)
    w.bars("997010")                        # kbj.core.rows.Bar(krx) — PRICE_FROM ~ LAST_KRX_DAY
    w.series("FRED:DGS10")                  # MacroSeriesRow(합성 값)
    w.minutes("k200_fut")                   # InvestorMinute(백만원 — 오늘 09:00~15:30 391분)
    w.program_checks()                      # 검산 ①~④ 차이 수(합성 원장 — 전부 0)

무엇이 있나(설계 §1.1)
- 종목 24개(`9970xx` — P3 가짜 대역 `990xxx`·P3 ETF `995xxx`·`996xxx`·`998xxx`·P4 `994xxx` 와 겹치지
  않는다) + KRX 일봉(PRICE_FROM ~ LAST_KRX_DAY — 테마 강도 1Y 입력).
- 매크로: 공개 7·로그인 3 시리즈(id 는 config/macro.yaml 의 것, 값은 합성) — 공표 지연(회사채 AA- 의
  마지막 날이 아직 없음)·결측(DGS2 하루 '.' → value None·invalid). 캘린더 7건(공개 6 + 로그인
  예상치 1).
- (분류 사전 조각은 뺐다 — 테마·밸류체인 사전 통합은 사용자가 따로 진행, 2026-10-13 Q1)
- ETF 8개(`9975xx` — 유형·레버리지·액티브·신규 상장) 일별·구성종목 두 기준일(동시 편입 심음).
- 미국: 유니버스 12(펀드 1·이력 짧음 1)·일봉·지수 2, 간밤 신고가 심음(`US_DAY`).
- 금투협 통계(credit_balance·market_capital·fund_nav·cma) — 신용잔고 마지막 날 공표 지연.
- F2: 1분 누적(시장 5 × 12구분 × 391분 — 백만원, 검산 ①~③ 이 정확히 맞게), 프로그램 일별 60일·장중
  분·투자자별·종목(scan 슬롯 + sweep 마감) — 검산 ④ 정확히.
- 브리핑 재료: TODAY 아침에 일부 늦거나 없는 날, 전날은 다 있는 날.

가짜 시계: `CLOCK` = 2026-11-17(화) 16:30 KST(하루 운영 시뮬레이션 창 — 설계 §8.4). KRX 확정 마지막
날은
`LAST_KRX_DAY` = 11-16(월). 간밤 미국 거래일 `US_DAY` = 11-16(뉴욕).

[확인 필요] 실측 전의 모양: 현물 1분 조합 코드(#39 — 여기서는 `SYNK`·`SYNQ`·`SYN0` 자리표시),
프로그램 TR 단위(#40·#41 — 원으로 둠), 사전 원본 yaml 모양(THM). 실측으로 바뀌면 이 파일 한 곳을
고친다(FND 담당 — 다른 묶음은 요청).
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import cache
from typing import Any, Final, Literal

from kbj.core.calendar import TradingCalendar
from kbj.core.p5_rows import (
    MINUTE_INVESTORS,
    CalendarEvent,
    InvestorMinute,
    MacroSeriesMeta,
    MacroSeriesRow,
    MarketStatRow,
    ProgramInvestorRow,
    ProgramMarketRow,
    ProgramStockRow,
    UsDailyRow,
    UsUniverseRow,
)
from kbj.core.quality import Quality
from kbj.core.rows import Bar, EtfDay, EtfType, HoldingRow
from kbj.core.time import KST

__all__ = [
    "CLOCK",
    "LAST_KRX_DAY",
    "MINUTE_PAIRS",
    "SEED",
    "SYNTHETIC",
    "TODAY",
    "US_DAY",
    "Material",
    "MinutePair",
    "P5World",
    "WorldEtf",
    "WorldStock",
    "build_world",
]

SEED: Final = 20261113
SYNTHETIC: Final = (
    "SYNTHETIC — tests/fixtures/synthetic/p5_world.py(고정 시드 생성기)가 만든 합성 데이터. "
    "실측 아님"
)
TODAY: Final = date(2026, 11, 17)
CLOCK: Final = datetime(2026, 11, 17, 16, 30, tzinfo=KST)
LAST_KRX_DAY: Final = date(2026, 11, 16)
US_DAY: Final = date(2026, 11, 16)
PRICE_FROM: Final = date(2025, 9, 1)
MACRO_DAYS: Final = 60  # 일별 시리즈 영업일 수
MACRO_MONTHS: Final = 36  # 월별 시리즈 달 수
PROGRAM_DAYS: Final = 60  # metrics §22 일별 막대(markets.yaml program.daily_days)
SCAN_N: Final = 10  # 장중 종목 훑기 = 거래대금 상위 N + 관심종목(합성 세계 크기에 맞춘 값)
US_LOOKBACK: Final = 252
_MIN_PER_DAY: Final = 391  # 09:00~15:30
_OPEN: Final = time(9, 0)

# ── 종목·ETF·미국 심볼 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorldStock:
    code: str
    name: str
    market: Literal["KOSPI", "KOSDAQ"]
    sector: str
    base_price: float

    def record(self) -> dict[str, Any]:
        return {"_source": SYNTHETIC, "kind": "stock", **asdict(self)}


_SECTORS: Final = ("반도체", "전력설비", "제약", "화장품", "자동차", "은행")
_NAMES: Final = (
    "합성소재", "합성부품", "합성기판", "합성장비", "합성검사", "합성세트",  # 반도체 체인
    "합성설계", "합성주기기", "합성배관", "합성계측", "합성건설",  # 원전 체인
    "합성바이오A", "합성바이오B", "합성바이오C", "합성바이오D",  # 개발 단계
    "합성원료", "합성ODM", "합성브랜드",  # 화장품
    "합성로봇", "합성신약", "합성자동차", "합성은행", "합성부품2", "합성모빌리티",
)  # fmt: skip
STOCK_CODES: Final[tuple[str, ...]] = tuple(f"997{n:03d}" for n in range(10, 250, 10))
_SECTOR_OF: Final[tuple[int, ...]] = (
    0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 4, 2, 4, 5, 0, 4,
)  # fmt: skip


@dataclass(frozen=True)
class WorldEtf:
    code: str
    name: str
    etf_type: EtfType
    leverage: float | None
    theme: str | None
    active: bool
    listed_on: date
    fund_id: str

    def record(self) -> dict[str, Any]:
        return {"_source": SYNTHETIC, "kind": "etf", **asdict(self)}


@dataclass(frozen=True)
class Material:
    """브리핑 재료 하나의 그날 상태(BRF — metrics §20). status: ok·late(as_of 가 기대보다 이르다)·
    missing(재료 없음 — 시한까지 미준비)."""

    day: date
    name: str
    job: str
    expected_as_of: date
    as_of: datetime | None
    quality: Quality | None
    status: Literal["ok", "late", "missing"]

    def record(self) -> dict[str, Any]:
        return {"_source": SYNTHETIC, "kind": "brief_material", **asdict(self)}


@dataclass(frozen=True)
class MinutePair:
    """1분 수집 조합(markets.yaml flows.minute.pairs 의 id). 현물 둘은 코드가 [확인 필요 #39] 라
    합성 자리표시 코드를 쓴다."""

    id: str
    market_code: str
    sector_code: str
    spot: bool


MINUTE_PAIRS: Final[tuple[MinutePair, ...]] = (
    MinutePair("kospi", "SYNK", "SYN0", True),
    MinutePair("kosdaq", "SYNQ", "SYN0", True),
    MinutePair("k200_fut", "K2I", "F001", False),
    MinutePair("k200_call", "K2I", "OC01", False),
    MinutePair("k200_put", "K2I", "OP01", False),
)
_ORGN_PARTS: Final = ("scrt", "ivtr", "pe_fund", "bank", "insu", "mrbn", "fund")
# 검산 ① 의 항(현물): prsn + frgn + orgn + etc_corp + etc_orgt = 0
_CHECK1_TERMS: Final = ("prsn", "frgn", "orgn", "etc_corp", "etc_orgt")

# 매크로(id 는 config/macro.yaml — 값은 합성). (id, tab, title, unit, freq, tier, source, dataset)
_MACRO: Final[tuple[tuple[str, str, str, str, str, str, str, str], ...]] = (
    ("FRED:DGS10", "us", "미 국채 10년", "%", "D", "public", "FRED", "FRED:series"),
    ("FRED:DGS2", "us", "미 국채 2년", "%", "D", "public", "FRED", "FRED:series"),
    ("FRED:DGS3MO", "us", "미 국채 3개월", "%", "D", "public", "FRED", "FRED:series"),
    ("ECOS:722Y001:base", "rates", "한국은행 기준금리", "%", "D", "public", "ECOS", "ECOS:722Y001"),
    ("ECOS:817Y002:ktb3y", "rates", "국고채 3년", "%", "D", "public", "ECOS", "ECOS:817Y002"),
    ("ECOS:817Y002:corp_aa3y", "rates", "회사채 3년 AA-", "%", "D", "public", "ECOS",
     "ECOS:817Y002"),
    ("KOSIS:DT_1J22003:cpi", "prices", "소비자물가지수", "2020=100", "M", "public", "KOSIS",
     "KOSIS:DT_1J22003"),
    ("YAHOO:KRW=X", "market", "원/달러(Yahoo)", "원", "D", "private", "YAHOO", "YAHOO:macro"),
    ("FRED:VIXCLS", "market", "VIX", "pt", "D", "private", "FRED", "FRED:restricted_series"),
    ("ECOS:901Y056:deposit", "market", "투자자예탁금", "십억원", "M", "private", "ECOS",
     "ECOS:901Y056"),
)  # fmt: skip
_MACRO_BASE: Final[dict[str, float]] = {
    "FRED:DGS10": 4.30, "FRED:DGS2": 3.95, "FRED:DGS3MO": 4.10, "ECOS:722Y001:base": 2.50,
    "ECOS:817Y002:ktb3y": 2.60, "ECOS:817Y002:corp_aa3y": 3.20, "KOSIS:DT_1J22003:cpi": 116.0,
    "YAHOO:KRW=X": 1385.0, "FRED:VIXCLS": 17.0, "ECOS:901Y056:deposit": 55000.0,
}  # fmt: skip
MISSING_DGS2_OFFSET: Final = 3  # 끝에서 몇 번째 미국 영업일에 DGS2 가 '.'(결측)인가
WATCH: Final[tuple[str, ...]] = ("997230", "997240")  # 장중 훑기 범위에 더하는 관심종목(합성)


def _rng(seed: int, name: str) -> random.Random:
    return random.Random(f"{seed}:{name}")  # noqa: S311 — 합성 데이터(보안 무관)


def _dec(v: float) -> Decimal:
    return Decimal(f"{v:.2f}")


@cache
def _cal() -> TradingCalendar:
    return TradingCalendar.default()


def _kr_days(start: date, end: date) -> tuple[date, ...]:
    cal = _cal()
    out: list[date] = []
    d = start
    while d <= end:
        if cal.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


# 미국 휴장(합성 근사 — 고정 목록. 실제 NYSE 달력과 다를 수 있다)
_US_HOLIDAYS: Final = frozenset({
    date(2025, 11, 27), date(2025, 12, 25), date(2026, 1, 1), date(2026, 1, 19),
    date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3),
    date(2026, 9, 7),
})  # fmt: skip


def _us_days(start: date, end: date) -> tuple[date, ...]:
    out: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in _US_HOLIDAYS:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


def _minutes(day: date) -> tuple[datetime, ...]:
    start = datetime.combine(day, _OPEN, tzinfo=KST)
    return tuple(start + timedelta(minutes=i) for i in range(_MIN_PER_DAY))


def _row(kind: str, obj: object) -> dict[str, Any]:
    body = asdict(obj)  # pyright: ignore[reportArgumentType]
    return {"_source": SYNTHETIC, "kind": kind, **body}


# ── 세계 ──────────────────────────────────────────────────────────────────────────────


@dataclass
class P5World:
    seed: int
    stocks: tuple[WorldStock, ...]
    krx_days: tuple[date, ...]
    _bars: dict[str, tuple[Bar, ...]]
    macro_meta: tuple[MacroSeriesMeta, ...]
    _macro: dict[str, tuple[MacroSeriesRow, ...]]
    calendar: tuple[CalendarEvent, ...]
    etfs: tuple[WorldEtf, ...]
    _etf_days: dict[str, tuple[EtfDay, ...]]
    _holdings: dict[tuple[str, date], tuple[HoldingRow, ...]]
    holding_dates: tuple[date, date]
    us_universe: tuple[UsUniverseRow, ...]
    _us_bars: dict[str, tuple[UsDailyRow, ...]]
    market_stats: tuple[MarketStatRow, ...]
    _minutes: dict[str, tuple[InvestorMinute, ...]]
    program_days: tuple[ProgramMarketRow, ...]
    program_intraday: tuple[ProgramMarketRow, ...]
    program_investors: tuple[ProgramInvestorRow, ...]
    program_stocks: tuple[ProgramStockRow, ...]
    materials: tuple[Material, ...]
    extra: dict[str, Any] = field(default_factory=dict[str, Any])

    # ── 읽기 ──
    def stock(self, code: str) -> WorldStock:
        for s in self.stocks:
            if s.code == code:
                return s
        raise KeyError(code)

    def bars(self, code: str) -> tuple[Bar, ...]:
        return self._bars[code]

    def series(self, series_id: str) -> tuple[MacroSeriesRow, ...]:
        return self._macro[series_id]

    def macro_rows(self, tier: Literal["public", "private"]) -> list[MacroSeriesRow]:
        ids = [m.series_id for m in self.macro_meta if m.tier == tier]
        return [r for i in ids for r in self._macro[i]]

    def etf_days(self, code: str) -> tuple[EtfDay, ...]:
        return self._etf_days[code]

    def holdings(self, fund_id: str, asof: date) -> tuple[HoldingRow, ...]:
        return self._holdings.get((fund_id, asof), ())

    def us_bars(self, symbol: str) -> tuple[UsDailyRow, ...]:
        return self._us_bars[symbol]

    def minutes(
        self, pair: str | None = None, *, drop: Collection[time] = ()
    ) -> list[InvestorMinute]:
        """1분 누적 행. `drop` 의 분은 빼고(수집 실패 변형 — 빈 분은 행이 없다)."""
        ids = [pair] if pair is not None else [p.id for p in MINUTE_PAIRS]
        out: list[InvestorMinute] = []
        for i in ids:
            out += [m for m in self._minutes[i] if m.ts.astimezone(KST).time() not in drop]
        return out

    def investor_daily_won(self) -> dict[tuple[str, str], int]:
        """검산 ③ 의 참값: 현물 조합의 마감 일별 순매수 대금(원 — `prv_flows.market_investor_daily`
        단위). 마지막 분 값(백만원) × 1,000,000 과 같다."""
        return dict(self.extra["investor_daily_won"])

    def materials_on(self, day: date) -> list[Material]:
        return [m for m in self.materials if m.day == day]

    # ── 검산(합성 원장 — 전부 0 이어야) ──
    def minute_checks(self) -> dict[str, int]:
        """검산 ①(현물 합 0)·②(기관 = 세부 합)·③(마지막 분 × 1e6 = 일별 원) 의 어긋난 행 수."""
        c1 = c2 = c3 = 0
        daily = self.investor_daily_won()
        for p in MINUTE_PAIRS:
            by_ts: dict[datetime, dict[str, InvestorMinute]] = {}
            for m in self._minutes[p.id]:
                by_ts.setdefault(m.ts, {})[m.investor] = m
            for ts, inv in by_ts.items():
                if p.spot and sum(_v(inv[t].net_value) for t in _CHECK1_TERMS) != 0:
                    c1 += 1
                for fld in ("net_value", "buy_value", "sell_value", "net_qty"):
                    parts = sum(_v(getattr(inv[t], fld)) for t in _ORGN_PARTS)
                    if _v(getattr(inv["orgn"], fld)) != parts:
                        c2 += 1
                del ts
            if p.spot:
                last = max(by_ts)
                for name, m in by_ts[last].items():
                    if _v(m.net_value) * 1_000_000 != daily[(p.id, name)]:
                        c3 += 1
        return {"c1": c1, "c2": c2, "c3": c3}

    def program_checks(self) -> dict[str, int]:
        """검산 ④: 전체 = 차익 + 비차익, 위탁 + 자기 = 합계(대금·수량 각각, 허용 0). 순 = 매수 −
        매도도 본다."""
        bad = 0
        groups: dict[tuple[object, ...], dict[tuple[str, str], ProgramMarketRow]] = {}
        for r in (*self.program_days, *self.program_intraday):
            groups.setdefault((r.trade_date, r.market, r.venue, r.ts), {})[(r.kind, r.side)] = r
        flds = ("sell_qty", "buy_qty", "net_qty", "sell_value", "buy_value", "net_value")
        for g in groups.values():
            for f in flds:
                for side in ("entm", "onsl", "smtn"):
                    whol = _v(getattr(g[("whol", side)], f))
                    parts = _v(getattr(g[("arbt", side)], f)) + _v(getattr(g[("nabt", side)], f))
                    bad += whol != parts
                for kind in ("arbt", "nabt", "whol"):
                    tot = _v(getattr(g[(kind, "smtn")], f))
                    parts = _v(getattr(g[(kind, "entm")], f)) + _v(getattr(g[(kind, "onsl")], f))
                    bad += tot != parts
            for r in g.values():
                bad += _v(r.net_value) != _v(r.buy_value) - _v(r.sell_value)
                bad += _v(r.net_qty) != _v(r.buy_qty) - _v(r.sell_qty)
        for r in self.program_investors:
            bad += _v(r.net_value) != _v(r.buy_value) - _v(r.sell_value)
        return {"c4": bad}

    # ── 손계산 기대값(같은 수가 p5_expect.yaml 에 — 시험이 둘을 맞춘다) ──
    def expect_macro(self) -> dict[str, dict[str, float | None]]:
        """매크로 파생(metrics §16) 마지막 3 영업일 — 한쪽 없으면 None(계산하지 않는다)."""

        def by_day(sid: str) -> dict[str, Decimal | None]:
            return {r.period: r.value for r in self._macro[sid]}

        def spread(a: str, b: str) -> dict[str, float | None]:
            x, y = by_day(a), by_day(b)
            days = sorted(x)[-3:]
            return {
                d: (float(x[d] - y[d]) if x.get(d) is not None and y.get(d) is not None else None)  # pyright: ignore[reportOperatorIssue]
                for d in days
            }

        return {
            "DERIVED:us_10y_2y": spread("FRED:DGS10", "FRED:DGS2"),
            "DERIVED:credit_aa": spread("ECOS:817Y002:corp_aa3y", "ECOS:817Y002:ktb3y"),
        }

    def expect_us(self) -> dict[str, list[str]]:
        """간밤 미국 신고가(US_DAY): 마지막 봉 종가가 직전 252봉 고가 최대보다 큼(종가·고가 두 해석
        모두에서 신고가가 되게 심었다). 이력 < 252 + 10 은 보류, 펀드는 유니버스 밖."""
        highs: list[str] = []
        held: list[str] = []
        funds = sorted(u.symbol for u in self.us_universe if u.is_fund)
        for u in self.us_universe:
            if u.is_fund:
                continue
            bars = self._us_bars[u.symbol]
            if len(bars) < US_LOOKBACK + 10:
                held.append(u.symbol)
                continue
            last = bars[-1]
            prior = max(_f(b.high) for b in bars[-US_LOOKBACK - 1 : -1])
            if _f(last.close) > prior:
                highs.append(u.symbol)
        return {"new_highs": sorted(highs), "held": sorted(held), "funds": funds}

    def expect_etf_consensus(self) -> list[str]:
        """같은 날 액티브 ETF 2개 이상이 새로 담은(NEW) 종목(metrics §19 — 1개는 제외)."""
        prev_d, cur_d = self.holding_dates
        count: dict[str, int] = {}
        for e in self.etfs:
            if not e.active:
                continue
            before = {h.code for h in self.holdings(e.fund_id, prev_d)}
            for h in self.holdings(e.fund_id, cur_d):
                if h.code not in before:
                    count[h.code] = count.get(h.code, 0) + 1
        return sorted(c for c, n in count.items() if n >= 2)

    def expect_program_top(self, n: int = 5) -> list[str]:
        """마감 훑기(sweep) 순매수 대금 상위 n 종목(코드 순서)."""
        rows = [r for r in self.program_stocks if r.scope == "sweep"]
        rows.sort(key=lambda r: (-_v(r.net_value), r.code))
        return [r.code for r in rows[:n]]

    # ── 기록·요약 해시 ──
    def records(self) -> Iterator[dict[str, Any]]:
        """세계의 모든 기록(머리 칸 `_source` = SYNTHETIC)."""
        yield {"_source": SYNTHETIC, "kind": "world", "seed": self.seed, "clock": CLOCK}
        for s in self.stocks:
            yield s.record()
        for code in sorted(self._bars):
            for b in self._bars[code]:
                yield _row("bar", b)
        for m in self.macro_meta:
            yield _row("macro_meta", m)
        for sid in sorted(self._macro):
            for r in self._macro[sid]:
                yield _row("macro_series", r)
        for ev in self.calendar:
            yield _row("calendar", ev)
        for e in self.etfs:
            yield e.record()
        for code in sorted(self._etf_days):
            for d in self._etf_days[code]:
                yield _row("etf_day", d)
        for (fund, asof), rows in sorted(self._holdings.items()):
            for h in rows:
                yield _row("holding", h) | {"fund_id": fund, "asof": asof}
        for u in self.us_universe:
            yield _row("us_universe", u)
        for sym in sorted(self._us_bars):
            for b in self._us_bars[sym]:
                yield _row("us_bar", b)
        for st in self.market_stats:
            yield _row("market_stat", st)
        for pid in sorted(self._minutes):
            for m in self._minutes[pid]:
                yield _row("investor_minute", m) | {"pair": pid}
        for seq, kind in (
            (self.program_days, "program_day"),
            (self.program_intraday, "program_minute"),
            (self.program_investors, "program_investor"),
            (self.program_stocks, "program_stock"),
        ):
            for r in seq:
                yield _row(kind, r)
        for mt in self.materials:
            yield mt.record()

    def digest(self) -> str:
        """세계 전체의 sha256(같은 시드 → 같은 값)."""
        h = hashlib.sha256()
        for rec in self.records():
            h.update(json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str).encode())
        return h.hexdigest()


def _v(x: int | None) -> int:
    return 0 if x is None else x


def _f(x: float | None) -> float:
    if x is None:
        raise ValueError("합성 세계에 없는 값")
    return x


def _make_stocks(seed: int) -> tuple[WorldStock, ...]:
    r = _rng(seed, "stocks")
    out: list[WorldStock] = []
    for i, code in enumerate(STOCK_CODES):
        market: Literal["KOSPI", "KOSDAQ"] = "KOSPI" if i % 3 != 2 else "KOSDAQ"
        price = float(round(r.uniform(5_000, 150_000), -1))
        out.append(WorldStock(code, _NAMES[i], market, _SECTORS[_SECTOR_OF[i]], price))
    return tuple(out)


def _make_bars(
    seed: int, stocks: Sequence[WorldStock], days: Sequence[date]
) -> dict[str, tuple[Bar, ...]]:
    out: dict[str, tuple[Bar, ...]] = {}
    for s in stocks:
        r = _rng(seed, f"bars:{s.code}")
        px = s.base_price
        rows: list[Bar] = []
        for d in days:
            o = px
            px = max(100.0, round(px * (1 + r.gauss(0.0004, 0.02)), -1))
            hi = max(o, px) * (1 + abs(r.gauss(0, 0.006)))
            lo = min(o, px) * (1 - abs(r.gauss(0, 0.006)))
            vol = int(r.uniform(50_000, 2_000_000))
            rows.append(Bar(s.code, d, o, round(hi, -1), round(lo, -1), px, vol, int(px * vol),
                            "krx", "KRX", Quality.OK))  # fmt: skip
        out[s.code] = tuple(rows)
    return out


def _make_macro(
    seed: int,
) -> tuple[tuple[MacroSeriesMeta, ...], dict[str, tuple[MacroSeriesRow, ...]]]:
    meta: list[MacroSeriesMeta] = []
    rows: dict[str, tuple[MacroSeriesRow, ...]] = {}
    kr_days = _kr_days(LAST_KRX_DAY - timedelta(days=120), LAST_KRX_DAY)[-MACRO_DAYS:]
    us_days = _us_days(US_DAY - timedelta(days=120), US_DAY)[-MACRO_DAYS:]
    for sid, tab, title, unit, freq, tier, source, dataset in _MACRO:
        meta.append(MacroSeriesMeta(sid, tab, title, unit, freq, tier, source, dataset))  # pyright: ignore[reportArgumentType]
        r = _rng(seed, f"macro:{sid}")
        base = _MACRO_BASE[sid]
        out: list[MacroSeriesRow] = []
        if freq == "M":
            y, m = 2023, 10
            v = base
            for _ in range(MACRO_MONTHS):
                v = v * (1 + r.gauss(0.002, 0.002))
                period = f"{y:04d}-{m:02d}"
                released = datetime(y + (m == 12), m % 12 + 1, 5, 8, 0, tzinfo=KST)
                out.append(MacroSeriesRow(sid, period, _dec(v), unit, source, released,
                                          Quality.OK, released_at=released))  # fmt: skip
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        else:
            days = us_days if source in ("FRED", "YAHOO") else kr_days
            if sid == "ECOS:817Y002:corp_aa3y":
                days = days[:-1]  # 공표 지연 — 마지막 날 값이 아직 없다(신용스프레드 stale)
            v = base
            for i, d in enumerate(days):
                v = v + r.gauss(0, base * 0.004)
                released = datetime.combine(d + timedelta(days=1), time(6, 0), tzinfo=KST)
                missing = sid == "FRED:DGS2" and i == len(days) - MISSING_DGS2_OFFSET
                out.append(MacroSeriesRow(
                    sid, d.isoformat(), None if missing else _dec(v), unit, source, released,
                    Quality.INVALID if missing else Quality.OK, released_at=released,
                ))  # fmt: skip
        rows[sid] = tuple(out)
    return tuple(meta), rows


def _make_calendar() -> tuple[CalendarEvent, ...]:
    def ev(
        country: str, kind: str, d: date, src: str, title: str, imp: int, **kw: Any
    ) -> CalendarEvent:
        return CalendarEvent(f"{country}:{kind}:{d.isoformat()}:{src}", kind, d, kw.pop("t", None),
                             title, country, imp, src, kw.pop("q", Quality.OK), **kw)  # fmt: skip

    return (
        ev("KR", "cpi", date(2026, 11, 4), "kosis", "소비자물가(10월)", 2, t=time(8, 0),
           actual=Decimal("116.85"), actual_series="KOSIS:DT_1J22003:cpi", unit="2020=100"),
        ev("US", "cpi", date(2026, 11, 13), "fred", "미 소비자물가(10월)", 3, t=time(22, 30)),
        ev("KR", "bok_rate", date(2026, 11, 27), "bok", "금융통화위원회 기준금리 결정", 3,
           t=time(10, 0)),
        ev("KR", "export", date(2026, 12, 1), "manual", "수출입동향(11월)", 2, t=time(9, 0)),
        ev("US", "fomc", date(2026, 12, 10), "fomc", "FOMC 정책금리 결정", 3, t=time(4, 0)),
        ev("KR", "gdp", date(2026, 12, 3), "ecos", "국민소득(3분기 잠정)", 2, t=time(8, 0)),
        # 로그인 등급 — 예상치(forecast)가 있는 행은 공개 표에 갈 수 없다
        ev("US", "payrolls", date(2026, 12, 4), "ff", "미 비농업 고용(11월)", 3, t=time(22, 30),
           forecast=Decimal("150"), unit="천명"),
    )  # fmt: skip


_ETFS: Final[tuple[tuple[str, str, EtfType, float | None, str | None, bool, int], ...]] = (
    # (코드, 이름, 유형, 레버리지, 테마, 액티브, 상장 경과 영업일)
    ("997510", "합성 코스피200", EtfType.KR_INDEX, None, None, False, 250),
    ("997520", "합성 반도체", EtfType.KR_THEME, None, "반도체", False, 250),
    ("997530", "합성 원전", EtfType.KR_THEME, None, "원전", False, 250),
    ("997540", "합성 레버리지", EtfType.LEVERAGED_INVERSE, 2.0, None, False, 250),
    ("997550", "합성 인버스", EtfType.LEVERAGED_INVERSE, -1.0, None, False, 250),
    ("997560", "합성 액티브A", EtfType.KR_THEME, None, "반도체", True, 250),
    ("997570", "합성 액티브B", EtfType.KR_THEME, None, "반도체", True, 250),
    ("997580", "합성 신규상장", EtfType.KR_THEME, None, "바이오", False, 8),
)


def _make_etfs(
    seed: int, days: Sequence[date]
) -> tuple[
    tuple[WorldEtf, ...],
    dict[str, tuple[EtfDay, ...]],
    dict[tuple[str, date], tuple[HoldingRow, ...]],
    tuple[date, date],
]:
    etfs: list[WorldEtf] = []
    edays: dict[str, tuple[EtfDay, ...]] = {}
    for code, name, typ, lev, theme, active, age in _ETFS:
        listed = days[-age]
        etfs.append(WorldEtf(code, name, typ, lev, theme, active, listed, f"synthetic:{code}"))
        r = _rng(seed, f"etf:{code}")
        nav = 10_000.0
        shrs = int(r.uniform(1e6, 2e7))
        rows: list[EtfDay] = []
        for d in days[-min(age, 70) :]:
            nav = round(nav * (1 + r.gauss(0.0005, 0.012) * (lev or 1.0)), 2)
            rows.append(EtfDay(code, d, name, round(nav * 1.001, 0), nav, shrs, int(shrs * nav),
                               int(r.uniform(1e8, 5e10)), int(r.uniform(1e4, 1e6)),
                               int(shrs * nav), None, "krx", "KRX", Quality.OK))  # fmt: skip
        edays[code] = tuple(rows)
    prev_d, cur_d = days[-2], days[-1]
    c = STOCK_CODES
    base = {
        # 액티브A·B 가 같은 날 합성로봇(c[18])을 새로 담는다(동시 편입), 합성부품2(c[22])는 A
        # 만(1개 — 제외)
        "997560": ([c[0], c[1], c[3], c[4]], [c[0], c[1], c[3], c[4], c[18], c[22]]),
        "997570": ([c[0], c[2], c[3], c[5]], [c[0], c[2], c[3], c[5], c[18]]),
        "997520": ([c[0], c[1], c[2], c[3], c[4]], [c[0], c[1], c[2], c[3], c[4]]),
        "997530": ([c[6], c[7], c[8], c[9], c[10]], [c[6], c[7], c[8], c[9], c[10]]),
    }
    holdings: dict[tuple[str, date], tuple[HoldingRow, ...]] = {}
    for code, (before, after) in base.items():
        for d, codes in ((prev_d, before), (cur_d, after)):
            wt = round(100.0 / len(codes), 4)
            holdings[(f"synthetic:{code}", d)] = tuple(
                HoldingRow(x, None, 1_000.0, wt, 1_000_000.0, "ETF_ISSUERS:synthetic", Quality.OK)
                for x in codes
            )
    return tuple(etfs), edays, holdings, (prev_d, cur_d)


_US: Final[tuple[tuple[str, str, bool, int, str], ...]] = (
    # (심볼, 이름, 펀드, 이력 봉 수, 마지막 봉 모양: high·flat·low)
    ("SY01", "Synthetic One", False, 400, "high"),
    ("SY02", "Synthetic Two", False, 400, "high"),
    ("SY03", "Synthetic Three", False, 400, "high"),
    ("SY04", "Synthetic Four", False, 400, "flat"),
    ("SY05", "Synthetic Five", False, 400, "low"),
    ("SY06", "Synthetic Six", False, 400, "low"),
    ("SY07", "Synthetic Seven", False, 400, "flat"),
    ("SY08", "Synthetic Eight", False, 400, "low"),
    ("SY09", "Synthetic Nine", False, 400, "low"),
    ("SY10", "Synthetic Ten", False, 400, "flat"),
    ("SY11", "Synthetic Fund", True, 400, "high"),
    ("SY12", "Synthetic Newco", False, 100, "high"),
)


def _make_us(seed: int) -> tuple[tuple[UsUniverseRow, ...], dict[str, tuple[UsDailyRow, ...]]]:
    days = _us_days(US_DAY - timedelta(days=620), US_DAY)
    uni: list[UsUniverseRow] = []
    bars: dict[str, tuple[UsDailyRow, ...]] = {}
    for sym, name, fund, n, shape in _US:
        r = _rng(seed, f"us:{sym}")
        uni.append(
            UsUniverseRow(
                sym,
                US_DAY,
                name,
                "NASDAQ",
                "Technology",
                None,
                round(r.uniform(5e8, 5e10), -6),
                fund,
                "nasdaq",
                Quality.OK,
            )
        )
        px = r.uniform(20, 300)
        rows: list[UsDailyRow] = []
        span = days[-n:]
        for d in span[:-1]:
            o = px
            px = max(1.0, px * (1 + r.gauss(0, 0.015)))
            hi = max(o, px) * (1 + abs(r.gauss(0, 0.005)))
            lo = min(o, px) * (1 - abs(r.gauss(0, 0.005)))
            rows.append(UsDailyRow(sym, d, "stock", round(o, 2), round(hi, 2), round(lo, 2),
                                   round(px, 2), int(r.uniform(1e5, 1e7)), True, "nasdaq",
                                   Quality.OK))  # fmt: skip
        top = max(_f(b.high) for b in rows[-US_LOOKBACK:])
        last = {"high": top * 1.03, "flat": top * 0.999, "low": top * 0.8}[shape]
        hi = last * 1.002 if shape == "high" else min(last * 1.0005, top * 0.9995)
        rows.append(UsDailyRow(sym, span[-1], "stock", round(last * 0.99, 2), round(hi, 2),
                               round(last * 0.985, 2), round(last, 2), int(r.uniform(1e5, 1e7)),
                               True, "nasdaq", Quality.OK))  # fmt: skip
        bars[sym] = tuple(rows)
    for sym in ("^SYN1", "^SYN2"):
        r = _rng(seed, f"us:{sym}")
        px = r.uniform(3_000, 20_000)
        rows = []
        for d in days[-300:]:
            px = px * (1 + r.gauss(0.0003, 0.01))
            rows.append(UsDailyRow(sym, d, "index", None, None, None, round(px, 2), None, None,
                                   "yahoo", Quality.OK))  # fmt: skip
        bars[sym] = tuple(rows)
    return tuple(uni), bars


_STATS: Final[tuple[tuple[str, str, float], ...]] = (
    # 항목 이름은 수집기 kbj.services.collectors.kofia ITEMS 와 같다(MAC 요청)
    ("credit_balance", "loan_kospi", 9_500_000.0),
    ("credit_balance", "loan_kosdaq", 8_200_000.0),
    ("market_capital", "investor_deposit", 55_000_000.0),
    ("fund_nav", "equity", 120_000_000.0),
    ("cma", "balance", 80_000_000.0),
)


def _make_stats(seed: int, days: Sequence[date]) -> tuple[MarketStatRow, ...]:
    out: list[MarketStatRow] = []
    for stat, item, base in _STATS:
        r = _rng(seed, f"stat:{stat}:{item}")
        v = base
        span = days[-40:-1] if stat == "credit_balance" else days[-40:]  # 신용잔고는 하루 늦게 공표
        for d in span:
            v = v * (1 + r.gauss(0, 0.004))
            as_of = datetime.combine(d + timedelta(days=1), time(18, 0), tzinfo=KST)
            out.append(MarketStatRow(stat, d, item, Decimal(f"{v:.0f}"), "백만원", "DATAGO",  # pyright: ignore[reportArgumentType]
                                     as_of, Quality.OK))  # fmt: skip
    return tuple(out)


def _make_minutes(
    seed: int,
) -> tuple[dict[str, tuple[InvestorMinute, ...]], dict[tuple[str, str], int]]:
    out: dict[str, tuple[InvestorMinute, ...]] = {}
    daily: dict[tuple[str, str], int] = {}
    stamps = _minutes(TODAY)
    for p in MINUTE_PAIRS:
        r = _rng(seed, f"minute:{p.id}")
        cum: dict[str, list[int]] = {i: [0, 0, 0, 0] for i in MINUTE_INVESTORS}  # sv, bv, sq, bq
        rows: list[InvestorMinute] = []
        for ts in stamps:
            inc: dict[str, list[int]] = {}
            for i in (*_ORGN_PARTS, "frgn", "etc_corp", "etc_orgt"):
                sv, bv = r.randint(0, 400), r.randint(0, 400)
                inc[i] = [sv, bv, sv * 10, bv * 10]
            inc["orgn"] = [sum(inc[x][k] for x in _ORGN_PARTS) for k in range(4)]
            # 개인 = 나머지(현물 검산 ① 이 정확히 0 이 되게 — 순매수 대금 합 0)
            others = ("frgn", "orgn", "etc_corp", "etc_orgt")
            net_rest = sum(inc[x][1] - inc[x][0] for x in others)
            qty_rest = sum(inc[x][3] - inc[x][2] for x in others)
            ps = r.randint(200, 800) + max(0, net_rest)  # 매수 대금이 음수가 되지 않게
            inc["prsn"] = [ps, ps - net_rest, ps * 10, ps * 10 - qty_rest]
            for i in MINUTE_INVESTORS:
                for k in range(4):
                    cum[i][k] += inc[i][k]
                sv, bv, sq, bq = cum[i]
                rows.append(
                    InvestorMinute(
                        ts,
                        TODAY,
                        "day",
                        p.market_code,
                        p.sector_code,
                        i,
                        sq,
                        bq,
                        bq - sq,
                        sv,
                        bv,
                        bv - sv,
                        Quality.ESTIMATED,
                    )
                )
        out[p.id] = tuple(rows)
        if p.spot:
            for i in MINUTE_INVESTORS:
                daily[(p.id, i)] = (cum[i][1] - cum[i][0]) * 1_000_000
    return out, daily


def _flow(r: random.Random, scale: int) -> tuple[int, int, int, int]:
    sv, bv = r.randint(0, scale), r.randint(0, scale)
    return sv // 10_000, bv // 10_000, sv, bv  # (매도 수량, 매수 수량, 매도 대금, 매수 대금)


def _add4(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2], a[3] + b[3])


def _program_group(
    r: random.Random,
    d: date,
    market: Literal["K", "Q"],
    ts: datetime | None,
    idx: float | None,
    scale: int,
    q: Quality,
) -> list[ProgramMarketRow]:
    base: dict[tuple[str, str], tuple[int, int, int, int]] = {}
    for kind in ("arbt", "nabt"):
        for side in ("entm", "onsl"):
            base[(kind, side)] = _flow(r, scale)
    for kind in ("arbt", "nabt"):
        base[(kind, "smtn")] = _add4(base[(kind, "entm")], base[(kind, "onsl")])
    for side in ("entm", "onsl", "smtn"):
        base[("whol", side)] = _add4(base[("arbt", side)], base[("nabt", side)])
    out: list[ProgramMarketRow] = []
    for (kind, side), (sq, bq, sv, bv) in sorted(base.items()):
        out.append(ProgramMarketRow(d, market, "KRX", kind, side, sq, bq, bq - sq, sv, bv, bv - sv,  # pyright: ignore[reportArgumentType]
                                    idx, "KIS", q, ts=ts))  # fmt: skip
    return out


def _make_program(
    seed: int, days: Sequence[date], stocks: Sequence[WorldStock]
) -> tuple[
    tuple[ProgramMarketRow, ...],
    tuple[ProgramMarketRow, ...],
    tuple[ProgramInvestorRow, ...],
    tuple[ProgramStockRow, ...],
]:
    r = _rng(seed, "program")
    daily: list[ProgramMarketRow] = []
    span = (*days[-(PROGRAM_DAYS - 1) :], TODAY)
    for market in ("K", "Q"):
        idx = 2_600.0 if market == "K" else 850.0
        for d in span:
            idx = round(idx * (1 + r.gauss(0, 0.01)), 2)
            q = Quality.ESTIMATED if d == TODAY else Quality.OK
            daily += _program_group(r, d, market, None, idx, 2_000_000_000_000, q)  # pyright: ignore[reportArgumentType]
    intraday: list[ProgramMarketRow] = []
    for market in ("K", "Q"):
        cum: dict[tuple[str, str], list[int]] = {}
        for ts in _minutes(TODAY):
            step = _program_group(r, TODAY, market, ts, None, 5_000_000_000, Quality.ESTIMATED)  # pyright: ignore[reportArgumentType]
            for row in step:  # 누적(장중 값은 그 시각까지의 합)
                acc = cum.setdefault((row.kind, row.side), [0, 0, 0, 0])
                for k, v in enumerate((row.sell_qty, row.buy_qty, row.sell_value, row.buy_value)):
                    acc[k] += _v(v)
                sq, bq, sv, bv = acc
                intraday.append(ProgramMarketRow(TODAY, market, "KRX", row.kind, row.side, sq, bq,  # pyright: ignore[reportArgumentType]
                                                 bq - sq, sv, bv, bv - sv, None, "KIS",
                                                 Quality.ESTIMATED, ts=ts))  # fmt: skip
    investors: list[ProgramInvestorRow] = []
    for market in ("K", "Q"):
        for inv in MINUTE_INVESTORS:
            parts: dict[str, tuple[int, int, int, int]] = {
                k: _flow(r, 300_000_000_000) for k in ("arbt", "nabt")
            }
            parts["whol"] = _add4(parts["arbt"], parts["nabt"])
            for kind, (sq, bq, sv, bv) in parts.items():
                investors.append(ProgramInvestorRow(TODAY, market, "KRX", inv, kind, sq, bq,  # pyright: ignore[reportArgumentType]
                                                    bq - sq, sv, bv, bv - sv, "KIS",
                                                    Quality.ESTIMATED))  # fmt: skip
    stock_rows: list[ProgramStockRow] = []
    by_turnover = sorted(
        stocks, key=lambda s: -s.base_price
    )  # 합성 — 가격 높은 순을 거래대금 순으로
    scan = list(by_turnover[:SCAN_N]) + [
        s for s in stocks if s.code in WATCH and s not in by_turnover[:SCAN_N]
    ]
    slots = [
        datetime.combine(TODAY, time(9, 5), tzinfo=KST) + timedelta(minutes=10 * i)
        for i in range(38)
    ]
    for s in stocks:
        rs = _rng(seed, f"program:{s.code}")
        mk: Literal["K", "Q"] = "K" if s.market == "KOSPI" else "Q"
        net_total = 0
        for ts in slots:
            sq, bq, sv, bv = _flow(rs, 3_000_000_000)
            net_total += bv - sv
            if s in scan:
                q = Quality.ESTIMATED
                row = ProgramStockRow(TODAY, s.code, mk, "KRX", "scan", s.base_price, 0.5, sq, bq,
                                      bq - sq, sv, bv, bv - sv, "KIS", q, ts=ts)  # fmt: skip
                stock_rows.append(row)
        sv = abs(net_total) + 1_000_000_000
        bv = sv + net_total
        stock_rows.append(ProgramStockRow(TODAY, s.code, mk, "KRX", "sweep", s.base_price, 0.5,
                                          sv // 10_000, bv // 10_000, bv // 10_000 - sv // 10_000,
                                          sv, bv, bv - sv, "KIS", Quality.OK))  # fmt: skip
    return tuple(daily), tuple(intraday), tuple(investors), tuple(stock_rows)


def _make_materials() -> tuple[Material, ...]:
    prev = LAST_KRX_DAY

    def at(d: date, hh: int, mm: int = 0) -> datetime:
        return datetime.combine(d, time(hh, mm), tzinfo=KST)

    out: list[Material] = []
    for day, krx_day, us_day in (
        (prev, date(2026, 11, 13), date(2026, 11, 13)),
        (TODAY, prev, US_DAY),
    ):
        today = day == TODAY
        out += [
            Material(day, "board", "board.daily", krx_day, at(krx_day, 18, 40), Quality.OK, "ok"),
            Material(day, "us_summary", "us.eod", us_day, at(day, 6, 20), Quality.OK, "ok"),
            # 매크로: 오늘은 늦다(전 영업일 값 — 줄에 실제 기준일을 적는다)
            Material(day, "macro", "macro.morning", day, at(prev if today else day, 7, 0),
                     Quality.STALE if today else Quality.OK, "late" if today else "ok"),
            # ETF 리포트: 오늘은 없다(무른 의존 시한까지 미준비)
            Material(day, "etf_report", "etf.report", krx_day, None if today else at(day, 8, 20),
                     None if today else Quality.OK, "missing" if today else "ok"),
        ]  # fmt: skip
    return tuple(out)


@cache
def build_world(seed: int = SEED) -> P5World:
    """합성 세계(캐시 — 같은 시드는 같은 객체). 시드가 다르면 값(시세·매크로·흐름)이 달라진다 —
    구조(종목·심은 충돌·검산 항등식)는 같다."""
    stocks = _make_stocks(seed)
    krx_days = _kr_days(PRICE_FROM, LAST_KRX_DAY)
    meta, macro = _make_macro(seed)
    etfs, edays, holdings, hdates = _make_etfs(seed, krx_days)
    uni, us_bars = _make_us(seed)
    minutes, daily = _make_minutes(seed)
    pd, pi, pinv, pst = _make_program(seed, krx_days, stocks)
    return P5World(
        seed=seed,
        stocks=stocks,
        krx_days=krx_days,
        _bars=_make_bars(seed, stocks, krx_days),
        macro_meta=meta,
        _macro=macro,
        calendar=_make_calendar(),
        etfs=etfs,
        _etf_days=edays,
        _holdings=holdings,
        holding_dates=hdates,
        us_universe=uni,
        _us_bars=us_bars,
        market_stats=_make_stats(seed, krx_days),
        _minutes=minutes,
        program_days=pd,
        program_intraday=pi,
        program_investors=pinv,
        program_stocks=pst,
        materials=_make_materials(),
        extra={"investor_daily_won": daily},
    )


def expect_doc(w: P5World) -> Mapping[str, Any]:
    """p5_expect.yaml 에 적는 손계산 값들(생성 확인용 — 시험은 yaml 의 글자와 이 값을 맞춘다)."""
    return {
        "macro": w.expect_macro(),
        "us": w.expect_us(),
        "etf_consensus": w.expect_etf_consensus(),
        "program_top": w.expect_program_top(),
    }

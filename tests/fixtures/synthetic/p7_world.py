"""합성 P7 세계 `p7_world` — P7 묶음(ENG·ING·DIAG·OUT·WRAP)이 같이 쓰는 고정 시드 합성 데이터.

근거: docs/p7_design.md §1.1(FND-B 표 `p7_world` 행)·§8·부록 A(metrics §D0~§D9).
KIS·KRX·금융위·ECOS·
운용사 원자료와 그것으로 만든 픽스처는 공개 레포에 넣지 않는다 — **전부
합성**(`random.Random(f"{seed}:
{이름}")` — 같은 시드 → 같은 `digest()`). 종목코드·이름은 가상(`A0` 접두 합성 코드).

쓰는 법
    from tests.fixtures.synthetic.p7_world import build_world
    w = build_world()                 # 시드 20261201(캐시 — 같은 객체)
    w.chain                           # ChainRecord — 주간 3사이클 + 야간 1사이클(월물·위클리 2)
    w.fill_gex(repo)                  # MemoryGexRepo 에 수집 표 씨앗 + 엔진 산출 하루치
    w.series["credit_spread"]         # 진단 입력 시계열(SeriesPoint — 300거래일, 공표 시각 포함)
    w.events                          # 이벤트(만기·금통위·실적 몰림·수출 속보)
    build_world(with_trade=False)     # P6 수출입 표가 없는 변형 — 수출 속보·모멘텀 없음

무엇이 있나(손계산 기대값은 `p7_expect.yaml`)
- 가짜 시계 `CLOCK` = 2026-12-10(목) 14:00 KST — 12월 월물 만기일(둘째 목요일 — 0DTE).
- 체인: 월물 202612(오늘 만기)·202701, 위클리 WKM 261202(12-14 월)·WKI 261203(12-17 목). 행사가
  330~370
  2.5pt, 콜·풋, board 원천. 주간 3사이클(10:00·11:30·13:30) + 전날 야간(12-09 19:00 — 거래일 12-10
  night,
  월물만). 마지막 사이클 1행은 invalid(엔진이 빼야 한다).
- 선물 전광판: 근월물 A01612·차월물 A01703(합성 코드), 사이클마다, 야간은 single.
- 투자자별(prv_gex.investor_flow): 7조합(K2I F001·OC01·OP01, WKM OC05·OP05, WKI OC04·OP04) ×
  투자자 12 ×
  정규장 30분마다(09:00~14:00 — 11 슬롯). **야간 행 없음**(D-P7-6). 대금 백만원(GX 단위).
- 엔진 산출 하루치: 사이클마다 레벨(범위 all·nearest·0dte — visible 핵심 + vex·cex shadow 레벨
  없음),
  지표(net_gex·atm_iv visible, vex·cex·skew_25d·hiro shadow), 행사가 GEX(마지막 사이클).
- 진단 입력(metrics §D1~§D7): 300거래일(오늘 포함) 시계열 — 신용스프레드(%p)·국고
  3년(%)·신용잔고(원)·
  예탁금(원)·20일선 위 비율(%)·신고가 비율(%)·외국인 현물 순매수(원)·K200 선물 외국인
  순매수(원)·코스피·
  MSCI ACWI(합성 지수)·VKOSPI(pt). 심은 것: VKOSPI 결측 7일·신용잔고 공표 지연 3일(D+2)·
  수출 모멘텀(with_trade 일 때만).
- 이벤트: 만기(월물·위클리 — 캘린더 정본으로 계산), 금통위(config/calendar_events.yaml 날짜를
  복사하지 않고
  합성 날짜 2개), 실적 몰림(그날 시총 상위 200 중 실적 일정 수 — 하루 ≥ 10 하나·9 하나), 수출 속보
  공표일
  (1·11·21 — with_trade 일 때만).
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from functools import cache
from typing import Any, Final

from kbj.core.calendar import TradingCalendar, monthly_expiry
from kbj.core.gex.records import (
    ChainRecord,
    ExpiryRecord,
    FuturesRecord,
    InvestorRecord,
    LevelRecord,
    MetricRecord,
    StrikeGexRecord,
)
from kbj.core.time import KST

__all__ = [
    "CLOCK",
    "COMBOS",
    "INVESTORS",
    "SEED",
    "SYN",
    "EventPoint",
    "P7World",
    "SeriesPoint",
    "build_world",
]

SEED: Final = 20261201
SYN: Final = "SYNTHETIC — tests/fixtures/synthetic/p7_world.py(고정 시드 생성기). 실데이터 아님"
CLOCK: Final = datetime(2026, 12, 10, 14, 0, tzinfo=KST)
TODAY: Final = CLOCK.date()
DAYS: Final = 300
STRIKES: Final[tuple[Decimal, ...]] = tuple(Decimal("330") + Decimal("2.5") * i for i in range(17))
SERIES: Final[tuple[tuple[str, str], ...]] = (
    ("", "202612"), ("", "202701"), ("WKM", "261202"), ("WKI", "261203"),
)  # fmt: skip
DAY_CYCLES: Final[tuple[time, ...]] = (time(10, 0), time(11, 30), time(13, 30))
NIGHT_AT: Final = datetime(2026, 12, 9, 19, 0, tzinfo=KST)  # 거래일 12-10 의 야간 세션
NEAR, NEXT = "A01612", "A01703"
COMBOS: Final[tuple[tuple[str, str], ...]] = (
    ("K2I", "F001"), ("K2I", "OC01"), ("K2I", "OP01"),
    ("WKM", "OC05"), ("WKM", "OP05"), ("WKI", "OC04"), ("WKI", "OP04"),
)  # fmt: skip
INVESTORS: Final[tuple[str, ...]] = (
    "frgn", "prsn", "orgn", "scrt", "ivtr", "pe_fund", "bank", "insu", "mrbn", "fund",
    "etc_orgt", "etc_corp",
)  # fmt: skip
SLOTS: Final[tuple[time, ...]] = tuple(time(9 + m // 60, m % 60) for m in range(0, 301, 30))
VKOSPI_MISSING: Final = 7
CREDIT_LATE: Final = 3
SERIES_KEYS: Final[tuple[str, ...]] = (
    "credit_spread", "ktb3y", "credit_balance", "deposits", "breadth_ma20", "new_high_ratio",
    "foreign_spot", "foreign_k200_fut", "kospi", "acwi", "vkospi",
)  # fmt: skip


@dataclass(frozen=True)
class SeriesPoint:
    """진단 입력 한 점 — published_at 은 공표 시각(시점 입력만 — `published_at ≤ as_of`)."""

    key: str
    day: date
    value: Decimal | None
    unit: str
    published_at: datetime
    source: str = "SYNTHETIC"


@dataclass(frozen=True)
class EventPoint:
    day: date
    kind: str  # expiry · mpc · export_flash · earnings_cluster
    title: str
    detail: Mapping[str, Any]


@dataclass(frozen=True)
class P7World:
    seed: int
    clock: datetime
    with_trade: bool
    days: tuple[date, ...]
    chain: tuple[ChainRecord, ...]
    futures: tuple[FuturesRecord, ...]
    expiries: tuple[ExpiryRecord, ...]
    investor: tuple[InvestorRecord, ...]
    levels: tuple[LevelRecord, ...]
    metrics: tuple[MetricRecord, ...]
    strike_gex: tuple[StrikeGexRecord, ...]
    series: Mapping[str, tuple[SeriesPoint, ...]]
    events: tuple[EventPoint, ...]
    earnings_counts: Mapping[date, int]

    @property
    def cycles(self) -> tuple[datetime, ...]:
        return tuple(sorted({r.ts for r in self.chain}))

    def records(self) -> Iterator[dict[str, Any]]:
        for kind, seq in (
            ("chain", self.chain), ("futures", self.futures), ("expiries", self.expiries),
            ("investor", self.investor), ("levels", self.levels), ("metrics", self.metrics),
            ("strike_gex", self.strike_gex),
        ):  # fmt: skip
            for r in seq:
                yield {"_source": SYN, "_kind": kind, **r.model_dump(mode="json")}
        for key in sorted(self.series):
            for p in self.series[key]:
                yield {"_source": SYN, "_kind": "series", **asdict(p)}
        for e in self.events:
            yield {"_source": SYN, "_kind": "event", **asdict(e)}

    def digest(self) -> str:
        h = hashlib.sha256()
        for rec in self.records():
            h.update(json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str).encode())
        return h.hexdigest()

    def fill_gex(self, repo: Any, *, engine: bool = True) -> None:
        """`MemoryGexRepo` 에 수집 표 씨앗(체인·선물·만기·투자자별)과 엔진 산출(engine=True)."""
        repo.seed_chain(self.chain)
        repo.seed_futures(self.futures)
        repo.seed_expiries(self.expiries)
        repo.seed_investor(self.investor)
        if engine:
            repo.write_levels(self.levels)
            repo.write_metrics(self.metrics)
            repo.write_strike_gex(self.strike_gex)

    def as_of(self, key: str, at: datetime) -> tuple[SeriesPoint, ...]:
        """시점 입력 — `at` 까지 공표된 점만(재계산 같음 시험용)."""
        return tuple(p for p in self.series[key] if p.published_at <= at)


def _rng(seed: int, *parts: object) -> random.Random:
    return random.Random(":".join([str(seed), *map(str, parts)]))  # noqa: S311 — 합성 데이터


def _trading_days(cal: TradingCalendar) -> tuple[date, ...]:
    out = [TODAY]
    d = TODAY
    while len(out) < DAYS:
        d = cal.prev_trading_day(d)
        out.append(d)
    return tuple(reversed(out))


def _utc(local: datetime) -> datetime:
    return local.astimezone(UTC)


def _forward(seed: int, at: datetime) -> Decimal:
    r = _rng(seed, "fwd", at.isoformat())
    return Decimal("351.20") + Decimal(r.randint(-80, 80)) / 100


def _make_chain(seed: int) -> tuple[ChainRecord, ...]:
    out: list[ChainRecord] = []
    cycles = [(datetime.combine(TODAY, t, tzinfo=KST), "day") for t in DAY_CYCLES]
    cycles.append((NIGHT_AT, "night"))
    for at, session in cycles:
        f = _forward(seed, at)
        series = SERIES if session == "day" else SERIES[:2]
        for cls, mtrt in series:
            r = _rng(seed, "chain", at.isoformat(), cls, mtrt)
            for k in STRIKES:
                for cp in ("C", "P"):
                    intrinsic = max(Decimal(0), (f - k) if cp == "C" else (k - f))
                    tv = (
                        Decimal(r.randint(5, 120))
                        / 100
                        * (1 if mtrt != "202612" else Decimal("0.3"))
                    )
                    last = (intrinsic + tv).quantize(Decimal("0.01"))
                    oi = r.randint(200, 9000) + (4000 if k in (Decimal(340), Decimal(360)) else 0)
                    out.append(ChainRecord(
                        ts=_utc(at), trade_date=TODAY, session=session,  # type: ignore[arg-type]
                        mrkt_cls=cls, expiry=mtrt, strike=k, cp=cp, source="board",  # type: ignore[arg-type]
                        code=f"A{cls or 'M'}{mtrt[-3:]}{int(k * 10):05d}{cp}",
                        last=last, bid=last - Decimal("0.01"), ask=last + Decimal("0.01"),
                        oi=oi, oi_chg=r.randint(-200, 200), volume=r.randint(0, 5000),
                        iv_kis=Decimal(r.randint(1300, 2600)) / 100, quality="ok",
                    ))  # fmt: skip
    # 마지막 주간 사이클의 한 행은 invalid(검증 실패 — 엔진·저장소가 빼야 한다)
    last_at = _utc(datetime.combine(TODAY, DAY_CYCLES[-1], tzinfo=KST))
    for i, c in enumerate(out):
        if c.ts == last_at and c.expiry == "202701" and c.strike == Decimal(350) and c.cp == "C":
            out[i] = c.model_copy(update={"quality": "invalid"})
            break
    return tuple(out)


def _make_futures(seed: int) -> tuple[FuturesRecord, ...]:
    out: list[FuturesRecord] = []
    for t in DAY_CYCLES:
        at = datetime.combine(TODAY, t, tzinfo=KST)
        f = _forward(seed, at)
        for code, extra in ((NEAR, Decimal(0)), (NEXT, Decimal("1.10"))):
            out.append(FuturesRecord(
                ts=_utc(at), trade_date=TODAY, session="day", code=code, market="F",
                source="board", price=f + extra, volume=1000, oi=200000,
                remaining_days=0 if code == NEAR else 91, quality="ok",
            ))  # fmt: skip
    f = _forward(seed, NIGHT_AT)
    out.append(FuturesRecord(
        ts=_utc(NIGHT_AT), trade_date=TODAY, session="night", code=NEAR, market="CM",
        source="single", price=f, quality="ok",
    ))  # fmt: skip
    return tuple(out)


def _make_expiries(cal: TradingCalendar) -> tuple[ExpiryRecord, ...]:
    at = _utc(datetime.combine(TODAY, time(8, 50), tzinfo=KST))
    last = {
        ("", "202612"): monthly_expiry(2026, 12, cal),
        ("", "202701"): monthly_expiry(2027, 1, cal),
        ("WKM", "261202"): date(2026, 12, 14),
        ("WKI", "261203"): date(2026, 12, 17),
    }
    return tuple(
        ExpiryRecord(
            ts=at,
            trade_date=TODAY,
            session="day",
            mrkt_cls=cls,
            expiry=mtrt,
            last_trade_date=d,
            source="kis",
            calendar_date=d,
            matches=True,
            quality="ok",
        )
        for (cls, mtrt), d in sorted(last.items())
    )


def _make_investor(seed: int) -> tuple[InvestorRecord, ...]:
    out: list[InvestorRecord] = []
    for slot in SLOTS:
        at = datetime.combine(TODAY, slot, tzinfo=KST)
        for market, sector in COMBOS:
            for inv in INVESTORS:
                r = _rng(seed, "inv", slot.isoformat(), market, sector, inv)
                sell, buy = r.randint(0, 5000), r.randint(0, 5000)
                sv, bv = r.randint(0, 900), r.randint(0, 900)
                out.append(InvestorRecord(
                    ts=_utc(at), trade_date=TODAY, session="day", market_code=market,
                    sector_code=sector, investor=inv,  # type: ignore[arg-type]
                    sell_qty=sell, buy_qty=buy, net_qty=buy - sell,
                    sell_value=sv, buy_value=bv, net_value=bv - sv, quality="ok",
                ))  # fmt: skip
    return tuple(out)


def _make_engine(
    seed: int,
) -> tuple[tuple[LevelRecord, ...], tuple[MetricRecord, ...], tuple[StrikeGexRecord, ...]]:
    levels: list[LevelRecord] = []
    metrics: list[MetricRecord] = []
    for i, t in enumerate(DAY_CYCLES):
        at = _utc(datetime.combine(TODAY, t, tzinfo=KST))
        f = float(_forward(seed, datetime.combine(TODAY, t, tzinfo=KST)))
        flip = (
            348.5 + i
        )  # 사이클마다 1pt 오른다(마지막 사이클에서 현재가가 Flip 아래로 — 알림 시험)
        for scope in ("all", "nearest", "0dte"):
            base = dict(ts=at, trade_date=TODAY, session="day", scope=scope, quality="ok")
            for name, value in (
                ("call_wall", 360.0), ("put_wall", 340.0), ("abs_gamma", 350.0), ("flip", flip),
                ("flip_distance", round((f - flip) / f * 100, 4)),
                ("expected_move_calendar", 4.2), ("expected_move_trading", 3.9),
                ("top_levels", 3.0),
            ):  # fmt: skip
                levels.append(LevelRecord.model_validate({**base, "name": name, "value": value}))
        for name, value, flag in (
            ("net_gex", 1.25e11 - i * 1e10, "visible"), ("atm_iv", 0.165, "visible"),
            ("vex", 2.1e9, "shadow"), ("cex", -3.3e8, "shadow"), ("skew_25d", 0.031, "shadow"),
            ("hiro", 1.5e8 * (i + 1), "shadow"),
        ):  # fmt: skip
            metrics.append(MetricRecord(
                ts=at, trade_date=TODAY, session="day", metric=name, scope="all", key="",
                value=value, payload={}, quality="ok", flag=flag,  # type: ignore[arg-type]
            ))  # fmt: skip
    last = _utc(datetime.combine(TODAY, DAY_CYCLES[-1], tzinfo=KST))
    strikes: list[StrikeGexRecord] = []
    for k in STRIKES:
        r = _rng(seed, "sgex", str(k))
        call = r.randint(0, 40) * 1e8
        put = -r.randint(0, 40) * 1e8
        strikes.append(StrikeGexRecord(
            ts=last, trade_date=TODAY, session="day", mrkt_cls="", expiry="202612", strike=k,
            gex_call=call, gex_put=put, gex=call + put, forward=351.2, excluded_oi_ratio=0.02,
            quality="ok",
        ))  # fmt: skip
    return tuple(levels), tuple(metrics), tuple(strikes)


_SPEC: Final[dict[str, tuple[str, float, float, str]]] = {
    # 키: (단위, 시작, 하루 표준편차, 공표 — same·d1_13·d1_08)
    "credit_spread": ("%p", 0.62, 0.01, "same"),
    "ktb3y": ("%", 2.55, 0.02, "same"),
    "credit_balance": ("원", 19.5e12, 0.06e12, "d1_13"),
    "deposits": ("원", 55.0e12, 0.4e12, "d1_13"),
    "breadth_ma20": ("%", 52.0, 4.0, "same"),
    "new_high_ratio": ("%", 3.0, 0.8, "same"),
    "foreign_spot": ("원", 0.0, 3.0e11, "same"),
    "foreign_k200_fut": ("원", 0.0, 1.5e11, "same"),
    "kospi": ("pt", 2600.0, 20.0, "same"),
    "acwi": ("pt", 820.0, 5.0, "d1_08"),
    "vkospi": ("pt", 18.0, 0.6, "d1_08"),
}


def _published(cal: TradingCalendar, d: date, rule: str, late: bool) -> datetime:
    if rule == "same":
        return datetime.combine(d, time(18, 0), tzinfo=KST)
    nxt = cal.next_trading_day(d)
    if late:  # 공표 지연(D+2)
        nxt = cal.next_trading_day(nxt)
    return datetime.combine(nxt, time(13, 0) if rule == "d1_13" else time(8, 0), tzinfo=KST)


def _make_series(
    seed: int, cal: TradingCalendar, days: tuple[date, ...]
) -> dict[str, tuple[SeriesPoint, ...]]:
    out: dict[str, tuple[SeriesPoint, ...]] = {}
    r_miss = _rng(seed, "vkospi_missing")
    missing = set(r_miss.sample(days[:-1], VKOSPI_MISSING))
    late = set(_rng(seed, "credit_late").sample(days[-60:-2], CREDIT_LATE))
    for key in SERIES_KEYS:
        unit, start, sd, rule = _SPEC[key]
        r = _rng(seed, "series", key)
        v = start
        pts: list[SeriesPoint] = []
        for d in days:
            step = r.gauss(0, sd)
            if key in ("foreign_spot", "foreign_k200_fut"):
                v = step  # 하루 순매수(누적은 엔진이)
            else:
                v = max(v + step, sd) if unit != "%p" else max(v + step, 0.05)
            value: Decimal | None = Decimal(str(round(v, 4 if unit in ("%p", "%") else 0)))
            if key == "vkospi" and d in missing:
                value = None
            pts.append(SeriesPoint(
                key=key, day=d, value=value, unit=unit,
                published_at=_published(cal, d, rule, key == "credit_balance" and d in late),
            ))  # fmt: skip
        out[key] = tuple(pts)
    return out


def _make_events(
    seed: int, cal: TradingCalendar, with_trade: bool
) -> tuple[tuple[EventPoint, ...], dict[date, int]]:
    ev: list[EventPoint] = []
    for y, m in ((2026, 11), (2026, 12), (2027, 1)):
        d = monthly_expiry(y, m, cal)
        ev.append(EventPoint(d, "expiry", "월물 만기", {"series": f"{y}{m:02d}"}))
    ev.append(EventPoint(date(2026, 12, 14), "expiry", "위클리(월) 만기", {"series": "WKM:261202"}))
    ev.append(EventPoint(date(2026, 12, 17), "expiry", "위클리(목) 만기", {"series": "WKI:261203"}))
    for d in (date(2026, 11, 26), date(2027, 1, 14)):  # 합성 날짜(공표 일정 복사 아님)
        ev.append(EventPoint(d, "mpc", "금통위(합성)", {}))
    counts = {date(2026, 12, 11): 12, date(2026, 12, 15): 9}  # 시총 상위 200 중 실적 일정 수
    for d, n in counts.items():
        if n >= 10:
            ev.append(EventPoint(d, "earnings_cluster", f"실적 몰림 {n}종목", {"count": n}))
    if with_trade:
        for d in (date(2026, 12, 1), date(2026, 12, 11), date(2026, 12, 21)):
            ev.append(
                EventPoint(d, "export_flash", "수출 속보(10일 잠정)", {"seg": (d.day - 1) // 10})
            )
    del seed
    ev.sort(key=lambda e: (e.day, e.kind, e.title))
    return tuple(ev), counts


@cache
def build_world(seed: int = SEED, *, with_trade: bool = True) -> P7World:
    cal = TradingCalendar.default()
    days = _trading_days(cal)
    levels, metrics, strike_gex = _make_engine(seed)
    series = _make_series(seed, cal, days)
    if with_trade:
        r = _rng(seed, "export")
        series["export_yoy"] = tuple(
            SeriesPoint(
                key="export_yoy",
                day=d,
                value=Decimal(str(round(r.gauss(5, 8), 2))),
                unit="%",
                published_at=datetime.combine(d, time(9, 0), tzinfo=KST),
            )
            for d in days
            if d.day in (1, 11, 21)
        )
    events, counts = _make_events(seed, cal, with_trade)
    return P7World(
        seed=seed, clock=CLOCK, with_trade=with_trade, days=days, chain=_make_chain(seed),
        futures=_make_futures(seed), expiries=_make_expiries(cal), investor=_make_investor(seed),
        levels=levels, metrics=metrics, strike_gex=strike_gex, series=series, events=events,
        earnings_counts=counts,
    )  # fmt: skip

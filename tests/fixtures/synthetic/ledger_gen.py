"""합성 원장 생성기 — 검산 ①②③ 과 수급·시장 엔진 시험의 입력.

근거: docs/metrics.md §5, docs/p3_design.md §8.2. 로그인 등급(KIS·KRX)이라 실데이터 fixture 를
쓰지 않는다 — 이 생성기가 시드로 결정적으로 만든다.

만드는 것(모두 `kbj.core.rows` 행 — 금액은 원 단위 정수)
- 가짜 캘린더: start 부터 평일, 그중 시드로 고른 휴장일 2일을 뺀 `days` 영업일.
- 종목(`Q00000` 꼴 — 실제 코드와 겹치지 않는 합성 코드): 코스피·코스닥·코넥스, 보통주·우선주·스팩·
  리츠·ETN·ETF 종류, 거래정지(그날 거래대금 0·투자자 행 없음·상태 `halted`)·관리종목·상태 모름(None)
  ·신규 상장(첫 행이 기간 중간)·상장폐지(마지막 행 뒤 없음).
- 투자자: 기관 7구분을 먼저 뽑고 **기관 = 7구분 합**,
  개인 = −(외국인 + 기타외국인 + 기관 + 기타법인) 으로 만든다 — 그래서 검산 ①(4구분 합 0)·
  ②(7구분 합 = 기관)가 **항상** 성립한다. 원천은 `kis`(ok), 마지막 날에는 앞선 종목에 장중 잠정
  (`kis.prelim`, estimated) 행도 같이 둔다(원장이 kis 를 골라야 한다).
- 스냅·일봉: 마지막 날 전까지는 KRX 확정(`krx`), 마지막 날은 KIS 마감(`kis`).
- 시장별 투자자(코드 `KOSPI`·`KOSDAQ`): 종목 행의 합 — ①② 가 시장 단위에서도 성립.
- 지수: `0001`(코스피)·`1001`(코스닥) 종가 = 그 시장 주식 시총 합 ÷ 1e10, 업종지수 `S0001`~`S0004`.
- ETF(`E00000` 꼴): 좌수 Sₜ = Sₜ₋₁·ratio + ΔS, NAV 무작위 걸음 + 1:10 분할·5:1 병합·분배금·
  신규 상장·상장폐지. 보고 순자산 = round(S × NAV). `etf_truth` 에 생성 과정의 참값
  (순유입 = ΔS × NAVₜ, 가격효과 = Sₜ₋₁·ratio × (NAVₜ − NAVₜ₋₁/ratio))을 둔다 — 공식과 독립인
  기대값(검산 ③).

깨뜨리기: `corrupt_investor`(한 행에 금액을 더함 → 그 행만 invalid 가 되어야 한다), `drop_investor`
(구분 하나를 통째로 뺌 → 검산 불가 `None` + 사유 — 메인 결정 R2).
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Literal

from kbj.core.quality import Quality
from kbj.core.rows import (
    INST7,
    Bar,
    EtfDay,
    EtfMeta,
    EtfType,
    IndexBar,
    Investor,
    InvestorDay,
    Snap,
    SplitEvent,
    UniverseRow,
)

__all__ = [
    "SECTOR_CODES",
    "EtfTruth",
    "StockSpec",
    "SyntheticMarket",
    "corrupt_investor",
    "drop_investor",
    "generate",
    "ledger_inputs",
]

WON_UNIT = 1_000_000  # 투자자 금액 단위(KIS 백만원 → 원)
SHARE_UNIT = 10_000  # ETF 좌수 단위(5:1 병합 뒤에도 정수)
SECTOR_CODES = ("S0001", "S0002", "S0003", "S0004")
OK = Quality.OK


@dataclass(frozen=True)
class StockSpec:
    code: str
    name: str
    market: str
    kind: str
    listed_idx: int  # 첫 행의 영업일 번호
    last_idx: int  # 마지막 행의 영업일 번호(상장폐지면 끝보다 앞)
    halts: frozenset[int]
    flags: tuple[str, ...] | None  # None = 상태 모름
    shares: int


@dataclass(frozen=True)
class EtfTruth:
    """생성 과정의 참값(원 — NAV 가 소수라 float). status 는 엔진 `EtfFlow.status` 와 같은 값."""

    inflow: float | None
    price_effect: float | None
    status: Literal["ok", "new", "split_adjusted"]
    ratio: float


@dataclass(frozen=True)
class SyntheticMarket:
    seed: int
    trading_days: tuple[date, ...]
    holidays: tuple[date, ...]
    stocks: tuple[StockSpec, ...]
    snaps: tuple[Snap, ...]
    bars: tuple[Bar, ...]
    investors: tuple[InvestorDay, ...]
    universe: tuple[UniverseRow, ...]
    market_investors: tuple[InvestorDay, ...]
    index_bars: tuple[IndexBar, ...]
    etf_days: tuple[EtfDay, ...]
    etf_meta: tuple[EtfMeta, ...]
    split_events: tuple[SplitEvent, ...]
    etf_truth: Mapping[tuple[str, date], EtfTruth] = field(
        default_factory=dict[tuple[str, date], EtfTruth]
    )

    @property
    def last_day(self) -> date:
        return self.trading_days[-1]

    def spec(self, code: str) -> StockSpec:
        return next(s for s in self.stocks if s.code == code)

    def index_series(self) -> dict[str, list[IndexBar]]:
        out: dict[str, list[IndexBar]] = {}
        for b in sorted(self.index_bars, key=lambda b: (b.code, b.date)):
            out.setdefault(b.code, []).append(b)
        return out


def ledger_inputs(m: SyntheticMarket) -> dict[str, object]:
    """`kbj.engines.flows.ledger.build_ledger(**ledger_inputs(m))` 용 인자."""
    return {
        "snaps": m.snaps,
        "bars": m.bars,
        "investors": m.investors,
        "universe": m.universe,
        "trading_days": m.trading_days,
    }


def corrupt_investor(
    m: SyntheticMarket, code: str, d: date, investor: Investor, delta: int
) -> SyntheticMarket:
    """(code, d, investor) 의 원천 kis 행 금액에 delta 를 더한다(없으면 KeyError)."""
    rows = list(m.investors)
    for i, r in enumerate(rows):
        if (r.code, r.date, r.investor, r.source) == (code, d, investor, "kis"):
            assert r.net_value is not None
            rows[i] = replace(r, net_value=r.net_value + delta)
            return replace(m, investors=tuple(rows))
    raise KeyError((code, d, investor))


def drop_investor(m: SyntheticMarket, investor: Investor) -> SyntheticMarket:
    """구분 하나를 종목·시장 행에서 통째로 뺀다(KIS 가 그 구분을 주지 않는 경우)."""
    return replace(
        m,
        investors=tuple(r for r in m.investors if r.investor != investor),
        market_investors=tuple(r for r in m.market_investors if r.investor != investor),
    )


# ── 만들기 ──────────────────────────────────────────────────────────────────────────────


def _calendar(rng: random.Random, start: date, n: int) -> tuple[tuple[date, ...], tuple[date, ...]]:
    weekdays: list[date] = []
    d = start
    while len(weekdays) < n + 2:
        if d.weekday() < 5:
            weekdays.append(d)
        d += timedelta(days=1)
    holidays = tuple(sorted(rng.sample(weekdays[2 : n - 2], 2)))
    days = tuple(x for x in weekdays if x not in holidays)[:n]
    return days, holidays


def _stock_specs(
    rng: random.Random, n: int, n_days: int, *, halts: bool, listings: bool
) -> list[StockSpec]:
    specs: list[StockSpec] = []
    last = n_days - 1
    for i in range(n):
        code = f"Q{i:05d}"
        market = "KOSPI" if i % 2 == 0 else "KOSDAQ"
        kind = "common"
        name = f"합성{i:02d}"
        if i % 10 == 3:
            kind, name = "pref", f"합성{i:02d}우"
        elif i == 7:
            kind = "reit"
        elif i == 11:
            kind = "etn"
        elif i == 15:
            kind = "etf"
        elif i == 17:
            kind = "spac"
        if i == 9:
            market = "KONEX"
        listed_idx, last_idx = 0, last
        if listings and i == 4:
            listed_idx = min(8, last)  # 마지막 날 기준 직전 거래일이 적다 — 급증 배수 계산 안 함
        if listings and i == 6:
            listed_idx = max(0, last - 2)
        if listings and i == 8:
            last_idx = max(0, last - 6)  # 상장폐지
        h: set[int] = set()
        if halts:
            if i == 1:
                h |= {10, 11}
            if i == 2:
                h |= {last}  # 오늘 거래정지 → 연속일 0
            if i == 5:
                h |= {0, 1, 2}
        flags: tuple[str, ...] | None = ()
        if i == 16:
            flags = ("managed",)
        if i == 12:
            flags = None
        shares = rng.randrange(5, 200) * 1_000_000
        specs.append(
            StockSpec(code, name, market, kind, listed_idx, last_idx, frozenset(h), flags, shares)
        )
    return specs


def _investor_draw(rng: random.Random, i: int, k: int, last: int) -> dict[Investor, int]:
    seven = {inv: rng.randint(-50, 50) * WON_UNIT for inv in INST7}
    foreign = rng.randint(-300, 300) * WON_UNIT
    other_corp = rng.randint(-30, 30) * WON_UNIT
    foreign_other = rng.randint(-5, 5) * WON_UNIT
    # 연속 순매수 사례를 심는다(시드와 무관하게 모양이 정해진다)
    if i == 0 and k > last - 5:
        foreign = abs(foreign) + WON_UNIT  # 외국인 5일 연속
    if i == 10 and k > last - 4:
        seven[Investor.TRUST] = abs(seven[Investor.TRUST]) + 400 * WON_UNIT  # 기관 4일 연속
    if i == 14:
        if k == last:
            foreign = abs(foreign) + WON_UNIT
        elif k == last - 1:
            foreign = 0  # 0원에서 끊긴다
    inst = sum(seven.values())
    indiv = -(foreign + foreign_other + inst + other_corp)
    out = {
        Investor.FOREIGN: foreign,
        Investor.INSTITUTION: inst,
        Investor.OTHER_CORP: other_corp,
        Investor.INDIVIDUAL: indiv,
        **seven,
    }
    if i % 2 == 0:
        out[Investor.FOREIGN_OTHER] = foreign_other
    else:
        out[Investor.FOREIGN] = foreign + foreign_other  # 응답이 나눠 주지 않는 경우
    return out


def generate(
    seed: int,
    *,
    days: int = 30,
    n_stocks: int = 40,
    n_etfs: int = 12,
    halts: bool = True,
    splits: bool = True,
    distributions: bool = True,
    listings: bool = True,
    prelim_last_day: bool = True,
    start: date = date(2026, 8, 3),
) -> SyntheticMarket:
    """결정적 합성 시장(같은 시드 → 같은 행). days ≥ 25, n_stocks ≥ 20(사례 종목 번호를 쓴다)."""
    if days < 25 or n_stocks < 20 or n_etfs < 0:
        raise ValueError("days ≥ 25, n_stocks ≥ 20, n_etfs ≥ 0")
    rng = random.Random(seed)  # noqa: S311 — 합성 데이터(암호 용도 아님)
    tdays, hols = _calendar(rng, start, days)
    last = len(tdays) - 1
    specs = _stock_specs(rng, n_stocks, len(tdays), halts=halts, listings=listings)

    snaps: list[Snap] = []
    bars: list[Bar] = []
    invs: list[InvestorDay] = []
    mkt_sum: dict[tuple[str, date, Investor], int] = {}
    cap_by: dict[tuple[str, date], int] = {}
    tv_by: dict[tuple[str, date], int] = {}
    for i, sp in enumerate(specs):
        close = float(rng.randrange(20, 2000) * 100)
        hist_tv: list[int] = []
        for k, d in enumerate(tdays):
            if k < sp.listed_idx or k > sp.last_idx:
                continue
            halted = k in sp.halts
            prev = close
            if not halted and k > sp.listed_idx:
                close = float(max(100, round(prev * (1 + rng.uniform(-0.03, 0.03)))))
            chg = 0.0 if k == sp.listed_idx else (close / prev - 1) * 100
            turnover = 0 if halted else int(rng.lognormvariate(23.0, 0.6))
            if i == 0 and k == last:
                prior = hist_tv[-19:]
                turnover = 3 * sum(prior) // len(prior)  # 급증 — 직전 19일 평균의 3배
            if not halted:
                hist_tv.append(turnover)
            volume = turnover // int(close)
            mktcap = int(close * sp.shares)
            src = "kis" if k == last else "krx"
            flags = sp.flags if not halted else ((*(sp.flags or ()), "halted"))
            snaps.append(
                Snap(sp.code, d, sp.name, sp.market, sp.kind, close, chg, volume, turnover, False,
                     mktcap, sp.shares, flags, src, "KRX", OK)
            )  # fmt: skip
            if k < last:
                hi = close * (1 + rng.uniform(0, 0.02))
                lo = close * (1 - rng.uniform(0, 0.02))
                bars.append(
                    Bar(sp.code, d, prev, hi, lo, close, volume, turnover, "krx", "KRX", OK)
                )
            if sp.kind in ("common", "pref", "spac") and sp.market in ("KOSPI", "KOSDAQ"):
                cap_by[(sp.market, d)] = cap_by.get((sp.market, d), 0) + mktcap
                tv_by[(sp.market, d)] = tv_by.get((sp.market, d), 0) + turnover
            if halted:
                continue
            vals = _investor_draw(rng, i, k, last)
            for inv, v in vals.items():
                invs.append(InvestorDay(sp.code, d, inv, v, None, "kis", "KRX", OK))
                if sp.market in ("KOSPI", "KOSDAQ"):
                    key = (sp.market, d, inv)
                    mkt_sum[key] = mkt_sum.get(key, 0) + v
            if prelim_last_day and k == last and i < 6:
                for inv in (Investor.FOREIGN, Investor.INSTITUTION):
                    est = vals[inv] + rng.randint(-20, 20) * WON_UNIT
                    invs.append(
                        InvestorDay(sp.code, d, inv, est, None, "kis.prelim", "KRX",
                                    Quality.ESTIMATED)
                    )  # fmt: skip

    # 시장별 투자자: 기타외국인을 나눠 주지 않는 종목이 있어 외국인 = 외국인 + 기타외국인 합
    mkt_rows: list[InvestorDay] = []
    for (mk, d, inv), v in sorted(mkt_sum.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        if inv is Investor.FOREIGN_OTHER:
            continue
        if inv is Investor.FOREIGN:
            v += mkt_sum.get((mk, d, Investor.FOREIGN_OTHER), 0)
        mkt_rows.append(InvestorDay(mk, d, inv, v, None, "kis", "KRX", OK))

    idx: list[IndexBar] = []
    for code, mk in (("0001", "KOSPI"), ("1001", "KOSDAQ")):
        for d in tdays:
            cap = cap_by.get((mk, d))
            if cap is None:
                continue
            v = cap / 1e10
            idx.append(IndexBar(code, d, mk, v, v, v, v, None, tv_by.get((mk, d)), "krx", OK))
    for j, code in enumerate(SECTOR_CODES):
        v = 1000.0 + 100 * j
        for d in tdays:
            v = round(v * (1 + rng.uniform(-0.02, 0.02)), 2)
            tv = int(rng.lognormvariate(25.0, 0.4))
            idx.append(IndexBar(code, d, f"합성업종{j}", v, v, v, v, None, tv, "krx", OK))

    universe = [
        UniverseRow(
            sp.code, tdays[-1], sp.name, sp.market, sp.kind, tdays[sp.listed_idx], "krx", OK
        )
        for sp in specs
    ]
    etf_days, meta, events, truth = _etfs(
        rng, tdays, n_etfs, splits=splits, distributions=distributions, listings=listings
    )
    return SyntheticMarket(
        seed=seed,
        trading_days=tdays,
        holidays=hols,
        stocks=tuple(specs),
        snaps=tuple(snaps),
        bars=tuple(bars),
        investors=tuple(invs),
        universe=tuple(universe),
        market_investors=tuple(mkt_rows),
        index_bars=tuple(idx),
        etf_days=tuple(etf_days),
        etf_meta=tuple(meta),
        split_events=tuple(events),
        etf_truth=truth,
    )


def _etfs(
    rng: random.Random,
    tdays: tuple[date, ...],
    n: int,
    *,
    splits: bool,
    distributions: bool,
    listings: bool,
) -> tuple[list[EtfDay], list[EtfMeta], list[SplitEvent], dict[tuple[str, date], EtfTruth]]:
    last = len(tdays) - 1
    types = list(EtfType)
    rows: list[EtfDay] = []
    meta: list[EtfMeta] = []
    events: list[SplitEvent] = []
    truth: dict[tuple[str, date], EtfTruth] = {}
    for i in range(n):
        code = f"E{i:05d}"
        first, end = 0, last
        if listings and i == 1:
            first = 10
        if listings and i == 2:
            end = last - 8
        event: tuple[int, float] | None = None  # (영업일 번호, ratio)
        if splits and i == 3:
            event = (12, 10.0)  # 1:10 분할
        if splits and i == 4:
            event = (15, 0.2)  # 5:1 병합
        dist_k = 18 if distributions and i == 5 else None
        shares = rng.randrange(50, 2000) * SHARE_UNIT * 5
        nav = round(rng.uniform(8_000, 60_000), 2)
        etype = types[i % len(types)]
        meta.append(
            EtfMeta(code, f"합성ETF{i:02d}", "합성운용", "합성", None, etype, None,
                    f"합성지수{i:02d}", tdays[first], tdays[end + 1] if end < last else None,
                    "synthetic", OK)
        )  # fmt: skip
        if event is not None:
            events.append(
                SplitEvent(code, tdays[event[0]], event[1], "manual", "합성 분할·병합",
                           "synthetic", OK)
            )  # fmt: skip
        prev_s: int | None = None
        prev_nav: float | None = None
        for k in range(first, end + 1):
            d = tdays[k]
            ratio = event[1] if event is not None and event[0] == k else 1.0
            if prev_s is None or prev_nav is None:
                s, cur_nav = shares, nav
                truth[(code, d)] = EtfTruth(None, None, "new", 1.0)
            else:
                adj_s = round(prev_s * ratio)
                adj_nav = prev_nav / ratio
                if ratio != 1.0 or k == dist_k:
                    ds = 0
                else:
                    ds = rng.randint(-20, 20) * SHARE_UNIT * 5
                    ds = max(ds, -(adj_s - SHARE_UNIT * 5))
                s = adj_s + ds
                if k == dist_k:
                    cur_nav = round(adj_nav - 500.0, 2)  # 분배금 500원
                else:
                    cur_nav = round(adj_nav * (1 + rng.uniform(-0.02, 0.02)), 2)
                truth[(code, d)] = EtfTruth(
                    ds * cur_nav,
                    adj_s * (cur_nav - adj_nav),
                    "split_adjusted" if ratio != 1.0 else "ok",
                    ratio,
                )
            close = float(round(cur_nav * (1 + rng.uniform(-0.004, 0.004))))
            turnover = int(rng.lognormvariate(22.0, 0.8))
            rows.append(
                EtfDay(code, d, f"합성ETF{i:02d}", close, cur_nav, s, round(s * cur_nav), turnover,
                       turnover // max(1, int(close)), round(s * close), f"합성지수{i:02d}", "krx",
                       "KRX", OK)
            )  # fmt: skip
            prev_s, prev_nav = s, cur_nav
    return rows, meta, events, truth

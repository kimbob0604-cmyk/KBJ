"""`diag.daily`(거래일 16:00) — 시장 진단·국장 공포탐욕·종합 판정(로그인 — `prv_diag.*`).

docs/p7_design.md §1.5·§3.2·D-P7-15, metrics §D6~§D8.

- 그날 D 는 `estimated` 로, 직전 거래일 D−1 은 KRX 확정 원장으로 다시 계산해 덮는다(입력 품질
  그대로 —
  확정용 두 번째 작업 없음, 쓰기 주인 하나). D−1 을 먼저 쓴 뒤 D 를 계산한다(D 의 이력에 D−1 이
  든다).
- 지표 값은 정본 함수로만: 반도체 쏠림·경기민감 대 방어 = `kbj.engines.market.ribbon`(§6.4 —
  20거래일),
  시장폭 = `kbj.engines.market.breadth`(§6.2), 한국 대 글로벌 =
  `kbj.engines.macro.ribbon.kr_vs_global_chip`
  (§6.5 — 20거래일 전 봉과 그날 봉 두 점을 넘긴다), 수급 = 외국인 현물(코스피+코스닥 시장별 일별,
  원)
  20일 합 + K200 선물 외국인 순매수(`prv_gex.investor_flow` 정규장 마지막 행, 백만원 → 원) 20일 합.
- 백분위 이력: 시장 지표는 앞서 저장한 `prv_diag.indicator`(trade_date < 그날) + 그날 값. 원장으로
  250일을
  매일 다시 만들지 않는다 — 처음엔 '이력 부족'(estimated)이고 `backfill` 로 과거 날을 차례로 채운다.
- 공포탐욕 변동성: VKOSPI(`prv_diag.vol_index` — 꺼진 `krx.vkospi`) 이력이 `min_history` 이상이면
  그것,
  아니면 월물 ATM IV(`prv_gex.metrics atm_iv` 시리즈 'M:' 중 가장 가까운 만기, visible·invalid
  아님 —
  날마다 그날 마지막 값) + '대체: ATM IV'(estimated).
- 신용스프레드·신용잔고 성분은 공개 진단 행(`pub_diag.indicator` — 같은 날 08:40 diag.public)을
  읽는다.
- GEX 체제: 범위 all 의 마지막 주간 사이클(그날 16:00 까지) Flip 거리 부호(현재가 − Flip) —
  visible 일
  때만(§D8). 이벤트 주의 꼬리표: `pub_diag.event_marker` 의 그날~다음 거래일.
- 오류는 삼키지 않는다(저장소·설정 오류는 그대로).
"""

from __future__ import annotations

import argparse
import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final, Protocol

from kbj.config.diag import DiagConfig, load_diag
from kbj.config.markets import MarketsConfig, load_markets
from kbj.core.calendar import TradingCalendar
from kbj.core.p7_rows import DiagIndicatorRow, FearGreedRow, VerdictRow
from kbj.core.quality import Quality
from kbj.core.rows import IndexBar, Investor
from kbj.core.time import KST
from kbj.engines.diagnosis.bands import Obs, pct_rank, worst
from kbj.engines.diagnosis.events import caution_days
from kbj.engines.diagnosis.fear_greed import SUBSTITUTE_ATM, Component, fear_greed
from kbj.engines.diagnosis.indicators import indicator_row, usable
from kbj.engines.diagnosis.verdict import GexRegime, event_flags, verdict
from kbj.engines.flows.checks import apply_checks
from kbj.engines.flows.ledger import Ledger, build_ledger
from kbj.engines.macro.ribbon import US_INDEX, kr_vs_global_chip
from kbj.engines.market.breadth import market_breadth
from kbj.engines.market.ribbon import cyclical_vs_defensive, semi_skew_from_ledger
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import pg_repos
from kbj.store.repos.board import BoardRepo
from kbj.store.repos.diag import DiagPublicRepo
from kbj.store.repos.diag_prv import DiagRepo
from kbj.store.repos.flows import FlowsRepo
from kbj.store.repos.gex import GexRepo
from kbj.store.repos.market import MarketRepo
from kbj.store.repos.us import UsRepo

__all__ = [
    "FUT_MARKET",
    "FUT_SECTOR",
    "JOB",
    "KOSPI",
    "SOURCE",
    "SPOT_MARKETS",
    "DailyOut",
    "MarketValues",
    "Repos",
    "TodayValue",
    "compute_daily",
    "run",
    "run_day",
]

JOB: Final = "diag.daily"
SOURCE: Final = "KBJ:diag.daily"
KOSPI: Final = "0001"  # KIS 업종 코드(코스피 지수 — readers.market 과 같은 값)
KOSPI_KRX: Final = "코스피"  # KRX 지수 일별의 코드(공백 뺀 이름 — krx_daily.index_code)


def _kospi_bars(repos: Repos, day: date, n: int) -> list[IndexBar]:
    """코스피 일봉 — KIS 코드(0001)로 쌓인 것이 없으면 KRX 일별(코스피). 원장은 KRX 이름 코드로만
    들어와 0001 로만 찾으면 늘 비었다(2026-10-20 — 반도체 쏠림·한국 대 글로벌 '값 없음'). 그날 봉이
    아직 없으면(KRX 는 D+1 08:00) 마감 뒤 KIS 마지막 시세로 잠정 봉을 붙인다(`_with_today`)."""
    got = repos.market.index_series([KOSPI, KOSPI_KRX], day, n)
    bars = got.get(KOSPI) or got.get(KOSPI_KRX, [])
    return _with_today({KOSPI_KRX: bars}, day, _today_closes(repos, day))[KOSPI_KRX]


CLOSE_FROM: Final = time(15, 30)  # 이 시각 뒤 KIS 장중 시세 = 그날 잠정 종가
_KRX_OF_KIS: Final = {"0001": "코스피", "1001": "코스닥", "2001": "코스피200"}


def _today_closes(repos: Repos, day: date) -> dict[str, tuple[float, int | None]]:
    """그날 15:30 뒤 KIS 마지막 지수·코스피 업종 시세 → {KRX 코드·정규화 업종 이름: (값, 거래대금)}.

    diag.daily 는 16:00 에 돈다 — KRX 지수 일별은 D+1 08:00 이라 그날 봉이 없어 코스피·업종 수익률
    지표가 모두 '봉 없음'이었다(2026-10-20). 그날 D 는 estimated 로 내고 다음 날 D−1 재계산이 KRX
    확정 봉으로 덮는다(작업 설계 그대로)."""
    start = datetime.combine(day, CLOSE_FROM, tzinfo=KST)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=KST)
    best: dict[str, tuple[datetime, float, int | None]] = {}

    def keep(key: str, ts: datetime, value: float | None, turnover: int | None) -> None:
        if value is None or value <= 0:
            return
        cur = best.get(key)
        if cur is None or ts > cur[0]:
            best[key] = (ts, value, turnover)

    for q in repos.market.index_quotes(start, end):
        krx = _KRX_OF_KIS.get(q.code)
        if krx is not None and q.quality.usable:
            keep(krx, q.ts, q.value, q.turnover)
    for s in repos.market.sector_quotes(start, end):
        if s.market == "KOSPI" and s.code.startswith("0") and s.quality.usable:
            keep(_norm_sector(s.name or ""), s.ts, s.value, s.turnover)
    return {k: (v[1], v[2]) for k, v in best.items()}


def _norm_sector(name: str) -> str:
    return "".join(ch for ch in name if ch not in " ·").upper()


def _with_today(
    series: Mapping[str, Sequence[IndexBar]],
    day: date,
    closes: Mapping[str, tuple[float, int | None]],
) -> dict[str, list[IndexBar]]:
    """그날 봉이 없는 코드에 KIS 잠정 봉(estimated)을 붙인다 — KRX 이름 코드·코스피 업종 이름."""
    out: dict[str, list[IndexBar]] = {}
    for code, bars in series.items():
        rows = list(bars)
        if not rows or rows[-1].date < day:
            got = closes.get(code) or closes.get(_norm_sector(code))
            if got is not None:
                rows.append(IndexBar(code, day, None, None, None, None, got[0], None, got[1],
                                     "KIS(잠정)", Quality.ESTIMATED))  # fmt: skip
        out[code] = rows
    return out


SPOT_MARKETS: Final[tuple[str, ...]] = (
    "0001",
    "1001",
)  # 시장별 투자자 일별(코스피·코스닥) [추정 #24]
FUT_MARKET: Final = "K2I"
FUT_SECTOR: Final = "F001"
FUT_FOREIGN: Final = "frgn"  # investor_flow 외국인(GX 표기)
FUT_UNIT: Final = Decimal(1_000_000)  # investor_flow 대금 백만원 → 원
LEDGER_N: Final = 25
DAY_CLOSE: Final = time(16, 0)
HISTORY_DAYS: Final = 400


class Repos(Protocol):
    """이 작업이 읽고 쓰는 저장소(`kbj.store.repos.Repos`·`MemoryRepos` 둘 다 만족)."""

    @property
    def market(self) -> MarketRepo: ...
    @property
    def flows(self) -> FlowsRepo: ...
    @property
    def board(self) -> BoardRepo: ...
    @property
    def us(self) -> UsRepo: ...
    @property
    def gex(self) -> GexRepo: ...
    @property
    def diag(self) -> DiagRepo: ...
    @property
    def diag_pub(self) -> DiagPublicRepo: ...


@dataclass(frozen=True)
class DailyOut:
    day: date
    indicators: list[DiagIndicatorRow]
    fear: FearGreedRow
    verdict: VerdictRow


def _load_ledger(repos: Repos, end: date, n: int) -> Ledger | None:
    """end 이하 n 영업일 원장(검산 적용) — `readers._common.load_ledger` 와 같은 고르기."""
    days = set(repos.market.trading_days(end, n))
    _, snap_day = repos.market.snapshot(end)
    if snap_day is not None and snap_day <= end:
        days.add(snap_day)
    picked = sorted(days)[-n:]
    if not picked:
        return None
    start, last = picked[0], picked[-1]
    snaps = [s for d in picked for s in repos.market.snapshots(d)]
    bars = [b for rows in repos.market.series(None, last, n).values() for b in rows]
    bars = [b for b in bars if start <= b.date <= last]
    invs = [r for rows in repos.flows.days(None, start, last).values() for r in rows]
    ledger, _ = apply_checks(build_ledger(snaps, bars, invs, repos.market.universe(last), picked))
    return ledger


@dataclass(frozen=True)
class TodayValue:
    value: Decimal | None
    quality: Quality | None
    source: str
    reason: str | None = None


MarketValues = Callable[[Repos, date, MarketsConfig, DiagConfig], dict[str, TodayValue]]


def _dec(v: float | None) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _market_values(
    repos: Repos, day: date, markets: MarketsConfig, cfg: DiagConfig
) -> dict[str, TodayValue]:
    n = cfg.change_days
    out: dict[str, TodayValue] = {}
    ledger = _load_ledger(repos, day, LEDGER_N)
    rcfg = markets.ribbon
    sectors = [*rcfg.cyclical, *rcfg.defensive]
    got_idx = repos.market.index_series(sectors, day, n + 5)
    idx = _with_today({c: got_idx.get(c, []) for c in sectors}, day, _today_closes(repos, day))
    kospi_bars = _kospi_bars(repos, day, n + 5)
    if ledger is None or ledger.last_day != day:
        why = "원장 없음 — market.close_collect·krx.daily"
        for k in ("semis_concentration", "breadth_ma20", "breadth_new_high"):
            out[k] = TodayValue(None, None, "", why)
    else:
        semi = semi_skew_from_ledger(ledger, kospi_bars, day, semis=list(rcfg.semis), period=n)
        out["semis_concentration"] = TodayValue(
            _dec(semi.value), semi.quality, semi.source, semi.reason
        )
        labels = list(repos.board.labels(day, "close").values())
        n_board = len(repos.board.stock_days(day)) or None
        b = market_breadth(
            ledger,
            day,
            labels,
            limit_move_pct=markets.market.limit_move_pct,
            board_universe=n_board,
        )
        out["breadth_ma20"] = TodayValue(
            _dec(b.above_ma20_pct), b.quality, b.source or "KBJ",
            None if b.above_ma20_pct is not None else "20일선 계산 종목 없음",
        )  # fmt: skip
        out["breadth_new_high"] = TodayValue(
            _dec(b.newhigh_pct), b.quality, b.source or "KBJ",
            None if b.newhigh_pct is not None else "; ".join(b.notes) or "신고가 비율 없음",
        )  # fmt: skip
    cyc = cyclical_vs_defensive(
        idx, day, cyclical=list(rcfg.cyclical), defensive=list(rcfg.defensive), period=n
    )
    out["cyclical_defensive"] = TodayValue(_dec(cyc.value), cyc.quality, cyc.source, cyc.reason)
    out["kr_vs_global"] = _kr_vs_global(repos, day, n)
    out["foreign_flow"] = _foreign(repos, day, n)
    return out


def _kr_vs_global(repos: Repos, day: date, n: int) -> TodayValue:
    kospi = [b for b in _kospi_bars(repos, day, n + 1) if b.date <= day]
    us = repos.us.daily(list(US_INDEX), day, n + 1)
    if len(kospi) < n + 1 or kospi[-1].date != day:
        return TodayValue(None, None, "", f"코스피 {n + 1}일 봉 없음")
    pairs = {s: rows for s, rows in us.items() if len(rows) >= n + 1}
    if set(pairs) != set(US_INDEX):
        return TodayValue(None, None, "", f"미국 지수 {n + 1}일 봉 없음")
    chip = kr_vs_global_chip(
        [kospi[-(n + 1)], kospi[-1]], {s: [r[-(n + 1)], r[-1]] for s, r in pairs.items()}
    )
    if chip is None or chip.value is None:
        return TodayValue(None, None, "", "한국 대 글로벌 계산 불가")
    v = chip.value
    return TodayValue(_dec(v.value), v.quality, v.source)


def _foreign(repos: Repos, day: date, n: int) -> TodayValue:
    rows = repos.flows.market_days(day, n + 5)
    per_day: dict[date, Decimal] = {}
    qs: list[Quality] = []
    for code in SPOT_MARKETS:
        for r in rows.get(code, []):
            if r.investor is Investor.FOREIGN and r.net_value is not None and r.date <= day:
                per_day[r.date] = per_day.get(r.date, Decimal(0)) + Decimal(r.net_value)
                qs.append(r.quality)
    days = sorted(per_day)[-n:]
    if len(days) < n or days[-1] != day:
        return TodayValue(None, None, "", f"외국인 현물 {n}일 없음")
    spot = sum((per_day[d] for d in days), Decimal(0))
    fut = Decimal(0)
    for d in days:
        upto = datetime.combine(d, DAY_CLOSE, tzinfo=KST)
        recs = [
            r for r in repos.gex.investor_latest(d, "day", upto)
            if r.market_code == FUT_MARKET and r.sector_code == FUT_SECTOR
            and r.investor == FUT_FOREIGN and r.net_value is not None
        ]  # fmt: skip
        if not recs:
            return TodayValue(
                None, None, "", f"K200 선물 외국인 {d.isoformat()} 없음(investor_flow)"
            )
        last = max(recs, key=lambda r: r.ts)
        fut += Decimal(last.net_value or 0) * FUT_UNIT
        qs.append(Quality(last.quality))
    return TodayValue(spot + fut, worst(qs), "KIS")


def _history(rows: Sequence[DiagIndicatorRow]) -> list[Obs]:
    return [
        Obs(r.trade_date, r.value, r.quality, str(r.inputs.get("input_source", r.source)))
        for r in rows
    ]


def _vol_component(repos: Repos, day: date, cfg: DiagConfig, cal: TradingCalendar) -> Component:
    start = day - timedelta(days=HISTORY_DAYS)
    vk = [
        Obs(r.trade_date, r.close, r.quality, r.source)
        for r in repos.diag.vol_index_history("vkospi", start, day)
    ]
    clean = usable(vk, day)
    if len(clean) >= cfg.min_history:
        window = [o.value for o in clean[-cfg.window :] if o.value is not None]
        return Component(pct_rank(window, window[-1]), clean[-1].quality)
    atm: list[Obs] = []
    d = day
    for i in range(cfg.window):
        if len(atm) + (cfg.window - i) < cfg.min_history:
            break  # 남은 날을 다 채워도 최소 이력에 못 미친다
        recs = [
            r for r in repos.gex.metric_last(d, "day", "atm_iv", "series", "M:")
            if r.flag == "visible" and r.value is not None and r.quality != "invalid"
        ]  # fmt: skip
        if recs:
            r = min(recs, key=lambda x: x.key)
            atm.append(Obs(d, Decimal(str(r.value)), Quality(r.quality), "KBJ:gex.engine"))
        elif i == 0:
            break  # 그날 ATM IV 가 없으면 성분 없음(이력을 볼 필요 없다)
        d = cal.prev_trading_day(d)
    atm.sort(key=lambda o: o.day)
    if len(atm) < cfg.min_history or atm[-1].day != day:
        return Component(None, Quality.INVALID)
    window = [o.value for o in atm if o.value is not None]
    return Component(pct_rank(window, window[-1]), worst(o.quality for o in atm[-1:]) or Quality.OK,
                     SUBSTITUTE_ATM)  # fmt: skip


def _gex(repos: Repos, day: date, cutoff: datetime) -> GexRegime | None:
    upto = min(cutoff, datetime.combine(day, DAY_CLOSE, tzinfo=KST))
    recs = {
        r.name: r
        for r in repos.gex.levels_latest("all", upto)
        if r.trade_date == day and r.session == "day"
    }
    flip, dist = recs.get("flip"), recs.get("flip_distance")
    if flip is None:
        return None
    visible = flip.flag == "visible"
    q = Quality(flip.quality)
    if flip.value is None:
        return GexRegime(spot=0.0, flip=None, visible=visible, quality=q)
    if dist is None or dist.value is None or dist.value >= 100:
        return None
    spot = flip.value / (1 - dist.value / 100)  # 거리 = (현재가 − Flip) ÷ 현재가 × 100
    return GexRegime(
        spot=spot, flip=flip.value, visible=visible and dist.flag == "visible", quality=q
    )


def compute_daily(
    repos: Repos,
    *,
    day: date,
    cutoff: datetime,
    cfg: DiagConfig,
    markets: MarketsConfig,
    cal: TradingCalendar,
    quality_cap: Quality | None,
    market_values: MarketValues | None = None,
) -> DailyOut:
    """그날의 시장 진단·공포탐욕·판정(쓰지 않는다). market_values = 지표 값(시험이 바꿔 끼운다)."""
    if cutoff.tzinfo is None:
        raise ValueError("cutoff 는 시간대가 있는 시각이어야 한다")
    start = day - timedelta(days=HISTORY_DAYS)
    today = (market_values or _market_values)(repos, day, markets, cfg)
    rows: list[DiagIndicatorRow] = []
    for key in cfg.keys("login"):
        t = today[key]
        spec = cfg.indicators[key]
        if t.value is None or t.quality is None:
            why = [t.reason or "입력 없음"]
            rows.append(
                indicator_row(key, spec, cfg, [], day=day, as_of=cutoff, source=SOURCE, reasons=why)
            )
            continue
        past = [r for r in repos.diag.indicator_history(key, start, day) if r.trade_date < day]
        hist = [*_history(past), Obs(day, t.value, t.quality, t.source)]
        rows.append(
            indicator_row(
                key, spec, cfg, hist, day=day, as_of=cutoff, source=SOURCE,
                quality_cap=quality_cap, reasons=[t.reason] if t.reason else (),
            )
        )  # fmt: skip
    pub = {r.key: r for r in repos.diag_pub.indicators_on(day)}
    by = {r.key: r for r in rows}

    def comp(r: DiagIndicatorRow | None) -> Component:
        if r is None:
            return Component(None, Quality.INVALID)
        return Component(r.pct_rank, r.quality)

    comps = {
        "vol": _vol_component(repos, day, cfg, cal),
        "breadth": comp(by.get("breadth_ma20")),
        "credit_balance": comp(pub.get("credit_balance")),
        "foreign": comp(by.get("foreign_flow")),
        "credit_spread": comp(pub.get("credit_spread")),
    }
    fear = fear_greed(comps, cfg.fear_greed, day=day, as_of=cutoff, source=SOURCE)
    if quality_cap is not None and fear.score is not None:
        q = worst((fear.quality, quality_cap)) or fear.quality
        fear = dataclasses.replace(fear, quality=q)
    window = caution_days(day, cfg.events, cal)
    events = repos.diag_pub.events_between(window[0], window[-1])
    inds = [*rows, *(r for k, r in pub.items() if k == "credit_spread")]
    v = verdict(cfg, day=day, as_of=cutoff, source=SOURCE, fear=fear, indicators=inds,
                gex=_gex(repos, day, cutoff), flags=event_flags(events, window))  # fmt: skip
    return DailyOut(day, rows, fear, v)


def _write(repos: Repos, out: DailyOut, *, now: datetime) -> int:
    n = repos.diag.upsert_indicators(out.indicators, computed_at=now, loaded_by=JOB)
    n += repos.diag.upsert_fear_greed(out.fear, computed_at=now, loaded_by=JOB)
    n += repos.diag.upsert_verdict(out.verdict, computed_at=now, loaded_by=JOB)
    return n


def run_day(
    repos: Repos,
    *,
    day: date,
    now: datetime,
    cfg: DiagConfig,
    markets: MarketsConfig,
    cal: TradingCalendar,
    confirm_prev: bool = True,
    market_values: MarketValues | None = None,
) -> dict[str, Any]:
    """D−1(확정 — 입력 품질 그대로) → D(estimated) 차례로 계산해 쓴다. 쓴 행 수·판정."""
    detail: dict[str, Any] = {"day": day.isoformat()}
    n = 0
    if confirm_prev:
        prev = cal.prev_trading_day(day)
        out_p = compute_daily(repos, day=prev, cutoff=now, cfg=cfg, markets=markets, cal=cal,
                              quality_cap=None, market_values=market_values)  # fmt: skip
        n += _write(repos, out_p, now=now)
        detail["prev"] = {"day": prev.isoformat(), "verdict": out_p.verdict.label}
    out = compute_daily(repos, day=day, cutoff=now, cfg=cfg, markets=markets, cal=cal,
                        quality_cap=Quality.ESTIMATED, market_values=market_values)  # fmt: skip
    n += _write(repos, out, now=now)
    detail.update(
        rows=n,
        verdict=out.verdict.label,
        fear_greed=None if out.fear.score is None else str(out.fear.score),
        missing=[r.key for r in out.indicators if r.value is None],
    )
    return detail


def _repos(ctx: JobContext) -> Repos:
    got = ctx.resources.get("repos")
    if got is not None:
        return got
    return pg_repos(ctx.resource("connect"))


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.engine.diag:run`)."""
    day = date.fromisoformat(ctx.as_of[:10])
    cfg: DiagConfig = ctx.resources.get("diag_config") or load_diag(settings=ctx.settings)
    markets: MarketsConfig = ctx.resources.get("markets") or load_markets(settings=ctx.settings)
    cal: TradingCalendar = ctx.resources.get("kr") or TradingCalendar.default()
    detail = run_day(_repos(ctx), day=day, now=ctx.now, cfg=cfg, markets=markets, cal=cal)
    return JobResult("ok", rows=detail["rows"], detail=detail)


def backfill(
    repos: Repos,
    *,
    start: date,
    end: date,
    now: Callable[[date], datetime],
    cfg: DiagConfig,
    markets: MarketsConfig,
    cal: TradingCalendar,
) -> list[Mapping[str, Any]]:
    """과거 거래일을 오래된 날부터 차례로(백분위 이력 채우기). now(날) = 그날 계산 시각."""
    out: list[Mapping[str, Any]] = []
    d = start if cal.is_trading_day(start) else cal.next_trading_day(start)
    while d <= end:
        out.append(run_day(repos, day=d, now=now(d), cfg=cfg, markets=markets, cal=cal,
                           confirm_prev=False))  # fmt: skip
        d = cal.next_trading_day(d)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m kbj.services.engine.diag backfill --from YYYY-MM-DD --to YYYY-MM-DD`(로그인
    VM)."""
    from kbj.config.settings import Settings
    from kbj.store.db import connect

    p = argparse.ArgumentParser(prog="kbj.services.engine.diag")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    b.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    a = p.parse_args(argv)
    settings = Settings()
    repos = pg_repos(lambda: connect(settings, service=JOB))
    cal = TradingCalendar.default()
    res = backfill(
        repos, start=a.start, end=a.end,
        now=lambda d: datetime.combine(cal.next_trading_day(d), DAY_CLOSE, tzinfo=KST),
        cfg=load_diag(settings=settings), markets=load_markets(settings=settings), cal=cal,
    )  # fmt: skip
    for r in res:
        print(r["day"], r["verdict"], r["fear_greed"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

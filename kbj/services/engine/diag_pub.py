"""`diag.public`(평일 08:40, 캐치업 10:00) — 공공지표 진단·매크로 위험 게이지·이벤트 표시.

docs/p7_design.md §1.5·§3.2·D-P7-15, metrics §D1~§D5·§D9. **공개 표만 읽고 쓴다**(계약 가 — 로그인
원천·로그인 저장소·GEX 를 import 하지 않는다): `pub_macro`(ECOS 국고 3년·회사채
AA-)·`pub_market_stats`
(금투협 신용공여·예탁금)·`pub_trade`(수출 속보)·`pub_filings`(실적 일정) → `pub_diag.*`.

- 시점 입력만: 공표·수신 시각이 `cutoff`(작업 = 실행 시각) 이하인 행만 쓴다 — 같은 날짜를 나중에
  같은
  cutoff 로 다시 돌리면 그 사이 새 자료가 와도 결과가 같다(`compute_public`).
- 신용스프레드는 정본 `kbj.engines.macro.derive.spread`(§16), 수출 YoY 는 정본
  `kbj.engines.trade.flash.flash_chip`(§25 — 발표 당시 값, estimated).
- 기대 관측 날(공표 지연 판단): ECOS 는 그날 저녁 공표라 아침엔 전 거래일, 금투협은 D+1 13:30 이라
  13:30 전엔 전전 거래일.
- 실적 집중은 실적 공시 예고(`pre_announce`)의 회사 수만 센다 — 법정 기한(statutory)은 실제 발표일이
  아니라 같은 날에 몰린다. 시총 상위 200 거르기(§D9 [제안])는 공개 입력에 시총이 없어 하지 않는다.
- 오류는 삼키지 않는다(저장소·설정 오류는 그대로 — 실행기가 failed 로 기록).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Final, Protocol

from kbj.config.diag import DiagConfig, load_diag
from kbj.config.markets import CalendarEvents, load_calendar_events
from kbj.core.calendar import TradingCalendar
from kbj.core.p5_rows import MacroSeriesRow, MarketStatRow
from kbj.core.p7_rows import DiagIndicatorRow, EventMarker, GaugeRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import TenDayRow
from kbj.engines.diagnosis.bands import Obs, worst
from kbj.engines.diagnosis.events import (
    EXPORT_DAYS,
    earnings_events,
    expiry_events,
    export_flash_events,
    mpc_events,
)
from kbj.engines.diagnosis.public import PublicInputs, macro_gauge, public_indicators
from kbj.engines.macro.derive import spread
from kbj.engines.trade.flash import CHIP_KIND, flash_chip
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.diag import DiagPublicRepo
from kbj.store.repos.filings import FilingsRepo
from kbj.store.repos.macro import MacroPublicRepo
from kbj.store.repos.market_stats import MarketStatsRepo
from kbj.store.repos.public import pg_public_repos
from kbj.store.repos.trade import PgTradeRepo, TradeRepo

__all__ = [
    "AA3Y",
    "HISTORY_DAYS",
    "JOB",
    "KTB3Y",
    "SOURCE",
    "PublicOut",
    "PublicRepos",
    "compute_public",
    "run",
]

JOB: Final = "diag.public"
SOURCE: Final = "KBJ:diag.public"
KTB3Y: Final = "ECOS:817Y002:ktb3y"
AA3Y: Final = "ECOS:817Y002:corp_aa3y"
HISTORY_DAYS: Final = 420  # 250거래일 창 + 20거래일 변화(달력일 여유)
EVENTS_BACK_DAYS: Final = 7
KOFIA_AT: Final = time(13, 30)
MACRO_AT: Final = time(18, 0)
EARNINGS_SOURCE: Final = "DART:결산실적공시예고(pub_filings.earnings_schedule)"


class PublicRepos(Protocol):
    """이 작업이 읽고 쓰는 공개 저장소(`PublicRepos`·`MemoryPublicRepos` 둘 다 만족)."""

    @property
    def macro(self) -> MacroPublicRepo: ...
    @property
    def market_stats(self) -> MarketStatsRepo: ...
    @property
    def filings(self) -> FilingsRepo: ...
    @property
    def diag(self) -> DiagPublicRepo: ...


@dataclass(frozen=True)
class PublicOut:
    indicators: list[DiagIndicatorRow]
    gauge: GaugeRow
    events: list[EventMarker]
    event_start: date
    event_end: date


def _macro_avail(r: MacroSeriesRow) -> datetime:
    """이 값을 알 수 있게 된 시각 — 기록된 공표·받은 시각과 공표 일정(그날 MACRO_AT) 중 이른 것.

    백필한 행은 공표 시각 칸이 '받은 시각'(백필한 날)이라 그대로 쓰면 지난날 진단에서 모두 '아직
    공표 전'으로 빠졌다(2026-10-20 점검: 신용스프레드·금리 방향이 날마다 '입력 없음'). ECOS 일별
    시장금리는 그날 MACRO_AT 까지 나온다 — 그보다 늦게 적힌 시각은 받은 시각일 뿐이다."""
    seen = r.released_at or r.as_of
    sched = datetime.combine(date.fromisoformat(r.period), MACRO_AT, tzinfo=KST)
    return min(seen, sched)


def _macro_obs(rows: Sequence[MacroSeriesRow], cutoff: datetime) -> list[MacroSeriesRow]:
    return [r for r in rows if len(r.period) == 10 and _macro_avail(r) <= cutoff]


def _to_obs(points: Sequence[tuple[str, object, Quality]], source: str) -> list[Obs]:
    return [
        Obs(date.fromisoformat(period), None if v is None else Decimal(str(v)), q, source)
        for period, v, q in points
    ]


def _stat_obs(
    rows: Sequence[MarketStatRow],
    items: Sequence[str],
    cutoff: datetime,
    cal: TradingCalendar | None = None,
) -> list[Obs]:
    """항목들의 같은 날 합(하나라도 없으면 그날 점 없음). 알 수 있게 된 시각 = 받은 시각과 공표
    일정(다음 거래일 KOFIA_AT — 금투협 D+1 13:30) 중 이른 것(백필 행 — `_macro_avail` 참고)."""
    by_day: dict[date, dict[str, MarketStatRow]] = {}
    for r in rows:
        avail = r.as_of
        if cal is not None:
            avail = min(avail, datetime.combine(cal.next_trading_day(r.date), KOFIA_AT, tzinfo=KST))
        if r.item in items and avail <= cutoff:
            by_day.setdefault(r.date, {})[r.item] = r
    out: list[Obs] = []
    for d in sorted(by_day):
        got = by_day[d]
        vals = [got[i].value for i in items if i in got]
        if len(vals) != len(items) or any(v is None for v in vals):
            continue
        total = sum((v for v in vals if v is not None), Decimal(0))
        q = worst(got[i].quality for i in items) or Quality.INVALID
        out.append(Obs(d, total, q, "DATAGO"))
    return out


def _publish_day(ym: str, seg: int) -> date:
    """구간(1~10·1~20·1~말일)이 공표되는 날 — 11·21일·다음 달 1일(D-P6-6)."""
    y, m = int(ym[:4]), int(ym[5:7])
    if seg < len(EXPORT_DAYS):
        return date(y, m, EXPORT_DAYS[seg])
    return date(y + 1, 1, EXPORT_DAYS[0]) if m == 12 else date(y, m + 1, EXPORT_DAYS[0])


def _export_obs(
    rows: Sequence[TenDayRow], cutoff: datetime
) -> tuple[list[Obs], dict[date, datetime]]:
    """구간마다 발표 당시 YoY(%) 한 점(그 구간을 처음 본 날) + 공표일 → 처음 본 시각."""
    seen_rows = [r for r in rows if r.first_seen_at <= cutoff]
    segs = sorted({(r.ym, r.seg) for r in seen_rows if r.kind == CHIP_KIND and r.col_no == 0})
    out: list[Obs] = []
    seen: dict[date, datetime] = {}
    for ym, seg in segs:
        chip = flash_chip([r for r in seen_rows if (r.ym, r.seg) <= (ym, seg)])
        if chip is None or (chip.ym, chip.seg) != (ym, seg):
            continue
        seen[_publish_day(ym, seg)] = chip.first_seen_at
        if chip.yoy is not None:
            day = chip.first_seen_at.astimezone(KST).date()
            out.append(Obs(day, Decimal(str(chip.yoy)) * 100, chip.quality, chip.source))
    return out, seen


def _expected(day: date, cutoff: datetime, cal: TradingCalendar) -> dict[str, date]:
    prev = cal.prev_trading_day(day)
    k = cutoff.astimezone(KST)
    macro = day if (k.date() > day or (k.date() == day and k.time() >= MACRO_AT)) else prev
    kofia_ready = k.date() > day or (k.date() == day and k.time() >= KOFIA_AT)
    kofia = prev if kofia_ready else cal.prev_trading_day(prev)
    return {
        "credit_spread": macro,
        "rate_direction": macro,
        "credit_balance": kofia,
        "deposits": kofia,
    }


def compute_public(
    repos: PublicRepos,
    trade: TradeRepo | None,
    *,
    day: date,
    cutoff: datetime,
    cfg: DiagConfig,
    cal: TradingCalendar,
    mpc: CalendarEvents,
) -> PublicOut:
    """그날의 공공지표 진단(쓰지 않는다 — 순수 계산 + 저장소 읽기)."""
    if cutoff.tzinfo is None:
        raise ValueError("cutoff 는 시간대가 있는 시각이어야 한다")
    start = day - timedelta(days=HISTORY_DAYS)
    macro = repos.macro.series([KTB3Y, AA3Y], start.isoformat(), day.isoformat())
    ktb = _macro_obs(macro.get(KTB3Y, []), cutoff)
    aa = _macro_obs(macro.get(AA3Y, []), cutoff)
    cs = _to_obs([(p.period, p.value, p.quality) for p in spread(aa, ktb)], "ECOS")
    ktb_obs = _to_obs([(r.period, r.value, r.quality) for r in ktb], "ECOS")
    credit = repos.market_stats.series("credit_balance", ["loan_kospi", "loan_kosdaq"], start, day)
    capital = repos.market_stats.series("market_capital", ["investor_deposit"], start, day)
    ten = trade.ten_day(kind=CHIP_KIND) if trade is not None else []
    export_obs, seen = _export_obs(ten, cutoff)
    inp = PublicInputs(
        credit_spread=cs, ktb3y=ktb_obs,
        credit_loan=_stat_obs(credit, ("loan_kospi", "loan_kosdaq"), cutoff, cal),
        deposits=_stat_obs(capital, ("investor_deposit",), cutoff, cal),
        export_yoy=export_obs, export_ready=bool(ten),
        expected=_expected(day, cutoff, cal),
    )  # fmt: skip
    rows = public_indicators(inp, cfg, day=day, as_of=cutoff, source=SOURCE)
    gauge = macro_gauge(rows, cfg, day=day, as_of=cutoff, source=SOURCE)
    ev_start = day - timedelta(days=EVENTS_BACK_DAYS)
    ev_end = day + timedelta(days=cfg.events.horizon_days)
    sched = repos.filings.schedule(start=ev_start, end=ev_end)
    counts = Counter(
        d for d, _ in {(s.planned_date, s.corp_code) for s in sched if s.kind == "pre_announce"}
    )
    mpc_items = [(e.date, e.title, e.source) for e in mpc.events if e.kind == "bok_mpc"]
    events = [
        *expiry_events(ev_start, ev_end, cal, as_of=cutoff),
        *mpc_events(mpc_items, ev_start, ev_end, as_of=cutoff),
        *export_flash_events(ev_start, ev_end, seen=seen, available=bool(ten), as_of=cutoff),
        *earnings_events(
            counts, cfg.events, ev_start, ev_end, source=EARNINGS_SOURCE, as_of=cutoff
        ),
    ]
    events.sort(key=lambda e: (e.date, e.kind, e.title))
    return PublicOut(rows, gauge, events, ev_start, ev_end)


def _public_repos(ctx: JobContext) -> PublicRepos:
    got = ctx.resources.get("public_repos")
    if got is not None:
        return got
    repos = ctx.resources.get("repos")
    if repos is not None:
        return repos.public()
    return pg_public_repos(ctx.resource("connect"))


def _trade(ctx: JobContext) -> TradeRepo | None:
    got = ctx.resources.get("trade_repo")
    if got is not None:
        return got
    repos = ctx.resources.get("repos")
    if repos is not None:
        return repos.trade
    if "connect" in ctx.resources:
        return PgTradeRepo(ctx.resource("connect"))
    return None


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.engine.diag_pub:run`)."""
    day = date.fromisoformat(ctx.as_of[:10])
    cfg: DiagConfig = ctx.resources.get("diag_config") or load_diag(settings=ctx.settings)
    mpc: CalendarEvents = ctx.resources.get("calendar_events") or load_calendar_events(
        settings=ctx.settings
    )
    cal: TradingCalendar = ctx.resources.get("kr") or TradingCalendar.default()
    repos = _public_repos(ctx)
    out = compute_public(repos, _trade(ctx), day=day, cutoff=ctx.now, cfg=cfg, cal=cal, mpc=mpc)
    n = repos.diag.upsert_indicators(out.indicators, computed_at=ctx.now, loaded_by=JOB)
    n += repos.diag.upsert_gauge(out.gauge, computed_at=ctx.now, loaded_by=JOB)
    n += repos.diag.replace_events(
        out.event_start, out.event_end, out.events, computed_at=ctx.now, loaded_by=JOB
    )
    missing = [r.key for r in out.indicators if r.value is None]
    return JobResult(
        status="ok",
        rows=n,
        detail={
            "day": day.isoformat(),
            "gauge": out.gauge.label,
            "events": len(out.events),
            "missing": missing,
        },
    )

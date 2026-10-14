"""`rules.outcome`(08:30)·`rules.backtest_nightly`(08:45) 처리기 + 백테스트 입력 고르기(P8 묶음 BKT,
docs/p8_design.md §1.3·§3.2·§4.5·§4.6, D-P8-12·13·14·17, metrics §30.4).

로그인 작업 — 저장소만 읽고 쓴다(KIS·KRX 호출 없음). 즉석 백테스트 API(`POST /api/backtest`)도
같은 입력 고르기(`frame_inputs`·`code_data`)를 부른다 — 그래서 이 모듈은 어댑터·수집기를 import
하지 않는다(계약 ⑩ — API 가 import 한다).

입력 고르기(`kbj.services.engine.rules_input.frame_inputs` 하나 — 실시간 `load_frames` 도 같은
함수를 거친다, P8 WRAP 이 두 벌을 합쳤다):
- 칸 = 정본 거래일 캘린더 `max(anchor, 첫 봉)` ~ upto, 일봉 = `MarketRepo.series`(원장 우선순위),
  수급 = 확정 원장 `FlowsRepo.days` → `build_ledger`(입력 지문이 수급 값을 포함), 분할 의심 =
  board `split_guard` 문턱, 보유 시작일 = `live.holdings`(trail_from_peak), 신고가 = board 라벨
  (종가 기준 — 규칙이 신고가를 쓸 때만).
- 장중 재생: 저장된 슬롯(`watch_quote_slot`) + 그 회차 가집계(`investor_intraday` ts ∈ [슬롯,
  슬롯+10분) → 오늘 원장 행, source kis.prelim, estimated — 사용자 결정 2026-10-13 장중 수급
  라이브).

`outcome(ctx)` — as_of = 전 거래일(KRX 확정 일봉 뒤): 최근 `max(horizons) × 2 + 5` 거래일의 허락된
신호(sent·suppressed·dry, 진입가 있음)마다 만기가 지난 기간의 성과를 다시 계산해 upsert
(`prv_alerts.signal_outcome` — 확정 값으로 덮는다, 멱등). 비교 지수 = 시장별(config/rules.yaml
index).

`nightly(ctx)` — 켜진 규칙마다(규칙 하나 실패는 그 규칙만 — detail 에 사유, 실행은 failed):
1. `nightly.years` 년 백테스트(마감 모드 — 장중 재생은 저장 슬롯이 있는 최근 `INTRADAY_REPLAY_DAYS`
   거래일만 [제안 — 속도]) → `backtest_run`·`backtest_signal`.
2. 최근 `match.window_days` 거래일의 실시간 신호(같은 판)와 비교 → `match_check`(날마다 한 줄).
   `logic` ≥ 1 이면 health 오류(실행 failed + detail — ops.watchdog 이 본다).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Final, Protocol, cast

from kbj.config.rules_cfg import RulesConfig, load_rules_cfg
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.rows import IndexBar
from kbj.core.rules_rows import (
    ALLOWED_SIGNAL_STATUSES,
    BacktestRun,
    BacktestSignal,
    MatchRow,
    OutcomeRow,
    QuoteSlot,
    SignalRow,
)
from kbj.core.time import KST
from kbj.engines.backtest.costs import Costs
from kbj.engines.board.config import BoardConfig
from kbj.engines.flows.ledger import LedgerRow
from kbj.engines.rules.backtest import (
    BacktestOut,
    CodeData,
    EvalMode,
    TradeParams,
    modes_of,
    run_backtest,
)
from kbj.engines.rules.dsl import DslError, Rule, conditions, limits_of, validate_rule
from kbj.engines.rules.live import holdings, universe_codes
from kbj.engines.rules.match import LIVE_TRUE_STATUSES, MatchEvent, match_days
from kbj.engines.rules.outcome import signal_outcomes
from kbj.services.engine.rules_input import (
    NEW_HIGH_BASIS,
    calendar_days,
    frame_inputs,
    live_flows,
)
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import pg_repos
from kbj.store.repos.board import BoardRepo
from kbj.store.repos.flows import FlowsRepo
from kbj.store.repos.journal import JournalRepo
from kbj.store.repos.market import MarketRepo
from kbj.store.repos.rules import RulesRepo

__all__ = [
    "INTRADAY_REPLAY_DAYS",
    "JOB_NIGHTLY",
    "JOB_OUTCOME",
    "NEW_HIGH_BASIS",
    "PRELIM_SOURCE",
    "SOURCE_NIGHTLY",
    "SOURCE_OUTCOME",
    "NightlyOut",
    "PerfRepos",
    "RuleRef",
    "calendar_days",
    "code_data",
    "compute_outcomes",
    "costs_of",
    "enabled_rule_refs",
    "frame_inputs",
    "index_by_market",
    "live_flow_rows",
    "nightly",
    "nightly_rule",
    "outcome",
    "rule_codes",
    "run_id_for",
    "summary_of",
]

JOB_OUTCOME: Final = "rules.outcome"
JOB_NIGHTLY: Final = "rules.backtest_nightly"
SOURCE_OUTCOME: Final = "KBJ:rules.outcome"
SOURCE_NIGHTLY: Final = "KBJ:rules.backtest_nightly"
PRELIM_SOURCE: Final = "kis.prelim"  # kbj.data.private.kis.investors.PRELIM_SOURCE 와 같은 값
SLOT_MIN: Final = 10
INTRADAY_REPLAY_DAYS: Final = 60  # 야간 장중 재생 창(거래일) [제안 — 속도, 3년은 마감 모드]
_CROCKFORD: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CLOSE_TIME: Final = time(15, 30)  # 원장 기준 시각(그날 마감)


class PerfRepos(Protocol):
    """이 모듈이 읽고 쓰는 저장소 — `Repos`(Pg)·`MemoryRepos`(시험)·API `ApiRepos` 모두 맞는다."""

    @property
    def market(self) -> MarketRepo: ...

    @property
    def flows(self) -> FlowsRepo: ...

    @property
    def board(self) -> BoardRepo: ...

    @property
    def journal(self) -> JournalRepo: ...

    @property
    def rules(self) -> RulesRepo: ...


# ── 입력 고르기(실시간과 같은 규칙) ──────────────────────────────────────────────────────────


def _back(cal: TradingCalendar, end: date, n: int) -> date:
    """end 포함 거꾸로 n 번째 거래일(n ≥ 1)."""
    d = end if cal.is_trading_day(end) else cal.prev_trading_day(end)
    for _ in range(n - 1):
        d = cal.prev_trading_day(d)
    return d


def uses_new_high(rule: Rule) -> bool:
    return any(c.ind == "new_high" for _, c in conditions(rule.when))


def live_flow_rows(flows: FlowsRepo, ts: datetime) -> dict[str, LedgerRow]:
    """그 슬롯의 장중 수급 → 종목별 오늘 원장 행 — 실시간과 같은 함수(`rules_input.live_flows`:
    확실히 들어온 가장 최근 가집계 회차만)."""
    return live_flows(flows, ts)


def index_by_market(
    repos: PerfRepos, cfg: RulesConfig, *, upto: date, n: int
) -> dict[str, dict[date, IndexBar]]:
    """시장(KOSPI·KOSDAQ) → 비교 지수 일봉 {날짜: 봉}(config/rules.yaml index [확인 필요])."""
    codes: dict[str, str] = {str(m): c for m, c in cfg.index.items()}
    got = repos.market.index_series(list(codes.values()), upto, n)
    return {m: {b.date: b for b in got.get(c, [])} for m, c in codes.items()}


def code_data(
    repos: PerfRepos,
    codes: Sequence[str],
    *,
    upto: date,
    cfg: RulesConfig,
    cal: TradingCalendar,
    split_ratio: float,
    slots_from: date | None = None,
    hold_since: Mapping[str, date] | None = None,
    new_high_days: Sequence[date] = (),
) -> dict[str, CodeData]:
    """백테스트 입력(프레임 입력 + 장중 재생 슬롯·가집계 + 시장 지수 + 상장 여부). `slots_from` 이
    없으면 장중 재생 입력을 싣지 않는다."""
    fis = frame_inputs(repos, codes, upto=upto, cfg=cfg, cal=cal, split_ratio=split_ratio,
                       hold_since=hold_since, new_high_days=new_high_days)  # fmt: skip
    if not fis:
        return {}
    uni = {u.code: u for u in repos.market.universe(upto)}
    n_days = max(len(fi.calendar or ()) for fi in fis.values())
    idx = index_by_market(repos, cfg, upto=upto, n=max(n_days, 1))
    slots_by: dict[str, list[QuoteSlot]] = {}
    lf_by: dict[str, dict[datetime, LedgerRow]] = {}
    if slots_from is not None and slots_from <= upto:
        start = datetime.combine(slots_from, datetime.min.time(), tzinfo=KST)
        end = datetime.combine(upto + timedelta(days=1), datetime.min.time(), tzinfo=KST)
        for s in repos.rules.quote_slots(list(fis), start, end):
            slots_by.setdefault(s.code, []).append(s)
        for ts in sorted({s.slot_ts for ss in slots_by.values() for s in ss}):
            for code, row in live_flow_rows(repos.flows, ts).items():
                lf_by.setdefault(code, {})[ts] = row
    out: dict[str, CodeData] = {}
    for code, fi in fis.items():
        u = uni.get(code)
        market = u.market if u is not None else None
        out[code] = CodeData(
            inputs=fi,
            slots=tuple(sorted(slots_by.get(code, []), key=lambda s: s.slot_ts)),
            live_flows=lf_by.get(code, {}),
            index_bars=idx.get(market) if market is not None else None,
            listed=u is not None,
        )
    return out


# ── 규칙 ──────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RuleRef:
    rule_id: str
    name: str
    version: int
    dsl_hash: str
    rule: Rule


def enabled_rule_refs(repo: RulesRepo, cfg: RulesConfig) -> tuple[list[RuleRef], dict[str, str]]:
    """켜진 규칙(규칙 id 순). 판 DSL 이 지금 검사를 못 지나면 그 규칙만 빼고 사유."""
    lim = limits_of(cfg)
    out: list[RuleRef] = []
    bad: dict[str, str] = {}
    for head, ver in repo.enabled_rules():
        try:
            rule = validate_rule(dict(ver.dsl), lim)
        except DslError as e:
            bad[head.rule_id] = "dsl_invalid: " + ",".join(i.reason for i in e.issues)[:200]
            continue
        out.append(RuleRef(head.rule_id, head.name, ver.version, ver.dsl_hash, rule))
    return sorted(out, key=lambda r: r.rule_id), bad


def _market_codes(market: MarketRepo, day: date) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"KOSPI": [], "KOSDAQ": []}
    for u in market.universe(day):
        if u.market in out:
            out[u.market].append(u.code)
    return out


def rule_codes(repos: PerfRepos, rule: Rule, *, day: date) -> tuple[list[str], dict[str, date]]:
    """규칙 우니버스 → 종목(실시간과 같은 `live.universe_codes`)과 보유 시작일."""
    hold = holdings(repos.journal.trades(), day)
    wl: dict[str, list[str]] = {}
    u = rule.universe
    name = getattr(u, "watchlist", None)
    if isinstance(name, str):
        items = sorted(repos.journal.watchlist(name), key=lambda w: (w.sort, w.code))
        wl[name] = [w.code for w in items]
    market = _market_codes(repos.market, day) if getattr(u, "market", None) else None
    codes = universe_codes(rule, watchlists=wl, holdings=sorted(hold), market=market)
    return codes, hold


def costs_of(cfg: RulesConfig) -> Costs:
    return Costs(cfg.costs.fee_bps, cfg.costs.sell_tax_pct, cfg.costs.slippage_bps)


# ── 알림 후 성과(rules.outcome) ─────────────────────────────────────────────────────────────


def _forward_days(cal: TradingCalendar, d: date, n: int) -> list[date]:
    out = [d]
    for _ in range(n):
        out.append(cal.next_trading_day(out[-1]))
    return out


def compute_outcomes(
    repos: PerfRepos,
    *,
    day: date,
    now: datetime,
    cfg: RulesConfig,
    cal: TradingCalendar,
    split_ratio: float,
) -> list[OutcomeRow]:
    """day(확정 원장의 마지막 날)까지 만기가 지난 성과 행."""
    max_h = max(cfg.horizons)
    look = max_h * 2 + 5
    start = _back(cal, day, look)
    sigs = [
        s for s in repos.rules.signals(start, day)
        if s.status in ALLOWED_SIGNAL_STATUSES and s.entry_price is not None
    ]  # fmt: skip
    if not sigs:
        return []
    codes = sorted({s.code for s in sigs})
    n = len(calendar_days(cal, start, day)) + 1
    series = repos.market.series(codes, day, n)
    idx = index_by_market(repos, cfg, upto=day, n=n)
    uni = {u.code: u for u in repos.market.universe(day)}
    idx_codes: dict[str, str] = {str(k): v for k, v in cfg.index.items()}
    out: list[OutcomeRow] = []
    for s in sigs:
        bars = {b.date: b for b in series.get(s.code, []) if b.date >= s.trade_date}
        u = uni.get(s.code)
        market = u.market if u is not None else None
        ib = idx.get(market) if market is not None else None
        index_code = idx_codes.get(market) if market is not None else None
        for o in signal_outcomes(
            entry_price=s.entry_price, days=_forward_days(cal, s.trade_date, max_h), bars=bars,
            index_bars=ib, horizons=cfg.horizons, upto=day, split_ratio=split_ratio,
            listed=u is not None,
        ):  # fmt: skip
            out.append(
                OutcomeRow(
                    signal_id=s.signal_id,
                    horizon=o.horizon,
                    due_date=o.due_date,
                    close_at=o.close_at,
                    ret=o.ret,
                    index_code=index_code if ib else None,
                    excess=o.excess,
                    mfe=o.mfe,
                    mae=o.mae,
                    reason=o.reason,
                    quality=o.quality,
                    computed_at=now,
                    source=SOURCE_OUTCOME,
                )
            )
    return out


# ── 자원 ──────────────────────────────────────────────────────────────────────────────────────


def _repos(ctx: JobContext) -> PerfRepos:
    got = ctx.resources.get("repos")
    if got is not None:
        return cast(PerfRepos, got)
    return pg_repos(ctx.resource("connect"))


def _cfg(ctx: JobContext) -> RulesConfig:
    got = ctx.resources.get("rules_config")
    return got if isinstance(got, RulesConfig) else load_rules_cfg(settings=ctx.settings)


def _cal(ctx: JobContext) -> TradingCalendar:
    got = ctx.resources.get("kr")
    return got if isinstance(got, TradingCalendar) else TradingCalendar.default()


def split_ratio_of(ctx: JobContext | None = None) -> float:
    if ctx is not None:
        got = ctx.resources.get("split_ratio")
        if isinstance(got, int | float):
            return float(got)
    return float(BoardConfig.load()["integrity"]["split_guard_ratio"])


def outcome(ctx: JobContext) -> JobResult:
    """등록부 처리기 `rules.outcome`(as_of = 전 거래일)."""
    day = date.fromisoformat(ctx.as_of[:10])
    repos = _repos(ctx)
    rows = compute_outcomes(repos, day=day, now=ctx.now, cfg=_cfg(ctx), cal=_cal(ctx),
                            split_ratio=split_ratio_of(ctx))  # fmt: skip
    n = repos.rules.put_outcomes_rules_outcome(rows) if rows else 0
    reasons: dict[str, int] = {}
    for r in rows:
        if r.ret is None and r.reason:
            reasons[r.reason] = reasons.get(r.reason, 0) + 1
    detail = {"day": day.isoformat(), "rows": n, "signals": len({r.signal_id for r in rows}),
              "excluded": reasons}  # fmt: skip
    return JobResult("ok", rows=n, detail=detail)


# ── 야간 백테스트·일치 감시(rules.backtest_nightly) ─────────────────────────────────────────


def run_id_for(rule_id: str, version: int, day: date, now: datetime) -> str:
    """결정적 실행 id `bt_` + ULID(시각 48비트 + sha256(규칙·판·날) 80비트)."""
    if now.tzinfo is None:
        raise ValueError("naive datetime 은 받지 않는다")
    ms = int(now.timestamp() * 1000) & ((1 << 48) - 1)
    h = int.from_bytes(hashlib.sha256(f"{rule_id}:{version}:{day}".encode()).digest()[:10], "big")
    n = (ms << 80) | h
    out: list[str] = []
    for _ in range(26):
        n, r = divmod(n, 32)
        out.append(_CROCKFORD[r])
    return "bt_" + "".join(reversed(out))


def _num(v: float | None) -> float | None:
    return None if v is None else float(v)


def summary_of(out: BacktestOut) -> dict[str, Any]:
    """`backtest_run.summary`(JSON) — 거래 통계·신호 성과 칸·수."""
    st = out.stats
    return {
        "trades": {
            "n": st.n, "win_rate": _num(st.win_rate), "mean": _num(st.mean),
            "median": _num(st.median), "p10": _num(st.p10), "p90": _num(st.p90),
            "profit_factor": _num(st.profit_factor), "avg_bars": _num(st.avg_bars),
            "mdd": _num(st.mdd), "total": _num(st.total), "annualized": _num(st.annualized),
            "reasons": dict(st.reasons),
        },
        "outcome": [
            {"horizon": c.horizon, "n": c.n, "excluded": c.excluded, "win_rate": c.win_rate,
             "mean": c.mean, "median": c.median, "q25": c.q25, "q75": c.q75,
             "excess_mean": c.excess_mean, "mfe_median": c.mfe_median,
             "mae_median": c.mae_median, "small": c.small}
            for c in out.outcome
        ],
        "signals": len(out.signals),
        "allowed": sum(1 for s in out.signals if s.status == "dry"),
        "evaluated": out.evaluated,
        "skipped": dict(out.skipped),
    }  # fmt: skip


@dataclass(frozen=True)
class NightlyOut:
    run: BacktestRun
    signals: tuple[BacktestSignal, ...]
    matches: tuple[MatchRow, ...]

    @property
    def logic_n(self) -> int:
        return sum(m.logic_n for m in self.matches)


def _live_mode(s: SignalRow) -> EvalMode:
    return "close" if s.mode == "close" else "intraday_replay"


def _revised(data: Mapping[str, CodeData], e: MatchEvent) -> bool:
    """실시간 지문이 없는 칸의 입력이 실시간 평가 뒤 바뀌었을 수 있나 — 마감은 그날 봉이 지금 KRX
    확정(ok·KIS 아님)이면 그렇다(16:00 평가는 KIS 마감 값), 장중은 저장된 슬롯·이미 확정이던
    원장이라 아니다."""
    if e.mode != "close":
        return False
    cd = data.get(e.code)
    if cd is None:
        return True
    b = next((x for x in cd.inputs.bars if x.date == e.trade_date), None)
    if b is None:
        return True
    return b.quality is Quality.OK and "kis" not in b.source.lower()


def _bt_fp(data: Mapping[str, CodeData], upto: date) -> Callable[[MatchEvent], str | None]:
    cache: dict[str, Any] = {}

    def fp(e: MatchEvent) -> str | None:
        cd = data.get(e.code)
        if cd is None:
            return None
        if e.mode == "close":
            fr = cache.get(e.code)
            if fr is None:
                fr = cd.inputs.close_frame(upto)
                cache[e.code] = fr
            i = fr.index_of(e.trade_date)
            return None if i is None else str(fr.input_fp(i))
        s = next((x for x in cd.slots if x.slot_ts == e.eval_ts), None)
        if s is None:
            return None
        f2 = cd.inputs.intraday_frame(s, cd.live_flows.get(s.slot_ts))
        return None if f2 is None else f2.input_fp(f2.last)

    return fp


def nightly_rule(
    repos: PerfRepos,
    ref: RuleRef,
    *,
    day: date,
    now: datetime,
    cfg: RulesConfig,
    cal: TradingCalendar,
    split_ratio: float,
    should_stop: Callable[[], bool] | None = None,
) -> NightlyOut:
    """규칙 판 하나의 야간 백테스트 + 일치 감시(쓰지 않는다 — 부르는 쪽이 한 트랜잭션으로)."""
    rule = ref.rule
    years_start = date(day.year - cfg.nightly.years, day.month, min(day.day, 28))
    start = max(years_start, cfg.anchor_date)
    wdays = calendar_days(cal, _back(cal, day, cfg.match.window_days), day)
    live = [
        s for s in repos.rules.signals(wdays[0], day, rule_id=ref.rule_id)
        if s.version == ref.version and s.status in LIVE_TRUE_STATUSES
    ]  # fmt: skip
    codes, hold = rule_codes(repos, rule, day=day)
    codes = list(dict.fromkeys([*codes, *sorted({s.code for s in live})]))
    modes = modes_of(rule)
    nh_days = calendar_days(cal, start, day) if uses_new_high(rule) else ()
    slots_from = _back(cal, day, INTRADAY_REPLAY_DAYS) if "intraday_replay" in modes else None
    data = code_data(repos, codes, upto=day, cfg=cfg, cal=cal, split_ratio=split_ratio,
                     slots_from=slots_from, hold_since=hold, new_high_days=nh_days)  # fmt: skip
    cool = rule.alert.cooldown_min or cfg.cooldown_min.default
    params = TradeParams(costs=costs_of(cfg))
    res = run_backtest(rule, data, rule_id=ref.rule_id, start=start, end=day, modes=modes,
                       cooldown_min=max(cool, cfg.cooldown_min.floor), params=params,
                       horizons=cfg.horizons, min_samples=cfg.stats.min_samples,
                       should_stop=should_stop)  # fmt: skip
    run_id = run_id_for(ref.rule_id, ref.version, day, now)
    run = BacktestRun(
        run_id=run_id, rule_id=ref.rule_id, version=ref.version, dsl_hash=ref.dsl_hash,
        mode="intraday_replay" if modes == ("intraday_replay",) else "close",
        start=start, end=day,
        params={"hold": params.hold, "stop_pct": params.stop_pct, "target_pct": params.target_pct,
                "costs": {"fee_bps": params.costs.fee_bps,
                          "sell_tax_pct": params.costs.sell_tax_pct,
                          "slippage_bps": params.costs.slippage_bps},
                "modes": list(modes), "codes_n": len(data),
                "intraday_from": None if slots_from is None else slots_from.isoformat(),
                "cooldown_min": max(cool, cfg.cooldown_min.floor)},
        ledger_as_of=datetime.combine(day, CLOSE_TIME, tzinfo=KST),
        summary=summary_of(res), created_at=now, quality=res.quality, source=SOURCE_NIGHTLY,
    )  # fmt: skip
    sigs = tuple(
        BacktestSignal(
            run_id,
            s.code,
            s.trade_date,
            s.eval_ts,
            s.mode,
            s.entry_price,
            s.rets,
            s.status,
            s.input_fp,
        )
        for s in res.signals
    )
    lv = [MatchEvent(s.code, s.eval_ts, s.trade_date, _live_mode(s), s.input_fp) for s in live]
    bt = [
        MatchEvent(s.code, s.eval_ts, s.trade_date, s.mode, s.input_fp)
        for s in res.signals if s.trade_date >= wdays[0] and s.mode in modes
    ]  # fmt: skip
    days = match_days(wdays, lv, bt, backtest_fp=_bt_fp(data, day),
                      revised=lambda e: _revised(data, e))  # fmt: skip
    matches = tuple(
        MatchRow(
            m.trade_date,
            ref.rule_id,
            ref.version,
            m.live_n,
            m.backtest_n,
            tuple(d.as_dict() for d in m.diffs),
            now,
            Quality.OK,
            SOURCE_NIGHTLY,
        )
        for m in days
    )
    return NightlyOut(run, sigs, matches)


def nightly(ctx: JobContext) -> JobResult:
    """등록부 처리기 `rules.backtest_nightly`(as_of = 전 거래일)."""
    day = date.fromisoformat(ctx.as_of[:10])
    repos, cfg, cal = _repos(ctx), _cfg(ctx), _cal(ctx)
    ratio = split_ratio_of(ctx)
    refs, bad = enabled_rule_refs(repos.rules, cfg)
    rows = 0
    errors: dict[str, str] = dict(bad)
    logic: dict[str, int] = {}
    runs = 0
    for ref in refs:
        try:
            out = nightly_rule(repos, ref, day=day, now=ctx.now, cfg=cfg, cal=cal,
                               split_ratio=ratio)  # fmt: skip
            rows += repos.rules.put_backtest_nightly(out.run, out.signals, out.matches)
        except Exception as e:
            errors[ref.rule_id] = f"{type(e).__name__}: {str(e)[:160]}"
            continue
        runs += 1
        if out.logic_n:
            logic[ref.rule_id] = out.logic_n
    detail: dict[str, Any] = {"day": day.isoformat(), "rules": len(refs), "runs": runs,
                              "rows": rows, "errors": errors, "logic": logic}  # fmt: skip
    if logic:
        detail["health"] = "logic_mismatch"
    status = "failed" if (errors or logic) else "ok"
    return JobResult(status, rows=rows, detail=json.loads(json.dumps(detail)))

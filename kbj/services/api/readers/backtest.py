"""백테스트 리더 — `/api/backtest/runs`·`/api/backtest/match`·즉석 `POST /api/backtest`
(docs/p8_design.md §4.6·§5·D-P8-14·17 — 로그인, DB·Redis 만 읽는다 — 계약 ⑩).

- 야간 실행 요약(`backtest_runs`)·일치 감시(`match_checks`)는 `rules.backtest_nightly` 가 쓴 표.
- 즉석 백테스트는 **저장하지 않는다**: 저장소에서 읽고(`rules_perf.code_data` — 야간 작업·실시간과
  같은 입력 고르기) 순수 엔진(`kbj.engines.rules.backtest`)으로 계산. 상한(config/rules.yaml adhoc)
  — 종목 수·기간 넘으면 413, 시간 넘으면 503. 결과는 Redis `rules:bt:<요청 해시>:<원장 기준일>`
  (`cache_ttl_s`). 규칙은 `rule_id`(+ 판) 또는 `dsl` 하나 — DSL 검사 오류는 422(위치·사유만).
- 봉투 notes: "표본 = 원장 보유 구간(생존편향 주의)" — 수익을 약속하는 문구 없음.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, Final, Protocol

from kbj.config.rules_cfg import RulesConfig
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.flows.ledger import worst_quality
from kbj.engines.rules.backtest import BacktestOut, BacktestTimeout, TradeParams, run_backtest
from kbj.engines.rules.dsl import DslError, Rule, dsl_hash, limits_of, validate_rule
from kbj.services.api.deps import ApiError
from kbj.services.api.models.backtest import (
    BacktestIn,
    BacktestMatch,
    BacktestResult,
    BacktestRunOut,
    BacktestRuns,
    BacktestSignalOut,
    BacktestTrade,
    CurvePoint,
    MatchDiff,
    MatchRowOut,
    StatsCell,
    TradeStatsOut,
)
from kbj.services.api.models.common import Envelope
from kbj.services.api.readers._common import NoData, ReadContext, envelope
from kbj.services.engine.rules_perf import CLOSE_TIME, code_data, costs_of, rule_codes

__all__ = [
    "ADHOC_SOURCE",
    "CACHE_PREFIX",
    "MAX_LIST",
    "NOTE_SAMPLE",
    "NO_BACKTEST",
    "adhoc_backtest",
    "backtest_match",
    "backtest_runs",
]

NO_BACKTEST: Final = "아직 없음 — rules.backtest_nightly 08:45"
SOURCE: Final = "KBJ:rules.backtest_nightly"
ADHOC_SOURCE: Final = "KBJ:backtest(즉석)"
CACHE_PREFIX: Final = "rules:bt:"
NOTE_SAMPLE: Final = "표본 = 원장 보유 구간(상장폐지 종목은 원장에 있으면 포함 — 생존편향 주의)"
NOTE_COSTS: Final = (
    "비용 = 수수료 양쪽 + 매도 거래세[확인 필요] + 슬리피지(config/rules.yaml costs)"
)
NOTE_FILL: Final = (
    "체결 = 마감 신호 d → d+1 시가, 장중 신호 → 슬롯 가격, 같은 날 손절·익절이면 손절"
)
NOTE_MATCH: Final = (
    "차이 사유: data_revised = 입력이 바뀜(KIS 잠정 → KRX 확정 등), logic = 같은 입력에 다른 결과"
)
MAX_LIST: Final = 1000  # 응답에 싣는 거래·신호 상한(최근 것) — 통계는 전부로
MATCH_DAYS: Final = 20


class _Redis(Protocol):
    def get(self, name: str) -> Any: ...
    def set(self, name: str, value: str, ex: int | None = None) -> Any: ...


# ── 야간 실행·일치 감시 ─────────────────────────────────────────────────────────────────────────


def _close_ts(d: date) -> datetime:
    return datetime.combine(d, CLOSE_TIME, tzinfo=KST)


def backtest_runs(ctx: ReadContext, *, rule_id: str | None) -> Envelope[BacktestRuns]:
    runs = ctx.repos.rules.backtest_runs(rule_id=rule_id, limit=20)
    if not runs:
        raise NoData(NO_BACKTEST)
    out = [
        BacktestRunOut(
            run_id=r.run_id,
            rule_id=r.rule_id,
            version=r.version,
            mode=r.mode,
            start=r.start,
            end=r.end,
            summary=dict(r.summary),
            created_at=r.created_at,
        )
        for r in runs
    ]
    return envelope(
        BacktestRuns(runs=out),
        source=SOURCE,
        as_of=max(r.ledger_as_of for r in runs),
        quality=worst_quality([r.quality for r in runs]),
        notes=[NOTE_SAMPLE, NOTE_COSTS, NOTE_FILL],
        generated_at=ctx.now(),
    )


def backtest_match(
    ctx: ReadContext, *, start: date | None, end: date | None
) -> Envelope[BacktestMatch]:
    last = end or ctx.today()
    first = start
    if first is None:
        d = last if ctx.cal.is_trading_day(last) else ctx.cal.prev_trading_day(last)
        for _ in range(MATCH_DAYS - 1):
            d = ctx.cal.prev_trading_day(d)
        first = d
    if first > last:
        raise ApiError(422, "bad_range", "from 이 to 보다 늦다")
    rows = ctx.repos.rules.match_checks(first, last)
    if not rows:
        raise NoData(NO_BACKTEST)
    out: list[MatchRowOut] = []
    for m in sorted(rows, key=lambda r: (r.trade_date, r.rule_id), reverse=True):
        diffs = [
            MatchDiff(
                code=str(d["code"]),
                eval_ts=datetime.fromisoformat(str(d["eval_ts"])),
                side=d["side"],
                reason=d["reason"],
            )
            for d in m.diffs
        ]
        out.append(
            MatchRowOut(
                trade_date=m.trade_date,
                rule_id=m.rule_id,
                version=m.version,
                live_n=m.live_n,
                backtest_n=m.backtest_n,
                logic_n=m.logic_n,
                diffs=diffs,
            )
        )
    logic = sum(m.logic_n for m in rows)
    notes = [
        "차이 사유: data_revised = 입력이 바뀜(KIS 잠정 → KRX 확정 등), logic = 같은 입력 다른 결과"
    ]
    if logic:
        notes.append(f"logic 차이 {logic}건 — 같은 입력에 다른 결과(점검 필요)")
    return envelope(
        BacktestMatch(rows=out),
        source=SOURCE,
        as_of=_close_ts(max(m.trade_date for m in rows)),
        quality=worst_quality([m.quality for m in rows]),
        notes=notes,
        generated_at=ctx.now(),
    )


# ── 즉석 백테스트(저장 없음) ────────────────────────────────────────────────────────────────────


def _rule_of(ctx: ReadContext, body: BacktestIn, cfg: RulesConfig) -> tuple[Rule, str, int | None]:
    """(규칙, 규칙 id 또는 'adhoc', 판)."""
    if (body.rule_id is None) == (body.dsl is None):
        raise ApiError(422, "rule_or_dsl", "rule_id 와 dsl 중 하나만")
    lim = limits_of(cfg)
    if body.dsl is not None:
        data: Any = body.dsl
        rid, ver = "r_0000000adhoc", None
    else:
        rid = str(body.rule_id)
        head = ctx.repos.rules.rule(rid)
        if head is None:
            raise ApiError(404, "unknown_rule", "규칙이 없다")
        want = body.version or head.current_version
        v = next((x for x in ctx.repos.rules.versions(rid) if x.version == want), None)
        if v is None:
            raise ApiError(404, "unknown_version", "그 판이 없다")
        data, ver = dict(v.dsl), v.version
    try:
        rule = validate_rule(data, lim)
    except DslError as e:
        where = ",".join(f"{i.pointer}:{i.reason}" for i in e.issues)[:500]
        raise ApiError(422, "dsl_invalid", where) from None
    return rule, rid, ver


def _ledger_day(ctx: ReadContext) -> date:
    got = ctx.repos.market.trading_days(ctx.today(), 1)
    if not got:
        raise NoData("원장 없음 — krx.daily 08:05")
    return got[-1]


def _cache_key(rule: Rule, body: BacktestIn, codes: list[str], day: date, cfg: RulesConfig) -> str:
    req = {
        "dsl": dsl_hash(rule),
        "mode": body.mode,
        "start": body.start.isoformat(),
        "end": body.end.isoformat(),
        "hold": body.hold,
        "stop": body.stop_pct,
        "target": body.target_pct,
        "codes": codes,
        "costs": cfg.costs.model_dump(),
        "anchor": cfg.anchor_date.isoformat(),
    }
    h = hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()[:32]
    return f"{CACHE_PREFIX}{h}:{day.isoformat()}"


def _result(
    out: BacktestOut,
    rule: Rule,
    rid: str | None,
    ver: int | None,
    body: BacktestIn,
    start: date,
    end: date,
    params: TradeParams,
    codes_n: int,
) -> BacktestResult:
    st = out.stats
    trades = sorted(out.trades, key=lambda t: (t.exit_date, t.code))[-MAX_LIST:]
    signals = sorted(out.signals, key=lambda s: (s.eval_ts, s.code))[-MAX_LIST:]
    return BacktestResult(
        rule_id=rid,
        version=ver,
        dsl_hash=dsl_hash(rule),
        mode=body.mode,
        start=start,
        end=end,
        params={
            "hold": params.hold,
            "stop_pct": params.stop_pct,
            "target_pct": params.target_pct,
            "codes_n": codes_n,
            "costs": {
                "fee_bps": params.costs.fee_bps,
                "sell_tax_pct": params.costs.sell_tax_pct,
                "slippage_bps": params.costs.slippage_bps,
            },
            "evaluated": out.evaluated,
            "skipped": dict(out.skipped),
            "signals_n": len(out.signals),
            "trades_n": len(out.trades),
        },
        summary=TradeStatsOut(
            n=st.n,
            win_rate=st.win_rate,
            mean=st.mean,
            median=st.median,
            p10=st.p10,
            p90=st.p90,
            profit_factor=st.profit_factor,
            avg_bars=st.avg_bars,
            mdd=st.mdd,
            total=st.total,
            annualized=st.annualized,
            reasons=dict(st.reasons),
        ),
        outcome=[
            StatsCell(
                horizon=c.horizon,
                n=c.n,
                excluded=c.excluded,
                win_rate=c.win_rate,
                mean=c.mean,
                median=c.median,
                q25=c.q25,
                q75=c.q75,
                excess_mean=c.excess_mean,
                mfe_median=c.mfe_median,
                mae_median=c.mae_median,
                small=c.small,
            )
            for c in out.outcome
        ],
        curve=[CurvePoint(date=d, equity=e) for d, e in out.curve],
        trades=[
            BacktestTrade(
                code=t.code,
                signal_date=t.signal_date,
                entry_date=t.entry_date,
                entry_price=t.entry_price,
                exit_date=t.exit_date,
                exit_price=t.exit_price,
                reason=t.reason,
                ret_pct=t.ret_pct,
                bars=t.bars,
            )
            for t in trades
        ],
        signals=[
            BacktestSignalOut(
                code=s.code,
                trade_date=s.trade_date,
                eval_ts=s.eval_ts,
                mode=s.mode,
                entry_price=s.entry_price,
                rets=s.rets,
                status=s.status,
            )
            for s in signals
        ],
        cached=False,
    )


def adhoc_backtest(
    ctx: ReadContext,
    body: BacktestIn,
    *,
    cfg: RulesConfig,
    split_ratio: float,
    redis: _Redis | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Envelope[BacktestResult]:
    """즉석 백테스트 — 저장 없음. 상한 넘으면 413(종목·기간)·503(시간)."""
    rule, rid, ver = _rule_of(ctx, body, cfg)
    if body.start > body.end:
        raise ApiError(422, "bad_range", "start 가 end 보다 늦다")
    if (body.end - body.start) > timedelta(days=366 * cfg.adhoc.max_years):
        raise ApiError(413, "range_too_long", f"기간은 {cfg.adhoc.max_years}년 이하")
    day = _ledger_day(ctx)
    end = min(body.end, day)
    start = max(body.start, cfg.anchor_date)
    if start > end:
        raise NoData("그 기간의 원장이 없다")
    if body.codes:
        codes = list(dict.fromkeys(body.codes))
        hold: dict[str, date] = {}
    else:
        try:
            codes, hold = rule_codes(ctx.repos, rule, day=day)
        except ValueError:
            raise ApiError(422, "universe", "시장 우니버스는 codes 를 함께 보낸다") from None
    if len(codes) > cfg.adhoc.max_codes:
        raise ApiError(413, "too_many_codes", f"종목은 {cfg.adhoc.max_codes}개 이하")
    if not codes:
        raise NoData("대상 종목이 없다(우니버스 비었음)")
    key = _cache_key(rule, body, codes, day, cfg)
    notes = [NOTE_SAMPLE, NOTE_COSTS, NOTE_FILL, "저장하지 않음(즉석 계산)"]
    as_of = _close_ts(day)
    if redis is not None:
        hit = redis.get(key)
        if hit is not None:
            obj = json.loads(hit)
            cached = BacktestResult.model_validate(obj["r"]).model_copy(update={"cached": True})
            return envelope(
                cached,
                source=ADHOC_SOURCE,
                as_of=as_of,
                quality=Quality(obj["q"]),
                notes=notes,
                generated_at=ctx.now(),
            )
    deadline = clock() + cfg.adhoc.timeout_s
    slots_from = start if body.mode == "intraday_replay" else None
    data = code_data(
        ctx.repos,
        codes,
        upto=day,
        cfg=cfg,
        cal=ctx.cal,
        split_ratio=split_ratio,
        slots_from=slots_from,
        hold_since=hold,
    )
    if not data:
        raise NoData("대상 종목의 일봉이 없다")
    params = TradeParams(
        hold=body.hold, stop_pct=body.stop_pct, target_pct=body.target_pct, costs=costs_of(cfg)
    )
    cool = max(rule.alert.cooldown_min or cfg.cooldown_min.default, cfg.cooldown_min.floor)
    try:
        out = run_backtest(
            rule,
            data,
            rule_id=rid,
            start=start,
            end=end,
            modes=(body.mode,),
            cooldown_min=cool,
            params=params,
            horizons=cfg.horizons,
            min_samples=cfg.stats.min_samples,
            should_stop=lambda: clock() > deadline,
        )
    except BacktestTimeout:
        raise ApiError(503, "timeout", f"{cfg.adhoc.timeout_s}초 안에 끝나지 않았다") from None
    res = _result(
        out, rule, None if body.dsl is not None else rid, ver, body, start, end, params, len(data)
    )
    if redis is not None and cfg.adhoc.cache_ttl_s > 0:
        blob = json.dumps({"q": out.quality.value, "r": res.model_dump(mode="json")})
        redis.set(key, blob, ex=cfg.adhoc.cache_ttl_s)
    if len(out.trades) > MAX_LIST or len(out.signals) > MAX_LIST:
        notes.append(f"거래·신호 목록은 최근 {MAX_LIST}건만(통계는 전부)")
    return envelope(
        res,
        source=ADHOC_SOURCE,
        as_of=as_of,
        quality=out.quality,
        notes=notes,
        generated_at=ctx.now(),
    )

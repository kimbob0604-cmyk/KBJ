"""`program.*` — 프로그램매매 수집(시장 장중·일별·백필·종목 장중 훑기·마감 전 종목 훑기 — P5 F2).

근거: docs/p5_flow_program_prompt.md §0-2·§0-3·§1.4·§3.4, docs/p5_design.md §0.4·§3.2·§3.4·§4.7,
docs/metrics.md §22. 파서·TR 표는 `kbj.data.private.kis.program`(전부 [확인 필요 #40·#41] — 그래서
등록부에서 꺼져 있다. 실측 뒤 켠다). 금액은 원(파서가 바꾼다).

처리기 → 작업 · 데이터 키 · 쓰는 표
- `intraday` → `program.intraday`(매분) · `program_market_intraday@<분>[거래소]` ·
  `prv_flows.program_market_intraday`
- `daily` → `program.daily`(close+20) · `program_market_daily@<날>`·`program_investor_today@<날>` ·
  `prv_flows.program_market_daily`·`program_investor_daily`
- `backfill` → `program.backfill`(수동) · `program.daily` 와 같은 키(지난 날) · 같은 표
- `scan` → `program.stock_scan`(10분 — open+5) · `program_stock_intraday@<10분 슬롯>[거래소]` ·
  `prv_flows.program_stock_intraday`(scope scan)
- `sweep` → `program.stock_sweep`(18:10) · `program_stock_daily@<종목>:<날>[거래소]`(event —
  처리기가
  직접 선점) · `prv_flows.program_stock_daily`(scope sweep)

- 시장 2개(K 코스피·Q 코스닥) × 켠 거래소(`config/markets.yaml kis.venues` — 끈 거래소 키는 부르지
  않고 `venues_off`). 한 시장·한 종목의 실패는 다른 것을 멈추지 않는다(받은 것은 쓴다 — 절대 규칙
  4).
- 검산 ④(전체 = 차익 + 비차익, 위탁 + 자기 = 합계)를 쓰기 전에 하고 어긋난 묶음은 `invalid` 로 쓴다.
- 장중 시장 행: ts = 슬롯 분, 슬롯 + 45초 안 `ok`·늦으면 `estimated`. 장중 종목 행: `estimated`
  (metrics §22). 마감 훑기 행: `ok`.
- **장중 훑기 범위**(F2 §1.4 — 프로그램 순위 TR 이 KIS 에 없다): 그 시각까지의 마지막 거래대금 순위
  (`prv_market.turnover_rank_intraday` — P3 `flows.intraday`) 상위 `program.scan_top_n` + 관심종목
  (`prv_journal.watchlist` main, 앞 `scan_watch_max`). 행 ts = 슬롯 시작 + `SCAN_OFFSET_MIN`(등록부
  `open+5` — 재시도도 같은 ts 에 쓰고, 이미 받은 종목은 다시 부르지 않는다).
- **마감 훑기**: 그날 유니버스(ETF·ETN 제외)의 코스피·코스닥 전 종목을
  `program_stock_daily@<종목>:<날>`
  키로 하나씩 선점(`claims` 자원 — D-P4-25 ②)해 부른다. 이미 받은 종목(`swept_codes`)·남이 잡은 키는
  건너뛴다 → 끊기면 다음 실행이 **이어서** 받는다. 결과 `collected` 에는 넣지 않는다(event 키).
- 투자자별 프로그램(HHPPG046600C1)은 **당일** TR 이다 — 백필(지난 날)에서는 부르지 않고 0행으로
  끝낸다
  (지난 날 값을 오늘 값으로 쓰지 않는다 — detail `investor_today_past`).
- 텔레그램 없음(F2 §3.7).

자원: `repos`, `kis`, `markets`, `claims`(sweep), `kis_priority`(시험) — 없으면 운영 기본(_p3 와
같다).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal, Protocol

from kbj.config.markets import MarketsConfig
from kbj.core.p5_rows import ProgramInvestorRow, ProgramMarketRow, ProgramStockRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.data.private.kis.parse import KisEmpty
from kbj.data.private.kis.program import (
    PROGRAM_TRS,
    params_investor_today,
    params_market_daily,
    params_market_intraday,
    params_stock_daily,
    params_stock_intraday,
    parse_investor_today,
    parse_market_daily,
    parse_market_intraday,
    parse_stock_daily,
    parse_stock_intraday,
)
from kbj.data.ratelimit import Priority
from kbj.data.spec import DataKey
from kbj.engines.flows.minute import slot_quality
from kbj.engines.flows.program import MARKET_NAME, apply_program_checks, scan_codes, sweep_universe
from kbj.services.collectors._p3 import (
    KisGetter,
    NotReady,
    Outcome,
    PartialFailure,
    kis_client,
    markets,
    reason,
    slot_ts,
    split_venues,
)
from kbj.services.collectors.flows_minute import call_tr, kis_priority, minute_ts
from kbj.services.runtime.log import log_event
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import JournalRepo, MarketRepo, ProgramRepo, pg_repos

__all__ = [
    "MARKETS",
    "SCAN_OFFSET_MIN",
    "SOURCE",
    "backfill",
    "daily",
    "intraday",
    "scan",
    "scan_targets",
    "sweep",
    "sweep_targets",
]

SOURCE: Final = "KIS"
MARKETS: Final[tuple[Literal["K", "Q"], ...]] = ("K", "Q")
SCAN_OFFSET_MIN: Final = 5  # 등록부 program.stock_scan `open+5`(P3 10분 슬롯과 5분 엇갈림 — R5)
TIMEOUT_MINUTE_S: Final = 20.0
TIMEOUT_S: Final = 60.0
LOADED_BY: Final = {
    "intraday": "program.intraday",
    "daily": "program.daily",
    "backfill": "program.backfill",
    "scan": "program.stock_scan",
    "sweep": "program.stock_sweep",
}
SERVICE: Final = "scheduler"
log = logging.getLogger("kbj.services.collectors")


class ProgramRepos(Protocol):
    @property
    def market(self) -> MarketRepo: ...

    @property
    def program(self) -> ProgramRepo: ...

    @property
    def journal(self) -> JournalRepo: ...


def _repos(ctx: JobContext) -> ProgramRepos:
    got = ctx.resources.get("repos")
    return got if got is not None else pg_repos(ctx.resource("connect"))


@dataclass
class _Run:
    ctx: JobContext
    kis: KisGetter
    repos: ProgramRepos
    cfg: MarketsConfig
    priority: Priority
    loaded_by: str
    detail: dict[str, Any] = field(default_factory=dict[str, Any])

    def get(self, name: str, params: dict[str, str], what: str, timeout: float) -> dict[str, Any]:
        return call_tr(
            self.kis, PROGRAM_TRS[name], params, what=what, priority=self.priority, timeout=timeout
        )

    def bump(self, name: str, n: int = 1) -> None:
        self.detail[name] = int(self.detail.get(name, 0)) + n


def _each_market(run: _Run, what: str, fn: Callable[[Literal["K", "Q"]], int]) -> int:
    """시장마다 fn — 한 시장 실패는 다른 시장을 멈추지 않는다. 모두 공표 전이면 NotReady, 하나라도
    실패면(받은 것은 쓴 뒤) PartialFailure."""
    rows = 0
    errors: dict[str, str] = {}
    empty: dict[str, str] = {}
    for m in MARKETS:
        try:
            rows += fn(m)
        except KisEmpty as e:
            empty[m] = reason(e)
        except Exception as e:  # 격리 — 이 시장만 미완
            errors[m] = reason(e)
            log_event(log, logging.WARNING, SERVICE, "program_market_failed", job=run.ctx.job,
                      what=what, market=m, error=errors[m])  # fmt: skip
    if errors:
        run.detail.setdefault("errors", {})[what] = errors
        raise PartialFailure(f"{what}: 시장 {len(errors)}개 실패 — {next(iter(errors.values()))}")
    if empty and len(empty) == len(MARKETS):
        raise NotReady(f"{what}: 아직 공표 전 — {next(iter(empty.values()))}")
    if empty:
        run.detail.setdefault("empty", {})[what] = sorted(empty)
    return rows


def _checked_put_minutes(run: _Run, rows: list[ProgramMarketRow], *, daily: bool) -> int:
    checked, fails = apply_program_checks(rows)
    if fails:
        run.bump("check4_failed", len(fails))
    if daily:
        return run.repos.program.put_market_days(
            checked, loaded_by=run.loaded_by, received_at=run.ctx.now
        )
    return run.repos.program.put_market_minutes(
        checked, loaded_by=run.loaded_by, received_at=run.ctx.now
    )


def _start(ctx: JobContext, mode: str, role: str, default: Priority, kis: KisGetter) -> _Run:
    return _Run(
        ctx, kis, _repos(ctx), markets(ctx), kis_priority(ctx, role, default), LOADED_BY[mode]
    )


def _keyed(
    ctx: JobContext,
    run: _Run,
    handlers: dict[str, Callable[[_Run, DataKey], int]],
) -> JobResult:
    out = Outcome(ctx.job, tuple(ctx.keys))
    run.detail = out.detail
    active, off = split_venues(ctx.keys, run.cfg.kis.venues)
    out.skip_off(off)
    for key in sorted(active, key=DataKey.label):
        fn = handlers.get(key.dataset)
        if fn is None:
            out.errors[key.label()] = f"모르는 데이터셋: {key.dataset}"
            continue
        out.run(key, lambda fn=fn, key=key: fn(run, key))
    return out.result()


# ── 시장 장중(매분) ─────────────────────────────────────────────────────────────────────


def _market_minute(run: _Run, key: DataKey) -> int:
    ts = minute_ts(key.as_of)
    venue = key.venue or "KRX"
    q = slot_quality(ts, run.ctx.now)
    if q != Quality.OK:
        run.detail["late_s"] = int((run.ctx.now - ts).total_seconds())

    def one(m: Literal["K", "Q"]) -> int:
        body = run.get(
            "program_market_intraday", params_market_intraday(m, venue),
            f"FHPPG04600101 {m}", TIMEOUT_MINUTE_S,
        )  # fmt: skip
        rows = parse_market_intraday(
            body, ts=ts, trade_date=ts.date(), market=m, venue=venue, quality=q
        )
        return _checked_put_minutes(run, rows, daily=False)

    return _each_market(run, "program_market_intraday", one)


def intraday(ctx: JobContext) -> JobResult:
    """등록부 처리기(`…program:intraday` — `program.intraday`)."""
    if not ctx.keys:
        return JobResult("skipped", detail={"reason": "선점한 키가 없다"})
    with kis_client(ctx) as kis:
        run = _start(ctx, "intraday", "program_intraday", Priority.P2, kis)
        return _keyed(ctx, run, {"program_market_intraday": _market_minute})


# ── 시장 일별·투자자별(마감 뒤·백필) ─────────────────────────────────────────────────────


def _market_day(run: _Run, key: DataKey) -> int:
    day = date.fromisoformat(key.as_of[:10])
    venue = key.venue or "KRX"

    def one(m: Literal["K", "Q"]) -> int:
        body = run.get(
            "program_market_daily", params_market_daily(m, venue, day, day),
            f"FHPPG04600001 {m}", TIMEOUT_S,
        )  # fmt: skip
        rows = parse_market_daily(body, day=day, market=m, venue=venue)
        return _checked_put_minutes(run, rows, daily=True)

    return _each_market(run, "program_market_daily", one)


def _investor_day(run: _Run, key: DataKey) -> int:
    day = date.fromisoformat(key.as_of[:10])
    venue = key.venue or "KRX"
    today = run.ctx.now.astimezone(KST).date()
    if day != today:
        # 당일 TR — 지난 날을 부르면 오늘 값이 온다(지어내지 않는다). 0행으로 끝낸다
        run.detail["investor_today_past"] = key.as_of[:10]
        return 0

    def one(m: Literal["K", "Q"]) -> int:
        body = run.get(
            "program_investor_today", params_investor_today(m, venue),
            f"HHPPG046600C1 {m}", TIMEOUT_S,
        )  # fmt: skip
        rows: list[ProgramInvestorRow] = parse_investor_today(
            body, trade_date=day, market=m, venue=venue
        )
        return run.repos.program.put_investor_days(
            rows, loaded_by=run.loaded_by, received_at=run.ctx.now
        )

    return _each_market(run, "program_investor_today", one)


_DAILY: Final[dict[str, Callable[[_Run, DataKey], int]]] = {
    "program_market_daily": _market_day,
    "program_investor_today": _investor_day,
}


def daily(ctx: JobContext) -> JobResult:
    """등록부 처리기(`…program:daily` — `program.daily`, close+20)."""
    if not ctx.keys:
        return JobResult("skipped", detail={"reason": "선점한 키가 없다"})
    with kis_client(ctx) as kis:
        run = _start(ctx, "daily", "close_collect", Priority.P3, kis)
        return _keyed(ctx, run, _DAILY)


def backfill(ctx: JobContext) -> JobResult:
    """등록부 처리기(`…program:backfill` — `program.backfill`, 수동: `python -m
    kbj.services.scheduler backfill program.backfill --from … --to …`). 키는 `program.daily` 것."""
    if not ctx.keys:
        return JobResult("skipped", detail={"reason": "선점한 키가 없다"})
    with kis_client(ctx) as kis:
        run = _start(ctx, "backfill", "backfill", Priority.P4, kis)
        return _keyed(ctx, run, _DAILY)


# ── 종목 장중 훑기(10분) ─────────────────────────────────────────────────────────────────


def scan_targets(
    r: ProgramRepos, day: date, ts: datetime, market: str, *, top_n: int, watch_max: int
) -> list[str]:
    """그 시각까지 마지막 거래대금 순위(그 시장) 상위 top_n + 관심종목(그 시장) 앞 watch_max."""
    name = MARKET_NAME[market]
    start = datetime.combine(day, datetime.min.time(), tzinfo=KST)
    ranks = [q for q in r.market.rank_rows(start, ts + timedelta(minutes=1)) if q.market == name]
    ranked: list[str] = []
    if ranks:
        last = max(q.ts for q in ranks)
        ranked = [q.code for q in sorted((q for q in ranks if q.ts == last), key=lambda q: q.rank)]
    uni = {u.code: u.market for u in r.market.universe(day)}
    watch = [w.code for w in r.journal.watchlist("main") if uni.get(w.code) == name]
    return scan_codes(ranked, watch, top_n=top_n, watch_max=watch_max)


def _stock_scan(run: _Run, key: DataKey) -> int:
    ts = slot_ts(key.as_of) + timedelta(minutes=SCAN_OFFSET_MIN)
    day = ts.date()
    venue = key.venue or "KRX"
    cfg = run.cfg.program
    rows: list[ProgramStockRow] = []
    targets = 0
    errors: dict[str, str] = {}
    for m in MARKETS:
        codes = scan_targets(
            run.repos, day, ts, m, top_n=cfg.scan_top_n, watch_max=cfg.scan_watch_max
        )
        targets += len(codes)
        have = {x.code for x in run.repos.program.stock_scan(day, m, venue, ts)}
        for code in codes:
            if code in have:
                run.bump("already")
                continue
            try:
                body = run.get(
                    "program_stock_intraday", params_stock_intraday(code, venue),
                    "FHPPG04650101", TIMEOUT_S,
                )  # fmt: skip
                rows.append(
                    parse_stock_intraday(
                        body, code=code, ts=ts, trade_date=day, market=m, venue=venue
                    )
                )
            except KisEmpty:
                run.bump("empty")
            except Exception as e:  # 격리 — 이 종목만 미완(재시도가 남은 종목을 받는다)
                errors[code] = reason(e)
    if rows:
        run.repos.program.put_stock_scan(rows, loaded_by=run.loaded_by, received_at=run.ctx.now)
    run.detail["targets"] = targets
    run.detail["scope"] = f"거래대금 상위 {cfg.scan_top_n} + 관심종목"
    if errors:
        run.detail["failed"] = len(errors)
        raise PartialFailure(f"종목 {len(errors)}개 실패 — {next(iter(errors.values()))}")
    if targets == 0:
        raise NotReady("훑을 종목이 없다 — 거래대금 순위(flows.intraday)·관심종목이 아직 없다")
    return len(rows)


def scan(ctx: JobContext) -> JobResult:
    """등록부 처리기(`…program:scan` — `program.stock_scan`, 10분)."""
    if not ctx.keys:
        return JobResult("skipped", detail={"reason": "선점한 키가 없다"})
    with kis_client(ctx) as kis:
        run = _start(ctx, "scan", "program_scan", Priority.P3, kis)
        return _keyed(ctx, run, {"program_stock_intraday": _stock_scan})


# ── 마감 뒤 전 종목 훑기(18:10 — 이어 받기) ──────────────────────────────────────────────


def sweep_targets(r: ProgramRepos, day: date, markets_: Sequence[str]) -> list[tuple[str, str]]:
    """(종목, 시장 K·Q) — 그날 유니버스의 코스피·코스닥 종목(ETF·ETN 제외), 코드 순."""
    return sweep_universe(r.market.universe(day), markets_)


def sweep(ctx: JobContext) -> JobResult:
    """등록부 처리기(`…program:sweep` — `program.stock_sweep`). 키는 처리기가 직접 선점한다."""
    day = date.fromisoformat(ctx.as_of[:10])
    claims = ctx.resource("claims")
    with kis_client(ctx) as kis:
        run = _start(ctx, "sweep", "program_sweep", Priority.P4, kis)
        targets = sweep_targets(run.repos, day, run.cfg.program.sweep_markets)
        if not targets:
            return JobResult(
                "not_ready", detail={"reason": f"{day} 유니버스가 없다 — 훑을 종목을 정할 수 없다"}
            )
        rows = 0
        failed: list[str] = []
        for venue in run.cfg.kis.venues:
            have = run.repos.program.swept_codes(day, venue)
            run.bump("already", sum(1 for c, _ in targets if c in have))
            for code, m in targets:
                if code in have:
                    continue
                key = DataKey(SOURCE, "program_stock_daily", f"{code}:{day.isoformat()}", venue)
                if not claims.claim(key, ctx.job, ctx.run_id, ctx.now):
                    run.bump("duplicate")
                    continue
                try:
                    body = run.get(
                        "program_stock_daily", params_stock_daily(code, venue, day),
                        "FHPPG04650201", TIMEOUT_S,
                    )  # fmt: skip
                    row = parse_stock_daily(body, code=code, day=day, market=m, venue=venue)
                except KisEmpty:
                    run.bump("empty")
                    claims.complete(key, ctx.run_id, 0, ctx.now)
                    continue
                except Exception as e:  # 격리 — 이 종목만 실패(다음 실행이 이어서 받는다)
                    why = reason(e)
                    claims.fail(key, ctx.run_id, why, ctx.now)
                    failed.append(why)
                    continue
                n = run.repos.program.put_stock_days(
                    [row], loaded_by=run.loaded_by, received_at=ctx.now
                )
                claims.complete(key, ctx.run_id, n, ctx.now)
                rows += n
        detail: dict[str, Any] = {**run.detail, "targets": len(targets), "rows": rows}
        detail["venues"] = list(run.cfg.kis.venues)
    if failed:
        detail["failed"] = len(failed)
        detail["reason"] = f"종목 {len(failed)}개 실패 — {failed[0]}(다음 실행이 이어서 받는다)"
        return JobResult("failed", rows=rows, detail=detail)
    return JobResult("ok", rows=rows, detail=detail)

"""`flows.intraday`·`market.intraday` — 장중 10분 슬롯 수집(잠정, quality estimated).

근거: docs/p3_design.md §1.2·§3.2·§3.5·§3.7·§8.3, D-P3-7·14. 완료 기준 '장중 1시간 무결측'의
처리기다.

`flows(ctx)` — 선점한 키(as_of = 슬롯 시작 `YYYY-MM-DDTHH:MM` KST):
- `inst_foreign_intraday`[거래소] → FHPTJ04400000 시장 2 × 구분(외국인·기관) 2 = 4건. 이력
  `prv_flows.investor_intraday`(ts = 슬롯 시작, source `kis.prelim`) + 일별 원장 오늘 행
  (`prv_flows.stock_investor_daily`, source `kis.prelim`, estimated, revise=False — 마감 확정이
  덮는다). 가집계 회차 전의 빈 목록은 0행(실패 아님).
- `turnover_rank_intraday`[거래소] → FHPST01710000 시장 2건. 이력
  `prv_market.turnover_rank_intraday` + 목록 종목의 오늘 잠정 스냅(`prv_market.stock_snapshot`,
  source `kis.prelim`, estimated).
- `etf_quote_intraday` → FHPST02400000 × 감시 ETF(순자산 상위 `config/markets.yaml`
  `etf.watch_top_n` — 그날 이하 최근 KRX ETF 일별 기준). 이력 `prv_etf.quote_intraday`. 감시 목록을
  정할 ETF 일별이 없으면 실패(지어내지 않는다).

`market(ctx)`:
- `index_quote_intraday` → FHPUP02100000 × `config/markets.yaml`
  `market.intraday_indices`(코스피·코스닥· 코스피200) → `prv_market.index_intraday`.
- `sector_quote_intraday` → FHPUP02140000 × 시장 2 → `prv_market.sector_intraday`.

무결측(D-P3-14): 등록부가 슬롯 안에서 재시도한다(`retry {max: 2, backoff_s: [20, 40]}`,
`deadline_min: 9`). 이 처리기는 **키마다 따로** 받고, 실패한 키만 돌려주지 않는다 — 재시도는 그 키만
다시 잡는다. 같은 슬롯을 다시 써도 기본 키(ts 포함)가 같아 덮어쓴다(멱등). KIS 허가 대기는 한 호출에
`TIMEOUT_S`(30초)까지 — 넘기면 그 키는 미완(슬롯 안 재시도). 우선순위 P2(투자자별·다음 주기로 —
`kbj.data.ratelimit.Priority`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from kbj.config.markets import MarketsConfig
from kbj.core.rows import EtfQuote, IntradayInvestor, RankRow, SectorQuote, Snap
from kbj.data.private.kis.etf import params_etf_quote, parse_etf_quote
from kbj.data.private.kis.index import (
    SECTOR_MARKETS,
    params_index_quote,
    params_sector_quotes,
    parse_index_quote,
    parse_sector_quotes,
)
from kbj.data.private.kis.investors import (
    INST_FOREIGN_WHO,
    merge_inst_foreign,
    params_inst_foreign,
    parse_inst_foreign_total,
    prelim_days,
)
from kbj.data.private.kis.ranks import (
    MARKET_CODES,
    params_volume_rank,
    parse_rank_snaps,
    parse_volume_rank,
)
from kbj.data.ratelimit import Priority
from kbj.data.spec import DataKey
from kbj.services.collectors._p3 import (
    KisGetter,
    Outcome,
    RepoSet,
    day_of,
    kis_client,
    kis_get,
    markets,
    repos,
    slot_ts,
    split_venues,
)
from kbj.services.scheduler.handlers import JobContext, JobResult

__all__ = ["PRIORITY", "TIMEOUT_S", "flows", "market", "watch_list"]

PRIORITY: Final = Priority.P2
TIMEOUT_S: Final = 30.0
INVESTOR_MARKETS: Final = ("0001", "1001")  # 코스피·코스닥 [추정 #23]


@dataclass
class _Ctx:
    repos: RepoSet
    kis: KisGetter
    ts: datetime
    day: date
    now: datetime
    job: str
    cfg: MarketsConfig
    detail: dict[str, Any]


def _get(c: _Ctx, name: str, params: dict[str, str], what: str) -> dict[str, Any]:
    return kis_get(c.kis, name, params, what=what, priority=PRIORITY, timeout=TIMEOUT_S)


def watch_list(r: RepoSet, day: date, top_n: int) -> list[str]:
    """감시 ETF — 그날 이하 최근 KRX ETF 일별의 순자산 상위 top_n(같으면 코드 순)."""
    latest = r.etf.etf_days(day, 1)
    rows = [v[-1] for v in latest.values() if v and v[-1].net_asset is not None]
    if not rows:
        raise RuntimeError(f"{day} 이하 ETF 일별(순자산)이 없다 — 감시 목록을 정할 수 없다")
    rows.sort(key=lambda d: (-(d.net_asset or 0), d.code))
    return [d.code for d in rows[:top_n]]


# ── flows.intraday ───────────────────────────────────────────────────────────────────────


def _inst_foreign(c: _Ctx, venue: str) -> int:
    lists: list[tuple[str, list[IntradayInvestor]]] = []
    for market in INVESTOR_MARKETS:
        for who in INST_FOREIGN_WHO:
            body = _get(c, "inst_foreign_total", params_inst_foreign(market, who, venue),
                        f"FHPTJ04400000 {market} {who}")  # fmt: skip
            lists.append((who, parse_inst_foreign_total(body, ts=c.ts, venue=venue)))
    rows = merge_inst_foreign(lists)
    c.repos.flows.put_intraday(rows, loaded_by=c.job, received_at=c.now)
    rep = c.repos.flows.upsert_investor_days(
        prelim_days(rows, c.day), revise=False, loaded_by=c.job, now=c.now, received_at=c.now
    )
    c.detail["inst_foreign_intraday"] = {"rows": len(rows), "ledger": rep.written}
    if rep.skipped:
        c.detail["inst_foreign_intraday"]["ledger_skipped"] = rep.skipped  # 확정이 이미 있다
    return len(rows)


def _ranks(c: _Ctx, venue: str) -> int:
    rows: list[RankRow] = []
    snaps: list[Snap] = []
    for market in MARKET_CODES:
        body = _get(c, "volume_rank", params_volume_rank(market, venue), f"FHPST01710000 {market}")
        rows += parse_volume_rank(body, market=market, ts=c.ts, venue=venue)
        snaps += parse_rank_snaps(body, market=market, day=c.day, venue=venue)
    c.repos.market.put_rank_rows(rows, loaded_by=c.job, received_at=c.now)
    c.repos.market.upsert_snapshots(snaps, loaded_by=c.job, received_at=c.now)
    c.detail["turnover_rank_intraday"] = {"rows": len(rows), "snaps": len(snaps)}
    return len(rows)


def _etf_quotes(c: _Ctx, _venue: str) -> int:
    codes = watch_list(c.repos, c.day, c.cfg.etf.watch_top_n)
    quotes: list[EtfQuote] = []
    for code in codes:
        body = _get(c, "etf_quote", params_etf_quote(code), f"FHPST02400000 {code}")
        quotes.append(parse_etf_quote(body, code, c.ts))
    c.repos.etf.put_quotes(quotes, loaded_by=c.job, received_at=c.now)
    c.detail["etf_quote_intraday"] = {"rows": len(quotes)}
    return len(quotes)


# ── market.intraday ──────────────────────────────────────────────────────────────────────


def _indices(c: _Ctx, _venue: str) -> int:
    quotes = []
    for code, name in c.cfg.market.intraday_indices.items():
        body = _get(c, "index_quote", params_index_quote(code), f"FHPUP02100000 {code}")
        quotes.append(parse_index_quote(body, code, c.ts, name=name))
    c.repos.market.put_index_quotes(quotes, loaded_by=c.job, received_at=c.now)
    return len(quotes)


def _sectors(c: _Ctx, _venue: str) -> int:
    rows: list[SectorQuote] = []
    for market in SECTOR_MARKETS:
        body = _get(c, "sector_quotes", params_sector_quotes(market), f"FHPUP02140000 {market}")
        rows += parse_sector_quotes(body, market, c.ts)
    c.repos.market.put_sector_quotes(rows, loaded_by=c.job, received_at=c.now)
    return len(rows)


_FLOWS: Final[dict[str, Callable[[_Ctx, str], int]]] = {
    "inst_foreign_intraday": _inst_foreign,
    "turnover_rank_intraday": _ranks,
    "etf_quote_intraday": _etf_quotes,
}
_MARKET: Final[dict[str, Callable[[_Ctx, str], int]]] = {
    "index_quote_intraday": _indices,
    "sector_quote_intraday": _sectors,
}


def collect(
    keys: Sequence[DataKey], c: _Ctx, handlers: dict[str, Callable[[_Ctx, str], int]]
) -> Outcome:
    out = Outcome(c.job, tuple(keys))
    c.detail = out.detail
    active, off = split_venues(keys, c.cfg.kis.venues)
    out.skip_off(off)
    for key in sorted(active, key=lambda k: (k.dataset, k.venue)):
        fn = handlers.get(key.dataset)
        if fn is None:
            out.errors[key.label()] = f"모르는 데이터셋: {key.dataset}"
            continue
        out.run(key, lambda fn=fn, v=key.venue: fn(c, v or "KRX"))
    return out


def _run(ctx: JobContext, handlers: dict[str, Callable[[_Ctx, str], int]]) -> JobResult:
    if not ctx.keys:
        return JobResult("skipped", detail={"reason": "선점한 키가 없다"})
    ts = slot_ts(ctx.as_of)
    with kis_client(ctx) as kis:
        c = _Ctx(repos(ctx), kis, ts, day_of(ctx.as_of), ctx.now, ctx.job, markets(ctx), {})
        out = collect(ctx.keys, c, handlers)
    return out.result()


def flows(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.market_intraday:flows` — `flows.intraday`)."""
    return _run(ctx, _FLOWS)


def market(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.market_intraday:market` — `market.intraday`)."""
    return _run(ctx, _MARKET)

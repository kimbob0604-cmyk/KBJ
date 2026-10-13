"""ETF 맵·보유 탐색·액티브 동시 편입 readers(docs/p5_design.md §4.4·§5, metrics §19 — 로그인).

`prv_etf.*` → `kbj.engines.etf.{map,holders}` → 모델. P3 ETF reader(`readers/etf.py`)와 파일을
나눴다.

- `/api/etf/map`: 마지막 확정 ETF 일별(오늘 이하) 기준 셀·묶음. 유니버스(순자산 50억↑·20일 거래대금
  1억↑) 밖은 notes 에 수만 적는다. 레버리지·인버스는 `lev=true` 일 때만.
- `/api/etf/fund/{code}/holdings`: 펀드 id 또는 상장 코드 → 오늘 이하 마지막 PDF 스냅(비중
  내림차순) +
  그 스냅이 마지막 변동 실행에서 바뀐 종류(NEW·IN10·ADD·CUT — DROP·OUT10 은 스냅에 없다).
- `/api/etf/holders/{code}`: 종목 → 담은 ETF(펀드마다 마지막 스냅 — 운용사 기준일이 다르면 그대로).
  구성종목 스냅이 하나도 없으면 404, 있는데 담은 ETF 가 없으면 빈 목록(200).
- `/api/etf/active-consensus?days=N`: 마지막 변동 실행 N 번(오늘 이하) 안에서 같은 날 액티브 ETF 2개
  이상이 NEW·IN10·ADD 한 종목.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Final, Literal

from kbj.core.quality import Quality
from kbj.core.rows import Change, EtfType
from kbj.engines.etf import map as etf_map_engine
from kbj.engines.etf.holders import MIN_FUNDS, of_fund, of_stock
from kbj.engines.etf.holders import active_consensus as consensus_rows
from kbj.engines.flows.ledger import close_as_of
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.etf import (
    ActiveConsensus,
    ConsensusStock,
    EtfHolders,
    EtfHolderView,
    EtfHoldings,
    EtfHoldingView,
    EtfMap,
    EtfMapCellView,
    EtfMapGroup,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, label, worst

__all__ = ["NO_DATA", "active_consensus", "etf_holders", "etf_holdings", "etf_map"]

NO_DATA: Final = "아직 없음 — etf.collect 08:00 · krx.daily 08:05"
NO_HOLDINGS: Final = "아직 없음 — etf.collect 08:00(운용사 구성종목)"
PDF_NOTE: Final = "운용사 공시 PDF 기준일은 운용사마다 다르다 — 섞지 않고 펀드마다 그대로"
ISSUER_SOURCE: Final = "ETF_ISSUERS"
Period = Literal["1D", "1W", "1M", "3M"]
GroupBy = Literal["type", "theme"]
HoldKind = Literal["NEW", "IN10", "ADD", "CUT"]
_SNAP_KINDS: Final[frozenset[str]] = frozenset({"NEW", "IN10", "ADD", "CUT"})


def _period(v: str) -> Period:
    if v not in etf_map_engine.PERIOD_TDAYS:
        raise ValueError(f"모르는 기간: {v!r}")
    return v  # pyright: ignore[reportReturnType]


def _group(v: str) -> GroupBy:
    if v not in ("type", "theme"):
        raise ValueError(f"모르는 묶음: {v!r}")
    return v  # pyright: ignore[reportReturnType]


def _issuer_source(sources: list[str]) -> str:
    """보유 행 원천(`ETF_ISSUERS:<운용사>`)의 묶음 이름 — 운용사 이름은 싣지 않는다."""
    return "+".join(sorted({s.split(":", 1)[0] for s in sources if s})) or ISSUER_SOURCE


def etf_map(ctx: ReadContext, *, period: str, group: str, lev: bool) -> Envelope[EtfMap]:
    p, g = _period(period), _group(group)
    last = ctx.repos.etf.etf_days(ctx.today(), 1)
    ends = [d.date for rows in last.values() for d in rows]
    if not ends:
        raise NoData(NO_DATA)
    end = max(ends)
    days = ctx.repos.etf.etf_days(end, etf_map_engine.WINDOW)
    dates = sorted({r.date for rows in days.values() for r in rows})
    cells, ex = etf_map_engine.cells(
        ctx.repos.etf.meta(), days, dates, p, splits=ctx.repos.etf.split_events(), lev=lev
    )
    groups = [
        EtfMapGroup(
            key=gr.key,
            label=gr.label,
            cells=[
                EtfMapCellView(
                    code=c.code,
                    name=c.name,
                    etf_type=None if c.etf_type is None else EtfType(c.etf_type),
                    theme=c.theme,
                    net_asset=c.net_asset,
                    ret_pct=c.ret_pct,
                    leveraged=c.leveraged,
                    new_listing=c.new_listing,
                )
                for c in gr.cells
            ],
        )
        for gr in etf_map_engine.build_groups(cells, g)
    ]
    notes: list[str] = []
    if ex.total:
        notes.append(
            f"유니버스 밖 {ex.total}개(순자산 50억 미만 {ex.small} · 20일 거래대금 1억 미만 "
            f"{ex.illiquid} · 시세 없음 {ex.no_data})"
        )
    if any(c.new_listing for c in cells):
        notes.append("신규 상장(이력이 기간보다 짧음)은 수익률 없이 회색")
    if g == "theme":
        notes.append("테마 묶음은 국내주식(국내 대표지수·국내 테마)만")
    if not lev:
        notes.append("레버리지·인버스 숨김(lev=true 로 보기)")
    return envelope(
        EtfMap(period=p, group=g, lev=lev, asof=end, groups=groups),
        source=label(c.source for c in cells),
        as_of=close_as_of(end),
        quality=worst(c.quality for c in cells) if cells else Quality.OK,
        notes=notes,
        generated_at=ctx.now(),
    )


def _last_kinds(ctx: ReadContext, fund_id: str, asof: date) -> dict[str, HoldKind]:
    run = ctx.repos.etf.last_change_run(ctx.today())
    if run is None:
        return {}
    out: dict[str, HoldKind] = {}
    for c in ctx.repos.etf.changes(run):
        if c.fund_id == fund_id and c.asof == asof and c.kind in _SNAP_KINDS:
            out[c.code] = c.kind  # pyright: ignore[reportArgumentType]
    return out


def etf_holdings(ctx: ReadContext, *, code: str) -> Envelope[EtfHoldings]:
    rows = of_fund(ctx.repos.etf.holdings_of(code, ctx.today()))
    if not rows:
        raise NoData(NO_HOLDINGS)
    first = rows[0]
    kinds = _last_kinds(ctx, first.fund_id, first.asof)
    return envelope(
        EtfHoldings(
            fund_id=first.fund_id,
            etf_code=first.etf_code,
            etf_name=first.etf_name,
            asof=first.asof,
            rows=[
                EtfHoldingView(
                    code=h.code,
                    name=h.name,
                    wt=h.wt,
                    qty=h.qty,
                    val=h.val,
                    change=kinds.get(h.code),
                )
                for h in rows
            ],
        ),
        source=_issuer_source([h.source for h in rows]),
        as_of=close_as_of(first.asof),
        quality=worst(h.quality for h in rows),
        notes=[PDF_NOTE],
        generated_at=ctx.now(),
    )


def etf_holders(ctx: ReadContext, *, code: str) -> Envelope[EtfHolders]:
    if not ctx.repos.etf.holding_dates():
        raise NoData(NO_HOLDINGS)
    today = ctx.today()
    rows = of_stock(ctx.repos.etf.holders_of(code, today), code)
    name = next((h.name for h in rows if h.name), None)
    asof = max((h.asof for h in rows), default=today)
    return envelope(
        EtfHolders(
            code=code,
            name=name,
            rows=[
                EtfHolderView(
                    fund_id=h.fund_id,
                    etf_code=h.etf_code,
                    etf_name=h.etf_name,
                    is_active=h.is_active,
                    asof=h.asof,
                    wt=h.wt,
                    qty=h.qty,
                    val=h.val,
                )
                for h in rows
            ],
        ),
        source=_issuer_source([h.source for h in rows]),
        as_of=close_as_of(asof),
        quality=worst(h.quality for h in rows) if rows else Quality.OK,
        notes=[PDF_NOTE] + ([] if rows else ["이 종목을 담은 ETF 가 없다(추적 펀드 기준)"]),
        generated_at=ctx.now(),
    )


def _runs(ctx: ReadContext, days: int) -> list[date]:
    out: list[date] = []
    upto = ctx.today()
    while len(out) < days:
        run = ctx.repos.etf.last_change_run(upto)
        if run is None:
            break
        out.append(run)
        upto = run - timedelta(days=1)
    return out


def active_consensus(ctx: ReadContext, *, days: int) -> Envelope[ActiveConsensus]:
    if days < 1:
        raise ValueError("days 는 1 이상")
    runs = _runs(ctx, days)
    if not runs:
        raise NoData(NO_HOLDINGS)
    changes: list[Change] = [c for r in runs for c in ctx.repos.etf.changes(r)]
    funds = ctx.repos.etf.funds()
    rows = consensus_rows(changes, funds, MIN_FUNDS)
    used = {c.fund_id for c in changes}
    return envelope(
        ActiveConsensus(
            days=days,
            min_funds=MIN_FUNDS,
            asof=runs[0],
            rows=[
                ConsensusStock(
                    code=r.code,
                    name=r.name,
                    funds=list(r.funds),
                    kinds=list(r.kinds),
                    n_funds=r.n_funds,
                )
                for r in rows
            ],
        ),
        source=_issuer_source([f.source for fid, f in funds.items() if fid in used]),
        as_of=close_as_of(runs[0]),
        quality=Quality.OK,
        notes=[
            "같은 날 액티브 ETF 2개 이상이 새로 담았거나(NEW·IN10) 늘린(ADD) 종목 — 1개는 제외",
            f"변동 실행 {len(runs)}회({runs[-1].isoformat()} ~ {runs[0].isoformat()})",
        ],
        generated_at=ctx.now(),
    )

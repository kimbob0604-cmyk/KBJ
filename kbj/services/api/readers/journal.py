"""저널 리더 — `/api/journal/*`(분석 일지·매매 기록·추천 성과 — docs/p8_design.md §4.7·§5, 로그인).
본문은 JNL(웨이브 2): `ctx.repos.journal`(읽기)·prefill 은 P4 밸류에이션·시나리오·컨센서스 저장소와
원장 일봉 읽기(`kbj.engines.journal.prefill`).

- 목록(일지·매매)이 비면 200 + 빈 목록(안내는 notes) — 아직 쓰지 않은 것이지 수집이 늦은 것이
  아니다. 없는 id 는 404(no_data).
- 추천 성과는 이관분(SD `recommendation_history`)만 — 없으면 404(화면은 패널을 숨긴다). 사후 수익
  d1~d7 = 추천일 뒤 k 번째 거래일 확정 종가 ÷ 추천가 − 1(%) — 추천가가 없으면 추천일 종가, 둘 다
  없거나 그날 종가가 없으면 None.
- 개인 기록이라 원천 표기는 'KBJ'(사용자 입력), 품질 ok. prefill 은 읽은 행의 원천·품질을 합친다.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Final, cast

from kbj.core.quality import Quality
from kbj.core.rules_rows import AnalysisRow, PickRow, TradeRow
from kbj.engines.flows.ledger import close_as_of
from kbj.engines.journal.markdown import to_markdown
from kbj.engines.journal.prefill import (
    RET_52W_DAYS,
    PrefillInput,
    ScenarioCase,
    build_prefill,
)
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.journal import (
    AnalysisFields,
    AnalysisList,
    AnalysisMarkdown,
    AnalysisOut,
    AnalysisPrefill,
    PickList,
    PickOut,
    Scenario3,
    SourceT,
    TradeList,
    TradeOut,
    ValueSourceT,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, label, worst

__all__ = [
    "NO_ANALYSIS",
    "NO_PICKS",
    "NO_TRADES",
    "PICK_HORIZONS",
    "SOURCE",
    "analysis",
    "analysis_env",
    "analysis_list",
    "analysis_markdown",
    "analysis_out",
    "analysis_prefill",
    "picks",
    "trade_env",
    "trade_out",
    "trades",
]

SOURCE: Final = "KBJ"
NO_ANALYSIS: Final = "아직 없음 — 분석 일지 없음(페이지 12 에서 작성·SD 이관)"
NO_TRADES: Final = "아직 없음 — 매매 기록 없음(페이지 12 에서 입력·CSV 가져오기)"
NO_PICKS: Final = "아직 없음 — 추천 이력 이관분 없음"
NO_ID: Final = "아직 없음 — 그 id 의 일지가 없다(지워졌거나 틀린 id)"
NO_PREFILL: Final = "아직 없음 — prefill 할 값 없음(밸류에이션·시나리오·컨센서스·일봉 수집 전)"
PICK_HORIZONS: Final = tuple(range(1, 8))
MAX_PICKS: Final = 1000
_VALUE_SOURCES: Final = ("kbj", "consensus", "manual")


def _f(v: Decimal | float | None) -> float | None:
    return None if v is None else float(v)


def _three(m: Mapping[str, Decimal | float | None]) -> Scenario3:
    return Scenario3(bear=_f(m.get("bear")), base=_f(m.get("base")), bull=_f(m.get("bull")))


def analysis_out(row: AnalysisRow) -> AnalysisOut:
    if row.id is None:  # pragma: no cover — 저장소 행은 id 가 있다
        raise ValueError("id 없는 일지")
    vs: dict[str, ValueSourceT] = {
        k: cast("ValueSourceT", v) for k, v in row.value_sources.items() if v in _VALUE_SOURCES
    }
    return AnalysisOut(
        id=row.id,
        code=row.code,
        analyzed_on=row.analyzed_on,
        chain_ids=list(row.chain_ids),
        price=_f(row.price),
        ret_52w=row.ret_52w,
        fwd_per=row.fwd_per,
        band_pos=row.band_pos,
        ev_ebitda=row.ev_ebitda,
        mktcap=None if row.mktcap is None else int(row.mktcap),
        opm=row.opm,
        eps=_three(row.eps),
        per=_three(row.per),
        tp=_three(row.tp),
        tp_source=row.tp_source,
        conviction=row.conviction,
        memo=row.memo,
        tags=list(row.tags),
        conclusion=row.conclusion,
        value_sources=vs,
        source=cast("SourceT", row.source),
        updated_at=row.updated_at,
    )


def trade_out(t: TradeRow) -> TradeOut:
    if t.id is None:  # pragma: no cover
        raise ValueError("id 없는 매매")
    return TradeOut(
        id=t.id,
        code=t.code,
        name_at_trade=t.name_at_trade,
        side=t.side,
        trade_date=t.trade_date,
        price=float(t.price),
        qty=t.qty,
        fee=float(t.fee),
        tax=float(t.tax),
        strategy=t.strategy,
        memo=t.memo,
        signal_id=t.signal_id,
        source=cast("SourceT", t.source),
        created_at=t.created_at,
    )


def _env[T](
    ctx: ReadContext, data: T, *, as_of: datetime | None, notes: Iterable[str] = ()
) -> Envelope[T]:
    now = ctx.now()
    return envelope(
        data, source=SOURCE, as_of=as_of or now, quality=Quality.OK, notes=notes, generated_at=now
    )


def analysis_env(ctx: ReadContext, row: AnalysisRow) -> Envelope[AnalysisOut]:
    return _env(ctx, analysis_out(row), as_of=row.updated_at)


def trade_env(ctx: ReadContext, row: TradeRow) -> Envelope[TradeOut]:
    return _env(ctx, trade_out(row), as_of=row.created_at)


def analysis_list(ctx: ReadContext, *, code: str | None) -> Envelope[AnalysisList]:
    rows = ctx.repos.journal.analyses(code=code)
    latest = max((r.updated_at for r in rows), default=None)
    data = AnalysisList(items=[analysis_out(r) for r in rows])
    return _env(ctx, data, as_of=latest, notes=[] if rows else [NO_ANALYSIS])


def _one(ctx: ReadContext, analysis_id: int) -> AnalysisRow:
    row = ctx.repos.journal.analysis(analysis_id)
    if row is None:
        raise NoData(NO_ID)
    return row


def analysis(ctx: ReadContext, analysis_id: int) -> Envelope[AnalysisOut]:
    return analysis_env(ctx, _one(ctx, analysis_id))


def _name(ctx: ReadContext, code: str) -> str | None:
    return next((u.name for u in ctx.repos.market.universe(ctx.today()) if u.code == code), None)


def analysis_markdown(ctx: ReadContext, analysis_id: int) -> Envelope[AnalysisMarkdown]:
    row = _one(ctx, analysis_id)
    md = AnalysisMarkdown(id=analysis_id, markdown=to_markdown(row, name=_name(ctx, row.code)))
    return _env(ctx, md, as_of=row.updated_at)


def trades(ctx: ReadContext, *, code: str | None) -> Envelope[TradeList]:
    """최근 매매 먼저(매매일·id 내림차순)."""
    rows = ctx.repos.journal.trades(code=code)
    latest = max((r.created_at for r in rows), default=None)
    data = TradeList(items=[trade_out(r) for r in reversed(rows)])
    return _env(ctx, data, as_of=latest, notes=[] if rows else [NO_TRADES])


# ── prefill ─────────────────────────────────────────────────────────────────────────


def analysis_prefill(ctx: ReadContext, code: str) -> Envelope[AnalysisPrefill]:
    today = ctx.today()
    cons = ctx.repos.consensus
    srcs: set[str] = set()
    quals: list[Quality | None] = []
    dates: list[date] = []
    vals = cons.valuation_days(code, today - timedelta(days=30), today)
    vd = vals[-1] if vals else None
    if vd is not None:
        srcs.add(vd.source)
        quals.append(vd.quality)
        dates.append(vd.trade_date)
    band = next((b for b in cons.bands(code) if b.metric == "per"), None)
    if band is not None and band.current_rank_pct is not None:
        srcs.add(band.source)
        quals.append(band.quality)
    scen = cons.scenarios(code)
    cases = {s.case: ScenarioCase(s.eps, s.multiple, s.tp, s.method) for s in scen}
    for s in scen:
        srcs.add(s.source)
        quals.append(s.quality)
    tgt = cons.target(code)
    if tgt is not None:
        srcs.add(tgt.source)
        quals.append(tgt.quality)
    snaps = cons.snapshots(code, today - timedelta(days=120), today)
    rev: int | None = None
    op: int | None = None
    if snaps:
        last = max(s.snap_date for s in snaps)
        est = sorted(
            (s for s in snaps if s.snap_date == last and s.is_estimate), key=lambda s: s.fiscal_ym
        )
        if est and est[0].revenue and est[0].op is not None:
            rev, op = est[0].revenue, est[0].op
            srcs.add(est[0].source)
            quals.append(est[0].quality)
    bars = ctx.repos.market.series([code], today, RET_52W_DAYS).get(code, [])
    if bars:
        srcs.add(bars[-1].source)
        quals.append(worst(b.quality for b in bars))
        dates.append(bars[-1].date)
    price = vd.price if vd is not None and vd.price is not None else None
    if price is None and bars:
        price = bars[-1].close
    ev: float | None = None
    if vd is not None:
        ev = vd.ev_ebitda_fwd if vd.ev_ebitda_fwd is not None else vd.ev_ebitda
    p = build_prefill(
        PrefillInput(
            code=code,
            day=today,
            price=price,
            mktcap=None if vd is None else vd.mktcap_company,
            fwd_per=None if vd is None else vd.per_fwd,
            ev_ebitda=ev,
            band_pos=None if band is None else band.current_rank_pct,
            closes=[b.close for b in bars],
            revenue_e=rev,
            op_e=op,
            scenarios=cases,
            target_mean=None if tgt is None else tgt.mean,
            target_n=None if tgt is None else tgt.n_brokers,
        )
    )
    if p.filled == 0:
        raise NoData(NO_PREFILL)
    fields = AnalysisFields(
        code=code,
        analyzed_on=today,
        price=p.price,
        ret_52w=p.ret_52w,
        fwd_per=p.fwd_per,
        band_pos=p.band_pos,
        ev_ebitda=p.ev_ebitda,
        mktcap=p.mktcap,
        opm=p.opm,
        eps=Scenario3(**p.eps),
        per=Scenario3(**p.per),
        tp=Scenario3(**p.tp),
        tp_source=p.tp_source,
        value_sources=dict(p.value_sources),
    )
    q = worst(quals) or Quality.OK
    as_of = close_as_of(max(dates)) if dates else None
    now = ctx.now()
    notes = ["밸류체인 칸은 사전 통합 전까지 빈칸", "값은 초안 — 저장 전에 확인"]
    return envelope(
        AnalysisPrefill(fields=fields, as_of=as_of, quality=q),
        source=label(srcs),
        as_of=as_of or now,
        quality=q,
        notes=notes,
        generated_at=now,
    )


# ── 추천 성과(이관분) ───────────────────────────────────────────────────────────────────


def _rets(pick: PickRow, days: list[date], closes: Mapping[date, float]) -> dict[str, float | None]:
    out: dict[str, float | None] = {f"d{k}": None for k in PICK_HORIZONS}
    base = float(pick.price_at_rec) if pick.price_at_rec is not None else closes.get(pick.date)
    if base is None or base <= 0:
        return out
    i = bisect_right(days, pick.date)  # 추천일 뒤 첫 거래일 = d1
    for k in PICK_HORIZONS:
        j = i + k - 1
        if j < len(days) and (c := closes.get(days[j])) is not None:
            out[f"d{k}"] = (c / base - 1.0) * 100.0
    return out


def picks(ctx: ReadContext) -> Envelope[PickList]:
    all_rows = ctx.repos.journal.picks()
    if not all_rows:
        raise NoData(NO_PICKS)
    rows = sorted(all_rows, key=lambda p: (p.date, p.source, p.rank or 0, p.code), reverse=True)
    rows = rows[:MAX_PICKS]
    today = ctx.today()
    first = min(p.date for p in rows)
    n = min(3000, (today - first).days + 15)
    series = ctx.repos.market.series(sorted({p.code for p in rows}), today, n)
    days = sorted(ctx.repos.market.trading_days(today, n))
    quals: list[Quality | None] = []
    items: list[PickOut] = []
    for p in rows:
        bars = series.get(p.code, [])
        closes = {b.date: b.close for b in bars if b.close is not None}
        quals += [b.quality for b in bars]
        items.append(
            PickOut(
                date=p.date,
                source=p.source,
                rank=p.rank,
                code=p.code,
                name=p.name,
                market=p.market,
                score=p.score,
                price_at_rec=_f(p.price_at_rec),
                rets=_rets(p, days, closes),
            )
        )
    now = ctx.now()
    notes = ["SD 추천 이력 이관분 — 새로 쌓지 않는다", "사후 수익은 확정 일봉 종가 기준(%)"]
    if len(all_rows) > MAX_PICKS:
        notes.append(f"최근 {MAX_PICKS}건만")
    return envelope(
        PickList(items=items),
        source=label({b.source for bs in series.values() for b in bs}),
        as_of=close_as_of(days[-1]) if days else now,
        quality=worst(quals) or Quality.OK,
        notes=notes,
        generated_at=now,
    )

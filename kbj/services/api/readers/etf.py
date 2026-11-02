"""ETF 수급 readers — 자금 흐름·유형별·괴리율·구성종목 변동(docs/p3_design.md §4.3·§5.3,
metrics §4).

- 순유입·가격효과·검산 ③ 은 `kbj.engines.etf.flows`(KRX 일별 좌수·마감 NAV — 분할·병합 보정 포함).
  장내 투자자별 순매수(KIS)는 **순유입과 합치지 않고** 따로 싣는다(LP 상대 — §4.3).
- 금액은 원 단위 정수(엔진의 부동소수 원을 반올림). 튜닝값은 `config/markets.yaml` `etf.*`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from typing import Final, Literal

from kbj.core.quality import Quality
from kbj.core.rows import EtfDay, EtfMeta, EtfType, Investor, InvestorDay, pick_best, row_rank
from kbj.engines.etf.flows import (
    EtfFlow,
    PeriodFlow,
    by_type,
    check3,
    period_totals,
    type_of,
    window_flows,
)
from kbj.engines.etf.holdings import LABEL, sort_key
from kbj.engines.etf.premium import alerts, premium_rows
from kbj.engines.etf.stock_flows import EtfSide, aggregate, coverage
from kbj.engines.flows.ledger import close_as_of, investor_values, source_label, sum_known
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.etf import (
    ChangeRow,
    Check3,
    EtfFlowRow,
    EtfFlows,
    EtfHoldingChanges,
    EtfPremium,
    EtfStockFlows,
    EtfTypes,
    NewListing,
    PremiumRow,
    StockEtfFlowRow,
    StockFlowEtf,
    TypeRollup,
)
from kbj.services.api.readers._common import (
    NoData,
    ReadContext,
    day_start,
    envelope,
    won,
    worst,
)

__all__ = ["etf_flows", "etf_holding_changes", "etf_premium", "etf_stock_flows", "etf_types"]

FlowMode = Literal["in", "out", "value", "indiv", "foreign", "inst"]
StockSort = Literal["in", "out", "value", "pct", "indiv", "foreign", "inst"]
NO_ETF: Final = "아직 없음 — krx.daily 08:05(ETF 일별)"


def _last_etf_day(ctx: ReadContext, upto: date) -> date:
    last = ctx.repos.etf.etf_days(upto, 1)
    days = [d.date for rows in last.values() for d in rows]
    if not days:
        raise NoData(NO_ETF)
    return max(days)


def _window(
    ctx: ReadContext, period: int, upto: date
) -> tuple[dict[str, list[EtfDay]], list[EtfFlow], dict[str, EtfMeta], list[date]]:
    end = _last_etf_day(ctx, upto)
    days = ctx.repos.etf.etf_days(end, period + 1)
    tds = sorted({d.date for rows in days.values() for d in rows})
    meta = ctx.repos.etf.meta()
    events = [ev for evs in ctx.repos.etf.split_events().values() for ev in evs]
    cfg = ctx.markets.etf
    flows: list[EtfFlow] = []
    for code, rows in sorted(days.items()):
        m = meta.get(code)
        flows += window_flows(
            rows,
            period,
            trading_days=tds,
            splits=[ev for ev in events if ev.code == code],
            tol=cfg.split_tol,
            ratios=cfg.split_ratios,
            delisted_on=None if m is None else m.delisted_on,
        )
    return days, flows, meta, tds


def _investor_sums(
    ctx: ReadContext, codes: Sequence[str], start: date, end: date
) -> dict[str, dict[str, int | None]]:
    """ETF 장내 투자자별 순매수 기간 합(KIS `etf_investor_daily` — 원장과 같은 고르기)."""
    if not codes:
        return {}
    rows: list[InvestorDay] = [
        r for rs in ctx.repos.flows.days(codes, start, end).values() for r in rs
    ]
    best = pick_best(
        rows,
        lambda r: (r.code, r.date, r.investor),
        lambda r: row_rank(r.source, r.quality, r.venue),
    )
    by: dict[tuple[str, date], dict[Investor, InvestorDay]] = {}
    for (code, d, who), r in best.items():
        by.setdefault((code, d), {})[who] = r
    acc: dict[str, dict[str, list[int | None]]] = {}
    for (code, _), inv in by.items():
        v = investor_values(inv)
        a = acc.setdefault(code, {"indiv": [], "foreign": [], "inst": []})
        a["indiv"].append(v.indiv)
        a["foreign"].append(v.foreign)
        a["inst"].append(v.inst)
    return {code: {k: sum_known(vs) for k, vs in a.items()} for code, a in acc.items()}


def _status(p: PeriodFlow) -> Literal["ok", "new", "split_adjusted", "invalid"]:
    if p.new_on is not None:
        return "new"
    if p.days == 0:
        return "invalid"
    return "split_adjusted" if p.splits else "ok"


def _sort_key(mode: FlowMode, r: EtfFlowRow) -> tuple[bool, float, str]:
    val: int | None = {
        "in": r.net_inflow,
        "out": None if r.net_inflow is None else -r.net_inflow,
        "value": r.turnover,
        "indiv": r.indiv,
        "foreign": r.foreign,
        "inst": r.inst,
    }[mode]
    return (val is None, -(val or 0), r.code)


def etf_flows(
    ctx: ReadContext,
    *,
    mode: FlowMode,
    etf_type: EtfType | None,
    period: int,
    limit: int,
) -> Envelope[EtfFlows]:
    days, flows, meta, tds = _window(ctx, period, ctx.today())
    totals = period_totals(flows)
    q_by: dict[str, list[Quality]] = {}
    for f in flows:
        if f.counted:
            q_by.setdefault(f.code, []).append(f.quality)
    win = tds[-period:]
    start, end = (win[0], win[-1]) if win else (None, None)
    inv = _investor_sums(ctx, list(totals), start, end) if start and end else {}
    rows: list[EtfFlowRow] = []
    new: list[NewListing] = []
    for code, p in sorted(totals.items()):
        m = meta.get(code)
        t = type_of(m)
        if etf_type is not None and t is not etf_type:
            continue
        hist = days.get(code, [])
        last = hist[-1] if hist else None
        na = None
        if last is not None:
            na = last.net_asset
            if na is None and last.list_shrs is not None and last.nav is not None:
                na = round(last.list_shrs * last.nav)
        base = next((d for d in reversed(hist) if start is not None and d.date < start), None)
        ret = None
        if not p.splits and base is not None and last is not None and base.close and last.close:
            ret = (last.close / base.close - 1) * 100
        turn = sum_known(d.turnover for d in hist if start is not None and d.date >= start)
        name = (m.name if m is not None else None) or (last.name if last is not None else None)
        status = _status(p)
        i = inv.get(code, {})
        rows.append(
            EtfFlowRow(
                code=code,
                name=name,
                etf_type=t,
                etf_type_label=t.label,
                theme=None if m is None else m.theme,
                issuer=None if m is None else m.issuer,
                net_asset=na,
                net_inflow=won(p.net_inflow) if p.days else None,
                price_effect=won(p.price_effect) if p.days else None,
                ret_pct=ret,
                turnover=turn,
                indiv=i.get("indiv"),
                foreign=i.get("foreign"),
                inst=i.get("inst"),
                days=p.days,
                n_invalid=p.n_invalid,
                status=status,
                quality=worst(q_by.get(code, []))
                or (Quality.INVALID if status == "invalid" else Quality.OK),
            )
        )
        if p.new_on is not None:
            new.append(
                NewListing(code=code, name=name, listed_on=p.new_on, net_asset=won(p.new_net_asset))
            )
    rows.sort(key=lambda r: _sort_key(mode, r))
    n_invalid = sum(p.n_invalid for p in totals.values())
    notes = ["투자자별 순매수(장내 — KIS)는 순유입과 합치지 않는다(LP 상대)"]
    if n_invalid:
        notes.append(f"invalid 흐름 {n_invalid}일 제외(검산 ③ 실패·결측·공백)")
    n_split = sum(len(p.splits) for p in totals.values())
    if n_split:
        notes.append(f"분할·병합 보정 {n_split}건(감지분은 잠정)")
    used = [f for f in flows if f.counted]
    data = EtfFlows(
        mode=mode,
        etf_type=etf_type,
        period=period,
        start=start,
        end=end,
        n_total=len(rows),
        rows=rows[:limit],
        new_listings=new,
    )
    if end is None:
        raise NoData(NO_ETF)
    return envelope(
        data,
        source=source_label(f.source for f in used) or source_label(f.source for f in flows),
        as_of=close_as_of(end),
        quality=worst(f.quality for f in used) or Quality.INVALID,
        notes=notes,
        generated_at=ctx.now(),
    )


NO_HOLDINGS: Final = "구성종목을 아는 ETF 가 없다 — etf.collect 08:00(운용사 PDF)"


def _stock_sort_key(sort: StockSort, r: StockEtfFlowRow) -> tuple[bool, float, str]:
    val: float | None = {
        "in": r.inflow,
        "out": None if r.inflow is None else -r.inflow,
        "value": r.etf_turnover,
        "pct": r.inflow_pct,
        "indiv": r.indiv,
        "foreign": r.foreign,
        "inst": r.inst,
    }[sort]
    return (val is None, -(val or 0), r.code)


def _ratio(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else a / b * 100


def _shares(v: float | None, close: float | None) -> int | None:
    return None if v is None or not close else round(v / close)


def etf_stock_flows(
    ctx: ReadContext, *, sort: StockSort, period: int, limit: int
) -> Envelope[EtfStockFlows]:
    """종목별 ETF 수급 — ETF 기간 순유입·거래대금·투자자별 순매수를 구성종목 비중으로 나눠
    종목마다 더한다
    (`kbj.engines.etf.stock_flows`). 종목 거래대금·종가는 원장 스냅(기간 각 거래일)."""
    days, flows, meta, tds = _window(ctx, period, ctx.today())
    win = tds[-period:]
    if not win:
        raise NoData(NO_ETF)
    start, end = win[0], win[-1]
    totals = period_totals(flows)
    inv = _investor_sums(ctx, list(totals), start, end)
    sides: dict[str, EtfSide] = {}
    for code, rows in days.items():
        p = totals.get(code)
        m = meta.get(code)
        i = inv.get(code, {})
        sides[code] = EtfSide(
            code=code,
            name=(m.name if m is not None else None) or (rows[-1].name if rows else None),
            inflow=p.net_inflow if p is not None and p.days else None,
            turnover=sum_known(d.turnover for d in rows if d.date >= start),
            indiv=i.get("indiv"),
            foreign=i.get("foreign"),
            inst=i.get("inst"),
        )
    held = ctx.repos.etf.latest_holdings(end)
    if not any(c in sides for c in held):
        raise NoData(NO_HOLDINGS)
    # 종목 거래대금·거래량(기간 합)·끝 종가 — 원장 스냅(장중 잠정만 있는 날은 저장소가 건너뛴다)
    s_turn: dict[str, int] = {}
    s_vol: dict[str, int] = {}
    close: dict[str, float] = {}
    names: dict[str, str] = {}
    stocks: set[str] = set()
    for d in win:
        snaps, sd = ctx.repos.market.snapshot(d)
        if sd != d:
            continue
        for code, sn in snaps.items():
            if code in sides:
                continue
            stocks.add(code)
            if sn.turnover is not None:
                s_turn[code] = s_turn.get(code, 0) + sn.turnover
            if sn.volume is not None:
                s_vol[code] = s_vol.get(code, 0) + sn.volume
            if sn.close:
                close[code] = sn.close
            if sn.name:
                names[code] = sn.name
    hmap = {etf: {c: (h.name, h.wt) for c, h in rows.items()} for etf, (_, rows) in held.items()}
    agg = aggregate(sides, hmap, stocks=stocks or None)
    n_known, cov = coverage(sides, held)
    rows_out: list[StockEtfFlowRow] = []
    for code, a in agg.items():
        c = close.get(code)
        st = s_turn.get(code)
        rows_out.append(
            StockEtfFlowRow(
                code=code,
                name=names.get(code) or a.name,
                n_etfs=a.n_etfs,
                inflow=won(a.inflow),
                inflow_shares=_shares(a.inflow, c),
                inflow_pct=_ratio(a.inflow, st),
                etf_turnover=won(a.turnover),
                etf_volume=_shares(a.turnover, c),
                turnover_pct=_ratio(a.turnover, st),
                indiv=won(a.indiv),
                foreign=won(a.foreign),
                inst=won(a.inst),
                stock_turnover=st,
                stock_volume=s_vol.get(code),
                close=c,
                top_etfs=[StockFlowEtf(code=e, name=n, inflow=round(v)) for e, n, v in a.top()],
            )
        )
    rows_out.sort(key=lambda r: _stock_sort_key(sort, r))
    asofs = [d for etf, (d, _) in held.items() if etf in sides]
    notes = [
        "ETF 기간 값 × 구성종목 비중(운용사 PDF) — 순유입 몫은 설정·환매로 생긴 바스켓 매매 추정, "
        "거래대금 몫은 ETF 장내 거래의 노출분",
        "주식 수 = 금액 ÷ 기간 끝 종가(추정). 선물·스왑·현금·다른 ETF 행은 넣지 않는다",
    ]
    if cov is not None and cov < 80:
        notes.append(f"구성종목을 아는 ETF 가 순유입의 {cov:.0f}% 만 덮는다 — 나머지는 빠졌다")
    used = [f for f in flows if f.counted]
    data = EtfStockFlows(
        sort=sort,
        period=period,
        start=start,
        end=end,
        n_total=len(rows_out),
        n_etfs_known=n_known,
        coverage_pct=cov,
        holdings_asof=min(asofs) if asofs else None,
        rows=rows_out[:limit],
    )
    return envelope(
        data,
        source=source_label(f.source for f in used) or source_label(f.source for f in flows),
        as_of=close_as_of(end),
        quality=Quality.ESTIMATED,
        notes=notes,
        generated_at=ctx.now(),
    )


def etf_types(ctx: ReadContext, *, period: int) -> Envelope[EtfTypes]:
    _, flows, meta, tds = _window(ctx, period, ctx.today())
    win = tds[-period:]
    if not win:
        raise NoData(NO_ETF)
    roll = by_type(flows, meta)
    used = [f for f in flows if f.counted]
    verdicts = Counter(check3(f) for f in flows)
    chg = [f.net_asset_chg for f in used]
    na_chg = None if any(c is None for c in chg) else sum(c for c in chg if c is not None)
    inflow = sum(f.net_inflow or 0.0 for f in used)
    price = sum(f.price_effect or 0.0 for f in used)
    resid = [f.residual for f in used]
    data = EtfTypes(
        period=period,
        start=win[0],
        end=win[-1],
        rows=[
            TypeRollup(
                etf_type=r.etf_type,
                label=r.label,
                net_inflow=round(r.net_inflow),
                price_effect=round(r.price_effect),
                n_etfs=r.n_etfs,
                n_new=r.n_new,
                n_invalid=r.n_invalid,
            )
            for r in roll
        ],
        check3=Check3(
            net_asset_chg=won(na_chg),
            inflow=round(inflow),
            price_effect=round(price),
            residual=None if any(x is None for x in resid) else won(sum(x or 0.0 for x in resid)),
            n_checked=verdicts[True] + verdicts[False],
            n_failed=verdicts[False],
            n_unavailable=verdicts[None],
        ),
    )
    notes: list[str] = []
    if verdicts[False]:
        notes.append(f"검산 ③ 실패 {verdicts[False]}건 — invalid 로 합계에서 뺐다")
    if verdicts[None]:
        notes.append(f"검산 ③ 불가 {verdicts[None]}건(신규 상장·결측)")
    return envelope(
        data,
        source=source_label(f.source for f in used) or "KBJ",
        as_of=close_as_of(win[-1]),
        quality=worst(f.quality for f in used) or Quality.INVALID,
        notes=notes,
        generated_at=ctx.now(),
    )


def _as_dt(v: date | datetime) -> datetime:
    """마감 괴리율은 거래일(→ 그날 15:30 KST), 장중은 시각 그대로."""
    return v if isinstance(v, datetime) else close_as_of(v)


def etf_premium(ctx: ReadContext, *, basis: Literal["nav", "inav"]) -> Envelope[EtfPremium]:
    meta = ctx.repos.etf.meta()
    thr = ctx.markets.etf.premium_warn_pct
    if basis == "nav":
        end = _last_etf_day(ctx, ctx.today())
        src_rows: list[EtfDay] = [
            d for rows in ctx.repos.etf.etf_days(end, 1).values() for d in rows if d.date == end
        ]
        checked = premium_rows(src_rows, thr, meta)
        hits = alerts(src_rows, thr, meta)
    else:
        now = ctx.now_kst()
        quotes = ctx.repos.etf.quotes(day_start(now.date()), now)
        if not quotes:
            raise NoData("아직 없음 — flows.intraday(장중 ETF 현재가·iNAV)")
        latest = {q.code: q for q in sorted(quotes, key=lambda q: q.ts)}
        checked = premium_rows(latest.values(), thr, meta)
        hits = alerts(latest.values(), thr, meta)
    if not checked:
        raise NoData("아직 없음 — 괴리율을 낼 가격·NAV 없음")
    rows = [
        PremiumRow(
            code=r.code,
            name=(meta[r.code].name if r.code in meta else None),
            price=r.price,
            nav=r.nav,
            premium_pct=r.premium_pct,
            threshold=r.threshold,
            warn=r.warn,
            as_of=_as_dt(r.as_of),
            quality=r.quality,
            source=source_label([r.source]) or "KBJ",
        )
        for r in hits
    ]
    as_ofs = [_as_dt(r.as_of) for r in checked]
    notes = ["장중 iNAV 는 추정치(잠정)"] if basis == "inav" else []
    return envelope(
        EtfPremium(basis=basis, n_checked=len(checked), rows=rows),
        source=source_label(r.source for r in checked) or "KBJ",
        as_of=max(as_ofs),
        quality=worst(r.quality for r in checked),
        notes=notes,
        generated_at=ctx.now(),
    )


def etf_holding_changes(
    ctx: ReadContext,
    *,
    day: date | None,
    kind: str | None,
    issuer: str | None,
) -> Envelope[EtfHoldingChanges]:
    run = day if day is not None else ctx.repos.etf.last_change_run(ctx.today())
    if run is None:
        raise NoData("아직 없음 — etf.collect 08:00(운용사 구성종목)")
    changes = ctx.repos.etf.changes(run)
    funds = ctx.repos.etf.funds()
    n_funds = Counter((c.code, c.kind) for c in changes)
    rows: list[ChangeRow] = []
    for c in changes:
        f = funds.get(c.fund_id)
        if kind is not None and c.kind != kind:
            continue
        if issuer is not None and (f is None or f.issuer != issuer):
            continue
        rows.append(
            ChangeRow(
                fund_id=c.fund_id,
                etf_code=None if f is None else f.ticker,
                fund_name=None if f is None else f.name,
                issuer=None if f is None else f.issuer,
                is_active=None if f is None else f.is_active,
                theme=None if f is None else f.theme,
                code=c.code,
                name=c.name,
                kind=c.kind,
                kind_label=LABEL.get(c.kind, c.kind),
                prev_qty=c.prev_qty,
                cur_qty=c.cur_qty,
                prev_wt=c.prev_wt,
                cur_wt=c.cur_wt,
                qty_pct_adj=c.qty_pct_adj,
                asof=c.asof,
                prev_asof=c.prev_asof,
                gap_days=c.gap_days,
            )
        )
    rows.sort(
        key=lambda r: (
            *sort_key(r.theme, bool(r.is_active), n_funds[(r.code, r.kind)]),
            r.kind,
            r.code,
            r.fund_id,
        )
    )
    src = sorted({f.source for f in funds.values() if f.fund_id in {c.fund_id for c in changes}})
    asof = max((c.asof for c in changes), default=run)
    return envelope(
        EtfHoldingChanges(run_date=run, rows=rows),
        source="+".join(src) or "ETF_ISSUERS",
        as_of=close_as_of(asof),
        quality=Quality.OK,
        notes=["운용사 공시 PDF 기준일은 운용사마다 다르다 — 펀드마다 최근 두 스냅 비교"],
        generated_at=ctx.now(),
    )

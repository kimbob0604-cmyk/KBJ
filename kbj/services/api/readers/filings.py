"""공시 readers — 종목 상세(공시·잠정실적·오버행·내부자)와 페이지 5 패널(피드·잠정실적·새 오버행·
내부자 큰 변동·실적 일정)(docs/p4_design.md §4.1~§4.4·§5.3·§6.4).

- 공개 원천(DART — `pub_filings.*`)의 파생값은 `filings.derive` 가 미리 썼다(오버행 상태·요약·내부자
  창·실적 일정·키워드 점수). 여기서는 읽고, **로그인에서만** 읽을 때 계산하는 값(관심종목·시총 가산
  중요도, 오버행 금액·ITM·상장주식수 분모 비율, 내부자 금액 — 설계 §2 혼합 값)만 엔진으로 더한다
  (`pub_*` 에 쓰지 않는다).
- 잠정실적의 분류는 알림 작업의 기록(`prv_fin.earnings_surprise`)이 있으면 그것, 없으면 같은 엔진
  규칙(`classify_prelim` — 공개 YoY 규칙)으로 읽을 때 낸다.
- 정정 공시는 새 접수번호의 새 행이다 — 내부자 보고·5% 보고는 엔진의 정정 대체(`dedupe`)를 거친다.
- 보호예수는 출처가 없어 추정 해제일만(D-P4-18 — 언제나 estimated, 화면 '추정' 배지).
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Final, Literal, cast

from kbj.core.fin_rows import (
    DisclosureRow,
    InsiderReport,
    MajorHolderReport,
    OverhangEvent,
    OverhangState,
    PrelimRow,
    ScheduleRow,
    SurpriseRow,
)
from kbj.core.quality import Quality
from kbj.engines.filings import earnings as earn
from kbj.engines.filings import importance, insider
from kbj.engines.filings import overhang as oh
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.filings import (
    Disclosures,
    DisclosureView,
    Earnings,
    EarningsList,
    EarningsSchedule,
    FilingsFeed,
    Importance,
    InsiderRecent,
    InsiderRecentRow,
    InsiderReportView,
    Insiders,
    Instrument,
    InstrumentView,
    LockupView,
    MajorHolderView,
    Overhang,
    OverhangRecent,
    OverhangRecentRow,
    OverhangSummaryView,
    PrelimView,
    ScheduleView,
    SurpriseView,
    TimelineItem,
    WindowView,
)
from kbj.services.api.readers._common import NoData, ReadContext, day_start, envelope, worst
from kbj.services.api.readers.stock import Lookup, dart_as_of

__all__ = [
    "DERIVE_ETA",
    "FEED_ETA",
    "INSIDER_LABEL",
    "corp_mktcaps",
    "filings_earnings",
    "filings_feed",
    "filings_insiders",
    "filings_overhang",
    "filings_schedule",
    "stock_disclosures",
    "stock_earnings",
    "stock_insiders",
    "stock_overhang",
]

FEED_ETA: Final = "filings.dart_feed(매분 07:00~19:59)"
DERIVE_ETA: Final = "filings.derive(30분 — 07:10~21:40)"
INSIDER_LABEL: Final = "보고 기준 순증감(사유 미구분 포함)"
_DART: Final = "DART"
_VIEWER: Final = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
PRELIM_LOOKBACK_DAYS: Final = 400
SCHEDULE_BACK_DAYS: Final = 7
SCHEDULE_AHEAD_DAYS: Final = 180
INSIDER_FETCH_DAYS: Final = 240  # 창(180일) + 정정 짝을 찾을 여유(E 의 파생 작업과 같다)
MAJOR_DAYS: Final = 365
REPORTS_MAX: Final = 50
MAJOR_MAX: Final = 20
CLOSE_BARS: Final = 200  # 내부자 금액의 보고일 종가(창 180일 ≈ 125거래일 + 여유)
_MARKET_CLS: Final = {"KOSPI": "Y", "KOSDAQ": "K", "KONEX": "N"}
Market = Literal["KOSPI", "KOSDAQ", "KONEX"]


def _link(rcept_no: str) -> str:
    return _VIEWER + rcept_no


# ── 공용 ──────────────────────────────────────────────────────────────────────────────


def corp_mktcaps(lk: Lookup) -> dict[str, int]:
    """{corp_code: 전 거래일 회사 시총(원 — 상장 종류주식 합)} — 로그인 중요도 가산(§4.1)."""
    snaps, _ = lk.snapshot
    by_common: dict[str, int] = {}
    for code, s in snaps.items():
        if s.mktcap is None:
            continue
        sc = lk.classes.get(code)
        common = sc.common_code if sc is not None else code
        by_common[common] = by_common.get(common, 0) + s.mktcap
    return {corp: by_common[c] for c, (corp, _n) in lk.corp_index.items() if c in by_common}


class _Scorer:
    """공시 행 → 화면 행(키워드 점수 + 관심종목·시총 가산 — 읽을 때)."""

    def __init__(self, ctx: ReadContext, lk: Lookup) -> None:
        self.cfg = ctx.fin_config().filings
        self.lk = lk
        self.caps = corp_mktcaps(lk)

    def view(self, d: DisclosureRow) -> DisclosureView:
        if d.kw_score is not None:
            kw = importance.KeywordScore(d.kw_score, tuple(d.kw_matched))
        else:  # filings.derive 가 아직 점수를 채우지 않은 행 — 같은 엔진 규칙
            kw = importance.keyword_score(d.title, self.cfg.keywords)
        watch = d.corp_code in self.lk.watch_corps
        sc = importance.login_score(
            kw, is_watch=watch, mktcap=self.caps.get(d.corp_code), cfg=self.cfg
        )
        return DisclosureView(
            rcept_no=d.rcept_no,
            rcept_dt=d.rcept_dt,
            first_seen_at=d.first_seen_at,
            corp_code=d.corp_code,
            stock_code=d.stock_code,
            corp_name=d.corp_name,
            corp_cls=d.corp_cls,
            title=d.title,
            kind=d.kind,
            is_correction=d.is_correction,
            kw_score=kw.score,
            kw_matched=list(kw.matched),
            login_score=sc.total,
            importance=sc.importance,
            is_watch=watch,
            parse_state=d.parse_state,
            link=d.link,
        )


def _as_of_rows(ctx: ReadContext, rows: Sequence[DisclosureRow], day: date) -> datetime:
    if rows:
        return max(r.first_seen_at for r in rows)
    return day_start(day) if day < ctx.today() else ctx.now()


# ── 공시(종목·피드) ───────────────────────────────────────────────────────────────────


def stock_disclosures(
    ctx: ReadContext, code: str, *, days: int, kind: str | None
) -> Envelope[Disclosures]:
    lk = Lookup(ctx)
    ref = lk.ref(code)
    corp = ref.need_corp()
    t = lk.today
    rows = ctx.repos.filings.disclosures(
        start=t - timedelta(days=days),
        end=t,
        corp_code=corp,
        kinds={kind} if kind else None,
    )
    sc = _Scorer(ctx, lk)
    data = Disclosures(
        code=code, corp_code=corp, days=days, kind=kind, rows=[sc.view(r) for r in rows]
    )
    notes = ["중요도 = 제목 키워드 + 관심종목 + 시총 가산(로그인 — 읽을 때 계산)"]
    if not rows:
        notes.append(f"최근 {days}일 공시 없음")
    return envelope(
        data,
        source=_DART,
        as_of=_as_of_rows(ctx, rows, t),
        quality=worst(r.quality for r in rows) or Quality.OK,
        notes=notes,
        generated_at=ctx.now(),
    )


LISTED_CLS: Final = frozenset({"Y", "K", "N"})  # DART corp_cls — 유가·코스닥·코넥스(E = 기타)
# 상장사 소식이 아니라 증권 발행 서류(투자자 모집용) — 피드 기본에서 뺀다
ISSUE_DOCS: Final = (
    "투자설명서", "일괄신고", "채무증권", "파생결합", "집합투자", "증권발행실적", "기업어음",
    "단기사채", "유동화",
)  # fmt: skip


def _listed_news(corp_cls: str | None, title: str | None) -> bool:
    t = (title or "").replace(" ", "")
    return corp_cls in LISTED_CLS and not any(w in t for w in ISSUE_DOCS)


def filings_feed(
    ctx: ReadContext,
    *,
    day: date | None,
    kind: str | None,
    level: Importance | None,
    market: Market | None,
    watch_only: bool,
    limit: int,
    listed_only: bool = True,
) -> Envelope[FilingsFeed]:
    """페이지 5 공시 피드 — 하루치(기본 오늘). 일반 공시는 push 하지 않는다(D-P4-14).

    `listed_only`(기본): 상장사(유가·코스닥·코넥스) 공시만, 발행 서류(펀드 투자설명서·채무증권·
    파생결합증권 일괄신고 등)는 뺀다 — 하루 공시의 40% 넘게가 이것이라 피드가 잡음으로 찼다
    (2026-10-20 점검: 544건 중 비상장 227건, 펀드 투자설명서 123건)."""
    lk = Lookup(ctx)
    d = day or lk.today
    rows = ctx.repos.filings.disclosures(start=d, end=d)
    if not rows and not ctx.repos.filings.disclosures(start=d - timedelta(days=7), end=d, limit=1):
        raise NoData(f"아직 없음 — {FEED_ETA}")
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    sc = _Scorer(ctx, lk)
    views = [sc.view(r) for r in rows]
    cls = _MARKET_CLS.get(market) if market else None
    picked = [
        v
        for v in views
        if (kind is None or v.kind == kind)
        and (level is None or v.importance == level)
        and (cls is None or v.corp_cls == cls)
        and (not watch_only or v.is_watch)
        and (not listed_only or _listed_news(v.corp_cls, v.title))
    ]
    picked.sort(key=lambda v: (v.first_seen_at, v.rcept_no), reverse=True)
    data = FilingsFeed(
        date=d,
        kind=kind,
        importance=level,
        market=market,
        watch_only=watch_only,
        n_total=len(picked),
        by_kind=dict(sorted(by_kind.items())),
        rows=picked[:limit],
    )
    return envelope(
        data,
        source=_DART,
        as_of=_as_of_rows(ctx, rows, d),
        quality=worst(r.quality for r in rows) or Quality.OK,
        notes=["접수 시각은 우리가 처음 본 시각(DART 응답은 날짜만 — first_seen_at)"],
        generated_at=ctx.now(),
    )


# ── 잠정실적·실적 일정 ─────────────────────────────────────────────────────────────────


class _Prelims:
    """잠정실적 행 → 화면 행(분류는 알림 기록 → 없으면 엔진 규칙)."""

    def __init__(self, ctx: ReadContext, lk: Lookup) -> None:
        cfg = ctx.fin_config().earnings
        self.th = cfg.thresholds
        self.tol = cfg.yoy_reported_tol_pp
        self.ctx = ctx
        self.lk = lk

    def views(self, rows: Sequence[PrelimRow]) -> list[PrelimView]:
        if not rows:
            return []
        repo = self.ctx.repos
        known = {s.rcept_no: s for s in self._surprises(rows)}
        start = min(r.rcept_dt for r in rows)
        end = max(r.rcept_dt for r in rows)
        fixes = {
            d.rcept_no
            for d in repo.filings.disclosures(start=start, end=end, kinds={"prelim"})
            if d.is_correction
        }
        out: list[PrelimView] = []
        for r in sorted(rows, key=lambda x: x.rcept_no, reverse=True):
            sig = earn.classify_prelim(r, self.th, tol_pp=self.tol)
            s = known.get(r.rcept_no)
            hit = self.lk.stock_of_corp.get(r.corp_code)
            out.append(
                PrelimView(
                    rcept_no=r.rcept_no,
                    rcept_dt=r.rcept_dt,
                    corp_code=r.corp_code,
                    stock_code=hit[0] if hit else None,
                    corp_name=hit[1] if hit else None,
                    scope=r.scope.value,
                    period=r.period,
                    label=earn.period_label(r),
                    fiscal_year=r.fiscal_year,
                    fiscal_q=r.fiscal_q,
                    is_annual=r.is_annual,
                    period_end=r.period_end,
                    period_inferred=r.period_inferred,
                    currency=r.currency,
                    revenue_cur=r.revenue_cur,
                    revenue_prev_q=r.revenue_prev_q,
                    revenue_prev_y=r.revenue_prev_y,
                    op_cur=r.op_cur,
                    op_prev_q=r.op_prev_q,
                    op_prev_y=r.op_prev_y,
                    ni_cur=r.ni_cur,
                    ni_prev_q=r.ni_prev_q,
                    ni_prev_y=r.ni_prev_y,
                    ni_owner_cur=r.ni_owner_cur,
                    eps_cur=r.eps_cur,
                    rev_yoy=sig.rev_pct if sig.method == "yoy" else None,
                    op_yoy=sig.op_pct if sig.method == "yoy" else None,
                    rev_qoq=sig.rev_qoq,
                    op_qoq=sig.op_qoq,
                    signal=s.signal if s is not None else sig.signal,
                    priority=s.priority if s is not None else sig.priority,
                    is_correction=r.rcept_no in fixes,
                    is_watch=r.corp_code in self.lk.watch_corps,
                    alerted=s is not None and s.alerted_at is not None,
                    quality=r.quality,
                    notes=list(dict.fromkeys([*r.notes, *sig.notes])),
                    link=_link(r.rcept_no),
                )
            )
        return out

    def _surprises(self, rows: Sequence[PrelimRow]) -> list[SurpriseRow]:
        corps = {r.corp_code for r in rows}
        if len(corps) == 1:
            return self.ctx.repos.consensus.surprises(corp_code=next(iter(corps)))
        return self.ctx.repos.consensus.surprises()


def _schedule_views(lk: Lookup, rows: Iterable[ScheduleRow]) -> list[ScheduleView]:
    out: list[ScheduleView] = []
    for r in sorted(rows, key=lambda x: (x.planned_date, x.corp_code, x.kind)):
        hit = lk.stock_of_corp.get(r.corp_code)
        out.append(
            ScheduleView(
                corp_code=r.corp_code,
                stock_code=hit[0] if hit else None,
                corp_name=hit[1] if hit else None,
                period_end=r.period_end,
                kind=r.kind,
                planned_date=r.planned_date,
                fiscal_year=r.fiscal_year,
                fiscal_q=r.fiscal_q,
                rcept_no=r.rcept_no,
                note=r.note,
                is_watch=r.corp_code in lk.watch_corps,
                quality=r.quality,
            )
        )
    return out


def _surprise(s: SurpriseRow) -> SurpriseView:
    return SurpriseView(
        rcept_no=s.rcept_no,
        period=s.period,
        scope=s.scope.value,
        signal=s.signal,
        priority=s.priority,
        rev_yoy=s.rev_yoy,
        op_yoy=s.op_yoy,
        rev_surprise=s.rev_surprise,
        op_surprise=s.op_surprise,
        alerted_at=s.alerted_at,
        price_at_alert=s.price_at_alert,
        perf_3d=s.perf_3d,
        perf_5d=s.perf_5d,
        perf_7d=s.perf_7d,
        quality=s.quality,
    )


def stock_earnings(ctx: ReadContext, code: str) -> Envelope[Earnings]:
    lk = Lookup(ctx)
    ref = lk.ref(code)
    corp = ref.need_corp()
    t = lk.today
    repo = ctx.repos
    prelims = repo.filings.prelims(
        start=t - timedelta(days=PRELIM_LOOKBACK_DAYS), end=t, corp_code=corp
    )
    sched = repo.filings.schedule(
        start=t - timedelta(days=SCHEDULE_BACK_DAYS),
        end=t + timedelta(days=SCHEDULE_AHEAD_DAYS),
        corp_code=corp,
    )
    surprises = sorted(
        repo.consensus.surprises(corp_code=corp), key=lambda s: s.rcept_no, reverse=True
    )
    data = Earnings(
        code=code,
        corp_code=corp,
        prelims=_Prelims(ctx, lk).views(prelims),
        schedule=_schedule_views(lk, sched),
        surprises=[_surprise(s) for s in surprises],
    )
    notes = [
        "분류 = 전년동기 대비(YoY — 공개 규칙)",
        "분기 컨센서스 대비 서프라이즈는 출처 없음(KIS 교체로 잃음 — D-P4-10)",
    ]
    if any(r.quality is Quality.ESTIMATED for r in sched):
        notes.append("법정 제출기한은 추정(분기·반기 45일, 사업 90일 [확인 필요])")
    days = [r.rcept_dt for r in prelims]
    qs: list[Quality | None] = [*(r.quality for r in prelims), *(r.quality for r in sched)]
    return envelope(
        data,
        source=_DART,
        as_of=dart_as_of(max(days)) if days else ctx.now(),
        quality=worst(qs) or Quality.OK,
        notes=notes,
        generated_at=ctx.now(),
    )


def filings_earnings(ctx: ReadContext, *, start: date, end: date) -> Envelope[EarningsList]:
    lk = Lookup(ctx)
    rows = ctx.repos.filings.prelims(start=start, end=end)
    data = EarningsList(from_date=start, to_date=end, rows=_Prelims(ctx, lk).views(rows))
    return envelope(
        data,
        source=_DART,
        as_of=dart_as_of(max(r.rcept_dt for r in rows)) if rows else ctx.now(),
        quality=worst(r.quality for r in rows) or Quality.OK,
        notes=["분류 = 전년동기 대비(YoY — 공개 규칙)"],
        generated_at=ctx.now(),
    )


def filings_schedule(ctx: ReadContext, *, days: int) -> Envelope[EarningsSchedule]:
    lk = Lookup(ctx)
    t = lk.today
    rows = ctx.repos.filings.schedule(start=t, end=t + timedelta(days=days))
    data = EarningsSchedule(days=days, rows=_schedule_views(lk, rows))
    return envelope(
        data,
        source=_DART,
        as_of=ctx.now(),
        quality=worst(r.quality for r in rows) or Quality.OK,
        notes=["법정 제출기한(statutory)은 추정 — 결산실적공시예고(pre_announce)가 있으면 그 날"],
        generated_at=ctx.now(),
    )


# ── 오버행 ────────────────────────────────────────────────────────────────────────────


def _timeline(st: OverhangState, ev: OverhangEvent | None) -> TimelineItem:
    if ev is None:
        start = end = None
    elif ev.instrument.value == "paid_in":
        start, end = ev.rcept_dt, ev.listing_date
    else:
        ends = [x for x in (ev.exercise_end, ev.maturity) if x is not None]
        start, end = ev.exercise_start, (min(ends) if ends else None)
    return TimelineItem(
        rcept_no=st.rcept_no,
        instrument=cast(Instrument, st.instrument.value),
        start=start,
        end=end,
        active=st.active,
    )


def stock_overhang(ctx: ReadContext, code: str) -> Envelope[Overhang]:
    lk = Lookup(ctx)
    ref = lk.ref(code)
    corp = ref.need_corp()
    common = ref.need_common()
    f = ctx.repos.filings
    events = {e.rcept_no: e for e in f.overhang_events(corp)}
    states = f.overhang_state(corp)
    summary = f.overhang_summary(corp)
    if events and summary is None and not states:
        raise NoData(f"아직 없음 — {DERIVE_ETA}")
    close, cday = lk.close(common)
    s = lk.snap(common)
    listed = s.shares if s is not None else None
    months = ctx.fin_config().overhang.lockup_months_third_party
    instruments: list[InstrumentView] = []
    amount = 0
    amount_known = False
    itm_shares = 0
    for st in sorted(states, key=lambda x: x.rcept_no):
        ev = events.get(st.rcept_no)
        is_itm = oh.itm(st, close)
        val = oh.overhang_value(st, close)
        if st.active and val is not None:
            amount += val
            amount_known = True
        if st.active and is_itm and st.potential_shares is not None:
            itm_shares += st.potential_shares
        instruments.append(
            InstrumentView(
                rcept_no=st.rcept_no,
                rcept_dt=ev.rcept_dt if ev is not None else st.as_of,
                instrument=cast(Instrument, st.instrument.value),
                seq=ev.seq if ev is not None else None,
                face_value=ev.face_value if ev is not None else None,
                remaining_face=st.remaining_face,
                price=st.price,
                refix_floor=ev.refix_floor if ev is not None else None,
                potential_shares=st.potential_shares,
                active=st.active,
                exercisable=st.exercisable,
                exercise_start=ev.exercise_start if ev is not None else None,
                exercise_end=ev.exercise_end if ev is not None else None,
                maturity=ev.maturity if ev is not None else None,
                listing_date=ev.listing_date if ev is not None else None,
                alloc_method=ev.alloc_method if ev is not None else None,
                lockup_end_est=st.lockup_end_est,
                itm=is_itm,
                amount=val,
                quality=st.quality,
                notes=list(st.notes),
                link=_link(st.rcept_no),
            )
        )
    lockups = [
        LockupView(
            rcept_no=x.rcept_no,
            listing_date=x.listing_date,
            lockup_end_est=x.lockup_end_est,
            shares=x.shares,
            months=x.months,
            basis=x.basis,
            quality=x.quality,
        )
        for x in oh.lockup_estimates(events.values(), months)
    ]
    sv = None
    if summary is not None:
        sv = OverhangSummaryView(
            as_of=summary.as_of,
            potential_new_shares=summary.potential_new_shares,
            exercisable_shares=summary.exercisable_shares,
            exchange_shares=summary.exchange_shares,
            denominator=summary.denominator,
            denom_source=summary.denom_source,
            ratio_pct=summary.ratio_pct,
            exercisable_ratio_pct=summary.exercisable_ratio_pct,
            listed_shares=listed,
            login_ratio_pct=oh.login_ratio(summary, listed),
            amount=amount if amount_known else None,
            itm_shares=itm_shares if close is not None else None,
            quality=summary.quality,
            notes=list(summary.notes),
        )
    data = Overhang(
        code=code,
        corp_code=corp,
        close=close,
        close_date=cday,
        summary=sv,
        instruments=instruments,
        lockups=lockups,
        timeline=[
            _timeline(st, events.get(st.rcept_no))
            for st in sorted(states, key=lambda x: x.rcept_no)
        ],
    )
    notes = ["오버행 가치는 EV 에 더하지 않는다(D-P4-11)"]
    if not events and not states:
        notes.append("오버행 사건(CB·BW·EB·유상증자) 없음")
    if instruments or summary is not None:
        notes.append("금액·ITM·상장주식수 비율은 전 거래일 종가 기준 추정(로그인 — 시가가 아님)")
    if lockups:
        notes.append(oh.LOCKUP_BASIS)
    qs: list[Quality | None] = [summary.quality if summary is not None else None]
    qs += [st.quality for st in states] + [x.quality for x in lockups]
    as_day = summary.as_of if summary is not None else (states[0].as_of if states else lk.today)
    return envelope(
        data,
        source=_DART if close is None else "DART+KRX",
        as_of=dart_as_of(as_day),
        quality=worst(qs) or Quality.OK,
        notes=notes,
        generated_at=ctx.now(),
    )


def filings_overhang(
    ctx: ReadContext, *, days: int, instrument: Instrument | None
) -> Envelope[OverhangRecent]:
    lk = Lookup(ctx)
    t = lk.today
    f = ctx.repos.filings
    events = f.overhang_events(start=t - timedelta(days=days), end=t)
    if instrument is not None:
        events = [e for e in events if e.instrument.value == instrument]
    rows: list[OverhangRecentRow] = []
    ratios: dict[str, float | None] = {}
    for e in sorted(events, key=lambda x: x.rcept_no, reverse=True):
        if e.corp_code not in ratios:
            s = f.overhang_summary(e.corp_code)
            ratios[e.corp_code] = s.ratio_pct if s is not None else None
        hit = lk.stock_of_corp.get(e.corp_code)
        rows.append(
            OverhangRecentRow(
                rcept_no=e.rcept_no,
                rcept_dt=e.rcept_dt,
                corp_code=e.corp_code,
                stock_code=hit[0] if hit else None,
                corp_name=hit[1] if hit else None,
                instrument=cast(Instrument, e.instrument.value),
                face_value=e.face_value,
                price=e.price,
                shares=e.shares,
                exercise_start=e.exercise_start,
                exercise_end=e.exercise_end,
                ratio_pct=ratios[e.corp_code],
                is_watch=e.corp_code in lk.watch_corps,
                quality=e.quality,
                link=_link(e.rcept_no),
            )
        )
    data = OverhangRecent(days=days, instrument=instrument, rows=rows)
    return envelope(
        data,
        source=_DART,
        as_of=dart_as_of(max(e.rcept_dt for e in events)) if events else ctx.now(),
        quality=worst(e.quality for e in events) or Quality.OK,
        notes=["비율 = 회사 오버행 비율(최근 요약 — DART 주식총수 분모)"],
        generated_at=ctx.now(),
    )


# ── 내부자 ────────────────────────────────────────────────────────────────────────────


def _corrections(ctx: ReadContext, corp: str | None, start: date, end: date) -> set[str]:
    return {
        d.rcept_no
        for d in ctx.repos.filings.disclosures(
            start=start, end=end, corp_code=corp, kinds={"insider", "major_holder"}
        )
        if d.is_correction
    }


def _closes(ctx: ReadContext, code: str) -> tuple[list[date], list[float]]:
    bars = ctx.repos.market.series([code], ctx.today(), CLOSE_BARS).get(code, [])
    pairs = [(b.date, b.close) for b in bars if b.close is not None and (b.volume or 0) > 0]
    return [d for d, _ in pairs], [cast(float, c) for _, c in pairs]


def _report_view(r: InsiderReport) -> InsiderReportView:
    return InsiderReportView(
        rcept_no=r.rcept_no,
        rcept_dt=r.rcept_dt,
        reporter=r.reporter,
        position=r.position,
        registered=r.registered,
        major_holder=r.major_holder,
        shares_after=r.shares_after,
        shares_delta=r.shares_delta,
        ratio_after=r.ratio_after,
        ratio_delta=r.ratio_delta,
        reason=r.reason,
        quality=r.quality,
        link=_link(r.rcept_no),
    )


def _major_view(r: MajorHolderReport) -> MajorHolderView:
    return MajorHolderView(
        rcept_no=r.rcept_no,
        rcept_dt=r.rcept_dt,
        reporter=r.reporter,
        report_type=r.report_type,
        shares=r.shares,
        shares_delta=r.shares_delta,
        ratio=r.ratio,
        ratio_delta=r.ratio_delta,
        reason=r.reason,
        quality=r.quality,
        link=_link(r.rcept_no),
    )


def stock_insiders(ctx: ReadContext, code: str, *, days: int) -> Envelope[Insiders]:
    lk = Lookup(ctx)
    ref = lk.ref(code)
    corp = ref.need_corp()
    common = ref.need_common()
    t = lk.today
    f = ctx.repos.filings
    start = t - timedelta(days=INSIDER_FETCH_DAYS)
    fixes = _corrections(ctx, corp, start, t)
    reports = insider.dedupe(f.insider_reports(corp, start=start, end=t), fixes)
    windows = f.insider_windows(corp)
    if reports and not windows:
        raise NoData(f"아직 없음 — {DERIVE_ETA}")
    dates, closes = _closes(ctx, common)

    def close_on(_code: str, d: date) -> float | None:
        i = bisect.bisect_right(dates, d)
        return closes[i - 1] if i > 0 else None

    wins: list[WindowView] = []
    for w in sorted(windows, key=lambda x: x.days):
        lo = w.as_of - timedelta(days=w.days)
        inside = [r for r in reports if lo <= r.rcept_dt <= w.as_of]
        amt, missing = insider.insider_amount(inside, close_on, common)
        wins.append(
            WindowView(
                days=w.days,
                as_of=w.as_of,
                net_shares=w.net_shares,
                buy_shares=w.buy_shares,
                sell_shares=w.sell_shares,
                n_reports=w.n_reports,
                n_reporters=w.n_reporters,
                ratio_pct=w.ratio_pct,
                reason_known_pct=w.reason_known_pct,
                amount=amt,
                amount_missing=missing,
                quality=w.quality,
            )
        )
    lo = t - timedelta(days=days)
    shown = sorted((r for r in reports if r.rcept_dt >= lo), key=lambda r: r.rcept_no)[::-1]
    majors = insider.dedupe_major(
        f.major_holder_reports(corp, start=t - timedelta(days=MAJOR_DAYS), end=t), fixes
    )
    data = Insiders(
        code=code,
        corp_code=corp,
        days=days,
        label=INSIDER_LABEL,
        windows=wins,
        reports=[_report_view(r) for r in shown[:REPORTS_MAX]],
        major_holders=[_major_view(r) for r in majors[::-1][:MAJOR_MAX]],
    )
    notes = [
        f"{INSIDER_LABEL} — 증여·상속·신규선임 등 비거래 증감이 섞인다(장내 순매수가 아니다)",
        "금액 = Σ 증감 × 보고일 종가(로그인 — 추정, 실거래가 아님)",
        "5% 대량보유는 목록만(임원 보고와 같은 지분을 두 번 세지 않게 합계를 내지 않는다)",
    ]
    if not reports and not windows:
        notes.append("최근 소유보고 없음")
    as_day = max((w.as_of for w in windows), default=None)
    qs: list[Quality | None] = [*(w.quality for w in windows), *(r.quality for r in shown)]
    return envelope(
        data,
        source=_DART,
        as_of=dart_as_of(as_day) if as_day is not None else ctx.now(),
        quality=worst(qs) or Quality.OK,
        notes=notes,
        generated_at=ctx.now(),
    )


def filings_insiders(
    ctx: ReadContext, *, days: int, min_ratio_pct: float | None
) -> Envelope[InsiderRecent]:
    """페이지 5 내부자 큰 변동 — 최근 days 일 소유보고를 회사마다 더한다(정정 대체 뒤)."""
    lk = Lookup(ctx)
    t = lk.today
    f = ctx.repos.filings
    lo = t - timedelta(days=days)
    corps = sorted({d.corp_code for d in f.disclosures(start=lo, end=t, kinds={"insider"})})
    fixes = _corrections(ctx, None, t - timedelta(days=INSIDER_FETCH_DAYS), t)
    rows: list[InsiderRecentRow] = []
    for corp in corps:
        reps = insider.dedupe(
            f.insider_reports(corp, start=t - timedelta(days=INSIDER_FETCH_DAYS), end=t), fixes
        )
        inside = [r for r in reps if lo <= r.rcept_dt <= t]
        if not inside:
            continue
        deltas = [r.shares_delta for r in inside if r.shares_delta is not None]
        buy = sum(x for x in deltas if x > 0)
        sell = -sum(x for x in deltas if x < 0)
        sc = f.latest_share_count(corp, upto=t)
        den = sc.common_issued if sc is not None else None
        ratio = (buy - sell) / den * 100 if den else None
        if min_ratio_pct is not None and (ratio is None or abs(ratio) < min_ratio_pct):
            continue
        hit = lk.stock_of_corp.get(corp)
        unknown = len(inside) - len(deltas)
        rows.append(
            InsiderRecentRow(
                corp_code=corp,
                stock_code=hit[0] if hit else None,
                corp_name=hit[1] if hit else None,
                n_reports=len(inside),
                n_reporters=len({r.reporter for r in inside}),
                net_shares=buy - sell,
                buy_shares=buy,
                sell_shares=sell,
                ratio_pct=ratio,
                last_rcept_dt=max(r.rcept_dt for r in inside),
                is_watch=corp in lk.watch_corps,
                quality=Quality.ESTIMATED
                if unknown
                else (worst(r.quality for r in inside) or Quality.OK),
            )
        )
    rows.sort(key=lambda r: (-(abs(r.ratio_pct) if r.ratio_pct is not None else -1), r.corp_code))
    data = InsiderRecent(days=days, min_ratio_pct=min_ratio_pct, label=INSIDER_LABEL, rows=rows)
    return envelope(
        data,
        source=_DART,
        as_of=dart_as_of(max(r.last_rcept_dt for r in rows)) if rows else ctx.now(),
        quality=worst(r.quality for r in rows) or Quality.OK,
        notes=[
            f"{INSIDER_LABEL} — 비율 분모 = 최근 DART 주식총수(보통주 발행)",
        ],
        generated_at=ctx.now(),
    )

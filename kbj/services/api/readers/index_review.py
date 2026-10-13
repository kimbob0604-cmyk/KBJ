"""`/api/index-review*` 리더 — 저장된 예측(`prv_index.*`)을 화면 모양으로(P5 2차 묶음 IDX, metrics
§24).

계산하지 않는다(엔진 결과를 저장한 `index.predict` 의 행을 읽는다 — D-day 만 오늘 날짜로 센다). 일정
표가
비었으면 `NoData`(아직 없음 — index.calendar). 구성종목 목록이 없으면 실행 status no_constituents 를
그대로 보이고 후보 표는 비운다(지어내지 않음). 로그인 전용 — 공개 내보내기는 이 모듈을 모른다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from typing import Any, Final, Literal, cast

from kbj.core.index_rows import PredictionRow, PredictionRun, ReviewEvent
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.index_review import (
    CandidateRow,
    CutoffInfo,
    IndexReview,
    IndexRunView,
    IndexTab,
    ReviewDates,
    StockIndexBadge,
    StockIndexBadges,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, worst

__all__ = ["NO_CALENDAR", "index_review", "stock_badges"]

NO_CALENDAR: Final = "아직 없음 — index.calendar 06:41(지수 리뷰 일정)"
SOURCE: Final = "KBJ(KRX·KIS 시세·DART 지분 — 추정)"
_VERDICT_KO: Final = {
    "add": "편입 후보",
    "delete": "편출 후보",
    "borderline": "경계",
    "keep": "유지",
}


def _dates(ev: ReviewEvent, today: date) -> ReviewDates:
    return ReviewDates(
        index_id=ev.index_id, index_title=ev.index_title, review_id=ev.review_id,
        base_date=ev.base_date, announce_date=ev.announce_date, trade_date=ev.trade_date,
        effective_date=ev.effective_date, d_base=(ev.base_date - today).days,
        d_announce=(ev.announce_date - today).days, d_trade=(ev.trade_date - today).days,
        d_effective=(ev.effective_date - today).days, confirmed=ev.confirmed, quality=ev.quality,
    )  # fmt: skip


def _num(ev: Mapping[str, Any], key: str) -> float | None:
    v = ev.get(key)
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _int(ev: Mapping[str, Any], key: str) -> int | None:
    v = ev.get(key)
    return int(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _row(p: PredictionRow) -> CandidateRow:
    ev = p.evidence
    basis = ev.get("required_basis")
    met = ev.get("condition_met")
    reasons = ev.get("reasons")
    return CandidateRow(
        code=p.code, name=p.name, verdict=p.verdict, member=p.member,
        mktcap=_int(ev, "mktcap"), ff_mcap=_int(ev, "ff_mcap"), fif=_num(ev, "fif"),
        fif_how=ev.get("fif_how") if isinstance(ev.get("fif_how"), str) else None,
        gap_mcap=_num(ev, "gap_mcap"), gap_ff=_num(ev, "gap_ff"),
        condition_met=met if isinstance(met, bool) else None,
        required_rise=_num(ev, "required_rise"),
        required_basis=cast(Literal["mcap", "ff"], basis) if basis in ("mcap", "ff") else None,
        passive_won=_int(ev, "passive_won"), adv_won=_int(ev, "adv_won"),
        adv_multiple=_num(ev, "adv_multiple"), rank=_int(ev, "rank"),
        reasons=[str(x) for x in cast(list[Any], reasons)] if isinstance(reasons, list) else [],
        quality=p.quality,
    )  # fmt: skip


def _by_rise(rows: Sequence[CandidateRow]) -> list[CandidateRow]:
    """필요 상승률 오름차순(모르면 뒤) — 화면 기본 정렬."""
    return sorted(rows, key=lambda r: (r.required_rise is None, r.required_rise or 0.0, r.code))


def _run_view(read: ReadContext, run: PredictionRun) -> IndexRunView:
    rows = [
        _row(p)
        for p in read.repos.index_review.predictions(
            run.index_id, run.review_id, run.base_date, run.cutoff_kind
        )
    ]
    cutoff = None
    if run.cutoff_won is not None:
        cutoff = CutoffInfo(
            kind=run.cutoff_kind, won=run.cutoff_won, low_won=run.cutoff_low_won,
            high_won=run.cutoff_high_won, method=run.cutoff_method,
        )  # fmt: skip
    return IndexRunView(
        cutoff_kind=run.cutoff_kind, status=run.status, base_date=run.base_date, cutoff=cutoff,
        n_members=run.n_members, n_add=run.n_add, n_delete=run.n_delete,
        n_borderline=run.n_borderline, aum_won=run.aum_won, notes=list(run.notes),
        quality=run.quality,
        adds=_by_rise([r for r in rows if r.verdict == "add"]),
        deletes=_by_rise([r for r in rows if r.verdict == "delete"]),
        borderline=_by_rise([r for r in rows if r.verdict == "borderline"]),
    )  # fmt: skip


def _pick(events: Sequence[ReviewEvent], review: str | None, today: date) -> ReviewEvent | None:
    if review is not None:
        for ev in events:
            if ev.review_id == review:
                return ev
        raise NoData(f"리뷰 {review} 일정 없음")
    upcoming = [ev for ev in events if ev.effective_date > today]
    if upcoming:
        return upcoming[0]
    return events[-1] if events else None


def index_review(
    read: ReadContext, *, index: str | None = None, review: str | None = None
) -> Envelope[IndexReview]:
    today = read.today()
    cal = read.repos.index_review.calendar()
    if not cal:
        raise NoData(NO_CALENDAR)
    tabs: dict[str, IndexTab] = {}
    for ev in cal:
        tabs.setdefault(ev.index_id, IndexTab(id=ev.index_id, title=ev.index_title,
                                              order=ev.display_order))  # fmt: skip
    ordered = sorted(tabs.values(), key=lambda t: (t.order, t.id))
    index_id = index if index is not None else ordered[0].id
    if index_id not in tabs:
        raise NoData(f"지수 {index_id} 일정 없음")
    mine = sorted((ev for ev in cal if ev.index_id == index_id), key=lambda e: e.effective_date)
    picked = _pick(mine, review, today)
    runs: list[IndexRunView] = []
    if picked is not None:
        latest = read.repos.index_review.latest_runs(index_id, picked.review_id)
        runs = [_run_view(read, r) for r in sorted(latest, key=lambda r: r.cutoff_kind != "model")]
    upcoming = sorted((ev for ev in cal if ev.effective_date > today),
                      key=lambda e: (e.announce_date, e.display_order))  # fmt: skip
    data = IndexReview(
        indexes=ordered, index_id=index_id,
        review=_dates(picked, today) if picked else None,
        reviews=[_dates(ev, today) for ev in mine],
        runs=runs, calendar=[_dates(ev, today) for ev in upcoming],
    )  # fmt: skip
    notes: list[str] = []
    if picked is not None and not picked.confirmed:
        notes.append("일정 추정 — 공표 일정표와 대조 전")
    if picked is not None and not runs:
        notes.append("예측 없음 — index.predict 17:20(장 마감 뒤)")
    for r in runs:
        if r.status == "no_constituents":
            notes.append("구성종목 목록 없음 — 예측하지 않는다(로그인 VM 에 목록을 올린다)")
    as_of = _as_of(read, index_id, picked)
    q = worst([*(r.quality for r in runs), *(ev.quality for ev in mine)])
    return envelope(data, source=SOURCE, as_of=as_of, quality=q, notes=notes,
                    generated_at=read.now())  # fmt: skip


def _as_of(read: ReadContext, index_id: str, picked: ReviewEvent | None) -> datetime:
    if picked is not None:
        runs = read.repos.index_review.latest_runs(index_id, picked.review_id)
        if runs:
            return max(r.data_as_of for r in runs)
    return datetime.combine(read.today(), time(0, 0), tzinfo=KST)


def _label(title: str, verdict: str, rise: float | None) -> str:
    head = f"{title} {_VERDICT_KO.get(verdict, verdict)}"
    if rise is None:
        return head
    if rise > 0:
        need = "유지에 필요" if verdict == "delete" else "필요"
        return f"{head} · {need} +{rise * 100:.1f}%"
    return f"{head} · 여유 {rise * 100:.1f}%"


def stock_badges(read: ReadContext, code: str) -> Envelope[StockIndexBadges]:
    """종목의 지수별 판정(효력 전 리뷰만, 유지는 빼고) — 페이지 8 머리 한 줄."""
    today = read.today()
    cal = {(ev.index_id, ev.review_id): ev for ev in read.repos.index_review.calendar()}
    badges: list[StockIndexBadge] = []
    as_of: datetime | None = None
    for p in read.repos.index_review.predictions_for_code(code):
        ev = cal.get((p.index_id, p.review_id))
        if ev is None or ev.effective_date <= today or p.verdict == "keep":
            continue
        rise = _num(p.evidence, "required_rise")
        badges.append(StockIndexBadge(
            index_id=p.index_id, index_title=ev.index_title, review_id=p.review_id,
            cutoff_kind=p.cutoff_kind, verdict=p.verdict, required_rise=rise,
            label=_label(ev.index_title, p.verdict, rise), quality=p.quality,
        ))  # fmt: skip
        as_of = p.data_as_of if as_of is None else max(as_of, p.data_as_of)
    badges.sort(key=lambda b: (cal[(b.index_id, b.review_id)].display_order, b.cutoff_kind))
    q = worst(b.quality for b in badges) if badges else Quality.OK
    return envelope(
        StockIndexBadges(code=code, badges=badges), source=SOURCE,
        as_of=as_of or datetime.combine(today, time(0, 0), tzinfo=KST), quality=q,
        notes=[] if badges else ["지수 편입·편출 후보 아님(또는 예측 없음)"],
        generated_at=read.now(),
    )  # fmt: skip

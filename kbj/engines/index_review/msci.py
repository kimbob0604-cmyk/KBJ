"""컷오프 배수 규칙 — MSCI Korea(Standard)·FTSE 형(metrics §24.6).

순서
1. 구성종목이 하나도 없으면 `no_constituents` — 예측을 내지 않는다(지어내지 않음).
2. 대상: 설정 kinds(보통주)·exclude_flags(거래정지·정리매매) 밖. 대상 밖 구성종목은 편출 후보(사유).
3. 유동비율: 리뷰 날짜로 고른 규칙(`fif_rule`)으로 반올림·완충(기존 구성종목만) → FOL(0.1% 반올림)
   조정 → foreign room 계수(`fif.room_factor`). 모르면 None.
4. 컷오프: 사용자 값(`cutoff_override` — kind user)이 있으면 그것, 없으면 커버리지 추정(kind model).
5. 배수(`multipliers` — GIMI 2026-08판 §3.1.5·§3.1.6): 구성종목 keep(⅔·⅓), 비구성종목은 하위
   세그먼트(Small Cap) 이동 1.5× · IMI 밖 신규 1.0×(모르면 설정 segment_default) · 유동 0.5×.
   유동비율 < min_fif_add 면 유동 배수 low_fif(0.9× 신규·0.6× 유지 — 설정에 없으면 신규 편입 불가).
   Light Rebalancing 이 켜져 있으면 신규 배수는 그 값(1.8×·0.9×).
6. 판정(g = min(시총 괴리율, 유동 괴리율)): |g| < borderline 이면 경계, 아니면 구성종목은 g ≥ 0
   유지·g < 0 편출, 비구성종목은 g ≥ 0 편입·g < 0 후보 아님. 주가로 풀 수 없는 편입 장애
   (유동비율 하한 — low_fif 배수가 없을 때, foreign room < 15%)는 편입 대신 경계(사유).
   유동비율을 모르면 시총 조건만 보고 경계까지(사유 — estimated). 유동 조건은 구성종목이면
   room 계수 전, 비구성종목이면 뒤 값으로 본다(지수 비중·패시브 매매는 계수 뒤 값).
7. 예상 패시브 매매(`passive.with_passive`).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Final

from kbj.config.index_reviews import FifRule, FolCfg, MsciRules
from kbj.engines.index_review.cutoff import estimate_cutoff
from kbj.engines.index_review.fif import resolve_fif, room_factor
from kbj.engines.index_review.passive import with_passive
from kbj.engines.index_review.required import ff_value, gap, meets, required_rise, threshold
from kbj.engines.index_review.types import Cutoff, IndexResult, Outcome, StockIn, StockOut

NO_LIST: Final = "구성종목 목록 없음 — 예측하지 않는다"

__all__ = ["eligible", "evaluate", "multipliers", "predict_msci"]

Room = tuple[float, bool, str | None]  # (foreign room 계수, 편입 가능, 사유)
NO_ROOM: Final[Room] = (1.0, True, None)


def _d(x: float) -> Decimal:
    return Decimal(str(x))


def eligible(s: StockIn, rules: MsciRules) -> str | None:
    """대상 밖이면 사유, 대상이면 None."""
    if s.kind is not None and s.kind not in rules.kinds:
        return f"종류 {s.kind} — 대상 아님"
    bad = set(s.flags or ()) & set(rules.exclude_flags)
    if bad:
        return f"상태 {','.join(sorted(bad))} — 대상 아님"
    if s.mktcap is None or s.mktcap <= 0:
        return "시총 없음"
    return None


def _outcome(member: bool, g: float, band: float) -> Outcome:
    if abs(g) < band:
        return "borderline"
    if member:
        return "keep" if g >= 0 else "delete"
    return "add" if g >= 0 else "none"


def multipliers(s: StockIn, fif: float | None, rules: MsciRules) -> tuple[float, float]:
    """(시총 배수, 유동 배수) — 구성 여부·세그먼트·유동비율 하한·Light Rebalancing 으로 고른다.

    시총과 무관하다(판정 단조 — 속성 시험)."""
    low = fif is not None and fif < rules.val("min_fif_add")
    if s.member:
        f = rules.val("keep_ff_mult")
        lf = rules.opt("low_fif_ff_mult_keep")
        return rules.val("keep_mcap_mult"), (lf if low and lf is not None else f)
    lr = rules.light_rebalance
    if lr is not None and lr.enabled:
        m, f = float(lr.add_mcap_mult.v or 0), float(lr.add_ff_mult.v or 0)
    else:
        seg = s.lower_segment
        migrate = seg if seg is not None else rules.segment_default == "migrate"
        m = rules.val("add_mcap_mult_migrate" if migrate else "add_mcap_mult_new")
        f = rules.val("add_ff_mult")
    lf = rules.opt("low_fif_ff_mult_add")
    if low and lf is not None:
        f = max(f, lf)
    return m, f


def evaluate(
    s: StockIn,
    fif: float | None,
    fif_how: str | None,
    cutoff: Cutoff,
    rules: MsciRules,
    rank: int | None,
    *,
    room: Room = NO_ROOM,
) -> StockOut:
    """한 종목 판정(컷오프 고정). 시총이 오르면 판정이 나빠지지 않는다(속성 시험).

    fif = foreign room 계수 전 적용 유동비율, room = (계수, 편입 가능, 사유)."""
    if s.mktcap is None or s.mktcap <= 0:
        raise ValueError(f"{s.code}: 시총이 없으면 판정하지 않는다")
    factor, addable, room_why = room
    m_mult, f_mult = multipliers(s, fif, rules)
    thr_m = threshold(m_mult, cutoff.won)
    thr_f = threshold(f_mult, cutoff.won)
    band = rules.val("borderline")
    reasons: list[str] = []
    estimated = cutoff.kind == "model"
    if room_why:
        reasons.append(room_why)
        estimated = True
    shown = None if fif is None else (fif if factor == 1.0 else float(_d(fif) * _d(factor)))
    cond = fif if s.member else shown  # 기존 종목은 계수 전 값으로 유동 요건을 본다
    g_m = gap(s.mktcap, thr_m)
    if cond is None or shown is None:
        reasons.append("유동비율 없음 — 시총 조건만")
        estimated = True
        g_f = None
        met = None
        rr, basis = required_rise(s.mktcap, None, thr_m, None)
        ff = None
        outcome = _outcome(s.member, g_m, band)
        if outcome in ("add", "keep"):
            outcome = "borderline"  # 유동 조건을 모르면 단정하지 않는다
    else:
        ffv = ff_value(s.mktcap, cond)
        ff = int(ff_value(s.mktcap, shown).to_integral_value())
        g_f = gap(ffv, thr_f)
        met = meets(s.mktcap, cond, thr_m, thr_f)
        rr, basis = required_rise(s.mktcap, cond, thr_m, thr_f)
        outcome = _outcome(s.member, min(g_m, g_f), band)
        if fif_how and fif_how != "prev":
            estimated = True
    floor = rules.val("min_fif_add")
    if fif is not None and fif < floor:
        if rules.opt("low_fif_ff_mult_add") is None:
            if not s.member:
                reasons.append(f"유동비율 {fif:.1%} < 최소 {floor:.0%} — 주가로 풀 수 없음")
                if outcome == "add":
                    outcome = "borderline"
        else:
            reasons.append(f"유동비율 {fif:.1%} < {floor:.0%} — 유동시총 문턱 {f_mult:g}× 컷오프")
    if not s.member and not addable and outcome == "add":
        outcome = "borderline"  # foreign room 부족 — 주가로 풀 수 없음(사유는 위)
    return StockOut(
        code=s.code, name=s.name, member=s.member, outcome=outcome, mktcap=s.mktcap, ff_mcap=ff,
        fif=shown, fif_raw=s.fif_raw, fif_how=fif_how, gap_mcap=g_m, gap_ff=g_f,
        condition_met=met,
        required_rise=float(rr), required_basis=basis,
        threshold_mcap=int(thr_m.to_integral_value()), threshold_ff=int(thr_f.to_integral_value()),
        rank=rank, passive_won=None, adv_won=None if s.adv_won is None else round(s.adv_won),
        adv_multiple=None, reasons=tuple(reasons), estimated=estimated,
    )  # fmt: skip


def predict_msci(
    stocks: Sequence[StockIn],
    rules: MsciRules,
    fif_rule: FifRule,
    fol: FolCfg,
    *,
    cutoff_override: int | None = None,
    aum_won: int | None = None,
) -> IndexResult:
    members = [s for s in stocks if s.member]
    if not members:
        return IndexResult("no_constituents", None, (), 0, (NO_LIST,), True)
    notes: list[str] = []
    ok: list[StockIn] = []
    out: list[StockOut] = []
    for s in stocks:
        why = eligible(s, rules)
        if why is None:
            ok.append(s)
        elif s.member:  # 대상 밖 구성종목 — 편출 후보(시세가 없으면 단정하지 않고 경계)
            no_px = s.mktcap is None or s.mktcap <= 0
            out.append(StockOut(
                code=s.code, name=s.name, member=True, outcome="borderline" if no_px else "delete",
                mktcap=s.mktcap, ff_mcap=None, fif=None, fif_raw=s.fif_raw, fif_how=None,
                gap_mcap=None, gap_ff=None, condition_met=False if not no_px else None,
                required_rise=None, required_basis=None, threshold_mcap=None, threshold_ff=None,
                rank=None, passive_won=None, adv_won=None, adv_multiple=None, reasons=(why,),
                estimated=True,
            ))  # fmt: skip
    step = fol.val("limit_step")
    fifs: dict[str, tuple[float | None, str | None]] = {
        s.code: resolve_fif(s.fif_raw, s.fif_prev, fif_rule, fol.limit_of(s.code),
                            member=s.member, fol_step=step)
        for s in ok
    }  # fmt: skip
    rooms: dict[str, Room] = {
        s.code: room_factor(fol, fol.limit_of(s.code), s.foreign_held, member=s.member) for s in ok
    }
    n_seg = sum(1 for s in ok if not s.member and s.lower_segment is None)
    lr = rules.light_rebalance
    if lr is not None and lr.enabled:
        notes.append("Light Rebalancing — 신규 편입 배수 "
                     f"{lr.add_mcap_mult.v:g}×·유동 {lr.add_ff_mult.v:g}×")  # fmt: skip
    elif n_seg:
        mult = rules.val(
            "add_mcap_mult_migrate" if rules.segment_default == "migrate" else "add_mcap_mult_new"
        )
        notes.append(f"비구성종목 {n_seg}종목은 하위 세그먼트 여부를 몰라 시총 {mult:g}×로 봤다")
    if cutoff_override is not None:
        if cutoff_override <= 0:
            raise ValueError("사용자 컷오프는 0 보다 크다")
        cutoff: Cutoff | None = Cutoff(cutoff_override, None, None, "user", "user")
    else:
        items = [
            (s.code, s.mktcap or 0, ff_value(s.mktcap or 0, f) * _d(rooms[s.code][0]))
            for s in ok
            if (f := fifs[s.code][0]) is not None
        ]
        n_unknown = len(ok) - len(items)
        if n_unknown:
            notes.append(f"유동비율 없는 {n_unknown}종목은 컷오프 추정에서 뺐다")
        cutoff = estimate_cutoff(items, rules)
    if cutoff is None:
        why = "컷오프를 추정할 시세·유동비율 없음"
        return IndexResult("no_data", None, (), len(members), (why,), True)
    ranked = sorted(ok, key=lambda s: (-(s.mktcap or 0), s.code))
    rank = {s.code: i + 1 for i, s in enumerate(ranked)}
    for s in ok:
        fif, how = fifs[s.code]
        out.append(evaluate(s, fif, how, cutoff, rules, rank[s.code], room=rooms[s.code]))
    out = with_passive(out, aum_won)
    out.sort(key=lambda o: (o.rank is None, o.rank or 0, o.code))
    estimated = cutoff.kind == "model" or any(o.estimated for o in out if o.outcome != "none")
    if aum_won is None:
        notes.append("추종 자금 없음 — 예상 패시브 매매를 계산하지 않았다")
    return IndexResult("ok", cutoff, tuple(out), len(members), tuple(notes), estimated)

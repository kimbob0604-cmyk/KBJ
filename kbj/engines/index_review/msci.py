"""컷오프 배수 규칙 — MSCI Korea(Standard)·FTSE 형(metrics §24.6).

순서
1. 구성종목이 하나도 없으면 `no_constituents` — 예측을 내지 않는다(지어내지 않음).
2. 대상: 설정 kinds(보통주)·exclude_flags(거래정지·정리매매) 밖. 대상 밖 구성종목은 편출 후보(사유).
3. 유동비율: 리뷰 날짜로 고른 규칙(`fif_rule`)으로 반올림·완충 → FOL 조정. 모르면 None.
4. 컷오프: 사용자 값(`cutoff_override` — kind user)이 있으면 그것, 없으면 커버리지 추정(kind model).
5. 판정(g = min(시총 괴리율, 유동 괴리율)): |g| < borderline 이면 경계, 아니면 구성종목은 g ≥ 0
유지·
   g < 0 편출, 비구성종목은 g ≥ 0 편입·g < 0 후보 아님. 비구성종목은 유동비율 < min_fif_add 면
   주가로
   풀 수 없어 편입하지 않는다(사유). 유동비율을 모르면 시총 조건만 보고 경계까지(사유 — estimated).
6. 예상 패시브 매매(`passive.with_passive`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from kbj.config.index_reviews import FifRule, FolCfg, MsciRules
from kbj.engines.index_review.cutoff import estimate_cutoff
from kbj.engines.index_review.fif import resolve_fif
from kbj.engines.index_review.passive import with_passive
from kbj.engines.index_review.required import ff_value, gap, meets, required_rise, threshold
from kbj.engines.index_review.types import Cutoff, IndexResult, Outcome, StockIn, StockOut

NO_LIST: Final = "구성종목 목록 없음 — 예측하지 않는다"

__all__ = ["eligible", "evaluate", "predict_msci"]


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


def evaluate(
    s: StockIn,
    fif: float | None,
    fif_how: str | None,
    cutoff: Cutoff,
    rules: MsciRules,
    rank: int | None,
) -> StockOut:
    """한 종목 판정(컷오프 고정). 시총이 오르면 판정이 나빠지지 않는다(속성 시험)."""
    if s.mktcap is None or s.mktcap <= 0:
        raise ValueError(f"{s.code}: 시총이 없으면 판정하지 않는다")
    m_mult, f_mult = (
        (rules.val("keep_mcap_mult"), rules.val("keep_ff_mult")) if s.member
        else (rules.val("add_mcap_mult"), rules.val("add_ff_mult"))
    )  # fmt: skip
    thr_m = threshold(m_mult, cutoff.won)
    thr_f = threshold(f_mult, cutoff.won)
    band = rules.val("borderline")
    reasons: list[str] = []
    estimated = cutoff.kind == "model"
    g_m = gap(s.mktcap, thr_m)
    if fif is None:
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
        ffv = ff_value(s.mktcap, fif)
        ff = int(ffv.to_integral_value())
        g_f = gap(ffv, thr_f)
        met = meets(s.mktcap, fif, thr_m, thr_f)
        rr, basis = required_rise(s.mktcap, fif, thr_m, thr_f)
        outcome = _outcome(s.member, min(g_m, g_f), band)
        if fif_how and fif_how != "prev":
            estimated = True
    if not s.member and fif is not None and fif < rules.val("min_fif_add"):
        floor = rules.val("min_fif_add")
        reasons.append(f"유동비율 {fif:.1%} < 최소 {floor:.0%} — 주가로 풀 수 없음")
        if outcome == "add":
            outcome = "borderline"
    return StockOut(
        code=s.code, name=s.name, member=s.member, outcome=outcome, mktcap=s.mktcap, ff_mcap=ff,
        fif=fif, fif_raw=s.fif_raw, fif_how=fif_how, gap_mcap=g_m, gap_ff=g_f, condition_met=met,
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
    fifs: dict[str, tuple[float | None, str | None]] = {
        s.code: resolve_fif(s.fif_raw, s.fif_prev, fif_rule, fol.limit_of(s.code)) for s in ok
    }
    if cutoff_override is not None:
        if cutoff_override <= 0:
            raise ValueError("사용자 컷오프는 0 보다 크다")
        cutoff: Cutoff | None = Cutoff(cutoff_override, None, None, "user", "user")
    else:
        items = [
            (s.code, s.mktcap or 0, ff_value(s.mktcap or 0, f))
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
        out.append(evaluate(s, fif, how, cutoff, rules, rank[s.code]))
    out = with_passive(out, aum_won)
    out.sort(key=lambda o: (o.rank is None, o.rank or 0, o.code))
    estimated = cutoff.kind == "model" or any(o.estimated for o in out if o.outcome != "none")
    if aum_won is None:
        notes.append("추종 자금 없음 — 예상 패시브 매매를 계산하지 않았다")
    return IndexResult("ok", cutoff, tuple(out), len(members), tuple(notes), estimated)

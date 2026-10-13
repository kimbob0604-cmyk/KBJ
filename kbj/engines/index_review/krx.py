"""시총 순위·완충 규칙 — KOSPI200·KOSDAQ150·KRX300 형(metrics §24.7).

KRX 지수 산출 방법론 원문을 옮기기 전 근사다 [확인 필요] — 업종별 누적 시총 비중 규칙은 아직 없다.

순서
1. 구성종목이 없으면 `no_constituents`.
2. 대상: 설정 시장(markets)·kinds·exclude_flags(관리·거래정지·정리매매) 밖. 대상 밖 구성종목은 편출
후보.
3. 순위 = 대상 종목 전체 시총 내림차순(1 = 가장 큼).
4. 유동성: 산업군 안 일평균 거래대금 순위 백분위 ≤ liquidity_pct. 거래대금을 모르면 판정 보류(None).
5. 문턱: 신규 = 순위 floor(size × add_rank_ratio) 종목의 시총, 유지 = floor(size × keep_rank_ratio).
   괴리율 = 시총 ÷ 문턱 − 1, 필요 상승률 = 문턱 ÷ 시총 − 1(1bp 올림 — 다른 종목 고정).
6. 신규 상장 min_listing_days 미만은 편입하지 않는다(시총 순위 ≤ fast_entry_rank 면 특례).
7. 정원 맞추기: 편입 뒤 수 > size 면 순위 낮은 편입 후보부터 경계, < size 면 다음 순위 비구성종목을
채운다.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence
from datetime import date
from typing import Final

from kbj.config.index_reviews import KrxRules
from kbj.engines.index_review.passive import with_passive
from kbj.engines.index_review.required import gap, required_rise, threshold
from kbj.engines.index_review.types import Cutoff, IndexResult, Outcome, StockIn, StockOut

NO_LIST: Final = "구성종목 목록 없음 — 예측하지 않는다"

__all__ = ["liquidity", "predict_krx"]

DEFAULT_SECTOR = "기타"


def _eligible(s: StockIn, rules: KrxRules, markets: Sequence[str]) -> str | None:
    if s.market is not None and s.market not in markets:
        return f"시장 {s.market} — 대상 아님"
    if s.kind is not None and s.kind not in rules.kinds:
        return f"종류 {s.kind} — 대상 아님"
    bad = set(s.flags or ()) & set(rules.exclude_flags)
    if bad:
        return f"상태 {','.join(sorted(bad))} — 대상 아님"
    if s.mktcap is None or s.mktcap <= 0:
        return "시총 없음"
    return None


def liquidity(stocks: Sequence[StockIn], pct: float) -> dict[str, bool | None]:
    """산업군 안 거래대금 순위 백분위 ≤ pct 면 True. 거래대금을 모르면 None."""
    groups: dict[str, list[StockIn]] = {}
    for s in stocks:
        groups.setdefault(s.sector or DEFAULT_SECTOR, []).append(s)
    out: dict[str, bool | None] = {}
    for rows in groups.values():
        known = sorted((s for s in rows if s.adv_won is not None),
                       key=lambda s: (-(s.adv_won or 0.0), s.code))  # fmt: skip
        n = len(known)
        for i, s in enumerate(known):
            out[s.code] = (i + 1) / n <= pct + 1e-12
        for s in rows:
            out.setdefault(s.code, None)
    return out


def _outcome(member: bool, met: bool, g: float, band: float) -> Outcome:
    if abs(g) < band:
        return "borderline"
    if member:
        return "keep" if met else "delete"
    return "add" if met else "none"


def predict_krx(
    stocks: Sequence[StockIn],
    rules: KrxRules,
    markets: Sequence[str],
    base_date: date,
    *,
    aum_won: int | None = None,
) -> IndexResult:
    members = [s for s in stocks if s.member]
    if not members:
        return IndexResult("no_constituents", None, (), 0, (NO_LIST,), True)
    size = int(rules.val("size"))
    add_n = max(1, math.floor(size * rules.val("add_rank_ratio") + 1e-9))
    keep_n = max(add_n, math.floor(size * rules.val("keep_rank_ratio") + 1e-9))
    band = rules.val("borderline")
    notes: list[str] = []
    ok: list[StockIn] = []
    out: list[StockOut] = []
    for s in stocks:
        why = _eligible(s, rules, markets)
        if why is None:
            ok.append(s)
        elif s.member:
            no_px = s.mktcap is None or s.mktcap <= 0
            out.append(StockOut(
                code=s.code, name=s.name, member=True, outcome="borderline" if no_px else "delete",
                mktcap=s.mktcap, ff_mcap=None, fif=None, fif_raw=s.fif_raw, fif_how=None,
                gap_mcap=None, gap_ff=None, condition_met=False if not no_px else None,
                required_rise=None, required_basis=None, threshold_mcap=None, threshold_ff=None,
                rank=None, passive_won=None, adv_won=None, adv_multiple=None, reasons=(why,),
                estimated=True,
            ))  # fmt: skip
    if not ok:
        return IndexResult("no_data", None, (), len(members), ("시세 없음",), True)
    ranked = sorted(ok, key=lambda s: (-(s.mktcap or 0), s.code))
    rank = {s.code: i + 1 for i, s in enumerate(ranked)}
    add_thr_won = ranked[min(add_n, len(ranked)) - 1].mktcap or 0
    keep_thr_won = ranked[min(keep_n, len(ranked)) - 1].mktcap or 0
    liquid = liquidity(ok, rules.val("liquidity_pct"))
    min_days = int(rules.val("min_listing_days"))
    fast = int(rules.val("fast_entry_rank"))
    blocked: set[str] = set()  # 유동성·상장 기간으로 막힌 종목(정원 채움에서도 뺀다)
    for s in ok:
        if s.mktcap is None:
            raise ValueError(f"{s.code}: 시총 없음")
        thr_won = keep_thr_won if s.member else add_thr_won
        thr = threshold(1.0, thr_won)
        g = gap(s.mktcap, thr)
        rr, _ = required_rise(s.mktcap, None, thr, None)
        reasons: list[str] = []
        lq = liquid.get(s.code)
        met = rank[s.code] <= (keep_n if s.member else add_n)
        if lq is False:
            met = False
            reasons.append("산업군 내 거래대금 순위 밖")
        elif lq is None:
            reasons.append("거래대금 없음 — 유동성 판정 보류")
        if not s.member and s.listed_on is not None and (base_date - s.listed_on).days < min_days:
            if rank[s.code] <= fast:
                reasons.append(f"신규 상장 특례(시총 {rank[s.code]}위 ≤ {fast})")
            else:
                met = False
                reasons.append(f"상장 {min_days}일 미만")
        outcome = _outcome(s.member, met, g, band)
        if lq is None and outcome in ("add", "keep"):
            outcome = "borderline"
        if lq is False or any("미만" in r for r in reasons):
            blocked.add(s.code)
        out.append(StockOut(
            code=s.code, name=s.name, member=s.member, outcome=outcome, mktcap=s.mktcap,
            ff_mcap=None if s.fif_raw is None else round(s.mktcap * s.fif_raw), fif=s.fif_raw,
            fif_raw=s.fif_raw, fif_how=None if s.fif_raw is None else "estimate", gap_mcap=g,
            gap_ff=None, condition_met=met, required_rise=float(rr), required_basis="mcap",
            threshold_mcap=thr_won, threshold_ff=None, rank=rank[s.code], passive_won=None,
            adv_won=None if s.adv_won is None else round(s.adv_won), adv_multiple=None,
            reasons=tuple(reasons), estimated=True,
        ))  # fmt: skip
    out = _fit_size(out, size, blocked, notes)
    out = with_passive(out, aum_won)
    out.sort(key=lambda o: (o.rank is None, o.rank or 0, o.code))
    cutoff = Cutoff(add_thr_won, None, keep_thr_won, f"rank {add_n}/{keep_n}", "model")
    if aum_won is None:
        notes.append("추종 자금 없음 — 예상 패시브 매매를 계산하지 않았다")
    return IndexResult("ok", cutoff, tuple(out), len(members), tuple(notes), True)


def _fit_size(
    out: list[StockOut], size: int, blocked: set[str], notes: list[str]
) -> list[StockOut]:
    stay = sum(1 for o in out if o.member and o.outcome != "delete")
    adds = sorted((o for o in out if o.outcome == "add"), key=lambda o: (o.rank or 0, o.code))
    n_after = stay + len(adds)
    by_code = {o.code: o for o in out}
    if n_after > size:
        extra = n_after - size
        for o in reversed(adds[-extra:] if extra <= len(adds) else adds):
            by_code[o.code] = dataclasses.replace(
                o, outcome="borderline", reasons=(*o.reasons, "정원 초과 — 순위 낮은 신규")
            )
        notes.append(f"정원 {size} 초과 {extra} — 순위 낮은 신규를 경계로")
    elif n_after < size:
        need = size - n_after
        pool = sorted(
            (o for o in out
             if not o.member and o.outcome in ("none", "borderline") and o.code not in blocked
             and o.rank is not None),
            key=lambda o: (o.rank or 0, o.code),
        )  # fmt: skip
        for o in pool[:need]:
            by_code[o.code] = dataclasses.replace(
                o, outcome="add", reasons=(*o.reasons, "정원 채움 — 다음 순위")
            )
        if pool[:need]:
            notes.append(f"정원 {size} 부족 — 다음 순위 {len(pool[:need])}종목을 채웠다")
    return [by_code[o.code] for o in out]

"""시총 순위·완충 규칙 — KOSPI200·KOSDAQ150·KRX300 형(metrics §24.7).

KRX 방법론 원문을 직접 받지 못해 2차 자료(삼성증권 2026-03-27 등)로 옮긴 근사다 [확인 필요].
산업군은 설정 `sectors`(KSIC 근사 — 2026-11-02 KRX 개정의 KRICS 9개 산업군 매핑은 TODO).
예비종목 제도는 2026-11-02 개정으로 폐지 — 이 엔진에 없다.

순서
1. 구성종목이 없으면 `no_constituents`.
2. 대상: 설정 시장(markets)·kinds·exclude_flags(관리·거래정지·정리매매·투자유의)·exclude_foreign
   (종목코드 9 로 시작하는 외국기업) 밖. 대상 밖 구성종목은 편출 후보.
3. 심사 시총 = 심사기간 일평균 시총(`mktcap_avg`), 없으면 기준일 시총으로 대체(사유 — 추정).
   순위 = 대상 종목 전체 심사 시총 내림차순(표시·신규 상장 특례·정원 채움 순서).
4. 산업군마다 심사 시총 내림차순으로 산업군 안 순위 p, 산업군 구성종목 수 n:
   신규 = p ≤ floor(n × add_rank_ratio) 이고 산업군 누적 시총 비중 안(자기보다 큰 종목 비중 <
   sector_cum_mcap — 문턱에 처음 닿는 종목까지), 기존 = p ≤ floor(n × keep_rank_ratio).
   문턱 = 그 경계 위치 종목의 심사 시총. 괴리율 = 심사 시총 ÷ 문턱 − 1, 필요 상승률 = 문턱 ÷ 심사
   시총 − 1(1bp 올림 — 다른 종목 고정). 시총이 오르면 p 는 나빠지지 않고 누적 비중도 줄어든다(단조).
5. 유동성: 산업군 안 일평균 거래대금 순위 백분위 ≤ liquidity_pct. 거래대금을 모르면 판정 보류(None).
6. 신규 상장 min_listing_days 미만은 편입하지 않는다(전체 순위 ≤ fast_entry_rank 면 특례).
7. 정원 맞추기: 편입 뒤 수 > size 면 순위 낮은 편입 후보부터 경계, < size 면 다음 순위 비구성종목을
   채운다. 채운 종목의 문턱은 채운 것 가운데 가장 작은 심사 시총(마지막 자리) — 괴리율 ≥ 0·필요
   상승률 ≤ 0 이라 판정('편입 후보')과 숫자가 어긋나지 않는다(산업군 문턱을 그대로 두면 정보기술처럼
   큰 종목이 누적 비중을 다 채운 산업군에서 +수천 % 로 보였다).
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
FOREIGN_PREFIX: Final = "9"  # KRX 외국기업 종목코드(950xxx·900xxx)


def _eligible(s: StockIn, rules: KrxRules, markets: Sequence[str]) -> str | None:
    if s.market is not None and s.market not in markets:
        return f"시장 {s.market} — 대상 아님"
    if s.kind is not None and s.kind not in rules.kinds:
        return f"종류 {s.kind} — 대상 아님"
    if rules.exclude_foreign and s.code.startswith(FOREIGN_PREFIX):
        return "외국기업 — 대상 아님"
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


def _cap(s: StockIn) -> int:
    """심사 시총 — 심사기간 일평균, 없으면 기준일 시총."""
    return s.mktcap_avg if s.mktcap_avg is not None and s.mktcap_avg > 0 else (s.mktcap or 0)


def sector_limits(
    stocks: Sequence[StockIn], add_ratio: float, keep_ratio: float, cum_pct: float | None
) -> dict[str, tuple[str, int, int, int, int, int | None, int]]:
    """종목 → (산업군, 산업군 안 순위, 산업군 구성종목 수, 신규 한도 위치, 유지 한도 위치,
    신규 문턱(원 — 한도 0 이면 None), 유지 문턱(원)). 순위·문턱은 심사 시총 기준."""
    groups: dict[str, list[StockIn]] = {}
    for s in stocks:
        groups.setdefault(s.sector or DEFAULT_SECTOR, []).append(s)
    out: dict[str, tuple[str, int, int, int, int, int | None, int]] = {}
    for sec, rows in groups.items():
        rows = sorted(rows, key=lambda s: (-_cap(s), s.code))
        n_mem = sum(1 for s in rows if s.member)
        add_k = math.floor(n_mem * add_ratio + 1e-9)
        keep_k = max(add_k, math.floor(n_mem * keep_ratio + 1e-9), 1)
        if cum_pct is not None:
            total = sum(_cap(s) for s in rows)
            larger, cum_k = 0, 0
            for s in rows:  # 자기보다 큰 종목 비중 < 문턱인 앞부분(비중은 위치에 따라 늘기만 한다)
                if total <= 0 or larger / total >= cum_pct - 1e-12:
                    break
                cum_k += 1
                larger += _cap(s)
            add_k = min(add_k, cum_k)
        add_k = min(add_k, len(rows))
        keep_k = min(keep_k, len(rows))
        add_thr = _cap(rows[add_k - 1]) if add_k >= 1 else None
        keep_thr = _cap(rows[keep_k - 1])
        for i, s in enumerate(rows):
            out[s.code] = (sec, i + 1, n_mem, add_k, keep_k, add_thr, keep_thr)
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
    add_ratio, keep_ratio = rules.val("add_rank_ratio"), rules.val("keep_rank_ratio")
    cum_pct = rules.opt("sector_cum_mcap")
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
    n_no_avg = sum(1 for s in ok if s.mktcap_avg is None or s.mktcap_avg <= 0)
    if n_no_avg:
        notes.append(f"심사기간 평균 시총 없는 {n_no_avg}종목은 기준일 시총으로 대체(추정)")
    ranked = sorted(ok, key=lambda s: (-_cap(s), s.code))
    rank = {s.code: i + 1 for i, s in enumerate(ranked)}
    lim = sector_limits(ok, add_ratio, keep_ratio, cum_pct)
    liquid = liquidity(ok, rules.val("liquidity_pct"))
    min_days = int(rules.val("min_listing_days"))
    fast = int(rules.val("fast_entry_rank"))
    blocked: set[str] = set()  # 유동성·상장 기간으로 막힌 종목(정원 채움에서도 뺀다)
    for s in ok:
        if s.mktcap is None:
            raise ValueError(f"{s.code}: 시총 없음")
        sec, p, n_mem, add_k, keep_k, add_thr, keep_thr = lim[s.code]
        cap = _cap(s)
        reasons: list[str] = []
        if s.member:
            thr_won: int = keep_thr
            met = p <= keep_k
        elif add_thr is None:
            thr_won = keep_thr if keep_thr > 0 else cap
            met = False
            reasons.append(f"산업군 {sec} 구성종목 {n_mem} — 신규 편입 자리 없음")
        else:
            thr_won = add_thr
            met = p <= add_k
        reasons.append(f"산업군 {sec} 안 {p}위/구성 {n_mem}(한도 {keep_k if s.member else add_k})")
        thr = threshold(1.0, thr_won)
        g = gap(cap, thr)
        rr, _ = required_rise(cap, None, thr, None)
        lq = liquid.get(s.code)
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
        if not s.member and add_thr is None:
            outcome = "none"  # 산업군에 신규 자리가 없다(문턱 없음 — 경계로 보이지 않는다)
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
    out = _fit_size(out, size, blocked, notes, {s.code: _cap(s) for s in ok})
    out = with_passive(out, aum_won)
    out.sort(key=lambda o: (o.rank is None, o.rank or 0, o.code))
    # 대표 문턱(화면용) = 산업군 신규·유지 문턱 가운데 가장 낮은 값 — 산업군마다 다르다
    adds = [v[5] for v in lim.values() if v[5] is not None]
    keeps = [v[6] for v in lim.values()]
    method = f"sector rank {add_ratio:.0%}/{keep_ratio:.0%}" + (
        f" cum {cum_pct:.0%}" if cum_pct is not None else ""
    )
    cutoff = Cutoff(min(adds) if adds else min(keeps), None, min(keeps), method, "model")
    if aum_won is None:
        notes.append("추종 자금 없음 — 예상 패시브 매매를 계산하지 않았다")
    return IndexResult("ok", cutoff, tuple(out), len(members), tuple(notes), True)


def _fit_size(
    out: list[StockOut], size: int, blocked: set[str], notes: list[str], caps: dict[str, int]
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
        filled = pool[:need]
        last = min((caps[o.code] for o in filled), default=0)  # 마지막 자리 심사 시총
        for o in filled:
            thr = threshold(1.0, last)
            rr, _ = required_rise(caps[o.code], None, thr, None)
            by_code[o.code] = dataclasses.replace(
                o, outcome="add", condition_met=True, threshold_mcap=last,
                gap_mcap=gap(caps[o.code], thr), required_rise=float(rr),
                reasons=(*o.reasons, "정원 채움 — 다음 순위(문턱 = 마지막 자리 시총)"),
            )  # fmt: skip
        if pool[:need]:
            notes.append(f"정원 {size} 부족 — 다음 순위 {len(pool[:need])}종목을 채웠다")
    return [by_code[o.code] for o in out]

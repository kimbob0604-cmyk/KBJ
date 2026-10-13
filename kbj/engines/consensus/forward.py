"""결산연도 FY1·FY2 와 12개월 선행(NTM) 추정 — §4.7, docs/metrics.md §10.

- 스냅: t 이하 가장 늦은 `snap_date` 의 행들만 쓴다(`latest_snapshot`). 그 날짜가 t 보다 이르면 결과
  품질은 stale(수집이 멈춘 날 — 지난 값임을 숨기지 않는다).
- FY1 = 추정(`is_estimate`) 행 가운데 결산일이 t **뒤**인 가장 이른 결산년월, FY2 = 그다음.
- NTM = w·FY1 + (1 − w)·FY2, w = clip((FY1 결산일 − t) ÷ 365, 0, 1). 칸마다 FY1·FY2 둘 다 값이 있을
  때만(하나라도 None 이면 그 칸 None). FY2 가 없으면 NTM 칸은 모두 None 이고 FY1 값은 `fy1` 로 따로
  남는다(FY1 만으로 NTM 을 지어내지 않는다).
- 지배순이익(`ni_owner`)은 KIS 파서가 채우지 않는다(순이익 기준 [확인 필요 #29]) — 선행 PER 은 V 가
  NTM EPS 경로로 낸다(§4.8).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Final

from kbj.core.fin_rows import ConsensusRow
from kbj.core.quality import Quality

__all__ = ["METRICS", "FyEstimate", "Ntm", "fiscal_years", "latest_snapshot", "ntm", "worst"]

METRICS: Final[tuple[str, ...]] = ("revenue", "op", "ni", "ni_owner", "eps", "ebitda")
_RANK: Final = {Quality.OK: 0, Quality.STALE: 1, Quality.ESTIMATED: 2, Quality.INVALID: 3}


def worst(*qs: Quality) -> Quality:
    """가장 나쁜 품질(ok < stale < estimated < invalid)."""
    return max(qs, key=lambda q: _RANK[q], default=Quality.OK)


@dataclass(frozen=True)
class FyEstimate:
    """결산연도 하나의 추정(금액 원, EPS 원/주)."""

    fiscal_ym: str
    fiscal_end: date
    revenue: int | None
    op: int | None
    ni: int | None
    ni_owner: int | None
    eps: float | None
    ebitda: int | None
    quality: Quality

    @classmethod
    def of(cls, r: ConsensusRow) -> FyEstimate:
        return cls(r.fiscal_ym, r.fiscal_end, r.revenue, r.op, r.ni, r.ni_owner, r.eps, r.ebitda,
                   r.quality)  # fmt: skip


@dataclass(frozen=True)
class Ntm:
    """12개월 선행 추정. 칸은 실수(가중 평균 — 원·원/주). FY2 가 없으면 NTM 칸은 None."""

    code: str
    as_of: date
    snap_date: date
    fy1: FyEstimate
    fy2: FyEstimate | None
    weight: float | None
    revenue: float | None
    op: float | None
    ni: float | None
    ni_owner: float | None
    eps: float | None
    ebitda: float | None
    source: str
    quality: Quality


def latest_snapshot(rows: Iterable[ConsensusRow], t: date) -> list[ConsensusRow]:
    """t 이하 가장 늦은 snap_date 의 행들(결산년월 순). 한 종목만 받는다."""
    best: date | None = None
    keep: list[ConsensusRow] = []
    code: str | None = None
    for r in rows:
        if code is None:
            code = r.code
        elif r.code != code:
            raise ValueError(f"다른 종목의 스냅이 섞였다: {code} · {r.code}")
        if r.snap_date > t:
            continue
        if best is None or r.snap_date > best:
            best, keep = r.snap_date, [r]
        elif r.snap_date == best:
            keep.append(r)
    return sorted(keep, key=lambda r: (r.fiscal_ym, r.source))


def fiscal_years(
    rows: Iterable[ConsensusRow], t: date
) -> tuple[ConsensusRow | None, ConsensusRow | None]:
    """(FY1, FY2) — t 이하 마지막 스냅에서 결산일이 t 뒤인 추정 행 둘(없으면 None)."""
    est = [r for r in latest_snapshot(rows, t) if r.is_estimate and r.fiscal_end > t]
    est.sort(key=lambda r: r.fiscal_ym)
    fy1 = est[0] if est else None
    fy2 = est[1] if len(est) > 1 else None
    return fy1, fy2


def _blend(w: float, a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return w * float(a) + (1.0 - w) * float(b)


def ntm(rows: Iterable[ConsensusRow], t: date) -> Ntm | None:
    """t 기준 NTM. FY1 이 없으면 None."""
    fy1, fy2 = fiscal_years(rows, t)
    if fy1 is None:
        return None
    q = worst(fy1.quality, *(() if fy2 is None else (fy2.quality,)))
    if fy1.snap_date < t:
        q = worst(q, Quality.STALE)
    one = FyEstimate.of(fy1)
    two = None if fy2 is None else FyEstimate.of(fy2)
    if two is None:
        return Ntm(fy1.code, t, fy1.snap_date, one, None, None, None, None, None, None, None, None,
                   fy1.source, q)  # fmt: skip
    w = min(1.0, max(0.0, (one.fiscal_end - t).days / 365))
    vals = {m: _blend(w, getattr(one, m), getattr(two, m)) for m in METRICS}
    return Ntm(
        code=fy1.code,
        as_of=t,
        snap_date=fy1.snap_date,
        fy1=one,
        fy2=two,
        weight=w,
        revenue=vals["revenue"],
        op=vals["op"],
        ni=vals["ni"],
        ni_owner=vals["ni_owner"],
        eps=vals["eps"],
        ebitda=vals["ebitda"],
        source=fy1.source,
        quality=q,
    )

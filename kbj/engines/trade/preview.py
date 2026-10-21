"""실적 프리뷰(P10 단계 3 — docs/p10_trade_stocks_plan.md, 나쁜양파 'Earning Preview' 같은 판정).

종목 수출 지수(매핑 흐름 가중합, 달러)를 원화로 바꿔 분기마다 더하고, 과거 분기 매출과의 회귀
(OLS 매출 = a + b × 수출)로 **추정 대상 분기** 매출을 추정한 뒤 컨센서스와 비교한다(순수 함수).

- 추정 대상 분기 = 수출 지수가 있는 마지막 달의 분기. 그 분기 매출이 이미 공시됐으면 `reported` 로
  표시만 한다(맞았는지 보는 사후 대조).
- 진행 중 분기(1~2개월만 있음)는 있는 달 평균 × 3 으로 채운다(EST). 환율은 달 평균 원/달러 — 없으면
  달러 그대로 회귀(메모).
- 컨센서스: 연간 추정 매출(12월 결산만) − 그해 이미 공시된 분기 합 → 남은 분기 수로 나눈 값(분기
  컨센서스가 따로 없을 때의 근사 — 화면에 그렇게 적는다).
- 판정: 추정 ÷ 컨센서스 − 1 이 +band 초과 BEAT, −band 미만 MISS, 그 사이 IN LINE. 회귀 점이 적거나
  R² 가 낮으면 신뢰 '낮음'(판정은 하되 그렇게 적는다).
- 매매 권유가 아니다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from kbj.core.trade_rows import prev_ym
from kbj.engines.trade.overlay import quarter_of

__all__ = ["Preview", "PreviewCfg", "QuarterFit", "fit_line", "preview", "quarter_months"]

Verdict = Literal["BEAT", "IN LINE", "MISS"]


@dataclass(frozen=True)
class PreviewCfg:
    band: float = 0.05  # ±5% 안이면 IN LINE
    min_points: int = 6  # 회귀에 쓸 최소 분기 수
    max_points: int = 12  # 최근 이 분기 수만
    r2_ok: float = 0.5  # 이 이상이면 신뢰 '보통', 0.75 이상 '높음'
    r2_high: float = 0.75


@dataclass(frozen=True)
class QuarterFit:
    quarter: str
    export_krw: float | None  # 분기 수출(원 환산, 진행 중 분기는 채운 값)
    revenue: int | None  # 공시 매출(원)
    fitted: float | None  # 회귀 값
    months: int  # 그 분기에 수출 지수가 있는 달 수


@dataclass(frozen=True)
class Preview:
    quarter: str
    months_seen: int
    reported: bool  # 그 분기 매출이 이미 공시됨(사후 대조)
    export_krw: float | None
    revenue_est: float | None
    consensus: float | None
    consensus_note: str | None
    diff: float | None
    verdict: Verdict | None
    r2: float | None
    points: int
    confidence: Literal["높음", "보통", "낮음"]
    history: tuple[QuarterFit, ...]
    notes: tuple[str, ...]


def quarter_months(q: str) -> tuple[str, str, str]:
    y, n = int(q[:4]), int(q[5])
    return tuple(f"{y:04d}-{3 * (n - 1) + k:02d}" for k in (1, 2, 3))  # type: ignore[return-value]


def fit_line(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float, float] | None:
    """OLS y = a + b·x → (a, b, R²). 점 2개 미만이거나 x 분산 0 이면 None."""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    a = my - b * mx
    sst = sum((y - my) ** 2 for y in ys)
    sse = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys, strict=True))
    return a, b, (1.0 - sse / sst) if sst > 0 else 0.0


def _quarter_export(
    q: str, monthly: Mapping[str, Decimal | None], fx: Mapping[str, float]
) -> tuple[float | None, int, bool]:
    """(원 환산 분기 수출, 값이 있는 달 수, 환율 없이 달러로 셈)."""
    vals: list[float] = []
    no_fx = False
    for ym in quarter_months(q):
        v = monthly.get(ym)
        if v is None:
            continue
        rate = fx.get(ym)
        if rate is None:
            no_fx = True
            rate = 1.0
        vals.append(float(v) * rate)
    if not vals:
        return None, 0, no_fx
    return sum(vals) / len(vals) * 3, len(vals), no_fx


def _consensus(
    q: str, annual: Mapping[int, int], revenue: Mapping[str, int | None]
) -> tuple[float | None, str | None]:
    year = int(q[:4])
    total = annual.get(year)
    if total is None:
        return None, None
    done = [revenue.get(f"{year}Q{k}") for k in range(1, 5)]
    reported = [v for v in done if v is not None]
    left = 4 - len(reported)
    if left <= 0 or revenue.get(q) is not None:
        return None, None
    return (
        total - sum(reported)
    ) / left, f"{year} 연간 컨센서스 − 공시 {len(reported)}개 분기 → 남은 {left}개 분기 평균"


def preview(
    monthly: Mapping[str, Decimal | None],
    revenue: Mapping[str, int | None],
    *,
    fx: Mapping[str, float],
    annual_consensus: Mapping[int, int],
    cfg: PreviewCfg = PreviewCfg(),  # noqa: B008 — 얼린 dataclass(바뀌지 않는다)
) -> Preview | None:
    """수출 지수(달 → 달러)·분기 매출(달력 분기 → 원)·환율(달 → 원/달러)·연간 컨센서스(해 → 원)."""
    have = sorted(ym for ym, v in monthly.items() if v is not None)
    if not have:
        return None
    target = quarter_of(have[-1])
    notes: list[str] = []
    qs: list[str] = []
    q = target
    for _ in range(cfg.max_points + 1):
        qs.append(q)
        q = quarter_of(prev_ym(quarter_months(q)[0], 1))
    qs.reverse()
    hist: list[tuple[str, float | None, int, int | None]] = []
    no_fx_any = False
    for qq in qs:
        x, m, no_fx = _quarter_export(qq, monthly, fx)
        no_fx_any |= no_fx
        hist.append((qq, x, m, revenue.get(qq)))
    pts = [
        (x, r)
        for qq, x, m, r in hist
        if qq != target and x is not None and r is not None and m == 3
    ]
    fit = (
        fit_line([float(x) for x, _ in pts], [float(r) for _, r in pts])
        if len(pts) >= cfg.min_points
        else None
    )
    if no_fx_any:
        notes.append("환율이 없는 달은 달러 그대로 셈 — 원화 매출과 단위가 섞인다")
    if fit is None:
        notes.append(f"회귀 점 부족({len(pts)}개 < {cfg.min_points}) — 추정하지 않았다")
    _t, x_t, m_t, r_t = hist[-1]
    if 0 < m_t < 3:
        notes.append(f"{target} 는 {m_t}개월만 — 남은 달은 있는 달 평균으로 채웠다(EST)")
    est = None if fit is None or x_t is None else fit[0] + fit[1] * x_t
    cons, cnote = _consensus(target, annual_consensus, revenue)
    diff = None if est is None or cons is None or cons <= 0 else est / cons - 1.0
    verdict: Verdict | None = None
    if diff is not None:
        verdict = "BEAT" if diff > cfg.band else "MISS" if diff < -cfg.band else "IN LINE"
    r2 = None if fit is None else fit[2]
    conf: Literal["높음", "보통", "낮음"] = (
        "높음"
        if r2 is not None and r2 >= cfg.r2_high
        else "보통"
        if r2 is not None and r2 >= cfg.r2_ok
        else "낮음"
    )
    history = tuple(
        QuarterFit(qq, x, r, None if fit is None or x is None else fit[0] + fit[1] * x, m)
        for qq, x, m, r in hist
    )
    return Preview(
        quarter=target, months_seen=m_t, reported=r_t is not None, export_krw=x_t, revenue_est=est,
        consensus=cons, consensus_note=cnote, diff=diff, verdict=verdict, r2=r2, points=len(pts),
        confidence=conf, history=history, notes=tuple(notes),
    )  # fmt: skip

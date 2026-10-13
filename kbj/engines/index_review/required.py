"""조건 문턱·괴리율·충족·필요 주가 상승률(metrics §24.3) — 정확 십진.

- 문턱: T_m = 배수_m × 컷오프, T_f = 배수_f × 컷오프(원).
- 괴리율: gap_m = 시총 ÷ T_m − 1, gap_f = 유동시총 ÷ T_f − 1(유동시총 = 시총 × 적용 FIF).
- 충족: 시총 ≥ T_m 그리고 유동시총 ≥ T_f.
- 필요 상승률 r: 주가가 (1 + r) 배가 되면(주식 수·FIF 그대로 — 유동시총도 (1 + r) 배) 두 조건을 다
  넘는
  최소값 = max(T_m ÷ 시총, T_f ÷ 유동시총) − 1 을 **1bp(0.0001) 단위로 올림**. 그래서 r 만큼 오르면
  조건을
  넘고, r − 1bp 면 넘지 못한다(역산 검증 — tests/property/test_index_review_props.py).
  FIF 는 적용(반올림·완충 뒤) 값이다 — 유동비율 띠 경계는 주가로 바뀌지 않는다(주식 수 기준).
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Context, Decimal, localcontext
from typing import Final, Literal

__all__ = ["BP", "gap", "meets", "required_rise", "threshold"]

BP: Final = Decimal("0.0001")
_CTX: Final = Context(prec=50)


def _d(x: float | int | Decimal) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x)) if isinstance(x, float) else Decimal(x)


def threshold(mult: float, cutoff_won: int) -> Decimal:
    with localcontext(_CTX):
        return _d(mult) * Decimal(cutoff_won)


def gap(value: Decimal | int, thr: Decimal) -> float:
    """값 ÷ 문턱 − 1(문턱이 0 이하면 오류)."""
    if thr <= 0:
        raise ValueError("문턱은 0 보다 크다")
    with localcontext(_CTX):
        return float(_d(value) / thr - 1)


def ff_value(mcap: int, fif: float, rise: Decimal = Decimal(0)) -> Decimal:
    with localcontext(_CTX):
        return Decimal(mcap) * (1 + rise) * _d(fif)


def meets(
    mcap: int, fif: float | None, thr_m: Decimal, thr_f: Decimal | None, rise: Decimal = Decimal(0)
) -> bool:
    """주가가 (1 + rise) 배일 때 두 조건(유동 문턱이 없으면 시총만)을 다 넘는가."""
    with localcontext(_CTX):
        m = Decimal(mcap) * (1 + rise)
        if m < thr_m:
            return False
        if thr_f is None:
            return True
        if fif is None:
            raise ValueError("유동 문턱이 있으면 유동비율이 있어야 한다")
        return m * _d(fif) >= thr_f


def required_rise(
    mcap: int, fif: float | None, thr_m: Decimal, thr_f: Decimal | None
) -> tuple[Decimal, Literal["mcap", "ff"]]:
    """(필요 상승률 — 1bp 올림, 기준). mcap > 0."""
    if mcap <= 0:
        raise ValueError("시총은 0 보다 크다")
    with localcontext(_CTX):
        need_m = thr_m / Decimal(mcap)
        basis: Literal["mcap", "ff"] = "mcap"
        need = need_m
        if thr_f is not None:
            if fif is None or fif <= 0:
                raise ValueError("유동 문턱이 있으면 유동비율이 있어야 한다")
            need_f = thr_f / (Decimal(mcap) * _d(fif))
            if need_f > need_m:
                need, basis = need_f, "ff"
        r = need - 1
        return (r / BP).to_integral_value(rounding=ROUND_CEILING) * BP, basis

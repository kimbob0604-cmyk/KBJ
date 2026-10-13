"""단가 대 물량 엔진(순수 — 계약 ⑨, docs/p6_design.md §4.3, metrics §27).

- 단가 = 금액 ÷ 중량(달러/kg). 중량 0·None, 금액 None 이면 None(0 으로 채우지 않는다).
- 분해(YoY 기준 항등식): `ln(1+YoY금액) = ln(1+YoY단가) + ln(1+YoY중량)`, 곧
  `ln(v/v₀) = ln(p/p₀) + ln(q/q₀)` (p = v/q). 넷 중 하나라도 없거나 0 이하면 분해 없음(None).
- 중량 기준이 의미 없는 품목(선박 등 — `config/trade.yaml price_volume.weight_unfit` 의 HS 접두)은
  '중량 기준 부적합' 꼬리표(값은 그대로 계산 — 화면이 꼬리표를 붙인다) [확인 필요: 목록].
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

__all__ = ["Decomp", "decompose", "unit_price", "weight_unfit"]

Num = Decimal | int | float | None


def _pos(v: Num) -> float | None:
    if v is None:
        return None
    f = float(v)
    return f if math.isfinite(f) and f > 0 else None


def unit_price(usd: Num, kg: Num) -> float | None:
    """달러/kg. 중량 0·None 또는 금액 None 이면 None. 금액 0 이면 0.0."""
    if usd is None or kg is None:
        return None
    k = float(kg)
    if not math.isfinite(k) or k <= 0:
        return None
    u = float(usd)
    return u / k if math.isfinite(u) and u >= 0 else None


@dataclass(frozen=True)
class Decomp:
    """로그 분해 — ln_value = ln_price + ln_volume(부동소수 오차 안에서, 속성 시험 < 1e-9)."""

    ln_value: float
    ln_price: float
    ln_volume: float


def decompose(usd: Num, kg: Num, usd_prev: Num, kg_prev: Num) -> Decomp | None:
    """올해·전년 같은 달(금액·중량)로 YoY 분해. 하나라도 없거나 0 이하면 None."""
    v, q, v0, q0 = _pos(usd), _pos(kg), _pos(usd_prev), _pos(kg_prev)
    if v is None or q is None or v0 is None or q0 is None:
        return None
    ln_volume = math.log(q) - math.log(q0)
    ln_value = math.log(v) - math.log(v0)
    return Decomp(ln_value=ln_value, ln_price=ln_value - ln_volume, ln_volume=ln_volume)


def weight_unfit(hs: str, prefixes: Iterable[str]) -> bool:
    """그 HS 가 '중량 기준 부적합' 접두 아래인가.

    접두가 HS 보다 길면 HS 가 그 위(부적합 품목이 섞여 있다) — 참."""
    return any(hs.startswith(p) or p.startswith(hs) for p in prefixes)

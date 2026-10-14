"""상품 명세 (PLAN §2.1). 승수·호가단위·결제월은 여기 한 곳에만 둔다 — 다른 모듈은 `spec()`
으로 읽는다.

가격은 `Decimal` 로만 받는다(float 는 `as_decimal` 로 먼저 바꾼다). 틱 반올림 결과는 **결과 가격
자신의 호가단위** 격자 위에 있다. 옵션은 10pt 에서 호가단위가 0.01 → 0.05 로 바뀌므로 경계 근처에서
격자 간격이 달라진다: 9.996 → 10.00, 10.02 → 10.00, 10.03 → 10.05.

- `nearest` 의 정확한 절반은 위로(ROUND_HALF_UP — 가격은 음수가 없어 '0 에서 먼 쪽' = 위)
- `down`·`up` 은 가격 이하 최대·이상 최소 격자점. 격자 위 가격은 그대로
- 결과 자릿수는 틱 자릿수(소수 둘째 자리)로 맞춘다: `Decimal("10")` → `Decimal("10.00")`
- 음수·NaN·무한대는 거부한다. 0 은 격자점이라 0.004 → 0.00 — 주문가 양수 검사는 호출 측(exec) 몫
- 계산은 반올림 없는 정밀 문맥에서 한다. 자릿수가 너무 많아 정확히 못 셀 입력은 `ValueError`
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, NamedTuple, cast

RoundMode = Literal["nearest", "down", "up"]

# 반올림이 일어나면(Inexact) 예외 — 틱 계산은 항상 정확해야 한다
_EXACT = Context(prec=60, traps=[InvalidOperation, DivisionByZero, Overflow, Inexact])


class Product(StrEnum):
    KOSPI200_FUTURES = "kospi200_futures"
    MINI_KOSPI200_FUTURES = "mini_kospi200_futures"
    KOSPI200_OPTIONS = "kospi200_options"  # 월물·위클리(월·목) 공통


class TickTier(NamedTuple):
    """가격이 `floor` 이상일 때 쓰는 호가단위."""

    floor: Decimal
    tick: Decimal


@dataclass(frozen=True)
class Spec:
    product: Product
    name: str
    multiplier: int  # 거래승수, 원/pt
    tiers: tuple[TickTier, ...]  # floor 오름차순, 첫 floor = 0
    settlement_months: frozenset[int]  # 월물 결제월(1~12)

    def __post_init__(self) -> None:
        if self.multiplier <= 0:
            raise ValueError(f"{self.product}: 승수는 양수여야 한다")
        if not self.settlement_months or not self.settlement_months <= frozenset(range(1, 13)):
            raise ValueError(f"{self.product}: 결제월은 1~12 중 하나 이상")
        if not self.tiers or self.tiers[0].floor != 0:
            raise ValueError(f"{self.product}: 호가단위 구간은 0pt 부터 시작해야 한다")
        prev: TickTier | None = None
        for t in self.tiers:
            if not t.tick.is_finite() or t.tick <= 0 or not t.floor.is_finite():
                raise ValueError(f"{self.product}: 잘못된 호가단위 {t}")
            if prev is not None:
                if t.floor <= prev.floor:
                    raise ValueError(f"{self.product}: 구간 경계는 오름차순이어야 한다")
                # 경계가 아래·위 두 격자 모두에 있어야 반올림이 경계를 넘을 때 격자 밖으로 안 나간다
                if t.floor % t.tick != 0 or t.floor % prev.tick != 0:
                    raise ValueError(f"{self.product}: 경계 {t.floor} 가 양쪽 틱의 배수가 아니다")
            prev = t

    def tick_size(self, price: Decimal) -> Decimal:
        """`price` 에서의 호가단위 (pt)."""
        return _tier(self, _check_price(price)).tick

    def tick_value(self, price: Decimal) -> Decimal:
        """`price` 에서 1틱의 가치 (원) = 호가단위 × 승수. 주변 문맥과 무관하게 정확히."""
        with localcontext(_EXACT):
            return self.tick_size(price) * self.multiplier


_SPECS: dict[Product, Spec] = {
    Product.KOSPI200_FUTURES: Spec(
        product=Product.KOSPI200_FUTURES,
        name="코스피200 선물",
        multiplier=250_000,
        tiers=(TickTier(Decimal(0), Decimal("0.05")),),
        settlement_months=frozenset({3, 6, 9, 12}),
    ),
    Product.MINI_KOSPI200_FUTURES: Spec(
        product=Product.MINI_KOSPI200_FUTURES,
        name="미니 코스피200 선물",
        multiplier=50_000,
        tiers=(TickTier(Decimal(0), Decimal("0.02")),),
        settlement_months=frozenset(range(1, 13)),  # 연속월
    ),
    Product.KOSPI200_OPTIONS: Spec(
        product=Product.KOSPI200_OPTIONS,
        name="코스피200 옵션",
        multiplier=250_000,
        # 프리미엄 10pt 미만 0.01, 10pt 이상 0.05
        tiers=(TickTier(Decimal(0), Decimal("0.01")), TickTier(Decimal(10), Decimal("0.05"))),
        settlement_months=frozenset(range(1, 13)),  # 월물. 위클리는 월물리스트 API 가 원천
    ),
}
SPECS: Mapping[Product, Spec] = MappingProxyType(_SPECS)


def spec(product: Product) -> Spec:
    return SPECS[Product(product)]


def as_decimal(value: Decimal | float | str) -> Decimal:
    """float·int·문자열을 `Decimal` 로. float 는 최단 표기(`repr`)로 바꿔 0.1 → Decimal("0.1")."""
    if isinstance(value, bool):
        raise TypeError("bool 은 가격이 아니다")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, float):
        d = Decimal(repr(value))
    elif isinstance(value, int):
        d = Decimal(value)
    else:
        try:
            d = Decimal(value.strip())
        except DecimalException as e:
            raise ValueError(f"숫자가 아니다: {value!r}") from e
    if not d.is_finite():
        raise ValueError(f"유한한 수가 아니다: {value!r}")
    return d


def _check_price(price: Decimal) -> Decimal:
    if not isinstance(cast(object, price), Decimal):
        kind = type(price).__name__
        raise TypeError(f"가격은 Decimal 이어야 한다(float 는 as_decimal 로): {kind}")
    if not price.is_finite():
        raise ValueError(f"유한한 가격이 아니다: {price}")
    if price < 0:
        raise ValueError(f"음수 가격: {price}")
    return price.copy_abs()  # -0 → 0


def _tier(s: Spec, p: Decimal) -> TickTier:
    for t in reversed(s.tiers):
        if p >= t.floor:
            return t
    raise AssertionError("첫 구간 floor 가 0 이라 도달하지 않는다")  # pragma: no cover


def _bracket(s: Spec, p: Decimal) -> tuple[Decimal, Decimal, bool]:
    """(가격 이하 최대 격자점, 이상 최소 격자점, 위쪽이 nearest 인가).

    가격 구간의 틱으로 센다. 아래 격자점은 같은 구간 안(구간 floor 가 그 틱의 배수)이고, 위 격자점은
    많아야 다음 구간 floor 다(floor 가 아래 틱의 배수이고 위 격자에도 있다 — Spec 검증).
    """
    t = _tier(s, p).tick
    try:
        with localcontext(_EXACT):
            q, r = divmod(p, t)
            down = q * t
            up = down if r == 0 else down + t
            upper = r * 2 >= t  # 정확한 절반은 위로
    except DecimalException as e:
        raise ValueError(f"정밀도 밖의 가격: {p}") from e
    return down, up, upper


def round_to_tick(product: Product, price: Decimal, mode: RoundMode = "nearest") -> Decimal:
    """`price` 를 호가 격자에 맞춘다. 결과는 결과 가격 자신의 호가단위 배수다."""
    down, up, upper = _bracket(spec(product), _check_price(price))
    if mode == "down":
        return down
    if mode == "up":
        return up
    if mode == "nearest":
        return up if upper else down
    raise ValueError(f"mode 는 nearest|down|up: {mode!r}")


def is_on_tick(product: Product, price: Decimal) -> bool:
    """`price` 가 자기 호가단위의 배수인가 (옵션 10.01 은 0.05 구간이라 False)."""
    down, up, _ = _bracket(spec(product), _check_price(price))
    return down == up


def ticks_between(product: Product, a: Decimal, b: Decimal) -> Decimal:
    """`(b − a) / tick(a)` — `a` 의 호가단위로 센 틱 수. b < a 면 음수.

    호가 스프레드(metrics §1.1)는 `ticks_between(옵션, bid, ask)`: 10pt 경계를 걸쳐도 bid 의 틱으로
    센다 [확인 필요]. 격자점 개수가 아니다(9.98 → 10.05 는 bid 틱 0.01 로 7).
    """
    t = spec(product).tick_size(a)
    b = _check_price(b)
    try:
        with localcontext(_EXACT):
            return (b - a) / t
    except DecimalException as e:
        raise ValueError(f"정밀도 밖의 가격: {a}, {b}") from e


def abs_diff(a: Decimal, b: Decimal) -> Decimal:
    """`|a − b|` 를 반올림 없이 — 주변 decimal 문맥(기본 28자리, 호출 측이 좁힐 수도)과 무관.

    ATM 동률·거리순(core.chain)이 문맥 반올림으로 가짜 동률을 만들지 않게 한다. 정확히 못 셀 만큼
    자릿수가 많으면 `ValueError`.
    """
    try:
        with localcontext(_EXACT):
            return abs(a - b)
    except DecimalException as e:
        raise ValueError(f"정밀도 밖의 차이: {a}, {b}") from e


def option_tick(premium: Decimal) -> Decimal:
    """옵션 프리미엄의 호가단위: 10pt 이상 0.05, 미만 0.01."""
    return SPECS[Product.KOSPI200_OPTIONS].tick_size(premium)

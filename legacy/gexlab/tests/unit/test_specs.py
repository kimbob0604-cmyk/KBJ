from decimal import Decimal as D
from decimal import localcontext

import pytest

from core.specs import (
    SPECS,
    Product,
    Spec,
    TickTier,
    abs_diff,
    as_decimal,
    is_on_tick,
    option_tick,
    round_to_tick,
    spec,
    ticks_between,
)

FUT = Product.KOSPI200_FUTURES
MINI = Product.MINI_KOSPI200_FUTURES
OPT = Product.KOSPI200_OPTIONS


# --- CLAUDE.md / PLAN §2.1 표 값 고정 ---


def test_every_product_has_a_spec() -> None:
    assert set(SPECS) == set(Product)
    assert all(spec(p).product is p for p in Product)


@pytest.mark.parametrize(("product", "mult"), [(FUT, 250_000), (MINI, 50_000), (OPT, 250_000)])
def test_multipliers(product: Product, mult: int) -> None:
    assert spec(product).multiplier == mult


@pytest.mark.parametrize(
    ("product", "price", "tick", "value"),
    [
        (FUT, "1098.35", "0.05", 12_500),
        (FUT, "0", "0.05", 12_500),
        (MINI, "1098.36", "0.02", 1_000),
        (OPT, "0.01", "0.01", 2_500),
        (OPT, "9.99", "0.01", 2_500),
        (OPT, "9.999", "0.01", 2_500),
        (OPT, "10", "0.05", 12_500),  # 10pt 이상 → 0.05
        (OPT, "10.00", "0.05", 12_500),
        (OPT, "35.5", "0.05", 12_500),
    ],
)
def test_tick_size_and_value(product: Product, price: str, tick: str, value: int) -> None:
    s = spec(product)
    assert s.tick_size(D(price)) == D(tick)
    assert s.tick_value(D(price)) == value
    assert isinstance(s.tick_size(D(price)), D)


def test_tick_value_ignores_narrowed_context() -> None:
    # 호출 측이 전역 문맥을 2자리로 좁혀도 12,500 이 1.2E+4(=12,000)로 뭉개지지 않는다
    with localcontext() as ctx:
        ctx.prec = 2
        assert spec(OPT).tick_value(D("10")) == 12_500
        assert spec(MINI).tick_value(D("1098.36")) == 1_000


def test_option_tick_helper() -> None:
    assert option_tick(D("9.99")) == D("0.01")
    assert option_tick(D("10")) == D("0.05")


def test_settlement_months() -> None:
    assert spec(FUT).settlement_months == {3, 6, 9, 12}
    assert spec(MINI).settlement_months == set(range(1, 13))
    # 옵션 월물은 매월(비분기월 202610·202611 월물 상장 — probe_results #11·#12b). 분기월 판정
    # (metrics §1.3)은 선물 결제월로 한다. 위클리 만기는 KIS 월물리스트 API 가 원천이라 여기 없다
    assert spec(OPT).settlement_months == set(range(1, 13))


def test_spec_accepts_product_value_string() -> None:
    assert spec(Product("kospi200_options")) is SPECS[OPT]
    assert str(OPT) == "kospi200_options"


# --- 틱 반올림: 옵션 10pt 경계 ---


@pytest.mark.parametrize(
    ("price", "nearest", "down", "up"),
    [
        ("9.996", "10.00", "9.99", "10.00"),  # 아래 구간 가격이 경계로 올라감
        ("9.995", "10.00", "9.99", "10.00"),  # 정확한 절반 → 위
        ("9.994", "9.99", "9.99", "10.00"),
        ("10.02", "10.00", "10.00", "10.05"),  # 위 구간: 0.05 격자
        ("10.025", "10.05", "10.00", "10.05"),  # 정확한 절반 → 위
        ("10.03", "10.05", "10.00", "10.05"),
        ("10.001", "10.00", "10.00", "10.05"),
        ("9.999", "10.00", "9.99", "10.00"),
        ("10", "10.00", "10.00", "10.00"),
        ("10.05", "10.05", "10.05", "10.05"),
        ("0.004", "0.00", "0.00", "0.01"),
        ("0.005", "0.01", "0.00", "0.01"),
        ("1.234", "1.23", "1.23", "1.24"),
    ],
)
def test_option_rounding_modes(price: str, nearest: str, down: str, up: str) -> None:
    p = D(price)
    got = (
        round_to_tick(OPT, p),
        round_to_tick(OPT, p, "down"),
        round_to_tick(OPT, p, "up"),
    )
    # str 비교로 값뿐 아니라 자릿수(소수 둘째 자리)까지 고정
    assert tuple(str(x) for x in got) == (nearest, down, up)
    assert all(is_on_tick(OPT, x) for x in got)


@pytest.mark.parametrize(
    ("product", "price", "nearest", "down", "up"),
    [
        (FUT, "1098.37", "1098.35", "1098.35", "1098.40"),
        (FUT, "1098.375", "1098.40", "1098.35", "1098.40"),  # 절반 → 위
        (FUT, "1098.425", "1098.45", "1098.40", "1098.45"),
        (FUT, "1098.4", "1098.40", "1098.40", "1098.40"),
        (MINI, "1098.37", "1098.38", "1098.36", "1098.38"),  # 0.02 격자의 절반 → 위
        (MINI, "1098.369", "1098.36", "1098.36", "1098.38"),
        (MINI, "1098.371", "1098.38", "1098.36", "1098.38"),
    ],
)
def test_futures_rounding_modes(
    product: Product, price: str, nearest: str, down: str, up: str
) -> None:
    p = D(price)
    assert str(round_to_tick(product, p)) == nearest
    assert str(round_to_tick(product, p, "down")) == down
    assert str(round_to_tick(product, p, "up")) == up


def test_rounding_is_exact_decimal() -> None:
    # float 였으면 0.1+0.2 류 오차가 생길 자리
    r = round_to_tick(OPT, D("0.30000000000000004"))
    assert r == D("0.30") and isinstance(r, D)
    assert round_to_tick(FUT, D("1234.55")) == D("1234.55")
    assert str(round_to_tick(FUT, D("1E+3"))) == "1000.00"


def test_negative_zero_becomes_zero() -> None:
    r = round_to_tick(OPT, D("-0"))
    assert r == 0 and not r.is_signed()


@pytest.mark.parametrize("bad", ["NaN", "sNaN", "Infinity", "-Infinity", "-0.01", "-10"])
def test_rejects_non_finite_and_negative(bad: str) -> None:
    with pytest.raises(ValueError):
        round_to_tick(OPT, D(bad))
    with pytest.raises(ValueError):
        spec(OPT).tick_size(D(bad))
    with pytest.raises(ValueError):
        is_on_tick(FUT, D(bad))


def test_rejects_float_and_int() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        round_to_tick(OPT, 9.99)  # pyright: ignore[reportArgumentType]
    with pytest.raises(TypeError):
        spec(FUT).tick_size(1098)  # pyright: ignore[reportArgumentType]


def test_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        round_to_tick(OPT, D("1.23"), "ceil")  # pyright: ignore[reportArgumentType]


def test_rejects_too_many_digits() -> None:
    with pytest.raises(ValueError, match="정밀도"):
        round_to_tick(OPT, D("1" * 70))


def test_abs_diff_is_exact_regardless_of_context() -> None:
    a, b = D("1100"), D("1098.7500000000000000000000000001")  # 유효숫자 32 > 기본 문맥 28
    assert abs_diff(a, b) == D("1.2499999999999999999999999999")
    assert abs_diff(b, a) == abs_diff(a, b)
    with localcontext() as ctx:
        ctx.prec = 2  # 호출 측이 좁혀도
        assert abs_diff(D("1098.751"), D("1100")) == D("1.249")
    with pytest.raises(ValueError, match="정밀도"):
        abs_diff(D("1" * 70), D("0.1"))


# --- 격자 판정·틱 수 ---


@pytest.mark.parametrize(
    ("product", "price", "on"),
    [
        (OPT, "9.99", True),
        (OPT, "10.00", True),
        (OPT, "10.01", False),  # 10pt 이상은 0.05 배수만
        (OPT, "10.05", True),
        (OPT, "0", True),
        (FUT, "1098.35", True),
        (FUT, "1098.36", False),
        (MINI, "1098.36", True),
        (MINI, "1098.35", False),
    ],
)
def test_is_on_tick(product: Product, price: str, on: bool) -> None:
    assert is_on_tick(product, D(price)) is on


@pytest.mark.parametrize(
    ("bid", "ask", "ticks"),
    [
        ("1.20", "1.23", "3"),
        ("10.00", "10.15", "3"),
        ("9.97", "10.00", "3"),
        ("9.98", "10.05", "7"),  # 경계를 걸치면 bid 틱(0.01)으로 센다 — metrics §1.1
        ("10.05", "10.05", "0"),
        ("1.25", "1.20", "-5"),  # 역전 호가는 음수
    ],
)
def test_ticks_between_uses_bid_tick(bid: str, ask: str, ticks: str) -> None:
    assert ticks_between(OPT, D(bid), D(ask)) == D(ticks)


def test_ticks_between_futures() -> None:
    assert ticks_between(FUT, D("1098.35"), D("1098.50")) == 3
    assert ticks_between(MINI, D("1098.36"), D("1098.42")) == 3


def test_ticks_between_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        ticks_between(OPT, D("1.00"), D("NaN"))
    with pytest.raises(ValueError):
        ticks_between(OPT, D("-1.00"), D("1.00"))


# --- 변환 ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.1, "0.1"), (1098.35, "1098.35"), (10, "10"), (" 9.99 ", "9.99"), (D("1.5"), "1.5")],
)
def test_as_decimal(value: D | float | str, expected: str) -> None:
    assert as_decimal(value) == D(expected)
    assert str(as_decimal(0.1)) == "0.1"  # 이진 전개(0.1000000000000000055…)가 아니다


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "abc", "", "NaN", D("Infinity")])
def test_as_decimal_rejects_non_finite(bad: D | float | str) -> None:
    with pytest.raises(ValueError):
        as_decimal(bad)


def test_as_decimal_rejects_bool() -> None:
    with pytest.raises(TypeError):
        as_decimal(True)


# --- Spec 자체 검증 ---


def _spec(tiers: tuple[TickTier, ...], mult: int = 1, months: frozenset[int] | None = None) -> Spec:
    return Spec(
        product=OPT,
        name="t",
        multiplier=mult,
        tiers=tiers,
        settlement_months=frozenset({1}) if months is None else months,
    )


def test_spec_validation() -> None:
    ok = TickTier(D(0), D("0.01"))
    with pytest.raises(ValueError, match="승수"):
        _spec((ok,), mult=0)
    with pytest.raises(ValueError, match="결제월"):
        _spec((ok,), months=frozenset({13}))
    with pytest.raises(ValueError, match="결제월"):
        _spec((ok,), months=frozenset())
    with pytest.raises(ValueError, match="0pt"):
        _spec(())
    with pytest.raises(ValueError, match="0pt"):
        _spec((TickTier(D(1), D("0.01")),))
    with pytest.raises(ValueError, match="호가단위"):
        _spec((TickTier(D(0), D(0)),))
    with pytest.raises(ValueError, match="오름차순"):
        _spec((ok, TickTier(D(0), D("0.05"))))
    # 경계 10.02 는 0.05 의 배수가 아니다 → 위 구간 격자 밖
    with pytest.raises(ValueError, match="배수"):
        _spec((ok, TickTier(D("10.02"), D("0.05"))))
    # 경계 10.005 는 아래 틱 0.01 의 배수가 아니다
    with pytest.raises(ValueError, match="배수"):
        _spec((ok, TickTier(D("10.005"), D("0.005"))))


def test_spec_is_immutable() -> None:
    with pytest.raises(AttributeError):
        spec(OPT).multiplier = 1  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(TypeError):
        SPECS[OPT] = SPECS[FUT]  # pyright: ignore[reportIndexIssue]

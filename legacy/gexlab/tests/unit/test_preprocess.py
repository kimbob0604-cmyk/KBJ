"""core.preprocess — §1.1 가격 선택, §1.2 제외 OI 비율, §0 품질 합성."""

from decimal import Decimal, localcontext

import pytest

from core.preprocess import (
    NO_PRICE,
    PriceChoice,
    Quality,
    excluded_oi_ratio,
    select_price,
    worst,
)

D = Decimal


# --- §1.1 스프레드 3틱 경계 ---


@pytest.mark.parametrize(
    ("bid", "ask", "mid"),
    [
        ("1.00", "1.03", "1.015"),  # 10pt 미만 0.01 틱 × 3
        ("1.00", "1.00", "1.00"),  # 잠긴 호가(스프레드 0)
        ("10.00", "10.15", "10.075"),  # 10pt 이상 0.05 틱 × 3
        ("9.97", "10.00", "9.985"),  # 경계를 걸침 — bid 틱 0.01 로 3틱
    ],
)
def test_spread_within_three_ticks_is_mid(bid: str, ask: str, mid: str) -> None:
    assert select_price(D(bid), D(ask), D("0.5")) == PriceChoice(D(mid), "mid")


@pytest.mark.parametrize(
    ("bid", "ask"),
    [
        ("1.00", "1.04"),  # 3틱 + 0.01
        ("10.00", "10.20"),  # 4틱(0.05)
        ("9.96", "10.00"),  # bid 틱 0.01 로 4틱
        ("9.98", "10.05"),  # bid 틱으로 7틱(ask 틱 0.05 로 세면 1.4틱이라 mid 였을 것)
    ],
)
def test_spread_over_three_ticks_is_last(bid: str, ask: str) -> None:
    assert select_price(D(bid), D(ask), D("1.23")) == PriceChoice(D("1.23"), "last")


def test_wide_spread_without_last_has_no_price() -> None:
    assert select_price(D("1.00"), D("1.04"), None) is NO_PRICE


# --- 호가·last 없음 ---


@pytest.mark.parametrize(
    ("bid", "ask"),
    [
        (None, D("1.02")),
        (D("1.00"), None),
        (D(0), D("1.02")),  # KIS 는 없음을 0 으로 준다
        (D("1.00"), D("0.00")),
        (None, None),
    ],
)
def test_one_sided_quote_uses_last(bid: Decimal | None, ask: Decimal | None) -> None:
    assert select_price(bid, ask, D("1.01")) == PriceChoice(D("1.01"), "last")
    assert select_price(bid, ask, None) is NO_PRICE
    assert select_price(bid, ask, D(0)) is NO_PRICE


def test_crossed_quote_is_not_a_quote() -> None:
    # ask < bid 는 호가 없음으로 본다 [확인 필요]
    assert select_price(D("1.05"), D("1.03"), D("1.04")) == PriceChoice(D("1.04"), "last")
    assert select_price(D("1.05"), D("1.03"), None) is NO_PRICE


def test_mid_is_exact_in_narrow_context() -> None:
    with localcontext() as ctx:
        ctx.prec = 3
        choice = select_price(D("12.35"), D("12.40"), None)
    assert choice.price == D("12.375")


def test_rejects_float_and_bad_values() -> None:
    with pytest.raises(TypeError):
        select_price(1.0, D("1.01"), None)  # pyright: ignore[reportArgumentType]
    for bad in (D("-0.01"), D("NaN"), D("Infinity")):
        with pytest.raises(ValueError):
            select_price(None, None, bad)


def test_price_choice_invariants() -> None:
    with pytest.raises(ValueError):
        PriceChoice(D(1), None)
    with pytest.raises(ValueError):
        PriceChoice(None, "mid")
    with pytest.raises(ValueError):
        PriceChoice(D(1), "bid")  # pyright: ignore[reportArgumentType]
    # 전 세션 표시는 last 에만
    with pytest.raises(ValueError):
        PriceChoice(D(1), "mid", prev_session=True)
    with pytest.raises(ValueError):
        PriceChoice(None, None, prev_session=True)


# --- §1.1 전 세션 last (당일 거래량 0) [확인 필요] ---


def test_last_without_volume_today_is_prev_session() -> None:
    wide = (D("1.00"), D("1.10"))  # 10틱 → last
    assert select_price(*wide, D("1.23"), 0) == PriceChoice(D("1.23"), "last", prev_session=True)
    assert select_price(None, None, D("1.23"), volume=0).prev_session  # 보강 행(호가 없음)
    # 당일 거래가 있거나 거래량을 모르면 지금 동작 그대로
    assert select_price(*wide, D("1.23"), 3) == PriceChoice(D("1.23"), "last")
    assert select_price(*wide, D("1.23")) == PriceChoice(D("1.23"), "last")


def test_mid_is_never_prev_session() -> None:
    # 호가는 살아 있는 값이라 거래량 0 이어도 전 세션 가격이 아니다
    assert select_price(D("1.00"), D("1.02"), D("1.50"), 0) == PriceChoice(D("1.01"), "mid")
    assert select_price(None, None, None, 0) is NO_PRICE


@pytest.mark.parametrize(
    ("volume", "error"),
    [(-1, ValueError), (True, TypeError), (1.0, TypeError), ("0", TypeError)],
)
def test_rejects_bad_volume(volume: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        select_price(None, None, D("1.00"), volume)  # pyright: ignore[reportArgumentType]


# --- §1.2 제외 OI 비율 ---


@pytest.mark.parametrize(
    ("excluded", "total", "ratio"),
    [(0, 0, 0.0), (0, 500, 0.0), (10, 100, 0.1), (100, 100, 1.0), (1, 3, 1 / 3)],
)
def test_excluded_oi_ratio(excluded: int, total: int, ratio: float) -> None:
    assert excluded_oi_ratio(excluded, total) == ratio


@pytest.mark.parametrize(("excluded", "total"), [(-1, 10), (11, 10), (1, 0)])
def test_excluded_oi_ratio_rejects_inconsistent(excluded: int, total: int) -> None:
    with pytest.raises(ValueError):
        excluded_oi_ratio(excluded, total)


# --- §0 품질 합성 ---


@pytest.mark.parametrize(
    ("qs", "expected"),
    [
        ((), "ok"),
        (("ok", "ok"), "ok"),
        (("ok", "stale"), "stale"),
        (("stale", "estimated", "ok"), "estimated"),
        (("estimated", "invalid", "stale"), "invalid"),
    ],
)
def test_worst(qs: tuple[Quality, ...], expected: Quality) -> None:
    assert worst(*qs) == expected


def test_worst_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        worst("ok", "bad")  # pyright: ignore[reportArgumentType]

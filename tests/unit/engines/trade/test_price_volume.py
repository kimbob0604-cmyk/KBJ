"""단가 대 물량(metrics §27 — 묶음 EXP): 단가(중량 0·None → None)·로그 분해·중량 부적합 꼬리표."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from kbj.engines.trade.price_volume import decompose, unit_price, weight_unfit

D = Decimal


def test_단가() -> None:
    assert unit_price(D(100), D(4)) == 25.0
    assert unit_price(D(0), D(4)) == 0.0
    assert unit_price(D(100), D(0)) is None
    assert unit_price(D(100), None) is None and unit_price(None, D(1)) is None


def test_분해_항등식과_값() -> None:
    d = decompose(D(200), D(50), D(100), D(100))  # 금액 2배, 중량 절반 → 단가 4배
    assert d is not None
    assert d.ln_value == pytest.approx(math.log(2))
    assert d.ln_volume == pytest.approx(math.log(0.5))
    assert d.ln_price == pytest.approx(math.log(4))
    assert d.ln_price + d.ln_volume == pytest.approx(d.ln_value, abs=1e-12)


@pytest.mark.parametrize(
    "args",
    [(None, 1, 1, 1), (1, 0, 1, 1), (1, 1, 0, 1), (1, 1, 1, None), (-1, 1, 1, 1)],
)
def test_분해_없음(args: tuple[int | None, ...]) -> None:
    assert decompose(*args) is None


def test_중량_부적합() -> None:
    assert weight_unfit("8901", ["89"]) and weight_unfit("89", ["8901"])
    assert not weight_unfit("8501", ["89"]) and not weight_unfit("8901", [])

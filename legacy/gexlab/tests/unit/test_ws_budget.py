import pytest
from hypothesis import given
from hypothesis import strategies as st

from services.ws_gateway.budget import BudgetConfig, SessionBudgetConfig, derive, load


def test_config_file_gives_plan_budget() -> None:
    cfg = load()
    day, night = derive(cfg, "day"), derive(cfg, "night")
    # 주간: 선물 1 + 체결통보 1 + 옵션 38(ATM±9) + 여유 1
    assert (day.futures, day.notices, day.atm_range, day.options, day.spare) == (1, 1, 9, 38, 1)
    # 야간: 선물 1 + 체결통보 2 + 옵션 34(ATM±8) + 여유 4 — PLAN 의 36 은 계산 오류였다
    assert (night.futures, night.notices, night.atm_range, night.options, night.spare) == (
        1,
        2,
        8,
        34,
        4,
    )
    assert day.total == night.total == cfg.limit == 41


def test_too_small_budget_raises() -> None:
    cfg = BudgetConfig(
        limit=4,
        day=SessionBudgetConfig(futures=1, notices=1, min_spare=1),
        night=SessionBudgetConfig(futures=1, notices=1, min_spare=1),
    )
    with pytest.raises(ValueError, match="옵션에 쓸 구독"):
        derive(cfg, "day")


@given(
    limit=st.integers(min_value=2, max_value=200),
    futures=st.integers(min_value=0, max_value=5),
    notices=st.integers(min_value=0, max_value=5),
    min_spare=st.integers(min_value=0, max_value=5),
)
def test_derived_budget_is_maximal_symmetric_and_fits(
    limit: int, futures: int, notices: int, min_spare: int
) -> None:
    s = SessionBudgetConfig(futures=futures, notices=notices, min_spare=min_spare)
    cfg = BudgetConfig(limit=limit, day=s, night=s)
    avail = limit - futures - notices - min_spare
    if avail < 2:
        with pytest.raises(ValueError):
            derive(cfg, "day")
        return
    b = derive(cfg, "day")
    assert b.options == (2 * b.atm_range + 1) * 2  # ATM 양쪽 같은 수, 콜·풋 짝
    assert b.options <= avail  # 최소 여유를 지킨다
    assert (2 * (b.atm_range + 1) + 1) * 2 > avail  # 한 칸 더 넓히면 넘친다
    assert b.total == limit
    assert b.spare >= min_spare

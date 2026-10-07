from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from services.ws_gateway.budget import derive, load
from services.ws_gateway.subscriptions import (
    StrikeCodes,
    Subscription,
    changes,
    plan,
    should_rotate,
)


def chain(n: int = 60, start: str = "1000.0") -> list[StrikeCodes]:
    k0 = Decimal(start)
    return [StrikeCodes(k0 + Decimal("2.5") * i, f"BC{i:03d}", f"BP{i:03d}") for i in range(n)]


def test_day_plan_uses_day_trs_and_full_budget() -> None:
    b = derive(load(), "day")
    subs = plan(b, "A01612", chain(), atm_index=30)
    assert len(subs) == 1 + 38  # 선물 1 + ATM±9 × 콜·풋
    assert Subscription("H0IFCNT0", "A01612") in subs
    assert {s.tr_id for s in subs} == {"H0IFCNT0", "H0IOCNT0"}
    assert Subscription("H0IOCNT0", "BC021") in subs and Subscription("H0IOCNT0", "BP039") in subs
    assert Subscription("H0IOCNT0", "BC020") not in subs  # ATM-10 은 밖


def test_night_plan_uses_night_trs() -> None:
    b = derive(load(), "night")
    subs = plan(b, "A01612", chain(), atm_index=30)
    assert len(subs) == 1 + 34  # 선물 1 + ATM±8 × 콜·풋
    assert {s.tr_id for s in subs} == {"H0MFCNT0", "H0EUCNT0"}


def test_plan_clips_at_chain_edge() -> None:
    b = derive(load(), "day")
    subs = plan(b, "A01612", chain(), atm_index=2)
    assert len(subs) == 1 + (2 + 1 + 9) * 2  # 아래쪽은 2개뿐


@pytest.mark.parametrize(
    ("bad_chain", "atm"),
    [([], 0), (chain(5), 5), (list(reversed(chain(5))), 2)],
)
def test_plan_rejects_bad_input(bad_chain: list[StrikeCodes], atm: int) -> None:
    with pytest.raises(ValueError):
        plan(derive(load(), "day"), "A01612", bad_chain, atm)


def test_rotate_only_when_atm_moves_two_strikes() -> None:
    c = chain()
    k = [sc.strike for sc in c]
    assert should_rotate(None, k[30], c)
    assert not should_rotate(k[30], k[31], c)
    assert should_rotate(k[30], k[32], c)
    assert should_rotate(k[30], k[28], c)
    assert should_rotate(Decimal("1.0"), k[30], c)  # 기존 중심이 체인에 없음(만기 전환)


def test_changes_unsubscribe_first() -> None:
    b = derive(load(), "day")
    c = chain()
    cur, new = plan(b, "A01612", c, 30), plan(b, "A01612", c, 33)
    steps = changes(cur, new)
    kinds = [a for a, _ in steps]
    assert kinds == sorted(kinds, key=lambda a: a != "unsubscribe")  # 해지가 모두 먼저
    assert len(steps) == 12  # 한쪽 3행사가 × 콜·풋 해지 + 반대쪽 등록
    assert changes(cur, cur) == []


def test_session_switch_replaces_everything() -> None:
    c = chain()
    day = plan(derive(load(), "day"), "A01612", c, 30)
    night = plan(derive(load(), "night"), "A01612", c, 30)
    steps = changes(day, night)
    assert sum(a == "unsubscribe" for a, _ in steps) == len(day)
    assert sum(a == "subscribe" for a, _ in steps) == len(night)


@given(
    atm_a=st.integers(min_value=0, max_value=59),
    atm_b=st.integers(min_value=0, max_value=59),
    session=st.sampled_from(["day", "night"]),
)
def test_count_never_exceeds_limit_during_changes(atm_a: int, atm_b: int, session: str) -> None:
    b = derive(load(), session)  # type: ignore[arg-type]
    c = chain()
    cur = set(plan(b, "A01612", c, atm_a))
    for action, s in changes(frozenset(cur), plan(b, "A01612", c, atm_b)):
        if action == "unsubscribe":
            cur.remove(s)
        else:
            cur.add(s)
        assert len(cur) + b.notices <= b.limit

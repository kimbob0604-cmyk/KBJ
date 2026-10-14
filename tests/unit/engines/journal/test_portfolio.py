"""포지션 엔진 — 이동평균 단가·실현 손익·음수 보유 거절·월별(docs/p8_design.md §4.7, ADR 0033).
손계산 표."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

import pytest

from kbj.engines.journal.portfolio import (
    NegativeHolding,
    close_on,
    monthly,
    replay,
    to_won,
    unrealized,
)

D = Decimal
ZERO: Final = Decimal(0)


@dataclass(frozen=True)
class T:
    id: int | None
    code: str
    side: str
    trade_date: date
    price: Decimal
    qty: int
    fee: Decimal = ZERO
    tax: Decimal = ZERO


A = "990001"
B = "990002"


def test_average_cost_and_realized_by_hand() -> None:
    trades = [
        T(1, A, "buy", date(2026, 9, 1), D(10000), 10, fee=D(15)),
        T(2, A, "buy", date(2026, 9, 2), D(12000), 10, fee=D(15)),
        T(3, A, "sell", date(2026, 9, 10), D(13000), 5, fee=D(10), tax=D(130)),
    ]
    book = replay(trades)
    lot = book.lots[A]
    # 원가 = 100000+15 + 120000+15 = 220030, 평균 = 11001.5
    # 실현 = (13000 − 11001.5) × 5 − 10 − 130 = 9852.5
    assert book.events[0].pnl == D("9852.5")
    assert book.events[0].hold_days == 9
    assert lot.qty == 15
    assert lot.avg_cost == D("11001.5")
    assert to_won(book.realized_total) == 9853
    u = unrealized(lot, 12000.0)
    assert u == (D(12000) - D("11001.5")) * 15


def test_full_exit_resets_opened_on_and_cost() -> None:
    trades = [
        T(1, A, "buy", date(2026, 9, 1), D(100), 3),
        T(2, A, "sell", date(2026, 9, 3), D(110), 3),
        T(3, A, "buy", date(2026, 9, 7), D(90), 1),
        T(4, A, "sell", date(2026, 9, 9), D(95), 1),
    ]
    book = replay(trades)
    assert [e.hold_days for e in book.events] == [2, 2]
    assert book.lots[A].qty == 0 and book.lots[A].cost == 0
    assert book.realized_total == D(30) + D(5)
    assert unrealized(book.lots[A], None) == 0  # 보유 없으면 평가 0


def test_sell_more_than_held_is_rejected() -> None:
    trades = [
        T(1, A, "buy", date(2026, 9, 1), D(100), 3),
        T(2, A, "sell", date(2026, 9, 2), D(100), 4),
    ]
    with pytest.raises(NegativeHolding) as e:
        replay(trades)
    assert e.value.code == A
    assert "4" not in str(e.value).replace("2026-09-02", "")  # 수량을 문구에 넣지 않는다


def test_sell_without_buy_rejected_and_same_day_order_by_id() -> None:
    with pytest.raises(NegativeHolding):
        replay([T(1, B, "sell", date(2026, 9, 1), D(10), 1)])
    # 같은 날 매수(id 2)·매도(id 3) — id 순서로 적용되면 통과
    ok = [
        T(3, A, "sell", date(2026, 9, 1), D(10), 1),
        T(2, A, "buy", date(2026, 9, 1), D(9), 1),
    ]
    assert replay(ok).realized_total == D(1)


def test_unknown_close_gives_none() -> None:
    book = replay([T(1, A, "buy", date(2026, 9, 1), D(100), 1)])
    assert unrealized(book.lots[A], None) is None


def test_close_on_picks_latest_not_after() -> None:
    s = [(date(2026, 9, 1), 1.0), (date(2026, 9, 3), 3.0)]
    assert close_on(s, date(2026, 8, 31)) is None
    assert close_on(s, date(2026, 9, 2)) == (date(2026, 9, 1), 1.0)
    assert close_on(s, date(2026, 9, 30)) == (date(2026, 9, 3), 3.0)


def test_monthly_by_hand() -> None:
    trades = [
        T(1, A, "buy", date(2026, 8, 10), D(100), 10),  # 8월 매수 1000
        T(2, A, "sell", date(2026, 9, 15), D(120), 5),  # 9월 실현 (120−100)×5 = 100
    ]
    closes = {A: [(date(2026, 8, 31), 110.0), (date(2026, 9, 30), 130.0)]}
    ms = monthly(trades, closes, asof=date(2026, 9, 30))
    assert [m.month for m in ms] == ["2026-08", "2026-09"]
    aug, sep = ms
    # 8월: 평가 (110−100)×10 = 100, 변화 100, 분모 = 월초 0 + 매수 1000 → 10%
    assert aug.realized == 0 and aug.unrealized_change == 100 and aug.ret_pct == pytest.approx(10.0)
    # 9월: 월말 평가 (130−100)×5 = 150, 변화 50, 실현 100, 분모 = 월초 원가 1000 → 15%
    assert sep.realized == 100 and sep.unrealized_change == 50
    assert sep.ret_pct == pytest.approx(15.0)


def test_monthly_missing_close_is_none_and_empty() -> None:
    trades = [T(1, A, "buy", date(2026, 9, 1), D(100), 1)]
    ms = monthly(trades, {}, asof=date(2026, 9, 30))
    assert ms[0].unrealized_change is None and ms[0].ret_pct is None
    assert monthly([], {}, asof=date(2026, 9, 30)) == []
    # 기준일 뒤 매매는 보지 않는다
    assert monthly(trades, {}, asof=date(2026, 8, 31)) == []

"""쿨다운 상태기(D-P8-10·metrics §30.3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from kbj.core.time import KST
from kbj.engines.rules.cooldown import CooldownBook, allow

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=KST)


@dataclass
class S:
    rule_id: str
    code: str
    eval_ts: datetime
    status: str


def test_window_boundary_inclusive() -> None:
    prior = [S("r_a", "005930", T0, "sent")]
    assert not allow(
        prior, rule_id="r_a", code="005930", ts=T0 + timedelta(minutes=59), cooldown_min=60
    ).allowed
    d = allow(prior, rule_id="r_a", code="005930", ts=T0 + timedelta(minutes=60), cooldown_min=60)
    assert d.allowed and d.last_ts == T0


def test_only_allowed_statuses_and_same_key() -> None:
    later = T0 + timedelta(minutes=10)
    for st in ("cooldown", "skipped"):
        assert allow(
            [S("r_a", "005930", T0, st)], rule_id="r_a", code="005930", ts=later, cooldown_min=60
        ).allowed
    for st in ("sent", "suppressed", "dry"):
        assert not allow(
            [S("r_a", "005930", T0, st)], rule_id="r_a", code="005930", ts=later, cooldown_min=60
        ).allowed
    assert allow(
        [S("r_b", "005930", T0, "sent")], rule_id="r_a", code="005930", ts=later, cooldown_min=60
    ).allowed
    assert allow(
        [S("r_a", "000660", T0, "sent")], rule_id="r_a", code="005930", ts=later, cooldown_min=60
    ).allowed


def test_future_signals_ignored() -> None:
    prior = [S("r_a", "005930", T0 + timedelta(hours=1), "sent")]
    assert allow(prior, rule_id="r_a", code="005930", ts=T0, cooldown_min=1440).allowed


def test_book_sliding_window_from_last_allowed() -> None:
    book = CooldownBook()
    got = [
        book.step(
            rule_id="r_a", code="005930", ts=T0 + timedelta(minutes=m), cooldown_min=60
        ).allowed
        for m in (0, 30, 60, 70, 130)
    ]
    assert got == [True, False, True, False, True]


def test_bad_args() -> None:
    with pytest.raises(ValueError):
        allow([], rule_id="r_a", code="005930", ts=T0, cooldown_min=0)
    with pytest.raises(ValueError):
        allow([], rule_id="r_a", code="005930", ts=T0.replace(tzinfo=None), cooldown_min=60)

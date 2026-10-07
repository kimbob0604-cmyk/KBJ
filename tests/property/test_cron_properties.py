"""kbj/core/cron.py 속성 — next_after 는 맞는 분이고, 그 사이에 맞는 분이 없다(설계 §1.7)."""

from __future__ import annotations

from datetime import datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.core.cron import CronSpec


def _field(lo: int, hi: int) -> st.SearchStrategy[str]:
    single = st.integers(lo, hi).map(str)
    rng = st.tuples(st.integers(lo, hi), st.integers(lo, hi)).map(lambda t: f"{min(t)}-{max(t)}")
    step = st.tuples(st.sampled_from(["*", f"{lo}-{hi}"]), st.integers(1, hi - lo + 1)).map(
        lambda t: f"{t[0]}/{t[1]}"
    )
    item = st.one_of(st.just("*"), single, rng, step)
    return st.lists(item, min_size=1, max_size=3).map(",".join)


exprs = st.tuples(
    _field(0, 59),
    _field(0, 23),
    st.one_of(st.just("*"), _field(1, 28)),
    _field(1, 12),
    _field(0, 6),
).map(" ".join)
_LO = datetime(2020, 1, 1)  # noqa: DTZ001 — 벽시계
_HI = datetime(2040, 12, 31)  # noqa: DTZ001
moments = st.datetimes(min_value=_LO, max_value=_HI)


@settings(max_examples=150, deadline=None)
@given(exprs, moments)
def test_next_after_matches_and_nothing_in_between(expr: str, start: datetime) -> None:
    spec = CronSpec.parse(expr)
    nxt = spec.next_after(start)
    assert nxt > start and nxt.second == 0 and nxt.microsecond == 0
    assert spec.matches(nxt)
    # 사이의 분은 하나도 맞지 않는다(최대 이틀만 훑는다 — 간격이 긴 식은 날짜 단위로 확인)
    cur = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = min(nxt, cur + timedelta(days=2))
    while cur < limit:
        assert not spec.matches(cur)
        cur += timedelta(minutes=1)
    d = limit.date()
    while d < nxt.date():
        assert not spec.day_matches(d) or not spec.times_on(d)
        d += timedelta(days=1)


@settings(max_examples=150, deadline=None)
@given(exprs, moments)
def test_prev_at_or_before_is_last_match_of_that_day(expr: str, at: datetime) -> None:
    spec = CronSpec.parse(expr)
    got = spec.prev_at_or_before(at)
    if got is None:
        assert all(t > at.time() for t in spec.times_on(at.date()))
        return
    assert got.date() == at.date() and got <= at and spec.matches(got)
    after = spec.next_after(got)
    assert after > at.replace(second=0, microsecond=0)

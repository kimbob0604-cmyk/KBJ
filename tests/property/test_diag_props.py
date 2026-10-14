"""진단 속성(metrics §D0·§D4·§D7·§D8 — hypothesis).

- 백분위는 0~100, 창 안의 값 순서와 단조(큰 값의 백분위가 작지 않다), 창을 뒤섞어도 같다.
- 다섯 칸은 백분위에 단조(높은 백분위의 칸이 낮은 칸이 아니다).
- 상태 점수는 위험 선호 방향을 뒤집으면 부호가 뒤집힌다.
- 공포탐욕: 성분 < 4 이면 값 없음(invalid), 아니면 점수 = 있는 성분 평균(소수 첫째)이고 0~100,
  빠진 성분이 있으면 ok 가 아니다. 성분을 지어내지 않는다(빠진 성분은 None).
- 판정(게이지·종합): 성분 수가 문턱 미만이면 보류·점수 없음, 아니면 점수 = 합이고 판정은
  점수 구간과 맞다.
"""

from __future__ import annotations

import random
from datetime import datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.config.diag import load_diag
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.diagnosis.bands import pct_rank, pct_state, state_score
from kbj.engines.diagnosis.fear_greed import Component, fear_greed
from kbj.engines.diagnosis.public import judge

CFG = load_diag()
AT = datetime(2026, 12, 10, 16, 0, tzinfo=KST)
_STATE_ORDER = ("extreme_low", "low", "normal", "high", "extreme_high")

vals = st.lists(st.integers(-10_000, 10_000), min_size=1, max_size=300)


@settings(max_examples=200, deadline=None)
@given(vals, st.data())
def test_pct_rank_bounds_monotone_and_order_free(xs: list[int], data: st.DataObject) -> None:
    w = [Decimal(x) for x in xs]
    a = data.draw(st.sampled_from(w))
    b = data.draw(st.sampled_from(w))
    pa, pb = pct_rank(w, a), pct_rank(w, b)
    assert Decimal(0) <= pa <= Decimal(100)
    if a < b:
        assert pa < pb
    elif a == b:
        assert pa == pb
    shuffled = list(w)
    random.Random(len(w)).shuffle(shuffled)  # noqa: S311 — 시험 순서 섞기
    assert pct_rank(shuffled, a) == pa


@given(st.decimals(0, 100, places=2), st.decimals(0, 100, places=2))
def test_five_bands_monotone(p: Decimal, q: Decimal) -> None:
    lo, hi = sorted((p, q))
    assert _STATE_ORDER.index(pct_state(lo, CFG.bands)) <= _STATE_ORDER.index(
        pct_state(hi, CFG.bands)
    )


@given(st.sampled_from([*_STATE_ORDER, "narrow", "wide"]))
def test_state_score_antisymmetric(state: str) -> None:
    hi = state_score(state, "high")  # type: ignore[arg-type]
    lo = state_score(state, "low")  # type: ignore[arg-type]
    assert hi is not None and lo is not None and hi == -lo
    assert state_score(state, "none") == 0  # type: ignore[arg-type]


comp = st.one_of(st.none(), st.decimals(0, 100, places=2))


@settings(max_examples=300)
@given(st.fixed_dictionaries({k: comp for k in CFG.fear_greed.components}))
def test_fear_greed_missing_rule(pcts: dict[str, Decimal | None]) -> None:
    comps = {k: Component(v, Quality.OK) for k, v in pcts.items()}
    r = fear_greed(comps, CFG.fear_greed, day=AT.date(), as_of=AT, source="KBJ:diag.daily")
    have = [v for v in r.components.values() if v is not None]
    assert len(have) == sum(1 for v in pcts.values() if v is not None)
    if len(have) < CFG.fear_greed.min_components:
        assert r.score is None and r.quality is Quality.INVALID
        return
    assert r.score is not None and Decimal(0) <= r.score <= Decimal(100)
    mean = sum(have, Decimal(0)) / len(have)
    assert abs(r.score - mean) <= Decimal("0.05")
    assert (r.quality is Quality.OK) == (len(have) == len(CFG.fear_greed.components))


@given(
    st.dictionaries(st.sampled_from(["a", "b", "c", "d", "e", "f"]), st.sampled_from([-1, 0, 1]))
)
def test_judge_consistent(parts: dict[str, int]) -> None:
    for cfg in (CFG.gauge, CFG.verdict):
        label, score = judge(parts, cfg)
        if len(parts) < cfg.min_components:
            assert (label, score) == ("hold", None)
            continue
        assert score is not None and score == sum(parts.values())
        want = (
            "risk_on"
            if score >= cfg.risk_on_at
            else "risk_off"
            if score <= cfg.risk_off_at
            else "neutral"
        )
        assert label == want

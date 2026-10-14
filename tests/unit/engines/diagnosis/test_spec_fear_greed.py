"""metrics §D7 국장 공포탐욕 — 손계산·뒤집기·성분 빠짐(4 → estimated, 3 → 값 없음)·대체 표시·구간
경계."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from kbj.config.diag import load_diag
from kbj.core.p7_rows import fear_greed_band
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.diagnosis.fear_greed import SUBSTITUTE_ATM, Component, band_score, fear_greed

D = Decimal
CFG = load_diag().fear_greed
AT = datetime(2026, 12, 10, 16, 0, tzinfo=KST)


def _fg(comps: dict[str, Component]):
    return fear_greed(comps, CFG, day=AT.date(), as_of=AT, source="KBJ:diag.daily")


def _ok(p: str) -> Component:
    return Component(D(p), Quality.OK)


def test_d7_hand_computed_with_inversion() -> None:
    r = _fg({
        "vol": _ok("80"),            # 100 − 80 = 20
        "breadth": _ok("60"),        # 60
        "credit_balance": _ok("70"), # 70
        "foreign": _ok("50"),        # 50
        "credit_spread": _ok("10"),  # 100 − 10 = 90
    })  # fmt: skip
    # (20 + 60 + 70 + 50 + 90) / 5 = 58.0 → 탐욕
    assert r.score == D("58.0") and r.band == "greed" and r.quality is Quality.OK
    assert r.components["vol"] == D("20.00") and r.components["credit_spread"] == D("90.00")


def test_d7_rounds_to_one_decimal() -> None:
    r = _fg({"vol": _ok("0"), "breadth": _ok("33.33"), "credit_balance": _ok("33.33"),
             "foreign": _ok("33.34"), "credit_spread": _ok("100")})  # fmt: skip
    # (100 + 33.33 + 33.33 + 33.34 + 0) / 5 = 40.0
    assert r.score == D("40.0") and r.band == "fear"


def test_d7_four_components_estimated_three_invalid() -> None:
    four = {"vol": _ok("50"), "breadth": _ok("50"), "credit_balance": _ok("50"),
            "foreign": _ok("50")}  # fmt: skip
    r4 = _fg(four)
    assert r4.score == D("50.0") and r4.quality is Quality.ESTIMATED
    assert r4.components["credit_spread"] is None
    assert any("credit_spread" in x for x in r4.reasons)
    three = dict(four)
    del three["foreign"]
    three["credit_spread"] = Component(None, Quality.OK)
    r3 = _fg(three)
    assert r3.score is None and r3.band is None and r3.quality is Quality.INVALID
    assert any("미만" in x for x in r3.reasons)


def test_d7_substitute_marks_estimated() -> None:
    comps = {k: _ok("50") for k in CFG.components}
    comps["vol"] = Component(D("40"), Quality.OK, SUBSTITUTE_ATM)
    r = _fg(comps)
    assert r.quality is Quality.ESTIMATED and SUBSTITUTE_ATM in r.reasons
    assert r.components["vol"] == D("60.00")


def test_d7_invalid_component_is_dropped() -> None:
    comps = {k: _ok("50") for k in CFG.components}
    comps["foreign"] = Component(D("99"), Quality.INVALID)
    r = _fg(comps)
    assert r.components["foreign"] is None and r.quality is Quality.ESTIMATED


def test_d7_unknown_component_rejected() -> None:
    with pytest.raises(ValueError, match="모르는 성분"):
        _fg({"vkospi": _ok("1")})


@pytest.mark.parametrize(
    ("score", "band"),
    [("0", "extreme_fear"), ("24.9", "extreme_fear"), ("25", "fear"), ("44.9", "fear"),
     ("45", "neutral"), ("55", "neutral"), ("55.1", "greed"), ("75", "greed"),
     ("75.1", "extreme_greed"), ("100", "extreme_greed")],
)  # fmt: skip
def test_d7_band_edges_match_config(score: str, band: str) -> None:
    assert fear_greed_band(D(score)) == band
    b1, b2, b3, b4 = CFG.bands  # 25·45·56·76 — 정수 점수 기준 경계와 같다
    s = D(score)
    idx = sum(1 for b in (b1, b2, b3, b4) if s >= b) if s == s.to_integral() else None
    if idx is not None:
        assert ("extreme_fear", "fear", "neutral", "greed", "extreme_greed")[idx] == band


def test_band_score() -> None:
    bands = ("extreme_fear", "fear", "neutral", "greed", "extreme_greed")
    assert [band_score(b) for b in bands] == [-1, -1, 0, 1, 1]
    assert band_score(None) is None

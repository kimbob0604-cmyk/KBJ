"""역사적 신고가 깊이(D-P3-11, 메인 결정 R8) — KRX 백필이 닿는 범위에서만 계산한다.

스칼라에 `history_from`(받은 일봉 이력의 첫날)이 있으면 상장일과 비교한다: 상장일이 그보다 앞이거나
모르면 hist 를 계산하지 않고 사유를 돌려준다(지어내지 않는다). history_from 이 없는 스칼라(ET 방식 —
골든)는 예전 그대로다. 엔진은 `Bar` 도 dict 행과 똑같이 받는다.
"""

from __future__ import annotations

from datetime import date, timedelta

from kbj.core.quality import Quality
from kbj.core.rows import AllTime, Bar
from kbj.engines.board import newhigh as nh
from kbj.engines.board.config import BoardConfig

CFG = BoardConfig.load()
AT = dict(hi=120.0, cl=118.0, prev_hi=110.0, prev_cl=109.0, last_date="2026-10-06", n_days=300)


def test_history_from_이_없으면_예전_그대로() -> None:
    assert nh.hist_ref_for(AT, "2026-10-06") == ({"high": 110.0, "close": 109.0}, "")
    assert nh.hist_ref_for(AT, "2026-10-06", listed_on=None)[1] == ""


def test_상장일이_이력_시작보다_앞이면_계산하지_않는다() -> None:
    at = dict(AT, history_from="2021-10-01")
    assert nh.hist_ref_for(at, "2026-10-06", listed_on="1999-05-03") == (
        None,
        nh.HIST_BEFORE_LISTING,
    )
    assert nh.hist_ref_for(at, date(2026, 10, 6), listed_on=date(2021, 10, 1))[1] == ""
    assert nh.hist_ref_for(at, "2026-10-06", listed_on="2023-01-02")[1] == ""


def test_상장일을_모르면_계산하지_않는다() -> None:
    at = dict(AT, history_from="2021-10-01")
    assert nh.hist_ref_for(at, "2026-10-06") == (None, nh.HIST_LISTING_UNKNOWN)


def test_AllTime_행도_받는다() -> None:
    row = AllTime(
        code="000010", hi=120.0, hi_date=None, cl=118.0, cl_date=None, prev_hi=110.0,
        prev_cl=109.0, first_date=date(2021, 10, 1), last_date=date(2026, 10, 6), n_days=300,
        suspect=False, suspect_date=None, suspect_note=None, history_from=date(2021, 10, 1),
        source="krx", quality=Quality.OK,
    )  # fmt: skip
    assert nh.hist_ref_for(row, date(2026, 10, 6), listed_on=date(2021, 10, 1)) == (
        {"high": 110.0, "close": 109.0},
        "",
    )
    assert nh.alltime_dict(row)["history_from"] == "2021-10-01"


def test_Bar_와_dict_행은_같은_결과를_낸다() -> None:
    d0 = date(2025, 1, 1)
    closes = [100.0 + (i % 5) for i in range(70)] + [130.0]
    dicts = [
        dict(asof=(d0 + timedelta(days=i)).isoformat(), open=c, high=c, low=c, close=c, volume=10)
        for i, c in enumerate(closes)
    ]
    bars = [
        Bar(code="000010", date=d0 + timedelta(days=i), open=c, high=c, low=c, close=c,
            volume=10, turnover=None, source="krx", venue="KRX", quality=Quality.OK)
        for i, c in enumerate(closes)
    ]  # fmt: skip
    a = nh.evaluate(dicts, dicts[-1]["asof"], CFG)
    b = nh.evaluate(bars, bars[-1].date, CFG)
    assert a == b
    assert a is not None and a["basis"]["close"]["label"] == "d60"
    r1 = nh.roll_alltime(None, dicts, CFG)
    r2 = nh.roll_alltime(None, bars, CFG)
    assert r1 == r2

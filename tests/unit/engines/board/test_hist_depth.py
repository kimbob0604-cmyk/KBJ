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
    closes = [100.0 + (i % 5) for i in range(130)] + [130.0]  # 120일 창을 채운다(ADR 0017)
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
    assert a is not None and a["basis"]["close"]["label"] == "d120"
    r1 = nh.roll_alltime(None, dicts, CFG)
    r2 = nh.roll_alltime(None, bars, CFG)
    assert r1 == r2


# ── 원천 바닥(ADR 0017 — 메인 추가 지시 2026-10-08) ───────────────────────────────────────
FLOOR = "2010-01-04"


def test_이력이_원천_바닥에_닿았으면_상장일이_더_앞이어도_판정한다() -> None:
    at = dict(AT, history_from=FLOOR)
    assert nh.hist_depth(at, "1975-06-11", FLOOR) == ("floor", "")
    assert nh.hist_ref_for(at, "2026-10-06", listed_on="1975-06-11", source_floor=FLOOR) == (
        {"high": 110.0, "close": 109.0},
        "",
    )
    # 상장일을 몰라도 바닥에 닿았으면 더 받을 것이 없다 — 판정한다(바닥 기준)
    assert nh.hist_depth(at, None, date(2010, 1, 4)) == ("floor", "")
    # 바닥보다 앞에서 시작한 이력도 바닥에 닿은 것이다
    assert nh.hist_depth(dict(AT, history_from="2009-12-30"), "1975-06-11", FLOOR)[0] == "floor"


def test_상장일에_닿으면_바닥이_아니라_상장_기준이다() -> None:
    at = dict(AT, history_from=FLOOR)
    assert nh.hist_depth(at, "2015-03-02", FLOOR) == ("listing", "")
    assert nh.hist_depth(at, FLOOR, FLOOR) == ("listing", "")


def test_상장일에도_바닥에도_못_닿으면_보류와_사유() -> None:
    at = dict(AT, history_from="2021-10-01")
    assert nh.hist_depth(at, "1999-05-03", FLOOR) == (None, nh.HIST_BEFORE_LISTING)
    assert nh.hist_depth(at, None, FLOOR) == (None, nh.HIST_LISTING_UNKNOWN)
    assert nh.hist_ref_for(at, "2026-10-06", listed_on="1999-05-03", source_floor=FLOOR) == (
        None,
        nh.HIST_BEFORE_LISTING,
    )
    # 바닥 설정이 없으면(legacy·ET 설정) 예전 규칙 그대로 — 상장일만 본다
    assert nh.hist_depth(dict(AT, history_from=FLOOR), "1975-06-11") == (
        None,
        nh.HIST_BEFORE_LISTING,
    )


def test_board_yaml_의_원천_바닥은_한_곳이다() -> None:
    assert CFG["newhigh"]["hist_source_floor"] == FLOOR

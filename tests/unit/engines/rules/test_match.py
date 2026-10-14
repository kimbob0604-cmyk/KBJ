"""일치 감시(D-P8-14, metrics §30.4) — 차이 사유 분류."""

from __future__ import annotations

from datetime import date, datetime

from kbj.core.time import KST
from kbj.engines.rules.match import MatchEvent, classify, compare, match_days

D1, D2 = date(2026, 10, 12), date(2026, 10, 13)


def _e(
    code: str, d: date, fp: str, mode: str = "close", hm: tuple[int, int] = (15, 30)
) -> MatchEvent:
    ts = datetime(d.year, d.month, d.day, *hm, tzinfo=KST)
    return MatchEvent(code, ts, d, mode, fp)  # type: ignore[arg-type]


def test_classify() -> None:
    assert classify("a", "a", revised=True) == "logic"
    assert classify("a", "b", revised=False) == "data_revised"
    assert classify(None, "b", revised=True) == "data_revised"
    assert classify("a", None, revised=False) == "logic"


def test_compare_sides_and_reasons() -> None:
    live = [_e("000001", D1, "f1"), _e("000002", D1, "f2"), _e("000003", D1, "f3")]
    bt = [_e("000001", D1, "x1"), _e("000004", D1, "f4")]
    fps = {"000002": "f2", "000003": "zz"}  # 실시간에만 있는 칸의 백테스트 지문
    diffs = compare(
        live, bt,
        backtest_fp=lambda e: fps.get(e.code),
        revised=lambda e: e.code == "000004",
    )  # fmt: skip
    got = {(d.code, d.side, d.reason) for d in diffs}
    # 000001 은 양쪽 참(지문이 달라도 차이 아님), 000002 지문 같음 → logic, 000003 다름 → 수정,
    # 000004 는 백테스트에만 — 실시간 지문 없음 → revised 판단
    assert got == {
        ("000002", "live_only", "logic"),
        ("000003", "live_only", "data_revised"),
        ("000004", "backtest_only", "data_revised"),
    }


def test_close_key_ignores_eval_time_intraday_does_not() -> None:
    live = [_e("000001", D1, "f", hm=(16, 0)), _e("000001", D1, "f", "intraday_replay", (10, 0))]
    bt = [_e("000001", D1, "f", hm=(15, 30)), _e("000001", D1, "f", "intraday_replay", (10, 10))]
    diffs = compare(live, bt, backtest_fp=lambda e: None, revised=lambda e: False)
    assert [(d.side, d.reason) for d in diffs] == [
        ("live_only", "logic"),
        ("backtest_only", "logic"),
    ]


def test_match_days_rows_every_day() -> None:
    live = [_e("000001", D1, "f")]
    bt = [_e("000001", D1, "f"), _e("000002", D2, "g")]
    rows = match_days([D1, D2], live, bt, backtest_fp=lambda e: None, revised=lambda e: False)
    assert [(r.trade_date, r.live_n, r.backtest_n, r.logic_n) for r in rows] == [
        (D1, 1, 1, 0),
        (D2, 0, 1, 1),
    ]
    assert rows[1].diffs[0].as_dict()["side"] == "backtest_only"

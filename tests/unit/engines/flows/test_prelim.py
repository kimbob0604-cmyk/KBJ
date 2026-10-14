"""가집계 회차 고르기(`kbj.engines.flows.prelim` — 공식 예제상 하루 4회 입력·순위 목록,
ADR 0031 §6).

합성 값(실데이터 아님). 슬롯마다 받은 목록 중 구분별로 확실히 들어온 가장 최근 회차의 목록 하나만
쓰고, 다음 회차 ±10분 창의 목록은 쓰지 않으며, 목록 밖·회차 전은 값이 없다(판단 불가).
"""

from __future__ import annotations

from datetime import datetime

from kbj.core.quality import Quality
from kbj.core.rows import IntradayInvestor, Investor
from kbj.core.time import KST
from kbj.engines.flows.prelim import latest_round, live_rows, settled_round

F, INST = Investor.FOREIGN, Investor.INSTITUTION


def at(h: int, m: int) -> datetime:
    return datetime(2026, 10, 7, h, m, tzinfo=KST)


def q(code: str, ts: datetime, inv: Investor, v: int) -> IntradayInvestor:
    return IntradayInvestor(code, ts, inv, "KRX", v, None, 1, "kis.prelim", Quality.ESTIMATED)


def test_settled_round_needs_ten_minutes_after_the_round() -> None:
    assert settled_round(F, at(9, 30)) is None  # ±10분 — 아직 확신할 수 없다
    r = settled_round(F, at(9, 40))
    assert r is not None and r.label == "09:30" and r.start == at(9, 40) and r.end == at(11, 10)
    assert settled_round(INST, at(9, 50)) is None  # 기관 첫 회차는 10:00
    r2 = settled_round(INST, at(13, 10))  # 13:20 회차 ±10분 창 — 앞 회차
    assert r2 is not None and r2.label == "11:20" and r2.end == at(13, 10)
    last = settled_round(F, at(15, 20))
    assert last is not None and last.label == "14:30" and last.end is None


def test_latest_round_uses_one_list_per_investor_and_skips_the_ambiguous_window() -> None:
    rows = [
        q("000010", at(11, 30), F, 100),  # 11:20 회차 창 첫 슬롯
        q("000010", at(13, 0), F, 100),  # 같은 회차 — 가장 늦게 받은 목록
        q("000020", at(13, 0), F, -50),  # 순매도 목록
        q("000010", at(13, 10), F, 999),  # 13:20 회차가 이미 들어왔을 수 있다 — 쓰지 않는다
        q("000010", at(13, 0), INST, 70),
    ]
    got, used = latest_round(rows, at(13, 10))
    assert {(r.code, r.investor, r.ts, r.net_value) for r in got} == {
        ("000010", F, at(13, 0), 100),
        ("000020", F, at(13, 0), -50),
        ("000010", INST, at(13, 0), 70),
    }
    assert {k: v.label for k, v in used.items()} == {F: "11:20", INST: "11:20"}
    # 13:30 — 13:20 회차가 확실하지만 그 창의 목록을 아직 못 받았으면(수집 실패) 그 구분은 없다
    got2, used2 = latest_round(rows, at(13, 30))
    assert got2 == [] and used2 == {}


def test_live_rows_before_first_round_and_outside_the_lists_are_missing() -> None:
    rows = [q("000010", at(9, 40), F, 100), q("000010", at(9, 40), INST, 0)]
    led, rounds = live_rows(rows, at(9, 40))
    # 외국인 09:30 회차만 확실 — 기관 값(0 으로 실린 것 포함)은 쓰지 않는다
    assert rounds == {"foreign": "09:30"}
    assert led["000010"].foreign == 100_000_000 or led["000010"].foreign == 100
    assert led["000010"].inst is None
    assert "000099" not in led  # 두 목록 어디에도 없는 종목 — 행 없음(판단 불가)
    assert live_rows(rows, at(9, 20)) == ({}, {})

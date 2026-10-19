"""장중 투자자 1분 엔진(metrics §21) — 손계산 표·분 축·끊긴 선·7카드(사모 제외)·검산 ①②③·품질."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time

import pytest

from kbj.core.p5_rows import MINUTE_INVESTORS, InvestorMinute
from kbj.core.quality import Quality
from kbj.core.rows import Investor
from kbj.core.time import KST
from kbj.engines.flows.minute import (
    C1_TOL_MILLION,
    DEFAULT_ON,
    LINE_ORDER,
    MILLION,
    check3,
    check_slot,
    minute_axis,
    minute_board,
    minute_report,
    slot_quality,
)
from tests.fixtures.synthetic.p5_world import TODAY, build_world

DAY = date(2026, 11, 17)


def ts(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 11, 17, h, m, s, tzinfo=KST)


def slot(t: datetime, net: dict[str, int], q: Quality = Quality.OK) -> dict[str, InvestorMinute]:
    """손계산용 한 분 — 순매수 대금(백만원)만 의미가 있고 매도 0·매수 = 순(음수면 매도)."""
    out: dict[str, InvestorMinute] = {}
    for inv in MINUTE_INVESTORS:
        n = net.get(inv, 0)
        sv, bv = (0, n) if n >= 0 else (-n, 0)
        out[inv] = InvestorMinute(t, DAY, "day", "SYNK", "SYN0", inv, 0, 0, 0, sv, bv, n, q)
    return out


BAL = {  # 검산 ①②가 맞는 한 분(백만원)
    "scrt": 10, "ivtr": 5, "pe_fund": -3, "bank": 1, "insu": 2, "mrbn": 0, "fund": 7,
    "orgn": 22, "frgn": -40, "etc_corp": 6, "etc_orgt": 2, "prsn": 10,
}  # fmt: skip


def test_axis_is_open_to_close_plus_ten_inclusive() -> None:
    ax = minute_axis(ts(9, 0), ts(15, 30))
    assert len(ax) == 401 and ax[0] == ts(9, 0) and ax[-1] == ts(15, 40)
    late = minute_axis(ts(10, 0), ts(16, 30))  # 수능일 지연 개장
    assert late[0] == ts(10, 0) and late[-1] == ts(16, 40)
    with pytest.raises(ValueError):
        minute_axis(datetime(2026, 11, 17, 9, 0), ts(15, 30))  # noqa: DTZ001 - 시간대 없음 거부


def test_slot_quality_45_seconds() -> None:
    assert slot_quality(ts(10, 0), ts(10, 0, 45)) == Quality.OK
    assert slot_quality(ts(10, 0), ts(10, 0, 46)) == Quality.ESTIMATED


def test_check_slot_hand_computed() -> None:
    c = check_slot(slot(ts(10, 0), BAL), spot=True)
    assert (c.c1_residual, c.c1_ok, c.c2_ok, c.ok) == (0, True, True, True)
    off = check_slot(slot(ts(10, 0), {**BAL, "prsn": BAL["prsn"] + C1_TOL_MILLION + 1}), spot=True)
    assert off.c1_residual == C1_TOL_MILLION + 1 and off.c1_ok is False and not off.ok
    within = check_slot(slot(ts(10, 0), {**BAL, "prsn": BAL["prsn"] + C1_TOL_MILLION}), spot=True)
    assert within.c1_ok is True
    # 선물·옵션은 ① 을 하지 않는다(정의 별도 — #39)
    fut = check_slot(slot(ts(10, 0), {**BAL, "prsn": 999}), spot=False)
    assert fut.c1_ok is None and fut.ok
    # ② 기관 ≠ 7세부 합(허용 = 구분 수 백만원) — 기타단체를 넣은 정의로도 안 맞으면 실패
    bad2 = check_slot(slot(ts(10, 0), {**BAL, "orgn": 22 + 30}), spot=False)
    assert bad2.c2_ok is False and "net_value" in bad2.c2_failed
    assert bad2.orgn_has_etc_orgt is False


def test_check2_accepts_orgn_that_includes_etc_orgt_and_check1_follows() -> None:
    """KIS 기관계가 기타단체를 넣은 정의여도 통과 — 그때 ① 은 기타단체를 두 번 더하지 않는다."""
    bal = {**BAL, "etc_orgt": 40, "orgn": 22 + 40}
    bal["prsn"] = -(bal["frgn"] + bal["orgn"] + bal["etc_corp"])  # 합 0(기타단체는 기관계 안)
    c = check_slot(slot(ts(10, 0), bal), spot=True)
    assert c.c2_ok is True and c.orgn_has_etc_orgt is True
    assert c.c1_ok is True and c.c1_residual == 0
    # 7세부 합 정의(기존)는 그대로 통과·① 은 기타단체를 더한다
    plain = check_slot(slot(ts(10, 0), BAL), spot=True)
    assert plain.orgn_has_etc_orgt is False and plain.c1_residual == 0


def test_missing_term_means_check_not_possible() -> None:
    one = slot(ts(10, 0), BAL)
    del one["etc_orgt"]
    c = check_slot(one, spot=True)
    assert c.c1_ok is None and c.c1_residual is None  # 0 으로 채우지 않는다
    del one["fund"]
    assert check_slot(one, spot=True).c2_ok is None


def test_board_lines_break_on_missing_minutes_and_cards_exclude_pe_fund() -> None:
    ax = minute_axis(ts(9, 0), ts(15, 30))
    rows = [
        *slot(ts(9, 0), BAL).values(),
        *slot(ts(9, 2), {**BAL, "frgn": -50, "prsn": 20}).values(),
    ]
    b = minute_board(rows, ax, DAY)
    frgn = next(ln for ln in b.lines if ln.investor == "frgn")
    assert frgn.values[:3] == (-40 * MILLION, None, -50 * MILLION)  # 09:01 빈 분은 None(잇지 않음)
    assert all(v is None for v in frgn.values[3:])
    assert [ln.investor for ln in b.lines] == list(LINE_ORDER)
    assert {ln.investor for ln in b.lines if ln.default_on} == set(DEFAULT_ON)
    cards = {c.investor: c for c in b.cards}
    assert list(cards) == ["prsn", "frgn", "orgn", "scrt", "ivtr", "fund", "etc_corp"]
    assert "pe_fund" not in cards  # 투신 카드는 사모 제외(사용자 결정 Q7)
    assert cards["frgn"].net_value == -50 * MILLION and cards["ivtr"].net_value == 5 * MILLION
    assert (b.last_ts, b.filled, b.missing) == (ts(9, 2), 2, 1)


def test_board_excludes_invalid_minutes_and_counts_them() -> None:
    ax = minute_axis(ts(9, 0), ts(15, 30))
    good = list(slot(ts(9, 0), BAL).values())
    bad = [replace(r, quality=Quality.INVALID) for r in slot(ts(9, 1), BAL).values()]
    b = minute_board(good + bad, ax, DAY)
    assert b.invalid_slots == 1 and b.last_ts == ts(9, 0) and b.quality == Quality.OK
    late = [replace(r, quality=Quality.ESTIMATED) for r in slot(ts(9, 1), BAL).values()]
    assert minute_board(good + late, ax, DAY).quality == Quality.ESTIMATED


def test_empty_board() -> None:
    b = minute_board([], minute_axis(ts(9, 0), ts(15, 30)), DAY)
    assert b.last_ts is None and b.quality is None and b.missing == 0
    assert all(c.net_value is None for c in b.cards)


def test_check3_last_minute_vs_daily_won() -> None:
    last = slot(ts(15, 30), BAL)
    daily = {Investor.INDIVIDUAL: 10 * MILLION, Investor.FOREIGN: -40 * MILLION + 3}
    res = check3(last, daily)
    assert res["prsn"] == 0 and res["frgn"] == -3 and res["orgn"] is None


def test_world_minutes_pass_all_checks_and_report_counts() -> None:
    w = build_world()
    assert w.minute_checks() == {"c1": 0, "c2": 0, "c3": 0}
    daily = {
        Investor.INDIVIDUAL: w.investor_daily_won()[("kospi", "prsn")],
        Investor.FOREIGN: w.investor_daily_won()[("kospi", "frgn")],
    }
    rep = minute_report(w.minutes("kospi"), spot=True, daily_won=daily)
    assert rep["slots"] == 391
    assert rep["c1"] == {"ok": 391, "failed": 0, "na": 0}
    assert rep["c2"] == {"ok": 391, "failed": 0, "na": 0}
    assert rep["c3"] == {"checked": 2, "failed": 0, "na": 9}
    fut = minute_report(w.minutes("k200_fut"), spot=False)
    assert fut["c1"] == {"status": "n/a", "reason": "현물만"}
    assert fut["c3"] == {"status": "n/a", "reason": "현물 일별 확정이 없다"}
    assert TODAY == DAY and time(9, 0) <= w.minutes("kospi")[0].ts.time()

"""내부자 창(kbj.engines.filings.insider) — docs/metrics.md §13, 설계 §4.4.

합성 세계 #15(대표·주요주주 대량 매도 + 정정 + 5% 보고)의 손계산(`p4_world.expect_insider_windows`·
`p4_expect.yaml hand.insider_994140`)과 같은지, 정정 대체·창 경계(닫힌 구간)·사유 파악률·PIT 를
본다.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.core.fin_rows import InsiderReport, MajorHolderReport
from kbj.core.quality import Quality
from kbj.engines.filings import insider as ins
from tests.unit.engines.filings.filings_world import world

EXPECT = Path(__file__).resolve().parents[4] / "tests/fixtures/synthetic/p4_expect.yaml"
CORP = "99400001"
T = date(2026, 11, 16)


def rep(n: int, day: date, delta: int | None, *, who: str = "가상임원",
        reason: str | None = "장내매도(-)", **kw: Any) -> InsiderReport:  # fmt: skip
    base: dict[str, Any] = dict(
        rcept_no=f"{day:%Y%m%d}{n:06d}", corp_code=CORP, rcept_dt=day, reporter=who,
        position="사내이사", registered=True, major_holder=False, shares_after=1_000_000,
        shares_delta=delta, ratio_after=2.0, ratio_delta=None, reason=reason,
    )  # fmt: skip
    base.update(kw)
    return InsiderReport(**base)


def test_world_case_15_matches_hand_calculation() -> None:
    w = world()
    corp = w.by_no(15).company_corp
    reports = [r for r in w.insider_reports if r.corp_code == corp]
    fixes = {d.rcept_no for d in w.disclosures if d.corp_code == corp and d.title.startswith("[")}
    den = max(
        (s for s in w.share_counts if s.corp_code == corp and (s.rcept_dt or s.period_end) <= T),
        key=lambda s: (s.period_end, s.rcept_no),
    ).common_issued
    got = {x.days: x for x in ins.windows(reports, T, corp_code=corp, denominator=den,
                                          corrections=fixes)}  # fmt: skip
    exp = w.expect_insider_windows()
    hand = yaml.safe_load(EXPECT.read_text(encoding="utf-8"))["hand"]["insider_994140"]
    for days in (30, 90, 180):
        g = got[days]
        for key in ("net_shares", "buy_shares", "sell_shares", "n_reports", "n_reporters"):
            assert getattr(g, key) == exp[days][key] == hand[days][key], (days, key)
        assert g.ratio_pct == pytest.approx(hand[days]["ratio_pct"], abs=5e-5)
        assert g.reason_known_pct == pytest.approx(100.0)
        assert g.quality is Quality.OK
    # 명시한 짝(world.insider_base)으로 대체해도 같다
    paired = ins.windows(reports, T, corp_code=corp, denominator=den, base_of=w.insider_base)
    assert [p.net_shares for p in paired] == [got[d].net_shares for d in (30, 90, 180)]


def test_correction_replaces_previous_report_of_same_reporter_only() -> None:
    a = rep(1, date(2026, 11, 2), -1_200_000, who="주주법인")
    other = rep(2, date(2026, 11, 3), -100, who="가상임원")
    fix = rep(3, date(2026, 11, 5), -1_150_000, who="주주법인")
    got = ins.dedupe([a, other, fix], {fix.rcept_no})
    assert [r.rcept_no for r in got] == [other.rcept_no, fix.rcept_no]
    # 정정 표시가 없으면 둘 다 다른 보고다
    assert len(ins.dedupe([a, other, fix])) == 3


def test_correction_of_correction_keeps_the_latest() -> None:
    a = rep(1, date(2026, 10, 1), -10)
    f1 = rep(2, date(2026, 10, 2), -20)
    f2 = rep(3, date(2026, 10, 3), -30)
    got = ins.dedupe([a, f1, f2], {f1.rcept_no, f2.rcept_no})
    assert [r.shares_delta for r in got] == [-30]


def test_correction_filed_after_t_is_not_used_at_t() -> None:
    a = rep(1, date(2026, 11, 2), -1_000)
    fix = rep(2, date(2026, 11, 20), -2_000)
    w30 = ins.windows([a, fix], T, corp_code=CORP, denominator=None, corrections={fix.rcept_no})
    assert w30[0].net_shares == -1_000  # t(11-16) 에는 정정을 몰랐다


def test_window_is_closed_interval_on_both_ends() -> None:
    edge = rep(1, T - timedelta(days=30), -100)
    out = rep(2, T - timedelta(days=31), -1_000)
    today = rep(3, T, 50, reason="장내매수(+)")
    w = ins.window([edge, out, today], T, 30, corp_code=CORP, denominator=10_000)
    assert (w.net_shares, w.buy_shares, w.sell_shares, w.n_reports) == (-50, 50, 100, 2)
    assert w.ratio_pct == pytest.approx(-0.5)


def test_reason_known_share_and_unknown_delta() -> None:
    a = rep(1, date(2026, 11, 1), -300, reason=None)
    b = rep(2, date(2026, 11, 2), 100, reason="증여")
    c = rep(3, date(2026, 11, 3), None)
    w = ins.window([a, b, c], T, 30, corp_code=CORP, denominator=None)
    assert w.reason_known_pct == pytest.approx(25.0)  # 100 / (300 + 100)
    assert w.n_reports == 3 and w.net_shares == -200
    assert w.quality is Quality.ESTIMATED  # 증감 칸이 없는 보고가 섞였다
    assert w.ratio_pct is None  # 분모 없음


def test_empty_window_is_zero_with_no_ratio_of_reason() -> None:
    w = ins.window([], T, 90, corp_code=CORP, denominator=1_000)
    assert (w.net_shares, w.n_reports, w.ratio_pct, w.reason_known_pct) == (0, 0, 0.0, None)
    with pytest.raises(ValueError):
        ins.window([], T, 60, corp_code=CORP, denominator=None)


def test_other_company_reports_are_ignored() -> None:
    mine = rep(1, date(2026, 11, 1), -10)
    theirs = rep(2, date(2026, 11, 1), -999, corp_code="99400002")
    assert ins.window([mine, theirs], T, 30, corp_code=CORP, denominator=None).net_shares == -10


def test_invalid_reports_are_dropped() -> None:
    bad = rep(1, date(2026, 11, 1), -10, quality=Quality.INVALID)
    assert ins.dedupe([bad]) == []


def test_major_holder_list_dedupes_the_same_way() -> None:
    def maj(n: int, day: date, ratio: float) -> MajorHolderReport:
        return MajorHolderReport(f"{day:%Y%m%d}{n:06d}", CORP, day, "가상자산운용", "일반",
                                 2_000_000, 0, ratio, 0.0, "보고")  # fmt: skip

    a, fix = maj(1, date(2026, 10, 20), 5.12), maj(2, date(2026, 10, 22), 5.21)
    assert [m.ratio for m in ins.dedupe_major([a, fix], {fix.rcept_no})] == [5.21]


def test_insider_amount_login_helper() -> None:
    a = rep(1, date(2026, 11, 2), -100)
    b = rep(2, date(2026, 11, 3), 50)
    closes = {date(2026, 11, 2): 1_000.0}
    amount, missing = ins.insider_amount([a, b], lambda _c, d: closes.get(d), "994140")
    assert (amount, missing) == (-100_000, 1)
    assert ins.insider_amount([b], lambda _c, _d: None, "994140") == (None, 1)

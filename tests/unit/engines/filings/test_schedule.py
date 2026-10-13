"""실적 일정(kbj.engines.filings.schedule) — docs/metrics.md §14 '실적 일정', 설계 §4.2.

결산실적공시예고 → 예정일 행(ok — 대상 기간 = 예정일 전에 끝난 마지막 회계 분기), 법정
제출기한(분기·반기 45일, 사업 90일 — estimated, 휴장일이면 다음 거래일), 이미 낸 기간은 지운다,
반기 보고 회사는 1·3분기 없음, 비12월 결산.
"""

from __future__ import annotations

from datetime import date

from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.engines.filings import schedule as sch
from tests.unit.engines.filings.filings_world import PRE_ANNOUNCE, world

CORP = "99400001"
KR = TradingCalendar.default()


def test_world_pre_announce_row_matches_the_world_truth() -> None:
    w = world()
    truth = w.schedule[0]
    item = sch.PreAnnounceItem(
        w.rcept("e1.preann"), w.today, PRE_ANNOUNCE.planned_date, PRE_ANNOUNCE.target
    )
    row = sch.from_pre_announce(truth.corp_code, 12, item)
    assert row == truth


def test_statutory_deadlines_around_today() -> None:
    rows = sch.statutory_deadlines(CORP, 12, date(2026, 11, 16), calendar=KR)
    got = [(r.period_end, r.fiscal_q, r.planned_date) for r in rows]
    # 3분기(09-30 + 45 = 11-14 토 → 다음 거래일 11-16), 4분기(12-31 + 90 = 2027-03-31)
    assert got == [
        (date(2026, 9, 30), 3, date(2026, 11, 16)),
        (date(2026, 12, 31), 4, date(2027, 3, 31)),
    ]
    assert all(r.kind == "statutory" and r.quality is Quality.ESTIMATED for r in rows)
    assert "분기말 + 45일" in (rows[0].note or "") and "결산일 + 90일" in (rows[1].note or "")


def test_without_calendar_the_raw_date_is_kept() -> None:
    rows = sch.statutory_deadlines(CORP, 12, date(2026, 11, 16))
    assert rows[0].planned_date == date(2026, 11, 14)  # 휴일 처리 없이 그대로 [확인 필요]
    assert "기한 지남" in (rows[0].note or "")


def test_filed_periods_are_removed() -> None:
    titles = ["분기보고서 (2026.09)", "[기재정정]반기보고서 (2026.06)"]
    filed = sch.filed_period_ends(titles)
    assert filed == {date(2026, 9, 30), date(2026, 6, 30)}
    rows = sch.statutory_deadlines(CORP, 12, date(2026, 11, 16), filed=filed)
    assert [r.period_end for r in rows] == [date(2026, 12, 31)]


def test_semiannual_filers_have_no_q1_q3_deadlines() -> None:
    assert sch.is_semiannual(["반기보고서 (2026.06)", "사업보고서 (2025.12)"], "Y")
    assert not sch.is_semiannual(["분기보고서 (2026.03)", "반기보고서 (2026.06)"], "K")
    assert sch.is_semiannual([], "N")  # 코넥스 [확인 필요]
    assert not sch.is_semiannual([], "Y")  # 이력이 없으면 모른다 — 분기 보고로 본다
    rows = sch.statutory_deadlines(CORP, 12, date(2026, 11, 16), semiannual=True)
    assert [r.fiscal_q for r in rows] == [4]


def test_march_year_end_labels_and_deadlines() -> None:
    rows = sch.statutory_deadlines(CORP, 3, date(2026, 11, 16))
    # 3월 결산: 2026 회계연도 2분기 = 07~09월(분기말 09-30), 3분기 = 10~12월
    assert [(r.fiscal_year, r.fiscal_q, r.period_end) for r in rows] == [
        (2026, 2, date(2026, 9, 30)),
        (2026, 3, date(2026, 12, 31)),
    ]
    assert rows[1].planned_date == date(2027, 2, 14)  # 3분기도 45일(사업보고서 아님)


def test_unknown_year_end_is_assumed_december_and_flagged() -> None:
    rows = sch.statutory_deadlines(CORP, None, date(2026, 11, 16))
    assert rows and all("12월로 가정" in (r.note or "") for r in rows)
    item = sch.PreAnnounceItem("20261116000001", date(2026, 11, 16), date(2027, 1, 28), None)
    row = sch.from_pre_announce(CORP, None, item)
    assert row is not None and row.quality is Quality.ESTIMATED
    assert "12월로 가정" in (row.note or "")


def test_pre_announce_for_quarter_and_filed_period() -> None:
    item = sch.PreAnnounceItem("20261020000001", date(2026, 10, 20), date(2026, 10, 28), "3분기")
    row = sch.from_pre_announce(CORP, 12, item)
    assert row is not None and (row.period_end, row.fiscal_q) == (date(2026, 9, 30), 3)
    assert row.kind == "pre_announce" and row.quality is Quality.OK
    assert sch.from_pre_announce(CORP, 12, item, filed={date(2026, 9, 30)}) is None


def test_schedule_rows_keeps_the_latest_pre_announce_per_period() -> None:
    a = sch.PreAnnounceItem("20261116000001", date(2026, 11, 16), date(2027, 1, 28), "예고")
    b = sch.PreAnnounceItem("20261201000001", date(2026, 12, 1), date(2027, 1, 30), "변경")
    rows = sch.schedule_rows(CORP, 12, date(2026, 12, 2), pre_announces=[b, a], calendar=KR)
    pre = [r for r in rows if r.kind == "pre_announce"]
    assert len(pre) == 1 and pre[0].planned_date == date(2027, 1, 30)
    assert rows == sorted(rows, key=lambda r: (r.planned_date, r.period_end, r.kind))
    filed = sch.schedule_rows(
        CORP, 12, date(2026, 12, 2), periodic_titles=["분기보고서 (2026.09)"], calendar=KR
    )
    assert all(r.period_end != date(2026, 9, 30) for r in filed)

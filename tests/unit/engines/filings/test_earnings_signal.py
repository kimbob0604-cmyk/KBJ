"""잠정실적 분류·서프라이즈·신뢰도·알림 본문·성과 거래일(kbj.engines.filings.earnings).

docs/p4_design.md §4.2·§8.3('test_earnings_signal — SD 표 그대로'), docs/metrics.md §14.
SD 원본 시험은 없다(설계 §1.15 — 옮김 0). 새로: YoY 분류 표 전부(경계 포함, 단위 원), SD
`classify_signal`:172 의 12 신호·경계, `sanity_check_consensus`:34 의 3관문, 범위 가드, 템플릿
본문의 숫자 출처, 알림 후 3·5·7 거래일.
"""

from __future__ import annotations

import re
from datetime import date, datetime

import pytest

from kbj.config.fin import EarningsThresholds, load_fin
from kbj.core.calendar import TradingCalendar
from kbj.core.fin_rows import PrelimRow, PrelimScope
from kbj.core.time import KST
from kbj.engines.filings import earnings as eng
from kbj.engines.filings.earnings import Figures

TH: EarningsThresholds = load_fin().earnings.thresholds
EOK = 100_000_000  # 1억 원


def yoy(cur_op: int | None, prev_op: int | None, cur_rev: int | None = None,
        prev_rev: int | None = None) -> eng.EarningsSignal:  # fmt: skip
    return eng.classify_yoy(Figures(cur_rev, cur_op), Figures(prev_rev, prev_op), th=TH)


def test_thresholds_are_sd_values() -> None:
    assert (TH.big_pct, TH.standard_pct, TH.inline_pct, TH.turnaround_partial_ratio) == (
        20.0, 10.0, 5.0, 0.5,
    )  # fmt: skip


# ── YoY(공개 규칙 — metrics §14) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("cur", "prev", "signal", "prio"),
    [
        (10 * EOK, -5 * EOK, "TURNAROUND", 1),  # 흑자전환
        (-5 * EOK, 10 * EOK, "SHOCK", 1),  # 적자전환
        (-50 * EOK, -100 * EOK, "TURNAROUND_PARTIAL", 2),  # 적자 50% 축소 — 경계 포함
        (-51 * EOK, -100 * EOK, "YOY_BIG_UP", 1),  # 49% 축소 — 부분 전환 아님, (−51+100)/100
        (120 * EOK, 100 * EOK, "YOY_BIG_UP", 1),  # +20% 경계 포함
        (11_999_999_999, 10_000_000_000, "YOY_UP", 2),  # +19.99…%
        (110 * EOK, 100 * EOK, "YOY_UP", 2),  # +10% 경계 포함
        (10_999_999_999, 10_000_000_000, "YOY_INLINE", 5),  # +9.99…%
        (90 * EOK, 100 * EOK, "YOY_DOWN", 2),  # −10% 경계 포함
        (80 * EOK, 100 * EOK, "YOY_BIG_DOWN", 1),  # −20% 경계 포함
        (8_000_000_001, 10_000_000_000, "YOY_DOWN", 2),  # −19.99…%
        (-200 * EOK, -100 * EOK, "YOY_BIG_DOWN", 1),  # 적자 확대 = (−200+100)/|−100| = −100%
        (0, -5 * EOK, "YOY_BIG_UP", 1),  # 적자 → 0 은 흑자전환이 아니다(0 < 당기 아님) — +100%
        (5 * EOK, 0, "YOY_INLINE", 5),  # 전년동기 0 — 비율 없음, 전환 판정만
    ],
)
def test_yoy_table(cur: int, prev: int, signal: str, prio: int) -> None:
    s = yoy(cur, prev)
    assert (s.signal, s.priority, s.method, s.basis) == (signal, prio, "yoy", "op")


def test_yoy_pct_uses_abs_denominator_and_is_not_rounded() -> None:
    s = yoy(-30 * EOK, -100 * EOK)  # 70% 축소 → 부분 전환
    assert s.signal == "TURNAROUND_PARTIAL" and s.op_pct == pytest.approx(70.0)
    s2 = yoy(1_234_567, 1_000_000)
    assert s2.op_pct == pytest.approx(23.4567)  # 반올림하지 않는다(화면에서만)


def test_zero_prev_has_no_pct_and_a_reason() -> None:
    s = yoy(5 * EOK, 0)
    assert s.op_pct is None and any("비율 없음" in n for n in s.notes)


def test_revenue_basis_when_op_missing() -> None:
    s = yoy(None, None, cur_rev=130 * EOK, prev_rev=100 * EOK)
    assert (s.signal, s.priority, s.basis) == ("YOY_BIG_UP", 1, "revenue")
    assert s.triggers == ("REV_YOY_BIG_UP",)
    assert any("매출 기준" in n for n in s.notes)


def test_two_column_form_is_no_data() -> None:
    s = eng.classify_yoy(Figures(100 * EOK, 10 * EOK), Figures(None, None),
                         Figures(90 * EOK, 9 * EOK), th=TH)  # fmt: skip
    assert (s.signal, s.priority, s.basis) == (eng.NO_DATA, 5, None)
    assert s.op_qoq == pytest.approx((10 - 9) / 9 * 100)  # QoQ 는 표시용으로 남는다
    assert s.rev_pct is None and s.op_pct is None


def test_reported_yoy_difference_goes_to_notes_only() -> None:
    s = eng.classify_yoy(
        Figures(110 * EOK, 120 * EOK), Figures(100 * EOK, 100 * EOK),
        reported_yoy={"revenue": 10.0, "op": 25.0}, th=TH, tol_pp=0.1,
    )  # fmt: skip
    assert s.signal == "YOY_BIG_UP"  # 계산값(+20%)으로 분류
    assert len(s.notes) == 1 and "영업이익" in s.notes[0] and "+25.0%" in s.notes[0]
    same = eng.classify_yoy(
        Figures(110 * EOK, 120 * EOK), Figures(100 * EOK, 100 * EOK),
        reported_yoy={"revenue": 10.05, "op": 20.1}, th=TH, tol_pp=0.1,
    )  # fmt: skip
    assert same.notes == ()  # 0.1%p 이내


def _prelim(**kw: object) -> PrelimRow:
    base: dict[str, object] = dict(
        rcept_no="20261116000001", corp_code="99400140", rcept_dt=date(2026, 11, 16),
        scope=PrelimScope.CONSOLIDATED, fiscal_year=2026, fiscal_q=3, is_annual=False,
        period_end=date(2026, 9, 30), period_inferred=False, unit_mult=100_000_000,
        revenue_cur=1_234 * EOK, revenue_prev_y=1_000 * EOK, revenue_prev_q=1_100 * EOK,
        op_cur=305 * EOK, op_prev_y=200 * EOK, op_prev_q=250 * EOK, ni_cur=210 * EOK,
    )  # fmt: skip
    base.update(kw)
    return PrelimRow(**base)  # type: ignore[arg-type]


def test_classify_prelim_uses_the_row_in_won() -> None:
    s = eng.classify_prelim(_prelim(), TH)
    assert (s.signal, s.priority) == ("YOY_BIG_UP", 1)
    assert s.op_pct == pytest.approx(52.5) and s.rev_pct == pytest.approx(23.4)
    assert s.op_qoq == pytest.approx(22.0)


# ── 서프라이즈(SD classify_signal:172 그대로) ────────────────────────────────────────────


def sup(rev_a: float | None, op_a: float | None, rev_c: float | None,
        op_c: float | None) -> eng.EarningsSignal:  # fmt: skip
    return eng.classify_surprise(Figures(rev_a, op_a), Figures(rev_c, op_c), TH)


@pytest.mark.parametrize(
    ("args", "signal", "prio"),
    [
        ((100, 10, 100, -5), "TURNAROUND_FULL", 1),
        ((100, -5, 100, 10), "SHOCK_FULL", 1),
        ((100, -5, 100, -10), "TURNAROUND_PARTIAL", 2),  # 적자폭 50% 축소 — 경계 포함
        ((100, 120, 100, 100), "BEAT_BIG", 1),  # +20% 경계
        ((100, 80, 100, 100), "MISS_BIG", 1),  # −20% 경계
        ((90, 110, 100, 100), "REVENUE_MISS_OP_BEAT", 2),
        ((110, 105, 100, 100), "REVENUE_BEAT_OP_INLINE", 3),  # |op| ≤ 5 경계
        ((100, 110, 100, 100), "BEAT", 2),
        ((100, 90, 100, 100), "MISS", 2),
        ((110, 106, 100, 100), "REVENUE_BEAT", 3),  # op 6% — inline(5) 밖·standard(10) 안
        ((90, 100, 100, 100), "REVENUE_MISS", 3),
        ((100, 100, 100, 100), "INLINE", 5),
        ((None, 125, None, 100), "BEAT_BIG", 1),  # 영업이익만
        ((None, 89, None, 100), "MISS", 2),
        ((115, None, 100, None), "REVENUE_BEAT", 3),  # 매출만
        ((None, None, None, None), "INLINE", 5),  # 데이터 부족
    ],
)
def test_surprise_table(args: tuple[float | None, ...], signal: str, prio: int) -> None:
    s = sup(*args)
    assert (s.signal, s.priority, s.method) == (signal, prio, "surprise")


def test_partial_turnaround_falls_through_to_pct_when_under_half() -> None:
    # SD: 둘 다 적자, 축소율 < 50% 면 비율 분기 — op_pct = (−6 − (−10)) / 10 = +40% → BEAT_BIG
    s = sup(100, -6, 100, -10)
    assert (s.signal, s.priority) == ("BEAT_BIG", 1)
    assert s.op_pct == pytest.approx(40.0)


def test_beat_big_triggers_include_rev_beat_only_over_standard() -> None:
    assert sup(110, 130, 100, 100).triggers == ("OP_BEAT_BIG", "REV_BEAT")
    assert sup(105, 130, 100, 100).triggers == ("OP_BEAT_BIG",)


# ── 신뢰도 3관문(SD sanity_check_consensus:34) ────────────────────────────────────────────


HIST = [Figures(1000, 100)] * 8  # 매출 1,000·영업이익률 10% 이력


def test_sanity_all_pass() -> None:
    s = eng.sanity(Figures(1000, 100), HIST, mktcap=10_000)
    assert s.gates == {"A": "pass", "B": "pass", "C": "pass"}
    assert (s.recommendation, s.is_valid, s.confidence) == ("use", True, 1.0)


def test_sanity_missing_revenue_consensus_is_skip() -> None:
    s = eng.sanity(Figures(None, 100), HIST, mktcap=10_000)
    assert (s.recommendation, s.is_valid) == ("skip", False)
    assert s.gates == {"A": "fail", "B": "skip", "C": "skip"}


@pytest.mark.parametrize(
    ("rev_c", "gate"),
    [(500, "pass"), (2000, "pass"), (300, "warn"), (2500, "warn"), (299, "fail"), (3001, "fail")],
)
def test_gate_a_ratio_bounds(rev_c: int, gate: str) -> None:
    s = eng.sanity(Figures(rev_c, None), HIST, mktcap=None)
    assert s.gates["A"] == gate


def test_gate_a_skips_with_less_than_three_quarters() -> None:
    s = eng.sanity(Figures(1000, None), [Figures(1000, 100)] * 2, mktcap=None)
    assert s.gates["A"] == "skip" and s.confidence == pytest.approx(0.8)


@pytest.mark.parametrize(("op_c", "gate"), [(250, "pass"), (350, "warn"), (360, "fail")])
def test_gate_b_opm_difference(op_c: int, gate: str) -> None:
    # 컨센 OPM 25%·35%·36% vs 이력 10% → 차이 15(pass)·25(warn)·26(fail)
    s = eng.sanity(Figures(1000, op_c), HIST, mktcap=None)
    assert s.gates["B"] == gate


@pytest.mark.parametrize(
    ("mktcap", "gate"),
    [(1000, "pass"), (200_000, "pass"), (500_000, "warn"), (999, "warn"), (1_000_001, "fail"),
     (199, "fail"), (0, "fail")],
)  # fmt: skip
def test_gate_c_revenue_to_mktcap(mktcap: int, gate: str) -> None:
    s = eng.sanity(Figures(1000, None), HIST, mktcap=mktcap)
    assert s.gates["C"] == gate


def test_recommendation_rules() -> None:
    two_fail = eng.sanity(Figures(10_000, 9_000), HIST, mktcap=None)  # A fail·B fail(90%)
    assert two_fail.recommendation == "skip" and not two_fail.is_valid
    one_fail = eng.sanity(Figures(10_000, 1000), HIST, mktcap=None)  # A fail·B pass
    assert one_fail.recommendation == "use_with_caution" and not one_fail.is_valid
    two_warn = eng.sanity(Figures(2500, 700), HIST, mktcap=None)  # A warn·B warn(18%p)
    assert two_warn.gates["A"] == "warn" and two_warn.gates["B"] == "warn"
    assert two_warn.recommendation == "use_with_caution" and two_warn.is_valid


def test_apply_sanity_demotes_priority_one_only_when_cautious() -> None:
    big = sup(100, 130, 100, 100)
    caution = eng.Sanity(False, 0.3, {"A": "fail"}, (), "use_with_caution")
    got = eng.apply_sanity(big, caution)
    assert got.priority == 2 and got.notes[-1] == "컨센 신뢰도 30%"
    assert eng.apply_sanity(big, eng.Sanity(True, 1.0)).priority == 1
    two = sup(100, 110, 100, 100)
    assert eng.apply_sanity(two, caution) == two


def test_scope_guard_sd_rules() -> None:
    assert eng.scope_guard(PrelimScope.STANDALONE, 111, 100, None) is not None
    assert eng.scope_guard(PrelimScope.STANDALONE, 110, 100, None) is None  # 10% 경계 안
    assert eng.scope_guard(PrelimScope.CONSOLIDATED, 130, 100, 5.0) is not None
    assert eng.scope_guard(PrelimScope.CONSOLIDATED, 130, 100, 15.0) is None
    assert eng.scope_guard(PrelimScope.CONSOLIDATED, 130, None, 5.0) is None


# ── 알림 본문(템플릿 — LLM 없음) ─────────────────────────────────────────────────────────


def test_subject_is_corp_period_scope_not_rcept_no() -> None:
    row = _prelim()
    assert eng.alert_subject(row) == "99400140:2026Q3:consolidated"
    fix = _prelim(rcept_no="20261116000099")
    assert eng.alert_subject(fix) == eng.alert_subject(row)
    annual = _prelim(fiscal_q=4, is_annual=True, period_end=date(2025, 12, 31))
    assert eng.alert_subject(annual) == "99400140:2026FY:consolidated"


def test_period_label_for_non_december_year_end() -> None:
    row = _prelim(fiscal_year=2026, fiscal_q=2, period_end=date(2026, 9, 30))
    assert eng.period_label(row) == "FY26 2Q (26.07~26.09)"  # 3월 결산 2분기
    assert eng.period_label(_prelim()) == "2026 3Q"


def test_alert_text_numbers_all_come_from_the_row() -> None:
    row = _prelim()
    sig = eng.classify_prelim(row, TH)
    link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={row.rcept_no}"
    text = eng.alert_text(row, sig, name="가상기업", code="994140", link=link)
    assert text.splitlines()[0] == "[잠정실적] 가상기업(994140) 2026 3Q 연결"
    assert "매출 1,234억 (전년동기 +23.4%) / 영업이익 305억 (전년동기 +52.5%)" in text
    assert "당기순이익 210억" in text and link in text
    # 본문의 수는 전부 입력에서 온 것 — 행 값(억)·계산 비율·기간·우선순위·코드·접수번호
    allowed = {"994140", "2026", "3", "1", "1,234", "23.4", "305", "52.5", "210"}
    nums = set(re.findall(r"\d[\d,]*(?:\.\d+)?", text.replace(link, "")))
    assert nums <= allowed, nums - allowed
    assert "정정" not in text
    assert "정정공시" in eng.alert_text(row, sig, name="가", code="994140", link=link,
                                    correction=True)  # fmt: skip


def test_alert_text_marks_missing_and_inferred() -> None:
    row = _prelim(op_cur=None, op_prev_y=None, op_prev_q=None, ni_cur=None, period_inferred=True)
    sig = eng.classify_prelim(row, TH)
    text = eng.alert_text(row, sig, name="가상", code=None, link="L")
    assert "영업이익 —" in text and "당기순이익" not in text
    assert "결산월로 추정" in text and text.splitlines()[0].startswith("[잠정실적] 가상 ")


# ── 알림 후 성과(거래일) ─────────────────────────────────────────────────────────────────


KR = TradingCalendar.default()


@pytest.mark.parametrize(
    ("when", "base"),
    [
        (datetime(2026, 11, 16, 16, 25, tzinfo=KST), date(2026, 11, 16)),  # 마감 뒤 → 그날
        (datetime(2026, 11, 16, 15, 30, tzinfo=KST), date(2026, 11, 16)),  # 마감 시각 그대로
        (datetime(2026, 11, 16, 11, 0, tzinfo=KST), date(2026, 11, 13)),  # 장중(월) → 앞 금요일
        (datetime(2026, 11, 15, 10, 0, tzinfo=KST), date(2026, 11, 13)),  # 일요일 → 금요일
    ],
)
def test_alert_base_day(when: datetime, base: date) -> None:
    assert eng.alert_base_day(when, KR) == base


def test_perf_days_count_trading_days() -> None:
    got = eng.perf_days(date(2026, 11, 13), (3, 5, 7), KR)  # 금요일 기준
    assert got == {3: date(2026, 11, 18), 5: date(2026, 11, 20), 7: date(2026, 11, 24)}
    with pytest.raises(ValueError):
        eng.perf_days(date(2026, 11, 13), (0,), KR)


def test_perf_pct() -> None:
    assert eng.perf_pct(10_000.0, 11_000.0) == pytest.approx(10.0)
    assert eng.perf_pct(None, 1.0) is None and eng.perf_pct(1.0, None) is None
    assert eng.perf_pct(0.0, 1.0) is None

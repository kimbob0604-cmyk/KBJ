"""core.forward — §1.3 합성선물 F, §1.4 잔존기간 T."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from core.calendar import KST, TradingCalendar, expiry_at
from core.forward import (
    FUTURES_REF_TOLERANCE,
    MAX_BASIS_CARRY_DAYS,
    MIN_PARITY_STRIKES,
    MINUTES_PER_YEAR,
    S_REF_SOURCE,
    S_REF_STALE_AFTER_S,
    ForwardResult,
    FuturesQuote,
    FuturesRef,
    basis_age,
    confirm_basis,
    futures_reference,
    is_quarterly_monthly,
    kis_time_to_expiry,
    select_s_ref,
    synthetic_forward,
    time_to_expiry,
)

D = Decimal

# 1,090 ~ 1,110 을 2.5pt 간격 — S_ref 1,100 이면 ATM±2 = 1095 ~ 1105
STRIKES = [D(1090) + D("2.5") * i for i in range(9)]


def _chain(
    parity: dict[str, str], put: str = "10.00"
) -> tuple[dict[Decimal, Decimal | None], dict[Decimal, Decimal | None]]:
    """행사가 → 원하는 `C − P + K` 가 되게 콜·풋 가격을 만든다(풋 가격 고정)."""
    calls: dict[Decimal, Decimal | None] = {}
    puts: dict[Decimal, Decimal | None] = {}
    for k, f in parity.items():
        kd = D(k)
        puts[kd] = D(put)
        calls[kd] = D(f) - kd + D(put)
    return calls, puts


# --- 중앙값 ---


def test_median_of_five_in_window() -> None:
    calls, puts = _chain(
        {
            "1095": "1100.30",
            "1097.5": "1100.10",
            "1100": "1100.20",
            "1102.5": "1099.90",
            "1105": "1100.50",
        }
    )
    res = synthetic_forward(calls, puts, 1100.0)
    assert res == ForwardResult(
        F=1100.2,
        quality="ok",
        atm=D(1100),
        strikes=(D(1095), D("1097.5"), D(1100), D("1102.5"), D(1105)),
        futures_gap=None,
        reasons=(),
        notes=("no_futures_ref",),  # 선물 기준가를 안 넘겼다 — 확인 건너뜀, 품질 그대로
    )


def test_median_of_even_count_is_mean_of_middle_two() -> None:
    # 1105 의 풋이 없어 4 개: 1100.10, 1100.20, 1100.30, 1100.60 → (1100.20 + 1100.30) / 2
    calls, puts = _chain(
        {"1095": "1100.60", "1097.5": "1100.10", "1100": "1100.20", "1102.5": "1100.30"}
    )
    calls[D(1105)] = D("3.00")
    puts[D(1105)] = None
    res = synthetic_forward(calls, puts, 1100.0)
    assert res.F == 1100.25
    assert res.strikes == (D(1095), D("1097.5"), D(1100), D("1102.5"))
    assert res.quality == "ok"


def test_only_atm_plus_minus_two_are_used() -> None:
    # 창 밖(1090·1092.5·1107.5·1110)은 가격이 있어도 안 쓴다
    parity = {str(k): "1100.00" for k in STRIKES}
    parity["1090"] = parity["1110"] = "1200.00"
    calls, puts = _chain(parity)
    res = synthetic_forward(calls, puts, D("1101"))
    assert res.atm == D(1100)
    assert res.strikes == (D(1095), D("1097.5"), D(1100), D("1102.5"), D(1105))
    assert res.F == 1100.0


def test_atm_moves_with_s_ref() -> None:
    calls, puts = _chain({str(k): "1100.00" for k in STRIKES})
    res = synthetic_forward(calls, puts, 1092.0)
    assert res.atm == D("1092.5")
    assert res.strikes == (D(1090), D("1092.5"), D(1095), D("1097.5"))  # 아래 끝에서 잘림


def test_master_strikes_choose_atm() -> None:
    # 전광판이 1100 까지만 왔어도 마스터로 참 ATM(1105)을 고른다 — 창 1100~1110 중 가격 있는 1100
    calls, puts = _chain({"1095": "1100.00", "1097.5": "1100.00", "1100": "1100.40"})
    res = synthetic_forward(calls, puts, 1105.0, strikes=STRIKES)
    assert res.atm == D(1105)
    assert res.strikes == (D(1100),)
    assert res.F == 1100.4
    assert (res.quality, res.reasons) == ("estimated", ("few_strikes",))


def test_exact_decimal_parity() -> None:
    calls = {D("1100"): D("12.34"), D("1102.5"): D("11.07")}
    puts = {D("1100"): D("12.21"), D("1102.5"): D("13.43")}
    # 1100.13, 1100.14 → 1100.135
    res = synthetic_forward(calls, puts, 1100.0, min_strikes=1)
    assert res.F == 1100.135


# --- 한쪽 가격·개수 ---


def test_one_sided_strikes_skipped() -> None:
    calls, puts = _chain({"1097.5": "1100.10", "1100": "1100.20", "1102.5": "1100.30"})
    calls[D(1095)], puts[D(1095)] = D("8.00"), None
    calls[D(1105)], puts[D(1105)] = None, D("9.00")
    res = synthetic_forward(calls, puts, 1100.0)
    assert res.strikes == (D("1097.5"), D(1100), D("1102.5"))
    assert (res.F, res.quality) == (1100.2, "ok")  # 정확히 3 개는 ok


def test_zero_usable_is_invalid() -> None:
    calls = {D(1100): D("5.00"), D("1102.5"): None}
    puts = {D(1100): None, D("1102.5"): D("6.00")}
    res = synthetic_forward(calls, puts, 1100.0)
    assert res == ForwardResult(
        None, "invalid", D(1100), (), None, ("no_parity_strikes",), notes=("no_futures_ref",)
    )


def test_no_strikes_is_invalid() -> None:
    res = synthetic_forward({}, {}, 1100.0)
    assert (res.F, res.quality, res.atm, res.reasons) == (None, "invalid", None, ("no_strikes",))


@pytest.mark.parametrize(("n", "quality"), [(1, "estimated"), (2, "estimated"), (3, "ok")])
def test_fewer_than_three_is_estimated(n: int, quality: str) -> None:
    ks = ["1100", "1097.5", "1102.5"][:n]
    calls, puts = _chain(dict.fromkeys(ks, "1100.00"))
    res = synthetic_forward(calls, puts, 1100.0)
    assert (res.F, res.quality) == (1100.0, quality)
    assert res.reasons == (() if quality == "ok" else ("few_strikes",))


def test_min_strikes_is_a_parameter() -> None:
    calls, puts = _chain({"1100": "1100.00", "1102.5": "1100.00"})
    assert synthetic_forward(calls, puts, 1100.0, min_strikes=2).quality == "ok"
    with pytest.raises(ValueError):
        synthetic_forward(calls, puts, 1100.0, min_strikes=0)


def test_nonpositive_forward_is_invalid() -> None:
    res = synthetic_forward({D(5): D("0.01")}, {D(5): D("9.00")}, 5.0)
    assert (res.F, res.reasons) == (None, ("nonpositive_forward",))


# --- 전 세션 가격 행사가 (§1.1 prev_session) [확인 필요] ---

# ATM±2 중 1097.5·1102.5 는 전 세션 가격끼리의 패리티라 +30pt 어긋나 있다(validation_greeks §5.4)
SHIFTED = {"1095": "1100.00", "1097.5": "1130.00", "1100": "1100.20", "1102.5": "1131.00"}
SHIFTED |= {"1105": "1100.40"}


def test_prev_session_strikes_are_skipped() -> None:
    calls, puts = _chain(SHIFTED)
    before = synthetic_forward(calls, puts, 1100.0)
    assert before.F == 1100.4  # 중앙값이 어긋난 쪽으로 끌려간다
    res = synthetic_forward(calls, puts, 1100.0, prev_session_strikes=[D("1097.5"), D("1102.5")])
    assert res == ForwardResult(
        F=1100.2,
        quality="ok",  # 남은 3 개 — 정확히 3 개는 ok
        atm=D(1100),
        strikes=(D(1095), D(1100), D(1105)),
        futures_gap=None,
        reasons=(),
        prev_session_skipped=(D("1097.5"), D("1102.5")),
        notes=("no_futures_ref",),
    )


def test_prev_session_skipping_does_not_widen_window() -> None:
    # 창 밖(1092.5·1107.5)에 새 가격이 있어도 끌어오지 않는다 — 남은 2 개로 estimated
    calls, puts = _chain(SHIFTED | {"1092.5": "1100.00", "1107.5": "1100.00"})
    stale = [D(1095), D("1097.5"), D("1102.5")]
    res = synthetic_forward(calls, puts, 1100.0, prev_session_strikes=stale)
    assert res.strikes == (D(1100), D(1105))
    assert (res.F, res.quality, res.reasons) == (1100.3, "estimated", ("few_strikes",))
    assert res.prev_session_skipped == tuple(stale)


def test_all_prev_session_is_invalid() -> None:
    calls, puts = _chain(SHIFTED)
    res = synthetic_forward(calls, puts, 1100.0, prev_session_strikes=[D(k) for k in SHIFTED])
    assert (res.F, res.quality, res.reasons) == (None, "invalid", ("no_parity_strikes",))
    assert res.prev_session_skipped == tuple(sorted(D(k) for k in SHIFTED))


def test_prev_session_skipped_lists_only_usable_window_strikes() -> None:
    # 한쪽 가격이 없던 행사가·창 밖 행사가는 어차피 안 썼으니 '뺀 행사가' 가 아니다
    calls, puts = _chain(SHIFTED | {"1110": "1100.00"})
    puts[D(1095)] = None
    res = synthetic_forward(calls, puts, 1100.0, prev_session_strikes=[D(1095), D(1110)])
    assert res.prev_session_skipped == ()
    assert res.strikes == (D("1097.5"), D(1100), D("1102.5"), D(1105))


def test_prev_session_strikes_must_be_decimal() -> None:
    calls, puts = _chain(SHIFTED)
    with pytest.raises(TypeError):
        synthetic_forward(calls, puts, 1100.0, prev_session_strikes=[1100.0])  # pyright: ignore[reportArgumentType]


def test_forward_result_rejects_overlapping_skipped() -> None:
    with pytest.raises(ValueError):
        ForwardResult(1100.0, "ok", D(1100), (D(1100),), None, (), (D(1100),))


# --- 선물 교차 확인 (§1.3, 2026-09-28 사용자 결정 — 검증 수정 2) ---

THREE = {"1097.5": "1100.10", "1100": "1100.20", "1102.5": "1100.30"}  # 중앙값 F = 1100.20


def same(price: str) -> FuturesRef:
    """같은 결제월 선물가 기준가(분기 월물)."""
    return FuturesRef(D(price), "same_month")


def near(futures: str, basis: str) -> FuturesRef:
    """근월물 선물가 + 확정 베이시스 기준가."""
    return FuturesRef(D(futures) + D(basis), "near_basis", D(basis))


@pytest.mark.parametrize(
    ("fut", "quality", "gap"),
    [
        ("1099.70", "ok", 0.5),  # |F − 선물가| = 0.5 정확히 → ok (PLAN §5.1)
        ("1100.70", "ok", -0.5),
        ("1099.69", "estimated", 0.51),
        ("1100.71", "estimated", -0.51),
        ("1100.20", "ok", 0.0),
    ],
)
def test_quarterly_futures_gap_boundary(fut: str, quality: str, gap: float) -> None:
    calls, puts = _chain(THREE)
    res = synthetic_forward(calls, puts, 1100.0, reference=same(fut))
    assert (res.F, res.quality, res.futures_gap) == (1100.2, quality, gap)
    assert res.reasons == (() if quality == "ok" else ("futures_gap",))
    assert (res.reference, res.notes) == (same(fut), ())


@pytest.mark.parametrize(
    ("ref", "quality", "gap"),
    [
        ("1098.20", "ok", 2.0),  # |F − 기준가| = 2.0 정확히 → ok [확인 필요]
        ("1102.20", "ok", -2.0),
        ("1098.19", "estimated", 2.01),
        ("1102.21", "estimated", -2.01),
        ("1101.70", "ok", -1.5),  # 근월물 + 베이시스엔 0.5 규칙을 쓰지 않는다
    ],
)
def test_reference_gap_boundary(ref: str, quality: str, gap: float) -> None:
    calls, puts = _chain(THREE)
    reference = near("1105.00", str(D(ref) - D("1105.00")))
    res = synthetic_forward(calls, puts, 1103.0, reference=reference)
    assert (res.F, res.quality, res.futures_gap) == (1100.2, quality, gap)
    assert res.reasons == (() if quality == "ok" else ("futures_ref_gap",))
    assert res.reference == reference


@pytest.mark.parametrize(
    ("fut", "reasons"),
    [
        ("1099.20", ("futures_gap",)),  # 1.0 — 0.5 규칙만
        ("1098.20", ("futures_gap",)),  # 2.0 정확히 — 2pt 규칙은 ok
        ("1098.19", ("futures_gap", "futures_ref_gap")),  # 둘 다 선다
        ("1102.21", ("futures_gap", "futures_ref_gap")),
    ],
)
def test_same_month_applies_both_rules(fut: str, reasons: tuple[str, ...]) -> None:
    calls, puts = _chain(THREE)
    res = synthetic_forward(calls, puts, 1100.0, reference=same(fut))
    assert (res.quality, res.reasons) == ("estimated", reasons)


def test_reference_tolerances_are_parameters() -> None:
    calls, puts = _chain(THREE)
    res = synthetic_forward(calls, puts, 1100.0, reference=near("1100.00", "-1.30"))
    assert (res.quality, res.futures_gap) == ("ok", pytest.approx(1.5))
    tight = synthetic_forward(
        calls, puts, 1100.0, reference=near("1100.00", "-1.30"), ref_tolerance=1.0
    )
    assert (tight.quality, tight.reasons) == ("estimated", ("futures_ref_gap",))
    assert FUTURES_REF_TOLERANCE == D("2.0") and MIN_PARITY_STRIKES == 2
    with pytest.raises(ValueError):
        synthetic_forward(calls, puts, 1100.0, ref_tolerance=-0.1)


def test_combined_few_strikes_and_gap_reasons() -> None:
    # 2 개(대체 없음) — few_strikes 에 기준가 규칙이 더해진다
    calls, puts = _chain({"1100": "1100.20", "1102.5": "1100.20"})
    res = synthetic_forward(calls, puts, 1100.0, reference=same("1097.00"))
    assert (res.F, res.quality) == (1100.2, "estimated")
    assert res.reasons == ("few_strikes", "futures_gap", "futures_ref_gap")
    assert res.futures_gap == pytest.approx(3.2)


@pytest.mark.parametrize("n", [0, 1])
def test_fewer_than_two_falls_back_to_reference(n: int) -> None:
    # 쓸 행사가 0·1 개 → F = 기준가, estimated(futures_fallback) — 패리티 값은 버린다 [확인 필요]
    calls, puts = _chain(dict.fromkeys(["1100"][:n], "1130.00"))
    calls[D("1102.5")], puts[D("1102.5")] = D("5.00"), None  # 한쪽만 — 못 쓴다
    reference = near("1105.00", "-4.80")
    res = synthetic_forward(calls, puts, 1100.0, strikes=STRIKES, reference=reference)
    assert res == ForwardResult(
        F=1100.2,
        quality="estimated",
        atm=D(1100),
        strikes=(),
        futures_gap=None,
        reasons=("futures_fallback",),
        reference=reference,
    )


def test_two_strikes_do_not_fall_back() -> None:
    calls, puts = _chain({"1100": "1100.00", "1102.5": "1100.40"})
    res = synthetic_forward(calls, puts, 1100.0, reference=same("1100.20"))
    assert (res.F, res.strikes, res.reasons) == (1100.2, (D(1100), D("1102.5")), ("few_strikes",))


def test_min_parity_strikes_is_a_parameter() -> None:
    calls, puts = _chain({"1100": "1100.00", "1102.5": "1100.40"})
    res = synthetic_forward(calls, puts, 1100.0, reference=same("1101.00"), min_parity_strikes=3)
    assert (res.F, res.reasons) == (1101.0, ("futures_fallback",))
    with pytest.raises(ValueError):
        synthetic_forward(calls, puts, 1100.0, min_parity_strikes=0)


def test_without_reference_keeps_old_rules() -> None:
    # 기준가가 없으면 전과 같다 — 1 개는 그 행사가로 few_strikes, 0 개는 invalid
    calls, puts = _chain({"1100": "1100.40"})
    one = synthetic_forward(calls, puts, 1100.0)
    assert (one.F, one.quality, one.reasons) == (1100.4, "estimated", ("few_strikes",))
    assert (one.reference, one.notes) == (None, ("no_futures_ref",))
    none = synthetic_forward(calls, {D(1100): None}, 1100.0)
    assert (none.F, none.reasons, none.notes) == (None, ("no_parity_strikes",), ("no_futures_ref",))
    assert synthetic_forward({}, {}, 1100.0).notes == ("no_futures_ref",)


def test_no_strikes_with_reference_falls_back() -> None:
    res = synthetic_forward({}, {}, 1100.0, reference=same("1100.50"))
    assert (res.F, res.atm, res.reasons) == (1100.5, None, ("futures_fallback",))


def test_nonpositive_forward_stays_invalid_with_reference() -> None:
    # 방어선 — 행사가가 2 개 이상이면 대체하지 않는다
    calls = {D(5): D("0.01"), D(6): D("0.01")}
    puts = {D(5): D("9.00"), D(6): D("10.00")}
    res = synthetic_forward(calls, puts, 5.0, reference=same("5.00"))
    assert (res.F, res.reasons, res.reference) == (None, ("nonpositive_forward",), same("5.00"))


def test_prev_session_rows_count_before_fallback() -> None:
    # 전 세션 규칙(§1.1)으로 빼고 남은 수로 센다 — 4 개를 빼 1 개가 남으면 기준가로 대체
    calls, puts = _chain(SHIFTED)
    stale = [D(1095), D("1097.5"), D("1102.5"), D(1105)]
    res = synthetic_forward(
        calls, puts, 1100.0, prev_session_strikes=stale, reference=near("1105.00", "-5.00")
    )
    assert (res.F, res.reasons, res.strikes) == (1100.0, ("futures_fallback",), ())
    assert res.prev_session_skipped == tuple(stale)
    # 기준가가 없으면 남은 1100 하나로 few_strikes
    alone = synthetic_forward(calls, puts, 1100.0, prev_session_strikes=stale)
    assert (alone.F, alone.reasons) == (1100.2, ("few_strikes",))


def test_all_prev_session_falls_back_only_with_reference() -> None:
    # validation_greeks §5.6 WKM 261001 — ATM±2 전부 전 세션 가격
    calls, puts = _chain(SHIFTED)
    stale = [D(k) for k in SHIFTED]
    res = synthetic_forward(
        calls, puts, 1100.0, prev_session_strikes=stale, reference=near("1095.00", "4.50")
    )
    assert (res.F, res.quality, res.reasons) == (1099.5, "estimated", ("futures_fallback",))
    assert res.prev_session_skipped == tuple(sorted(stale))
    gone = synthetic_forward(calls, puts, 1100.0, prev_session_strikes=stale)
    assert (gone.F, gone.quality) == (None, "invalid")


# --- 기준가 만들기·확정 베이시스 ---


def test_futures_reference_same_month_for_quarterly_monthly() -> None:
    ref = futures_reference("202612", date(2026, 12, 10), 1100.0, same_month_futures=1101.25)
    assert ref == FuturesRef(D("1101.25"), "same_month")
    # 같은 결제월 선물가가 있으면 베이시스는 안 본다
    both = futures_reference(
        "202612", date(2026, 12, 10), 1100.0, same_month_futures=D("1101.25"), basis=D(-3)
    )
    assert both == ref


@pytest.mark.parametrize(
    ("expiry", "expiry_date"),
    [
        ("202610", date(2026, 10, 8)),  # 월물이지만 같은 결제월 선물이 없다
        ("261001", date(2026, 10, 1)),  # 위클리
        ("261201", date(2026, 12, 3)),  # 분기월의 위클리 — 선물 최종거래일과 다르다 [확인 필요]
    ],
)
def test_futures_reference_uses_near_plus_basis_otherwise(expiry: str, expiry_date: date) -> None:
    ref = futures_reference(expiry, expiry_date, 1092.5, basis=-5.8)
    assert ref == FuturesRef(D("1086.7"), "near_basis", D("-5.8"))
    with pytest.raises(ValueError):
        futures_reference(expiry, expiry_date, 1092.5, same_month_futures=1092.5)


def test_futures_reference_quarterly_without_same_month_uses_basis() -> None:
    # 202703 월물 — 같은 결제월(3월) 선물가가 없으면 근월물(12월) + 베이시스
    ref = futures_reference("202703", date(2027, 3, 11), D("1100.00"), basis=D("7.25"))
    assert ref == FuturesRef(D("1107.25"), "near_basis", D("7.25"))


def test_futures_reference_none_without_inputs() -> None:
    assert futures_reference("202610", date(2026, 10, 8), 1092.5) is None  # 베이시스 없음
    assert futures_reference("202610", date(2026, 10, 8), None, basis=-5.8) is None
    assert futures_reference("202612", date(2026, 12, 10), None) is None


def test_futures_reference_rejects_bad_prices() -> None:
    with pytest.raises(ValueError):
        futures_reference("202612", date(2026, 12, 10), 1100.0, same_month_futures=0)
    with pytest.raises(ValueError):
        futures_reference("202610", date(2026, 10, 8), 0.0, basis=-5.0)
    with pytest.raises(ValueError):
        futures_reference("202610", date(2026, 10, 8), 5.0, basis=-5.0)  # 기준가 0
    with pytest.raises(ValueError):
        futures_reference("202610", date(2026, 10, 8), 1100.0, basis=float("nan"))
    with pytest.raises(ValueError):
        futures_reference("2026-10", date(2026, 10, 8), 1100.0, basis=-5.0)


def test_futures_ref_invariants() -> None:
    with pytest.raises(TypeError):
        FuturesRef(1100.0, "same_month")  # pyright: ignore[reportArgumentType]
    for bad in (
        (D(0), "same_month", None),
        (D(1100), "same_month", D(1)),  # 같은 결제월엔 베이시스가 없다
        (D(1100), "near_basis", None),
        (D(1100), "spot", None),
        (D("NaN"), "same_month", None),
    ):
        with pytest.raises(ValueError):
            FuturesRef(*bad)  # pyright: ignore[reportArgumentType]


def test_confirm_basis_only_from_ok_forward() -> None:
    calls, puts = _chain(THREE)
    ok = synthetic_forward(calls, puts, 1100.0)  # 기준가 없음 — ok 라 첫 베이시스가 된다
    assert confirm_basis(ok, D("1105.00")) == D("-4.80")
    assert confirm_basis(ok, 1105.0, previous=D(-3)) == D("-4.80")
    few = synthetic_forward(*_chain({"1100": "1100.20"}), 1100.0)  # estimated
    fallback = synthetic_forward({}, {}, 1100.0, reference=same("1100.00"))
    far = synthetic_forward(calls, puts, 1100.0, reference=near("1105.00", "-2.00"))  # 2.8pt
    invalid = synthetic_forward({}, {}, 1100.0)
    for res in (few, fallback, far, invalid):
        assert res.quality != "ok"
        assert confirm_basis(res, D("1105.00"), previous=D("-5.10")) == D("-5.10")
        assert confirm_basis(res, D("1105.00")) is None
    with pytest.raises(ValueError):
        confirm_basis(ok, 0.0)


def test_confirmed_basis_round_trips_to_reference() -> None:
    # 확정 베이시스로 만든 기준가 = 그 F — 같은 근월물 선물가면 다음 스냅샷의 gap 이 0
    calls, puts = _chain(THREE)
    ok = synthetic_forward(calls, puts, 1100.0)
    basis = confirm_basis(ok, 1092.35)
    ref = futures_reference("202610", date(2026, 10, 8), 1092.35, basis=basis)
    assert ref is not None and float(ref.price) == ok.F
    again = synthetic_forward(calls, puts, 1100.0, reference=ref)
    assert (again.quality, again.futures_gap) == ("ok", 0.0)


# --- 만기 코드 ---


@pytest.mark.parametrize(
    ("expiry", "expiry_date", "expected"),
    [
        ("202609", date(2026, 9, 10), True),
        ("202612", date(2026, 12, 10), True),
        ("202703", date(2027, 3, 11), True),
        ("202610", date(2026, 10, 8), False),  # 월물이지만 분기월 아님
        ("260904", date(2026, 9, 28), False),  # 위클리(YYMMWW)
        ("261201", date(2026, 12, 3), False),  # 분기월의 위클리
        ("202609", date(2026, 10, 8), False),  # 코드와 만기일이 어긋나면 월물로 안 본다
    ],
)
def test_is_quarterly_monthly(expiry: str, expiry_date: date, expected: bool) -> None:
    assert is_quarterly_monthly(expiry, expiry_date) is expected


@pytest.mark.parametrize("bad", ["20260", "2026090", "2026-9", "abcdef"])
def test_is_quarterly_monthly_rejects_bad_code(bad: str) -> None:
    with pytest.raises(ValueError):
        is_quarterly_monthly(bad, date(2026, 9, 10))


# --- 입력 검증 ---


def test_rejects_bad_inputs() -> None:
    calls, puts = _chain({"1100": "1100.00"})
    with pytest.raises(ValueError):
        synthetic_forward(calls, puts, 0.0)  # KIS 의 값 없음 0 으로 ATM 을 고르지 않는다
    with pytest.raises(TypeError):
        synthetic_forward(calls, puts, 1100.0, reference=1100.0)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError):
        synthetic_forward(calls, puts, 1100.0, tolerance=-0.1)
    with pytest.raises(TypeError):
        synthetic_forward({D(1100): 5.0}, puts, 1100.0)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError):
        synthetic_forward({D(1100): D(0)}, puts, 1100.0)


def test_result_invariants() -> None:
    with pytest.raises(ValueError):
        ForwardResult(None, "ok", None, (), None, ())
    with pytest.raises(ValueError):
        ForwardResult(1100.0, "estimated", D(1100), (D(1100),), None, ())
    with pytest.raises(ValueError):
        ForwardResult(-1.0, "invalid", None, (), None, ("x",))
    ref = same("1100.00")
    for bad in (
        # 기준가 없이 gap 을 적었다
        {"futures_gap": 0.1, "reasons": ()},
        # 기준가가 있는데 'no_futures_ref'
        {"reasons": (), "reference": ref, "notes": ("no_futures_ref",)},
        # 대체인데 F 가 기준가가 아니다·행사가가 남았다·기준가가 없다
        {"F": 1100.5, "quality": "estimated", "reasons": ("futures_fallback",), "reference": ref},
        {
            "quality": "estimated",
            "strikes": (D(1100),),
            "reasons": ("futures_fallback",),
            "reference": ref,
        },
        {"quality": "estimated", "reasons": ("futures_fallback",)},
    ):
        base: dict[str, object] = {
            "F": 1100.0,
            "quality": "ok",
            "atm": D(1100),
            "strikes": (),
            "futures_gap": None,
        }
        with pytest.raises(ValueError):
            ForwardResult(**(base | bad))  # pyright: ignore[reportArgumentType]


# --- §1.4 잔존기간 T ---

EXPIRY = expiry_at(date(2026, 10, 8))  # 15:20 KST


def _kst(h: int, m: int, day: int = 8) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=KST)


@pytest.mark.parametrize(
    ("now", "minutes"),
    [
        (_kst(15, 20), 5),  # 정각 → 하한
        (_kst(15, 19), 5),  # 1분 전 → 하한
        (_kst(15, 15), 5),  # 5분 전 = 하한과 같음
        (_kst(15, 10), 10),
        (_kst(15, 21), 5),  # 만기 뒤 → 하한
        (_kst(15, 20, day=7), 24 * 60),  # 전날 같은 시각 — 야간 포함 달력 분
        (_kst(18, 0, day=2), 5 * 24 * 60 + 21 * 60 + 20),  # 금요일 밤 → 목요일, 주말 포함
    ],
)
def test_time_to_expiry(now: datetime, minutes: float) -> None:
    assert time_to_expiry(now, EXPIRY) == minutes / MINUTES_PER_YEAR


def test_time_to_expiry_any_timezone() -> None:
    now_utc = datetime(2026, 10, 8, 6, 20, tzinfo=UTC) - timedelta(minutes=30)  # 14:50 KST
    assert time_to_expiry(now_utc, EXPIRY) == 30 / MINUTES_PER_YEAR
    assert MINUTES_PER_YEAR == 365 * 24 * 60


def test_time_to_expiry_floor_parameter() -> None:
    assert time_to_expiry(_kst(15, 19), EXPIRY, floor_minutes=1) == 1 / MINUTES_PER_YEAR
    for bad in (0.0, -5.0, float("nan")):
        with pytest.raises(ValueError):
            time_to_expiry(_kst(15, 0), EXPIRY, floor_minutes=bad)


def test_time_to_expiry_rejects_naive() -> None:
    naive = datetime(2026, 10, 8, 15, 0)  # noqa: DTZ001 — 거부되는지 본다
    with pytest.raises(ValueError, match="naive"):
        time_to_expiry(naive, EXPIRY)
    with pytest.raises(ValueError, match="naive"):
        time_to_expiry(_kst(15, 0), naive)


# --- §1.7 KIS 잔존기간 관례 T_KIS (§1.5 KIS IV 폴백 환산용) ---


@pytest.mark.parametrize(
    ("now", "expiry_date", "days"),
    [
        # 2026-09-28 실측: 202610(10-08) 은 D = 10 = hts_rmnn_dynu 11 − 1
        (datetime(2026, 9, 28, 14, 27, tzinfo=KST), date(2026, 10, 8), 10),
        # WKM 261001(10-06): 10-03(토)·04(일)·05(대체공휴일)도 센다 — 거래일이면 5일
        (datetime(2026, 9, 28, 14, 27, tzinfo=KST), date(2026, 10, 6), 8),
        # 만기일(D = 0)은 시각과 무관하게 0.5일 — 개장·실측 두 시각·15:20 전후·밤
        (datetime(2026, 9, 28, 8, 45, tzinfo=KST), date(2026, 9, 28), 0.5),
        (datetime(2026, 9, 28, 14, 27, tzinfo=KST), date(2026, 9, 28), 0.5),
        (datetime(2026, 9, 28, 14, 52, tzinfo=KST), date(2026, 9, 28), 0.5),
        (datetime(2026, 9, 28, 15, 19, tzinfo=KST), date(2026, 9, 28), 0.5),
        (datetime(2026, 9, 28, 15, 30, tzinfo=KST), date(2026, 9, 28), 0.5),
        # 만기 전날(D = 1)은 1일 [확인 필요 — 미실측]
        (datetime(2026, 9, 30, 9, 0, tzinfo=KST), date(2026, 10, 1), 1),
        # 야간 세션은 now 의 KST 달력 날짜 — 자정 전 D = 1, 자정 뒤 D = 0 → 0.5일 [확인 필요]
        (datetime(2026, 9, 30, 23, 59, tzinfo=KST), date(2026, 10, 1), 1),
        (datetime(2026, 10, 1, 0, 0, tzinfo=KST), date(2026, 10, 1), 0.5),
    ],
)
def test_kis_time_to_expiry(now: datetime, expiry_date: date, days: float) -> None:
    assert kis_time_to_expiry(now, expiry_date) == days / 365


def test_kis_time_to_expiry_uses_kst_date() -> None:
    # 09-27 16:00 UTC = 09-28 01:00 KST, 09-27 14:59 UTC = 09-27 23:59 KST
    assert (
        kis_time_to_expiry(datetime(2026, 9, 27, 16, 0, tzinfo=UTC), date(2026, 10, 8)) == 10 / 365
    )
    assert (
        kis_time_to_expiry(datetime(2026, 9, 27, 14, 59, tzinfo=UTC), date(2026, 10, 8)) == 11 / 365
    )


def test_kis_time_equals_own_t_at_1520() -> None:
    """만기 D 일 전 15:20 KST 정각에는 자체 T(D × 1440분)와 T_KIS(D/365)가 같은 float 이다 —
    §1.5 폴백 환산의 항등 지점. 그 전엔 자체 T 가 길고(장중), 뒤엔 짧다(15:20 뒤·야간)."""
    for d in (1, 3, 10, 45):
        now = datetime(2026, 10, 8, 15, 20, tzinfo=KST) - timedelta(days=d)
        assert time_to_expiry(now, EXPIRY) == kis_time_to_expiry(now, date(2026, 10, 8)) == d / 365
    assert time_to_expiry(_kst(9, 0, day=7), EXPIRY) > kis_time_to_expiry(
        _kst(9, 0, day=7), EXPIRY.date()
    )
    assert time_to_expiry(_kst(20, 0, day=7), EXPIRY) < kis_time_to_expiry(
        _kst(20, 0, day=7), EXPIRY.date()
    )


def test_kis_time_to_expiry_rejects() -> None:
    with pytest.raises(ValueError, match="만기 지남"):
        kis_time_to_expiry(datetime(2026, 10, 9, 0, 0, tzinfo=KST), date(2026, 10, 8))
    with pytest.raises(ValueError, match="naive"):
        kis_time_to_expiry(datetime(2026, 9, 28, 14, 27), date(2026, 10, 8))  # noqa: DTZ001
    with pytest.raises(TypeError, match="date"):
        kis_time_to_expiry(_kst(9, 0, day=1), EXPIRY)


# ── S_ref 출처 (2026-09-29 사용자 결정 — 야간은 단건 CM, 전광판 F 는 야간에 주간 종가 고정 #19) ──

NOW = datetime(2026, 9, 29, 21, 0, tzinfo=KST)
DAY_NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)


def _q(
    ts: datetime,
    session: str,
    market: str,
    source: str,
    price: str | None = "1100.00",
    *,
    code: str = "A01612",
    quality: str = "ok",
) -> FuturesQuote:
    return FuturesQuote(
        ts=ts,
        session=session,  # type: ignore[arg-type]
        market=market,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        code=code,
        price=None if price is None else D(price),
        quality=quality,  # type: ignore[arg-type]
    )


def test_s_ref_source_table_is_board_f_by_day_and_single_cm_at_night() -> None:
    assert S_REF_SOURCE == {"day": ("F", "board"), "night": ("CM", "single")}
    assert S_REF_STALE_AFTER_S == 90.0  # PLAN §6.1 — 90초 안 갱신이면 ok


def test_night_uses_single_cm_even_when_the_frozen_day_board_is_newer() -> None:
    frozen = _q(NOW - timedelta(seconds=5), "night", "F", "board", "1126.00")  # 주간 종가 고정
    cm = _q(NOW - timedelta(seconds=20), "night", "CM", "single", "1098.40")
    got = select_s_ref([frozen, cm], session="night", code="A01612", now=NOW)
    assert got is not None
    assert (got.price, got.quality, got.quote) == (D("1098.40"), "ok", cm)


def test_night_without_cm_quote_is_none_not_the_day_board() -> None:
    rows = [
        _q(NOW - timedelta(seconds=5), "night", "F", "board"),
        _q(NOW - timedelta(hours=6), "day", "F", "board"),
        _q(NOW - timedelta(hours=6), "day", "CM", "single"),  # 세션이 다르다
    ]
    assert select_s_ref(rows, session="night", code="A01612", now=NOW) is None


def test_day_uses_board_f_and_ignores_cm_single() -> None:
    board = _q(DAY_NOW - timedelta(seconds=30), "day", "F", "board", "1101.05")
    cm = _q(DAY_NOW - timedelta(seconds=1), "day", "CM", "single", "1099.00")
    got = select_s_ref([cm, board], session="day", code="A01612", now=DAY_NOW)
    assert got is not None and got.price == D("1101.05") and got.quote is board


def test_latest_valid_quote_of_the_code_wins() -> None:
    rows = [
        _q(NOW - timedelta(seconds=40), "night", "CM", "single", "1098.00"),
        _q(NOW - timedelta(seconds=10), "night", "CM", "single", "1098.60"),
        _q(NOW - timedelta(seconds=1), "night", "CM", "single", "1200.00", code="A01703"),
        _q(NOW - timedelta(seconds=2), "night", "CM", "single", None),  # 값 없음
        _q(NOW - timedelta(seconds=3), "night", "CM", "single", quality="invalid"),
        _q(NOW + timedelta(seconds=5), "night", "CM", "single", "1099.90"),  # 미래 행
    ]
    got = select_s_ref(rows, session="night", code="A01612", now=NOW)
    assert got is not None and got.price == D("1098.60")


def test_quote_older_than_90s_is_stale_boundary_inclusive() -> None:
    at_90 = _q(NOW - timedelta(seconds=90), "night", "CM", "single")
    got = select_s_ref([at_90], session="night", code="A01612", now=NOW)
    assert got is not None and got.quality == "ok"
    past = _q(NOW - timedelta(seconds=90, milliseconds=1), "night", "CM", "single")
    got = select_s_ref([past], session="night", code="A01612", now=NOW)
    assert got is not None and got.quality == "stale"


def test_row_quality_worse_than_age_is_kept() -> None:
    est = _q(NOW - timedelta(seconds=10), "night", "CM", "single", quality="estimated")
    got = select_s_ref([est], session="night", code="A01612", now=NOW)
    assert got is not None and got.quality == "estimated"
    old_est = _q(NOW - timedelta(seconds=200), "night", "CM", "single", quality="estimated")
    got = select_s_ref([old_est], session="night", code="A01612", now=NOW)
    assert got is not None and got.quality == "estimated"


def test_s_ref_rejects_naive_times_and_bad_prices() -> None:
    with pytest.raises(ValueError, match="naive"):
        select_s_ref([], session="night", code="A01612", now=datetime(2026, 9, 29, 21))  # noqa: DTZ001
    with pytest.raises(ValueError, match="naive"):
        _q(datetime(2026, 9, 29, 21), "night", "CM", "single")  # noqa: DTZ001
    with pytest.raises(ValueError):
        _q(NOW, "night", "CM", "single", "0")
    with pytest.raises(ValueError):
        _q(NOW, "evening", "CM", "single")


# ── 베이시스 이월 상한 (2026-09-29 사용자 결정 — 최대 2거래일 [확인 필요]) ──

# 월물 202610(분기 월물 아님) — 기준가 = 근월물 + 확정 베이시스
EXP, EXP_DATE = "202610", date(2026, 10, 8)


def _carried(age: int, basis: str = "-5.00", near: str = "1105.00") -> FuturesRef | None:
    return futures_reference(EXP, EXP_DATE, D(near), basis=D(basis), basis_age=age)


def test_max_basis_carry_is_two_trading_days() -> None:
    assert MAX_BASIS_CARRY_DAYS == 2


def test_futures_reference_marks_carried_and_expired_basis() -> None:
    same = _carried(0)
    assert same == FuturesRef(D("1100.00"), "near_basis", D("-5.00"))
    assert same is not None and (same.carried_days, same.expired) == (0, False)
    two = _carried(2)
    assert two is not None and (two.carried_days, two.expired) == (2, False)
    three = _carried(3)
    assert three is not None and (three.carried_days, three.expired) == (3, True)
    # 같은 결제월 선물가는 베이시스를 안 쓰니 나이와 무관
    q = futures_reference(
        "202612", date(2026, 12, 10), D("1105"), same_month_futures=D("1101"), basis_age=9
    )
    assert q == FuturesRef(D("1101"), "same_month")
    with pytest.raises(ValueError):
        _carried(-1)
    with pytest.raises(ValueError):
        futures_reference(EXP, EXP_DATE, D("1105"), basis=D("-5"), max_basis_carry=-1)


def test_futures_ref_field_rules_for_carry() -> None:
    with pytest.raises(ValueError):
        FuturesRef(D("1100"), "same_month", carried_days=1)
    with pytest.raises(ValueError):
        FuturesRef(D("1100"), "near_basis", D("-5"), carried_days=0, expired=True)
    with pytest.raises(ValueError):
        FuturesRef(D("1100"), "near_basis", D("-5"), carried_days=-1)
    with pytest.raises(TypeError):
        FuturesRef(D("1100"), "near_basis", D("-5"), carried_days=True)


@pytest.mark.parametrize("age", [1, 2])
def test_fallback_with_carried_basis_records_basis_carried(age: int) -> None:
    got = synthetic_forward({}, {}, D("1105"), strikes=STRIKES, reference=_carried(age))
    assert got.F == 1100.0
    assert (got.quality, got.reasons) == ("estimated", ("futures_fallback", "basis_carried"))


def test_fallback_with_same_day_basis_has_no_basis_carried() -> None:
    got = synthetic_forward({}, {}, D("1105"), strikes=STRIKES, reference=_carried(0))
    assert got.reasons == ("futures_fallback",)


@pytest.mark.parametrize("n_strikes", [0, 1])
def test_expired_carry_does_not_fall_back_and_is_invalid(n_strikes: int) -> None:
    parity = {"1105.0": "1100.00"} if n_strikes else {}
    calls, puts = _chain(parity)
    ref = _carried(3)
    got = synthetic_forward(calls, puts, D("1105"), strikes=STRIKES, reference=ref)
    assert got.F is None and got.quality == "invalid"
    assert got.reasons == ("basis_carry_expired",)
    assert got.reference == ref and got.notes == ("basis_carry_expired",)


def test_expired_carry_skips_the_cross_check_when_parity_is_enough() -> None:
    calls, puts = _chain({"1100.0": "1110.00", "1102.5": "1110.00", "1105.0": "1110.00"})
    got = synthetic_forward(calls, puts, D("1105"), strikes=STRIKES, reference=_carried(3))
    # 기준가 1100 과 10pt 차이지만 만료된 베이시스라 비교하지 않는다
    assert (got.F, got.quality, got.reasons) == (1110.0, "ok", ())
    assert got.futures_gap is None and got.notes == ("basis_carry_expired",)


def test_cross_check_with_carried_basis_notes_it_but_keeps_quality() -> None:
    calls, puts = _chain({"1100.0": "1101.00", "1102.5": "1101.00", "1105.0": "1101.00"})
    got = synthetic_forward(calls, puts, D("1105"), strikes=STRIKES, reference=_carried(1))
    assert (got.quality, got.reasons, got.notes) == ("ok", (), ("basis_carried",))
    assert got.futures_gap == pytest.approx(1.0)
    calls, puts = _chain({"1100.0": "1103.00", "1102.5": "1103.00", "1105.0": "1103.00"})
    got = synthetic_forward(calls, puts, D("1105"), strikes=STRIKES, reference=_carried(2))
    assert got.reasons == ("futures_ref_gap",) and got.notes == ("basis_carried",)


# ── 베이시스 나이 (2026-09-29 사용자 결정 — 같은 저녁의 야간은 이월로 보지 않는다) ──

XKRX = TradingCalendar()  # override 파일과 무관하게 고정
MON, TUE, WED, THU = date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)


@pytest.mark.parametrize(
    ("trade_date", "session", "age", "expired"),
    [
        (MON, "day", 0, False),  # 확정한 세션 그대로
        (TUE, "night", 0, False),  # 월 야간(귀속 화) — 바로 이어지는 야간은 이월 아님
        (TUE, "day", 1, False),
        (WED, "night", 1, False),  # 화 야간 — 화 주간과 같은 나이
        (WED, "day", 2, False),
        (THU, "night", 2, False),  # 수 야간
        (THU, "day", 3, True),  # 만료
    ],
)
def test_basis_age_monday_day_confirmation(
    trade_date: date, session: str, age: int, expired: bool
) -> None:
    got = basis_age(XKRX, MON, "day", trade_date, session)  # type: ignore[arg-type]
    assert got == age
    ref = futures_reference(EXP, EXP_DATE, D("1105"), basis=D("-5"), basis_age=got)
    assert ref is not None and ref.expired is expired


def test_basis_age_friday_night_belongs_to_friday() -> None:
    fri, mon = date(2026, 9, 18), date(2026, 9, 21)  # 금 야간 → 월 귀속
    assert basis_age(XKRX, fri, "day", mon, "night") == 0
    assert basis_age(XKRX, fri, "day", mon, "day") == 1


def test_basis_age_night_confirmation_counts_the_next_day_session() -> None:
    # 월 야간(귀속 화)에 확정 → 월의 저녁이라 화 주간은 1, 화 야간도 1
    assert basis_age(XKRX, TUE, "night", TUE, "night") == 0
    assert basis_age(XKRX, TUE, "night", TUE, "day") == 1
    assert basis_age(XKRX, TUE, "night", WED, "night") == 1


def test_basis_age_skips_holidays_and_rejects_going_back() -> None:
    wed_before, mon_after = date(2026, 9, 23), date(2026, 9, 28)  # 추석 09-24·25 휴장
    assert basis_age(XKRX, wed_before, "day", mon_after, "day") == 1
    with pytest.raises(ValueError):
        basis_age(XKRX, TUE, "day", MON, "day")
    with pytest.raises(ValueError):
        basis_age(XKRX, date(2026, 9, 24), "day", MON, "day")  # 휴장일 귀속은 없다

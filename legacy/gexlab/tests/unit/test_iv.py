"""core.iv — 역산 왕복과 폴백 경로 (docs/metrics.md §1.2·§1.5·§1.7, PLAN §6.1)."""

import math
from datetime import date, datetime

import pytest

from core import black76
from core.black76 import Flag
from core.calendar import KST, expiry_at
from core.forward import kis_time_to_expiry, time_to_expiry
from core.greeks import greeks
from core.iv import IV_MAX, MIN_PREMIUM, IvResult, implied_vol, rescale_sigma, usable

MINUTE = 1 / (365 * 24 * 60)
DAY = 1 / 365


def test_hull_example_round_trip() -> None:
    # Hull 7판 예제 16.6: F = K = 20, r = 9%, T = 4개월, σ = 25% → 1.12
    for flag in ("c", "p"):
        p = black76.price(flag, 20.0, 20.0, 4 / 12, 0.25, r=0.09)
        res = implied_vol(p, 20.0, 20.0, 4 / 12, flag, r=0.09)
        assert (res.quality, res.source, res.reason) == ("ok", "model", None)
        assert res.sigma == pytest.approx(0.25, rel=1e-12)


@pytest.mark.parametrize(
    ("flag", "k", "t", "sigma", "r"),
    [
        ("c", 1100.0, 5 * MINUTE, 0.2, 0.0),  # 만기 직전 ATM(T 하한 5분)
        ("p", 1097.5, DAY, 0.15, 0.0),
        ("c", 1150.0, 7 * DAY, 0.18, 0.0),  # OTM 콜
        ("p", 1000.0, 30 * DAY, 0.25, 0.0),  # OTM 풋
        ("c", 1050.0, 30 * DAY, 0.2, 0.035),  # ITM 콜, r > 0
        ("p", 1150.0, 7 * DAY, 0.3, 0.0),  # ITM 풋
        ("c", 1300.0, 0.5, 1.5, 0.0),  # 고변동성
        ("p", 1100.0, 0.25, 2.9, 0.0),  # 300% 바로 아래
    ],
)
def test_round_trip_price_iv_price(flag: Flag, k: float, t: float, sigma: float, r: float) -> None:
    f = 1100.0
    p = black76.price(flag, f, k, t, sigma, r=r)
    assert p >= MIN_PREMIUM
    res = implied_vol(p, f, k, t, flag, r=r)
    assert (res.quality, res.source, res.reason) == ("ok", "model", None)
    assert res.sigma is not None
    assert math.isclose(res.sigma, sigma, rel_tol=1e-9)
    assert math.isclose(black76.price(flag, f, k, t, res.sigma, r=r), p, rel_tol=1e-12)


# --- 프리미엄 하한 (§1.2) ---


def test_min_premium_boundary_is_inclusive() -> None:
    # 0.02pt 는 계산한다(0.02 미만만 제외)
    res = implied_vol(0.02, 1100.0, 1200.0, 7 * DAY, "c")
    assert res.quality == "ok"
    assert res.sigma is not None and 0 < res.sigma < IV_MAX
    assert res.below_min_premium is False


def test_below_min_premium_falls_back_to_kis_but_stays_excluded() -> None:
    # KIS IV 로 sigma 는 채워지지만(표시용) §1.2 제외 플래그는 선다 — GEX·그릭스는 이걸로 거른다
    res = implied_vol(0.019, 1100.0, 1200.0, 7 * DAY, "c", kis_iv_pct=25.0)
    assert res == IvResult(
        sigma=0.25,
        quality="estimated",
        source="kis",
        reason="below_min_premium",
        below_min_premium=True,
    )


def test_below_min_premium_without_kis_is_invalid() -> None:
    res = implied_vol(0.019, 1100.0, 1200.0, 7 * DAY, "c")
    assert res == IvResult(
        sigma=None,
        quality="invalid",
        source=None,
        reason="below_min_premium/kis_missing",
        below_min_premium=True,
    )


def test_min_premium_is_a_parameter() -> None:
    res = implied_vol(0.03, 1100.0, 1200.0, 7 * DAY, "c", min_premium=0.05)
    assert (res.reason, res.below_min_premium) == ("below_min_premium/kis_missing", True)
    res = implied_vol(0.01, 1100.0, 1200.0, 7 * DAY, "c", min_premium=0.0)
    assert (res.quality, res.below_min_premium) == ("ok", False)


@pytest.mark.parametrize("p", [-0.5, 0.0])
def test_negative_or_zero_price_is_below_min_premium(p: float) -> None:
    res = implied_vol(p, 1100.0, 1100.0, 7 * DAY, "p")
    assert (res.reason, res.below_min_premium) == ("below_min_premium/kis_missing", True)


@pytest.mark.parametrize(
    ("p", "k"),
    [
        (math.nan, 1100.0),  # invalid_price
        (49.0, 1050.0),  # below_intrinsic
        (1100.0, 1050.0),  # above_max
    ],
)
def test_other_fallbacks_are_not_premium_excluded(p: float, k: float) -> None:
    res = implied_vol(p, 1100.0, k, 7 * DAY, "c", kis_iv_pct=20.0)
    assert res.quality == "estimated"
    assert res.below_min_premium is False


@pytest.mark.parametrize("p", [math.nan, math.inf])
def test_non_finite_price(p: float) -> None:
    res = implied_vol(p, 1100.0, 1100.0, 7 * DAY, "c", kis_iv_pct=20.0)
    assert (res.sigma, res.quality, res.reason) == (0.2, "estimated", "invalid_price")


# --- 무차익 범위 밖 ---


@pytest.mark.parametrize(
    ("flag", "k", "p"),
    [
        ("c", 1050.0, 49.9),  # 내재가치 50 아래
        ("c", 1050.0, 50.0),  # 내재가치와 같음 — 시간가치 없음
        ("p", 1150.0, 49.5),
    ],
)
def test_below_intrinsic_falls_back(flag: Flag, k: float, p: float) -> None:
    res = implied_vol(p, 1100.0, k, 7 * DAY, flag, kis_iv_pct=21.5)
    assert res == IvResult(sigma=0.215, quality="estimated", source="kis", reason="below_intrinsic")
    assert implied_vol(p, 1100.0, k, 7 * DAY, flag).reason == "below_intrinsic/kis_missing"


def test_intrinsic_is_discounted_when_r_positive() -> None:
    # r > 0 이면 하한은 e^(−rT)·(F − K) = 49.86 — 49.99 는 내재가치 위라 역산된다
    t, r = 30 * DAY, 0.035
    assert black76.intrinsic("c", 1100.0, 1050.0, t, r) < 49.99 < 50.0
    assert implied_vol(49.99, 1100.0, 1050.0, t, "c", r=r).quality == "ok"


@pytest.mark.parametrize(("flag", "p"), [("c", 1100.0), ("c", 1200.0), ("p", 1050.0)])
def test_above_max_falls_back(flag: Flag, p: float) -> None:
    # 상한: 콜 F = 1100, 풋 K = 1050
    res = implied_vol(p, 1100.0, 1050.0, 7 * DAY, flag, kis_iv_pct=30.0)
    assert (res.sigma, res.quality, res.reason) == (0.3, "estimated", "above_max")


# --- §6.1 이상치 (IV ≤ 0, > 300%) ---


OUTLIER = IvResult(sigma=None, quality="invalid", source=None, reason="model_out_of_range")


@pytest.mark.parametrize("kis", [None, 40.0])
def test_model_iv_above_300pct_is_invalid_without_kis_fallback(kis: float | None) -> None:
    # 역산 결과 이상치는 역산 실패가 아니다 — KIS 값이 있어도 덮지 않는다(metrics §1.5)
    p = black76.price("c", 1100.0, 1100.0, 0.1, 3.5)
    assert implied_vol(p, 1100.0, 1100.0, 0.1, "c", kis_iv_pct=kis) == OUTLIER


@pytest.mark.parametrize("bad", [0.0, -0.1, 3.0001, math.inf, -math.inf])
def test_model_result_out_of_range_is_invalid(monkeypatch: pytest.MonkeyPatch, bad: float) -> None:
    monkeypatch.setattr("core.iv._vl_implied_volatility", lambda *_: bad)
    assert implied_vol(20.0, 1100.0, 1100.0, 7 * DAY, "c", kis_iv_pct=18.0) == OUTLIER
    assert implied_vol(20.0, 1100.0, 1100.0, 7 * DAY, "c") == OUTLIER


def test_model_result_at_300pct_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.iv._vl_implied_volatility", lambda *_: 3.0)
    res = implied_vol(20.0, 1100.0, 1100.0, 7 * DAY, "c", kis_iv_pct=18.0)
    assert (res.sigma, res.quality, res.source) == (3.0, "ok", "model")


def test_model_exception_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object) -> float:
        raise RuntimeError("수렴 실패")

    monkeypatch.setattr("core.iv._vl_implied_volatility", boom)
    res = implied_vol(20.0, 1100.0, 1100.0, 7 * DAY, "c", kis_iv_pct=18.0)
    assert (res.sigma, res.quality, res.reason) == (0.18, "estimated", "model_error")


def test_model_nan_is_convergence_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # nan 은 이상치가 아니라 수렴 실패 → KIS 폴백(metrics §1.5 model_error)
    monkeypatch.setattr("core.iv._vl_implied_volatility", lambda *_: math.nan)
    res = implied_vol(20.0, 1100.0, 1100.0, 7 * DAY, "c", kis_iv_pct=18.0)
    assert (res.sigma, res.quality, res.reason) == (0.18, "estimated", "model_error")
    assert implied_vol(20.0, 1100.0, 1100.0, 7 * DAY, "c").reason == "model_error/kis_missing"


# --- KIS 폴백 값 ---


@pytest.mark.parametrize(
    ("kis", "expected"),
    [
        (None, (None, "invalid", "below_intrinsic/kis_missing")),
        (0.0, (None, "invalid", "below_intrinsic/kis_missing")),  # 0 은 없음
        (math.nan, (None, "invalid", "below_intrinsic/kis_missing")),
        (-5.0, (None, "invalid", "below_intrinsic/kis_out_of_range")),
        (300.01, (None, "invalid", "below_intrinsic/kis_out_of_range")),
        (math.inf, (None, "invalid", "below_intrinsic/kis_out_of_range")),
        (300.0, (3.0, "estimated", "below_intrinsic")),  # 300% 까지는 쓴다
        (0.5, (0.005, "estimated", "below_intrinsic")),
    ],
)
def test_kis_fallback_values(kis: float | None, expected: tuple[float | None, str, str]) -> None:
    res = implied_vol(49.0, 1100.0, 1050.0, 7 * DAY, "c", kis_iv_pct=kis)
    assert (res.sigma, res.quality, res.reason) == expected
    assert res.source == ("kis" if res.quality == "estimated" else None)


def test_kis_value_ignored_when_model_succeeds() -> None:
    p = black76.price("c", 1100.0, 1110.0, 7 * DAY, 0.2)
    res = implied_vol(p, 1100.0, 1110.0, 7 * DAY, "c", kis_iv_pct=99.0)
    assert res.source == "model"
    assert res.sigma == pytest.approx(0.2, rel=1e-9)


# --- KIS IV 폴백의 T 환산 (§1.5 — 2026-09-28 사용자 결정, 검증 수정 3) ---

# 0DTE 실측(validation_greeks §5.3): WKM 260904 1102.5 풋 — 14:27 에 15:20 까지 53분, KIS IV 16.03%
EXP_0DTE = date(2026, 9, 28)
NOW_0DTE = datetime(2026, 9, 28, 14, 27, tzinfo=KST)
T_0DTE = time_to_expiry(NOW_0DTE, expiry_at(EXP_0DTE))  # 53분
T_KIS_0DTE = kis_time_to_expiry(NOW_0DTE, EXP_0DTE)  # 0.5일


def test_rescale_sigma_preserves_total_variance() -> None:
    assert rescale_sigma(0.16, 0.5 * DAY, 0.125 * DAY) == pytest.approx(0.32)
    s = rescale_sigma(0.16, T_KIS_0DTE, T_0DTE)
    assert s * s * T_0DTE == pytest.approx(0.16 * 0.16 * T_KIS_0DTE, rel=1e-14)
    assert rescale_sigma(0.16, 7 * DAY, 7 * DAY) == 0.16  # 같은 T 면 그대로


@pytest.mark.parametrize(
    ("sigma", "t_from", "t_to"),
    [
        (0.0, DAY, DAY),
        (-0.1, DAY, DAY),
        (0.2, 0.0, DAY),
        (0.2, DAY, -DAY),
        (math.nan, DAY, DAY),
        (0.2, math.inf, DAY),
        (0.2, DAY, math.nan),
    ],
)
def test_rescale_sigma_rejects(sigma: float, t_from: float, t_to: float) -> None:
    with pytest.raises(ValueError, match="유한한 양수"):
        rescale_sigma(sigma, t_from, t_to)


def test_0dte_fallback_moves_kis_sigma_to_own_t() -> None:
    """0DTE: T_KIS 0.5일 vs 자체 T 53분. σ = σ_KIS·√(T_KIS/T) — 그대로 쓰면 감마가 사실상 0 이다."""
    assert (T_0DTE, T_KIS_0DTE) == (53 * MINUTE, 0.5 * DAY)
    F, K = 1094.56, 1102.5
    res = implied_vol(6.15, F, K, T_0DTE, "p", kis_iv_pct=16.0286, t_kis=T_KIS_0DTE)
    assert (res.quality, res.source, res.reason) == ("estimated", "kis", "below_intrinsic")
    assert (res.rescaled, res.t_kis, res.below_min_premium) == (True, 0.5 * DAY, False)
    assert res.sigma is not None
    assert res.sigma == pytest.approx(0.160286 * math.sqrt(12 * 60 / 53))
    ours, at_kis_t = greeks("p", F, K, T_0DTE, res.sigma), greeks("p", F, K, T_KIS_0DTE, 0.160286)
    assert ours.gamma == pytest.approx(at_kis_t.gamma, rel=1e-9)
    assert ours.delta == pytest.approx(at_kis_t.delta, rel=1e-9)
    assert greeks("p", F, K, T_0DTE, 0.160286).gamma < 1e-4  # 옮기기 전(validation §5.3 −100%)


@pytest.mark.parametrize("minutes", [53, 27, 10, 6, 5])
def test_fallback_gamma_holds_as_own_t_shrinks(minutes: int) -> None:
    """T_self → 0(하한 5분까지): 옮긴 σ 는 1/√T 로 커지고 σ√T 는 σ_KIS·√T_KIS 그대로라 델타·감마가
    (σ_KIS, T_KIS) 값에 머문다 — 무너지지도(−100%) 튀지도 않는다."""
    t = minutes * MINUTE
    res = implied_vol(6.15, 1094.56, 1102.5, t, "p", kis_iv_pct=16.0286, t_kis=T_KIS_0DTE)
    assert res.sigma is not None and res.rescaled
    assert res.sigma * math.sqrt(t) == pytest.approx(0.160286 * math.sqrt(T_KIS_0DTE), rel=1e-12)
    g = greeks("p", 1094.56, 1102.5, t, res.sigma).gamma
    assert g == pytest.approx(greeks("p", 1094.56, 1102.5, T_KIS_0DTE, 0.160286).gamma, rel=1e-9)


@pytest.mark.parametrize(
    ("kis", "expected"),
    [
        (24.0, ("estimated", "below_intrinsic")),  # × 12 = 288%
        (26.0, ("invalid", "below_intrinsic/kis_out_of_range_rescaled")),  # × 12 = 312% > 300%
    ],
)
def test_outlier_gate_applies_after_rescaling_at_t_floor(
    kis: float, expected: tuple[str, str]
) -> None:
    """T 하한 5분에서 T_KIS 0.5일이면 √(720/5) = 12 배 — §6.1 이상치(> 300%)는 옮긴 σ 로 본다
    [확인 필요]. 옮기기 전 값(24·26%)은 둘 다 범위 안이다. 옮긴 탓에 invalid 가 된 행도 폴백을
    시도해 옮겼다는 기록(t_kis·rescaled)을 남기고, 사유는 `kis_out_of_range_rescaled` 다."""
    res = implied_vol(49.0, 1100.0, 1050.0, 5 * MINUTE, "c", kis_iv_pct=kis, t_kis=0.5 * DAY)
    assert (res.quality, res.reason) == expected
    assert (res.rescaled, res.t_kis) == (True, 0.5 * DAY)
    if res.sigma is not None:
        assert res.sigma == pytest.approx(kis / 100 * 12)


def test_rescale_caused_invalid_differs_from_raw_out_of_range() -> None:
    """KIS 값 자체가 > 300% 면 옮기든 말든 `kis_out_of_range` — 옮긴 탓(`…_rescaled`)과 가른다.
    옮겼으면(T_KIS 가 있고 자체 T 와 다름) 그 기록은 남는다."""
    args = (49.0, 1100.0, 1050.0, 5 * MINUTE, "c")
    raw = implied_vol(*args, kis_iv_pct=310.0)
    assert (raw.quality, raw.reason) == ("invalid", "below_intrinsic/kis_out_of_range")
    assert (raw.rescaled, raw.t_kis) == (False, None)
    moved = implied_vol(*args, kis_iv_pct=310.0, t_kis=0.5 * DAY)
    assert (moved.quality, moved.reason) == ("invalid", "below_intrinsic/kis_out_of_range")
    assert (moved.rescaled, moved.t_kis) == (True, 0.5 * DAY)
    by_rescale = implied_vol(*args, kis_iv_pct=26.0, t_kis=0.5 * DAY)
    assert by_rescale.reason == "below_intrinsic/kis_out_of_range_rescaled"
    assert by_rescale != moved and by_rescale != raw


def test_outlier_gate_uses_rescaled_sigma_when_own_t_is_longer() -> None:
    """자체 T 가 T_KIS 보다 길면(만기일 새벽 야간: 15:20 까지 13시간 20분 > 0.5일) σ 가 줄어든다 —
    KIS 310% 도 옮기면 294% 라 쓴다. 옮기지 않으면(t_kis 없음) 전처럼 > 300% 로 invalid."""
    now = datetime(2026, 9, 28, 2, 0, tzinfo=KST)
    t = time_to_expiry(now, expiry_at(EXP_0DTE))
    t_kis = kis_time_to_expiry(now, EXP_0DTE)
    assert (t, t_kis) == (800 * MINUTE, 0.5 * DAY)
    res = implied_vol(49.0, 1100.0, 1050.0, t, "c", kis_iv_pct=310.0, t_kis=t_kis)
    assert (res.quality, res.rescaled) == ("estimated", True)
    assert res.sigma == pytest.approx(3.1 * math.sqrt(720 / 800))
    old = implied_vol(49.0, 1100.0, 1050.0, t, "c", kis_iv_pct=310.0)
    assert (old.quality, old.reason) == ("invalid", "below_intrinsic/kis_out_of_range")


@pytest.mark.parametrize("kis", [-5.0, math.inf, -math.inf])
def test_negative_or_infinite_kis_is_out_of_range_before_rescaling(kis: float) -> None:
    res = implied_vol(49.0, 1100.0, 1050.0, 5 * MINUTE, "c", kis_iv_pct=kis, t_kis=0.5 * DAY)
    assert (res.quality, res.reason) == ("invalid", "below_intrinsic/kis_out_of_range")
    assert (res.rescaled, res.t_kis) == (False, None)  # 옮길 수 있는 σ 가 아니라 옮기지 않았다


def test_identity_when_t_kis_equals_own_t() -> None:
    res = implied_vol(49.0, 1100.0, 1050.0, 7 * DAY, "c", kis_iv_pct=21.5, t_kis=7 * DAY)
    assert res == IvResult(0.215, "estimated", "kis", "below_intrinsic", False, False, 7 * DAY)


def test_no_rescale_when_inversion_succeeds() -> None:
    p = black76.price("c", 1100.0, 1100.0, T_0DTE, 0.5)
    res = implied_vol(p, 1100.0, 1100.0, T_0DTE, "c", kis_iv_pct=16.0, t_kis=T_KIS_0DTE)
    assert (res.quality, res.source, res.rescaled, res.t_kis) == ("ok", "model", False, None)
    assert res.sigma == pytest.approx(0.5, rel=1e-9)


def test_fallback_without_t_kis_keeps_kis_sigma() -> None:
    # T_KIS 를 모르면(호출 쪽이 안 줌 — 만기일이 지난 만기 등) 옮기지 않고 그 사실을 남긴다
    res = implied_vol(6.15, 1094.56, 1102.5, T_0DTE, "p", kis_iv_pct=16.0286)
    assert (res.sigma, res.source, res.rescaled, res.t_kis) == (0.160286, "kis", False, None)


def test_below_min_premium_display_sigma_is_rescaled_too() -> None:
    # §1.2 제외 종목의 표시용 KIS σ 도 같은 T 로 옮긴다 — 제외 플래그는 그대로
    res = implied_vol(0.019, 1100.0, 1200.0, 7 * DAY, "c", kis_iv_pct=25.0, t_kis=6 * DAY)
    assert (res.quality, res.below_min_premium, res.rescaled) == ("estimated", True, True)
    assert res.sigma == pytest.approx(0.25 * math.sqrt(6 / 7))


@pytest.mark.parametrize("bad", [0.0, -DAY, math.nan, math.inf])
def test_rejects_bad_t_kis(bad: float) -> None:
    with pytest.raises(ValueError, match="t_kis"):
        implied_vol(49.0, 1100.0, 1050.0, 7 * DAY, "c", kis_iv_pct=20.0, t_kis=bad)


@pytest.mark.parametrize(
    ("sigma", "quality", "source", "reason", "rescaled", "t_kis"),
    [
        (0.2, "ok", "model", None, False, DAY),  # 자체 역산에 T_KIS 기록
        (0.2, "ok", "model", None, True, DAY),
        (None, "invalid", None, "below_intrinsic/kis_missing", False, DAY),  # 옮길 KIS 값 없음
        (None, "invalid", None, "model_out_of_range", True, DAY),  # KIS 를 보지 않았다
        # 옮긴 탓에 invalid 라면서 옮긴 기록이 없다
        (None, "invalid", None, "below_intrinsic/kis_out_of_range_rescaled", False, DAY),
        (None, "invalid", None, "below_intrinsic/kis_out_of_range_rescaled", False, None),
        (0.2, "estimated", "kis", "below_intrinsic", True, None),  # 환산했는데 T_KIS 없음
        (0.2, "estimated", "kis", "below_intrinsic", False, 0.0),
        (0.2, "estimated", "kis", "below_intrinsic", True, math.nan),
        (None, "invalid", None, "below_intrinsic/kis_out_of_range", True, -DAY),
    ],
)
def test_iv_result_rejects_inconsistent_rescale_record(
    sigma: float | None,
    quality: str,
    source: str | None,
    reason: str | None,
    rescaled: bool,
    t_kis: float | None,
) -> None:
    with pytest.raises(ValueError, match="IvResult"):
        IvResult(sigma, quality, source, reason, rescaled=rescaled, t_kis=t_kis)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("reason", "rescaled", "t_kis", "excluded"),
    [
        ("below_intrinsic/kis_out_of_range_rescaled", True, DAY, False),
        ("below_min_premium/kis_out_of_range_rescaled", True, DAY, True),  # §1.2 표시용 σ
        ("below_intrinsic/kis_out_of_range", True, DAY, False),  # 옮기기 전에도 > 300%
        ("below_intrinsic/kis_out_of_range", False, DAY, False),  # T_KIS = 자체 T
        ("below_intrinsic/kis_out_of_range", False, None, False),
    ],
)
def test_iv_result_keeps_rescale_record_on_kis_out_of_range(
    reason: str, rescaled: bool, t_kis: float | None, excluded: bool
) -> None:
    # 폴백 σ 가 이상치로 invalid 여도 옮긴 기록(t_kis·rescaled)은 남을 수 있다(§1.5 기록)
    res = IvResult(None, "invalid", None, reason, excluded, rescaled=rescaled, t_kis=t_kis)
    assert (res.rescaled, res.t_kis) == (rescaled, t_kis)


# --- 입력 검증·불변식 ---


@pytest.mark.parametrize(
    ("f", "k", "t", "flag", "match"),
    [
        (0.0, 1100.0, 0.1, "c", "F"),
        (1100.0, -1.0, 0.1, "c", "K"),
        (1100.0, 1100.0, 0.0, "c", "T"),
        (1100.0, 1100.0, 0.1, "call", "flag"),
    ],
)
def test_rejects_bad_inputs(f: float, k: float, t: float, flag: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        implied_vol(1.0, f, k, t, flag)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("mp", [-0.01, math.nan])
def test_rejects_bad_min_premium(mp: float) -> None:
    with pytest.raises(ValueError, match="min_premium"):
        implied_vol(1.0, 1100.0, 1100.0, 0.1, "c", min_premium=mp)


@pytest.mark.parametrize(
    ("sigma", "quality", "source", "reason"),
    [
        (None, "ok", "model", None),
        (0.2, "ok", "kis", None),
        (0.2, "ok", "model", "x"),
        (0.2, "estimated", "kis", None),
        (0.2, "invalid", None, "x"),
        (None, "invalid", "kis", "x"),
        (3.5, "estimated", "kis", "x"),  # §6.1 이상치
        (0.0, "ok", "model", None),
        (0.2, "stale", "model", None),
        (0.2, "estimated", "kis", "below_min_premium"),  # 사유와 플래그가 어긋남
        (None, "invalid", None, "below_min_premium/kis_missing"),
    ],
)
def test_iv_result_rejects_inconsistent_fields(
    sigma: float | None, quality: str, source: str | None, reason: str | None
) -> None:
    with pytest.raises(ValueError, match="IvResult"):
        IvResult(sigma=sigma, quality=quality, source=source, reason=reason)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("sigma", "quality", "source", "reason"),
    [
        (0.2, "ok", "model", None),
        (0.2, "estimated", "kis", "below_intrinsic"),
        (None, "invalid", None, "model_out_of_range"),
    ],
)
def test_iv_result_rejects_flag_without_premium_reason(
    sigma: float | None, quality: str, source: str | None, reason: str | None
) -> None:
    with pytest.raises(ValueError, match="IvResult"):
        IvResult(sigma, quality, source, reason, below_min_premium=True)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("sigma", "ok"),
    [
        (0.0, False),
        (1e-9, True),
        (3.0, True),
        (3.0000001, False),
        (-0.1, False),
        (math.nan, False),
        (math.inf, False),
    ],
)
def test_usable_bounds(sigma: float, ok: bool) -> None:
    assert usable(sigma) is ok

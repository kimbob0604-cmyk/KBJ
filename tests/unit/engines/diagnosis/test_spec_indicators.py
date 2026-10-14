"""metrics §D1~§D6 지표 한 줄 — 값·구간·품질 규칙(이력 부족·공표 지연·입력 없음)·플래그·파생
손계산."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

from kbj.config.diag import load_diag
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.diagnosis.bands import Obs
from kbj.engines.diagnosis.indicators import (
    NO_INPUT,
    add,
    change_bp,
    change_pct,
    indicator_row,
    rolling_sum,
)

D = Decimal
CFG = load_diag()
AT = datetime(2026, 12, 10, 16, 0, tzinfo=KST)
D0 = date(2026, 1, 1)


def _series(values: Sequence[float | None], *, q: Quality = Quality.OK) -> list[Obs]:
    return [
        Obs(D0 + timedelta(days=i), None if v is None else D(str(v)), q, "SYN")
        for i, v in enumerate(values)
    ]


def _row(key: str, hist: list[Obs], day: date, **kw: object):
    return indicator_row(
        key, CFG.indicators[key], CFG, hist, day=day, as_of=AT, source="KBJ:diag.public",
        **kw,  # type: ignore[arg-type]
    )  # fmt: skip


def test_short_history_has_no_band_and_is_estimated() -> None:
    hist = _series([1.0] * (CFG.min_history - 1))
    r = _row("deposits", hist, hist[-1].day)
    assert r.value == D("1.0000") and r.state is None and r.pct_rank is None
    assert r.quality is Quality.ESTIMATED
    assert f"이력 부족({CFG.min_history - 1}/{CFG.min_history})" in r.reasons


def test_full_history_band_and_ok() -> None:
    vals = [float(i) for i in range(100)]  # 마지막이 최대 → p = 100 → 극단 높음
    hist = _series(vals)
    r = _row("deposits", hist, hist[-1].day)
    assert r.pct_rank == D(100) and r.state == "extreme_high" and r.quality is Quality.OK
    assert r.inputs["n"] == 100


def test_window_is_last_250_observations() -> None:
    # 300 관측 중 처음 50 은 큰 값 — 창 밖이라 백분위에 들지 않는다
    vals = [1000.0] * 50 + [float(i) for i in range(250)]
    r = _row("deposits", _series(vals), D0 + timedelta(days=299))
    assert r.inputs["n"] == CFG.window and r.pct_rank == D(100)


def test_publication_lag_is_stale_with_date() -> None:
    hist = _series([float(i) for i in range(80)])
    last = hist[-1].day
    r = _row("credit_spread", hist, last + timedelta(days=3), expected=last + timedelta(days=1))
    assert r.quality is Quality.STALE
    assert f"공표 지연 — {last.isoformat()} 값" in r.reasons
    assert r.inputs["obs_day"] == last.isoformat()


def test_no_input_is_none_not_zero() -> None:
    r = _row("export_momentum", [], D0, reasons=["준비 중"])
    assert r.value is None and r.state is None and r.quality is Quality.INVALID
    assert r.reasons == ("준비 중",)
    r2 = _row("export_momentum", _series([None, None]), D0 + timedelta(days=1))
    assert r2.value is None and r2.reasons == (NO_INPUT,)


def test_future_observations_are_ignored() -> None:
    hist = _series([float(i) for i in range(100)])
    r = _row("deposits", hist, hist[49].day)
    assert r.value == D("49.0000") and r.inputs["n"] == 50


def test_credit_spread_uses_sigma_band_and_keeps_pct() -> None:
    vals = [0.6] * 99 + [0.9]
    r = _row("credit_spread", _series(vals), D0 + timedelta(days=99))
    assert r.state == "wide" and r.pct_rank == D(100) and "z" in r.inputs


def test_overheat_and_fragile_flags_only_at_extreme_high() -> None:
    hist = _series([float(i) for i in range(100)])
    assert _row("credit_balance", hist, hist[-1].day).flags == ("overheat",)
    assert _row("semis_concentration", hist, hist[-1].day).flags == ("fragile",)
    assert _row("deposits", hist, hist[-1].day).flags == ()
    assert _row("credit_balance", hist, hist[50].day).flags == ()


def test_quality_cap_and_invalid_inputs_skipped() -> None:
    hist = _series([float(i) for i in range(70)])
    hist.append(Obs(D0 + timedelta(days=70), D(999), Quality.INVALID, "SYN"))
    r = _row("export_momentum", hist, D0 + timedelta(days=70), quality_cap=Quality.ESTIMATED)
    assert r.value == D("69.0000") and r.quality is Quality.ESTIMATED


def test_change_pct_hand_computed() -> None:
    hist = _series([100.0, 110.0, None, 121.0])
    out = change_pct(hist, 1)  # 결측은 건너뛰고 앞 관측과 비교
    assert [(o.day.day, o.value) for o in out] == [(2, D(10)), (4, D(10))]
    assert change_pct(_series([0.0, 5.0]), 1) == []  # 분모 0 → 점 없음


def test_change_bp_and_rolling_sum_and_add() -> None:
    bp = change_bp(_series([2.50, 2.55, 2.40]), 2)
    assert [o.value for o in bp] == [D("-10.00")]
    rs = rolling_sum(_series([1.0, 2.0, 3.0, 4.0]), 3)
    assert [o.value for o in rs] == [D("6.0"), D("9.0")]
    a = _series([1.0, 2.0, None])
    b = [Obs(D0 + timedelta(days=1), D(10), Quality.STALE, "B")]
    s = add(a, b)
    assert len(s) == 1 and s[0].value == D("12.0") and s[0].quality is Quality.STALE
    assert s[0].source == "SYN+B"

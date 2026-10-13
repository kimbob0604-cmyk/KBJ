"""리비전 엔진(§4.7, metrics §10) — SD `revision_calculator._self_test`:296 분류 표 승격·기준선.

승격 원본: legacy/stock_dashboard/revision_calculator.py `_self_test`(분류 11사례 — 기대값 그대로).
경계 수치는 `config/fin.yaml consensus.signal_pct`(엔진은 수치를 다시 적지 않는다).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from kbj.config.fin import SignalPct, load_fin
from kbj.core.fin_rows import ConsensusRow, RevisionRow, TargetConsensus
from kbj.core.quality import Quality
from kbj.engines.consensus.revision import (
    REVISION_METRICS,
    TARGET_METRIC,
    TARGET_YM,
    alertable,
    change,
    classify,
    revisions,
)

CUTS: SignalPct = load_fin().consensus.signal_pct
T = date(2026, 11, 16)
EOK = 100_000_000


# ── SD _self_test 분류 표(그대로) ────────────────────────────────────────────────────────
SD_SELF_TEST = [
    (30.0, "STRONG_UP", 1),
    (15.0, "STRONG_UP", 1),
    (14.99, "UP", 2),
    (5.0, "UP", 2),
    (4.99, "NEUTRAL", 3),
    (0.0, "NEUTRAL", 3),
    (-4.99, "NEUTRAL", 3),
    (-5.0, "DOWN", 2),
    (-14.99, "DOWN", 2),
    (-15.0, "STRONG_DOWN", 1),
    (-40.0, "STRONG_DOWN", 1),
]


@pytest.mark.parametrize(("pct", "signal", "priority"), SD_SELF_TEST)
def test_sd_classify_signal_table(pct: float, signal: str, priority: int) -> None:
    assert classify(pct, CUTS) == (signal, priority)


def test_config_cuts_are_the_sd_boundaries() -> None:
    assert (CUTS.strong_up, CUTS.up, CUTS.down, CUTS.strong_down) == (15.0, 5.0, -5.0, -15.0)


# ── 변화율 ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("cur", "base", "want"),
    [
        (110.0, 100.0, (10.0, False)),
        (90.0, 100.0, (-10.0, False)),
        (-90.0, -100.0, (10.0, False)),  # 적자 축소 = 상향(|기준| 분모)
        (-110.0, -100.0, (-10.0, False)),
        (0.0, 100.0, (-100.0, False)),
        (5.0, 0.0, (None, False)),  # 기준 0 → 비율 없음
        (5.0, None, (None, False)),  # 기준 없음
        (15.0, -40.0, (None, True)),  # 부호 전환 → TURN
        (-15.0, 40.0, (None, True)),
    ],
)
def test_change(cur: float, base: float | None, want: tuple[float | None, bool]) -> None:
    pct, turn = change(cur, base)
    assert turn is want[1]
    assert pct == (None if want[0] is None else pytest.approx(want[0]))


# ── 리비전 행 ───────────────────────────────────────────────────────────────────────────


def snap(
    d: date,
    ym: str,
    *,
    est: bool = True,
    rev: int | None = 1000,
    op: int | None = 100,
    ni: int | None = 80,
    eps: float | None = 800.0,
    code: str = "994010",
    quality: Quality = Quality.OK,
) -> ConsensusRow:
    def won(v: int | None) -> int | None:
        return None if v is None else v * EOK

    return ConsensusRow(code, d, ym, est, won(rev), won(op), won(ni), None, eps, None,
                        quality=quality)  # fmt: skip


def series(days: list[int], ym: str = "202612", **kw: object) -> list[ConsensusRow]:
    """T 기준 며칠 전들의 스냅(같은 값) — 값은 kw 로 바꾼다."""
    return [snap(T - timedelta(days=n), ym, **kw) for n in days]  # type: ignore[arg-type]


def by(rows: list[RevisionRow], metric: str, w: int, ym: str = "202612") -> RevisionRow:
    (r,) = [x for x in rows if x.metric == metric and x.window_days == w and x.fiscal_ym == ym]
    return r


def test_baseline_is_latest_snapshot_at_or_before_t_minus_w() -> None:
    rows = [
        snap(T - timedelta(days=40), "202612", op=90),
        snap(T - timedelta(days=30), "202612", op=100),  # 30일 기준(경계 포함)
        snap(T - timedelta(days=8), "202612", op=104),
        snap(T - timedelta(days=7), "202612", op=105),  # 7일 기준(경계 포함)
        snap(T - timedelta(days=6), "202612", op=200),  # 창 안 — 기준 아님
        snap(T, "202612", op=110),
        snap(T + timedelta(days=1), "202612", op=999),  # t 뒤 — 무시(미래 참조 없음)
    ]
    out = revisions(rows, T, cuts=CUTS)
    r30, r7 = by(out, "op", 30), by(out, "op", 7)
    assert r30.baseline == 100 * EOK and r30.baseline_date == T - timedelta(days=30)
    assert r30.pct == pytest.approx(10.0) and r30.signal == "UP"
    assert r7.baseline == 105 * EOK and r7.pct == pytest.approx(100 * 5 / 105)
    assert r7.signal == "NEUTRAL"
    assert r30.current == 110 * EOK and r30.as_of == T and r30.quality is Quality.OK


def test_rows_cover_fy1_fy2_metrics_and_windows_only() -> None:
    rows = [
        *series([30, 0], "202412", est=False),  # 실적 — 리비전 없음
        *series([30, 0], "202612"),
        *series([30, 0], "202712"),
        *series([30, 0], "202812"),  # FY3 — 리비전 없음
    ]
    out = revisions(rows, T, cuts=CUTS, windows=(7, 30))
    assert {(r.fiscal_ym, r.metric, r.window_days) for r in out} == {
        (ym, m, w) for ym in ("202612", "202712") for m in REVISION_METRICS for w in (7, 30)
    }


def test_no_baseline_zero_and_sign_turn() -> None:
    rows = [
        snap(T - timedelta(days=31), "202612", op=0, ni=-40, rev=None),
        snap(T, "202612", op=25, ni=15, rev=1000),
    ]
    out = revisions(rows, T, cuts=CUTS)
    r = by(out, "op", 30)
    assert r.baseline == 0 and r.pct is None and r.signal is None  # 기준 0
    t = by(out, "ni", 30)
    assert t.signal == "TURN" and t.pct is None and t.baseline == -40 * EOK
    nb = by(out, "revenue", 30)  # 기준 스냅에 값이 없다 → 기준선 없음
    assert nb.baseline is None and nb.baseline_date is None and nb.pct is None
    seven = by(out, "op", 7)  # 7일 전 스냅이 없다(31일 전이 7일 기준선이 된다)
    assert seven.baseline_date == T - timedelta(days=31)


def test_missing_current_value_makes_no_row_and_baseline_skips_none() -> None:
    rows = [
        snap(T - timedelta(days=35), "202612", eps=700.0),
        snap(T - timedelta(days=30), "202612", eps=None),  # 값 없는 스냅은 기준선이 아니다
        snap(T, "202612", eps=770.0, op=None),
    ]
    out = revisions(rows, T, cuts=CUTS)
    assert not [r for r in out if r.metric == "op"]
    e = by(out, "eps", 30)
    assert e.baseline == 700.0 and e.baseline_date == T - timedelta(days=35)
    assert e.pct == pytest.approx(10.0)


def test_stale_and_estimated_quality() -> None:
    rows = [snap(T - timedelta(days=40), "202612"), snap(T - timedelta(days=2), "202612")]
    assert {r.quality for r in revisions(rows, T, cuts=CUTS)} == {Quality.STALE}
    rows = [
        snap(T - timedelta(days=40), "202612", quality=Quality.ESTIMATED),
        snap(T, "202612"),
    ]
    assert by(revisions(rows, T, cuts=CUTS), "op", 30).quality is Quality.ESTIMATED


def test_target_mean_revision() -> None:
    def tc(d: date, mean: float, n: int = 3) -> TargetConsensus:
        q = Quality.OK if n >= 3 else Quality.ESTIMATED
        return TargetConsensus("994010", d, mean, mean, mean, mean, n, 90, quality=q)

    targets = [tc(T - timedelta(days=30), 10000.0), tc(T - timedelta(days=7), 11000.0, 2),
               tc(T, 11550.0)]  # fmt: skip
    out = revisions([snap(T, "202612")], T, cuts=CUTS, targets=targets)
    r30 = by(out, TARGET_METRIC, 30, TARGET_YM)
    assert r30.pct == pytest.approx(15.5) and r30.signal == "STRONG_UP"
    r7 = by(out, TARGET_METRIC, 7, TARGET_YM)
    assert r7.pct == pytest.approx(5.0) and r7.quality is Quality.ESTIMATED
    # 스냅 없이 목표가만 있어도 목표가 행은 나온다
    only = revisions([], T, cuts=CUTS, targets=targets)
    assert {r.metric for r in only} == {TARGET_METRIC}


def test_bad_inputs() -> None:
    with pytest.raises(ValueError, match="windows"):
        revisions([], T, cuts=CUTS, windows=(7, 7))
    with pytest.raises(ValueError, match="섞였다"):
        revisions([snap(T, "202612"), snap(T, "202612", code="994020")], T, cuts=CUTS)


def test_alertable_takes_estimate_metrics_over_threshold_or_turn() -> None:
    rows = [
        snap(T - timedelta(days=30), "202612", op=100, ni=-40, rev=1000, eps=800.0),
        snap(T, "202612", op=104, ni=15, rev=1060, eps=800.0),
    ]
    tg = [
        TargetConsensus("994010", T - timedelta(days=30), 100.0, 100.0, 100.0, 100.0, 3, 90),
        TargetConsensus("994010", T, 150.0, 150.0, 150.0, 150.0, 3, 90),
    ]
    out = revisions(rows, T, cuts=CUTS, windows=(30,), targets=tg)
    got = {(r.metric, r.signal) for r in alertable(out, min_abs_pct=5.0)}
    assert got == {("revenue", "UP"), ("ni", "TURN")}  # 영업이익 4% 빠짐, 목표가 평균은 대상 아님
    assert alertable(out, min_abs_pct=100.0) == [r for r in out if r.signal == "TURN"]
    with pytest.raises(ValueError):
        alertable(out, min_abs_pct=-1)

"""매크로 파생(kbj/engines/macro/derive.py — metrics §16): 손계산 표·결측·단위·등급 섞임 금지."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from kbj.config.macro import DerivedCfg, Tier, load_macro
from kbj.core.p5_rows import MacroSeriesRow
from kbj.core.quality import Quality
from kbj.engines.macro.derive import (
    TierMixError,
    ann3m,
    derive,
    latest_change,
    mom,
    real_rate,
    spread,
    yoy,
)
from tests.fixtures.synthetic.p5_world import build_world

T = datetime(2026, 11, 17, tzinfo=UTC)


def row(
    sid: str, period: str, v: str | None, q: Quality = Quality.OK, src: str = "FRED"
) -> MacroSeriesRow:
    qual = q if v is not None else Quality.INVALID
    return MacroSeriesRow(sid, period, None if v is None else Decimal(v), "%", src, T, qual)


def test_spread_hand_table_with_missing_and_unmatched_dates() -> None:
    a = [
        row("A", "2026-11-12", "4.30"),
        row("A", "2026-11-13", "4.25"),
        row("A", "2026-11-16", "4.40"),
    ]
    b = [
        row("B", "2026-11-12", "3.95"),
        row("B", "2026-11-13", None),
        row("B", "2026-11-17", "3.90"),
    ]
    got = [(p.period, p.value, p.quality, p.reason) for p in spread(a, b)]
    assert got[0][:3] == ("2026-11-12", pytest.approx(0.35), Quality.OK)
    assert got[1][1] is None and got[1][2] is Quality.INVALID and got[1][3] == "입력 없음"
    assert got[2][1] is None and "뺄 값" in (got[2][3] or "")  # 날짜가 다르면 계산하지 않는다
    stale = spread([row("A", "2026-11-12", "4.0", Quality.STALE)], [row("B", "2026-11-12", "3.0")])
    assert stale[0].quality is Quality.STALE and stale[0].value == 1.0


def test_yoy_mom_ann3m_hand_values_and_january_boundary() -> None:
    xs = [row("C", f"2025-{m:02d}", str(100 + m)) for m in range(1, 13)]
    xs += [row("C", "2026-01", "110.24"), row("C", "2026-02", None)]
    y = {p.period: p for p in yoy(xs)}
    assert y["2026-01"].value == pytest.approx((110.24 / 101 - 1) * 100)  # 1월 → 전년 1월
    assert y["2025-12"].value is None and y["2025-12"].reason == "비교 기간 없음"
    assert y["2026-02"].value is None and y["2026-02"].reason == "입력 없음"
    m = {p.period: p for p in mom(xs)}
    assert m["2026-01"].value == pytest.approx((110.24 / 112 - 1) * 100)  # 1월 → 전년 12월
    a = {p.period: p for p in ann3m(xs)}
    assert a["2025-04"].value == pytest.approx(((104 / 101) ** 4 - 1) * 100)
    q = [row("G", "2025-Q4", "100"), row("G", "2026-Q1", "102")]
    assert mom(q)[1].value == pytest.approx(2.0)  # 분기 = 전기비
    with pytest.raises(ValueError):
        yoy([row("D", "2026-11-16", "1")])  # 일별은 비율 계산 대상이 아니다


def test_real_rate_uses_month_last_policy_rate_minus_cpi_yoy() -> None:
    cpi = [row("CPI", "2025-10", "100", src="KOSIS"), row("CPI", "2026-10", "102.5", src="KOSIS")]
    rate = [row("R", "2026-10-02", "2.75", src="ECOS"), row("R", "2026-10-30", "2.50", src="ECOS")]
    pts = {p.period: p.value for p in real_rate(rate, cpi)}
    assert pts["2026-10"] == pytest.approx(2.50 - 2.5)
    assert pts["2025-10"] is None


def test_derive_follows_config_and_refuses_tier_mix() -> None:
    cfg = load_macro()
    by = {d.id: d for d in cfg.derived}
    tiers: dict[str, Tier] = {s.id: s.tier for s in cfg.series}
    d = by["DERIVED:us_10y_2y"]
    a = [row("FRED:DGS10", "2026-11-16", "4.40")]
    b = [row("FRED:DGS2", "2026-11-16", "4.00")]
    out = derive(d, {"FRED:DGS10": a, "FRED:DGS2": b}, tiers)
    assert out.tier == "public" and out.unit == "%p" and out.points[0].value == pytest.approx(0.4)
    assert out.sources == ("FRED",)
    empty = derive(d, {"FRED:DGS10": a}, tiers)
    assert empty.points == () and "FRED:DGS2" in (empty.na_reason or "")
    mixed = DerivedCfg(id="DERIVED:x", kind="spread", inputs=("FRED:DGS10", "YAHOO:^TNX"),
                       title="x", tab="us", unit="%p", tier="public")  # fmt: skip
    with pytest.raises(TierMixError):
        derive(mixed, {}, tiers)
    with pytest.raises(ValueError):
        only: dict[str, Tier] = {"FRED:DGS10": "public"}
        derive(mixed, {}, only)


def test_latest_change_skips_missing_values() -> None:
    lc = latest_change([("2026-11-12", 4.0), ("2026-11-13", 4.2), ("2026-11-16", None)])
    assert lc is not None and lc.period == "2026-11-13" and lc.change == pytest.approx(0.2)
    assert lc.change_pct == pytest.approx(5.0) and lc.prev_period == "2026-11-12"
    assert latest_change([]) is None
    one = latest_change([("2026-11-12", Decimal("1"))])
    assert one is not None and one.change is None


def test_p5_world_expectations_match_engine() -> None:
    w = build_world()
    exp = w.expect_macro()
    for did, (a, b) in {
        "DERIVED:us_10y_2y": ("FRED:DGS10", "FRED:DGS2"),
        "DERIVED:credit_aa": ("ECOS:817Y002:corp_aa3y", "ECOS:817Y002:ktb3y"),
    }.items():
        got = {p.period: p.value for p in spread(w.series(a), w.series(b))}
        for period, want in exp[did].items():
            assert got[period] == (pytest.approx(want) if want is not None else None)

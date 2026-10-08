"""ETF 순유입·가격효과·검산 ③(docs/metrics.md §4·§8, docs/p3_design.md §4.3·§8.2).

metrics §4 '오류가 나기 쉬운 곳' 1~5번을 기대값으로 고정한다(구현 전에 시험으로 막는다).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.core.quality import Quality
from kbj.core.rows import EtfDay, EtfMeta, EtfType, SplitEvent
from kbj.engines.etf.flows import (
    REASON_C3,
    REASON_GAP,
    by_type,
    c3_checks,
    c3_tol,
    check3,
    daily_flow,
    period_totals,
    type_of,
    window_flows,
)
from tests.fixtures.synthetic.ledger_gen import generate

D0 = date(2026, 10, 1)
OK = Quality.OK


def day(code: str, d: date, s: int, nav: float, *, na: int | None = -1, q: Quality = OK) -> EtfDay:
    """합성 ETF 행. na=-1 이면 보고 순자산 = round(S×NAV), None 이면 없음."""
    net = round(s * nav) if na == -1 else na
    return EtfDay(code, d, f"합성{code}", nav, nav, s, net, 0, 0, None, "합성지수", "krx", "KRX", q)


def tdays(n: int, start: date = D0) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ── 공식 ──


def test_inflow_and_price_effect_formula() -> None:
    a = day("E1", date(2026, 10, 1), 1_000_000, 10_000.0)
    b = day("E1", date(2026, 10, 2), 1_100_000, 10_100.0)
    f = daily_flow(a, b)
    assert f.status == "ok" and f.quality is OK
    assert f.net_inflow == pytest.approx(100_000 * 10_100.0)  # ΔS × NAVₜ
    assert f.price_effect == pytest.approx(1_000_000 * 100.0)  # Sₜ₋₁ × ΔNAV
    assert f.basis == "reported"
    assert check3(f) is True
    assert abs(f.residual or 0) <= 1.0
    assert f.won(f.net_inflow) == 1_010_000_000


def test_computed_basis_when_no_reported_net_asset() -> None:
    a = day("E1", date(2026, 10, 1), 1_000, 10_000.123, na=None)
    b = day("E1", date(2026, 10, 2), 1_300, 9_876.5, na=None)
    f = daily_flow(a, b)
    assert f.basis == "computed" and f.status == "ok"
    assert f.residual == pytest.approx(0.0, abs=1e-6)  # 항등식 — 잔차 0
    assert check3(f) is True


def test_c3_tolerance_and_reported_mismatch_is_invalid() -> None:
    assert c3_tol(1000, 900, unit=1.0) == pytest.approx(0.005 * 1900 + 1)
    a = day("E1", date(2026, 10, 1), 1_000_000, 10_000.0)
    b = day("E1", date(2026, 10, 2), 1_000_000, 10_000.0, na=round(1_000_000 * 10_000.0) + 50_000)
    f = daily_flow(a, b)
    assert f.status == "invalid" and f.quality is Quality.INVALID and f.reason == REASON_C3
    assert check3(f) is False
    checks = c3_checks([f], datetime(2026, 10, 2, 9, tzinfo=UTC))
    assert [(c.domain, c.check_id, c.code) for c in checks] == [("etf", "c3", "E1")]
    assert checks[0].residual == pytest.approx(50_000.0)
    # 반올림 오차 안(0.005원 × 좌수)이면 통과
    b2 = replace(b, net_asset=round(1_000_000 * 10_000.0) + 5_000)
    assert daily_flow(a, b2).status == "ok"


# ── 오류 1: 분할·병합 ──


def test_split_1_to_10_has_zero_inflow() -> None:
    a = day("E1", date(2026, 10, 1), 1_000_000, 10_000.0)
    b = day("E1", date(2026, 10, 2), 10_000_000, 1_000.0)
    raw = daily_flow(a, b)  # 이벤트 없이 그대로 — 가짜 대규모 순유입
    assert raw.net_inflow == pytest.approx(9_000_000 * 1_000.0)
    [f] = window_flows([a, b], 1)  # 감지 → 보정
    assert f.status == "split_adjusted" and f.ratio == 10.0
    assert f.net_inflow == 0.0 and f.price_effect == pytest.approx(0.0)
    assert f.split is not None and f.split.origin == "detected"
    assert f.quality is Quality.ESTIMATED  # 감지는 추정
    assert check3(f) is True


def test_merge_5_to_1_has_zero_inflow() -> None:
    a = day("E1", date(2026, 10, 1), 10_000_000, 1_000.0)
    b = day("E1", date(2026, 10, 2), 2_000_000, 5_000.0)
    [f] = window_flows([a, b], 1)
    assert f.status == "split_adjusted" and f.ratio == pytest.approx(0.2)
    assert f.net_inflow == pytest.approx(0.0) and f.price_effect == pytest.approx(0.0)


def test_merge_with_odd_shares_has_no_rounding_artifact() -> None:
    """5:1 병합인데 전날 좌수가 5 로 나누어떨어지지 않는다(단주 현금 정산). 보정 좌수를 정수로
    반올림하면 가짜 순유입과 계산 순자산 잔차가 생긴다 — 순유입은 정산된 0.6좌 × NAVₜ 뿐이다."""
    a = day("E1", date(2026, 10, 1), 10_000_003, 1_000.0, na=None)
    b = day("E1", date(2026, 10, 2), 2_000_000, 5_000.0, na=None)
    [f] = window_flows([a, b], 1)
    assert f.status == "split_adjusted" and check3(f) is True
    assert f.net_inflow == pytest.approx(-0.6 * 5_000.0)
    assert f.price_effect == pytest.approx(0.0, abs=1e-6)


def test_big_creation_is_not_a_split() -> None:
    a = day("E1", date(2026, 10, 1), 1_000_000, 10_000.0)
    b = day("E1", date(2026, 10, 2), 10_000_000, 10_000.0)  # 좌수만 10배, NAV 그대로
    [f] = window_flows([a, b], 1)
    assert f.status == "ok" and f.split is None
    assert f.net_inflow == pytest.approx(9_000_000 * 10_000.0)


def test_manual_split_wins_over_detection() -> None:
    a = day("E1", date(2026, 10, 1), 1_000_000, 10_000.0)
    b = day("E1", date(2026, 10, 2), 10_000_000, 1_000.0)
    manual = SplitEvent("E1", b.date, 10.0, "manual", "운용사 공시", "manual", OK)
    detected = replace(manual, origin="detected", quality=Quality.ESTIMATED, source="engine")
    [f] = window_flows([a, b], 1, splits=[detected, manual])
    assert f.split is manual and f.quality is OK
    with pytest.raises(ValueError, match="그날"):
        daily_flow(a, b, replace(manual, effective_date=a.date))


# ── 오류 2: 분배금 ──


def test_distribution_is_price_effect_not_outflow() -> None:
    s = 2_000_000
    a = day("E1", date(2026, 10, 1), s, 10_000.0)
    b = day("E1", date(2026, 10, 2), s, 9_500.0)  # 분배금 500원
    f = daily_flow(a, b)
    assert f.net_inflow == 0.0
    assert f.price_effect == pytest.approx(-s * 500.0)
    assert f.status == "ok"


# ── 오류 3: 신규 상장·상장폐지·공백 ──


def test_new_listing_is_marked_new_and_excluded() -> None:
    ds = tdays(4)
    rows = [day("E2", d, 500_000, 10_000.0) for d in ds[2:]]
    flows = window_flows(rows, 3, trading_days=ds)
    assert [f.status for f in flows] == ["new", "ok"]
    new = flows[0]
    assert new.net_inflow is None and new.new_net_asset == pytest.approx(5_000_000_000)
    assert check3(new) is None  # 검산 불가 — 0 으로 바꾸지 않는다


def test_listing_on_window_first_day_needs_listed_on() -> None:
    ds = tdays(3)
    rows = [day("E2", d, 500_000, 10_000.0) for d in ds]
    unknown = window_flows(rows, 3, trading_days=ds)
    assert unknown[0].status == "invalid"  # 전날을 모른다
    known = window_flows(rows, 3, trading_days=ds, listed_on=ds[0])
    assert [f.status for f in known] == ["new", "ok", "ok"]


def test_gap_day_is_invalid_not_multi_day_flow() -> None:
    ds = tdays(4)
    rows = [day("E3", ds[0], 1_000, 10.0), day("E3", ds[1], 1_100, 10.0),
            day("E3", ds[3], 1_500, 10.0)]  # fmt: skip
    flows = window_flows(rows, 3, trading_days=ds)
    assert [f.status for f in flows] == ["ok", "invalid"]
    assert flows[-1].reason == REASON_GAP


def test_delisting_counts_until_the_day_before() -> None:
    ds = tdays(5)
    rows = [day("E4", d, 1_000 + i, 10.0) for i, d in enumerate(ds)]
    flows = window_flows(rows, 4, trading_days=ds, delisted_on=ds[3])
    assert [f.date for f in flows] == ds[1:3]


def test_invalid_row_is_excluded() -> None:
    a = day("E1", date(2026, 10, 1), 1_000, 10.0)
    b = day("E1", date(2026, 10, 2), 1_100, 10.0, q=Quality.INVALID)
    assert daily_flow(a, b).status == "invalid"
    c = day("E1", date(2026, 10, 2), 1_100, 10.0)
    assert daily_flow(replace(a, quality=Quality.INVALID), c).status == "invalid"
    assert daily_flow(replace(a, nav=None), c).status == "invalid"


def test_estimated_input_keeps_estimated_quality() -> None:
    a = day("E1", date(2026, 10, 1), 1_000, 10.0)
    b = day("E1", date(2026, 10, 2), 1_100, 10.0, q=Quality.ESTIMATED)
    assert daily_flow(a, b).quality is Quality.ESTIMATED


def test_input_validation() -> None:
    a = day("E1", date(2026, 10, 2), 1_000, 10.0)
    with pytest.raises(ValueError):
        daily_flow(a, day("E1", date(2026, 10, 1), 1_000, 10.0))
    with pytest.raises(ValueError):
        daily_flow(a, day("E9", date(2026, 10, 3), 1_000, 10.0))
    with pytest.raises(ValueError):
        window_flows([a, day("E9", date(2026, 10, 3), 1, 1.0)], 1)
    with pytest.raises(ValueError):
        window_flows([a], 0)
    assert window_flows([], 3) == []


# ── 기간·유형별 ──


def test_period_total_equals_sum_of_days() -> None:
    ds = tdays(6)
    rows = [day("E1", d, 1_000_000 + 37_000 * i * (-1) ** i, 10_000.0 + 13 * i)
            for i, d in enumerate(ds)]  # fmt: skip
    flows = window_flows(rows, 5, trading_days=ds)
    tot = period_totals(flows)["E1"]
    assert tot.days == 5 and tot.start == ds[1] and tot.end == ds[-1]
    assert tot.net_inflow == pytest.approx(sum(f.net_inflow or 0 for f in flows))
    assert tot.price_effect == pytest.approx(sum(f.price_effect or 0 for f in flows))


def _meta(code: str, t: EtfType | None, name: str | None = None) -> EtfMeta:
    return EtfMeta(code, name, None, None, None, t, None, None, None, None, "synthetic", OK)


def test_by_type_has_all_seven_types_and_skips_invalid() -> None:
    a, b = date(2026, 10, 1), date(2026, 10, 2)
    flows = [
        daily_flow(day("L1", a, 1_000, 10.0), day("L1", b, 2_000, 10.0)),
        daily_flow(day("K1", a, 1_000, 10.0), day("K1", b, 1_500, 10.0)),
        daily_flow(day("K2", a, 1_000, 10.0), day("K2", b, 1_100, 10.0, q=Quality.INVALID)),
        daily_flow(None, day("N1", b, 1_000, 10.0)),
    ]
    meta = {
        "L1": _meta("L1", EtfType.LEVERAGED_INVERSE),
        "K1": _meta("K1", None, "KODEX 200"),  # 유형이 비면 이름으로
        "K2": _meta("K2", EtfType.KR_INDEX),
        "N1": _meta("N1", EtfType.KR_THEME),
    }
    roll = {r.etf_type: r for r in by_type(flows, meta)}
    assert list(roll) == list(EtfType)
    assert roll[EtfType.LEVERAGED_INVERSE].net_inflow == pytest.approx(10_000.0)
    assert roll[EtfType.KR_INDEX].net_inflow == pytest.approx(5_000.0)
    assert roll[EtfType.KR_INDEX].n_invalid == 1 and roll[EtfType.KR_INDEX].n_etfs == 1
    assert roll[EtfType.KR_THEME].n_new == 1 and roll[EtfType.KR_THEME].net_inflow == 0.0
    assert roll[EtfType.LEVERAGED_INVERSE].label == "레버리지·인버스"
    assert type_of(None) is EtfType.OTHER
    assert type_of(_meta("X", None)) is EtfType.OTHER


# ── 합성 원장(고정 시드 생성기) — 검산 ③ 잔차 0, 참값과 같다 ──


def _check_market(seed: int) -> None:
    m = generate(seed)
    meta = {e.code: e for e in m.etf_meta}
    by_code: dict[str, list[EtfDay]] = {}
    for d in m.etf_days:
        by_code.setdefault(d.code, []).append(d)
    n = len(m.trading_days)
    seen = 0
    for code, rows in by_code.items():
        rows.sort(key=lambda r: r.date)
        mt = meta[code]
        flows = window_flows(rows, n, trading_days=m.trading_days, splits=m.split_events,
                             listed_on=mt.listed_on, delisted_on=mt.delisted_on)  # fmt: skip
        for f in flows:
            truth = m.etf_truth[(code, f.date)]
            assert f.status == truth.status, (seed, code, f.date)
            if truth.inflow is None:
                assert f.net_inflow is None
                continue
            assert f.net_inflow == pytest.approx(truth.inflow, rel=1e-12, abs=1e-6)
            assert f.price_effect == pytest.approx(truth.price_effect, rel=1e-12, abs=1e-6)
            assert check3(f) is True  # 보고 순자산과 대조해도 허용오차 안
            seen += 1
    assert seen > 0


def test_synthetic_ledger_fixed_seed() -> None:
    _check_market(7)


@settings(max_examples=12, deadline=None)
@given(st.integers(min_value=0, max_value=10_000))
def test_synthetic_ledger_any_seed(seed: int) -> None:
    _check_market(seed)


def test_broken_row_is_only_that_row_invalid() -> None:
    m = generate(11)
    code = m.etf_days[0].code
    rows = sorted((d for d in m.etf_days if d.code == code), key=lambda r: r.date)
    k = 5
    rows[k] = replace(rows[k], net_asset=(rows[k].net_asset or 0) + 10_000_000_000)
    meta = {e.code: e for e in m.etf_meta}[code]
    flows = window_flows(rows, len(m.trading_days), trading_days=m.trading_days,
                         splits=m.split_events, listed_on=meta.listed_on)  # fmt: skip
    bad = [f.date for f in flows if f.status == "invalid"]
    # k 일 보고 순자산이 틀리면 k 일(전날 대비)과 k+1 일(그날 대비) 대조가 깨진다
    assert bad == [rows[k].date, rows[k + 1].date]
    assert len(c3_checks(flows, datetime(2026, 10, 7, tzinfo=UTC))) == 2

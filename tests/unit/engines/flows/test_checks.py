"""검산 ①② — 0 차이·깨진 행 invalid·검산 불가 사유(docs/metrics.md §2·§5, 메인 결정 R2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kbj.core.rows import Investor, LedgerCheck
from kbj.engines.flows.checks import (
    apply_checks,
    check1,
    check1_reason,
    check2,
    check2_reason,
    ledger_checks,
)
from kbj.engines.flows.ledger import build_ledger
from tests.fixtures.synthetic.ledger_gen import (
    corrupt_investor,
    drop_investor,
    generate,
    ledger_inputs,
)
from tests.unit.engines.flows.flow_rows import D0, lrow

NOW = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def m():
    return generate(21)


def _checked(m):
    return apply_checks(build_ledger(**ledger_inputs(m)))  # type: ignore[arg-type]


def test_합성_원장은_잔차_0(m) -> None:
    led, failures = _checked(m)
    assert failures == []
    assert led.checks is not None
    assert led.checks.checked["c1"] > 1000 and led.checks.checked["c2"] > 1000
    assert led.checks.failed == {}
    # 거래정지일(투자자 행 없음)만 불가 — 사유가 남는다
    assert led.checks.reasons["c1"] == {"투자자별 행 없음": led.checks.unavailable["c1"]}
    assert all(r.usable for r in led.rows.values())


def test_깨진_행만_invalid_이고_집계에서_빠진다(m) -> None:
    d = m.trading_days[7]
    bad = corrupt_investor(m, "Q00000", d, Investor.OTHER_CORP, 3_000_000)
    led, failures = _checked(bad)
    assert [(f.code, f.date, f.check_id, f.residual) for f in failures] == [
        ("Q00000", d, "c1", 3_000_000)
    ]
    invalid = [k for k, r in led.rows.items() if not r.usable]
    assert invalid == [("Q00000", d)]
    row = led.get("Q00000", d)
    assert row is not None and row.failed_checks == ("c1",)
    agg = led.window("Q00000", d, 1)
    assert agg.n_invalid == 1 and agg.sum_turnover is None
    assert any("실패 1행" in n for n in led.notes)


def test_7구분을_깨면_검산_2만_실패(m) -> None:
    d = m.trading_days[3]
    bad = corrupt_investor(m, "Q00001", d, Investor.TRUST, 5_000_000)
    _, failures = _checked(bad)
    assert [(f.check_id, f.residual) for f in failures] == [("c2", 5_000_000)]


def test_기관_합계를_깨면_1과_2_모두_실패(m) -> None:
    d = m.trading_days[3]
    bad = corrupt_investor(m, "Q00001", d, Investor.INSTITUTION, 1_000_000)
    led, failures = _checked(bad)
    assert sorted(f.check_id for f in failures) == ["c1", "c2"]
    row = led.get("Q00001", d)
    assert row is not None and row.failed_checks == ("c1", "c2")


def test_기타법인이_없으면_검산_1_불가_사유_기록_R2(m) -> None:
    led, failures = _checked(drop_investor(m, Investor.OTHER_CORP))
    assert failures == []
    assert led.checks is not None
    assert "c1" not in led.checks.checked  # 0 으로 바꿔 '통과' 로 세지 않는다
    assert led.checks.reasons["c1"]["기타법인 미제공"] > 1000
    assert led.checks.checked["c2"] > 1000  # ② 는 그대로 된다
    assert any("검산 ①(4구분 합 0) 불가" in n and "기타법인 미제공" in n for n in led.notes)


def test_7구분이_없으면_검산_2_불가(m) -> None:
    led, _ = _checked(drop_investor(m, Investor.PENSION))
    assert led.checks is not None
    assert led.checks.reasons["c2"]["기관 7구분 미제공"] > 1000
    assert led.checks.checked["c1"] > 1000


def test_단일_함수() -> None:
    r = lrow("A00001", D0, foreign=10, inst=-3, other_corp=-2, indiv=-5)
    assert check1(r) == 0 and check1_reason(r) is None
    r2 = lrow("A00001", D0, other_corp=None, indiv=0)
    assert check1(r2) is None and check1_reason(r2) == "기타법인 미제공"
    r3 = lrow("A00001", D0, foreign=None, inst=None, other_corp=None, indiv=None)
    assert check1_reason(r3) == "투자자별 행 없음"
    assert check2(r) is None and check2_reason(r) == "기관 7구분 미제공"
    r4 = lrow("A00001", D0, inst=None)
    assert check2_reason(r4) == "기관 합계·7구분 미제공"


def test_이미_invalid_인_행은_검산하지_않는다() -> None:
    from kbj.core.quality import Quality
    from tests.unit.engines.flows.flow_rows import ledger_of

    led, failures = apply_checks(
        ledger_of([lrow("A00001", D0, foreign=5, quality=Quality.INVALID)])
    )
    assert failures == [] and led.checks is not None and led.checks.skipped_invalid == 1


def test_저장_행으로(m) -> None:
    bad = corrupt_investor(m, "Q00000", m.trading_days[2], Investor.OTHER_CORP, 1_000_000)
    _, failures = _checked(bad)
    rows = ledger_checks(failures, NOW)
    assert len(rows) == 1
    assert isinstance(rows[0], LedgerCheck)
    assert (rows[0].domain, rows[0].check_id, rows[0].residual) == ("stock", "c1", 1_000_000.0)
    assert set(rows[0].detail) == {"foreign", "inst", "other_corp", "indiv"}

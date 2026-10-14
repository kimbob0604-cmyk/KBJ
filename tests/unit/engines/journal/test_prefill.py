"""prefill 엔진 — 칸·출처·빈칸(docs/p8_design.md §4.7, ADR 0033)."""

from __future__ import annotations

from datetime import date

import pytest

from kbj.engines.journal.prefill import (
    RET_52W_MIN,
    PrefillInput,
    ScenarioCase,
    build_prefill,
    ret_52w,
)

DAY = date(2026, 10, 13)


def test_full_inputs_fill_with_sources() -> None:
    inp = PrefillInput(
        code="990001",
        day=DAY,
        price=12345.678,
        mktcap=1_000_000_000_000,
        fwd_per=11.5,
        ev_ebitda=7.2,
        band_pos=33.0,
        closes=[100.0] + [110.0] * 249,
        revenue_e=1_000,
        op_e=150,
        scenarios={
            "bear": ScenarioCase(1000.0, 8.0, 8000.0, "PER"),
            "base": ScenarioCase(1200.0, 10.0, 12000.0, "PER"),
            "bull": ScenarioCase(None, None, None, "INSUFFICIENT"),
        },
        target_mean=15000.0,
        target_n=4,
    )
    p = build_prefill(inp)
    assert p.price == pytest.approx(12345.68)
    assert p.ret_52w == pytest.approx(10.0)
    assert p.opm == pytest.approx(15.0)
    assert p.tp == {"bear": 8000.0, "base": 12000.0, "bull": None}
    assert p.per == {"bear": 8.0, "base": 10.0, "bull": None}
    assert p.tp_source == "kbj 시나리오 적정가"
    assert p.value_sources["opm"] == "consensus"
    assert p.value_sources["tp"] == "kbj"
    assert {"price", "mktcap", "fwd_per", "ev_ebitda", "band_pos", "ret_52w"} <= set(
        p.value_sources
    )


def test_consensus_target_when_no_scenario() -> None:
    p = build_prefill(PrefillInput(code="990001", day=DAY, target_mean=15000.0, target_n=3))
    assert p.tp == {"bear": None, "base": 15000.0, "bull": None}
    assert p.tp_source == "컨센서스 목표가 평균(3곳)"
    assert p.value_sources == {"tp": "consensus", "tp_source": "consensus"}


def test_nothing_is_invented() -> None:
    p = build_prefill(PrefillInput(code="990001", day=DAY))
    assert p.value_sources == {}
    assert p.price is None and p.opm is None and p.tp_source is None
    assert all(v is None for v in p.eps.values())


def test_ret_52w_needs_enough_days() -> None:
    assert ret_52w([100.0] * (RET_52W_MIN - 1)) is None
    assert ret_52w([None] * 20 + [100.0] * 229 + [120.0]) is None  # 유효 230
    assert ret_52w([50.0] + [100.0] * 300) == pytest.approx(0.0)  # 창은 마지막 250
    assert build_prefill(PrefillInput(code="990001", day=DAY, revenue_e=0, op_e=5)).opm is None

"""engine 확장 지표 연결 (services/engine/extended.py — phase3_design §3·§7-2, metrics §4·§5).

engine 사이클(`evaluate_cycle`)에 등록부 `REGISTRY` 를 꽂아:

- 지표별 행: vex·cex·gex_pc 는 범위 all·nearest·0dte, iv_term 은 칸(0dte·next_weekly·monthly),
  skew_25d 는 시리즈마다 — 값은 core.metrics 를 같은 만기 평가에 직접 부른 값, 새 지표라 shadow
- 품질: core 품질 ⊕ 범위·시리즈 입력 품질(S_ref stale → stale 이상, 옛 행 시리즈 → stale)
- 주기: `due` 가 거절한 지표는 부르지 않고 `plugins_run` 에 없다(Charm 2분은 서비스가 센다)
- 기간구조 칸은 평가하지 못한 시리즈도 본다: 그 칸에 들 수 있었던 실패한 시리즈 → invalid
  (`series_failed`), 최종거래일을 모르는 같은 종류 시리즈 → estimated(`series_no_expiry`) — 조용히
  다음 만기로 넘어가지 않는다(범위 지표 `_scope_input` 과 같은 규칙)
- 2026-09-28 작은 스냅샷(합성 — 원래 실측 발췌와 같은 자리)에서 예외 없이 값이 나고 크기·부호가
  상식 안(25Δ 풋 스큐 > 0)
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import pytest

import services.engine.evaluate as ev_mod
from core.metrics.exposure import cex, gex_put_call_ratio, vex
from core.metrics.vol import skew_25d, term_structure
from scripts.make_golden import CORE_FIXTURES
from services.bus import BasisBook
from services.engine.evaluate import CycleInput, CycleResult, ExpiryInfo, evaluate_cycle
from services.engine.extended import CHARM_EVERY_S, REGISTRY
from services.engine.records import MetricRecord
from services.engine.registry import MetricPlugin
from tests.fakes.engine_inputs import (
    CAL,
    NEAR,
    default_strikes,
    fut_row,
    kst,
    snapshot_cycle,
    synthetic_series,
)

TUE = date(2026, 10, 13)
NOV = ExpiryInfo(date(2026, 11, 12), "kis")  # 월물 202611
WKI = ExpiryInfo(date(2026, 10, 15), "kis")  # WKI 261003 (목)
WKM = ExpiryInfo(TUE, "kis")  # 가정 — 오늘 만기 위클리(0DTE 칸)
EXTENDED = ("vex", "cex", "gex_pc", "iv_term", "skew_25d")
NEAR_STRIKES = default_strikes("1082.5", 11, "2.5")  # 1082.5 ~ 1107.5


def rows(r: CycleResult, name: str) -> dict[tuple[str, str], MetricRecord]:
    return {(m.scope, m.key): m for m in r.metrics if m.metric == name}


def cycle_input(at_min: int = 0, *, futures_age_s: float = 5.0, stale_wki: bool = False):
    at = kst(TUE, 10, at_min)
    chain = [
        *synthetic_series("", "202611", NOV.last_trade_date, at, sigma=0.22),
        *synthetic_series(
            "WKI",
            "261003",
            WKI.last_trade_date,
            at - timedelta(seconds=200 if stale_wki else 0),
            sigma=0.25,
        ),
        *synthetic_series(  # 0DTE — ATM 가까운 행사가만(먼 행사가는 틱 반올림으로 KIS 폴백)
            "WKM", "261002", WKM.last_trade_date, at, sigma=0.30, strikes=NEAR_STRIKES
        ),
    ]
    return CycleInput(
        as_of=at,
        trade_date=TUE,
        session="day",
        chain=chain,
        futures=[fut_row(at - timedelta(seconds=futures_age_s))],
        expiries={("", "202611"): NOV, ("WKI", "261003"): WKI, ("WKM", "261002"): WKM},
        near_code=NEAR,
    )


def test_registered_metrics_are_stored_as_shadow_rows_with_core_values() -> None:
    r = evaluate_cycle(cycle_input(), BasisBook(), cal=CAL, registry=REGISTRY)
    assert r.status == "ok" and r.health == ()
    assert r.plugins_run == EXTENDED
    evals = {o.label: o.ev for o in r.series if o.ev is not None}
    assert set(evals) == {"M:202611", "WKI:261003", "WKM:261002"}
    scopes = {
        "all": list(evals.values()),
        "nearest": [evals["WKM:261002"]],
        "0dte": [evals["WKM:261002"]],
    }
    for name, fn in (("vex", vex), ("cex", cex)):
        got = rows(r, name)
        assert set(got) == {(s, "") for s in scopes}
        for scope, evs in scopes.items():
            want = fn(evs)
            assert got[(scope, "")].value == pytest.approx(want.value, rel=1e-12)
            assert got[(scope, "")].quality == want.quality == "ok"
            assert got[(scope, "")].flag == "shadow"
    pc = rows(r, "gex_pc")
    assert pc[("all", "")].value == pytest.approx(gex_put_call_ratio(scopes["all"]).value)
    assert pc[("all", "")].payload["call_gex"] > 0
    term = rows(r, "iv_term")
    assert set(term) == {("all", k) for k in ("0dte", "next_weekly", "monthly")}
    zero, nxt, mon = term_structure(
        [
            ("monthly", evals["M:202611"]),
            ("weekly", evals["WKI:261003"]),
            ("weekly", evals["WKM:261002"]),
        ],
        TUE,
    )
    assert term[("all", "0dte")].payload["series"] == "WKM:261002"
    assert term[("all", "next_weekly")].payload["series"] == "WKI:261003"
    assert term[("all", "monthly")].payload["series"] == "M:202611"
    assert term[("all", "0dte")].value == pytest.approx(zero.value)
    assert term[("all", "next_weekly")].value == pytest.approx(nxt.value)
    assert term[("all", "monthly")].value == pytest.approx(mon.value)
    assert term[("all", "monthly")].value == pytest.approx(0.22, abs=0.005)
    skew = rows(r, "skew_25d")
    assert set(skew) == {("series", label) for label in evals}
    for label, ev in evals.items():
        s = skew_25d(ev)
        got = skew[("series", label)]
        assert (got.value is None) == (s.value is None)
        if got.value is not None:
            assert got.value == pytest.approx(s.value, abs=1e-12)
            assert abs(got.value) < 0.01  # 스마일 없는 합성 체인
    # 30일 월물은 격자(1050~1140)가 콜 25Δ 까지 못 간다 — null(ok)
    assert skew[("series", "M:202611")].payload["reasons"] == ["call_out_of_range"]
    assert skew[("series", "WKI:261003")].value is not None
    assert not {m.metric for m in r.metrics if m.flag == "visible"} & set(EXTENDED)


def test_empty_term_slot_and_flags() -> None:
    inp = cycle_input()
    inp = CycleInput(
        **{**inp.__dict__, "chain": [c for c in inp.chain if c.mrkt_cls != "WKM"]},
    )
    r = evaluate_cycle(
        inp,
        BasisBook(),
        cal=CAL,
        registry=REGISTRY,
        flags={"vex": "visible", "skew_25d": "off"},
    )
    term = rows(r, "iv_term")
    empty = term[("all", "0dte")]
    assert (empty.value, empty.quality) == (None, "ok")
    assert empty.payload == {"empty": True, "reasons": []}
    assert {m.flag for m in rows(r, "vex").values()} == {"visible"}
    assert not rows(r, "skew_25d") and "skew_25d" not in r.plugins_run
    zero = rows(r, "vex")[("0dte", "")]
    assert (zero.value, zero.quality) == (None, "ok")  # 오늘 만기 없음 — 해당 없음


def test_input_quality_is_composed_into_the_extended_metrics() -> None:
    # S_ref 가 100초 묵었다 → 모든 산출 stale 이상, 사유가 payload 에
    r = evaluate_cycle(cycle_input(futures_age_s=100), BasisBook(), cal=CAL, registry=REGISTRY)
    for name in EXTENDED:
        for m in rows(r, name).values():
            if m.value is not None:
                assert m.quality == "stale", (name, m.scope, m.key)
    assert "s_ref_stale" in rows(r, "vex")[("all", "")].payload["reasons"]
    # WKI 행만 옛 행(전광판 200초 전) → 그 시리즈 스큐·기간구조 칸·그것이 든 범위만 stale
    r = evaluate_cycle(cycle_input(stale_wki=True), BasisBook(), cal=CAL, registry=REGISTRY)
    skew = rows(r, "skew_25d")
    assert skew[("series", "WKI:261003")].quality == "stale"
    assert "rows_stale" in skew[("series", "WKI:261003")].payload["reasons"]
    assert skew[("series", "M:202611")].quality == "ok"
    assert rows(r, "iv_term")[("all", "next_weekly")].quality == "stale"
    assert rows(r, "iv_term")[("all", "monthly")].quality == "ok"
    assert rows(r, "vex")[("all", "")].quality == "stale"
    assert rows(r, "vex")[("0dte", "")].quality == "ok"


def test_plugins_not_due_are_skipped() -> None:
    r = evaluate_cycle(
        cycle_input(), BasisBook(), cal=CAL, registry=REGISTRY, due=lambda p: p.name != "cex"
    )
    assert "cex" not in r.plugins_run and not rows(r, "cex")
    assert rows(r, "vex")
    cex_plugin = next(p for p in REGISTRY if p.name == "cex")
    assert cex_plugin.every_s == CHARM_EVERY_S == 120.0
    assert all(p.every_s is None for p in REGISTRY if p.name != "cex")
    with pytest.raises(ValueError, match="every_s"):
        MetricPlugin("x", "x", lambda _v: [], every_s=0)


def test_measured_snapshot_gives_sane_extended_metrics() -> None:
    """2026-09-28 14:52 작은 스냅샷(합성 — ATM±5 근처) — 예외·health 없이 값이 난다. 0DTE
    WKM 260904 는 델타 격자가 ±0.25 를 덮어 25Δ 스큐가 양수(풋 IV 가 높다), 월물 202610 은 발췌가
    풋 25Δ 까지 내려가지 않아 null(put_out_of_range). F 없는 시리즈는 invalid. 기간구조 월물 칸 =
    월물 ATM IV."""
    c = snapshot_cycle(CORE_FIXTURES[1])
    r = evaluate_cycle(c.inp, BasisBook(), cal=CAL, registry=REGISTRY)
    assert r.status == "ok" and r.health == ()
    assert not [m for m in r.metrics if m.payload.get("error")]
    for name in ("vex", "cex", "gex_pc"):
        v = rows(r, name)[("all", "")].value
        assert v is not None and math.isfinite(v)
    skew = rows(r, "skew_25d")
    zero = skew[("series", "WKM:260904")]
    assert zero.value is not None and 0 < zero.value < 0.5
    month = skew[("series", "M:202610")]
    assert month.value is None and month.payload["reasons"] == ["put_out_of_range"]
    assert skew[("series", "M:202611")].quality == "invalid"  # F 없음
    atm = next(m for m in r.metrics if (m.metric, m.key) == ("atm_iv", "M:202610"))
    assert rows(r, "iv_term")[("all", "monthly")].value == pytest.approx(atm.value)


DEC = ExpiryInfo(date(2026, 12, 10), "kis")  # 월물 202612


def _failing(monkeypatch: pytest.MonkeyPatch, *expiries: str) -> None:
    """주어진 만기 코드 시리즈의 평가만 예외(시리즈 하나의 결함 — 그 시리즈만 failed)."""
    real = ev_mod.evaluate_expiry

    def flaky(quotes: Any, *a: Any, **kw: Any) -> Any:
        qs = list(quotes)
        if qs[0].expiry in expiries:
            raise ZeroDivisionError("boom")
        return real(qs, *a, **kw)

    monkeypatch.setattr(ev_mod, "evaluate_expiry", flaky)


def _with_december(inp: CycleInput) -> CycleInput:
    chain = [
        *inp.chain,
        *synthetic_series("", "202612", DEC.last_trade_date, inp.as_of, sigma=0.35),
    ]
    expiries = {**inp.expiries, ("", "202612"): DEC}
    return CycleInput(**{**inp.__dict__, "chain": chain, "expiries": expiries})


def test_a_failed_nearest_monthly_does_not_let_the_next_month_fill_the_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """가장 가까운 월물(202611)이 실패하면 월물 칸은 다음 달(202612) 값을 ok 로 내지 않는다 —
    invalid(`series_failed`), 같은 사이클의 범위 지표와 같게. 그 월물이 들 수 없는 칸(0DTE·차기
    위클리)은 그대로 ok."""
    _failing(monkeypatch, "202611")
    r = evaluate_cycle(_with_december(cycle_input()), BasisBook(), cal=CAL, registry=REGISTRY)
    assert {o.label: o.status for o in r.series}["M:202611"] == "failed"
    term = rows(r, "iv_term")
    mon = term[("all", "monthly")]
    assert mon.quality == "invalid" and "series_failed" in mon.payload["reasons"]
    assert mon.payload["series"] == "M:202612"
    assert rows(r, "vex")[("all", "")].quality == "invalid"  # 범위 지표와 같은 판정
    for slot in ("0dte", "next_weekly"):
        assert term[("all", slot)].quality == "ok", slot
        assert "series_failed" not in term[("all", slot)].payload["reasons"], slot
    # 뒤 월물(202612)이 실패하면 가장 가까운 월물 칸은 그대로 ok
    monkeypatch.undo()
    _failing(monkeypatch, "202612")
    r = evaluate_cycle(_with_december(cycle_input()), BasisBook(), cal=CAL, registry=REGISTRY)
    mon = rows(r, "iv_term")[("all", "monthly")]
    assert (mon.quality, mon.payload["series"]) == ("ok", "M:202611")


def test_failed_weeklies_mark_the_slots_they_could_have_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """오늘 만기 위클리(WKM 261002)가 실패하면 0DTE 칸은 빈 칸이지만 해당 없음이 아니다 — null·
    invalid. 차기 위클리(WKI 261003)가 실패하면 그 칸 invalid. 월물 칸은 위클리 실패와 무관."""
    _failing(monkeypatch, "261002", "261003")
    r = evaluate_cycle(cycle_input(), BasisBook(), cal=CAL, registry=REGISTRY)
    term = rows(r, "iv_term")
    zero, nxt = term[("all", "0dte")], term[("all", "next_weekly")]
    assert (zero.value, zero.quality) == (None, "invalid")
    assert zero.payload == {"empty": True, "reasons": ["series_failed"]}
    assert (nxt.value, nxt.quality) == (None, "invalid")
    assert nxt.payload["reasons"] == ["series_failed"]
    assert term[("all", "monthly")].quality == "ok"


def test_a_series_without_an_expiry_date_makes_its_kind_of_slot_estimated() -> None:
    """최종거래일을 캘린더로도 셀 수 없는 위클리(WKI 261015 — 주차가 규칙 밖)는 오늘 만기나 더 이른
    위클리일 수 있다 — 0DTE·차기 위클리 칸 estimated(`series_no_expiry`), 월물 칸은 그대로 ok."""
    inp = cycle_input()
    odd = synthetic_series("WKI", "261015", WKI.last_trade_date, inp.as_of, sigma=0.25)
    r = evaluate_cycle(
        CycleInput(**{**inp.__dict__, "chain": [*inp.chain, *odd]}),
        BasisBook(),
        cal=CAL,
        registry=REGISTRY,
    )
    assert {o.label: o.status for o in r.series}["WKI:261015"] == "no_expiry"
    term = rows(r, "iv_term")
    for slot in ("0dte", "next_weekly"):
        m = term[("all", slot)]
        assert m.value is not None and m.quality == "estimated", slot
        assert "series_no_expiry" in m.payload["reasons"], slot
    assert term[("all", "monthly")].quality == "ok"

"""engine 사이클 평가 (services/engine/evaluate.py — docs/phase3_design.md §1·§2).

부작용 없는 평가를 가짜 입력(합성 체인·선물 행)과 2026-09-28 합성 작은 스냅샷(tests/fixtures/
validation)으로 확인한다:

- core 골든(tests/golden/core — scripts/make_golden.py core_golden)과 같은 값: 만기별 F·품질·사유,
  범위 순GEX·DEX·월·Flip, 만기별 감마 — engine 이 core_golden 흐름 그대로 core 를 부른다
- 야간(분기 B): 야간 행·단건 CM 만. S_ref 없음·모름 → F 없음. S_ref stale → 산출 stale·베이시스
  확정 안 함
- 확정 베이시스: ok F 로 확정, 나이(같은 저녁 0·다음 주간 1·…·3 만료), 근월물 롤이면 비움
- 옛 행(metrics §0): 전광판 90초·야간 B 보강 20분 넘게 갱신되지 않은 시리즈는 stale + health,
  베이시스는 F 에 쓴 행이 모두 90초 안일 때만 확정
- 만기 지난 시리즈(최신 행 시각·15:20 걸침), WKM·WKI 같은 만기값, 전광판 우선, KIS 그릭스 안 씀
- 만기 행이 없는 시리즈: 캘린더 계산값으로 평가, 그것도 못 세면 범위 all invalid·nearest·0dte
  estimated(조용히 빠지지 않는다)
- 격리: 시리즈·레벨·지표(등록부 플러그인) 하나의 예외는 그것만 invalid + health
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

import services.engine.evaluate as ev_mod
from core.calendar import state_at
from scripts.make_golden import CORE_FIXTURES, CORE_GOLDEN, NIGHT_DERIVED
from services.bus import BasisBook, BasisEntry
from services.engine.evaluate import (
    CycleInput,
    CycleResult,
    ExpiryInfo,
    SeriesOutcome,
    evaluate_cycle,
    near_code_from_quotes,
    series_quotes,
)
from services.engine.records import LevelRecord, MetricRecord
from services.engine.registry import CycleView, MetricPlugin, PluginValue
from tests.fakes.engine_inputs import (
    CAL,
    NEAR,
    default_strikes,
    fut_row,
    kst,
    snapshot_cycle,
    synthetic_series,
)

D28 = date(2026, 9, 28)  # 월 — WKM 260904 만기일
TUE = date(2026, 10, 13)  # 화 (10-09 한글날 휴장 다음 주)
NOV = ExpiryInfo(date(2026, 11, 12), "kis")  # 월물 202611
REL = 1e-12


def level(r: CycleResult, scope: str, name: str) -> LevelRecord:
    (x,) = [lv for lv in r.levels if (lv.scope, lv.name) == (scope, name)]
    return x


def metric(r: CycleResult, name: str, scope: str, key: str = "") -> MetricRecord:
    (x,) = [m for m in r.metrics if (m.metric, m.scope, m.key) == (name, scope, key)]
    return x


def outcome(r: CycleResult, label: str) -> SeriesOutcome:
    (x,) = [o for o in r.series if o.label == label]
    return x


def cycle(
    at: datetime,
    chain: Sequence[Any],
    *,
    futures: Sequence[Any] | None = None,
    expiries: dict[Any, ExpiryInfo] | None = None,
    near: str | None = NEAR,
    **kw: Any,
) -> CycleInput:
    st = state_at(at, CAL)
    assert st.trade_date is not None and st.session is not None
    return CycleInput(
        as_of=at,
        trade_date=st.trade_date,
        session=st.session,
        chain=list(chain),
        futures=list(futures if futures is not None else [fut_row(at - timedelta(seconds=5))]),
        expiries=expiries if expiries is not None else {("", "202611"): NOV},
        near_code=near,
        **kw,
    )


# ── core 골든과 같다 ──


def test_reproduces_the_core_golden_for_the_measured_snapshots_and_the_night_derivation() -> None:
    golden = json.loads(CORE_GOLDEN.read_text(encoding="utf-8"))["snapshots"]
    cycles = [
        snapshot_cycle(CORE_FIXTURES[0]),
        snapshot_cycle(CORE_FIXTURES[1]),
        snapshot_cycle(NIGHT_DERIVED.source, shift=NIGHT_DERIVED.shift),
    ]
    book = BasisBook()
    results: list[CycleResult] = []
    for i, (g, c) in enumerate(zip(golden, cycles, strict=True)):
        r = evaluate_cycle(c.inp, book, cal=CAL)
        results.append(r)
        assert r.status == "ok" and r.health == ()
        assert (r.trade_date.isoformat(), r.session) == (g["trade_date"], g["session"])
        for e in g["expiries"]:
            o = outcome(r, c.labels[e["label"]])
            assert o.status == "evaluated" and o.ev is not None
            fwd = o.ev.forward
            assert fwd.F == e["forward"]["F_pt"] and fwd.quality == e["forward"]["quality"]
            assert list(fwd.reasons) == e["forward"]["reasons"]
            if i < 2:  # 야간 파생은 베이시스 나이가 6거래일 — 아래
                assert list(fwd.notes) == e["forward"]["notes"]
            assert o.basis == (None if e["basis_in_pt"] is None else Decimal(str(e["basis_in_pt"])))
        for x in g["expired"]:
            assert outcome(r, c.labels[x["label"]]).status == "expired"
        for scope, gs in g["scopes"].items():
            for name in ("net_gex", "dex"):
                m = metric(r, name, scope)
                assert m.value == pytest.approx(gs[name]["value_won"], rel=REL)
                assert m.quality == gs[name]["quality"] and m.flag == "visible"
            for name in ("call_wall", "put_wall", "abs_gamma"):
                lv = level(r, scope, name)
                assert lv.value == float(gs[name]["strike"]) and lv.quality == gs[name]["quality"]
                assert lv.detail["gex_won"] == pytest.approx(gs[name]["value_won"], rel=REL)
            flip = level(r, scope, "flip")
            assert flip.value == gs["flip"]["level_pt"]
            assert flip.detail["crossings"] == gs["flip"]["crossings_pt"]
            assert flip.detail["multi_cross"] is gs["flip"]["multi_cross"]
            assert level(r, scope, "flip_distance").value == gs["flip"]["distance_pct"]
        for x in g["gamma_by_expiry"]:
            m = metric(r, "expiry_gamma", "series", c.labels[x["expiry"]])
            assert m.value == (None if x["value_won"] is None else pytest.approx(x["value_won"]))
        book = r.basis
    # 확정 베이시스: 14:27 ok F → 14:52 나이 0 으로 교차 확인, 야간 파생(10-07 밤, 10-08 귀속)은
    # 09-28 주간 확정에서 6거래일 — 이월 상한(2)을 넘어 교차 확인을 건너뛴다(F 는 패리티 그대로)
    night = outcome(results[-1], "M:202610")
    assert night.basis_age == 6 and night.ev is not None
    assert night.ev.forward.notes == ("basis_carry_expired",)
    assert book.entries["M:202610"].session == "night"  # 야간 ok F 로 다시 확정(나이 0 부터)


def test_the_snapshot_cycle_is_quick_enough_for_every_chain_ready() -> None:
    import time

    c = snapshot_cycle(CORE_FIXTURES[1])
    t0 = time.perf_counter()
    evaluate_cycle(c.inp, BasisBook(), cal=CAL)
    assert time.perf_counter() - t0 < 5.0


# ── 야간·S_ref ──


def test_night_uses_night_rows_and_the_single_cm_quote_only() -> None:
    night = kst(TUE, 21, 0)  # 10-13 밤 → 10-14 귀속
    day_rows = synthetic_series("", "202611", NOV.last_trade_date, kst(TUE, 15, 40), oi=9999)
    night_rows = synthetic_series("", "202611", NOV.last_trade_date, night, source="fill", F=1093.0)
    futures = [
        fut_row(night - timedelta(seconds=3), "1093.20"),  # CM 단건
        fut_row(night - timedelta(seconds=1), "1100.00", session="day", trade_date=TUE),
    ]
    inp = cycle(night, [*day_rows, *night_rows], futures=futures)
    assert (inp.trade_date, inp.session) == (date(2026, 10, 14), "night")
    r = evaluate_cycle(inp, BasisBook(), cal=CAL)
    assert r.s_ref is not None and r.s_ref.price == Decimal("1093.20")
    assert r.s_ref.quote.market == "CM" and r.s_ref.quote.source == "single"
    o = outcome(r, "M:202611")
    assert o.ev is not None and o.ev.F == pytest.approx(1093.0, abs=0.05)
    assert {q.quote_source for q in r.option_iv} == {"fill"}
    assert all(q.oi == 500 for q in r.option_iv)  # 주간 전광판 행(OI 9999)을 섞지 않았다
    assert {x.session for x in [*r.levels, *r.metrics, *r.strike_gex]} == {"night"}


def test_without_s_ref_there_is_no_f_and_the_basis_is_kept() -> None:
    at = kst(TUE, 10, 0)
    book = BasisBook(
        near_code=NEAR,
        entries={
            "M:202611": BasisEntry(
                basis=Decimal("-0.1"), trade_date=TUE, session="day", confirmed_at=at
            )
        },
    )
    chain = synthetic_series("", "202611", NOV.last_trade_date, at)
    r = evaluate_cycle(cycle(at, chain, futures=[]), book, cal=CAL)
    assert (r.status, r.quality, r.s_ref) == ("no_s_ref", "stale", None)
    assert r.levels == r.metrics == r.strike_gex == r.option_iv == ()
    assert [h.kind for h in r.health] == ["engine_no_s_ref"] and r.basis == book
    # 야간에 주간 전광판 F 행만 있으면 대체하지 않는다
    night = kst(TUE, 21, 0)
    day_f = fut_row(night - timedelta(seconds=1), session="day", trade_date=TUE)
    rows = synthetic_series("", "202611", NOV.last_trade_date, night, source="fill")
    r = evaluate_cycle(cycle(night, rows, futures=[day_f]), book, cal=CAL)
    assert r.status == "no_s_ref"
    r = evaluate_cycle(cycle(at, chain, near=None), book, cal=CAL)
    assert r.status == "no_s_ref" and [h.kind for h in r.health] == ["engine_no_near_code"]


def test_a_stale_s_ref_marks_every_output_and_confirms_no_basis() -> None:
    at = kst(TUE, 10, 0)
    chain = synthetic_series("", "202611", NOV.last_trade_date, at)
    r = evaluate_cycle(
        cycle(at, chain, futures=[fut_row(at - timedelta(seconds=91))]), BasisBook(), cal=CAL
    )
    assert r.status == "ok" and r.s_ref is not None and r.s_ref.quality == "stale"
    assert all(x.quality != "ok" for x in [*r.levels, *r.metrics])
    assert "s_ref_stale" in level(r, "all", "call_wall").reasons
    assert r.basis.entries == {}  # stale S_ref 로는 베이시스를 확정하지 않는다
    fresh = evaluate_cycle(cycle(at, chain), BasisBook(), cal=CAL)
    assert level(fresh, "all", "call_wall").quality == "ok"
    assert set(fresh.basis.entries) == {"M:202611"}


WKI15 = ("WKI", "261015")
WKI15_EXP = ExpiryInfo(date(2026, 10, 15), "kis")


def test_old_rows_make_their_series_stale_and_confirm_no_basis() -> None:
    """metrics §0 — 입력 스냅샷이 REST 90초 넘게 갱신되지 않았으면 stale. 전광판 요청이 끊긴 시리즈
    (다른 시리즈는 알림을 낸다)의 옛 행은 ok 로 평가되지 않고, 옛 옵션 가격으로 만든 F 와 지금 S_ref
    의 차를 베이시스로 확정하지 않는다."""
    as_of = kst(TUE, 14, 0)
    exp = {("", "202611"): NOV, WKI15: WKI15_EXP}
    now = as_of - timedelta(seconds=5)
    fresh = synthetic_series("", "202611", NOV.last_trade_date, now, F=1115)
    old = synthetic_series("WKI", "261015", WKI15_EXP.last_trade_date, kst(TUE, 9, 10), F=1095)
    futures = [fut_row(as_of - timedelta(seconds=5), "1115.00")]
    inp = cycle(as_of, [*fresh, *old], futures=futures, expiries=exp)
    r = evaluate_cycle(inp, BasisBook(), cal=CAL)
    o = outcome(r, "WKI:261015")
    assert o.status == "evaluated" and o.input_quality == "stale"
    assert o.input_reasons[0] == "rows_stale"
    assert outcome(r, "M:202611").input_quality == "ok"
    assert set(r.basis.entries) == {"M:202611"}  # 옛 WKI 행으로는 -20pt 를 확정하지 않는다
    for scope in ("all", "nearest"):  # WKI 10-15 가 최근접
        m = metric(r, "net_gex", scope)
        assert m.quality == "stale" and "WKI:261015:rows_stale" in m.payload["reasons"], scope
        assert level(r, scope, "call_wall").quality == "stale"
    assert level(r, "nearest", "expected_move_calendar").quality == "stale"
    assert metric(r, "atm_iv", "series", "WKI:261015").quality == "stale"
    assert r.quality == "stale"
    assert {x.quality for x in r.strike_gex if x.mrkt_cls == "WKI"} == {"stale"}
    assert {x.quality for x in r.option_iv if x.mrkt_cls == "WKI"} == {"stale"}
    assert "stale" not in {x.quality for x in r.strike_gex if x.mrkt_cls == ""}
    assert [h.subject for h in r.health if h.kind == "engine_series_stale"] == ["WKI:261015"]


def test_the_board_age_limit_is_ninety_seconds_even_with_fresh_fill_rows() -> None:
    """전광판 행이 있는 시리즈는 최신 전광판 행으로 본다 — 먼 행사가 보강 행이 새로 와도 전광판(ATM
    구간)이 끊겼으면 stale. 90초는 ok, 넘으면 stale(PLAN §6.1)."""
    as_of = kst(TUE, 10, 0)
    for age, want in ((90, "ok"), (91, "stale")):
        rows = synthetic_series("", "202611", NOV.last_trade_date, as_of - timedelta(seconds=age))
        r = evaluate_cycle(cycle(as_of, rows), BasisBook(), cal=CAL)
        assert outcome(r, "M:202611").input_quality == want, age
        assert (set(r.basis.entries) == {"M:202611"}) is (want == "ok"), age
    board = synthetic_series("", "202611", NOV.last_trade_date, as_of - timedelta(seconds=300))
    far = synthetic_series(
        "",
        "202611",
        NOV.last_trade_date,
        as_of - timedelta(seconds=2),
        source="fill",
        strikes=[Decimal(1300), Decimal("1302.5")],
    )
    r = evaluate_cycle(cycle(as_of, [*board, *far]), BasisBook(), cal=CAL)
    assert outcome(r, "M:202611").input_quality == "stale" and r.basis.entries == {}


def test_a_night_fill_only_series_uses_the_fill_rotation_limit_and_fresh_f_for_the_basis() -> None:
    """야간 B(단건 보강만): 차기 위클리는 보강 2 순환의 월물 꼬리 동안 행이 오지 않는다 — 몇 분 된
    최신 행은 stale 이 아니다(순환 한 바퀴 + 여유 20분 [확인 필요]). 베이시스 확정은 F 에 쓴 행이
    모두 90초 안일 때만."""
    night = kst(TUE, 21, 0)
    for age, want_q, confirmed in ((30, "ok", True), (100, "ok", False), (1201, "stale", False)):
        rows = synthetic_series(
            "", "202611", NOV.last_trade_date, night - timedelta(seconds=age), source="fill"
        )
        r = evaluate_cycle(cycle(night, rows), BasisBook(), cal=CAL)
        o = outcome(r, "M:202611")
        assert o.input_quality == want_q and o.ev is not None, age
        assert o.ev.forward.quality == "ok", age
        assert (set(r.basis.entries) == {"M:202611"}) is confirmed, age


def test_near_code_fallback_is_the_shortest_remaining_priced_quote() -> None:
    at = kst(TUE, 10, 0)
    rows = [
        fut_row(at, "1085.00", code="A01703", remaining_days=150),
        fut_row(at, "1095.10", code=NEAR, remaining_days=58),
        fut_row(at, "1090.00", code="A01999", remaining_days=10, session="night", trade_date=TUE),
    ]
    assert near_code_from_quotes(rows, "day") == NEAR
    assert near_code_from_quotes(rows, "night") == "A01999"
    assert near_code_from_quotes([], "day") is None


# ── 확정 베이시스 ──


def _calls_only(at: datetime, **kw: Any) -> list[Any]:
    """풋 행이 없는 시리즈 — 패리티 행사가 0 개라 F 는 선물 대체(기준가가 있을 때만)."""
    return [r for r in synthetic_series("", "202611", NOV.last_trade_date, at, **kw) if r.cp == "C"]


def test_basis_is_confirmed_by_an_ok_f_and_carried_up_to_two_trading_days() -> None:
    day1 = kst(TUE, 10, 0)
    r = evaluate_cycle(
        cycle(day1, synthetic_series("", "202611", NOV.last_trade_date, day1)), BasisBook(), cal=CAL
    )
    e = r.basis.entries["M:202611"]
    o = outcome(r, "M:202611")
    assert o.ev is not None and o.ev.forward.quality == "ok"
    assert e.basis == Decimal(str(o.ev.F)) - Decimal("1095.10")
    assert (e.trade_date, e.session, r.basis.near_code) == (TUE, "day", NEAR)
    book = r.basis
    steps = [
        (kst(TUE, 21, 0), 0, ("futures_fallback",)),  # 같은 저녁 야간 — 이월 아님
        (kst(date(2026, 10, 14), 10, 0), 1, ("futures_fallback", "basis_carried")),
        (kst(date(2026, 10, 15), 10, 0), 2, ("futures_fallback", "basis_carried")),
        (kst(date(2026, 10, 16), 10, 0), 3, ("basis_carry_expired",)),  # 상한 초과 — F 없음
    ]
    for at, age, reasons in steps:
        src = "fill" if at.hour >= 18 else "board"
        r = evaluate_cycle(cycle(at, _calls_only(at, source=src)), book, cal=CAL)
        o = outcome(r, "M:202611")
        assert o.basis_age == age and o.ev is not None
        assert o.ev.forward.reasons == reasons, (at, o.ev.forward)
        if age < 3:
            assert o.ev.F == pytest.approx(1095.10 + float(e.basis))
        else:
            assert o.ev.F is None and metric(r, "net_gex", "all").quality == "invalid"
        assert r.basis.entries["M:202611"] == e  # 대체 F(estimated)로는 다시 확정하지 않는다
        book = r.basis


def test_a_near_month_roll_clears_the_basis() -> None:
    at = kst(TUE, 10, 0)
    old = BasisBook(
        near_code=NEAR,
        entries={
            "M:202611": BasisEntry(
                basis=Decimal("-9"), trade_date=TUE, session="day", confirmed_at=at
            )
        },
    )
    rows = _calls_only(at)
    futures = [fut_row(at - timedelta(seconds=5), "1085.00", code="A01703", remaining_days=150)]
    r = evaluate_cycle(cycle(at, rows, futures=futures, near="A01703"), old, cal=CAL)
    assert r.basis.near_code == "A01703" and r.basis.entries == {}
    assert "engine_basis_rolled" in [h.kind for h in r.health]
    o = outcome(r, "M:202611")
    assert o.basis is None and o.ev is not None and o.ev.F is None  # 옛 베이시스로 대체하지 않았다


# ── 시리즈 ──


def test_expired_series_are_judged_at_their_latest_row_and_the_straddling_cycle() -> None:
    wkm = ("WKM", "260904")
    exp = {wkm: ExpiryInfo(D28, "kis"), ("", "202611"): NOV}
    before = kst(D28, 15, 19, 50)
    last = synthetic_series("WKM", "260904", D28, before)
    monthly = synthetic_series("", "202611", NOV.last_trade_date, kst(D28, 15, 20, 5))
    as_of = kst(D28, 15, 20, 5)
    # 15:20 을 걸친 사이클 — 이 사이클에 행이 왔다(직전 as_of 15:19:40 뒤) → 마지막 스냅샷을 평가
    r = evaluate_cycle(
        cycle(as_of, [*last, *monthly], expiries=exp, fresh_since=kst(D28, 15, 19, 40)),
        BasisBook(),
        cal=CAL,
    )
    assert outcome(r, "WKM:260904").status == "evaluated"
    assert outcome(r, "WKM:260904").now == before  # 평가 시각은 그 시리즈 최신 행
    # 다음 사이클 — 새 행이 없다 → 뺀다
    r = evaluate_cycle(
        cycle(as_of + timedelta(seconds=30), [*last, *monthly], expiries=exp, fresh_since=as_of),
        BasisBook(),
        cal=CAL,
    )
    assert outcome(r, "WKM:260904").status == "expired"
    assert metric(r, "net_gex", "nearest").payload["expiries"] == ["M:202611"]
    # 최신 행이 15:20 뒤면 늘 뺀다
    late = synthetic_series("WKM", "260904", D28, kst(D28, 15, 20, 1))
    r = evaluate_cycle(cycle(as_of, [*late, *monthly], expiries=exp), BasisBook(), cal=CAL)
    assert outcome(r, "WKM:260904").status == "expired"


def test_wkm_and_wki_with_the_same_expiry_value_are_separate_series() -> None:
    at = kst(D28, 10, 0)
    exp = {
        ("WKM", "261001"): ExpiryInfo(date(2026, 10, 6), "kis"),
        ("WKI", "261001"): ExpiryInfo(date(2026, 10, 1), "kis"),
    }
    rows = [
        *synthetic_series("WKM", "261001", date(2026, 10, 6), at, oi=100),
        *synthetic_series("WKI", "261001", date(2026, 10, 1), at, oi=300),
    ]
    r = evaluate_cycle(cycle(at, rows, expiries=exp), BasisBook(), cal=CAL)
    assert {o.label: o.status for o in r.series} == {
        "WKI:261001": "evaluated",
        "WKM:261001": "evaluated",
    }
    assert {x.mrkt_cls for x in r.strike_gex} == {"WKM", "WKI"}
    assert {m.key for m in r.metrics if m.metric == "atm_iv"} == {"WKI:261001", "WKM:261001"}
    assert metric(r, "net_gex", "nearest").payload["expiries"] == ["WKI:261001"]  # 10-01 이 먼저
    assert set(r.basis.entries) == {"WKI:261001", "WKM:261001"}


def test_a_series_without_an_expiry_row_falls_back_to_the_calendar_date() -> None:
    """series_expiries 에 없는 시리즈는 빼지 않는다 — poller 가 KIS 값을 못 받았을 때와 같이 캘린더
    계산값(원천 calendar — 만기 목록 품질 estimated)으로 평가하고 health."""
    at = kst(TUE, 10, 0)
    rows = [
        *synthetic_series("", "202611", NOV.last_trade_date, at),
        *synthetic_series("WKM", "261003", date(2026, 10, 19), at),  # 10월 셋째 월요일
    ]
    r = evaluate_cycle(cycle(at, rows), BasisBook(), cal=CAL)
    o = outcome(r, "WKM:261003")
    assert o.status == "evaluated" and o.expiry == ExpiryInfo(date(2026, 10, 19), "calendar")
    assert [h.subject for h in r.health if h.kind == "engine_no_expiry"] == ["WKM:261003"]
    assert metric(r, "net_gex", "nearest").payload["expiries"] == ["WKM:261003"]
    assert set(metric(r, "net_gex", "all").payload["expiries"]) == {"WKM:261003", "M:202611"}
    assert metric(r, "net_gex", "all").quality == "ok"
    assert metric(r, "expiry_gamma", "series", "WKM:261003").quality == "estimated"


def test_a_series_with_no_expiry_date_at_all_marks_the_scopes_it_may_be_in() -> None:
    """최종거래일을 캘린더로도 셀 수 없는 시리즈(만기값이 규칙 밖)는 평가하지 못한다 — 그래도 조용히
    빠지지 않는다: all 은 그 OI 가 빠져 invalid, nearest·0dte 는 그 시리즈가 더 이를 수 있어
    estimated(`series_no_expiry`) [확인 필요]."""
    at = kst(TUE, 10, 0)
    rows = [
        *synthetic_series("", "202611", NOV.last_trade_date, at),
        *synthetic_series("WKI", "261015", WKI15_EXP.last_trade_date, at, oi=5000),
    ]
    r = evaluate_cycle(cycle(at, rows), BasisBook(), cal=CAL)
    assert outcome(r, "WKI:261015").status == "no_expiry"
    assert [h.subject for h in r.health if h.kind == "engine_no_expiry"] == ["WKI:261015"]
    for scope, want in (("all", "invalid"), ("nearest", "estimated"), ("0dte", "estimated")):
        m = metric(r, "net_gex", scope)
        assert m.quality == want and "series_no_expiry" in m.payload["reasons"], scope
        lv = level(r, scope, "call_wall")
        assert lv.quality == want and "series_no_expiry" in lv.reasons, scope
    assert metric(r, "net_gex", "nearest").payload["expiries"] == ["M:202611"]
    assert r.quality == "invalid"
    known = evaluate_cycle(
        cycle(at, rows, expiries={("", "202611"): NOV, WKI15: WKI15_EXP}), BasisBook(), cal=CAL
    )
    assert metric(known, "net_gex", "all").quality == "ok"
    assert metric(known, "net_gex", "nearest").payload["expiries"] == ["WKI:261015"]


def test_series_quotes_prefer_the_board_row_and_mark_missing_oi() -> None:
    at = kst(TUE, 10, 0)
    board = synthetic_series("", "202611", NOV.last_trade_date, at, strikes=[Decimal(1095)])
    fill = synthetic_series(
        "",
        "202611",
        NOV.last_trade_date,
        at + timedelta(seconds=9),
        source="fill",
        strikes=[Decimal(1095), Decimal("1097.5")],
    )
    no_oi = board[0].model_copy(update={"strike": Decimal(1100), "oi": None})
    bad = board[1].model_copy(update={"strike": Decimal("1102.5"), "quality": "invalid"})
    qs, q, reasons = series_quotes([*board, *fill, no_oi, bad], NOV.last_trade_date)
    got = {(x.strike, x.cp): x.source for x in qs}
    assert got[(Decimal(1095), "C")] == got[(Decimal(1095), "P")] == "board"
    assert got[(Decimal("1097.5"), "C")] == "fill"
    assert (Decimal("1102.5"), "P") not in got  # invalid 행은 쓰지 않는다
    assert [x.oi for x in qs if x.strike == 1100] == [0]
    assert (q, reasons) == ("estimated", ("oi_missing",))


def test_kis_greeks_in_the_rows_change_nothing() -> None:
    at = kst(TUE, 10, 0)
    clean = synthetic_series("", "202611", NOV.last_trade_date, at)
    junk = synthetic_series(
        "",
        "202611",
        NOV.last_trade_date,
        at,
        extra={
            "delta": Decimal("0.9"),
            "gamma": Decimal("9"),
            "theta": Decimal("-9"),
            "vega": Decimal("9"),
            "rho": Decimal("9"),
        },
    )
    a = evaluate_cycle(cycle(at, clean), BasisBook(), cal=CAL)
    b = evaluate_cycle(cycle(at, junk), BasisBook(), cal=CAL)
    assert a.levels == b.levels and a.metrics == b.metrics and a.option_iv == b.option_iv


def test_master_strikes_choose_the_atm_when_the_board_is_cut() -> None:
    at = kst(TUE, 10, 0)
    high = [Decimal(1100) + Decimal("2.5") * i for i in range(10)]  # 전광판이 윗부분만 준 꼴
    rows = synthetic_series("", "202611", NOV.last_trade_date, at, strikes=high)
    plain = evaluate_cycle(cycle(at, rows), BasisBook(), cal=CAL)
    master = {("", "202611"): default_strikes()}
    full = evaluate_cycle(cycle(at, rows, strikes=master), BasisBook(), cal=CAL)
    p, f = outcome(plain, "M:202611").ev, outcome(full, "M:202611").ev
    assert p is not None and f is not None
    assert p.forward.atm == Decimal(1100) and f.forward.atm == Decimal(1095)


def test_a_quarterly_monthly_is_checked_against_its_own_futures_month() -> None:
    at = kst(TUE, 10, 0)
    dec = ("", "202612")
    exp = {dec: ExpiryInfo(date(2026, 12, 10), "kis")}
    rows = synthetic_series("", "202612", date(2026, 12, 10), at, F=1095.0)
    inp = cycle(at, rows, expiries=exp, futures_months={NEAR: "202612", "A01703": "202703"})
    o = outcome(evaluate_cycle(inp, BasisBook(), cal=CAL), "M:202612")
    assert o.ev is not None and o.ev.forward.reference is not None
    assert o.ev.forward.reference.kind == "same_month"


def test_zero_dte_is_empty_on_a_day_without_an_expiry() -> None:
    at = kst(TUE, 10, 0)
    r = evaluate_cycle(
        cycle(at, synthetic_series("", "202611", NOV.last_trade_date, at)), BasisBook(), cal=CAL
    )
    for name in ("call_wall", "flip", "expected_move_calendar", "top_levels"):
        lv = level(r, "0dte", name)
        assert (lv.value, lv.quality) == (None, "ok"), name
    m = metric(r, "net_gex", "0dte")
    assert (m.value, m.quality) == (None, "ok")
    move = level(r, "nearest", "expected_move_calendar")
    trading = level(r, "nearest", "expected_move_trading")
    assert move.value is not None and trading.value is not None and trading.value > move.value
    assert move.detail["expiry"] == "M:202611" and move.detail["lower"] < move.detail["upper"]
    top = level(r, "all", "top_levels")
    assert top.value is not None and top.value == len(top.detail["levels"]) and top.value <= 5


def test_option_iv_rows_record_the_iv_source_and_the_rescaling() -> None:
    c = snapshot_cycle(CORE_FIXTURES[1])  # 14:52 — 0DTE KIS 폴백 행이 T 환산된다
    r = evaluate_cycle(c.inp, BasisBook(), cal=CAL)
    kis = [x for x in r.option_iv if x.source == "kis"]
    own = [x for x in r.option_iv if x.source == "self"]
    assert kis and own
    assert all(x.t_kis is not None for x in kis if x.rescaled)
    assert any(x.rescaled and x.mrkt_cls == "WKM" and x.expiry == "260904" for x in kis)
    assert all(x.t_kis is None and not x.rescaled for x in own)
    assert all((x.excluded is None) == (x.gamma is not None) for x in r.option_iv)
    assert all(x.excluded != "no_price" for x in r.option_iv)  # IV 를 시도한 종목만


# ── 격리 ──


def test_one_failing_series_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    at = kst(D28, 10, 0)
    wkm = ("WKM", "260904")
    exp = {wkm: ExpiryInfo(D28, "kis"), ("", "202611"): NOV}
    rows = [
        *synthetic_series("WKM", "260904", D28, at),
        *synthetic_series("", "202611", NOV.last_trade_date, at),
    ]
    real = ev_mod.evaluate_expiry

    def flaky(quotes: Any, *a: Any, **kw: Any) -> Any:
        qs = list(quotes)
        if qs[0].expiry == "202611":
            raise ZeroDivisionError("boom")
        return real(qs, *a, **kw)

    monkeypatch.setattr(ev_mod, "evaluate_expiry", flaky)
    r = evaluate_cycle(cycle(at, rows, expiries=exp), BasisBook(), cal=CAL)
    assert outcome(r, "M:202611").status == "failed"
    assert outcome(r, "WKM:260904").status == "evaluated"
    assert [h.kind for h in r.health] == ["engine_series_failed"]
    net_all = metric(r, "net_gex", "all")
    assert net_all.quality == "invalid" and "series_failed" in net_all.payload["reasons"]
    assert metric(r, "net_gex", "nearest").quality != "invalid"  # 실패한 월물은 최근접이 아니다
    assert metric(r, "net_gex", "0dte").value is not None and r.quality == "invalid"


def test_one_failing_level_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    at = kst(TUE, 10, 0)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("flip bug")

    monkeypatch.setattr(ev_mod, "gamma_flip", boom)
    r = evaluate_cycle(
        cycle(at, synthetic_series("", "202611", NOV.last_trade_date, at)), BasisBook(), cal=CAL
    )
    for scope in ("all", "nearest", "0dte"):
        for name in ("flip", "flip_distance"):
            lv = level(r, scope, name)
            assert (lv.value, lv.quality) == (None, "invalid"), (scope, name)
        assert level(r, scope, "call_wall").quality == "ok"
    assert {h.kind for h in r.health} == {"engine_level_failed"}
    assert metric(r, "net_gex", "all").quality == "ok"


def test_registered_metrics_follow_their_flags_and_fail_alone() -> None:
    at = kst(TUE, 10, 0)
    seen: list[CycleView] = []

    def shadow(view: CycleView) -> list[PluginValue]:
        seen.append(view)
        return [PluginValue("all", 1.5, "ok", payload={"n": len(view.evals)})]

    def never(_view: CycleView) -> list[PluginValue]:
        raise AssertionError("off 인 지표를 불렀다")

    def broken(_view: CycleView) -> list[PluginValue]:
        raise ValueError("bug")

    registry = [
        MetricPlugin("vex", "vex", shadow),
        MetricPlugin("charm", "charm", never),
        MetricPlugin("skew_25d", "skew_25d", broken),
    ]
    r = evaluate_cycle(
        cycle(at, synthetic_series("", "202611", NOV.last_trade_date, at)),
        BasisBook(),
        cal=CAL,
        registry=registry,
        flags={"charm": "off", "skew_25d": "visible"},
    )
    vex = metric(r, "vex", "all")
    assert (vex.value, vex.flag, vex.payload) == (1.5, "shadow", {"n": 1})  # 새 지표 기본 shadow
    assert not [m for m in r.metrics if m.metric == "charm"]
    bad = metric(r, "skew_25d", "all")
    assert (bad.value, bad.quality, bad.flag) == (None, "invalid", "visible")
    assert [h.kind for h in r.health] == ["engine_metric_failed"]
    (view,) = seen
    assert set(view.scopes) == {"all", "nearest", "0dte"} and view.s_ref.price == Decimal("1095.10")


def test_phase2_outputs_follow_their_flags_and_are_visible_by_default() -> None:
    """Phase 2 핵심도 플래그를 따른다(설계 §4) — 기본은 카탈로그 visible, shadow 면 행의 flag 가
    shadow(계산·저장은 그대로). 기대변동폭 플래그 하나가 달력·거래 기준 두 레벨을 정한다. 로더는
    핵심 off 를 막지만 직접 넘긴 off 도 행을 떨구지 않고 shadow 로 둔다(값은 늘 계산 — 사이클 품질·
    레벨 사이 의존)."""
    at = kst(TUE, 10, 0)
    inp = cycle(at, synthetic_series("", "202611", NOV.last_trade_date, at))
    plain = evaluate_cycle(inp, BasisBook(), cal=CAL)
    assert {lv.flag for lv in plain.levels} == {"visible"}
    assert {m.flag for m in plain.metrics} == {"visible"}  # net_gex·dex·atm_iv·expiry_gamma
    r = evaluate_cycle(
        inp,
        BasisBook(),
        cal=CAL,
        flags={"flip": "shadow", "expected_move": "shadow", "net_gex": "shadow", "atm_iv": "off"},
    )
    shadow = {(lv.scope, lv.name) for lv in r.levels if lv.flag == "shadow"}
    names = ("flip", "expected_move_calendar", "expected_move_trading")
    assert shadow == {(s, n) for s in ("all", "nearest", "0dte") for n in names}
    assert level(r, "all", "flip").value == level(plain, "all", "flip").value
    assert level(r, "all", "flip_distance").flag == "visible"
    assert {(m.metric, m.flag) for m in r.metrics} == {
        ("net_gex", "shadow"),
        ("dex", "visible"),
        ("atm_iv", "shadow"),
        ("expiry_gamma", "visible"),
    }
    assert metric(r, "net_gex", "all").value == metric(plain, "net_gex", "all").value
    assert r.quality == plain.quality  # 사이클 품질은 플래그와 무관

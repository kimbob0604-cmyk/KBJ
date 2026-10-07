"""engine 플로우 지표 (services/engine/flow.py — phase3_design §3·§7-3, metrics §6).

사이클 등록부(`FLOW_REGISTRY`) — PCR·맥스페인:

- 행: pcr_oi·pcr_volume 은 시리즈마다(key 시리즈 라벨)와 전체(all), max_pain 은 시리즈마다 — 값은
  core.metrics.flow 를 같은 만기 평가의 종목에 직접 부른 값, 새 지표라 shadow(플래그 pcr·max_pain)
- 품질: F 를 쓰지 않아 S_ref 품질과 무관(S_ref stale 이어도 ok), 그 시리즈 체인 행 입력 품질(옛 행
  stale·OI 없음 estimated)은 합성, 평가하지 못한 시리즈가 있으면 전체 PCR invalid
- 맥스페인: 후보는 마스터 상장 행사가, 행이 한 번도 오지 않은 상장 행사가가 있으면 estimated
- engine 서비스의 기본 등록부에 든다

OI 증감(`OiTracker` — oi_changes 표):

- 첫 스냅샷은 종목마다 증감 없음(ts = 체인 행 시각), 같은 행(ts 그대로)은 스냅샷이 아니다, 증감이
  0 인 스냅샷은 쓰지 않는다, OI 없는 행은 건너뛴다
- 줄었다가 다음 스냅샷에 90% 이상 복구 → 새 칸과 앞 칸(같은 키로 고쳐 쓰기) 이상치 + health
- 칸 품질 = 이번·직전 두 스냅샷 체인 행 품질 중 나쁜 것(앞 칸 품질을 잇지 않는다)
- 세션이 바뀌면 비운다, 만기 지난 시리즈는 보지 않는다
- 세션 중간 재기동: 같은 최신 행을 첫 스냅샷으로 다시 써도 저장된 칸(증감·이상치) 그대로
- 서비스: 사이클마다 oi_changes 에 쓰고, 플래그 off 면 계산하지 않고, 예외는 그것만 health
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from core.metrics.flow import max_pain, pcr
from services.bus import BasisBook
from services.engine.evaluate import CycleInput, CycleResult, ExpiryInfo, evaluate_cycle
from services.engine.flow import FLOW_REGISTRY, OiTracker
from services.engine.records import MetricRecord
from services.engine.registry import Flag
from services.engine.service import ENGINE_REGISTRY
from services.poller.records import ChainRecord
from tests.fakes.engine_inputs import CAL, NEAR, fut_row, kst, synthetic_series
from tests.unit.test_engine_extended import (  # pyright: ignore[reportPrivateUsage]
    NOV,
    _failing,
    cycle_input,
)
from tests.unit.test_engine_service import Rig, T

FLOW = ("pcr_oi", "pcr_volume", "max_pain")


def rows(r: CycleResult, name: str) -> dict[tuple[str, str], MetricRecord]:
    return {(m.scope, m.key): m for m in r.metrics if m.metric == name}


def _skewed(inp: CycleInput) -> CycleInput:
    """풋 OI·거래량을 콜의 1.5·2 배로(시리즈마다 같은 비율)."""
    chain: list[ChainRecord] = []
    for r in inp.chain:
        if r.cp == "P":
            vol = None if r.volume is None else r.volume * 2
            r = r.model_copy(update={"oi": (r.oi or 0) * 3 // 2, "volume": vol})
        chain.append(r)
    return CycleInput(**{**inp.__dict__, "chain": chain})


def _run(inp: CycleInput) -> CycleResult:
    return evaluate_cycle(inp, BasisBook(), cal=CAL, registry=FLOW_REGISTRY)


def test_pcr_rows_are_per_series_and_total_as_shadow() -> None:
    r = _run(_skewed(cycle_input()))
    assert r.status == "ok" and r.health == () and r.plugins_run == FLOW
    evals = {o.label: o.ev for o in r.series if o.ev is not None}
    oi, vol = rows(r, "pcr_oi"), rows(r, "pcr_volume")
    assert set(oi) == set(vol) == {("all", ""), *(("series", k) for k in evals)}
    for label, ev in evals.items():
        direct = pcr((o.quote.cp, o.quote.oi, o.quote.volume) for o in ev.options)
        assert oi[("series", label)].value == direct.oi.value == 1.5
        assert vol[("series", label)].value == direct.volume.value == 2.0
        assert oi[("series", label)].payload["put"] == direct.oi.put
    total = oi[("all", "")]
    assert total.value == 1.5 and total.quality == "ok"
    assert total.payload["expiries"] == sorted(evals)
    assert {m.flag for m in r.metrics if m.metric in FLOW} == {"shadow"}


def test_pcr_ignores_the_s_ref_quality_but_takes_the_rows_quality() -> None:
    """S_ref 가 stale 이어도(선물 행 100초 전) PCR 은 F 를 쓰지 않아 ok. 옛 행(WKI 200초 전)은
    그 시리즈와 전체가 stale, OI 없는 행은 estimated."""
    r = _run(cycle_input(futures_age_s=100))
    assert r.s_ref is not None and r.s_ref.quality == "stale"
    assert {m.quality for m in rows(r, "pcr_oi").values()} == {"ok"}
    r = _run(cycle_input(stale_wki=True))
    oi = rows(r, "pcr_oi")
    assert oi[("series", "WKI:261003")].quality == "stale"
    assert "rows_stale" in oi[("series", "WKI:261003")].payload["reasons"]
    assert oi[("series", "M:202611")].quality == "ok" and oi[("all", "")].quality == "stale"
    inp = cycle_input()
    chain = [r.model_copy(update={"oi": None}) if r.expiry == "202611" else r for r in inp.chain]
    r = _run(CycleInput(**{**inp.__dict__, "chain": chain}))
    oi = rows(r, "pcr_oi")
    assert oi[("series", "M:202611")].quality == "estimated"
    assert "oi_missing" in oi[("series", "M:202611")].payload["reasons"]


def test_a_series_not_evaluated_makes_the_total_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    _failing(monkeypatch, "261003")
    r = _run(cycle_input())
    oi = rows(r, "pcr_oi")
    assert ("series", "WKI:261003") not in oi
    total = oi[("all", "")]
    assert total.quality == "invalid" and "series_failed" in total.payload["reasons"]
    assert total.value is not None  # 값은 평가한 시리즈만의 합
    assert oi[("series", "M:202611")].quality == "ok"


def test_max_pain_uses_listed_strikes_and_flags_unquoted_ones() -> None:
    inp = cycle_input()
    listed = sorted({r.strike for r in inp.chain if r.expiry == "202611"})
    extra = [listed[0] - Decimal("2.5") * i for i in (1, 2)]  # 상장됐지만 행이 온 적 없다
    key: Any = ("", "202611")
    full = CycleInput(**{**inp.__dict__, "strikes": {key: listed}})
    r = _run(full)
    mp = rows(r, "max_pain")
    ev = next(o.ev for o in r.series if o.label == "M:202611" and o.ev is not None)
    direct = max_pain(((o.quote.strike, o.quote.cp, o.quote.oi) for o in ev.options), listed, ev.F)
    row = mp[("series", "M:202611")]
    assert direct.strike is not None and row.value == float(direct.strike)
    assert row.quality == "ok" and row.payload["listed"] is True
    assert row.payload["pain_won"] == direct.pain_won and row.payload["unquoted"] == 0
    assert set(mp) == {("series", o.label) for o in r.series if o.ev is not None}
    r = _run(CycleInput(**{**inp.__dict__, "strikes": {key: [*extra, *listed]}}))
    row = rows(r, "max_pain")[("series", "M:202611")]
    assert row.quality == "estimated" and row.payload["unquoted"] == 2
    assert "strikes_unquoted" in row.payload["reasons"]
    # 마스터가 없으면 행이 온 행사가가 후보
    bare = rows(_run(inp), "max_pain")[("series", "M:202611")]
    assert bare.payload["listed"] is False and bare.quality == "ok"


def test_the_engine_service_runs_the_flow_plugins_by_default() -> None:
    names = [p.name for p in ENGINE_REGISTRY]
    assert names[-3:] == list(FLOW) and len(set(names)) == len(names)
    assert {p.flag for p in FLOW_REGISTRY} == {"pcr", "max_pain"}


def test_pcr_of_a_single_series_cycle_equals_its_total() -> None:
    """월물 하나뿐인 사이클 — 시리즈 한 줄과 전체가 같다(합성 체인은 콜·풋 OI 가 같다)."""
    base = cycle_input()
    chain = synthetic_series("", "202611", NOV.last_trade_date, base.as_of, sigma=0.22)
    one: Any = {("", "202611"): NOV}
    inp = CycleInput(**{**base.__dict__, "chain": chain, "expiries": one})
    oi = rows(_run(inp), "pcr_oi")
    assert oi[("all", "")].value == oi[("series", "M:202611")].value == 1.0


# ── OI 증감 (§6.7) ──

TUE = date(2026, 10, 13)
K1, K2 = Decimal("1095"), Decimal("1097.5")


def oi_cycle(
    at: datetime, ois: dict[tuple[Decimal, str], int | None] | None = None, **kw: Any
) -> CycleInput:
    """월물 202611 행사가 둘(콜·풋) — OI 500, ois 로 종목마다 바꾼다."""
    rows = synthetic_series("", "202611", NOV.last_trade_date, at, strikes=[K1, K2], **kw)
    over = ois or {}
    rows = [
        r.model_copy(update={"oi": over[(r.strike, r.cp)]}) if (r.strike, r.cp) in over else r
        for r in rows
    ]
    return CycleInput(
        as_of=at,
        trade_date=rows[0].trade_date,
        session=rows[0].session,
        chain=rows,
        futures=[fut_row(at - timedelta(seconds=5))],
        expiries={("", "202611"): NOV},
        near_code=NEAR,
    )


def track(tr: OiTracker, inp: CycleInput) -> tuple[list[Any], list[Any]]:
    return tr.cycle(inp, evaluate_cycle(inp, BasisBook(), cal=CAL))


def test_the_first_snapshot_has_no_change_and_unchanged_snapshots_are_not_written() -> None:
    tr = OiTracker()
    t0 = kst(TUE, 10, 0)
    rows, health = track(tr, oi_cycle(t0))
    assert health == [] and len(rows) == 4
    assert {(r.ts, r.trade_date, r.session, r.change, r.prev_ts) for r in rows} == {
        (t0, TUE, "day", None, None)
    }
    assert {(r.mrkt_cls, r.expiry, r.oi, r.outlier, r.quality) for r in rows} == {
        ("", "202611", 500, False, "ok")
    }
    rows, _ = track(tr, oi_cycle(t0))  # 같은 행 — 새 스냅샷이 아니다
    assert rows == []
    t1 = t0 + timedelta(seconds=30)
    rows, _ = track(tr, oi_cycle(t1, {(K1, "C"): 530}))  # 새 행 셋은 0 증감 — 쓰지 않는다
    (r,) = rows
    assert (r.strike, r.cp, r.oi, r.prev_oi, r.change, r.prev_ts, r.ts) == (
        K1,
        "C",
        530,
        500,
        30,
        t0,
        t1,
    )


def test_a_dip_recovered_by_ninety_percent_rewrites_both_cells_as_outliers() -> None:
    tr = OiTracker()
    t0 = kst(TUE, 10, 0)
    track(tr, oi_cycle(t0))
    t1, t2 = t0 + timedelta(seconds=30), t0 + timedelta(seconds=60)
    (dip,), _ = track(tr, oi_cycle(t1, {(K2, "P"): 300}))
    assert (dip.change, dip.outlier) == (-200, False)
    rows, health = track(tr, oi_cycle(t2, {(K2, "P"): 480}))
    assert [(r.ts, r.change, r.outlier) for r in rows] == [(t1, -200, True), (t2, 180, True)]
    assert rows[0].prev_ts == t0 and rows[1].prev_ts == t1
    (h,) = health
    assert h.kind == "engine_oi_outlier" and h.subject == "M:202611"
    assert "1097.5 P: OI 500→300→480" in h.detail
    # 89% 복구는 이상치가 아니다
    tr2 = OiTracker()
    track(tr2, oi_cycle(t0))
    track(tr2, oi_cycle(t1, {(K2, "P"): 300}))
    (rec,), health = track(tr2, oi_cycle(t2, {(K2, "P"): 478}))
    assert (rec.change, rec.outlier, health) == (178, False, [])


def _row_quality(inp: CycleInput, quality: Any) -> CycleInput:
    """1095 콜 체인 행만 품질을 바꾼다."""
    chain = [
        r.model_copy(update={"quality": quality}) if (r.strike, r.cp) == (K1, "C") else r
        for r in inp.chain
    ]
    return CycleInput(**{**inp.__dict__, "chain": chain})


def test_a_cell_quality_takes_only_its_two_snapshots() -> None:
    """칸의 품질 = 이번·직전 스냅샷 체인 행 품질 중 나쁜 것 — 세션 앞쪽 한 스냅샷의 품질이 뒤 칸에
    번지지 않는다(증감은 두 스냅샷에만 기댄다, 검토 F3). 이상치로 고친 앞 칸은 그 칸의 품질
    그대로."""
    tr = OiTracker()
    t = [kst(TUE, 10, 0) + timedelta(seconds=30 * i) for i in range(6)]
    steps: list[tuple[int, str]] = [
        (500, "stale"),
        (520, "ok"),
        (540, "ok"),
        (300, "estimated"),  # 줄었다
        (530, "ok"),  # 90% 넘게 복구 — 두 칸 이상치
        (550, "ok"),
    ]
    got: list[list[tuple[datetime, int | None, bool, str]]] = []
    for at, (oi, q) in zip(t, steps, strict=True):
        rows, _ = track(tr, _row_quality(oi_cycle(at, {(K1, "C"): oi}), q))
        mine = [r for r in rows if (r.strike, r.cp) == (K1, "C")]
        got.append([(r.ts, r.change, r.outlier, r.quality) for r in mine])
    assert got == [
        [(t[0], None, False, "stale")],
        [(t[1], 20, False, "stale")],
        [(t[2], 20, False, "ok")],
        [(t[3], -240, False, "estimated")],
        [(t[3], -240, True, "estimated"), (t[4], 230, True, "estimated")],
        [(t[5], 20, False, "ok")],
    ]


def test_rows_without_oi_are_skipped_and_the_session_change_starts_over() -> None:
    tr = OiTracker()
    t0 = kst(TUE, 10, 0)
    rows, _ = track(tr, oi_cycle(t0, {(K1, "C"): None}))
    assert len(rows) == 3 and (K1, "C") not in {(r.strike, r.cp) for r in rows}
    night = kst(TUE, 19, 0)
    rows, _ = track(tr, oi_cycle(night, {(K1, "C"): 450}, source="fill"))
    assert len(rows) == 4 and {(r.session, r.change) for r in rows} == {("night", None)}
    assert tr.tag == (TUE + timedelta(days=1), "night")


def test_expired_series_are_not_tracked() -> None:
    """15:20 만기 시리즈(최신 행이 만기 뒤)는 보지 않는다."""
    tr = OiTracker()
    at = kst(TUE, 15, 25)
    zero = ExpiryInfo(TUE, "kis")
    rows_0dte = synthetic_series("WKM", "261002", TUE, at, strikes=[K1, K2])
    base = oi_cycle(at)
    inp = CycleInput(
        **{
            **base.__dict__,
            "chain": [*base.chain, *rows_0dte],
            "expiries": {**base.expiries, ("WKM", "261002"): zero},
        }
    )
    rows, _ = track(tr, inp)
    assert {r.expiry for r in rows} == {"202611"}


def test_the_service_writes_oi_changes_every_cycle() -> None:
    rig = Rig()
    rig.svc.on_ready(rig.add_cycle(T, oi=500))
    first = dict(rig.store.oi_changes)
    assert first and {(r.ts, r.change) for r in first.values()} == {(T, None)}
    t1 = T + timedelta(seconds=30)
    rig.svc.on_ready(rig.add_cycle(t1, oi=520))
    later = [r for r in rig.store.oi_changes.values() if r.ts == t1]
    assert len(later) == len(first) and {r.change for r in later} == {20}
    assert rig.svc.stats.oi_rows == 2 * len(first)


def test_a_restart_mid_session_keeps_the_stored_cells() -> None:
    """재기동한 engine 의 새 OiTracker 는 같은 최신 체인 행을 첫 스냅샷(증감 없음)으로 본다 — 같은
    키의 저장된 칸(증감·이상치 격리)을 덮지 않는다(검토 F1). 다음 스냅샷부터는 그 행과의 증감."""
    rig = Rig()
    rig.svc.on_ready(rig.add_cycle(T, oi=500))
    t1, t2 = T + timedelta(seconds=30), T + timedelta(seconds=60)
    rig.svc.on_ready(rig.add_cycle(t1, oi=300))
    rig.svc.on_ready(rig.add_cycle(t2, oi=480))  # 90% 넘게 복구 — 두 칸 이상치
    before = dict(rig.store.oi_changes)
    assert {(r.ts, r.change, r.outlier) for r in before.values() if r.ts != T} == {
        (t1, -200, True),
        (t2, 180, True),
    }
    again = Rig(server=rig.server)  # 같은 Redis(engine:latest)·같은 저장소로 다시 기동
    again.store = rig.store
    again.svc.reader = again.svc.sink = rig.store
    later = t2 + timedelta(seconds=10)  # 새 체인 행이 아직 없다 — 최신 행은 t2 그대로
    assert again.svc.run_cycle(TUE, "day", later) is not None
    assert again.svc.stats.oi_rows == len([r for r in before.values() if r.ts == t2])
    assert rig.store.oi_changes == before
    t3 = t2 + timedelta(seconds=30)
    rig.store.chain += synthetic_series("", "202611", NOV.last_trade_date, t3, oi=490)
    rig.store.futures.append(fut_row(t3 - timedelta(seconds=5)))
    assert again.svc.run_cycle(TUE, "day", t3) is not None
    cells = [r for r in rig.store.oi_changes.values() if r.ts == t3]
    assert cells and {(r.prev_ts, r.change, r.outlier) for r in cells} == {(t2, 10, False)}


def test_oi_changes_follow_the_flag_and_failures_stay_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = Rig()
    rig.svc.on_ready(rig.add_cycle(T))
    assert {r.flag for r in rig.store.oi_changes.values()} == {"shadow"}  # 새 지표 기본
    rig = Rig()
    visible: dict[str, Flag] = {"oi_changes": "visible"}
    rig.svc.flags = visible
    rig.svc.on_ready(rig.add_cycle(T))
    assert {r.flag for r in rig.store.oi_changes.values()} == {"visible"}  # 계산 당시 플래그
    rig = Rig()
    off: dict[str, Flag] = {"oi_changes": "off"}
    rig.svc.flags = off
    assert rig.svc.on_ready(rig.add_cycle(T)) is not None
    assert rig.store.oi_changes == {} and rig.store.levels
    rig = Rig()

    def boom(*_a: Any) -> Any:
        raise ZeroDivisionError("boom")

    monkeypatch.setattr(rig.svc.oi, "cycle", boom)
    result = rig.svc.on_ready(rig.add_cycle(T))
    assert result is not None and result.status == "ok" and rig.store.levels
    kinds = [e.kind for e in rig.health.events]
    assert "engine_metric_failed" in kinds and rig.store.oi_changes == {}

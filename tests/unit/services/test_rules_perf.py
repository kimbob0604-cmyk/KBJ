"""`rules.outcome`·`rules.backtest_nightly` 처리기·입력 고르기(P8 묶음 BKT — 합성 세계 p8_world)."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.core.rules_rows import RuleRow, RuleVersion, SignalRow
from kbj.core.time import KST
from kbj.engines.rules.live import close_eval_ts, signal_id
from kbj.services.engine import rules as live_svc
from kbj.services.engine.rules_perf import (
    INTRADAY_REPLAY_DAYS,
    code_data,
    compute_outcomes,
    frame_inputs,
    live_flow_rows,
    nightly,
    nightly_rule,
    outcome,
    run_id_for,
)
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos import MemoryRepos
from tests.fixtures.synthetic.p8_world import P8World
from tests.unit.services.rules_perf_seed import RATIO, rule_id_of, seeded

World = tuple[MemoryRepos, P8World, dict[str, Any], Any, Any]


@pytest.fixture(scope="module")
def world() -> World:
    return seeded(("E1", "E8"), live_days=60)


def _ctx(w: P8World, job: str, repos: MemoryRepos, cfg: Any, cal: Any) -> JobContext:
    day = w.days[-1]
    return JobContext(
        job=job, as_of=day.isoformat(), run_id=f"t:{job}", attempt=1,
        now=datetime.combine(day, time(8, 45), tzinfo=KST),
        resources={"repos": repos, "rules_config": cfg, "kr": cal, "split_ratio": RATIO},
    )  # fmt: skip


# ── 입력 고르기 = 실시간 load_frames 와 같은 프레임 ─────────────────────────────────────────────


def test_frame_inputs_match_live_loader(world: World) -> None:
    repos, w, _, cfg, cal = world
    codes = ["Q80010", "Q80013", "Q80003"]
    day = w.days[-1]
    mine = frame_inputs(repos, codes, upto=day, cfg=cfg, cal=cal, split_ratio=RATIO)
    theirs = live_svc.load_frames(repos, codes, upto=day, cfg=cfg, cal=cal, split_ratio=RATIO)
    for c in codes:
        a, b = mine[c].close_frame(day), theirs[c]
        assert a.dates == b.dates and a.close == b.close and a.volume == b.volume
        assert a.halted == b.halted and a.split_idx == b.split_idx
        assert [a.input_fp(i) for i in range(0, a.n, 37)] == [
            b.input_fp(i) for i in range(0, b.n, 37)
        ]


def test_intraday_inputs_match_live_loader(world: World) -> None:
    repos, w, _, cfg, cal = world
    day = w.days[-2]
    code = "Q80016"
    s = w.slots_for(code, day)[30]
    mine = frame_inputs(repos, [code], upto=w.days[-1], cfg=cfg, cal=cal, split_ratio=RATIO)
    lf = live_flow_rows(repos.flows, s.slot_ts)
    assert lf == live_svc.live_flows(repos.flows, s.slot_ts)  # 가집계 → 오늘 원장 행 같은 규칙
    a = mine[code].intraday_frame(s, lf.get(code))
    b = live_svc.load_frames(
        repos, [code], upto=w.days[-3], cfg=cfg, cal=cal, split_ratio=RATIO,
        partial={code: s}, partial_flows=lf,
    )[code]  # fmt: skip
    assert a is not None
    assert a.dates == b.dates and a.close == b.close and a.live_flow and b.live_flow
    assert a.input_fp(a.last) == b.input_fp(b.last)


def test_code_data_has_slots_index_and_listing(world: World) -> None:
    repos, w, _, cfg, cal = world
    got = code_data(repos, ["Q80015", "Q80014", "Q80021"], upto=w.days[-1], cfg=cfg, cal=cal,
                    split_ratio=RATIO, slots_from=w.days[-5])  # fmt: skip
    # 가집계는 첫 회차(외국인 09:30)가 확실히 들어온 09:40 슬롯부터 — 09:00~09:30 네 슬롯은 판단
    # 불가(kbj.engines.flows.prelim, ADR 0031 §6)
    assert len(got["Q80015"].slots) == 5 * 40 and len(got["Q80015"].live_flows) == 5 * 36
    assert got["Q80014"].live_flows == {}  # 가집계 없음(판단 불가 종목)
    assert got["Q80021"].slots == ()  # 관심종목 밖 — 슬롯 없음
    assert got["Q80015"].index_bars and got["Q80015"].listed


# ── 알림 후 성과 ────────────────────────────────────────────────────────────────────────────────


def test_outcome_rows_hand_check(world: World) -> None:
    repos, w, refs, cfg, cal = world
    day = w.days[-1]
    rows = compute_outcomes(repos, day=day, now=w.clock, cfg=cfg, cal=cal, split_ratio=RATIO)
    assert rows and all(r.source == "KBJ:rules.outcome" for r in rows)
    sig = next(
        s for s in repos.rules.signals(w.days[-45], day, rule_id=refs["E1"].rule_id)
        if s.trade_date == w.days[-30]
    )  # fmt: skip
    bars = {b.date: b for b in w.bars[sig.code]}
    got = {r.horizon: r for r in rows if r.signal_id == sig.signal_id}
    assert sorted(got) == [1, 3, 5, 10, 20]
    for h, r in got.items():
        due = w.days[-30 + h]
        assert r.due_date == due
        assert r.ret == pytest.approx(bars[due].close / sig.entry_price - 1)  # type: ignore[operator]
        assert r.excess is not None and r.index_code in ("코스피", "코스닥")
    near = [r for r in rows if r.signal_id != sig.signal_id and r.due_date > day]
    assert near == []  # 미도래 칸은 만들지 않는다


def test_outcome_handler_is_idempotent(world: World) -> None:
    repos, w, _, cfg, cal = world
    r1 = outcome(_ctx(w, "rules.outcome", repos, cfg, cal))
    r2 = outcome(_ctx(w, "rules.outcome", repos, cfg, cal))
    assert r1.status == "ok" and r1.rows and r1.rows == r2.rows
    ids = {s.signal_id for s in repos.rules.signals(w.days[0], w.days[-1])}
    keys = [(o.signal_id, o.horizon) for o in repos.rules.outcomes(ids)]
    assert len(keys) == len(set(keys)) == r1.rows


def test_outcome_skips_blocked_and_entryless_signals(world: World) -> None:
    repos, w, refs, cfg, cal = world
    rows = compute_outcomes(repos, day=w.days[-1], now=w.clock, cfg=cfg, cal=cal,
                            split_ratio=RATIO)  # fmt: skip
    blocked = {s.signal_id for s in repos.rules.signals(w.days[-10], w.days[-1],
                                                         rule_id=refs["E8"].rule_id)
               if s.status == "cooldown"}  # fmt: skip
    assert blocked and not blocked & {r.signal_id for r in rows}


# ── 야간 백테스트·일치 감시 ─────────────────────────────────────────────────────────────────────


def test_nightly_writes_run_signals_and_clean_match(world: World) -> None:
    repos, w, refs, cfg, cal = world
    res = nightly(_ctx(w, "rules.backtest_nightly", repos, cfg, cal))
    assert res.status == "ok", res.detail
    assert res.detail["runs"] == 2 and res.detail["logic"] == {}
    for key in ("E1", "E8"):
        rid = refs[key].rule_id
        (run,) = repos.rules.backtest_runs(rule_id=rid)
        assert run.run_id.startswith("bt_") and run.version == 1
        assert run.summary["signals"] == len(repos.rules.backtest_signals(run.run_id))
        assert run.params["costs"]["sell_tax_pct"] == pytest.approx(0.20)
        ms = repos.rules.match_checks(w.days[-20], w.days[-1], rule_id=rid)
        assert len(ms) == cfg.match.window_days
        assert all(m.diffs == () for m in ms)  # 같은 원장 → 같은 신호(D-P8-6)
        assert sum(m.live_n for m in ms) == sum(m.backtest_n for m in ms) > 0
    e8 = repos.rules.backtest_runs(rule_id=refs["E8"].rule_id)[0]
    assert e8.mode == "intraday_replay"
    assert e8.params["intraday_from"] == cal_back(w, INTRADAY_REPLAY_DAYS)


def cal_back(w: P8World, n: int) -> str:
    from kbj.core.calendar import TradingCalendar

    cal = TradingCalendar.default()
    d = w.days[-1]
    for _ in range(n - 1):
        d = cal.prev_trading_day(d)
    return d.isoformat()


def _sig(ref: Any, code: str, d: Any, fp: str) -> SignalRow:
    ts = close_eval_ts(d)
    return SignalRow(signal_id("close", ts, ref.rule_id, ref.version, code), "close", ref.rule_id,
                     ref.version, ref.dsl_hash, code, ts, d, 100.0, {}, fp, "sent", None, None,
                     Quality.OK, "KBJ:rules.close")  # fmt: skip


def test_match_reasons() -> None:
    repos, w, refs, cfg, cal = seeded(("E1",), live_days=8)
    ref = refs["E1"]
    day = w.days[-1]
    fi = frame_inputs(repos, ["Q80020"], upto=day, cfg=cfg, cal=cal, split_ratio=RATIO)
    fr = fi["Q80020"].close_frame(day)
    d1, d2 = w.days[-3], w.days[-4]
    assert not any(s.code == "Q80020" and s.trade_date in (d1, d2)
                   for s in repos.rules.signals(d2, d1))  # fmt: skip
    i1 = fr.index_of(d1)
    assert i1 is not None
    repos.rules.put_signals_close([_sig(ref, "Q80020", d1, fr.input_fp(i1)),
                                   _sig(ref, "Q80020", d2, "0" * 16)])  # fmt: skip
    out = nightly_rule(repos, ref, day=day, now=w.clock, cfg=cfg, cal=cal, split_ratio=RATIO)
    diffs = {(d["code"], d["eval_ts"][:10], d["side"], d["reason"])
             for m in out.matches for d in m.diffs}  # fmt: skip
    # 실시간에만 — 지문 같음 → logic(버그), 지문 다름 → data_revised
    assert ("Q80020", d1.isoformat(), "live_only", "logic") in diffs
    assert ("Q80020", d2.isoformat(), "live_only", "data_revised") in diffs
    # 실시간 신호가 없던 앞 날(live_days=8 밖) — 백테스트에만, 그날 봉이 KRX 확정 → data_revised
    old = [d for d in diffs if d[2] == "backtest_only"]
    assert old and all(d[3] == "data_revised" for d in old)
    assert out.logic_n == 1


def test_nightly_isolates_bad_rule(world: World) -> None:
    repos, w, _, cfg, cal = world
    bad = rule_id_of("bad")
    head = RuleRow(bad, "깨진 규칙", 1, True, None, "api", w.clock, w.clock, "test")
    repos.personal.put_rule_personal(head, RuleVersion(bad, 1, {"v": 1, "name": "x"}, "0" * 64,
                                                       w.clock))  # fmt: skip
    res = nightly(_ctx(w, "rules.backtest_nightly", repos, cfg, cal))
    assert res.status == "failed"
    assert res.detail["errors"][bad].startswith("dsl_invalid")
    assert res.detail["runs"] == 2  # 다른 규칙은 그대로 돈다


def test_run_id_is_deterministic() -> None:
    now = datetime(2026, 10, 30, 8, 45, tzinfo=KST)
    a = run_id_for("r_0000000000e1", 1, now.date(), now)
    assert a == run_id_for("r_0000000000e1", 1, now.date(), now)
    assert a != run_id_for("r_0000000000e1", 2, now.date(), now)
    assert len(a) == 29 and a.startswith("bt_")
    with pytest.raises(ValueError):
        run_id_for("r_0000000000e1", 1, now.date(), now.replace(tzinfo=None))

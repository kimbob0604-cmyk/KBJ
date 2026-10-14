"""`tests/fixtures/synthetic/p8_world.py` — 결정성·합성 표시·기대값(p8_expect.yaml) 대조·심은 칸.

P8 묶음(EVL·BKT·JNL·WRAP)이 같은 세계를 쓴다. 실데이터가 아니다(`_source: SYNTHETIC`). 심은 칸은
실제
평가기(`kbj.engines.rules`)로 확인한다 — 실시간·백테스트가 같은 프레임·평가기를 부르므로 둘 다에
쓰인다.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

from kbj.engines.rules.evaluate import evaluate
from kbj.engines.rules.frame import Frame, build_frame
from kbj.store.repos import memory_repos
from tests.fixtures.synthetic.p8_world import DAYS, PLANTED, SYN, WATCH, P8World, build_world

ROOT = Path(__file__).resolve().parents[3]
EXPECT: dict[str, Any] = yaml.safe_load(
    (ROOT / "tests" / "fixtures" / "synthetic" / "p8_expect.yaml").read_text(encoding="utf-8")
)


def _close_frame(w: P8World, code: str, i: int) -> Frame:
    return build_frame(
        code,
        w.bars[code],
        upto=w.days[i],
        anchor=w.days[0],
        split_ratio=0.31,
        flows=w.flows[code],
        calendar=w.days,
    )


def _intra_frame(w: P8World, code: str, i: int, k: int) -> Frame:
    slot = w.slots_for(code, w.days[i])[k]
    return build_frame(
        code,
        w.bars[code],
        upto=w.days[i - 1],
        anchor=w.days[0],
        split_ratio=0.31,
        flows=w.flows[code],
        calendar=w.days,
        partial=slot,
        partial_flow=w.live_flow(code, slot.slot_ts),
    )


def test_deterministic_and_marked_synthetic() -> None:
    a = build_world()
    assert a.digest() == build_world.__wrapped__().digest()  # 캐시를 건너뛴 새 객체
    assert EXPECT["_source"].startswith("SYNTHETIC") and SYN.startswith("SYNTHETIC")
    assert all(r["_source"] == SYN for r in a.records())
    assert a.seed == EXPECT["seed"] and a.clock.isoformat() == EXPECT["clock"]


def test_counts_and_special_codes() -> None:
    w = build_world()
    c = EXPECT["counts"]
    assert len(w.days) == EXPECT["days"]["n"] == DAYS
    assert (w.days[0].isoformat(), w.days[-1].isoformat()) == (
        EXPECT["days"]["first"],
        EXPECT["days"]["last"],
    )
    assert len(w.bars) == c["codes"] and len(WATCH) == c["watch"]
    assert sum(len(b) for b in w.bars.values()) == c["bars"]
    assert sum(len(f) for f in w.flows.values()) == c["flows"]
    assert sum(len(s) for s in w.slots.values()) == c["slots"]
    assert len(w.live) == c["live"]
    assert len(w.trades) == c["trades"] and len(w.analyses) == c["analyses"]
    assert sum(len(b) for b in w.index_bars.values()) == c["index_bars"]
    sp = EXPECT["special"]
    split = _close_frame(w, sp["split"]["code"], DAYS - 1)
    assert split.index_of(w.days[sp["split"]["day"]]) in split.split_idx
    halted = {b.date for b in w.bars[sp["halted"]["code"]]}
    assert all(w.days[i] not in halted for i in range(sp["halted"]["from"], sp["halted"]["to"] + 1))
    assert w.bars[sp["new_listing"]["code"]][0].date == w.days[sp["new_listing"]["day"]]
    assert next(u for u in w.universe if u.code == sp["pref"]).kind == "pref"
    nl = sp["no_live_flow"]
    assert all(w.live_flow(nl, s.slot_ts) is None for s in w.slots_for(nl, w.days[-1]))


def test_intraday_slots_build_the_daily_bar() -> None:
    """장중 재생과 마감 평가가 같은 입력 — 그날 일봉 = 슬롯에서 만든 값."""
    w = build_world()
    for code in WATCH[:3]:
        for d in w.intraday_days:
            ss = w.slots_for(code, d)
            bar = next(b for b in w.bars[code] if b.date == d)
            assert len(ss) == 40 and bar.close == ss[-1].price and bar.volume == ss[-1].volume
            assert bar.open == ss[0].open
            assert all((s.volume or 0) <= (t.volume or 0) for s, t in pairwise(ss))


def test_planted_cells_are_true_with_the_real_evaluator() -> None:
    w = build_world()
    assert {(p.rule, p.code, p.day, p.slot) for p in PLANTED} == {
        (x["rule"], x["code"], x["day"], x.get("slot")) for x in EXPECT["planted"]
    }
    for p in PLANTED:
        rule = w.examples[p.rule]
        if p.slot is None:
            f = _close_frame(w, p.code, p.day)
            i = f.index_of(w.days[p.day])
            assert i is not None
            v = evaluate(rule, f, i)
        else:
            f = _intra_frame(w, p.code, p.day, p.slot)
            v = evaluate(rule, f, f.last)
        assert v.ok, (p, v.state, [(c.value, c.ref_value, c.reason) for c in v.conds])


def test_engine_snapshot_counts() -> None:
    w = build_world()
    got: dict[str, int] = {}
    for rid, rule in w.examples.items():
        if rule.mode != "close":
            continue
        n = 0
        for code in WATCH:
            f = _close_frame(w, code, DAYS - 1)
            for i in range(DAYS - 60, DAYS):
                j = f.index_of(w.days[i])
                n += j is not None and evaluate(rule, f, j).ok
        got[rid] = n
    assert got == EXPECT["signals_close_last60"]
    assert all(v > 0 for v in got.values())  # 예시마다 신호가 0 이 아니다(§4.1.4)


def test_fill_memory_repos() -> None:
    w = build_world()
    repos = memory_repos()
    w.fill(repos)
    assert repos.journal.watch_codes("main") == set(WATCH)
    assert len(repos.journal.trades()) == 30 and len(repos.journal.analyses()) == 5
    d = w.days[-1]
    from datetime import datetime, time

    from kbj.core.time import KST

    day0 = datetime.combine(d, time(0), tzinfo=KST)
    assert len(repos.rules.quote_slots(None, day0, day0.replace(hour=23))) == 20 * 40
    series = repos.market.series(["Q80010"], d, 5)
    assert [b.close for b in series["Q80010"]] == [b.close for b in w.bars["Q80010"][-5:]]

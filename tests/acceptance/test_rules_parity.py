"""P8 완료 시험 — 규칙 예시 10개가 백테스트와 실시간 평가에서 같은 신호를 낸다
(docs/p8_design.md §0.3 ①~⑤ — 웨이브 3 WRAP).

합성 세계 `p8_world`(고정 시드, 80종목 × 320 거래일 — 분할·거래정지·신규상장·우선주
포함, 실데이터 아님)에 `config/rule_examples.yaml` 10개(마감 7·장중 3)를 규칙 쓰기
모듈로 넣고:

① 마감 7개: 마지막 60 거래일을 날마다 실제 처리기 `rules.close`(16:00 — 가짜 시계·
   메모리 저장소·가짜 notify)로 돌린 신호 = 같은 원장으로 돌린
   `run_backtest(mode='close')` 신호 — (규칙, 종목, 날짜, 허락/쿨다운) 집합이 완전히
   같고, 진입가(그날 종가)는 허용오차 1e-9, 입력 지문은 같다.
② 장중 3개: 마지막 5 거래일 × 40 슬롯을 실제 처리기 `rules.intraday`(가짜 KIS 슬롯
   시세 + 그 회차 가집계)로 돌린 신호 = 처리기가 저장한 `watch_quote_slot` 과 가집계를
   재생한 `run_backtest(mode='intraday_replay')`.
③ 쿨다운으로 막힌 시점도 양쪽이 같다(①② 의 집합에 상태가 들어 있다 — 상태기 공유).
④ 변형: 실시간 신호가 난 날의 종가를 다음 날 KRX 확정이 바꾸면 야간 일치 감시가 그
   차이를 `data_revised` 로 분류하고 `logic` 0.
⑤ 분할 종목(Q80002 — 칸 200 분할, 창 260)은 양쪽 모두 허락 신호 0.

평가·백테스트 모두 그 시점까지의 원장만 본다(룩어헤드 없음 — 속성 시험은
tests/property). 실주문 없음.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from functools import cache
from typing import Final

import pytest

from kbj.config.rules_cfg import load_rules_cfg
from kbj.core.rules_rows import SignalRow
from kbj.core.time import KST
from kbj.engines.rules.backtest import BtSignal, run_backtest
from kbj.engines.rules.dsl import dsl_hash
from kbj.services.engine import rules as R
from kbj.services.engine.rules_perf import RuleRef, code_data, nightly_rule, rule_codes
from kbj.store.repos.memory import MemoryRepos
from tests.fixtures.synthetic.p8_world import (  # pyright: ignore[reportPrivateUsage]
    _slot_ts,
    build_world,
)
from tests.unit.collectors.p3_fakes import Clock
from tests.unit.services.rules_world import (
    KR,
    SPLIT_RATIO,
    FakeNotify,
    add_rules,
    job_ctx,
    kis_for,
    put_live_flows,
    repos_with_world,
    slot_as_of,
)

W = build_world()
CFG = load_rules_cfg()
CLOSE_KEYS: Final = tuple(k for k, r in W.examples.items() if r.mode == "close")
INTRADAY_KEYS: Final = tuple(k for k, r in W.examples.items() if r.mode == "intraday")
CLOSE_DAYS: Final = 60
INTRADAY_DAYS: Final = 5
SLOTS: Final = 40
ALLOWED: Final = frozenset({"sent", "suppressed", "dry"})
SPLIT_CODE: Final = "Q80002"

Key = tuple[str, str, date, datetime, str]  # (규칙, 종목, 날짜, 평가 시각, 허락|cooldown)


def _kind(status: str) -> str | None:
    if status in ALLOWED:
        return "allowed"
    return "cooldown" if status == "cooldown" else None


def _live_keys(rows: Iterable[SignalRow]) -> dict[Key, SignalRow]:
    out: dict[Key, SignalRow] = {}
    for r in rows:
        k = _kind(r.status)
        if k is not None:
            out[(r.rule_id, r.code, r.trade_date, r.eval_ts, k)] = r
    return out


def _bt_keys(rule_id: str, sigs: Iterable[BtSignal]) -> dict[Key, BtSignal]:
    return {(rule_id, s.code, s.trade_date, s.eval_ts, _kind(s.status) or "?"): s for s in sigs}


def _refs(repos: MemoryRepos, ids: dict[str, str]) -> dict[str, RuleRef]:
    out: dict[str, RuleRef] = {}
    for key, rid in ids.items():
        rule = W.examples[key]
        out[key] = RuleRef(rid, rule.name, 1, dsl_hash(rule), rule)
    for ref in out.values():
        head = repos.rules.rule(ref.rule_id)
        assert head is not None and head.current_version == 1
    return out


def _backtest(
    repos: MemoryRepos, ref: RuleRef, *, start: date, end: date, intraday: bool
) -> dict[Key, BtSignal]:
    codes, hold = rule_codes(repos, ref.rule, day=end)
    data = code_data(
        repos, codes, upto=end, cfg=CFG, cal=KR, split_ratio=SPLIT_RATIO,
        slots_from=start if intraday else None, hold_since=hold,
    )  # fmt: skip
    cool = ref.rule.alert.cooldown_min or CFG.cooldown_min.default
    out = run_backtest(
        ref.rule, data, rule_id=ref.rule_id, start=start, end=end,
        modes=("intraday_replay",) if intraday else ("close",), cooldown_min=cool,
    )  # fmt: skip
    return _bt_keys(ref.rule_id, out.signals)


@cache
def _close_world() -> tuple[MemoryRepos, dict[str, RuleRef], list[date], int]:
    repos = repos_with_world(W)
    ids = dict(zip(CLOSE_KEYS, add_rules(repos, {k: W.examples[k] for k in CLOSE_KEYS}),
                   strict=True))  # fmt: skip
    days = list(W.days[-CLOSE_DAYS:])
    sent = 0
    for d in days:
        n = FakeNotify()
        res = R.close(
            job_ctx("rules.close", d.isoformat(), now=datetime.combine(d, time(16, 0), tzinfo=KST),
                    repos=repos, notify=n)
        )  # fmt: skip
        assert res.status == "ok", (d, res.detail)
        assert len(n.sent) <= 1  # 16:00 한 통 묶음(0건이면 없음 — ADR 0018)
        sent += len(n.sent)
    return repos, _refs(repos, ids), days, sent


@cache
def _intraday_world() -> tuple[MemoryRepos, dict[str, RuleRef], list[date]]:
    repos = repos_with_world(W)
    ids = dict(zip(INTRADAY_KEYS, add_rules(repos, {k: W.examples[k] for k in INTRADAY_KEYS}),
                   strict=True))  # fmt: skip
    days = list(W.days[-INTRADAY_DAYS:])
    for d in days:
        for k in range(SLOTS):
            ts = _slot_ts(d, k)
            clock = Clock(ts + timedelta(seconds=40))
            put_live_flows(
                repos, W, ts
            )  # 같은 회차 flows.intraday(가집계 쓰기 주인)가 먼저 끝난 것처럼
            _, kis = kis_for(W, clock)
            res = R.intraday(
                job_ctx("rules.intraday", slot_as_of(ts), now=clock(), repos=repos, kis=kis,
                        notify=FakeNotify())
            )  # fmt: skip
            assert res.status in ("ok", "skipped"), (ts, res.status, res.detail)
    return repos, _refs(repos, ids), days


# ── ① 마감 7개 + ③ 쿨다운 ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", CLOSE_KEYS)
def test_close_rules_live_equals_backtest(key: str) -> None:
    repos, refs, days, _ = _close_world()
    ref = refs[key]
    live = _live_keys(
        r for r in repos.rules.signals(days[0], days[-1], mode="close") if r.rule_id == ref.rule_id
    )
    bt = _backtest(repos, ref, start=days[0], end=days[-1], intraday=False)
    assert set(live) == set(bt), key
    for k, row in live.items():
        b = bt[k]
        assert row.input_fp == b.input_fp, k
        assert row.entry_price is not None and b.entry_price is not None
        assert row.entry_price == pytest.approx(b.entry_price, abs=1e-9), k


def test_close_parity_is_not_vacuous() -> None:
    """심은 칸(E1 Q80010 칸 300)에서 허락 신호가 나고 16:00 묶음이 나간다(① 이 빈말이 아님)."""
    repos, refs, days, sent = _close_world()
    rows = [r for r in repos.rules.signals(days[0], days[-1], mode="close")
            if r.rule_id in {x.rule_id for x in refs.values()}]  # fmt: skip
    kinds = {_kind(r.status) for r in rows}
    assert "allowed" in kinds
    assert sent >= 1
    e1 = refs["E1"].rule_id
    assert any(r.rule_id == e1 and r.code == "Q80010" and r.trade_date == W.days[300]
               and r.status in ALLOWED for r in rows)  # fmt: skip


# ── ② 장중 3개 + ③ ────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", INTRADAY_KEYS)
def test_intraday_rules_live_equals_replay(key: str) -> None:
    repos, refs, days = _intraday_world()
    ref = refs[key]
    live = _live_keys(
        r for r in repos.rules.signals(days[0], days[-1], mode="intraday")
        if r.rule_id == ref.rule_id
    )  # fmt: skip
    bt = _backtest(repos, ref, start=days[0], end=days[-1], intraday=True)
    assert set(live) == set(bt), key
    for k, row in live.items():
        assert row.input_fp == bt[k].input_fp, k


def test_intraday_parity_is_not_vacuous() -> None:
    """E8·E9 Q80015 칸 317 슬롯 20(심은 +7%·거래량 3배)에서 실시간 신호가 난다."""
    repos, refs, days = _intraday_world()
    rows = repos.rules.signals(days[0], days[-1], mode="intraday")
    hits = {(r.rule_id, r.code, r.trade_date) for r in rows if r.status in ALLOWED}
    assert (refs["E8"].rule_id, "Q80015", W.days[317]) in hits
    # 가집계 없는 종목(Q80014)은 수급 조건이 '판단 불가' — 허락 신호 0(사용자 결정 2026-10-13)
    assert not any(code == "Q80014" for _r, code, _d in hits if _r == refs["E10"].rule_id)


# ── ⑤ 분할 종목 ────────────────────────────────────────────────────────────────────────────
def test_split_stock_has_no_allowed_signal_on_either_side() -> None:
    repos, refs, days, _ = _close_world()
    live = [r for r in repos.rules.signals(days[0], days[-1], mode="close")
            if r.code == SPLIT_CODE and r.status in ALLOWED]  # fmt: skip
    assert live == []
    for ref in refs.values():
        bt = _backtest(repos, ref, start=days[0], end=days[-1], intraday=False)
        assert not [k for k in bt if k[1] == SPLIT_CODE and k[4] == "allowed"], ref.rule_id
    irepos, irefs, idays = _intraday_world()
    assert not [r for r in irepos.rules.signals(idays[0], idays[-1], mode="intraday")
                if r.code == SPLIT_CODE and r.status in ALLOWED]  # fmt: skip
    assert irefs  # 장중 세계도 같은 분할 종목을 본다


# ── ④ KRX 확정이 종가를 바꾸면 data_revised(logic 0) ───────────────────────────────────────────
def test_krx_revision_is_classified_data_revised_not_logic() -> None:
    repos = repos_with_world(W)
    (rid,) = add_rules(repos, {"E1": W.examples["E1"]})
    ref = RuleRef(rid, W.examples["E1"].name, 1, dsl_hash(W.examples["E1"]), W.examples["E1"])
    days = list(W.days[-CLOSE_DAYS:])
    for d in days:
        res = R.close(
            job_ctx("rules.close", d.isoformat(), now=datetime.combine(d, time(16, 0), tzinfo=KST),
                    repos=repos, notify=FakeNotify())
        )  # fmt: skip
        assert res.status == "ok"
    live = [r for r in repos.rules.signals(days[0], days[-1], mode="close")
            if r.rule_id == rid and r.status in ALLOWED]  # fmt: skip
    assert live, "E1 실시간 신호가 있어야 변형을 걸 수 있다"
    target = live[0]
    # 다음 날 KRX 확정: 그날 종가를 크게 올려(과매도 아님) 같은 원장 재계산에서 신호가 사라지게 한다
    bars = [b for b in repos.market.series([target.code], target.trade_date, 1)[target.code]
            if b.date == target.trade_date]  # fmt: skip
    assert len(bars) == 1
    b = bars[0]
    assert b.close is not None and b.high is not None
    bump = b.close * 1.5
    repos.market.upsert_daily_bars(
        [replace(b, close=bump, high=max(b.high, bump))], loaded_by="krx.daily"
    )
    out = nightly_rule(
        repos, ref, day=days[-1],
        now=datetime.combine(days[-1], time(8, 45), tzinfo=KST) + timedelta(days=1),
        cfg=CFG, cal=KR, split_ratio=SPLIT_RATIO,
    )  # fmt: skip
    diffs = [d for m in out.matches for d in m.diffs]
    assert out.logic_n == 0
    assert any(d["code"] == target.code and d["reason"] == "data_revised" for d in diffs), diffs

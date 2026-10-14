"""BKT 시험·웹 픽스처 공용 — 합성 세계(p8_world)에 규칙·'실시간' 신호를 싣는다(실데이터 아님).

'실시간' 신호는 실시간 작업을 돌리는 대신 같은 평가기(백테스트 엔진 = 같은 frame·evaluate·cooldown)
로 만든 상태 `dry` 행이다 — 성과·통계·일치 감시 시험의 입력. 실시간 작업 자체와의 일치는 완료 시험
(WRAP `tests/acceptance/test_rules_parity.py`)이 본다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Final

from kbj.config.rules_cfg import RulesConfig, load_rules_cfg
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.rows import IntradayInvestor, Investor
from kbj.core.rules_rows import RuleRow, RuleVersion, SignalRow
from kbj.engines.rules.backtest import run_backtest
from kbj.engines.rules.dsl import dsl_hash
from kbj.engines.rules.live import signal_id
from kbj.services.engine.rules_perf import RuleRef, code_data, rule_codes
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.p8_world import P8World, build_world

__all__ = ["RATIO", "live_signals", "put_live_flows", "rule_id_of", "seed_rule", "seeded"]

RATIO: Final = 0.31  # config/board.yaml integrity.split_guard_ratio 와 같은 값(시험 고정)


def rule_id_of(key: str) -> str:
    return "r_" + key.lower().rjust(12, "0")


def seed_rule(repos: MemoryRepos, w: P8World, key: str, *, version: int = 1) -> RuleRef:
    rule = w.examples[key]
    rid = rule_id_of(key)
    h = dsl_hash(rule)
    head = RuleRow(rid, rule.name, version, True, None, "example", w.clock, w.clock, "test")
    ver = RuleVersion(rid, version, rule.model_dump(mode="json", exclude_none=True), h, w.clock)
    repos.personal.put_rule_personal(head, ver)
    return RuleRef(rid, rule.name, version, h, rule)


def put_live_flows(repos: MemoryRepos, w: P8World) -> int:
    """세계의 장중 가집계(LedgerRow — estimated) → `investor_intraday` 행(flows.intraday 대역)."""
    rows: list[IntradayInvestor] = []
    for (code, ts), r in sorted(w.live.items()):
        for inv, v in ((Investor.FOREIGN, r.foreign), (Investor.INSTITUTION, r.inst)):
            if v is not None:
                rows.append(IntradayInvestor(code, ts, inv, "KRX", v, None, None, "kis",
                                             Quality.ESTIMATED))  # fmt: skip
    return repos.flows.put_intraday(rows, loaded_by="test", received_at=w.clock)


def live_signals(
    repos: MemoryRepos,
    ref: RuleRef,
    *,
    start: date,
    end: date,
    cfg: RulesConfig,
    cal: TradingCalendar,
    now: datetime,
) -> list[SignalRow]:
    """같은 평가기로 만든 '실시간' 신호(상태 dry — 쿨다운에 막힌 칸은 cooldown)."""
    codes, hold = rule_codes(repos, ref.rule, day=end)
    modes = ("close",) if ref.rule.mode == "close" else ("intraday_replay",)
    slots_from = start if modes != ("close",) else None
    data = code_data(repos, codes, upto=end, cfg=cfg, cal=cal, split_ratio=RATIO,
                     slots_from=slots_from, hold_since=hold)  # fmt: skip
    cool = ref.rule.alert.cooldown_min or cfg.cooldown_min.default
    out = run_backtest(ref.rule, data, rule_id=ref.rule_id, start=start, end=end, modes=modes,
                       cooldown_min=cool)  # fmt: skip
    rows: list[SignalRow] = []
    for s in out.signals:
        mode = "close" if s.mode == "close" else "intraday"
        ev: dict[str, Any] = {"source": "test"}
        rows.append(
            SignalRow(
                signal_id(mode, s.eval_ts, ref.rule_id, ref.version, s.code),
                mode,
                ref.rule_id,
                ref.version,
                ref.dsl_hash,
                s.code,
                s.eval_ts,
                s.trade_date,
                s.entry_price,
                ev,
                s.input_fp,
                s.status,
                None,
                None,
                s.quality,
                f"KBJ:rules.{mode}",
            )
        )
    if any(r.mode == "close" for r in rows):
        repos.rules.put_signals_close([r for r in rows if r.mode == "close"])
    if any(r.mode == "intraday" for r in rows):
        repos.rules.put_signals_intraday([r for r in rows if r.mode == "intraday"])
    return rows


def seeded(
    keys: Sequence[str] = ("E1",), *, live_days: int = 60
) -> tuple[MemoryRepos, P8World, dict[str, RuleRef], RulesConfig, TradingCalendar]:
    """세계 + 규칙 + 마지막 `live_days` 거래일의 '실시간' 신호."""
    w = build_world()
    repos = memory_repos()
    w.fill(repos)
    put_live_flows(repos, w)
    cfg, cal = load_rules_cfg(), TradingCalendar.default()
    refs = {k: seed_rule(repos, w, k) for k in keys}
    start = w.days[-live_days]
    for ref in refs.values():
        live_signals(repos, ref, start=start, end=w.days[-1], cfg=cfg, cal=cal, now=w.clock)
    return repos, w, refs, cfg, cal

"""`/시그널` 봇 명령(P8 묶음 EVL — docs/p8_design.md §1.2): 오늘 규칙 신호 최신 10.

읽기 함수(`kbj.services.engine.rules.command_reader` — 저장소만)를 넘기면 동작하고, 넘기지 않으면
'준비 중(P8)'. 합성 입력(메모리 저장소).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from kbj.core.quality import Quality
from kbj.core.rules_rows import SignalRow
from kbj.core.time import KST
from kbj.engines.rules.live import signal_id
from kbj.services.engine.rules import command_reader
from kbj.services.notifier.commands import default_registry
from kbj.store.repos.memory import MemoryRepos
from tests.unit.services.rules_world import add_rules

DAY = date(2026, 10, 27)
T0 = datetime(2026, 10, 27, 9, 0, tzinfo=KST)
RULE = {
    "v": 1,
    "name": "장중 급등",
    "universe": {"codes": ["Q80015"]},
    "mode": "intraday",
    "when": {"all": [{"ind": "change_pct", "op": ">=", "value": 5}]},
}


def _sig(rid: str, k: int, status: str, code: str = "Q80015") -> SignalRow:
    ts = T0 + timedelta(minutes=10 * k)
    return SignalRow(
        signal_id=signal_id("intraday", ts, rid, 1, code),
        mode="intraday",
        rule_id=rid,
        version=1,
        dsl_hash="0" * 64,
        code=code,
        eval_ts=ts,
        trade_date=DAY,
        entry_price=100.0,
        evidence={"v": 1},
        input_fp="0" * 16,
        status=status,  # type: ignore[arg-type]
        skip_reason="no_live_flow" if status == "skipped" else None,
        ticket=None,
        quality=Quality.ESTIMATED,
        source="KBJ:rules.intraday",
    )


def test_읽기_함수가_있으면_오늘_신호_최신순() -> None:
    repos = MemoryRepos()
    (rid,) = add_rules(repos, [RULE])
    repos.rules.put_signals_intraday(
        [
            _sig(rid, 1, "sent"),
            _sig(rid, 3, "dry", "Q80016"),
            _sig(rid, 2, "cooldown"),
            _sig(rid, 4, "skipped", "Q80014"),
        ]
    )
    reg = default_registry(signals=command_reader(repos, lambda: T0 + timedelta(hours=1)))
    r = reg.dispatch("/시그널", chat_id=1)
    assert r is not None and r.parse_mode == "HTML"
    lines = r.text.splitlines()
    assert "최신 2/2" in lines[0]
    assert lines[1] == "09:30 장중 · 장중 급등 · Q80016"
    assert lines[2] == "09:10 장중 · 장중 급등 · Q80015"
    alias = reg.dispatch("/signals@kbj_bot", chat_id=1)
    assert alias is not None and alias.text == r.text
    assert "준비 중" not in reg.help_text().split("/시그널")[1].split("\n")[0]


def test_신호가_없으면_없다고() -> None:
    reg = default_registry(signals=command_reader(MemoryRepos(), lambda: T0))
    r = reg.dispatch("/시그널", chat_id=1)
    assert r is not None and "오늘 신호 없음" in r.text


def test_읽기_함수가_없으면_준비_중() -> None:
    r = default_registry().dispatch("/시그널", chat_id=1)
    assert r is not None and "준비 중" in r.text and "P8" in r.text

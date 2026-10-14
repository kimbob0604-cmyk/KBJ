"""P8 저장소(`rules`·`personal`·`journal` 읽기 — 0021) — 메모리판 계약 + Pg 판과 같은 메서드.

Postgres 판의 실제 왕복은 통합 시험(Docker — 메인이 돌린다). 여기서는 메모리판의 쓰기·읽기 규칙과 두
판의 메서드 모양이 같은지(같은 계약)를 본다.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from kbj.core.quality import Quality
from kbj.core.rules_rows import (
    AnalysisRow,
    BacktestRun,
    BacktestSignal,
    MatchRow,
    OutcomeRow,
    PickRow,
    QuoteSlot,
    RuleRow,
    RuleVersion,
    SignalRow,
    TradeRow,
)
from kbj.core.time import KST
from kbj.store.repos import memory_repos
from kbj.store.repos.journal import JournalRepo, MemoryJournalRepo, PgJournalRepo
from kbj.store.repos.personal import MemoryPersonalRepo, PersonalRepo, PgPersonalRepo
from kbj.store.repos.rules import MemoryRulesRepo, PgRulesRepo, RulesRepo

T = datetime(2026, 11, 2, 10, 0, tzinfo=KST)
D = T.date()
RID = "r_abcdefghijkl"
H = "b" * 64


def head(**kw: object) -> RuleRow:
    base: dict[str, object] = dict(
        rule_id=RID,
        name="규칙",
        current_version=1,
        enabled=True,
        archived_at=None,
        source="api",
        created_at=T,
        updated_at=T,
        loaded_by="test",
    )
    base.update(kw)
    return RuleRow(**base)  # type: ignore[arg-type]


def sig(n: int, mode: str = "intraday", **kw: object) -> SignalRow:
    pre = "si_" if mode == "intraday" else "sc_"
    base: dict[str, object] = dict(
        signal_id=f"{pre}01JBX00000000000000000{n:04d}",
        mode=mode,
        rule_id=RID,
        version=1,
        dsl_hash=H,
        code="005930",
        eval_ts=T + timedelta(minutes=10 * n),
        trade_date=D,
        entry_price=100.0,
        evidence={"a": 1},
        input_fp="0" * 16,
        status="sent",
        skip_reason=None,
        ticket=None,
        quality=Quality.ESTIMATED,
        source="KBJ:rules.intraday",
    )
    base.update(kw)
    return SignalRow(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("proto", "impls"),
    [
        (RulesRepo, (PgRulesRepo, MemoryRulesRepo)),
        (PersonalRepo, (PgPersonalRepo, MemoryPersonalRepo)),
        (JournalRepo, (PgJournalRepo, MemoryJournalRepo)),
    ],
)
def test_pg_and_memory_share_protocol(proto: type, impls: tuple[type, ...]) -> None:
    names = {n for n, _ in inspect.getmembers(proto, inspect.isfunction) if not n.startswith("_")}
    for impl in impls:
        for n in names:
            assert hasattr(impl, n), (impl.__name__, n)
            assert inspect.signature(getattr(impl, n)).parameters.keys() == (
                inspect.signature(getattr(proto, n)).parameters.keys()
            ), (impl.__name__, n)


def test_rules_and_versions_via_personal() -> None:
    r = memory_repos()
    v1 = RuleVersion(RID, 1, {"v": 1}, H, T)
    r.personal.put_rule_personal(head(), v1)
    assert r.rules.rule(RID) == head()
    assert r.rules.enabled_rules() == [(head(), v1)]
    with pytest.raises(ValueError):
        r.personal.put_rule_personal(head(), v1)  # 판은 바꾸지 않는다
    with pytest.raises(ValueError):
        r.personal.put_rule_personal(head(current_version=3), RuleVersion(RID, 2, {}, H, T))
    v2 = RuleVersion(RID, 2, {"v": 1, "x": 1}, H, T)
    r.personal.put_rule_personal(head(current_version=2), v2)
    assert [v.version for v in r.rules.versions(RID)] == [1, 2]
    assert r.personal.set_rule_state_personal(RID, updated_at=T, enabled=False)
    assert r.rules.enabled_rules() == []
    assert r.personal.set_rule_state_personal(RID, updated_at=T, archived_at=T)
    assert r.rules.rules() == [] and len(r.rules.rules(include_archived=True)) == 1
    assert not r.personal.set_rule_state_personal("r_zzzzzzzzzzzz", updated_at=T)


def test_signals_idempotent_keep_id_and_mode_tables() -> None:
    r = memory_repos().rules
    r.put_signals_intraday([sig(1, status="dry")])
    # 같은 (규칙·판·종목·시각)을 다시 쓰면 한 행 — id 는 처음 값, 상태는 새 값
    again = sig(1, status="sent", ticket="k")
    again = replace(again, signal_id="si_01JBX000000000000000009999")
    r.put_signals_intraday([again])
    got = r.signals(D, D)
    assert len(got) == 1 and got[0].status == "sent" and got[0].signal_id.endswith("0001")
    with pytest.raises(ValueError):
        r.put_signals_intraday([sig(2, mode="close")])
    r.put_signals_close([sig(3, mode="close", source="KBJ:rules.close")])
    assert [s.mode for s in r.signals(D, D)] == ["intraday", "close"]
    assert len(r.signals(D, D, mode="close")) == 1
    assert len(r.signals_since(RID, "005930", T + timedelta(minutes=15))) == 1


def test_quote_slots_window_and_outcomes() -> None:
    r = memory_repos().rules
    s = [
        QuoteSlot(
            T + timedelta(minutes=10 * i),
            "005930",
            1.0,
            1.0,
            1.0,
            1.0,
            1,
            None,
            None,
            (),
            Quality.ESTIMATED,
            "KIS",
        )
        for i in range(3)
    ]
    r.put_quote_slots_intraday(s)
    r.put_quote_slots_intraday(s)
    assert r.quote_slots(None, T, T + timedelta(minutes=20)) == s[:2]
    assert r.quote_slots({"000660"}, T, T + timedelta(hours=1)) == []
    o = OutcomeRow(
        sig(1).signal_id,
        1,
        D + timedelta(days=1),
        101.0,
        0.01,
        "1001",
        0.0,
        0.02,
        -0.01,
        None,
        Quality.OK,
        T,
        "KBJ:rules.outcome",
    )
    r.put_outcomes_rules_outcome([o, o])
    assert r.outcomes({o.signal_id}) == [o]


def test_backtest_nightly_one_unit() -> None:
    r = memory_repos().rules
    run = BacktestRun(
        "bt_01JBX0000000000000000000AB",
        RID,
        1,
        H,
        "close",
        D,
        D,
        {},
        T,
        {"n": 0},
        T,
        Quality.OK,
        "KBJ:rules.backtest_nightly",
    )
    bs = BacktestSignal(run.run_id, "005930", D, T, "close", 100.0, {"h1": None}, "dry", "0" * 16)
    m = MatchRow(
        D,
        RID,
        1,
        1,
        1,
        ({"code": "005930", "reason": "data_revised"},),
        T,
        Quality.OK,
        "KBJ:rules.backtest_nightly",
    )
    assert r.put_backtest_nightly(run, [bs], [m]) == 3
    assert r.backtest_runs(rule_id=RID) == [run]
    assert r.backtest_signals(run.run_id) == [bs]
    assert r.match_checks(D, D) == [m]
    with pytest.raises(ValueError):
        r.put_backtest_nightly(run, [replace(bs, run_id="bt_01JBX0000000000000000000CD")], [])


def test_journal_write_via_personal_read_via_journal() -> None:
    repos = memory_repos()
    a = AnalysisRow(
        None,
        "005930",
        D,
        (None, None, None),
        Decimal("70000"),
        None,
        None,
        None,
        None,
        None,
        None,
        {"base": Decimal("5000")},
        {},
        {},
        None,
        None,
        "메모",
        (),
        None,
        {"price": "kbj"},
        "api",
        T,
    )
    i = repos.personal.add_analysis_personal(a)
    assert repos.journal.analysis(i) == replace(a, id=i)
    assert repos.personal.update_analysis_personal(i, replace(a, memo="고침"))
    assert repos.journal.analyses(code="005930")[0].memo == "고침"
    assert repos.personal.delete_analysis_personal(i) and repos.journal.analysis(i) is None
    t1 = TradeRow(
        None,
        "005930",
        None,
        "buy",
        D,
        Decimal("100"),
        10,
        Decimal(0),
        Decimal(0),
        None,
        None,
        None,
        "api",
        T,
    )
    t0 = replace(t1, trade_date=D - timedelta(days=1))
    j1 = repos.personal.add_trade_personal(t1)
    j0 = repos.personal.add_trade_personal(t0)
    assert [t.id for t in repos.journal.trades()] == [j0, j1]  # (매매일, id) 순
    assert repos.personal.delete_trade_personal(j1) and not repos.personal.delete_trade_personal(j1)
    with pytest.raises(ValueError):
        repos.personal.add_trade_personal(replace(t1, id=5))
    p = PickRow(D, "sd", 1, "005930", None, None, None, None)
    repos.personal.put_picks_legacy_import([p], loaded_by="legacy_import")
    assert repos.journal.picks(D, D) == [p]
    assert repos.journal.picks(date(2027, 1, 1)) == []

"""규칙 쓰기 모듈 `kbj.services.personal.rules`(묶음 EVL — D-P8-15·16): 만들기·새 판(판은 바꾸지
않는다)·상태·보관·예시 가져오기·CLI. 메모리 저장소."""

from __future__ import annotations

import io
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from kbj.config.rules_cfg import load_rules_cfg
from kbj.engines.rules.dsl import DslError, load_example_rules
from kbj.services import personal
from kbj.services.personal.rules import (
    RuleNotFound,
    RuleStateError,
    cli,
    create_rule,
    import_examples,
    new_version,
    set_state,
)
from kbj.store.repos.memory import MemoryRepos
from tests.fixtures.synthetic.p8_world import build_world

CFG = load_rules_cfg()
NOW = build_world().clock
RULE: dict[str, Any] = {
    "v": 1,
    "name": "RSI 과매도",
    "universe": {"watchlist": "main"},
    "mode": "close",
    "when": {"all": [{"ind": "rsi", "op": "<", "value": 30}]},
}


def _seed() -> bytes:
    return b"\x01" * 10


def _new(repos: MemoryRepos, dsl: dict[str, Any] | None = None, **kw: Any) -> Any:
    return create_rule(
        repos,
        dsl or RULE,
        enabled=kw.pop("enabled", False),
        source="api",
        now=NOW,
        loaded_by="test",
        cfg=CFG,
        **kw,
    )


def test_create_normalizes_and_stores_version_1() -> None:
    repos = MemoryRepos()
    r = _new(repos, seed=_seed)
    assert r.version == 1 and r.created and not r.enabled and not r.archived
    head = repos.rules.rule(r.rule_id)
    assert head is not None and head.name == "RSI 과매도" and head.source == "api"
    (v,) = repos.rules.versions(r.rule_id)
    assert v.dsl["when"]["all"][0]["p"] == {"n": 14}  # 기본값을 채운 정규화
    assert v.dsl["alert"]["cooldown_min"] == CFG.cooldown_min.default
    assert v.dsl_hash == r.dsl_hash


def test_create_rejects_bad_dsl_with_pointer_only() -> None:
    bad = {
        **RULE,
        "when": {"all": [{"ind": "rsi", "op": "<", "value": 30, "p": {"n": 999}}]},
        "alert": {"cooldown_min": 5},
    }
    with pytest.raises(DslError) as ei:
        _new(MemoryRepos(), bad)
    reasons = {(i.pointer, i.reason) for i in ei.value.issues}
    assert ("/when/all/0/p/n", "param_range") in reasons
    assert ("/alert/cooldown_min", "cooldown_floor") in reasons


def test_id_collision_retries_with_new_seed() -> None:
    repos = MemoryRepos()
    seeds = iter([b"\x02" * 10, b"\x02" * 10, b"\x03" * 10])
    a = _new(repos, seed=lambda: next(seeds))
    b = _new(repos, seed=lambda: next(seeds))
    assert a.rule_id != b.rule_id


def test_new_version_never_rewrites_old_and_same_hash_keeps_version() -> None:
    repos = MemoryRepos()
    r = _new(repos)
    same = new_version(repos, r.rule_id, {**RULE, "name": "이름만"}, enabled=True, now=NOW, cfg=CFG)
    assert same.version == 1 and not same.created and same.enabled
    assert repos.rules.rule(r.rule_id).name == "이름만"  # type: ignore[union-attr]
    changed = {**RULE, "when": {"all": [{"ind": "rsi", "op": "<", "value": 25}]}}
    v2 = new_version(repos, r.rule_id, changed, enabled=None, now=NOW + timedelta(1), cfg=CFG)
    assert v2.version == 2 and v2.created and v2.enabled  # 켜짐은 그대로
    vs = repos.rules.versions(r.rule_id)
    assert [v.version for v in vs] == [1, 2]
    assert vs[0].dsl["when"]["all"][0]["value"] == 30  # 1판은 그대로
    with pytest.raises(RuleNotFound):
        new_version(repos, "r_zzzzzzzzzzzz", RULE, enabled=None, now=NOW, cfg=CFG)


def test_state_enable_disable_archive() -> None:
    repos = MemoryRepos()
    r = _new(repos)
    assert set_state(repos, r.rule_id, "enable", now=NOW).enabled
    assert [h.rule_id for h, _ in repos.rules.enabled_rules()] == [r.rule_id]
    assert not set_state(repos, r.rule_id, "disable", now=NOW).enabled
    a = set_state(repos, r.rule_id, "archive", now=NOW)
    assert a.archived and not a.enabled
    with pytest.raises(RuleStateError):
        set_state(repos, r.rule_id, "enable", now=NOW)
    with pytest.raises(RuleStateError):
        new_version(repos, r.rule_id, RULE, enabled=None, now=NOW, cfg=CFG)
    assert repos.rules.rules() == []  # 보관은 목록 기본에서 빠진다
    assert len(repos.rules.rules(include_archived=True)) == 1  # 지우지 않는다
    with pytest.raises(RuleNotFound):
        set_state(repos, "r_zzzzzzzzzzzz", "disable", now=NOW)


def test_import_examples_is_idempotent() -> None:
    repos = MemoryRepos()
    got = import_examples(repos, now=NOW, loaded_by="test", cfg=CFG)
    assert len(got) == len(load_example_rules()) == 10
    assert all(not g.enabled for g in got)
    assert {h.source for h in repos.rules.rules()} == {"example"}
    assert import_examples(repos, now=NOW, loaded_by="test", cfg=CFG) == []


def test_cli_round_trip(tmp_path: Path) -> None:
    repos = MemoryRepos()
    f = tmp_path / "rule.json"
    f.write_text(json.dumps(RULE, ensure_ascii=False), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()

    def run(*args: str) -> int:
        return cli(list(args), repos, now=lambda: NOW, out=out, err=err, cfg=CFG)

    assert run("add", str(f), "--enable") == 0
    rid = out.getvalue().split()[0]
    assert run("list") == 0 and "켜짐 RSI 과매도" in out.getvalue()
    assert run("show", rid) == 0 and '"rsi"' in out.getvalue()
    y = tmp_path / "rule.yaml"
    y.write_text(
        "v: 1\nname: 새판\nuniverse: {codes: [Q80001]}\nmode: close\n"
        "when: {all: [{ind: close, op: '>', value: 1}]}\n",
        encoding="utf-8",
    )
    assert run("update", rid, str(y)) == 0 and "2판" in out.getvalue()
    assert run("archive", rid) == 0 and "보관" in out.getvalue()
    assert run("enable", rid) == 1 and "거절" in err.getvalue()
    assert run("disable", "r_zzzzzzzzzzzz") == 1 and "없는 규칙" in err.getvalue()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**RULE, "mode": "weekly"}), encoding="utf-8")
    assert run("add", str(bad)) == 1 and "/mode" in err.getvalue()
    assert run("add", str(tmp_path / "없음.json")) == 2
    assert run("examples") == 0 and "예시 10개" in out.getvalue()


def test_package_main_dispatches_groups(capsys: pytest.CaptureFixture[str]) -> None:
    repos = MemoryRepos()
    assert personal.main(["nope"], repos=repos) == 2
    assert personal.main(["rules", "list"], repos=repos) == 0

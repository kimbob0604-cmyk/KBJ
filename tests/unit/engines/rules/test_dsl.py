"""규칙 DSL v1(§4.1 — D-P8-5): 모양·의미 검사·정규화·해시·예시·스키마."""

from __future__ import annotations

import io
import json

import pytest

from kbj.engines.rules import dsl
from kbj.engines.rules.dsl import DslError, DslLimits, dsl_hash, validate_rule

BASE = {"v": 1, "name": "x", "universe": {"watchlist": "main"}, "mode": "close"}


def _rule(when: dict[str, object], **kw: object) -> dict[str, object]:
    return {**BASE, "when": when, **kw}


def _reasons(data: object, limits: DslLimits | None = None) -> set[tuple[str, str]]:
    with pytest.raises(DslError) as ei:
        validate_rule(data, limits)
    return {(i.pointer, i.reason) for i in ei.value.issues}


def test_valid_rule_normalized_defaults() -> None:
    r = validate_rule(_rule({"all": [{"ind": "rsi", "op": "<", "value": 30}]}))
    d = r.model_dump(mode="json", exclude_none=True)
    assert d["when"]["all"][0]["p"] == {"n": 14}
    assert d["alert"]["cooldown_min"] == 1440
    assert d["when"]["all"][0]["value"] == 30.0


def test_same_meaning_same_hash_name_excluded() -> None:
    a = validate_rule(_rule({"all": [{"ind": "rsi", "op": "<", "value": 30}]}))
    b = validate_rule(
        {
            **_rule({"all": [{"ind": "rsi", "p": {"n": 14}, "op": "<", "value": 30.0}]}),
            "name": "다른 이름",
            "alert": {"cooldown_min": 1440},
        }
    )
    assert dsl_hash(a) == dsl_hash(b)
    c = validate_rule(_rule({"all": [{"ind": "rsi", "op": "<", "value": 31}]}))
    assert dsl_hash(a) != dsl_hash(c)
    assert len(dsl_hash(a)) == 64


def test_shape_errors_have_pointer_and_no_input_value() -> None:
    secret = "비밀값_입력"
    issues = _reasons(_rule({"all": [{"ind": secret, "op": "<", "value": 1}]}))
    assert ("/when/all/0/ind", "literal_error") in issues
    assert all(secret not in p and secret not in r for p, r in issues)
    assert ("/when/all/0/op", "literal_error") in _reasons(
        _rule({"all": [{"ind": "rsi", "op": "<<", "value": 1}]})
    )
    assert ("/universe/codes/0", "string_pattern_mismatch") in _reasons(
        {**_rule({"all": [{"ind": "rsi", "op": "<", "value": 1}]}), "universe": {"codes": ["x"]}}
    )


def test_semantic_errors() -> None:
    data = {
        **BASE,
        "universe": {"market": "ALL"},
        "mode": "intraday",
        "when": {
            "all": [
                {"ind": "bb", "field": "lower", "op": "<", "ref": {"ind": "rsi"}},
                {"ind": "macd", "op": "<", "value": 1, "p": {"zz": 1}},
                {"ind": "new_high", "op": "==", "value": 1},
                {"ind": "rsi", "op": "==", "value": 1},
                {"ind": "rsi", "op": "between", "value": [5, 1]},
                {"ind": "rsi", "op": "turns_positive"},
                {"ind": "rsi", "op": "<"},
                {"ind": "trail_from_peak", "op": "<", "value": -5},
                {"ind": "sma", "p": {"n": 1}, "op": "<", "value": 1},
                {"ind": "bb", "op": "<", "value": 1},
            ]
        },
        "alert": {"cooldown_min": 30},
    }
    got = _reasons(data)
    want = {
        ("/universe/market", "close_only"),
        ("/when/all/0/ref", "unit_mismatch"),
        ("/when/all/1/p/zz", "unknown_param"),
        ("/when/all/2/ind", "close_only"),
        ("/when/all/3/op", "eq_integer_only"),
        ("/when/all/4/value", "between_range"),
        ("/when/all/5/op", "turns_unit"),
        ("/when/all/6", "value_or_ref"),
        ("/when/all/7/ind", "portfolio_only"),
        ("/when/all/8/p/n", "param_range"),
        ("/when/all/9/field", "field_required"),
        ("/alert/cooldown_min", "cooldown_floor"),
    }
    assert want <= got


def test_depth_and_condition_limits() -> None:
    leaf = {"ind": "rsi", "op": "<", "value": 30}
    deep = _rule({"all": [{"any": [{"all": [{"any": [leaf]}]}]}]})
    assert ("/when", "max_depth") in _reasons(deep)
    many = _rule({"all": [leaf] * 13})
    assert ("/when", "max_conditions") in _reasons(many)
    assert validate_rule(many, DslLimits(max_conditions=13))


def test_live_flow_allowed_intraday() -> None:
    """사용자 결정 2026-10-13 — 장중 수급 조건 허용(값은 가집계 잠정)."""
    r = validate_rule(
        {**_rule({"all": [{"ind": "foreign_streak", "op": ">=", "value": 1}]}), "mode": "intraday"}
    )
    assert r.mode == "intraday"


def test_examples_file_all_valid() -> None:
    ex = dsl.load_example_rules()
    assert list(ex) == [f"E{i}" for i in range(1, 11)]
    modes = [r.mode for r in ex.values()]
    assert modes.count("close") == 7 and modes.count("intraday") == 3
    assert len({dsl_hash(r) for r in ex.values()}) == 10


def test_load_examples_reports_pointer() -> None:
    with pytest.raises(DslError) as ei:
        dsl.load_examples({"examples": {"X": _rule({"all": [{"ind": "rsi", "op": "<"}]})}})
    assert ei.value.issues[0].pointer == "/examples/X/when/all/0"


def test_schema_generated_and_fresh() -> None:
    s = dsl.json_schema()
    assert s["$id"] == "kbj:rule/v1"
    json.dumps(s)
    out = io.StringIO()
    assert dsl.main(["schema", "--check"], out) == 0, out.getvalue()

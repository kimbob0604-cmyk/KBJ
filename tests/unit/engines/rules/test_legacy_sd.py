"""SD 알림 규칙 → DSL 변환 표(docs/p8_design.md §4.1.5) — 변환 결과가 실제 DSL 검사를 통과한다."""

from __future__ import annotations

import pytest

from kbj.config.rules_cfg import load_rules_cfg
from kbj.engines.rules.dsl import limits_of, validate_rule
from kbj.engines.rules.legacy_sd import (
    COOLDOWN_MIN,
    SD_TYPES,
    Converted,
    Unconverted,
    convert,
)

LIM = limits_of(load_rules_cfg())


def _leaf(c: Converted) -> dict[str, object]:
    return c.rule["when"]["all"][0]


@pytest.mark.parametrize("sd_type", SD_TYPES)
def test_every_type_validates(sd_type: str) -> None:
    value = 70000 if sd_type.startswith("price_") else 2
    got = convert({"code": "990001", "type": sd_type, "value": value}, 0)
    assert isinstance(got, Converted)
    rule = validate_rule(got.rule, LIM)
    assert rule.mode == "both"
    assert rule.alert.cooldown_min == COOLDOWN_MIN


@pytest.mark.parametrize(
    ("item", "leaf"),
    [
        ({"type": "price_above", "value": 70000}, {"ind": "close", "op": ">=", "value": 70000}),
        (
            {"type": "price_below", "value": "1,000.5"},
            {"ind": "close", "op": "<=", "value": 1000.5},
        ),
        ({"type": "change_down", "value": -3}, {"ind": "change_pct", "op": "<=", "value": -3}),
        ({"type": "rsi_oversold"}, {"ind": "rsi", "op": "<=", "value": 30, "p": {"n": 14}}),
        (
            {"type": "rsi_overbought", "value": 75},
            {"ind": "rsi", "op": ">=", "value": 75, "p": {"n": 14}},
        ),
        ({"type": "foreign_strong_buy"}, {"ind": "foreign_streak", "op": ">=", "value": 3}),
        (
            {"type": "foreign_cum_buy", "value": 50},
            {"ind": "foreign_sum", "op": ">=", "value": 5_000_000_000, "p": {"n": 5}},
        ),
        (
            {"type": "macd_golden"},
            {
                "ind": "macd",
                "field": "line",
                "op": "cross_above",
                "ref": {"ind": "macd", "field": "signal"},
            },
        ),
    ],
)
def test_table(item: dict[str, object], leaf: dict[str, object]) -> None:
    got = convert({"code": "5930", **item}, 3)
    assert isinstance(got, Converted)
    assert _leaf(got) == leaf
    assert got.rule["universe"] == {"codes": ["005930"]}  # 앞자리 0 채움


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        ({"code": "AAPL", "market": "us", "type": "price_above", "value": 1}, "not_kr"),
        ({"code": "990001", "type": "volume_spike", "value": 2}, "unsupported_type"),
        ({"code": "990001", "type": "bb_upper"}, "unsupported_type"),
        ({"code": "990001", "type": "price_above", "value": 0}, "bad_value"),
        ({"code": "bad!", "type": "price_above", "value": 1}, "bad_code"),
        ({"code": "990001", "type": "price_above"}, "bad_value"),
        ({"code": "990001", "type": "change_up", "value": "abc"}, "bad_value"),
        ({"code": "990001", "type": "rsi_oversold", "value": True}, "bad_value"),
    ],
)
def test_unconverted(item: dict[str, object], reason: str) -> None:
    got = convert(item, 7)
    assert isinstance(got, Unconverted)
    assert got.reason == reason and got.index == 7


def test_name_and_enabled() -> None:
    got = convert({"code": "990001", "type": "macd_dead", "name": "합성", "enabled": False}, 0)
    assert isinstance(got, Converted)
    assert got.rule["name"] == "[SD] 합성 · MACD 데드크로스"
    assert got.enabled is False
    long = convert({"code": "990001", "type": "macd_dead", "name": "가" * 200}, 0)
    assert isinstance(long, Converted) and len(long.rule["name"]) == 80

"""`config/rules.yaml` 해석기(§3.5) — 기본값(사용자 결정 2026-10-13)·모르는 키 오류."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.rules_cfg import load_rules_cfg, parse_rules_cfg
from kbj.engines.rules.dsl import limits_of


def _raw() -> dict[str, Any]:
    return load_yaml_mapping("rules.yaml")


def test_defaults_match_user_decisions() -> None:
    cfg = load_rules_cfg()
    assert cfg.anchor_date == date(2020, 1, 2)
    assert cfg.horizons == (1, 3, 5, 10, 20)
    assert (cfg.cooldown_min.default, cfg.cooldown_min.floor) == (1440, 60)
    assert cfg.intraday_max_codes == 60
    assert (cfg.costs.fee_bps, cfg.costs.sell_tax_pct, cfg.costs.slippage_bps) == (1.5, 0.20, 0)
    assert cfg.match.window_days == 20 and cfg.nightly.years == 3
    assert set(cfg.index) == {"KOSPI", "KOSDAQ"}
    lim = limits_of(cfg)
    assert (lim.max_depth, lim.max_conditions, lim.cooldown_floor) == (3, 12, 60)


@pytest.mark.parametrize(
    "patch",
    [
        {"unknown_key": 1},
        {"horizons": [5, 1]},
        {"cooldown_min": {"default": 30, "floor": 60}},
        {"index": {"KOSPI": "1001"}},
        {"costs": {"fee_bps": -1, "sell_tax_pct": 0.2, "slippage_bps": 0}},
        {"dsl": {"max_depth": 3, "max_conditions": 12, "extra": 1}},
    ],
)
def test_rejects_bad(patch: dict[str, Any]) -> None:
    with pytest.raises(ConfigFileError):
        parse_rules_cfg({**_raw(), **patch})

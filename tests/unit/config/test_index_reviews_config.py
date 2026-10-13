"""`config/index_reviews.yaml` — 규칙 숫자가 한 곳에 출처·확인일·[확인 필요]와 함께 있다(완료
기준)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.config.files import REPO_ROOT, ConfigFileError
from kbj.config.index_reviews import Param, load_index_reviews, parse_index_reviews

RAW: dict[str, Any] = yaml.safe_load(
    (REPO_ROOT / "config" / "index_reviews.yaml").read_text(encoding="utf-8")
)
CFG = load_index_reviews()


def _params(node: object) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if set(node) == {"v", "src", "checked", "verify"}:
            return [node]
        for v in node.values():
            out += _params(v)
    elif isinstance(node, list):
        for v in node:
            out += _params(v)
    return out


def test_every_rule_number_is_a_sourced_param() -> None:
    for section in ("msci", "krx"):
        for name, rules in RAW[section].items():
            for key, val in rules.items():
                if isinstance(val, int | float) and not isinstance(val, bool):
                    pytest.fail(f"{section}.{name}.{key}: 맨 숫자 — {{v, src, checked, verify}} 로")
    params = _params(RAW)
    assert len(params) >= 40
    for p in params:
        assert p["src"].strip() and isinstance(p["checked"], date)


def test_unverified_numbers_are_marked() -> None:
    unv = CFG.unverified()
    for want in ("msci_korea.add_mcap_mult", "msci_korea.add_ff_mult", "msci_korea.keep_mcap_mult",
                 "kospi200.keep_rank_ratio", "fif_rounding.msci_2026_05"):  # fmt: skip
        assert want in unv
    # null(아직 모름)인 추종 자금은 지어내지 않는다
    assert CFG.msci_rules("msci_korea").aum_won.v is None
    assert CFG.krx_rules("kospi200").non_etf_aum_won.v is None


def test_first_wave_indexes_and_second_wave_off() -> None:
    assert [i.id for i in CFG.enabled_indexes()] == ["msci_korea", "kospi200", "kosdaq150"]
    assert {i.id for i in CFG.indexes if not i.enabled} == {"ftse_korea", "krx300"}


def test_reviews_are_dated_not_computed_and_unconfirmed_is_flagged() -> None:
    for name, revs in CFG.reviews.items():
        for r in revs:
            assert r.base <= r.announce <= r.trade < r.effective, (name, r.id)
            assert re.fullmatch(r"\d{4}-\d{2}", r.id)
    assert CFG.current_review("msci_korea", date(2026, 10, 13)).id == "2026-11"  # type: ignore[union-attr]
    assert CFG.current_review("kospi200", date(2026, 10, 13)).id == "2026-12"  # type: ignore[union-attr]
    assert CFG.current_review("kospi200", date(2027, 1, 1)) is None


def test_bad_shapes_are_rejected(tmp_path: Path) -> None:
    bad = yaml.safe_load(yaml.safe_dump(RAW))
    bad["msci"]["msci_korea"]["coverage_low"]["v"] = 0.95  # low > coverage
    with pytest.raises(ConfigFileError):
        parse_index_reviews(bad)
    bad = yaml.safe_load(yaml.safe_dump(RAW))
    bad["reviews"]["msci_korea"][0]["effective"] = date(2026, 1, 1)
    with pytest.raises(ConfigFileError):
        parse_index_reviews(bad)
    bad = yaml.safe_load(yaml.safe_dump(RAW))
    bad["msci"]["msci_korea"]["add_mcap_mult"]["unknown"] = 1
    with pytest.raises(ConfigFileError):
        parse_index_reviews(bad)
    bad = yaml.safe_load(yaml.safe_dump(RAW))
    del bad["krx"]["kospi200"]
    with pytest.raises(ConfigFileError):
        parse_index_reviews(bad)


def test_param_model() -> None:
    p = Param(v=1.8, src="x", checked=date(2026, 10, 13), verify=True)
    assert p.v == 1.8

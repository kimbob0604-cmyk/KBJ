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
    # 2차 자료뿐인 KRX 값·원문 표를 못 옮긴 FOL 중간 단계는 [확인 필요]로 남는다
    for want in ("kospi200.keep_rank_ratio", "kosdaq150.add_rank_ratio",
                 "kospi200.sector_cum_mcap", "fol.room_keep_mid_factor",
                 "fif_rounding.msci_legacy"):  # fmt: skip
        assert want in unv
    # GIMI 2026-08판 원문으로 확인한 MSCI 배수·반올림 규칙은 해제(2026-10-14)
    for done in ("msci_korea.add_mcap_mult_new", "msci_korea.add_mcap_mult_migrate",
                 "msci_korea.add_ff_mult", "msci_korea.keep_mcap_mult",
                 "msci_korea.low_fif_ff_mult_add", "fif_rounding.msci_2026_05"):  # fmt: skip
        assert done not in unv
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
    bad["msci"]["msci_korea"]["add_mcap_mult_new"]["unknown"] = 1
    with pytest.raises(ConfigFileError):
        parse_index_reviews(bad)
    bad = yaml.safe_load(yaml.safe_dump(RAW))
    del bad["krx"]["kospi200"]
    with pytest.raises(ConfigFileError):
        parse_index_reviews(bad)


def test_gimi_2026_08_values() -> None:
    m = CFG.msci_rules("msci_korea")
    assert (m.val("add_mcap_mult_new"), m.val("add_mcap_mult_migrate"), m.val("add_ff_mult")) == (
        1.0, 1.5, 0.5,
    )  # fmt: skip
    assert (m.val("keep_mcap_mult"), m.val("keep_ff_mult")) == (0.6667, 0.3333)
    assert (m.opt("low_fif_ff_mult_add"), m.opt("low_fif_ff_mult_keep")) == (0.9, 0.6)
    assert m.light_rebalance is not None and not m.light_rebalance.enabled
    assert m.segment_default == "migrate"
    k = CFG.krx_rules("kosdaq150")
    assert (k.val("add_rank_ratio"), k.val("keep_rank_ratio"), k.opt("sector_cum_mcap")) == (
        0.8, 1.2, 0.6,
    )  # fmt: skip
    assert CFG.krx_rules("kospi200").opt("sector_cum_mcap") == 0.85
    assert CFG.fol.limits == ()  # 종목별 한도 표는 사용자가 확인해 채운다
    msci = {r.id: (r.announce, r.confirmed) for r in CFG.reviews_of("msci_korea")}
    assert msci["2026-08"] == (date(2026, 8, 13), True)  # KST(CET 08-12 밤)
    assert msci["2026-11"] == (date(2026, 11, 12), True)
    assert all(c for _, c in msci.values())
    assert not any(r.confirmed for r in CFG.reviews_of("kospi200"))  # KRX 발표일은 추정


def test_param_model() -> None:
    p = Param(v=1.8, src="x", checked=date(2026, 10, 13), verify=True)
    assert p.v == 1.8


def test_krics_sectors_prefix_override_and_official() -> None:
    sec = CFG.sectors
    assert len(sec.groups) == 9 and "모빌리티" in sec.groups
    assert sec.sector_of("26110") == "정보기술"
    assert sec.sector_of("20423") == "소비재"  # 화장품 — 20 보다 긴 앞자리가 이긴다
    assert sec.sector_of("58211") == "미디어및콘텐츠" and sec.sector_of("58221") == "정보기술"
    assert sec.sector_of("71511", "034730") == "정보기술"  # SK — 지주회사지만 보도된 재분류
    assert sec.sector_of("26110", "999999", {"999999": "소재"}) == "소재"  # 공식 분류가 먼저
    assert sec.sector_of(None) == sec.default == "기타"


def test_krics_sector_names_must_be_known() -> None:
    raw = yaml.safe_load((REPO_ROOT / "config" / "index_reviews.yaml").read_text(encoding="utf-8"))
    raw["sectors"]["map"]["99"] = "커뮤니케이션"
    with pytest.raises(ConfigFileError, match="groups 에 없는 산업군"):
        parse_index_reviews(raw)

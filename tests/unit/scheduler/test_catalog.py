"""kbj/data/catalog.py — 어댑터 데이터셋을 모두 모으고, id 유일·등급↔저장 스키마가 맞다.

설계 §1.7.
"""

from __future__ import annotations

import pytest

from kbj.data import catalog
from kbj.data.private.kis import datasets as kis_datasets
from kbj.data.private.krx import datasets as krx_datasets
from kbj.data.public.dart import datasets as dart_datasets
from kbj.data.spec import DataKey, Tier, Venue


def test_collects_every_adapter_once() -> None:
    cat = catalog.all_datasets()
    for group in (kis_datasets.DATASETS, krx_datasets.DATASETS, dart_datasets.DATASETS):
        for spec in group:
            assert cat[spec.id] is spec
    assert len(cat) == sum(len(g) for g in catalog._ADAPTERS)  # pyright: ignore[reportPrivateUsage]


def test_tier_matches_store_schema() -> None:
    for spec in catalog.all_datasets().values():
        if spec.store:
            assert spec.store.startswith("pub_") is (spec.tier is Tier.PUBLIC), spec.id


def test_planned_sources_are_marked_and_private_by_default() -> None:
    for spec in catalog.PLANNED:
        assert "[어댑터 없음" in spec.notes
    tiers = {s.id: s.tier for s in catalog.PLANNED}
    assert tiers["YAHOO:macro"] is Tier.PRIVATE and tiers["FRED:gov_series"] is Tier.PUBLIC


def test_get_and_unknown() -> None:
    assert catalog.get("DART:corpCode").store == "pub_filings.corp_code"
    with pytest.raises(catalog.UnknownDataset):
        catalog.get("NAVER:daily")  # 네이버 어댑터는 만들지 않는다(U4)


def test_duplicate_ids_fail() -> None:
    spec = catalog.get("DART:corpCode")
    with pytest.raises(ValueError, match="겹친다"):
        catalog._collect([(spec,), (spec,)])  # pyright: ignore[reportPrivateUsage]


def test_keys_for_expands_venues() -> None:
    spec = catalog.get("KIS:stock_investor_daily")
    keys = catalog.keys_for(spec, "2026-10-06")
    assert [k.venue for k in keys] == ["KRX", "NXT", "TOTAL"]
    assert catalog.keys_for(spec, "2026-10-06", [Venue.NXT]) == [
        DataKey("KIS", "stock_investor_daily", "2026-10-06", "NXT")
    ]
    plain = catalog.get("DART:corpCode")
    assert catalog.keys_for(plain, "2026-10-06") == [DataKey("DART", "corpCode", "2026-10-06")]
    with pytest.raises(ValueError):
        catalog.keys_for(plain, "2026-10-06", [Venue.KRX])


def test_quote_on_demand_is_not_claimable() -> None:
    assert not catalog.claimable(catalog.get("KIS:quote_on_demand"))
    assert catalog.claimable(catalog.get("KIS:watch_quotes_intraday"))
    assert catalog.claimable(catalog.get("DART:document"))

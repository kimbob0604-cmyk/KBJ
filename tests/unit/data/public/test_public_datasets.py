"""공개 어댑터 데이터셋 명세(`kbj/data/public/*/datasets.py`) — 등급·저장 표·리미터·예산·id 유일.

카탈로그(`kbj/data/catalog.py`, 묶음 F)가 모으기 전에 C 묶음 쪽에서 지키는 것: 공개 등급만, `pub_*`
에만 쓰고, 리미터·예산 이름이 limits.yaml 에 있고, 클라이언트가 부르는 포털 ID 와 맞다.
"""

from __future__ import annotations

from kbj.data.limits import load_limits
from kbj.data.public.customs import client as customs_client
from kbj.data.public.customs import datasets as customs
from kbj.data.public.dart import datasets as dart
from kbj.data.public.ecos import datasets as ecos
from kbj.data.public.fsc_kofia_stats import client as kofia_client
from kbj.data.public.fsc_kofia_stats import datasets as kofia
from kbj.data.public.kosis import datasets as kosis
from kbj.data.spec import AS_OF_KINDS, Tier

ALL = (*dart.DATASETS, *ecos.DATASETS, *kosis.DATASETS, *customs.DATASETS, *kofia.DATASETS)


def test_every_public_dataset_is_public_and_stored_in_pub_schemas() -> None:
    assert ALL
    for d in ALL:
        assert d.tier is Tier.PUBLIC, d.id
        assert d.store.startswith("pub_"), d.id
        assert d.as_of_kind in AS_OF_KINDS
        assert d.id == f"{d.source}:{d.dataset}"


def test_ids_are_unique_across_public_adapters() -> None:
    ids = [d.id for d in ALL]
    assert len(ids) == len(set(ids))


def test_limiters_and_budgets_exist_in_limits_yaml() -> None:
    limits = load_limits()
    for d in ALL:
        src = limits.source(d.limiter)
        if d.budget is not None:
            b = limits.source(d.budget)
            assert b.daily_cap is not None or b.daily_cap_per_dataset is not None, d.id
        assert src.rate > 0


def test_design_dataset_lists() -> None:
    """설계 §2 표의 P2 데이터셋(카탈로그 id)이 다 있다."""
    ids = {d.id for d in ALL}
    expected = {
        "DART:corpCode",
        "DART:list",
        "DART:document",
        "DART:company",
        "DART:fnlttSinglAcntAll",
        "DART:fnlttMultiAcnt",
        "ECOS:817Y002",
        "ECOS:722Y001",
        "ECOS:404Y014",
        "ECOS:402Y014",
        "ECOS:401Y015",
        "ECOS:161Y005",
        "ECOS:301Y013",
        "ECOS:200Y102",
        "ECOS:513Y001",
        "ECOS:731Y003",
        "ECOS:721Y001",
        "KOSIS:DT_1C8015",
        "KOSIS:DT_1JH20201",
        "KOSIS:DT_1DA7001S",
        "KOSIS:DT_1J22003",
        "DATAGO:15100475",
        "DATAGO:15101609",
        "DATAGO:15101612",
        "DATAGO:15134343",
        "DATAGO:15157908",
        "DATAGO:15157941",
        "DATAGO:15157901",
        "DATAGO:15157909",
        "DATAGO:15094809/credit",
        "DATAGO:15094809/capital",
        "DATAGO:15094809/fund",
        "DATAGO:15094809/cma",
    }
    assert expected <= ids, sorted(expected - ids)


def test_datago_datasets_match_client_portal_ids() -> None:
    assert {d.dataset for d in customs.DATASETS} == set(customs_client.DATASET_IDS)
    assert {d.dataset.split("/")[0] for d in kofia.DATASETS} == {kofia_client.DATASET}
    assert {d.dataset.split("/")[1] for d in kofia.DATASETS} == set(kofia_client.OPERATIONS)
    ten_day = {d.dataset for d in customs.DATASETS if d.as_of_kind == "ten_day"}
    assert ten_day == set(customs_client.TEN_DAY.values())
    # 소매채권수익률(15094783)은 폐지 — 받지 않는다(probe_results F1)
    assert all("15094783" not in d.dataset for d in ALL)

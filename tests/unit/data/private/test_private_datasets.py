"""로그인 어댑터 데이터셋(KRX·금융위 시세·ECOS 타기관) — 카탈로그·등록부가 기대는 성질(새 시험).

카탈로그(`kbj/data/catalog.py`, 묶음 F)가 모으기 전에 묶음 D 쪽에서 지키는 것: id 유일·형식, 등급은
모두 로그인이고 저장 표는 `prv_*`, 리미터·예산 이름이 config/limits.yaml 에 있다, KRX 엔드포인트와
데이터셋이 하나씩 맞는다, 거래소를 나누는 데이터셋은 데이터 키에 거래소가 들어간다(D7).
"""

from __future__ import annotations

import pytest

from kbj.data.limits import load_limits
from kbj.data.private import ecos_restricted
from kbj.data.private.fsc_index_price import datasets as fsc_index
from kbj.data.private.fsc_stock_price import datasets as fsc_stock
from kbj.data.private.krx import datasets as krx
from kbj.data.private.krx.client import ENDPOINTS, dataset_of
from kbj.data.spec import DataKey, DatasetSpec, Tier, Venue

ALL: tuple[DatasetSpec, ...] = (
    *krx.DATASETS,
    *fsc_stock.DATASETS,
    *fsc_index.DATASETS,
    *ecos_restricted.DATASETS,
)


def test_ids_are_unique_private_and_stored_in_prv_schemas() -> None:
    ids = [d.id for d in ALL]
    assert len(ids) == len(set(ids))
    for d in ALL:
        assert d.tier is Tier.PRIVATE
        assert d.store.startswith("prv_"), d.id


def test_limiters_and_budgets_exist_in_limits_yaml() -> None:
    limits = load_limits()
    for d in ALL:
        limits.source(d.limiter)
        if d.budget is not None:
            limits.source(d.budget)


def test_every_krx_endpoint_has_exactly_one_dataset() -> None:
    assert sorted(d.dataset for d in krx.DATASETS) == sorted(dataset_of(e) for e in ENDPOINTS)
    assert all(d.limiter == "krx" and d.budget == "krx" for d in krx.DATASETS)


@pytest.mark.parametrize("dataset", ["sto/stk_bydd_trd", "sto/ksq_bydd_trd", "etp/etf_bydd_trd"])
def test_d7_datasets_put_the_venue_in_the_data_key(dataset: str) -> None:
    spec = next(d for d in krx.DATASETS if d.dataset == dataset)
    assert spec.venues == (Venue.KRX,)
    assert spec.key("2026-09-30", Venue.KRX) == DataKey("KRX", dataset, "2026-09-30", "KRX")
    with pytest.raises(ValueError):
        spec.key("2026-09-30")  # 거래소를 빼먹으면 오류
    with pytest.raises(ValueError):
        spec.key("2026-09-30", Venue.NXT)  # KRX OpenAPI 는 NXT 를 주지 않는다 [실측 필요]


def test_d7_field_notes_name_turnover_nav_shares_and_net_assets() -> None:
    by = {d.dataset: d for d in krx.DATASETS}
    assert "ACC_TRDVAL" in by["sto/stk_bydd_trd"].notes
    assert "ACC_TRDVAL" in by["sto/ksq_bydd_trd"].notes
    etf = by["etp/etf_bydd_trd"].notes
    for field in ("NAV", "LIST_SHRS", "INVSTASST_NETASST_TOTAMT", "ACC_TRDVAL", "[실측 필요]"):
        assert field in etf


def test_krx_daily_data_is_keyed_by_the_previous_trading_day() -> None:
    """KRX 일별은 다음 영업일 08:00 공표 — 등록부 `krx.daily` 가 @prev_trading_day 로 받는다."""
    assert {d.as_of_kind for d in krx.DATASETS} == {"prev_trading_day"}
    assert {d.as_of_kind for d in (*fsc_stock.DATASETS, *fsc_index.DATASETS)} == {
        "prev_trading_day"
    }


def test_derivatives_keep_the_gx_tables_and_indexes_do_not_split_venues() -> None:
    by = {d.dataset: d for d in krx.DATASETS}
    assert by["drv/fut_bydd_trd"].store == "prv_gex.krx_fut_daily"
    assert by["drv/opt_bydd_trd"].store == "prv_gex.krx_opt_daily"
    for name in ("idx/kospi_dd_trd", "idx/kosdaq_dd_trd", "idx/krx_dd_trd"):
        assert by[name].venues == () and by[name].key("2026-09-30").venue == ""


def test_fsc_datasets_are_datago_ids_with_the_unconfirmed_index_id_marked() -> None:
    (stock,) = fsc_stock.DATASETS
    (index,) = fsc_index.DATASETS
    assert stock.id == "DATAGO:15094808" and stock.limiter == stock.budget == "datago"
    assert index.id == "DATAGO:15094807" and "[확인 필요]" in index.notes
    assert "_V2" in stock.notes  # F7: V2 경로

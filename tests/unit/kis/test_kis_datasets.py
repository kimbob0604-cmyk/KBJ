"""KIS 논리 데이터셋 — 등급·저장 스키마·거래소 구분·D7 TR(docs/metrics.md, 설계 §2·§6.7)."""

from __future__ import annotations

import pytest

from kbj.data.limits import load_limits
from kbj.data.private.kis.datasets import DATASETS
from kbj.data.spec import Tier, Venue

BY_ID = {d.id: d for d in DATASETS}

# 설계 §2 표·§6.7 등록부가 쓰는 KIS 데이터셋 — 카탈로그에 빠지면 등록부 검증이 깨진다
DESIGN_IDS = {
    "KIS:stock_quote_eod",
    "KIS:stock_investor_daily",
    "KIS:market_investor_daily",
    "KIS:inst_foreign_top",
    "KIS:watch_quotes_intraday",
    "KIS:fo_master",
    "KIS:fut_minute_day",
    "KIS:fut_minute_night",
    "KIS:option_chain_live",
    "KIS:market_investor_intraday",
    "KIS:consensus_estimate",
    "KIS:quote_on_demand",
    "KIS:ws_ticks",
    # P3(docs/p3_design.md §3.3)
    "KIS:index_quote_intraday",
    "KIS:sector_quote_intraday",
    "KIS:etf_investor_daily",
}

# 메인 결정 D7 — 거래대금·투자자별 순매수·ETF 수급에 필요한 KIS TR
D7_TRS = {
    "FHKST01010900": "KIS:stock_investor_daily",  # 종목별 투자자
    "FHPTJ04400000": "KIS:inst_foreign_top",  # 외국인·기관 가집계
    "FHPST01710000": "KIS:turnover_rank_intraday",  # 거래량 순위
    "FHPST02400000": "KIS:etf_quote_intraday",  # ETF 현재가
}


def test_ids_are_unique_and_cover_the_design() -> None:
    assert len(BY_ID) == len(DATASETS)
    assert DESIGN_IDS <= set(BY_ID)


def test_all_kis_datasets_are_private_with_the_app_key_limiter() -> None:
    kis = load_limits().source("kis")
    assert kis.daily_cap is None  # KIS 일 한도 미공표 — 예산 없음
    for d in DATASETS:
        assert d.source == "KIS" and d.tier is Tier.PRIVATE
        assert d.limiter == "kis" and d.budget is None
        assert d.store == "" or d.store.startswith("prv_"), d.id  # 로그인 등급은 prv_* 에만


@pytest.mark.parametrize(("tr", "dataset_id"), sorted(D7_TRS.items()))
def test_d7_trs_are_registered_and_marked_unmeasured(tr: str, dataset_id: str) -> None:
    d = BY_ID[dataset_id]
    assert tr in d.notes
    assert "[실측 필요]" in d.notes


@pytest.mark.parametrize(
    "dataset_id",
    [
        "KIS:stock_quote_eod",
        "KIS:stock_investor_daily",
        "KIS:market_investor_daily",
        "KIS:inst_foreign_top",
        "KIS:inst_foreign_intraday",
        "KIS:turnover_rank_intraday",
    ],
)
def test_turnover_and_flow_datasets_split_by_venue(dataset_id: str) -> None:
    """거래대금·순매수는 KRX·NXT 를 나눠 받는다(metrics §1) — 데이터 키에 거래소가 들어간다."""
    d = BY_ID[dataset_id]
    assert {Venue.KRX, Venue.NXT} <= set(d.venues)
    key = d.key("2026-10-06", Venue.NXT)
    assert key.venue == "NXT" and key.label() == f"{dataset_id}@2026-10-06[NXT]"
    with pytest.raises(ValueError, match="venue"):
        d.key("2026-10-06")  # 거래소를 나누는 데이터셋은 구분이 필수


def test_unsplit_dataset_keys_have_no_venue() -> None:
    d = BY_ID["KIS:fo_master"]
    assert d.venues == () and d.key("2026-10-06").venue == ""


def test_on_demand_quote_is_not_stored() -> None:
    assert BY_ID["KIS:quote_on_demand"].store == ""
    assert BY_ID["KIS:quote_on_demand"].as_of_kind == "event"


# ── P3 데이터셋(docs/p3_design.md §3.3, D-P3-14) ─────────────────────────────────────────────

P3_STORES = {
    "KIS:index_quote_intraday": "prv_market.index_intraday",
    "KIS:sector_quote_intraday": "prv_market.sector_intraday",
    "KIS:inst_foreign_intraday": "prv_flows.investor_intraday",  # 장중 이력(원장 오늘 행도 갱신)
    "KIS:turnover_rank_intraday": "prv_market.turnover_rank_intraday",
    "KIS:etf_quote_intraday": "prv_etf.quote_intraday",
    "KIS:etf_investor_daily": "prv_flows.stock_investor_daily",
    "KIS:inst_foreign_top": "prv_flows.stock_investor_daily",
}


@pytest.mark.parametrize(("dataset_id", "store"), sorted(P3_STORES.items()))
def test_p3_stores_are_fixed(dataset_id: str, store: str) -> None:
    d = BY_ID[dataset_id]
    assert d.store == store
    assert "저장 표 확정은 P3 [확인 필요]" not in d.notes  # P3 에 확정했다


def test_p3_intraday_market_datasets() -> None:
    for ds, tr in (
        ("KIS:index_quote_intraday", "FHPUP02100000"),
        ("KIS:sector_quote_intraday", "FHPUP02140000"),
    ):
        d = BY_ID[ds]
        assert d.as_of_kind == "slot10m" and d.venues == ()  # 지수는 거래소를 나누지 않는다
        assert tr in d.notes and "[추정 TR]" in d.notes and "[실측 필요" in d.notes


def test_etf_investor_daily_is_split_by_venue_and_unmeasured() -> None:
    d = BY_ID["KIS:etf_investor_daily"]
    assert d.as_of_kind == "trade_date"
    assert {v.value for v in d.venues} == {"KRX", "NXT", "TOTAL"}
    assert "FHKST01010900" in d.notes and "[실측 필요" in d.notes

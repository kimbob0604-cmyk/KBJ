"""카탈로그 P3 변경(docs/p3_design.md §3.3).

ETF 운용사 데이터셋 이전, 새 KIS 데이터셋, 저장 표 확정.
"""

from __future__ import annotations

from kbj.data import catalog
from kbj.data.limits import load_limits
from kbj.data.private.etf_issuers import datasets as etf_issuer_datasets
from kbj.data.spec import Tier
from kbj.services.scheduler.registry import migration_tables

TABLES = migration_tables()


def test_etf_issuers_moved_out_of_planned() -> None:
    spec = catalog.get("ETF_ISSUERS:pdf")
    assert spec is etf_issuer_datasets.DATASETS[0]
    assert "ETF_ISSUERS:pdf" not in {s.id for s in catalog.PLANNED}  # 두 벌 금지
    assert spec.tier is Tier.PRIVATE and spec.store == "prv_etf.holding"
    assert "[어댑터 없음" not in spec.notes
    assert load_limits().source("etf_issuers").rate == 1.0


def test_every_adapter_limiter_is_configured() -> None:
    """어댑터가 있는 데이터셋의 리미터는 limits.yaml 에 있다(PLANNED 는 어댑터가 생길 때 더한다)."""
    limits = load_limits().sources
    planned = {s.id for s in catalog.PLANNED}
    used = {s.limiter for s in catalog.all_datasets().values() if s.id not in planned}
    assert sorted(used - set(limits)) == []


def test_p3_stores_are_created_by_migrations() -> None:
    """P3 에 쓰는 데이터셋의 저장 표는 0001~0009 가 만든다(등록부가 켤 때 검사하는 것과 같은 표)."""
    p3 = [
        "KIS:index_quote_intraday", "KIS:sector_quote_intraday", "KIS:inst_foreign_intraday",
        "KIS:turnover_rank_intraday", "KIS:etf_quote_intraday", "KIS:etf_investor_daily",
        "KIS:stock_quote_eod", "KIS:stock_investor_daily", "KIS:market_investor_daily",
        "KIS:inst_foreign_top", "ETF_ISSUERS:pdf",
    ]  # fmt: skip
    for ds in p3:
        assert catalog.get(ds).store in TABLES, ds


def test_planned_now_only_lists_p5_sources() -> None:
    assert {s.source for s in catalog.PLANNED} == {
        "NASDAQ", "YAHOO", "FRED", "TREASURY", "NYFED", "FF", "ANTHROPIC",
    }  # fmt: skip

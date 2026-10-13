"""`kbj.store.repos.trade` 메모리판 — 키·처음 값 유지·None 이 값을 지우지 않음·매핑 통째 교체·거르기
(docs/p6_design.md §1.1·§3.3). Pg 판은 같은 `Table` 명세를 쓰고 통합 시험이 표를 확인한다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from kbj.core import trade_rows as tr
from kbj.core.quality import Quality
from kbj.store.repos import Repos, memory_repos
from kbj.store.repos.trade import MemoryTradeRepo, PgTradeRepo

T0 = datetime(2026, 10, 11, 0, 20, tzinfo=UTC)
T1 = T0 + timedelta(days=1)


def ten(usd: int | None, *, at: datetime = T0, col: int = 0, q: str = "estimated") -> tr.TenDayRow:
    return tr.TenDayRow(
        kind="exp_item", ym="2026-10", seg=1, col_no=col, name="전체",
        usd=None if usd is None else Decimal(usd), first_usd=None if usd is None else Decimal(usd),
        first_seen_at=at, span_text="01~10", source="DATAGO:15157908", as_of=at, quality=Quality(q),
        revised_at=None if at == T0 else at,
    )  # fmt: skip


def item(ym: str, hs: str, exp: int | None, *, q: str = "ok") -> tr.ItemMonthRow:
    return tr.ItemMonthRow(
        ym=ym, hs=hs, hs_len=len(hs), exp_usd=None if exp is None else Decimal(exp),
        exp_kg=None, imp_usd=None, imp_kg=None, bal_usd=None, source="DATAGO:15101609",
        as_of=T0, quality=Quality(q),
    )  # fmt: skip


def smap(code: str, hs: str, origin: str = "public", **kw: Any) -> tr.StockMapRow:
    base: dict[str, Any] = dict(
        code=code, hs=hs, flow="exp", region_id="", name="가상", weight=0.5, origin=origin,
        reviewed=False, map_version="v1", source="KBJ:trade_map", as_of=T0, quality=Quality.OK,
    )  # fmt: skip
    base.update(kw)
    return tr.StockMapRow(**base)


def test_registered_in_repo_bundles() -> None:
    assert isinstance(memory_repos().trade, MemoryTradeRepo)
    assert "trade" in Repos.__dataclass_fields__
    assert repr(PgTradeRepo(lambda: None)) == "PgTradeRepo()"  # type: ignore[arg-type, return-value]


def test_ten_day_keeps_first_value_and_revises_latest() -> None:
    repo = MemoryTradeRepo()
    assert repo.upsert_ten_day([ten(1000)], loaded_by="trade.customs_tenday", received_at=T0) == 1
    assert repo.upsert_ten_day([ten(1200, at=T1)], loaded_by="trade.customs_tenday", received_at=T1)
    (r,) = repo.ten_day(kind="exp_item")
    assert r.usd == Decimal(1200) and r.first_usd == Decimal(1000)  # 발표 당시 값 유지
    assert r.first_seen_at == T0 and r.revised_at == T1
    # 값이 None 인 새 행은 있는 값을 지우지 않는다
    repo.upsert_ten_day([ten(None, at=T1, q="invalid")], loaded_by="x", received_at=T1)
    assert repo.ten_day()[0].usd == Decimal(1200)


def test_ten_day_columns_keep_seen_at() -> None:
    repo = MemoryTradeRepo()
    c = tr.TenDayColumn(kind="exp_item", col_no=1, valid_from="2026-01", name="반도체", seen_at=T0)
    repo.upsert_ten_day_columns([c], loaded_by="t")
    repo.upsert_ten_day_columns([tr.TenDayColumn(**{**c.__dict__, "seen_at": T1})], loaded_by="t")
    assert repo.ten_day_columns("exp_item")[0].seen_at == T0
    assert repo.ten_day_columns("imp_item") == []


def test_item_filters_prefix_len_range_and_last_ym() -> None:
    repo = MemoryTradeRepo()
    rows = [
        item("2026-08", "85", 10), item("2026-08", "8542", 7), item("2026-09", "854232", 5),
        item("2026-09", "8471", 3), item("2026-09", "30", None, q="invalid"),
    ]  # fmt: skip
    assert repo.upsert_item_monthly(rows, loaded_by="trade.customs_monthly", received_at=T0) == 5
    assert [r.hs for r in repo.item_monthly(prefix="85")] == ["85", "8542", "854232"]
    assert [r.hs for r in repo.item_monthly(hs_len=4)] == ["8542", "8471"]
    assert [r.ym for r in repo.item_monthly(ym_from="2026-09", prefix="8")] == ["2026-09"] * 2
    assert repo.item_monthly(ym_to="2026-07") == []
    assert repo.last_ym("pub_trade.item_monthly") == "2026-09"
    assert repo.last_ym("pub_trade.surge") is None
    with pytest.raises(ValueError):
        repo.last_ym("pub_trade.stock_map")  # ym 칸이 없다


def test_duplicate_keys_and_bad_meta_are_rejected() -> None:
    repo = MemoryTradeRepo()
    with pytest.raises(ValueError, match="같은 키"):
        repo.upsert_item_monthly([item("2026-09", "85", 1)] * 2, loaded_by="t", received_at=T0)
    with pytest.raises(ValueError):
        repo.upsert_item_monthly([item("2026-09", "85", 1)], loaded_by=" ", received_at=T0)
    with pytest.raises(ValueError):
        repo.upsert_item_monthly(
            [item("2026-09", "85", 1)],
            loaded_by="t",
            received_at=datetime(2026, 1, 1),  # noqa: DTZ001
        )


def test_replace_stock_map_swaps_one_origin_only() -> None:
    repo = MemoryTradeRepo()
    repo.replace_stock_map(
        [smap("000010", "8542"), smap("000020", "8703")], origin="public", loaded_by="m",
        received_at=T0,
    )  # fmt: skip
    repo.replace_stock_map([smap("000030", "8542", "local")], origin="local", loaded_by="m",
                           received_at=T0)  # fmt: skip
    repo.replace_stock_map([smap("000010", "854232")], origin="public", loaded_by="m",
                           received_at=T1)  # fmt: skip
    assert [(r.code, r.hs) for r in repo.stock_map(origin="public")] == [("000010", "854232")]
    assert [r.code for r in repo.stock_map(origin="local")] == ["000030"]
    assert len(repo.stock_map()) == 2
    with pytest.raises(ValueError, match="다른 origin"):
        repo.replace_stock_map([smap("000040", "85", "local")], origin="public", loaded_by="m",
                               received_at=T1)  # fmt: skip
    with pytest.raises(ValueError):
        repo.stock_map(origin="watchlist")


def test_derived_tables_round_trip() -> None:
    repo = MemoryTradeRepo()
    s = tr.SurgeRow(
        ym="2026-09", level="hs4", key="8542", flow="exp", rule="surge", yoy=0.6, yoy_3m=0.4,
        usd=Decimal(20_000_000), rank=1, source="KBJ:trade.derive", as_of=T0, quality=Quality.OK,
    )  # fmt: skip
    repo.upsert_surge([s], loaded_by="trade.derive", computed_at=T0)
    assert repo.surge(ym="2026-09", level="hs4", flow="exp") == [s]
    assert repo.surge(flow="imp") == []
    e = tr.StockExportRow(
        code="000010", period="2026Q3", export_usd=Decimal(1), yoy=None, revenue_yoy=None,
        status="short_overlap", map_version="v1", source="KBJ:trade.stock_index", as_of=T0,
        quality=Quality.ESTIMATED,
    )  # fmt: skip
    m = tr.StockExportRow(**{**e.__dict__, "period": "2026-09"})
    repo.upsert_stock_export([e, m], loaded_by="trade.stock_index", computed_at=T0)
    assert [r.period for r in repo.stock_export(code="000010")] == ["2026-09", "2026Q3"]
    assert [r.period for r in repo.stock_export(period_from="2026Q1")] == ["2026Q3"]
    # 통째 교체(WRAP — EXP·MAP 요청): 다시 돌려 빠진 행이 남지 않는다
    s2 = tr.SurgeRow(**{**s.__dict__, "key": "8541"})
    other = tr.SurgeRow(**{**s.__dict__, "ym": "2026-08"})
    repo.upsert_surge([other], loaded_by="trade.derive", computed_at=T0)
    repo.replace_surge([s2], ym="2026-09", loaded_by="trade.derive", computed_at=T1)
    assert [r.key for r in repo.surge(ym="2026-09")] == ["8541"]
    assert repo.surge(ym="2026-08") == [other]  # 다른 달은 그대로
    with pytest.raises(ValueError, match="다른 달"):
        repo.replace_surge([other], ym="2026-09", loaded_by="trade.derive", computed_at=T1)
    n = tr.StockExportRow(**{**e.__dict__, "code": "000020"})
    repo.replace_stock_export([n], loaded_by="trade.stock_index", computed_at=T1)
    assert [r.code for r in repo.stock_export()] == ["000020"]


def hsd(hs: str, first: str, last: str) -> tr.HsDim:
    return tr.HsDim(
        hs=hs, hs_len=len(hs), name_ko="가상", first_ym=first, last_ym=last,
        source="DATAGO:15101609", as_of=T0, quality=Quality.OK,
    )  # fmt: skip


def test_hs_dim_merges_first_and_last_ym() -> None:
    """백필이 정기 수집 뒤에 더 옛날 달을 넣어도 first_ym 이 당겨지고 last_ym 은 줄지 않는다
    (COL 요청)."""
    repo = MemoryTradeRepo()
    repo.upsert_hs_dim([hsd("8542", "2026-08", "2026-09")], loaded_by="c", received_at=T0)
    repo.upsert_hs_dim([hsd("8542", "2021-10", "2021-12"), hsd("8541", "2021-10", "2021-10")],
                       loaded_by="c", received_at=T1)  # fmt: skip
    got = {d.hs: (d.first_ym, d.last_ym) for d in repo.hs_dim()}
    assert got == {"8542": ("2021-10", "2026-09"), "8541": ("2021-10", "2021-10")}
    assert repo.upsert_hs_dim([], loaded_by="c", received_at=T1) == 0

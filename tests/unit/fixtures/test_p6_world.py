"""합성 P6 세계(`tests/fixtures/synthetic/p6_world.py`) — 결정성·손계산 기대값
(`p6_expect.yaml`)·불변식.

근거: docs/p6_design.md §1.1·§8, metrics §25~§29. 합성만(실집계 아님 — 사용자 결정 2026-10-13 Q3).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.core.trade_rows import prev_ym
from kbj.store.repos.trade import MemoryTradeRepo
from tests.fixtures.synthetic.p6_world import SIDO, SYN, P6World, build_world

EXPECT: dict[str, Any] = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "fixtures/synthetic/p6_expect.yaml").read_text("utf-8")
)


@pytest.fixture(scope="module")
def w() -> P6World:
    return build_world()


def _exp(w: P6World, hs: str, ym: str) -> int:
    (r,) = [x for x in w.items if x.hs == hs and x.ym == ym]
    return int(r.exp_usd or 0)


def test_deterministic_and_marked_synthetic(w: P6World) -> None:
    assert build_world() is w and w.seed == EXPECT["seed"] == 20261211
    assert w.clock.isoformat() == EXPECT["clock"]
    assert w.digest() == build_world.__wrapped__(EXPECT["seed"]).digest()  # type: ignore[attr-defined]
    assert all(rec["_source"].startswith("SYNTHETIC") for rec in w.records())
    assert SYN.startswith("SYNTHETIC")
    assert all(d.name_ko.startswith("가상") for d in w.hs)
    assert all(c.country_name.startswith("가상국-") for c in w.countries)


def test_counts_and_months(w: P6World) -> None:
    m, c = EXPECT["months"], EXPECT["counts"]
    assert (len(w.months), w.months[0], w.months[-1]) == (m["n"], m["first"], m["last"])
    assert len(w.hs) == c["hs_dim"] and len(w.items) == c["items"]
    assert len(w.countries) == c["countries"] and len(w.item_countries) == c["item_countries"]
    assert len(w.ten_day) == c["ten_day"] and len(w.ten_day_columns) == c["ten_day_columns"]
    assert len(w.stocks) == c["stocks"] and len(w.watchlist) == c["watchlist"]
    assert len(w.map_codes("public")) == c["map_public_stocks"]
    assert len(w.map_codes("local")) == c["map_local_stocks"]


def test_planted_surge_plunge_zero_weight_and_missing(w: P6World) -> None:
    for case in (*EXPECT["surge"], *EXPECT["plunge"]):
        ym = case["ym"]
        now, prev = _exp(w, case["hs4"], ym), _exp(w, case["hs4"], prev_ym(ym, 12))
        assert (now, prev) == (case["usd"], case["usd_prev_year"])
        assert now / prev - 1 == pytest.approx(case["yoy"])
        three = [prev_ym(ym, k) for k in range(3)]
        cur = sum(_exp(w, case["hs4"], y) for y in three)
        old = sum(_exp(w, case["hs4"], prev_ym(y, 12)) for y in three)
        assert cur / old - 1 == pytest.approx(case["yoy_3m"])
    z = EXPECT["zero_weight"]
    (row,) = [r for r in w.items if r.hs == z["hs"] and r.ym == z["ym"]]
    assert row.exp_kg == Decimal(0) and (row.exp_usd or 0) > 0
    miss = EXPECT["missing"]
    here = {r.hs for r in w.items if r.ym == miss["ym"]}
    assert miss["hs6"] not in here and miss["parent_present"] in here
    assert not [h for h in here if h.startswith(miss["hs6"])]


def test_prefix_sum_deepest_rows_equal_parent(w: P6World) -> None:
    """같은 달 접두 아래 가장 깊은 행만 더하면 상위 행과 같다(결측 달 제외 — metrics §26)."""
    by: dict[str, list[Any]] = defaultdict(list)
    for r in w.items:
        by[r.ym].append(r)
    for ym in (w.months[0], w.months[-1]):
        rows = by[ym]
        codes = {r.hs for r in rows}
        for parent in [r for r in rows if r.hs_len < 10]:
            kids = [
                r for r in rows
                if r.hs.startswith(parent.hs) and r.hs != parent.hs
                and not any(c != r.hs and c.startswith(r.hs) for c in codes)
            ]  # fmt: skip
            assert sum(int(k.exp_usd or 0) for k in kids) == int(parent.exp_usd or 0), parent.hs


def test_country_total_equals_hs2_total(w: P6World) -> None:
    for ym in (w.months[0], w.months[-1]):
        hs2 = sum(int(r.exp_usd or 0) for r in w.items if r.ym == ym and r.hs_len == 2)
        cc = sum(int(r.exp_usd or 0) for r in w.countries if r.ym == ym)
        assert hs2 == cc
        assert {r.country for r in w.countries if r.ym == ym} == {
            r.country for r in w.countries if r.ym == w.months[0]
        }


def test_ten_day_revision_column_change_and_latest(w: P6World) -> None:
    rv = EXPECT["revision"]
    (r,) = [
        x for x in w.ten_day
        if (x.kind, x.ym, x.seg, x.col_no) == (rv["kind"], rv["ym"], rv["seg"], rv["col_no"])
    ]  # fmt: skip
    assert int((r.usd or 0) - (r.first_usd or 0)) == rv["usd_minus_first"]
    assert r.revised_at is not None
    assert all(x.usd == x.first_usd for x in w.ten_day if x is not r)
    cc = EXPECT["column_change"]
    names = {
        x.ym: x.name for x in w.ten_day
        if x.kind == cc["kind"] and x.col_no == cc["col_no"] and x.seg == 1
    }  # fmt: skip
    assert names[prev_ym(cc["valid_from"])] == cc["old"] and names[cc["valid_from"]] == cc["new"]
    hist = [c for c in w.ten_day_columns if c.kind == cc["kind"] and c.col_no == cc["col_no"]]
    assert sorted(c.valid_from for c in hist)[-1] == cc["valid_from"]
    lt = EXPECT["ten_day_latest"]
    last = [x for x in w.ten_day if x.ym == lt["ym"]]
    assert {x.seg for x in last} == {lt["seg"]} and {x.span_text for x in last} == {lt["span"]}
    # 누계: 1~10 ≤ 1~20 ≤ 월 전체, 전체 열 ≥ 10대 열 합
    seg = {(x.kind, x.ym, x.seg, x.col_no): int(x.usd or 0) for x in w.ten_day}
    assert seg[("exp_item", "2026-10", 1, 0)] <= seg[("exp_item", "2026-10", 2, 0)]
    assert seg[("exp_item", "2026-10", 2, 0)] <= seg[("exp_item", "2026-10", 3, 0)]
    assert (
        sum(seg[("imp_item", "2026-10", 3, n)] for n in range(1, 11))
        <= seg[("imp_item", "2026-10", 3, 0)]
    )


def test_reorg_region_keeps_one_id(w: P6World) -> None:
    ro = EXPECT["reorg"]
    rows = [r for r in w.sigungu if r.region_id == ro["region_id"]]
    old = {r.ym for r in rows if r.sido_cd == ro["old_sido"]}
    new = {r.ym for r in rows if r.sido_cd == ro["new_sido"]}
    assert max(old) == ro["old_to"] and min(new) == ro["new_from"] and not old & new
    map_hs6 = {m.hs for m in w.stock_map if m.region_id}
    assert {r.hs6 for r in w.sigungu} == map_hs6  # 시군구는 매핑 HS6 만(F3)
    assert {r.sido_cd for r in w.sigungu} <= set(SIDO)


def test_map_public_is_not_the_watchlist_and_local_is(w: P6World) -> None:
    pub, loc = set(w.map_codes("public")), set(w.map_codes("local"))
    assert loc <= set(w.watchlist) and not loc & pub
    assert pub != set(w.watchlist)  # 공개 매핑은 관심목록과 따로 고른다(R9)
    for code in pub | loc:
        lines = [m for m in w.stock_map if m.code == code]
        assert sum(m.weight for m in lines) <= 1 + 1e-9
        assert all(m.evidence and not m.reviewed for m in lines)
    st = EXPECT["status"]
    assert [m.hs for m in w.stock_map if m.code == st["no_trade"]] == ["9901"]
    assert not {m.hs[:2] for m in w.stock_map if m.code == st["no_trade"]} & {
        d.hs for d in w.hs if d.hs_len == 2
    }
    assert w.revenue(st["no_revenue"]) == ()
    assert len(w.revenue(st["short_overlap"])) == 6
    assert len(w.revenue("994010")) >= 8 and w.revenue("994010")[0][0].endswith("Q1")


def test_fill_into_memory_repo(w: P6World) -> None:
    repo = MemoryTradeRepo()
    w.fill(repo)
    assert len(repo.item_monthly()) == len(w.items)
    assert repo.last_ym("pub_trade.item_monthly") == w.months[-1]
    assert len(repo.ten_day(kind="exp_item")) == len(w.ten_day) // 4
    assert {r.code for r in repo.stock_map(origin="local")} == set(w.map_codes("local"))
    assert len(repo.sigungu_monthly(region_id="KR-46-a01")) > 0

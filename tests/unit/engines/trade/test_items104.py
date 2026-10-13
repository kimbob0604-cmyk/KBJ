"""104품목(metrics §29, D-P6-9 — 묶음 EXP): fixed 묶음 합·접두 겹침 오류·top_n 자동 선택(동률 코드
순)·값 없는 칸 생략·estimated."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from kbj.config.trade import ItemGroup, load_trade_items
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import ItemMonthRow
from kbj.engines.trade.explore import item_index
from kbj.engines.trade.items104 import (
    Group,
    GroupOverlap,
    check_groups,
    group_rows,
    groups_from_config,
    top_n_groups,
)
from tests.fixtures.synthetic.p6_world import build_world

T = datetime(2026, 12, 11, 11, 30, tzinfo=KST)
D = Decimal


def _it(ym: str, hs: str, eu: int, ek: int) -> ItemMonthRow:
    return ItemMonthRow(
        ym=ym, hs=hs, hs_len=len(hs), exp_usd=D(eu), exp_kg=D(ek), imp_usd=D(1), imp_kg=D(1),
        bal_usd=None, source="DATAGO:15101609", as_of=T, quality=Quality.OK,
    )  # fmt: skip


def test_접두_겹침은_오류() -> None:
    with pytest.raises(GroupOverlap):
        check_groups([Group("a", "가", ("85",)), Group("b", "나", ("8501",))])
    with pytest.raises(GroupOverlap):
        check_groups([Group("a", "가", ("8501", "850110"))])
    with pytest.raises(GroupOverlap):
        check_groups([Group("a", "가", ("8501",)), Group("a", "나", ("8502",))])
    check_groups([Group("a", "가", ("8501",)), Group("b", "나", ("8502", "8503"))])


def test_fixed_합·YoY·단가·생략() -> None:
    rows = [
        _it("2025-10", "8501", 50, 5), _it("2025-10", "8502", 50, 5),
        _it("2026-10", "8501", 90, 9), _it("2026-10", "8502", 60, 6),
        _it("2026-09", "8501", 10, 1),
    ]  # fmt: skip
    g = [Group("g001", "전기", ("8501", "8502")), Group("g002", "없음", ("9999",))]
    out = group_rows(g, item_index(rows, "exp"), item_index(rows, "imp"),
                     months=["2026-09", "2026-10"], mode="fixed", as_of=T)  # fmt: skip
    by = {(r.group_id, r.ym): r for r in out}
    assert set(by) == {("g001", "2026-09"), ("g001", "2026-10")}  # 값 없는 묶음·칸은 행 없음
    r = by[("g001", "2026-10")]
    assert r.exp_usd == D(150) and r.exp_kg == D(15) and r.unit_price == 10.0
    assert r.yoy == pytest.approx(0.5) and r.quality is Quality.OK and r.mode == "fixed"
    part = by[("g001", "2026-09")]
    assert part.exp_usd == D(10) and part.yoy is None and part.quality is Quality.ESTIMATED


def test_top_n_동률은_코드_순() -> None:
    rows = [
        _it("2026-10", "8502", 10, 1),
        _it("2026-10", "8501", 10, 1),
        _it("2026-10", "8503", 30, 1),
    ]
    got = top_n_groups(item_index(rows, "exp"), {"8503": "셋"}, ym="2026-10", n=2, months=12)
    assert [(g.id, g.name, g.hs) for g in got] == [
        ("hs8503", "셋", ("8503",)),
        ("hs8501", "HS 8501", ("8501",)),
    ]


def test_세계_top_n(world_ym: str = "2026-10") -> None:
    w = build_world()
    exp = item_index(w.items, "exp")
    groups = top_n_groups(exp, {d.hs: d.name_ko for d in w.hs}, ym=world_ym, n=104, months=12)
    assert len(groups) == 24  # 세계의 HS4 는 24개뿐
    out = group_rows(
        groups, exp, item_index(w.items, "imp"), months=[world_ym], mode="top_n", as_of=T
    )
    assert len(out) == 24 and all(r.mode == "top_n" for r in out)
    s8502 = next(r for r in out if r.group_id == "hs8502")
    assert s8502.exp_usd == D(120_000_000) and s8502.yoy == pytest.approx(1.0)


def test_설정_파일_묶음은_겹치지_않는다() -> None:
    groups = groups_from_config(load_trade_items().groups)
    assert all(isinstance(g, Group) for g in groups)
    with pytest.raises(GroupOverlap):
        groups_from_config(
            [ItemGroup(id="a", name="가", hs=("85",)), ItemGroup(id="b", name="나", hs=("8501",))]
        )

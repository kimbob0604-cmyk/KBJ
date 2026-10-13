"""탐색 엔진(metrics §26 — 묶음 EXP): 접두 값(이중 합산 금지·누락 견딤)·YoY·3개월·비중·트리·국가·
품목×국가·지역. 손으로 만든 작은 행 + 합성 세계(p6_world — 고정 시드, 실데이터 아님)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import CountryMonthRow, ItemMonthRow, SigunguMonthRow
from kbj.engines.trade.explore import (
    avg_3m,
    country_lines,
    item_country_lines,
    item_index,
    months_between,
    region_lines,
    share,
    tree_nodes,
    yoy,
    yoy_3m,
)
from tests.fixtures.synthetic.p6_world import P6World, build_world

T = datetime(2026, 12, 11, 11, 30, tzinfo=KST)
D = Decimal


def _it(
    ym: str, hs: str, usd: int | None, kg: int | None = None, q: Quality = Quality.OK
) -> ItemMonthRow:
    return ItemMonthRow(
        ym=ym, hs=hs, hs_len=len(hs),
        exp_usd=None if usd is None else D(usd), exp_kg=None if kg is None else D(kg),
        imp_usd=D(1), imp_kg=D(1), bal_usd=None,
        source="DATAGO:15101609", as_of=T, quality=q if usd is not None else Quality.ESTIMATED,
    )  # fmt: skip


@pytest.fixture(scope="module")
def world() -> P6World:
    return build_world()


def test_상위·하위_함께면_더하지_않는다() -> None:
    rows = [_it("2026-10", "85", 100), _it("2026-10", "8501", 60), _it("2026-10", "8502", 40)]
    idx = item_index(rows, "exp")
    assert idx.usd("85", "2026-10") == D(100)  # 100 + 60 + 40 이 아니다
    assert idx.usd("8501", "2026-10") == D(60)


def test_상위_행이_없으면_하위를_모은다() -> None:
    rows = [
        _it("2026-10", "850110", 30, 3),
        _it("2026-10", "850120", 20, 2),
        _it("2026-10", "8502", 50, 5),
    ]
    idx = item_index(rows, "exp")
    a = idx.value("85", "2026-10")
    assert a is not None and a.usd == D(100) and a.kg == D(10) and a.quality is Quality.OK
    assert idx.usd("8501", "2026-10") == D(50)


def test_하위_누락이면_상위_행이_값() -> None:
    rows = [_it("2026-06", "3901", 100), _it("2026-06", "390110", 70)]  # 390120 누락
    assert item_index(rows, "exp").usd("3901", "2026-06") == D(100)


def test_하위_일부_None_이면_아는_값_합_estimated() -> None:
    rows = [_it("2026-10", "8501", 60), _it("2026-10", "8502", None)]
    a = item_index(rows, "exp").value("85", "2026-10")
    assert a is not None and a.usd == D(60) and a.quality is Quality.ESTIMATED


def test_같은_달_같은_HS_두_번이면_오류() -> None:
    with pytest.raises(ValueError, match="두 번"):
        item_index([_it("2026-10", "85", 1), _it("2026-10", "85", 2)], "exp")


def test_invalid_행은_쓰지_않는다() -> None:
    rows = [_it("2026-10", "85", 100, q=Quality.INVALID), _it("2026-10", "8501", 7)]
    assert item_index(rows, "exp").usd("85", "2026-10") == D(7)


def test_yoy·3개월·평균·비중() -> None:
    assert yoy(D(150), D(100)) == pytest.approx(0.5)
    assert yoy(D(1), D(0)) is None and yoy(None, D(1)) is None and yoy(D(1), None) is None
    vals = {
        "2025-08": D(10),
        "2025-09": D(10),
        "2025-10": D(10),
        "2026-08": D(20),
        "2026-09": D(20),
        "2026-10": D(5),
    }
    assert yoy_3m(vals.get, "2026-10") == pytest.approx(45 / 30 - 1)
    assert avg_3m(vals.get, "2026-10") == D(15)
    assert yoy_3m({**vals, "2025-09": None}.get, "2026-10") is None
    assert avg_3m(vals.get, "2026-07") is None
    assert share(D(1), D(4)) == 0.25 and share(D(1), D(0)) is None and share(None, D(1)) is None
    assert months_between("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert months_between("2026-03", "2026-02") == []


def test_트리_한_층_정렬·비중·기간_YoY() -> None:
    rows = [
        _it("2025-10", "8501", 50, 5), _it("2025-10", "8502", 50, 5),
        _it("2026-10", "8501", 60, 0), _it("2026-10", "8502", 140, 7),
        _it("2026-10", "850110", 60, 0),
    ]  # fmt: skip
    nodes = tree_nodes(
        item_index(rows, "exp"), level=4, parent="85", ym_from="2026-10", ym_to="2026-10"
    )
    assert [n.hs for n in nodes] == ["8502", "8501"]
    a, b = nodes
    assert a.usd == D(140) and a.yoy == pytest.approx(1.8) and a.share == pytest.approx(0.7)
    assert a.unit_price == pytest.approx(20.0) and not a.has_children
    assert b.unit_price is None and b.has_children  # 중량 0 → 단가 None
    # 기간에 빠진 달이 있으면 YoY 없음(estimated)
    two = tree_nodes(
        item_index(rows, "exp"), level=4, parent="85", ym_from="2026-09", ym_to="2026-10"
    )
    assert all(n.yoy is None and n.quality is Quality.ESTIMATED for n in two)
    with pytest.raises(ValueError):
        tree_nodes(
            item_index(rows, "exp"), level=4, parent="8501", ym_from="2026-10", ym_to="2026-10"
        )


def test_세계_HS2_합은_장_행_합(world: P6World) -> None:
    idx = item_index(world.items, "exp")
    nodes = tree_nodes(idx, level=2, parent=None, ym_from="2026-10", ym_to="2026-10")
    assert len(nodes) == 12 and all(n.quality is Quality.OK for n in nodes)
    want = sum((r.exp_usd or D(0)) for r in world.items if r.ym == "2026-10" and r.hs_len == 2)
    assert sum((n.usd or D(0)) for n in nodes) == want
    assert sum(n.share or 0.0 for n in nodes) == pytest.approx(1.0)


def test_세계_심은_급등·누락·중량0(world: P6World) -> None:
    idx = item_index(world.items, "exp")
    assert idx.usd("8502", "2026-10") == D(120_000_000)
    assert idx.usd("8502", "2025-10") == D(60_000_000)
    whole = next(r for r in world.items if r.hs == "3901" and r.ym == "2026-06")
    assert idx.usd("3901", "2026-06") == whole.exp_usd  # 하위 누락 — 상위 행
    z = tree_nodes(idx, level=10, parent="840110", ym_from="2026-10", ym_to="2026-10")
    assert [n.unit_price for n in z if n.hs == "8401100000"] == [None]


def test_국가_표(world: P6World) -> None:
    lines = country_lines(world.countries, "2026-10")
    assert len(lines) == 40
    assert [c.exp_usd for c in lines] == sorted((c.exp_usd for c in lines), reverse=True)  # type: ignore[arg-type]
    assert sum(c.exp_share or 0.0 for c in lines) == pytest.approx(1.0)
    one = lines[0]
    prev = next(r for r in world.countries if r.country == one.country and r.ym == "2025-10")
    assert one.exp_yoy == pytest.approx(yoy(one.exp_usd, prev.exp_usd))
    rows = [
        CountryMonthRow(ym="2026-10", country="US", country_name="가상국-US", exp_cnt=None,
                        exp_usd=D(5), imp_cnt=None, imp_usd=None, bal_usd=None,
                        source="DATAGO:15101612", as_of=T, quality=Quality.OK),
    ]  # fmt: skip
    only = country_lines(rows, "2026-10")[0]
    assert only.exp_yoy is None and only.imp_yoy is None and only.exp_share == 1.0


def test_품목_국가(world: P6World) -> None:
    lines = item_country_lines(world.item_countries, prefix="85", ym="2026-10", flow="exp")
    assert len(lines) == 10
    assert sum(s or 0.0 for _, _, _, s in lines) == pytest.approx(1.0)
    for cc, a, _, _ in lines:
        want = sum(
            (r.exp_usd or D(0)) for r in world.item_countries
            if r.country == cc and r.ym == "2026-10" and r.hs.startswith("85")
        )  # fmt: skip
        assert a.usd == want
    surge = item_country_lines(world.item_countries, prefix="8502", ym="2026-10", flow="exp")
    assert all(y == pytest.approx(1.0) for _, _, y, _ in surge)


def _sg(ym: str, sido: str, name: str, rid: str | None, usd: int) -> SigunguMonthRow:
    return SigunguMonthRow(
        ym=ym, sido_cd=sido, sgg_nm=name, hs6="850110", region_id=rid, exp_usd=D(usd),
        exp_cnt=None, imp_usd=None, imp_cnt=None, bal_usd=None, source="DATAGO:15134343", as_of=T,
        quality=Quality.OK,
    )  # fmt: skip


def test_지역_개편_전후_같은_ID_로_YoY() -> None:
    rows = [
        _sg("2025-10", "46", "가상시A", "KR-46-a01", 100),
        _sg("2026-10", "12", "가상시A", "KR-46-a01", 150),
        _sg("2026-10", "11", "가상시B", None, 50),
        _sg("2025-10", "11", "가상시B", None, 25),
    ]
    lines = region_lines(rows, "2026-10")
    assert [(x.sido_cd, x.exp_yoy) for x in lines] == [
        ("12", pytest.approx(0.5)),
        ("11", pytest.approx(1.0)),
    ]
    assert sum(x.share or 0.0 for x in lines) == pytest.approx(1.0)


def test_세계_지역(world: P6World) -> None:
    lines = region_lines(world.sigungu, "2026-10")
    assert lines and all(x.quality is Quality.OK for x in lines)
    moved = [x for x in lines if x.region_id == "KR-46-a01"]
    assert moved and all(x.sido_cd == "12" and x.exp_yoy is not None for x in moved)

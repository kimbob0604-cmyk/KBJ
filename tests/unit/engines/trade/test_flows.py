"""종목별 무역 흐름(P10 단계 1 — `kbj.engines.trade.flows`): 흐름 묶기(판 2 id·판 1 대체),
가중치 미적용 합, 중량·단가·일평균과 YoY, 시군구 흐름은 중량 없음, 일부 줄 결측은 estimated.
손으로 만든 작은 행."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from kbj.config.trade import parse_trade_map
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import ItemMonthRow, SigunguMonthRow, StockMapRow
from kbj.engines.trade.explore import months_between
from kbj.engines.trade.flows import flow_views, group_flows
from kbj.engines.trade.mapping import map_rows
from kbj.engines.trade.overlay import sources

T = datetime(2026, 10, 21, 21, 0, tzinfo=KST)
D = Decimal
MONTHS = months_between("2025-01", "2026-09")


def _it(ym: str, hs: str, usd: int, kg: int | None) -> ItemMonthRow:
    return ItemMonthRow(
        ym=ym, hs=hs, hs_len=len(hs), exp_usd=D(usd), exp_kg=None if kg is None else D(kg),
        imp_usd=D(0), imp_kg=None, bal_usd=None, source="DATAGO:15101609", as_of=T,
        quality=Quality.OK,
    )  # fmt: skip


def _sg(ym: str, rid: str, hs6: str, usd: int) -> SigunguMonthRow:
    return SigunguMonthRow(
        ym=ym, sido_cd="41", sgg_nm="가상시", hs6=hs6, region_id=rid, exp_usd=D(usd), exp_cnt=1,
        imp_usd=D(0), imp_cnt=0, bal_usd=None, source="DATAGO:15134343", as_of=T,
        quality=Quality.OK,
    )  # fmt: skip


MAP = {
    "version": 2,
    "stocks": [
        {
            "code": "990001", "name": "가상전자", "reviewed": False,
            "flows": [
                {
                    "id": "chip", "name": "가상시 메모리 수출", "kind": "sigungu",
                    "nature": "company", "regions": ["KR-41-vcity", "KR-41-wcity"],
                    "products": [{"hs": "854232", "name": "메모리", "weight": 0.3}],
                    "basis": "가상시·나시 공장(전공정) — 합성",
                },
                {
                    "id": "phone", "name": "휴대폰 전국 수출", "kind": "national",
                    "products": [{"hs": "8517", "weight": 0.1}, {"hs": "8471", "weight": 0.05}],
                },
            ],
            "evidence": [{"kind": "synthetic", "note": "합성"}],
        }
    ],
}  # fmt: skip


def _lines() -> list[StockMapRow]:
    return map_rows(parse_trade_map(MAP), origin="public", version="v2-x", as_of=T)


def test_판2_흐름은_id_와_meta_를_줄에_싣는다() -> None:
    lines = _lines()
    assert {(r.flow_id, r.hs, r.region_id) for r in lines} == {
        ("chip", "854232", "KR-41-vcity"), ("chip", "854232", "KR-41-wcity"),
        ("phone", "8517", ""), ("phone", "8471", ""),
    }  # fmt: skip
    chip = next(r for r in lines if r.flow_id == "chip")
    assert chip.weight == 0.3 and chip.flow_meta["nature"] == "company"
    assert chip.flow_meta["products"] == [{"hs": "854232", "name": "메모리"}]
    assert [g[0] for g in group_flows(lines)] == ["chip", "phone"]


def test_판1_줄은_방향·상대국·시군구로_다시_묶는다() -> None:
    v1 = parse_trade_map({
        "version": 1,
        "stocks": [{
            "code": "990002", "name": "판1", "reviewed": False,
            "items": [{"hs": "8517", "flow": "exp", "weight": 0.2},
                      {"hs": "8471", "flow": "exp", "weight": 0.1, "countries": ["US"]}],
            "regions": [{"region_id": "KR-41-vcity", "hs6": "854232", "weight": 0.1}],
            "evidence": [{"kind": "synthetic", "note": "합성"}],
        }],
    })  # fmt: skip
    rows = map_rows(v1, origin="public", version="v1-x", as_of=T)
    # 해석기가 판 1 을 흐름으로 바꿔 싣는다
    assert {r.flow_id for r in rows} == {"v1_exp_all", "v1_exp_us", "v1_kr_41_vcity"}
    # 옛 적재분(flow_id '')도 같은 규칙으로 묶인다
    bare = [
        StockMapRow(**{**r.__dict__, "flow_id": "", "flow_meta": {}})  # pyright: ignore[reportArgumentType]
        for r in rows
    ]
    assert [g[0] for g in group_flows(bare)] == ["v1_exp_all", "v1_exp_us", "v1_kr_41_vcity"]


def test_금액·중량·단가·일평균과_YoY() -> None:
    items = [
        _it("2025-09", "8517", 1000, 10), _it("2025-09", "8471", 500, 5),
        _it("2026-09", "8517", 1500, 10), _it("2026-09", "8471", 600, 4),
    ]  # fmt: skip
    sg = [
        _sg("2025-09", "KR-41-vcity", "854232", 300), _sg("2025-09", "KR-41-wcity", "854232", 100),
        _sg("2026-09", "KR-41-vcity", "854232", 500),  # 나시 결측
    ]  # fmt: skip
    src = sources(items, [], sg, months=MONTHS)
    days = {"2025-09": 20, "2026-09": 21}
    views = {v.flow_id: v for v in flow_views(_lines(), src, days_of=days)}
    phone = views["phone"]
    p = phone.points[-1]
    assert phone.has_kg and phone.last_ym == "2026-09"
    assert (p.usd, p.kg) == (D(2100), D(14))  # 가중치를 곱하지 않은 통관 합
    assert p.usd_yoy == pytest.approx(2100 / 1500 - 1)
    assert p.unit_price == pytest.approx(150.0) and p.price_yoy == pytest.approx(150 / 100 - 1)
    assert p.daily_usd == D(100) and p.daily_yoy == pytest.approx(100 / 75 - 1)
    assert p.products == {"8471": D(600), "8517": D(1500)} and p.quality is Quality.OK
    chip = views["chip"]
    c = chip.points[-1]
    assert not chip.has_kg and c.kg is None and c.unit_price is None  # 시군구 표는 중량 없음
    assert c.usd == D(500) and c.quality is Quality.ESTIMATED  # 한 시군구 결측 — 있는 줄의 합
    assert chip.points[-13].usd == D(400)
    empty = next(x for x in phone.points if x.ym == "2026-01")
    assert empty.usd is None and empty.usd_yoy is None


def test_시군구_흐름은_수출·HS6_만() -> None:
    bad = {**MAP, "stocks": [{**MAP["stocks"][0], "flows": [{  # type: ignore[index]
        "id": "x", "name": "x", "kind": "sigungu", "regions": ["KR-41-vcity"],
        "products": [{"hs": "8542", "weight": 0.1}],
    }]}]}  # fmt: skip
    with pytest.raises(Exception, match="HS6"):
        parse_trade_map(bad)

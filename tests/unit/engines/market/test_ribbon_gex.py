"""상단 띠 'GEX Flip 거리' 칩(P7 OUT — metrics §6.5 → §G3.5, docs/p7_design.md §1.6).

값 = (현재가 − Flip) ÷ 현재가 × 100(엔진 flip_distance 행 그대로), visible·invalid 아님일 때만.
섀도·행 없음은 '준비 중(P7)' 그대로, Flip 없음은 값 없이 사유, 장 중 지연은 stale. 합성 입력.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.engines.market.ribbon import (
    CHIP_ORDER,
    GEX_STALE_AFTER,
    RibbonInputs,
    gex_flip_chip,
    ribbon,
)
from tests.unit.api.api_world import World, build_world
from tests.unit.reports.gex.gex_world import at, levels

DAY = date(2026, 12, 10)
NOW = at(DAY, 14, 0)


def test_값은_엔진_flip_distance_그대로_부호와_원천() -> None:
    rows = levels(NOW - timedelta(seconds=30), f=351.2, flip=350.0)
    c = gex_flip_chip(rows, NOW, live=True)
    assert c.phase_pending is None and c.tier == "login"
    assert c.value is not None
    assert c.value.value == round((351.2 - 350.0) / 351.2 * 100, 4)
    assert c.value.source == "KBJ:gex.engine" and c.value.quality is Quality.OK
    assert c.value.as_of == NOW - timedelta(seconds=30)
    assert c.tags == ("주간",) and c.note == "Flip 350.00 · F 351.20"
    assert c.detail["flip"] == 350.0 and c.detail["price"] == 351.2
    below = gex_flip_chip(levels(NOW, f=349.0, flip=350.0), NOW, live=True)
    assert below.value is not None and below.value.value < 0


def test_거리_행이_없으면_같은_식으로_계산() -> None:
    rows = levels(NOW, f=400.0, flip=396.0, drop=("flip_distance",))
    c = gex_flip_chip(rows, NOW, live=True)
    assert c.value is not None and c.value.value == (400.0 - 396.0) / 400.0 * 100


def test_섀도_꺼짐_행없음_invalid_는_준비_중_그대로() -> None:
    for rows in (
        None,
        [],
        levels(NOW, flags={"flip": "shadow"}),
        levels(NOW, flags={"flip": "off"}),
        levels(NOW, quality="invalid"),
    ):
        c = gex_flip_chip(rows, NOW, live=True)
        assert c.value is None and c.phase_pending == "P7" and c.note is None


def test_Flip_없음은_값_없이_사유() -> None:
    c = gex_flip_chip(levels(NOW, flip=None), NOW, live=True)
    assert c.value is None and c.phase_pending is None
    assert c.note is not None and "Flip 없음" in c.note


def test_장중_지연은_stale_장_밖은_마지막_값_그대로() -> None:
    old = NOW - GEX_STALE_AFTER - timedelta(seconds=1)
    live = gex_flip_chip(levels(old), NOW, live=True)
    assert live.value is not None and live.value.quality is Quality.STALE
    closed = gex_flip_chip(levels(old), NOW, live=False)
    assert closed.value is not None and closed.value.quality is Quality.OK
    est = gex_flip_chip(levels(NOW, quality="estimated"), NOW, live=True)
    assert est.value is not None and est.value.quality is Quality.ESTIMATED


def test_띠_자리와_순서는_그대로() -> None:
    chips = ribbon(
        RibbonInputs(gex_levels=levels(NOW), gex_live=True), NOW, TradingCalendar.default()
    )
    assert [c.key for c in chips] == list(CHIP_ORDER)
    g = chips[CHIP_ORDER.index("gex_flip")]
    assert g.value is not None and g.phase_pending is None
    # 입력을 주지 않으면(읽지 않음) P3 이후와 같은 '준비 중' 칩
    plain = ribbon(RibbonInputs(), NOW, TradingCalendar.default())
    assert plain[CHIP_ORDER.index("gex_flip")].phase_pending == "P7"


@pytest.fixture
def world() -> World:
    return build_world()


def test_API_띠가_저장소의_최신_사이클을_싣는다(world: World) -> None:
    authed = world.client()
    world.login(authed)
    now = world.clock()
    world.repos.gex.write_levels(levels(now - timedelta(minutes=10), f=351.2, flip=352.0))
    world.repos.gex.write_levels(levels(now - timedelta(seconds=20), f=351.2, flip=350.0))
    world.repos.gex.write_levels(
        levels(now + timedelta(minutes=5), f=1.0, flip=2.0)
    )  # 미래 — 안 씀
    chips = {c["key"]: c for c in authed.get("/api/market/ribbon").json()["data"]["chips"]}
    g = chips["gex_flip"]
    assert g["phase_pending"] is None and g["tier"] == "login"
    assert g["value"]["value"] == round((351.2 - 350.0) / 351.2 * 100, 4)
    assert g["value"]["source"] == "KBJ:gex.engine"

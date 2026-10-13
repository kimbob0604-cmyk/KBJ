"""가짜 관세청(tests/fakes/customs_world.py — P6 COL): 파라미터·공표 시각·정정·주입이 실제 어댑터
(`CustomsClient`)를 거쳐 기대대로 나오는지. 값은 전부 합성(p6_world)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr

from kbj.core.time import KST
from kbj.data.budget import DailyBudget
from kbj.data.datago import DatagoTransport
from kbj.data.public.customs.client import DATASET_IDS, CustomsClient, CustomsTruncated
from kbj.data.public.customs.models import TenDayColumnsChanged
from tests.fakes.clock import FakeClock
from tests.fakes.customs_world import CustomsWorld
from tests.fakes.datago_server import KEY, FakeDatago
from tests.fixtures.synthetic.p6_world import build_world

W = build_world()


class _Rig:
    def __init__(self, at: datetime) -> None:
        self.clock = FakeClock(at)
        self.world = CustomsWorld(W)
        self.fake = FakeDatago(self.clock, customs=self.world)
        t = DatagoTransport(
            SecretStr(KEY),
            limiters={d: _NoLimit() for d in DATASET_IDS},
            budgets={d: DailyBudget(None, "datago", 9500, scope=d) for d in DATASET_IDS},
            transport=self.fake.transport("test"),
            retry_waits_s=(),
            now=self.clock,
            sleep=lambda _: None,
            monotonic=self.clock.monotonic,
        )
        self.client = CustomsClient(t)


class _NoLimit:
    def acquire(self, priority: object, tr_id: str, timeout: float | None = None) -> None:
        return None

    def on_rate_limited(self) -> float:
        return 0.0


@pytest.fixture
def rig() -> Iterator[_Rig]:
    yield _Rig(datetime(2026, 11, 16, 11, 0, tzinfo=KST))


def test_ten_day_segment_opens_at_publish_time(rig: _Rig) -> None:
    rig.clock.set(datetime(2026, 11, 11, 10, 40, tzinfo=KST))
    before = rig.client.ten_day("exp_item", "202610", "202611")
    assert {(r.month, r.segment) for r in before} == {("202610", 1), ("202610", 2), ("202610", 3)}
    rig.clock.set(datetime(2026, 11, 11, 10, 47, tzinfo=KST))
    after = rig.client.ten_day("exp_item", "202610", "202611")
    assert ("202611", 1) in {(r.month, r.segment) for r in after}
    want = {
        r.col_no: r.usd for r in W.ten_day if (r.kind, r.ym, r.seg) == ("exp_item", "2026-11", 1)
    }
    got = next(r for r in after if (r.month, r.segment) == ("202611", 1))
    assert {int(k): v * 1000 for k, v in got.amounts.items() if v is not None} == want


def test_delay_and_weekend_publish(rig: _Rig) -> None:
    rig.world.delay_ten("2026-11", 1, datetime(2026, 11, 12, 9, 20, tzinfo=KST))
    rig.clock.set(datetime(2026, 11, 11, 17, 40, tzinfo=KST))
    assert all(r.month != "202611" for r in rig.client.ten_day("exp_item", "202611", "202611"))
    rig.clock.set(datetime(2026, 11, 12, 9, 20, tzinfo=KST))
    assert rig.client.ten_day("exp_item", "202611", "202611")[0].segment == 1
    rig.clock.set(datetime(2026, 11, 21, 11, 0, tzinfo=KST))  # 토요일 공표
    assert {r.segment for r in rig.client.ten_day("imp_country", "202611", "202611")} == {1, 2}


def test_revision_serves_first_value_before_revised_at(rig: _Rig) -> None:
    kind, ym, seg, col = "exp_item", "2026-11", 3, 0
    cell = next(r for r in W.ten_day if (r.kind, r.ym, r.seg, r.col_no) == (kind, ym, seg, col))
    assert cell.revised_at is not None and cell.first_usd != cell.usd
    rig.clock.set(datetime(2026, 12, 1, 11, 0, tzinfo=KST))
    row = next(r for r in rig.client.ten_day(kind, "202611", "202611") if r.segment == 3)
    assert row.amounts["00"] == Decimal(cell.first_usd or 0) / 1000
    rig.clock.set(cell.revised_at)
    row = next(r for r in rig.client.ten_day(kind, "202611", "202611") if r.segment == 3)
    assert row.amounts["00"] == Decimal(cell.usd or 0) / 1000


def test_column_change_and_truncation_injection(rig: _Rig) -> None:
    rig.world.change_columns("exp_country")
    with pytest.raises(TenDayColumnsChanged):
        rig.client.ten_day("exp_country", "202610", "202610")
    assert rig.client.ten_day("exp_item", "202610", "202610")  # 다른 종류는 그대로
    rig.world.change_columns("exp_country", on=False)
    assert rig.client.ten_day("exp_country", "202610", "202610")
    rig.world.truncate("getItemtradeList")
    with pytest.raises(CustomsTruncated):
        rig.client.items("202610", "202610", hs="85")
    assert rig.client.items("202610", "202610", hs="85")  # 한 번만


def test_monthly_items_follow_params_and_restore_leading_zero(rig: _Rig) -> None:
    rows = rig.client.items("202610", "202610", hs="03")
    want = {r.hs for r in W.items if r.ym == "2026-10" and r.hs.startswith("03")}
    assert {r.hs for r in rows} == want and "03" in want  # 숫자형 `3` → `03`
    sent = rig.fake.calls("getItemtradeList")[-1].params
    assert sent == {"strtYymm": "202610", "endYymm": "202610", "hsSgn": "03"}
    one = next(r for r in rows if r.hs == "0301")
    src = next(r for r in W.items if (r.ym, r.hs) == ("2026-10", "0301"))
    assert (one.exp_usd, one.exp_weight_kg, one.imp_usd) == (src.exp_usd, src.exp_kg, src.imp_usd)
    assert one.item_name == "가상품목 0301" and one.period == "202610"


def test_monthly_publish_gate_and_nodata(rig: _Rig) -> None:
    rig.clock.set(datetime(2026, 11, 15, 9, 59, tzinfo=KST))
    assert rig.client.countries("202610", "202610") == []  # 03 데이터 없음 → 빈 목록
    rig.clock.set(datetime(2026, 11, 15, 10, 0, tzinfo=KST))
    assert len(rig.client.countries("202610", "202610")) == 40
    assert rig.client.items("202611", "202611") == []  # 세계 밖


def test_item_country_requires_country_and_twelve_months(rig: _Rig) -> None:
    rows = rig.client.item_country("202610", "202610", W.countries[0].country)
    assert len(rows) == 24 and {r.hs for r in rows} == {
        r.hs for r in W.item_countries if r.ym == "2026-10"
    }
    body = rig.world.respond("getNitemtradeList", {"strtYymm": "202501", "endYymm": "202610",
                                                    "cntyCd": "US"}, rig.clock())  # fmt: skip
    assert "<resultCode>99</resultCode>" in body
    nocc = rig.world.respond("getNitemtradeList", {"strtYymm": "202610", "endYymm": "202610"},
                             rig.clock())  # fmt: skip
    assert "<resultCode>99</resultCode>" in nocc


def test_sigungu_filters_sido_and_hs6_with_comma_strings(rig: _Rig) -> None:
    hs6 = sorted({r.hs6 for r in W.sigungu})[0]
    rig.clock.set(datetime(2026, 8, 16, 11, 0, tzinfo=KST))
    rows = rig.client.sigungu("202607", "202607", "12", hs6)
    want = [r for r in W.sigungu if (r.ym, r.sido_cd, r.hs6) == ("2026-07", "12", hs6)]
    assert len(rows) == len(want) and {r.sigungu_name for r in rows} == {r.sgg_nm for r in want}
    assert {r.exp_usd for r in rows} == {r.exp_usd for r in want}
    assert rig.client.sigungu("202606", "202606", "46", hs6)  # 개편 전 전남
    assert "," in rig.world.respond(
        "getSigunguPerPrlstPerAcrs",
        {"strtYymm": "202607", "endYymm": "202607", "sidoCd": "12", "HsSgn": hs6},
        rig.clock(),
    )


def test_collector_config_helpers() -> None:
    cw = CustomsWorld(W)
    m = cw.trade_map()
    hs6 = {rg.hs6 for s in m.stocks for rg in s.regions}
    assert hs6 == {r.hs for r in W.stock_map if r.region_id}
    pub = cw.trade_map(("public",))
    assert len(pub.stocks) <= len(m.stocks)
    assert cw.trade_regions().ids() == {g.id for g in W.regions}


def test_without_customs_world_the_old_fixture_is_served() -> None:
    clock = FakeClock(datetime(2026, 11, 16, 11, 0, tzinfo=KST))
    fake = FakeDatago(clock)
    status, body, kind = fake._body("getItemtradeList", {})  # pyright: ignore[reportPrivateUsage]
    assert status == 200 and kind == "xml" and "make_fixtures.py" in body

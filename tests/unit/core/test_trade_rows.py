"""`kbj.core.trade_rows` — P6 행 자료형 검증(docs/p6_design.md §1.1, metrics §25~§29)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from kbj.core import trade_rows as tr
from kbj.core.quality import Quality

NOW = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)


def ten_day(**kw: Any) -> tr.TenDayRow:
    base: dict[str, Any] = dict(
        kind="exp_item", ym="2026-10", seg=1, col_no=0, name="전체", usd=Decimal(1000),
        first_usd=Decimal(1000), first_seen_at=NOW, span_text="01~10",
        source="DATAGO:15157908", as_of=NOW, quality=Quality.ESTIMATED,
    )  # fmt: skip
    base.update(kw)
    return tr.TenDayRow(**base)


def item(**kw: Any) -> tr.ItemMonthRow:
    base: dict[str, Any] = dict(
        ym="2026-09", hs="854232", hs_len=6, exp_usd=Decimal(5), exp_kg=Decimal("1.5"),
        imp_usd=Decimal(3), imp_kg=None, bal_usd=Decimal(2), source="DATAGO:15101609",
        as_of=NOW, quality=Quality.OK,
    )  # fmt: skip
    base.update(kw)
    return tr.ItemMonthRow(**base)


def test_ym_helpers() -> None:
    assert tr.ym_ok("2026-01") and not tr.ym_ok("202601") and not tr.ym_ok("2026-13")
    assert tr.prev_ym("2026-01") == "2025-12"
    assert tr.prev_ym("2026-03", 12) == "2025-03"
    assert tr.prev_ym("2025-12", -1) == "2026-01"
    assert tr.hs_ok("85") and tr.hs_ok("8542320000") and not tr.hs_ok("854") and not tr.hs_ok("")
    with pytest.raises(ValueError):
        tr.prev_ym("2026-1")


def test_ten_day_row_ok_and_data_key() -> None:
    r = ten_day()
    assert r.quality is Quality.ESTIMATED
    assert r.data_key == "202610-1"
    assert ten_day(seg=3, span_text="01~31").data_key == "202610-3"


@pytest.mark.parametrize(
    "kw",
    [
        {"kind": "exp"},
        {"seg": 4},
        {"col_no": 11},
        {"span_text": "01~20"},  # seg 1 인데 20일
        {"usd": 1000.0},  # float 거부
        {"usd": Decimal("1.5")},  # 달러 정수
        {"source": "KIS"},
        {"usd": None, "quality": "ok"},  # 값 없으면 ok 아님
        {"first_seen_at": datetime(2026, 10, 13)},  # noqa: DTZ001 — naive
        {"quality": "good"},
    ],
)
def test_ten_day_row_rejects(kw: dict[str, Any]) -> None:
    with pytest.raises((ValueError, TypeError)):
        ten_day(**kw)


def test_ten_day_none_value_is_allowed_when_not_ok() -> None:
    assert ten_day(usd=None, quality="invalid").usd is None


def test_monthly_rows() -> None:
    assert item().quality is Quality.OK
    with pytest.raises(ValueError):
        item(hs_len=4)
    with pytest.raises(ValueError):
        item(hs="854")
    with pytest.raises(ValueError):
        item(exp_usd=None, imp_usd=None)  # quality ok
    assert item(exp_usd=None, imp_usd=None, quality="invalid").exp_usd is None
    assert item(bal_usd=Decimal(-7)).bal_usd == Decimal(-7)  # 수지는 음수 가능
    c = tr.CountryMonthRow(
        ym="2026-09", country="US", country_name="미국", exp_cnt=3, exp_usd=Decimal(1),
        imp_cnt=None, imp_usd=None, bal_usd=None, source="DATAGO:15101612", as_of=NOW,
        quality=Quality.OK,
    )  # fmt: skip
    assert c.country == "US"
    with pytest.raises(ValueError):
        tr.CountryMonthRow(**{**c.__dict__, "country": "usa"})
    # 관세청 특수 코드에 숫자가 섞인다(실측 2026-10-20 'Z1' — 9월 월간 국가 저장이 통째로 실패했다)
    assert tr.CountryMonthRow(**{**c.__dict__, "country": "Z1"}).country == "Z1"
    s = tr.SigunguMonthRow(
        ym="2026-09", sido_cd="41", sgg_nm="가상시", hs6="854232", region_id="KR-41-a01",
        exp_usd=Decimal(1), exp_cnt=1, imp_usd=None, imp_cnt=None, bal_usd=None,
        source="DATAGO:15134343", as_of=NOW, quality=Quality.OK,
    )  # fmt: skip
    assert s.region_id == "KR-41-a01"
    with pytest.raises(ValueError):
        tr.SigunguMonthRow(**{**s.__dict__, "hs6": "8542"})
    h = tr.HsDim(
        hs="85", hs_len=2, name_ko="가상 장", first_ym="2021-10", last_ym="2026-09",
        source="DATAGO:15101609", as_of=NOW, quality=Quality.OK,
    )  # fmt: skip
    with pytest.raises(ValueError):
        tr.HsDim(**{**h.__dict__, "first_ym": "2027-01"})


def test_derived_rows() -> None:
    s = tr.SurgeRow(
        ym="2026-09", level="hs4", key="8542", flow="exp", rule="surge", yoy=0.6, yoy_3m=0.4,
        usd=Decimal(20_000_000), rank=1, source="KBJ:trade.derive", as_of=NOW, quality=Quality.OK,
    )  # fmt: skip
    assert s.rank == 1
    for bad in ({"key": "854232"}, {"level": "country", "key": "8542"}, {"rank": 0}):
        with pytest.raises(ValueError):
            tr.SurgeRow(**{**s.__dict__, **bad})
    assert tr.SurgeRow(**{**s.__dict__, "level": "country", "key": "VN"}).key == "VN"
    g = tr.ItemGroupMonthRow(
        ym="2026-09", group_id="g001", exp_usd=Decimal(1), exp_kg=Decimal(2), imp_usd=None,
        imp_kg=None, yoy=None, unit_price=0.5, mode="fixed", source="KBJ:trade.derive",
        as_of=NOW, quality=Quality.OK,
    )  # fmt: skip
    with pytest.raises(ValueError):
        tr.ItemGroupMonthRow(**{**g.__dict__, "mode": "auto"})


def test_stock_map_row_rules() -> None:
    m = tr.StockMapRow(
        code="000000", hs="854232", flow="exp", region_id="", name="예시전자", weight=0.55,
        origin="public", reviewed=False, map_version="v1", source="KBJ:trade_map", as_of=NOW,
        quality=Quality.OK, countries=["CN", "US"],  # type: ignore[arg-type]
        evidence=[{"kind": "dart_business_report", "rcept_no": "x"}],  # type: ignore[arg-type]
    )  # fmt: skip
    assert m.countries == ("CN", "US") and isinstance(m.evidence, tuple)
    for bad in (
        {"weight": 1.2},
        {"code": "12345"},
        {"countries": ("CN", "CN")},
        {"region_id": "KR-41-a01", "hs": "8542"},  # 지역 행은 HS6
        {"region_id": "KR-41-a01", "flow": "imp"},
        {"origin": "watchlist"},
        {"hs": "8"},
    ):
        with pytest.raises((ValueError, TypeError)):
            tr.StockMapRow(**{**m.__dict__, **bad})
    assert tr.StockMapRow(**{**m.__dict__, "region_id": "KR-41-a01"}).region_id == "KR-41-a01"


def test_stock_export_row() -> None:
    e = tr.StockExportRow(
        code="000000", period="2026Q3", export_usd=Decimal(10), yoy=0.1, revenue_yoy=0.05,
        status="ok", map_version="v1", source="KBJ:trade.stock_index", as_of=NOW,
        quality=Quality.OK,
        lag_corr={"0": 0.5, "1": None},
    )  # fmt: skip
    assert e.is_quarter and e.lag_corr == {"0": 0.5, "1": None}
    assert not tr.StockExportRow(**{**e.__dict__, "period": "2026-09"}).is_quarter
    for bad in ({"period": "2026-Q3"}, {"status": "bad"}, {"lag_corr": {"3": 0.1}},
                {"lag_corr": {"0": 1.5}}):  # fmt: skip
        with pytest.raises((ValueError, TypeError)):
            tr.StockExportRow(**{**e.__dict__, **bad})


def test_enums_match_adapter_kinds() -> None:
    """core 의 10일 종류 = 어댑터(kbj.data.public.customs.models)의 종류 — 같은 말."""
    from kbj.data.public.customs.models import TEN_DAY_COLUMN_COUNT, TEN_DAY_KINDS

    assert tuple(tr.TEN_DAY_KINDS) == tuple(TEN_DAY_KINDS)
    assert tr.TEN_DAY_COLUMN_COUNT == TEN_DAY_COLUMN_COUNT
    assert len(tr.TRADE_TABLES) == 11

"""P5 행 자료형(kbj/core/p5_rows.py — docs/p5_design.md §1.1): 불변·검증·직렬화·quality 값.

마이그레이션 CHECK 와 같은 말을 쓰는지는 tests/test_store_layout.py:test_p5_enum_checks_match_rows.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import pytest

from kbj.core import p5_rows as p5
from kbj.core.quality import Quality

NOW = datetime(2026, 11, 13, 7, 0, tzinfo=UTC)
D = date(2026, 11, 13)


def _series(**kw: Any) -> p5.MacroSeriesRow:
    base: dict[str, Any] = {
        "series_id": "FRED:DGS10", "period": "2026-11-13", "value": Decimal("4.12"), "unit": "%",
        "source": "FRED", "as_of": NOW, "quality": "ok",
    }  # fmt: skip
    return p5.MacroSeriesRow(**{**base, **kw})


def test_rows_are_frozen_and_quality_is_coerced() -> None:
    r = _series()
    assert r.quality is Quality.OK
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.value = Decimal(0)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kw", "err"),
    [
        ({"value": 4.12}, TypeError),  # float 는 받지 않는다(공표 자릿수)
        ({"value": Decimal("NaN"), "quality": "invalid"}, ValueError),
        ({"value": None}, ValueError),  # 값이 없으면 ok 일 수 없다(0 으로 채우지 않는다)
        ({"period": "2026/11"}, ValueError),
        ({"as_of": datetime(2026, 11, 13)}, ValueError),  # noqa: DTZ001 — naive 거부
        ({"quality": "good"}, ValueError),
    ],
)
def test_macro_series_rejects_bad_values(kw: dict[str, Any], err: type[Exception]) -> None:
    with pytest.raises(err):
        _series(**kw)


def test_missing_value_is_none_not_zero() -> None:
    r = _series(value=None, quality="estimated")
    assert r.value is None and r.quality is Quality.ESTIMATED
    for q in ("2026", "2026-Q3", "2026-11", "2026-11-13"):
        assert _series(period=q).period == q


def test_public_meta_rules() -> None:
    ok = p5.MacroSeriesMeta(
        "FRED:DGS10", "us", "10Y", "%", "D", "public", "FRED", "FRED:gov_series"
    )
    assert ok.tier == "public"
    with pytest.raises(ValueError, match="저작권"):
        p5.MacroSeriesMeta("FRED:SP500", "us", "S&P", "pt", "D", "public", "FRED",
                           "FRED:restricted_series")  # fmt: skip
    with pytest.raises(ValueError):
        p5.MacroSeriesMeta("YAHOO:CL=F", "market", "WTI", "USD", "D", "public", "YAHOO",
                           "YAHOO:macro")  # fmt: skip
    assert p5.MacroSeriesMeta("YAHOO:^VIX", "market", "VIX", "pt", "D", "private", "YAHOO",
                              "YAHOO:macro").tier == "private"  # fmt: skip


def test_calendar_event_id_and_public_flag() -> None:
    eid = p5.event_id("KR", "meeting", D, "bok_mpc")
    assert eid == "KR:meeting:2026-11-13:bok_mpc"
    e = p5.CalendarEvent(eid, "meeting", D, time(10, 0), "금통위", "KR", 1, "bok", Quality.OK)
    assert e.is_public
    ff = dataclasses.replace(e, source="ff", forecast=Decimal("3.1"))
    assert not ff.is_public
    with pytest.raises(ValueError, match="event_id"):
        dataclasses.replace(e, country="US")
    with pytest.raises(ValueError):
        dataclasses.replace(e, importance=4)


def test_investor_minute_units_and_slot() -> None:
    ts = datetime(2026, 11, 13, 0, 1, tzinfo=UTC)
    m = p5.InvestorMinute(
        ts, D, "day", "K2I", "F001", "frgn", 1, 2, 1, 10, 20, 10, Quality.ESTIMATED
    )
    assert m.source == "KIS" and m.net_value == 10  # 백만원 정수(GX 단위)
    with pytest.raises(ValueError, match="분 경계"):
        dataclasses.replace(m, ts=ts.replace(second=30))
    with pytest.raises(ValueError):
        dataclasses.replace(m, investor="foreign")
    with pytest.raises(TypeError):
        dataclasses.replace(m, net_value=1.5)
    with pytest.raises(ValueError):
        dataclasses.replace(m, sell_value=-1)
    assert [c for c, _ in p5.MINUTE_CARDS] == [
        "prsn", "frgn", "orgn", "scrt", "ivtr", "fund", "etc_corp",
    ]  # fmt: skip
    assert set(dict(p5.MINUTE_CARDS)) <= set(p5.MINUTE_INVESTORS)
    assert "pe_fund" not in dict(p5.MINUTE_CARDS)  # 투신 카드는 사모 제외(§10 Q7 기본값)


def test_program_rows() -> None:
    r = p5.ProgramMarketRow(D, "K", "KRX", "whol", "smtn", 1, 2, 1, 1_000_000, 2_000_000,
                            1_000_000, 2500.0, "KIS", Quality.OK)  # fmt: skip
    assert r.ts is None
    with pytest.raises(ValueError):
        dataclasses.replace(r, kind="total")
    with pytest.raises(ValueError):
        dataclasses.replace(r, venue="")
    s = p5.ProgramStockRow(D, "995010", "Q", "KRX", "scan", 1000.0, -1.0, 1, 1, 0, 1, 1, 0,
                           "KIS", Quality.ESTIMATED, ts=NOW)  # fmt: skip
    with pytest.raises(ValueError):
        dataclasses.replace(s, scope="all")


def test_brief_section_rules() -> None:
    ok = p5.BriefSection(
        "morning", "us", "간밤 미국", ("S&P +1.0%",), "ok", "nasdaq", NOW, Quality.OK
    )
    assert ok.lines == ("S&P +1.0%",)
    nr = p5.BriefSection("morning", "us", "간밤 미국", ("미준비 — us.eod(06:10)",), "not_ready",
                         None, None, None)  # fmt: skip
    assert nr.quality is None
    with pytest.raises(ValueError, match="절대 규칙 1"):
        p5.BriefSection("closing", "board", "신고가", ("x",), "ok", None, None, None)
    with pytest.raises(ValueError, match="omitted"):
        p5.BriefSection("closing", "gex", "GEX", ("x",), "omitted", None, None, None)


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime | date | time):
        return v.isoformat()
    if isinstance(v, Quality):
        return v.value
    if isinstance(v, tuple):
        return [_plain(x) for x in v]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}  # pyright: ignore[reportUnknownVariableType]
    return v


def test_rows_serialize_to_json() -> None:
    rows: list[Any] = [
        _series(),
        p5.MarketStatRow("credit_balance", D, "credit_total", None, "원", "DATAGO", NOW,
                         Quality.STALE),
        p5.UsUniverseRow("BRK.B", D, "Berkshire", "NYSE", None, None, 1e12, False, "nasdaq",
                         Quality.OK),
        p5.UsArtifact(D, "us_brief", {"lines": ["a"]}, "v1", "d", NOW, NOW, "nasdaq", Quality.OK),
        p5.EtfMapCell("995900", "A", "theme", "ai", 10**12, 1.5, "1M", False, False, D, "krx",
                      Quality.OK),
        p5.EtfHolder("F1", "995900", "A", True, D, "995010", "x", 5.0, 1.0, 1.0, "x", Quality.OK),
    ]  # fmt: skip
    for r in rows:
        text = json.dumps({k: _plain(v) for k, v in dataclasses.asdict(r).items()})
        assert "NaN" not in text
    with pytest.raises(ValueError):
        p5.MarketStatRow("credit_balance", D, "x", Decimal(1), "원", "KIS", NOW, Quality.OK)
    with pytest.raises(ValueError):
        p5.UsArtifact(D, "newhigh", {}, "v", "d", NOW, NOW, "s", Quality.OK)  # type: ignore[arg-type]

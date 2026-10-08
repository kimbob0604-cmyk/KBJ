"""업종 히트맵 — KRX 업종지수 셀(docs/metrics.md §6.3)."""

from __future__ import annotations

from datetime import datetime

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import IndexBar, SectorQuote
from kbj.core.time import KST
from kbj.engines.market.sectors import index_return_pct, sector_heat
from tests.fixtures.synthetic.ledger_gen import SECTOR_CODES, generate
from tests.unit.engines.flows.flow_rows import days

D = days(25)


def _bars(code: str, closes: list[float]) -> list[IndexBar]:
    return [
        IndexBar(code, d, f"업종{code}", c, c, c, c, None, 1000 + i, "krx", Quality.OK)
        for i, (d, c) in enumerate(zip(D, closes, strict=False))
    ]


def test_코드가_없으면_빈_목록_준비_중() -> None:
    assert sector_heat({}, [], [], 1) == []


def test_마감_뒤_기간_등락률() -> None:
    closes = [100.0 + i for i in range(21)]  # D[0]..D[20]
    bars = {"S0001": _bars("S0001", closes)}
    one = sector_heat(bars, [], ["S0001"], 1)[0]
    assert one.chg_pct == pytest.approx((120 / 119 - 1) * 100)
    five = sector_heat(bars, [], ["S0001"], 5)[0]
    assert five.chg_pct == pytest.approx((120 / 115 - 1) * 100)
    twenty = sector_heat(bars, [], ["S0001"], 20)[0]
    assert twenty.chg_pct == pytest.approx(20.0)
    assert one.quality is Quality.OK and one.turnover == 1020 and one.source == "KRX"
    assert one.as_of is not None and one.as_of.hour == 15


def test_기간이_모자라면_invalid_셀() -> None:
    bars = {"S0001": _bars("S0001", [100.0, 101.0])}
    cell = sector_heat(bars, [], ["S0001", "S0002"], 5)
    assert [c.quality for c in cell] == [Quality.INVALID, Quality.INVALID]
    assert cell[0].chg_pct is None and cell[0].note == "5일 전 종가 없음"
    assert cell[1].note == "업종지수 자료 없음"


def test_장중은_마지막_시세_estimated() -> None:
    bars = {"S0001": _bars("S0001", [100.0] * 10)}  # D[0]..D[9]
    ts1 = datetime.combine(D[10], datetime.min.time(), tzinfo=KST).replace(hour=10)
    ts2 = ts1.replace(hour=11)
    quotes = [
        SectorQuote("KOSPI", "S0001", ts1, "업종", 101.0, 1.0, 5, "kis", Quality.ESTIMATED),
        SectorQuote("KOSPI", "S0001", ts2, "업종", 110.0, 10.0, 9, "kis", Quality.ESTIMATED),
    ]
    one = sector_heat(bars, quotes, ["S0001"], 1)[0]
    assert (one.chg_pct, one.turnover, one.quality, one.market) == (
        10.0, 9, Quality.ESTIMATED, "KOSPI"
    )  # fmt: skip
    assert one.as_of is not None and one.as_of.hour == 11 and one.as_of.minute == 10
    five = sector_heat(bars, quotes, ["S0001"], 5)[0]
    assert five.chg_pct == pytest.approx(10.0)


def test_합성_업종지수() -> None:
    m = generate(4)
    series = m.index_series()
    cells = sector_heat(series, [], list(SECTOR_CODES), 20)
    assert len(cells) == 4 and all(c.chg_pct is not None for c in cells)
    for c in cells:
        assert c.chg_pct == pytest.approx(index_return_pct(series[c.code], m.last_day, 20))


def test_index_return_pct_와_기간_검증() -> None:
    bars = _bars("S0001", [100.0, 110.0])
    assert index_return_pct(bars, D[1], 1) == pytest.approx(10.0)
    assert index_return_pct(bars, D[2], 1) is None  # 그날 봉 없음
    assert index_return_pct(bars, D[1], 2) is None
    with pytest.raises(ValueError):
        sector_heat({}, [], ["S0001"], 3)

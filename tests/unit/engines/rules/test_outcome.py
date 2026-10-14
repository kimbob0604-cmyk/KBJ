"""알림 후 성과(metrics §30.4) — 손계산."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import Bar, IndexBar
from kbj.engines.rules.outcome import OutcomeCalc, signal_outcomes
from tests.unit.engines.rules.rule_rows import CODE, days

DS = days(8)


def _bar(d: date, o: float, h: float, lo: float, c: float, q: Quality = Quality.OK) -> Bar:
    return Bar(CODE, d, o, h, lo, c, 1000, None, "krx", "KRX", q)


def _ix(d: date, c: float) -> IndexBar:
    return IndexBar("1001", d, None, c, c, c, c, None, None, "krx", Quality.OK)


BARS = {
    DS[0]: _bar(DS[0], 100, 101, 99, 100),
    DS[1]: _bar(DS[1], 101, 106, 100, 104),
    DS[2]: _bar(DS[2], 104, 105, 95, 96),
    DS[3]: _bar(DS[3], 96, 99, 94, 98, Quality.STALE),
}
IDX = {DS[0]: _ix(DS[0], 2000), DS[1]: _ix(DS[1], 2020), DS[3]: _ix(DS[3], 1980)}


def _calc(**kw: Any) -> list[OutcomeCalc]:
    args: dict[str, Any] = {
        "entry_price": 100.0, "days": DS, "bars": BARS, "index_bars": IDX,
        "horizons": (1, 3), "upto": DS[3], "split_ratio": 0.31,
    }  # fmt: skip
    args.update(kw)
    return signal_outcomes(**args)


def test_hand_calc() -> None:
    h1, h3 = _calc()
    assert h1.horizon == 1 and h1.due_date == DS[1]
    assert h1.ret == pytest.approx(0.04)
    assert h1.excess == pytest.approx(0.04 - 0.01)
    assert h1.mfe == pytest.approx(0.06)
    assert h1.mae == pytest.approx(0.0)
    assert h1.quality is Quality.OK
    assert h3.ret == pytest.approx(-0.02)
    assert h3.excess == pytest.approx(-0.02 - (-0.01))
    assert h3.mfe == pytest.approx(0.06)
    assert h3.mae == pytest.approx(-0.06)
    assert h3.quality is Quality.STALE  # 창 안 가장 나쁜 품질


def test_not_due_rows_are_not_made() -> None:
    out = _calc(horizons=(1, 5), upto=DS[3])
    assert [o.horizon for o in out] == [1]


def test_entry_missing_gives_nothing() -> None:
    assert _calc(entry_price=None) == []


def test_halted_and_delisted() -> None:
    gap = {k: v for k, v in BARS.items() if k != DS[1]}
    (h1,) = _calc(bars=gap, horizons=(1,))
    assert h1.ret is None and h1.reason == "halted"  # 그 뒤 봉이 있다
    assert h1.quality is not Quality.OK
    only0 = {DS[0]: BARS[DS[0]]}
    (d1,) = _calc(bars=only0, horizons=(1,), listed=False)
    assert d1.reason == "delisted"
    (s1,) = _calc(bars=only0, horizons=(1,), listed=True)
    assert s1.reason == "halted"


def test_split_suspect_in_window() -> None:
    split = dict(BARS)
    split[DS[2]] = _bar(DS[2], 52, 53, 50, 52)  # 104 → 52(−50%)
    h1, h3 = _calc(bars=split)
    assert h1.reason is None and h1.ret is not None
    assert h3.reason == "split_suspect" and h3.ret is None


def test_no_index_keeps_ret() -> None:
    (h3,) = _calc(index_bars=None, horizons=(3,))
    assert h3.ret == pytest.approx(-0.02) and h3.excess is None
    assert h3.reason == "no_index"


def test_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        _calc(entry_price=0.0)
    with pytest.raises(ValueError):
        _calc(horizons=(0,))
    with pytest.raises(ValueError):
        _calc(days=[])

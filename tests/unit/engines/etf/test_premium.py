"""괴리율·경고(docs/metrics.md §8.3)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from kbj.config.markets import PremiumCfg
from kbj.core.quality import Quality
from kbj.core.rows import EtfDay, EtfMeta, EtfQuote, EtfType
from kbj.engines.etf.premium import alerts, premium_pct, premium_rows, threshold_for

THR = PremiumCfg(default=0.5, overseas=1.0, bond=0.3)
D = date(2026, 10, 6)


def eday(code: str, close: float | None, nav: float | None, q: Quality = Quality.OK) -> EtfDay:
    return EtfDay(code, D, None, close, nav, None, None, None, None, None, None, "krx", "KRX", q)


def meta(code: str, t: EtfType) -> EtfMeta:
    return EtfMeta(code, None, None, None, None, t, None, None, None, None, "s", Quality.OK)


def test_formula_and_boundary() -> None:
    assert premium_pct(1010.0, 1000.0) == pytest.approx(1.0)
    assert premium_pct(1005.0, 1000.0) == 0.5  # 부동소수 끝자리로 0.4999… 가 되지 않는다
    rows = premium_rows([eday("A", 1005.0, 1000.0), eday("B", 995.0, 1000.0),
                         eday("C", 1004.9, 1000.0)], THR)  # fmt: skip
    assert [(r.code, r.warn) for r in rows] == [("A", True), ("B", True), ("C", False)]
    with pytest.raises(ValueError):
        premium_pct(1.0, 0.0)


def test_thresholds_by_type() -> None:
    assert threshold_for(EtfType.OVERSEAS, THR) == 1.0
    assert threshold_for(EtfType.BOND_CASH, THR) == 0.3
    assert threshold_for(EtfType.KR_THEME, THR) == 0.5
    assert threshold_for(None, THR) == 0.5
    rows = [eday("O", 1008.0, 1000.0), eday("K", 1008.0, 1000.0)]
    hit = alerts(rows, THR, {"O": meta("O", EtfType.OVERSEAS), "K": meta("K", EtfType.KR_THEME)})
    assert [r.code for r in hit] == ["K"]


def test_intraday_is_estimated_and_bad_rows_skipped() -> None:
    ts = datetime(2026, 10, 6, 1, 10, tzinfo=UTC)
    q = EtfQuote("A", ts, 1010.0, 1000.0, None, None, None, "kis", Quality.OK)
    [r] = premium_rows([q], THR)
    assert r.basis == "intraday" and r.quality is Quality.ESTIMATED and r.as_of == ts
    skipped = [eday("N", 1000.0, None), eday("Z", 1000.0, 0.0),
               eday("I", 1100.0, 1000.0, Quality.INVALID)]  # fmt: skip
    assert premium_rows(skipped, THR) == []


def test_alerts_sorted_by_magnitude() -> None:
    rows = [eday("A", 1006.0, 1000.0), eday("B", 980.0, 1000.0), eday("C", 1001.0, 1000.0)]
    assert [r.code for r in alerts(rows, THR)] == ["B", "A"]

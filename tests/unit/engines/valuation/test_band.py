"""배수 밴드 — 선형 백분위(numpy 대조)·250일 미만 사유·거름값 밖 제외 수·현재 백분위(§8.3
test_band)."""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

import numpy as np
import pytest

from kbj.core.fin_rows import NaReason
from kbj.core.quality import Quality
from kbj.engines.valuation.band import band, band_lines, bands
from tests.unit.engines.valuation.val_rows import CAPS, vday

D0 = date(2021, 1, 4)
CAP = (0.0, 200.0)


def _days(n: int) -> list[date]:
    return [D0 + timedelta(days=i) for i in range(n)]


def test_백분위는_numpy_linear_와_같고_현재_순위는_경험적() -> None:
    rng = random.Random(3)  # noqa: S311 — 고정 시드 합성 표본
    days = _days(400)
    vals = [rng.uniform(5, 40) for _ in days]
    series = [vday(d, per=v) for d, v in zip(days, vals, strict=True)]
    b = band(series, "per", code="990010", as_of=days[-1], cap=CAP, window_days=300, min_days=250)
    window = vals[-300:]
    want = np.percentile(np.array(window), [10, 25, 50, 75, 90])
    got = (b.p10, b.p25, b.p50, b.p75, b.p90)
    assert all(
        math.isclose(g or 0, float(w), rel_tol=1e-12) for g, w in zip(got, want, strict=True)
    )
    assert (b.n_days, b.n_excluded, b.window_from) == (300, 0, days[100])
    assert b.min == min(window) and b.max == max(window)
    assert b.current == vals[-1]
    assert b.current_rank_pct == pytest.approx(100 * sum(x <= vals[-1] for x in window) / 300)
    assert b.na_reason is None and b.quality is Quality.OK


def test_거름값_밖과_None_은_빼고_센다() -> None:
    days = _days(300)
    series = []
    for i, d in enumerate(days):
        if i % 10 == 0:
            series.append(vday(d, per=None, na={"per": NaReason.LOSS}))
        elif i % 10 == 1:
            series.append(vday(d, per=250.0))  # 거름값 밖(저장은 하되 밴드에서 뺀다)
        else:
            series.append(vday(d, per=10.0 + i % 7))
    b = band(series, "per", code="990010", as_of=days[-1], cap=CAP, window_days=300, min_days=200)
    assert (b.n_days, b.n_excluded) == (240, 60)
    assert b.max is not None and b.max < 200


def test_현재가_거름값_밖이어도_현재는_그대로() -> None:
    days = _days(260)
    series = [vday(d, per=12.0) for d in days[:-1]] + [vday(days[-1], per=300.0)]
    b = band(series, "per", code="990010", as_of=days[-1], cap=CAP, window_days=300, min_days=250)
    assert b.p50 == 12.0 and b.current == 300.0 and b.current_rank_pct == 100.0


def test_창이_짧으면_short_history() -> None:
    days = _days(20)  # 신규상장 20거래일
    series = [vday(d, pbr=2.0) for d in days]
    b = band(series, "pbr", code="990010", as_of=days[-1], cap=CAP, window_days=1240, min_days=250)
    assert b.p50 is None and b.na_reason is NaReason.SHORT_HISTORY
    assert (b.n_days, b.current, b.current_rank_pct) == (20, 2.0, None)


def test_창은_길어도_유효_일이_모자라면_현재_사유() -> None:
    days = _days(400)
    series = [vday(d, per=None, na={"per": NaReason.SPAC}) for d in days]
    b = band(series, "per", code="990010", as_of=days[-1], cap=CAP, window_days=1240, min_days=250)
    assert b.na_reason is NaReason.SPAC and b.n_excluded == 400


def test_현재_사유가_순위_앞이면_짧은_창보다_먼저() -> None:
    days = _days(30)
    series = [vday(d, per=None, na={"per": NaReason.CURRENCY_MISMATCH}) for d in days]
    b = band(series, "per", code="990010", as_of=days[-1], cap=CAP, window_days=1240, min_days=250)
    assert b.na_reason is NaReason.CURRENCY_MISMATCH


def test_현재_사유가_없으면_창에서_가장_많은_사유() -> None:
    days = _days(300)
    series = [vday(d, per=None, na={"per": NaReason.LOSS}) for d in days[:200]]
    series += [vday(d, per=None, na={"per": NaReason.MISSING_INPUT}) for d in days[200:250]]
    series += [vday(d, per=15.0) for d in days[250:]]
    b = band(series, "per", code="990010", as_of=days[-1], cap=CAP, window_days=1240, min_days=250)
    assert b.na_reason is NaReason.LOSS and b.current == 15.0


def test_as_of_뒤의_행은_보지_않는다() -> None:
    days = _days(300)
    series = [vday(d, psr=1.0 + (i % 3)) for i, d in enumerate(days)]
    b = band(
        series,
        "psr",
        code="990010",
        as_of=days[260],
        cap=(0.0, 100.0),
        window_days=1240,
        min_days=250,
    )
    assert b.n_days == 261 and b.current == series[260].psr


def test_invalid_행은_빼고_품질은_가장_나쁜_것() -> None:
    days = _days(260)
    series = [vday(d, per=10.0, quality=Quality.ESTIMATED) for d in days[:5]]
    series += [
        vday(d, per=1000.0 if i == 0 else 11.0, quality=Quality.INVALID if i == 0 else Quality.OK)
        for i, d in enumerate(days[5:])
    ]
    b = band(
        series,
        "per",
        code="990010",
        as_of=days[-1],
        cap=(0.0, 2000.0),
        window_days=1240,
        min_days=250,
    )
    assert b.max == 11.0 and b.quality is Quality.ESTIMATED


def test_네_지표_한번에() -> None:
    days = _days(260)
    series = [vday(d, per=10.0, pbr=1.0, psr=2.0, ev_ebitda=5.0) for d in days]
    got = bands(series, code="990010", as_of=days[-1], caps=CAPS, window_days=1240, min_days=250)
    assert [b.metric for b in got] == ["per", "pbr", "psr", "ev_ebitda"]
    assert all(b.na_reason is None for b in got)


def test_밴드가는_종가_나누기_배수_곱하기_백분위() -> None:
    days = _days(260)
    series = [vday(d, per=10.0 + (i % 5), price=1000.0) for i, d in enumerate(days)]
    b = band(series, "per", code="990010", as_of=days[-1], cap=CAP, window_days=1240, min_days=250)
    lines = band_lines(series, b)
    assert len(lines) == 260
    last = lines[-1]
    per_share = 1000.0 / series[-1].per  # type: ignore[operator]
    assert last.lines["p50"] == pytest.approx((b.p50 or 0) * per_share)
    assert (
        band_lines(
            series,
            band(
                series[:10],
                "per",
                code="990010",
                as_of=days[9],
                cap=CAP,
                window_days=1240,
                min_days=250,
            ),
        )
        == []
    )


def test_인자_검사() -> None:
    with pytest.raises(ValueError):
        band([], "per", code="990010", as_of=D0, cap=CAP, window_days=10, min_days=20)

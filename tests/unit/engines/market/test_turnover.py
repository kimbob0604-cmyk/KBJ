"""시장 거래대금 — ETF·ETN·리츠 제외, 장중(지수 기준)과 확정이 다른 꼬리표(metrics §6.1·§6.7)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import IndexQuote
from kbj.core.time import KST
from kbj.engines.flows.ledger import build_ledger
from kbj.engines.market.turnover import (
    intraday_market_turnover,
    market_turnover,
    turnover_coverage,
    turnover_ratio,
    turnover_series,
)
from tests.fixtures.synthetic.ledger_gen import generate, ledger_inputs
from tests.unit.engines.flows.flow_rows import days, ledger_of, lrow

D = days(3)


def test_주식만_더한다_ETF_ETN_리츠_코넥스_제외() -> None:
    rows = [
        lrow("A00001", D[0], turnover=100),
        lrow("A00002", D[0], turnover=200, kind="pref", market="KOSDAQ"),
        lrow("A00003", D[0], turnover=300, kind="spac"),
        lrow("A00004", D[0], turnover=10_000, kind="etf"),
        lrow("A00005", D[0], turnover=10_000, kind="etn"),
        lrow("A00006", D[0], turnover=10_000, kind="reit"),
        lrow("A00007", D[0], turnover=10_000, market="KONEX"),
        lrow("A00008", D[0], turnover=10_000, kind=None),
        lrow("A00009", D[0], turnover=10_000, quality=Quality.INVALID),
        lrow("A00010", D[0], turnover=None),
    ]
    led = ledger_of(rows)
    v = market_turnover(led, D[0])
    assert v is not None
    assert v.value == 600
    assert v.quality is Quality.OK and v.source == "KRX"
    assert (v.as_of.hour, v.as_of.minute) == (15, 30)
    cov = turnover_coverage(led, D[0])
    assert (cov.n_used, cov.n_invalid, cov.n_missing_turnover) == (3, 1, 1)
    assert (cov.n_other_kind, cov.n_other_market, cov.n_unknown_kind) == (3, 1, 1)


def test_쓸_행이_없으면_None() -> None:
    led = ledger_of([lrow("A00001", D[0], kind="etf")])
    assert market_turnover(led, D[0]) is None
    assert market_turnover(led, D[1]) is None


def test_합성_원장_손계산과_같다() -> None:
    m = generate(9)
    led = build_ledger(**ledger_inputs(m))  # type: ignore[arg-type]
    d = m.trading_days[4]
    want = sum(
        s.turnover or 0
        for s in m.snaps
        if s.date == d and s.market in ("KOSPI", "KOSDAQ") and s.kind in ("common", "pref", "spac")
    )
    v = market_turnover(led, d)
    assert v is not None and v.value == want
    last = market_turnover(led, m.last_day)
    assert last is not None and last.source == "KIS"
    series = turnover_series(led, m.last_day, 20)
    assert len(series) == 20 and series[-1].value == last.value
    r = turnover_ratio(led, m.last_day)
    assert r.n_prior == 20 and r.today == last
    assert r.ratio == pytest.approx(last.value / r.avg_prior)  # type: ignore[operator]


def test_20일_미만이면_있는_날로_나눈다() -> None:
    rows = [lrow("A00001", D[0], turnover=100), lrow("A00001", D[1], turnover=300)]
    rows.append(lrow("A00001", D[2], turnover=400))
    r = turnover_ratio(ledger_of(rows), D[2])
    assert (r.n_prior, r.avg_prior, r.ratio) == (2, 200.0, 2.0)


def _q(code: str, ts: datetime, tv: int | None, q: Quality = Quality.ESTIMATED) -> IndexQuote:
    return IndexQuote(code, ts, None, 1.0, 0.1, tv, None, "kis", q)


def test_장중은_지수_누적_합_estimated_슬롯_끝() -> None:
    t0 = datetime(2026, 10, 7, 10, 0, tzinfo=KST)
    t1 = t0 + timedelta(minutes=10)
    quotes = [
        _q("0001", t0, 100), _q("1001", t0, 50),
        _q("0001", t1, 150),  # 코스닥이 빠진 슬롯 — 쓰지 않는다
        _q("2001", t1, 999),
    ]  # fmt: skip
    v = intraday_market_turnover(quotes)
    assert v is not None
    assert v.value == 150
    assert v.quality is Quality.ESTIMATED
    assert v.as_of == t0 + timedelta(minutes=10)
    assert v.source == "KIS"
    assert intraday_market_turnover([_q("0001", t0, 1)]) is None
    assert intraday_market_turnover([_q("0001", t0, 1), _q("1001", t0, None)]) is None


def test_장중_값을_분자로_20일_대비() -> None:
    rows = [lrow("A00001", D[0], turnover=100), lrow("A00001", D[1], turnover=300)]
    led = ledger_of(rows)
    t0 = datetime(2026, 10, 7, 10, 0, tzinfo=KST)
    today = intraday_market_turnover([_q("0001", t0, 400), _q("1001", t0, 0)])
    r = turnover_ratio(led, D[2], today=today)
    assert r.ratio == 2.0 and r.today is today

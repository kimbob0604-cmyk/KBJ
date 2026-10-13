"""가짜 Nasdaq·Stooq(tests/fakes/nasdaq_server.py) — 어댑터 파서가 읽는 모양·결정성."""

from __future__ import annotations

import httpx

from kbj.data.private.nasdaq.client import parse_nasdaq_history, parse_screener
from kbj.data.private.stooq.client import parse_stooq
from kbj.engines.us.config import load_us
from tests.fakes.nasdaq_server import EXCHANGES, FakeNasdaq, FakeStooq
from tests.fixtures.synthetic.us import demo

CFG = load_us()


def _get(t: httpx.MockTransport, url: str) -> httpx.Response:
    with httpx.Client(transport=t, base_url="https://x") as c:
        return c.get(url)


def test_스크리너_모양은_파서가_읽고_ETF_는_펀드로_빠진다() -> None:
    f = FakeNasdaq.synthetic(n=6, seed=1)
    rows: list[dict[str, object]] = []
    funds = 0
    for ex in EXCHANGES:
        got, drop = parse_screener(
            _get(f.transport(), f"/api/screener/stocks?exchange={ex}").json(), CFG
        )
        rows += got
        funds += drop["fund"]
    assert len(rows) == 6 and funds == 1
    assert all(isinstance(r["close"], float) and r["mktcap"] for r in rows)


def test_히스토리는_내림차순_MMDDYYYY_이고_구간으로_자른다() -> None:
    f = FakeNasdaq.synthetic(n=1, seed=2)
    r = _get(f.transport(), "/api/quote/SYN00/historical?fromdate=2026-09-01&todate=2026-09-10")
    raw = r.json()["data"]["tradesTable"]["rows"]
    assert raw[0]["date"] == "09/10/2026" and raw[-1]["date"] == "09/01/2026"
    bars = parse_nasdaq_history(r.json())
    assert [b["asof"] for b in bars] == sorted(b["asof"] for b in bars)
    assert len(bars) == 8  # 9/1~9/10 평일


def test_클래스주는_정한_표기만_200() -> None:
    f = FakeNasdaq()
    f.add_series("BRK.B", n=5, accept_form="BRK/B")
    assert _get(f.transport(), "/api/quote/BRK.B/historical").status_code == 404
    assert _get(f.transport(), "/api/quote/BRK%2FB/historical").status_code == 200


def test_같은_시드는_같은_세계() -> None:
    a, b = FakeNasdaq.synthetic(n=3, seed=9), FakeNasdaq.synthetic(n=3, seed=9)
    assert a.series == b.series and a.screener_rows == b.screener_rows
    assert FakeNasdaq.synthetic(n=3, seed=10).series != a.series


def test_demo_세계를_그대로_싣는다() -> None:
    series, snaps, _asof = demo.make(n_tickers=6, seed=1)
    f = FakeNasdaq.from_series(series, snaps)
    assert sum(len(v) for v in f.screener_rows.values()) == 6
    assert all(f.screener_rows[ex] for ex in EXCHANGES)  # 거래소마다 0행이면 어댑터가 예외


def test_stooq_csv_와_빈_본문() -> None:
    s = FakeStooq.synthetic(["AAA"], seed=1)
    r = _get(s.transport(), "/q/d/l/?s=aaa.us&i=d&d1=20260901")
    rows = parse_stooq(r.text)
    assert rows and rows[0]["asof"] >= "2026-09-01" and rows[0]["source"] == "stooq"
    s.blank.add("AAA")
    assert _get(s.transport(), "/q/d/l/?s=aaa.us&i=d").text == ""
    assert _get(s.transport(), "/q/d/l/?s=zzz.us&i=d").text == "No data"

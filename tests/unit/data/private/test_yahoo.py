"""Yahoo 어댑터(kbj/data/private/yahoo — docs/p5_design.md §1.1 FND).

1) 승격: ETF-Traker `board/tests/test_us_sources.py:TestYahoo` 3개 — 같은 단언, import 경로만
   (`from board.us import sources as S` → `kbj.data.private.yahoo.client`). 나머지(Nasdaq·Stooq)는
   US 묶음.
2) 새 시험: 가짜 서버(tests/fakes/yahoo_server.py)로 리미터·오류 사유·장중 봉 estimated·격리.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest

from kbj.core.quality import Quality
from kbj.data.catalog import get
from kbj.data.private.yahoo import client as S
from kbj.data.private.yahoo.client import YahooClient, YahooNotFound, YahooThrottled
from kbj.data.ratelimit import Priority
from tests.fakes.yahoo_server import FakeYahoo


# ── 승격(원본 그대로) ──────────────────────────────────────────────────────────────────────
class TestYahoo(unittest.TestCase):
    PAYLOAD: ClassVar[dict[str, Any]] = {
        "chart": {
            "result": [
                {
                    "timestamp": [1757548800, 1757808000],
                    "indicators": {
                        "quote": [
                            {
                                "open": [1, 2],
                                "high": [2, 3],
                                "low": [0.5, 1.5],
                                "close": [1.5, 2.5],
                                "volume": [100, 200],
                            }
                        ]
                    },
                }
            ]
        }
    }

    def test_parse(self):
        rows = S.parse_yahoo(self.PAYLOAD)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["close"], 2.5)

    def test_null_close_rows_dropped(self):
        p = {'chart': {'result': [{'timestamp': [1757548800],
                                   'indicators': {'quote': [{'close': [None]}]}}]}}  # fmt: skip
        self.assertEqual(S.parse_yahoo(p), [])

    def test_empty_result(self):
        self.assertEqual(S.parse_yahoo({"chart": {"result": []}}), [])


# ── 새 시험 ────────────────────────────────────────────────────────────────────────────────
NOW = datetime(2026, 11, 17, 15, 0, tzinfo=UTC)  # 10:00 ET


class _Limiter:
    def __init__(self) -> None:
        self.calls: list[tuple[Priority, str]] = []
        self.slowed = 0

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        self.calls.append((priority, tr_id))

    def on_rate_limited(self) -> float:
        self.slowed += 1
        return 0.5


def _client(fake: FakeYahoo, lim: _Limiter | None = None) -> YahooClient:
    return YahooClient(lim or _Limiter(), transport=fake.transport("test"), base_url="https://y")


def test_chart_returns_bars_through_the_limiter() -> None:
    lim = _Limiter()
    bars = _client(FakeYahoo(lambda: NOW), lim).chart("^GSPC", "1mo")
    assert bars and all(b.source == "yahoo" and b.venue == "" for b in bars)
    assert [b.date for b in bars] == sorted(b.date for b in bars)
    assert all(b.quality is Quality.OK for b in bars)  # 장 마감 뒤처럼 — 모두 확정
    assert lim.calls == [(Priority.P3, "yahoo:chart")]


def test_live_last_bar_is_estimated_not_a_close() -> None:
    bars = _client(FakeYahoo(lambda: NOW, live=True)).chart("^GSPC", "1mo")
    assert bars[-1].quality is Quality.ESTIMATED
    assert {b.quality for b in bars[:-1]} == {Quality.OK}


def test_errors_carry_reasons_and_429_slows_down() -> None:
    fake = FakeYahoo(lambda: NOW)
    lim = _Limiter()
    c = _client(fake, lim)
    with pytest.raises(YahooNotFound, match="Not Found"):
        c.chart("NOPE")
    fake.inject(429)
    with pytest.raises(YahooThrottled):
        c.chart("^VIX")
    assert lim.slowed == 1
    fake.inject("empty")
    assert c.chart("^VIX") == []  # 빈 결과는 빈 목록 — 부르는 쪽이 '못 받음'
    with pytest.raises(ValueError):
        c.chart("^VIX", "7y")


def test_one_symbol_failure_does_not_stop_the_others() -> None:
    fake = FakeYahoo(lambda: NOW)
    ok, failed = _client(fake).charts(["^GSPC", "NOPE", "^VIX"], "5d")
    assert set(ok) == {"^GSPC", "^VIX"} and set(failed) == {"NOPE"}
    fake.inject(429)
    with pytest.raises(YahooThrottled):
        _client(fake).charts(["^GSPC", "^VIX"], "5d")


def test_catalog_lists_yahoo_as_private_adapter() -> None:
    for ds in ("YAHOO:us_index_daily", "YAHOO:macro"):
        spec = get(ds)
        assert spec.tier.value == "private" and spec.limiter == "yahoo"
        assert "[어댑터 없음" not in spec.notes
    assert get("YAHOO:us_index_daily").store == "prv_market.daily_bar"

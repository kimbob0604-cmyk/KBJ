"""목표가 컨센서스·투자의견 분포(§4.7, metrics §10) — 증권사별 마지막 의견·90일 창·n 별 품질."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from kbj.core.fin_rows import OpinionRow
from kbj.core.quality import Quality
from kbj.engines.consensus.target import (
    is_rated,
    latest_by_broker,
    opinion_bucket,
    opinion_counts,
    target_consensus,
)

T = date(2026, 11, 16)


def op(
    broker: str,
    days_ago: int,
    target: float | None,
    opinion: str | None = "매수",
    *,
    code: str = "994010",
    quality: Quality = Quality.OK,
) -> OpinionRow:
    return OpinionRow(code, broker, T - timedelta(days=days_ago), opinion, None, target,
                      quality=quality)  # fmt: skip


def test_hand_computed_consensus() -> None:
    rows = [
        op("A", 80, 9000.0),  # A 의 옛 의견 — 마지막 것만 쓴다
        op("A", 10, 12000.0),
        op("B", 20, 10000.0),
        op("C", 90, 14000.0),  # 창 경계(t − 90) 포함
        op("D", 91, 50000.0),  # 창 밖
        op("E", 5, 0.0, "Not Rated"),  # 미제시 — 뺀다
        op("F", 40, 13000.0),
        op("F", 3, 11000.0, "N/R"),  # F 의 마지막 의견이 미제시 → F 를 뺀다(옛 목표가로 안 돌아감)
        op("G", 1, None),  # 목표가 없음 — 뺀다
    ]
    tc = target_consensus(rows, T, window_days=90, min_brokers_ok=3)
    assert tc is not None
    assert tc.n_brokers == 3  # A 12000, B 10000, C 14000
    assert tc.mean == pytest.approx(12000.0) and tc.median == 12000.0
    assert (tc.high, tc.low) == (14000.0, 10000.0)
    assert tc.quality is Quality.OK and tc.as_of == T and tc.window_days == 90
    assert tc.code == "994010" and tc.source == "KIS"


def test_even_count_median_and_quality_by_n() -> None:
    two = [op("A", 1, 10000.0), op("B", 2, 13000.0)]
    tc = target_consensus(two, T)
    assert tc is not None and tc.median == 11500.0 and tc.n_brokers == 2
    assert tc.quality is Quality.ESTIMATED  # 1~2 곳
    one = target_consensus([op("A", 1, 10000.0)], T)
    assert one is not None and one.quality is Quality.ESTIMATED
    assert target_consensus([op("A", 1, 0.0)], T) is None  # 0 → 행 없음
    assert target_consensus([], T) is None
    est = [op("A", 1, 1.0), op("B", 1, 2.0), op("C", 1, 3.0, quality=Quality.ESTIMATED)]
    got = target_consensus(est, T)
    assert got is not None and got.quality is Quality.ESTIMATED


def test_future_opinions_are_ignored_and_codes_not_mixed() -> None:
    assert target_consensus([op("A", -1, 10000.0)], T) is None  # t 뒤 의견 — PIT
    with pytest.raises(ValueError, match="섞였다"):
        target_consensus([op("A", 1, 1.0), op("B", 1, 1.0, code="994020")], T)
    with pytest.raises(ValueError):
        target_consensus([op("A", 1, 1.0)], T, window_days=0)


def test_latest_by_broker_same_day_takes_the_later_row() -> None:
    rows = [op("A", 1, 10000.0), op("A", 1, 11000.0)]
    (last,) = latest_by_broker(rows, T, window_days=90)
    assert last.target_price == 11000.0


@pytest.mark.parametrize(
    ("text", "bucket", "rated"),
    [
        ("매수", "buy", True),
        ("Strong Buy", "buy", True),
        ("Trading Buy", "buy", True),
        ("Outperform", "buy", True),
        ("중립", "hold", True),
        ("HOLD", "hold", True),
        ("Market Perform", "hold", True),
        ("매도", "sell", True),
        ("Underperform", "sell", True),
        ("Not Rated", None, False),
        ("N.R", None, False),
        ("미제시", None, False),
        ("", None, False),
        (None, None, False),
        ("관심", None, True),  # 모르는 문구 — 분류하지 않는다
    ],
)
def test_opinion_bucket(text: str | None, bucket: str | None, rated: bool) -> None:
    assert opinion_bucket(text) == bucket
    assert is_rated(text) is rated


def test_opinion_counts() -> None:
    rows = [
        op("A", 1, 1.0, "매수"),
        op("B", 1, 1.0, "중립"),
        op("C", 1, 1.0, "Sell"),
        op("D", 1, 0.0, "Not Rated"),
        op("E", 1, 1.0, "관심"),
        op("F", 200, 1.0, "매도"),  # 창 밖
    ]
    c = opinion_counts(rows, T, window_days=90)
    assert (c.buy, c.hold, c.sell, c.unrated, c.unknown) == (1, 1, 1, 1, 1)
    assert c.n_brokers == 5

"""미 재무부 수익률 곡선 어댑터(kbj/data/public/treasury — 공개, 사용자 결정 Q8). 가짜
서버(합성)만."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from kbj.core.quality import Quality
from kbj.data.public.treasury import DATASETS, parse_yield_csv
from kbj.data.public.treasury.client import TreasuryClient, TreasuryError, TreasuryThrottled
from kbj.data.ratelimit import Priority
from tests.fakes.treasury_server import TENORS, FakeTreasury

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


class _Limiter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.slowed = 0

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        self.calls.append(tr_id)

    def on_rate_limited(self) -> float:
        self.slowed += 1
        return 0.5


def _client(fake: FakeTreasury, lim: _Limiter | None = None) -> TreasuryClient:
    return TreasuryClient(lim or _Limiter(), transport=fake.transport(), base_url="https://t.test")


def test_yield_curve_rows_by_date_and_tenor_with_blank_cells_as_none() -> None:
    fake = FakeTreasury(lambda: NOW, blank={("11/13/2026", "10 Yr")})
    lim = _Limiter()
    pts = _client(fake, lim).yield_curve(2026)
    assert lim.calls == ["treasury:yield_curve"]
    assert pts[-1].date == date(2026, 11, 16)  # 끝 = 뉴욕 전날
    tenors = {p.tenor for p in pts}
    assert tenors == set(TENORS)
    gap = next(p for p in pts if p.date == date(2026, 11, 13) and p.tenor == "10 Yr")
    assert gap.value is None and gap.quality is Quality.INVALID
    assert [(p.date, p.tenor) for p in pts] == sorted((p.date, p.tenor) for p in pts)


def test_errors_and_format_change() -> None:
    fake = FakeTreasury(lambda: NOW)
    lim = _Limiter()
    fake.inject(429)
    with pytest.raises(TreasuryThrottled):
        _client(fake, lim).yield_curve(2026)
    assert lim.slowed == 1
    fake.inject(503)
    with pytest.raises(TreasuryError, match="HTTP 503"):
        _client(fake).yield_curve(2026)
    fake.inject("no_date")
    with pytest.raises(TreasuryError, match="Date 열"):
        _client(fake).yield_curve(2026)
    with pytest.raises(ValueError):
        _client(fake).yield_curve(1800)


def test_parse_csv_rejects_non_numbers_and_keeps_bom_free() -> None:
    assert parse_yield_csv("﻿Date,10 Yr\n11/16/2026,4.10\n")[0].value == Decimal("4.10")
    with pytest.raises(ValueError):
        parse_yield_csv("Date,10 Yr\n11/16/2026,abc\n")
    assert DATASETS[0].id == "TREASURY:yield_curve" and DATASETS[0].tier.value == "public"

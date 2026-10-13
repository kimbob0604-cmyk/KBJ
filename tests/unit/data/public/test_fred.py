"""FRED 공개 어댑터(kbj/data/public/fred — docs/p5_design.md §1.2, D-P5-6). 가짜 서버(합성)만."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr

from kbj.config.macro import load_macro
from kbj.core.quality import Quality
from kbj.data.public.fred import DATASETS
from kbj.data.public.fred.client import (
    FredClient,
    FredError,
    FredKeyError,
    FredNotFound,
    FredThrottled,
    TierError,
    parse_value,
)
from kbj.data.ratelimit import Priority
from tests.fakes.fred_server import FAKE_KEY, FakeFred

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)  # 07:00 ET
GOV = ("DGS10", "DGS2", "DGS3MO")


class _Limiter:
    def __init__(self) -> None:
        self.calls: list[tuple[Priority, str]] = []
        self.slowed = 0

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        self.calls.append((priority, tr_id))

    def on_rate_limited(self) -> float:
        self.slowed += 1
        return 0.5


def _client(fake: FakeFred, lim: _Limiter | None = None, key: str = FAKE_KEY) -> FredClient:
    return FredClient(
        SecretStr(key), limiter=lim or _Limiter(), gov_series=GOV,
        transport=fake.transport(), base_url="https://fred.test",
    )  # fmt: skip


def test_observations_parse_dates_values_and_missing_dot() -> None:
    fake = FakeFred(lambda: NOW, missing={("DGS2", "2026-11-13")})
    lim = _Limiter()
    rows = _client(fake, lim).observations("DGS2", date(2026, 11, 9), date(2026, 11, 20))
    assert [r.date for r in rows] == [date(2026, 11, d) for d in (9, 10, 11, 12, 13, 16)]
    gap = next(r for r in rows if r.date == date(2026, 11, 13))
    assert gap.value is None and gap.raw == "." and gap.quality is Quality.INVALID
    assert all(isinstance(r.value, Decimal) for r in rows if r.date != gap.date)
    assert lim.calls == [(Priority.P3, "fred:observations")]


def test_public_client_refuses_restricted_series_without_calling() -> None:
    fake = FakeFred(lambda: NOW)
    with pytest.raises(TierError, match="fred_restricted"):
        _client(fake).observations("SP500")
    assert fake.seen == []


def test_errors_carry_reasons_and_never_the_key() -> None:
    fake = FakeFred(lambda: NOW)
    secret = "wrong-key-SECRET-abcdef123456"
    with pytest.raises(FredKeyError) as ei:
        _client(fake, key=secret).observations("DGS10")
    assert ei.value.critical and secret not in str(ei.value) and "api_key" in str(ei.value)
    c = FredClient(SecretStr(FAKE_KEY), limiter=_Limiter(), gov_series=("NOPE1",),
                   transport=fake.transport(), base_url="https://fred.test")  # fmt: skip
    with pytest.raises(FredNotFound, match="does not exist"):
        c.observations("NOPE1")
    lim = _Limiter()
    fake.inject(429)
    with pytest.raises(FredThrottled):
        _client(fake, lim).observations("DGS10")
    assert lim.slowed == 1
    fake.inject(500)
    with pytest.raises(FredError, match="HTTP 500"):
        _client(fake).observations("DGS10")
    fake.inject("garbage")
    with pytest.raises(FredError, match="숫자가 아니다"):
        _client(fake).observations("DGS10", date(2026, 11, 1))
    assert all(FAKE_KEY not in repr(s) for s in fake.seen)


def test_release_dates_are_sorted_and_named() -> None:
    fake = FakeFred(lambda: NOW)
    got = _client(fake).release_dates(date(2026, 11, 1), date(2026, 11, 30))
    assert [(r.date.day, r.release_id) for r in got] == [
        (5, 50),
        (13, 10),
        (20, 9999),
        (27, 53),
        (28, 54),
    ]
    assert got[1].name == "Consumer Price Index"
    with pytest.raises(ValueError):
        _client(fake).release_dates(date(2026, 11, 2), date(2026, 11, 1))


def test_parse_value_and_bad_inputs() -> None:
    assert parse_value(".") is None and parse_value(" 4.25 ") == Decimal("4.25")
    for bad in ("", "abc", "NaN", "inf"):
        with pytest.raises(ValueError):
            parse_value(bad)
    with pytest.raises(FredKeyError):
        FredClient(SecretStr(" "), limiter=_Limiter(), gov_series=GOV)
    with pytest.raises(ValueError):
        FredClient(SecretStr(FAKE_KEY), limiter=_Limiter(), gov_series=())
    with pytest.raises(ValueError):
        _client(FakeFred(lambda: NOW)).transport.observations("DGS10;DROP")
    assert "fake" not in repr(_client(FakeFred(lambda: NOW)))


def test_config_gov_series_are_the_public_fred_items_and_datasets_match_catalog_ids() -> None:
    cfg = load_macro()
    gov = {s.item for s in cfg.series if s.dataset == "FRED:gov_series"}
    restricted = {s.item for s in cfg.series if s.dataset == "FRED:restricted_series"}
    assert gov and restricted and not gov & restricted
    assert all(s.tier == "public" for s in cfg.series if s.dataset == "FRED:gov_series")
    assert {d.id for d in DATASETS} == {"FRED:gov_series", "FRED:release_dates"}
    assert all(d.tier.value == "public" and d.limiter == "fred" for d in DATASETS)

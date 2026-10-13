"""FRED 저작권 시리즈(로그인 — kbj/data/private/fred_restricted.py, D-P5-6). 가짜 서버(합성)만."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import SecretStr

from kbj.config.macro import load_macro
from kbj.data.private.fred_restricted import DATASETS, FredRestrictedClient
from kbj.data.public.fred.client import TierError
from kbj.data.ratelimit import Priority
from tests.fakes.fred_server import FAKE_KEY, FakeFred

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


class _Limiter:
    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        return None

    def on_rate_limited(self) -> float:
        return 0.5


def _client(fake: FakeFred) -> FredRestrictedClient:
    restricted = [s.item for s in load_macro().series if s.dataset == "FRED:restricted_series"]
    return FredRestrictedClient(
        SecretStr(FAKE_KEY), limiter=_Limiter(), restricted=[r for r in restricted if r],
        transport=fake.transport(), base_url="https://fred.test",
    )  # fmt: skip


def test_restricted_series_come_through_the_shared_transport() -> None:
    fake = FakeFred(lambda: NOW)
    rows = _client(fake).observations("VIXCLS", date(2026, 11, 9), date(2026, 11, 13))
    assert len(rows) == 5 and rows[0].series_id == "VIXCLS"


def test_government_series_are_refused_on_the_login_side() -> None:
    fake = FakeFred(lambda: NOW)
    with pytest.raises(TierError, match=r"kbj\.data\.public\.fred"):
        _client(fake).observations("DGS10")
    assert fake.seen == []
    with pytest.raises(ValueError):
        FredRestrictedClient(SecretStr(FAKE_KEY), limiter=_Limiter(), restricted=())


def test_dataset_is_private_on_the_shared_fred_limiter() -> None:
    (d,) = DATASETS
    assert d.id == "FRED:restricted_series" and d.tier.value == "private" and d.limiter == "fred"
    assert d.store == "prv_macro.series"

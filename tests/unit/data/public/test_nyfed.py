"""뉴욕연은 기준금리 어댑터(kbj/data/public/nyfed — 공개, 사용자 결정 Q8). 가짜 서버(합성)만."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from kbj.data.public.nyfed import DATASETS, parse_rates
from kbj.data.public.nyfed.client import NyfedClient, NyfedError, NyfedThrottled
from kbj.data.ratelimit import Priority
from tests.fakes.nyfed_server import FakeNyfed

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


class _Limiter:
    def __init__(self) -> None:
        self.slowed = 0

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        assert tr_id == "nyfed:rates"

    def on_rate_limited(self) -> float:
        self.slowed += 1
        return 0.5


def _client(fake: FakeNyfed, lim: _Limiter | None = None) -> NyfedClient:
    return NyfedClient(lim or _Limiter(), transport=fake.transport(), base_url="https://n.test")


def test_sofr_and_effr_ascending_with_null_kept_as_none() -> None:
    fake = FakeNyfed(lambda: NOW, null_on={"2026-11-12"})
    sofr = _client(fake).rates("sofr", date(2026, 11, 9), date(2026, 11, 30))
    assert [p.date.day for p in sofr] == [9, 10, 11, 12, 13, 16]
    assert next(p for p in sofr if p.date.day == 12).value is None
    effr = _client(fake).rates("effr", date(2026, 11, 9), date(2026, 11, 10))
    assert len(effr) == 2 and all(p.rate == "effr" for p in effr)
    assert fake.calls[0][0] == "/api/rates/secured/sofr/search.json"
    assert fake.calls[1][0] == "/api/rates/unsecured/effr/search.json"


def test_errors_and_bad_inputs() -> None:
    fake = FakeNyfed(lambda: NOW)
    lim = _Limiter()
    fake.inject(429)
    with pytest.raises(NyfedThrottled):
        _client(fake, lim).rates("sofr", date(2026, 11, 9), date(2026, 11, 10))
    assert lim.slowed == 1
    fake.inject(500)
    with pytest.raises(NyfedError, match="HTTP 500"):
        _client(fake).rates("sofr", date(2026, 11, 9), date(2026, 11, 10))
    fake.inject("garbage")
    with pytest.raises(NyfedError, match="숫자가 아니다"):
        _client(fake).rates("sofr", date(2026, 11, 9), date(2026, 11, 10))
    with pytest.raises(ValueError):
        _client(fake).rates("libor", date(2026, 11, 9), date(2026, 11, 10))
    with pytest.raises(ValueError):
        parse_rates("sofr", {"nope": []})
    assert DATASETS[0].id == "NYFED:rates" and DATASETS[0].tier.value == "public"

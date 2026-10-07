"""poller 저장 경계. DB(TimescaleDB, 설계 §8)는 Phase 1 `data/store.py` 몫이라 여기선 프로토콜만
두고, 테스트·로컬 실행은 메모리 구현을 쓴다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from services.poller.records import (
    ChainRecord,
    ExpiryRecord,
    FuturesRecord,
    HealthEvent,
    InvestorRecord,
    QuarantineRecord,
)
from services.recorder.envelope import RawEnvelope


class Sink(Protocol):
    def write_chain(self, rows: Sequence[ChainRecord]) -> None: ...

    def write_futures(self, rows: Sequence[FuturesRecord]) -> None: ...

    def write_investor(self, rows: Sequence[InvestorRecord]) -> None: ...

    def write_expiries(self, rows: Sequence[ExpiryRecord]) -> None: ...

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None: ...

    def write_quarantine(self, rows: Sequence[QuarantineRecord]) -> None: ...

    def write_health(self, events: Sequence[HealthEvent]) -> None: ...


@dataclass
class InMemorySink:
    chain: list[ChainRecord] = field(default_factory=list[ChainRecord])
    futures: list[FuturesRecord] = field(default_factory=list[FuturesRecord])
    investor: list[InvestorRecord] = field(default_factory=list[InvestorRecord])
    expiries: list[ExpiryRecord] = field(default_factory=list[ExpiryRecord])
    raw: list[RawEnvelope] = field(default_factory=list[RawEnvelope])
    quarantine: list[QuarantineRecord] = field(default_factory=list[QuarantineRecord])
    health: list[HealthEvent] = field(default_factory=list[HealthEvent])

    def write_chain(self, rows: Sequence[ChainRecord]) -> None:
        self.chain.extend(rows)

    def write_futures(self, rows: Sequence[FuturesRecord]) -> None:
        self.futures.extend(rows)

    def write_investor(self, rows: Sequence[InvestorRecord]) -> None:
        self.investor.extend(rows)

    def write_expiries(self, rows: Sequence[ExpiryRecord]) -> None:
        self.expiries.extend(rows)

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None:
        self.raw.extend(envelopes)

    def write_quarantine(self, rows: Sequence[QuarantineRecord]) -> None:
        self.quarantine.extend(rows)

    def write_health(self, events: Sequence[HealthEvent]) -> None:
        self.health.extend(events)

    def health_kinds(self) -> list[str]:
        return [e.kind for e in self.health]

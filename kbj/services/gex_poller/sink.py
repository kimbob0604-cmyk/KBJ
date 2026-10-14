"""poller 저장 경계 — GX `services/poller/sink.py` 이식(P9 GXI). DB 쓰기는
`kbj.services.gex_poller.store.GexIngestStore`, 시험·로컬 실행은 메모리 구현을 쓴다.

투자자별 쓰기(`write_investor`)는 프로토콜에서 뺐다 — `prv_gex.investor_flow` 의 쓰기 주인은
flows.market_minute(ADR 0019, D-P7-5). 메모리 구현의 `investor` 목록은 '부르지 않는다' 단언용으로
남긴다(언제나 비어 있다)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from kbj.core.gex.records import (
    ChainRecord,
    ExpiryRecord,
    FuturesRecord,
    HealthEvent,
    InvestorRecord,
    QuarantineRecord,
)
from kbj.services.recorder.envelope import RawEnvelope


class Sink(Protocol):
    def write_chain(self, rows: Sequence[ChainRecord]) -> None: ...

    def write_futures(self, rows: Sequence[FuturesRecord]) -> None: ...

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

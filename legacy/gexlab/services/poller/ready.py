"""체인 사이클 알림 `chain.ready` (Phase 3 설계 §1 입력 계약).

poller 는 한 사이클(전광판 3만기 + 보강)의 체인 행을 저장 싱크에 넘긴 뒤 `chain.ready` 에
`ChainReady{ts, trade_date, session, series}`(services/bus.py)를 발행한다. engine 은 알림을 받으면
그 ts 까지의 시리즈별 최신 행을 DB 에서 읽는다 — 알림은 지연을 줄이려는 것뿐이고(pub/sub 은 잃을 수
있다) engine 은 10초마다 DB `max(ts)` 로 따라잡는다.

- `ReadyTap`: poller `Sink` 감싸기. `write_chain` 이 안쪽 싱크에 예외 없이 넘어간 행만(스풀에
  들어간 것 포함 — 싱크가 받았다) `ReadyNotifier.note` 로 모은다. 안쪽이 예외를 올리면 그대로 올린다
  (수집기 `_emit` 이 격리한다) — 넘기지 못한 행은 알리지 않는다. 모으기가 실패해도 쓰기는 끝났다
  (로그만)
- `ReadyNotifier.flush`(서비스 곁일, 수집 조각마다): 모인 것이 있고 사이클이 끝났으면 발행한다
  - 끝남 = 추적 시리즈 전부의 전광판 행이 모였다(주간·야간 A), 또는 마지막 발행(처음이면 첫 행)
    뒤 `max_wait_s`(기본 30초 = 전광판 주기) — 야간 B 는 전광판이 없어 이쪽이다 [확인 필요]
  - 행의 (귀속 거래일, 세션)이 바뀌면 모인 것을 먼저 발행한다(한 알림 = 한 세션)
  - ts = 모인 행 중 가장 늦은 ts, series = 행이 온 (시장분류, 만기) 쌍
- 발행 실패(Redis 오류 등)는 로그·통계만 — 수집을 막지 않는다. 모인 것은 버린다(engine 따라잡기 몫)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import cast, get_args

from redis import Redis

from services.bus import CHAIN_READY, ChainReady, MrktCls, SeriesKey, publish_or_none
from services.poller.context import Series
from services.poller.records import (
    ChainRecord,
    ExpiryRecord,
    FuturesRecord,
    HealthEvent,
    InvestorRecord,
    QuarantineRecord,
    SessionName,
)
from services.poller.sink import Sink
from services.recorder.envelope import RawEnvelope
from services.runtime import log_event

SERVICE = "poller"
READY_MAX_WAIT_S = 30.0  # 전광판 주기(PollerConfig.board_period_s)와 같게 [확인 필요]
_CLASSES = frozenset(get_args(MrktCls))

log = logging.getLogger("services.poller")


@dataclass
class ReadyStats:
    published: int = 0  # 발행한 알림 (수신자 0 포함 — 발행은 됐다)
    failed: int = 0  # Redis 오류 등으로 못 낸 알림
    rows: int = 0  # 모은 체인 행
    note_errors: int = 0  # 모으기 실패(쓰기는 끝났다)


class ReadyNotifier:
    """체인 행을 모았다가 사이클이 끝나면 `chain.ready` 하나로 알린다."""

    def __init__(
        self,
        redis: Redis,
        *,
        max_wait_s: float = READY_MAX_WAIT_S,
        clock: Callable[[], float] = time.monotonic,
        channel: str = CHAIN_READY,
    ) -> None:
        if max_wait_s <= 0:
            raise ValueError("max_wait_s > 0")
        self._r = redis
        self._max_wait = max_wait_s
        self._clock = clock
        self._channel = channel
        self.stats = ReadyStats()
        self.last: ChainReady | None = None  # 마지막으로 발행한 알림
        self._tag: tuple[date, SessionName] | None = None
        self._ts: datetime | None = None
        self._series: set[SeriesKey] = set()
        self._boards: set[SeriesKey] = set()
        self._since: float | None = None  # 첫 행을 모은 시각(단조)
        self._last: float | None = None  # 마지막 발행 시각(단조)

    @property
    def pending(self) -> bool:
        return self._ts is not None

    def note(self, rows: Sequence[ChainRecord]) -> None:
        """싱크에 넘어간 체인 행. 세션이 바뀌면 모인 것을 먼저 발행한다."""
        for r in rows:
            key = series_key(r.mrkt_cls, r.expiry)
            if key is None:
                continue
            tag = (r.trade_date, r.session)
            if self.pending and tag != self._tag:
                self.flush(force=True)
            self._tag = tag
            self._ts = r.ts if self._ts is None else max(self._ts, r.ts)
            self._series.add(key)
            if r.source == "board":
                self._boards.add(key)
            if self._since is None:
                self._since = self._clock()
            self.stats.rows += 1

    def flush(self, tracked: Iterable[Series] | None = None, *, force: bool = False) -> bool:
        """사이클이 끝났으면(또는 force) 발행한다. 발행했으면(수신자 0 포함) True.

        tracked: 지금 추적 시리즈(최근접·차기·월물) — 전부의 전광판 행이 모였으면 사이클 끝이다.
        예외를 올리지 않는다(곁일 — 수집을 멈추지 않는다).
        """
        try:
            if not self.pending or not (force or self._due(tracked)):
                return False
            return self._publish()
        except Exception as e:
            self.stats.failed += 1
            self._reset()
            self._log(logging.WARNING, "chain_ready_failed", None, error=type(e).__name__)
            return False

    def _due(self, tracked: Iterable[Series] | None) -> bool:
        want = {series_key(s.cls, s.mtrt) for s in tracked} if tracked is not None else set()
        if want and None not in want and want <= self._boards:
            return True
        start = self._last if self._last is not None else self._since
        return start is not None and self._clock() - start >= self._max_wait

    def _publish(self) -> bool:
        tag, ts, series = self._tag, self._ts, tuple(sorted(self._series))
        self._reset()
        self._last = self._clock()
        if tag is None or ts is None or not series:
            return False
        msg = ChainReady(ts=ts, trade_date=tag[0], session=tag[1], series=series)
        got = publish_or_none(self._r, self._channel, msg.model_dump_json())
        if got is None:
            self.stats.failed += 1
            self._log(logging.WARNING, "chain_ready_failed", tag, error="RedisError")
            return False
        self.stats.published += 1
        self.last = msg
        self._log(logging.INFO, "chain_ready", tag, ts=ts.isoformat(), series=len(series), subs=got)
        return True

    def _reset(self) -> None:
        self._ts = None
        self._series = set()
        self._boards = set()
        self._since = None

    def _log(self, level: int, event: str, tag: tuple[date, str] | None, **fields: object) -> None:
        log_event(log, level, SERVICE, event, tag, **fields)


class ReadyTap:
    """poller `Sink` — 체인 행은 안쪽 싱크에 넘긴 뒤 알림용으로 모은다. 나머지는 그대로 넘긴다."""

    def __init__(self, inner: Sink, notifier: ReadyNotifier) -> None:
        self.inner = inner
        self.notifier = notifier

    def write_chain(self, rows: Sequence[ChainRecord]) -> None:
        self.inner.write_chain(rows)  # 예외면 알리지 않는다(넘기지 못했다)
        try:
            self.notifier.note(rows)
        except Exception as e:  # 알림은 곁일 — 쓰기는 끝났다
            self.notifier.stats.note_errors += 1
            err = type(e).__name__
            log_event(log, logging.WARNING, SERVICE, "chain_ready_note_failed", error=err)

    def write_futures(self, rows: Sequence[FuturesRecord]) -> None:
        self.inner.write_futures(rows)

    def write_investor(self, rows: Sequence[InvestorRecord]) -> None:
        self.inner.write_investor(rows)

    def write_expiries(self, rows: Sequence[ExpiryRecord]) -> None:
        self.inner.write_expiries(rows)

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None:
        self.inner.write_raw(envelopes)

    def write_quarantine(self, rows: Sequence[QuarantineRecord]) -> None:
        self.inner.write_quarantine(rows)

    def write_health(self, events: Sequence[HealthEvent]) -> None:
        self.inner.write_health(events)


def series_key(mrkt_cls: str, expiry: str) -> SeriesKey | None:
    """(시장분류, 만기) → 알림 키. 모르는 시장분류·만기 형식이면 None(알리지 않는다)."""
    if mrkt_cls not in _CLASSES or len(expiry) != 6 or not expiry.isdigit():
        return None
    return cast(SeriesKey, (mrkt_cls, expiry))

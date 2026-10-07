"""`chain.ready` 계약과 poller 발행 (Phase 3 설계 §1 — services/bus.py `ChainReady`,
services/poller/ready.py, services/poller/service.py).

fakeredis·가짜 시계·가짜 KIS 서버만 쓴다. 확인:
- 메시지 모델: UTC ts·(시장분류, 6자리 만기) 쌍 정렬·중복 없음·틀린 키 거부·JSON 왕복
- 사이클 끝 판정: 추적 시리즈 전광판이 다 모이면, 아니면 30초 — ts 는 모인 행 중 가장 늦은 것
- 싱크에 넘기지 못한 행은 알리지 않는다, 세션이 바뀌면 먼저 낸다, 발행 실패는 수집을 막지 않는다
- 서비스: 수집 2분에 알림이 나오고 series 가 실제로 쓴 시리즈와 같다. 야간 B(전광판 없음)도 30초로
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import pytest
from pydantic import ValidationError

from core.calendar import TradingCalendar
from data.kis.ratelimit import LocalRateLimiter
from services.bus import CHAIN_READY, ChainReady, series_label
from services.poller.collector import Collector
from services.poller.context import Series
from services.poller.ready import ReadyNotifier, ReadyTap, series_key
from services.poller.records import ChainRecord
from services.poller.service import PollerService, RawFanout
from services.poller.sink import InMemorySink
from tests.fakes.kis_server import FakeClock, FakeKisServer, default_chain, make_client

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
T0 = datetime(2026, 9, 28, 10, 0, tzinfo=KST)
D = date(2026, 9, 28)
CHAIN = default_chain()


def row(
    cls: str,
    expiry: str,
    *,
    at: float = 0.0,
    source: str = "board",
    trade_date: date = D,
    session: str = "day",
) -> ChainRecord:
    return ChainRecord.model_validate(
        {
            "ts": T0 + timedelta(seconds=at),
            "trade_date": trade_date,
            "session": session,
            "mrkt_cls": cls,
            "expiry": expiry,
            "strike": Decimal("1095.0"),
            "cp": "C",
            "source": source,
            "last": Decimal("5.2"),
            "oi": 10,
        }
    )


class Rig:
    def __init__(self) -> None:
        self.server = fakeredis.FakeServer()
        self.redis = fakeredis.FakeRedis(server=self.server)
        self.sub = self.redis.pubsub(ignore_subscribe_messages=True)
        self.sub.subscribe(CHAIN_READY)
        self.t = [0.0]
        self.ready = ReadyNotifier(self.redis, clock=lambda: self.t[0])

    def messages(self) -> list[ChainReady]:
        return drain(self.sub)


def drain(sub: Any) -> list[ChainReady]:
    """받은 알림 전부 — 구독 확인 메시지는 None 으로 오므로 None 두 번 연속까지 읽는다."""
    out: list[ChainReady] = []
    quiet = 0
    while quiet < 2:
        m = sub.get_message(timeout=0.01)
        if m is None:
            quiet += 1
            continue
        quiet = 0
        out.append(ChainReady.model_validate_json(m["data"]))
    return out


TRACKED = (Series("WKM", "260904"), Series("WKI", "261001"), Series("", "202610"))


# ── 메시지 모델 ──


def test_chain_ready_is_utc_sorted_unique_and_round_trips() -> None:
    msg = ChainReady(
        ts=T0,
        trade_date=D,
        session="day",
        series=(("WKM", "261001"), ("", "202610"), ("WKI", "261001"), ("WKM", "261001")),
    )
    assert msg.ts.tzinfo is UTC and msg.ts == T0
    # WKM·WKI 가 같은 만기값을 쓴다 — 쌍이 키다
    assert msg.series == (("", "202610"), ("WKI", "261001"), ("WKM", "261001"))
    assert ChainReady.model_validate_json(msg.model_dump_json()) == msg
    assert [series_label(k) for k in msg.series] == ["M:202610", "WKI:261001", "WKM:261001"]


@pytest.mark.parametrize(
    "bad",
    [
        {"series": ()},  # 빈 알림은 없다
        {"series": (("XYZ", "261001"),)},  # 모르는 시장분류
        {"series": (("WKM", "2610"),)},  # 6자리 아님
        {"ts": datetime(2026, 9, 28, 1, 0)},  # noqa: DTZ001 — naive 거부
        {"session": "evening"},
        {"extra": 1},
    ],
)
def test_chain_ready_rejects_bad_fields(bad: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "ts": T0,
        "trade_date": D,
        "session": "day",
        "series": (("WKM", "261001"),),
    }
    with pytest.raises(ValidationError):
        ChainReady.model_validate(base | bad)


def test_series_key_accepts_only_known_classes_and_six_digits() -> None:
    assert series_key("", "202610") == ("", "202610")
    assert series_key("WKI", "261001") == ("WKI", "261001")
    assert series_key("KQ", "261001") is None and series_key("WKM", "26100") is None


# ── 사이클 끝 판정 ──


def test_publishes_when_every_tracked_board_arrived() -> None:
    rig = Rig()
    rig.ready.note([row("WKM", "260904", at=1), row("WKI", "261001", at=2)])
    assert rig.ready.flush(TRACKED) is False  # 월물 전광판이 아직
    rig.ready.note([row("", "202610", at=5, source="fill"), row("", "202610", at=4)])
    assert rig.ready.flush(TRACKED) is True
    (msg,) = rig.messages()
    assert msg.ts == T0 + timedelta(seconds=5)  # 모인 행 중 가장 늦은 것
    assert msg.series == (("", "202610"), ("WKI", "261001"), ("WKM", "260904"))
    assert (msg.trade_date, msg.session) == (D, "day")
    assert rig.ready.pending is False and rig.ready.stats.published == 1
    assert rig.ready.flush(TRACKED) is False  # 모인 것이 없다


def test_without_all_boards_it_waits_thirty_seconds_from_the_last_publish() -> None:
    rig = Rig()
    rig.ready.note([row("", "202610", at=0, source="fill")])  # 야간 B 처럼 단건만
    rig.t[0] = 29.9
    assert rig.ready.flush(TRACKED) is False
    rig.t[0] = 30.0
    assert rig.ready.flush(TRACKED) is True
    rig.ready.note([row("", "202610", at=31, source="fill")])
    rig.t[0] = 45.0
    assert rig.ready.flush(TRACKED) is False  # 마지막 발행(30.0) 뒤 30초가 안 됐다
    rig.t[0] = 60.0
    assert rig.ready.flush(None) is True
    assert [m.ts for m in rig.messages()] == [T0, T0 + timedelta(seconds=31)]


def test_a_session_change_publishes_what_was_gathered_first() -> None:
    rig = Rig()
    rig.ready.note([row("WKM", "260904", at=1)])
    night = date(2026, 9, 29)
    rig.ready.note([row("WKI", "261001", at=2, source="fill", trade_date=night, session="night")])
    first = rig.messages()
    assert [(m.trade_date, m.session, m.series) for m in first] == [
        (D, "day", (("WKM", "260904"),))
    ]
    assert rig.ready.flush(force=True) is True
    (second,) = rig.messages()
    assert (second.trade_date, second.session) == (night, "night")


def test_unknown_series_rows_are_not_announced() -> None:
    rig = Rig()
    rig.ready.note([row("KQ", "261001")])
    assert rig.ready.pending is False and rig.ready.flush(force=True) is False


def test_a_redis_failure_is_counted_and_swallowed() -> None:
    rig = Rig()
    rig.ready.note([row("WKM", "260904")])
    rig.server.connected = False
    assert rig.ready.flush(force=True) is False  # 예외 없이
    assert rig.ready.stats.failed == 1 and rig.ready.pending is False
    rig.server.connected = True
    rig.ready.note([row("WKM", "260904", at=40)])
    assert rig.ready.flush(force=True) is True


# ── 싱크 감싸기 ──


class Boom(InMemorySink):
    def write_chain(self, rows: Sequence[ChainRecord]) -> None:
        raise RuntimeError("db down and no spool")


def test_only_rows_handed_to_the_sink_are_announced() -> None:
    rig = Rig()
    tap = ReadyTap(Boom(), rig.ready)
    with pytest.raises(RuntimeError):
        tap.write_chain([row("WKM", "260904")])
    assert rig.ready.pending is False
    ok = ReadyTap(InMemorySink(), rig.ready)
    ok.write_chain([row("WKM", "260904")])
    assert rig.ready.pending is True


def test_a_failing_notifier_does_not_fail_the_write(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig()
    inner = InMemorySink()
    tap = ReadyTap(inner, rig.ready)

    def boom(_rows: object) -> None:
        raise ValueError("bug")

    monkeypatch.setattr(rig.ready, "note", boom)
    tap.write_chain([row("WKM", "260904")])
    assert len(inner.chain) == 1 and rig.ready.stats.note_errors == 1


# ── 서비스 ──


class ServiceRig:
    def __init__(self, start: datetime) -> None:
        self.clock = FakeClock(start)
        self.kis = FakeKisServer(self.clock, CHAIN)
        self.server = fakeredis.FakeServer()
        self.redis = fakeredis.FakeRedis(server=self.server)
        self.sub = self.redis.pubsub(ignore_subscribe_messages=True)
        self.sub.subscribe(CHAIN_READY)
        self.inner = InMemorySink()
        mono = lambda: self.clock.now_us() / 1e6  # noqa: E731
        self.ready = ReadyNotifier(self.redis, clock=mono)
        sink = ReadyTap(RawFanout(self.inner, self.redis), self.ready)
        client = make_client(self.kis, LocalRateLimiter(clock=self.clock))
        self.collector = Collector(
            client, sink, calendar=CAL, clock=self.clock, master=CHAIN.master_rows()
        )
        self.svc = PollerService(self.collector, ready=self.ready)

    def messages(self) -> list[ChainReady]:
        return drain(self.sub)


def test_two_minutes_of_day_collection_announce_cycles_of_the_written_rows() -> None:
    rig = ServiceRig(T0)
    rig.svc.run(threading.Event(), until=T0 + timedelta(minutes=2))
    msgs = rig.messages()
    assert len(msgs) >= 3 and rig.ready.stats.failed == 0
    written = {(r.mrkt_cls, r.expiry) for r in rig.inner.chain}
    assert set().union(*(set(m.series) for m in msgs)) == written
    assert all((m.trade_date, m.session) == (D, "day") for m in msgs)
    ts = [m.ts for m in msgs]
    assert ts == sorted(ts) and ts[-1] <= max(r.ts for r in rig.inner.chain)
    # 사이클마다 추적 시리즈 셋의 전광판 — 전광판 주기(30초)마다 한 번꼴
    boards = {m.ts for m in msgs if len(m.series) >= 3}
    assert len(boards) >= 3


def test_night_b_without_boards_still_announces_every_thirty_seconds() -> None:
    start = datetime(2026, 9, 28, 18, 1, tzinfo=KST)
    rig = ServiceRig(start)
    rig.svc.run(threading.Event(), until=start + timedelta(minutes=2))
    msgs = rig.messages()
    assert {(m.trade_date, m.session) for m in msgs} == {(date(2026, 9, 29), "night")}
    assert 3 <= len(msgs) <= 5
    assert all(r.source == "fill" for r in rig.inner.chain)


def test_a_broken_redis_does_not_stop_collection() -> None:
    rig = ServiceRig(T0)
    rig.server.connected = False
    rig.svc.run(threading.Event(), until=T0 + timedelta(minutes=1))
    assert rig.collector.stats.executed["board"] > 0 and rig.inner.chain
    assert rig.collector.stats.sink_errors == 0
    assert rig.ready.stats.published == 0 and rig.ready.stats.failed > 0

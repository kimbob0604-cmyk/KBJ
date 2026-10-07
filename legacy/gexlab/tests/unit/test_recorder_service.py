"""recorder 서비스 (PLAN §4.1·§4.5, 설계 §2) — Redis ws.raw·rest.raw → raw_messages.

fakeredis 와 가짜 DB(tests/fakes/pg.py)만 쓴다. 실제 Redis·DB 는 부르지 않는다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from data.spool import DiskSpool
from data.store import PostgresSink, StoreError
from services.auth.health import MemoryHealthSink
from services.bus import REST_RAW, WS_RAW, encode_envelope, heartbeat_key
from services.recorder.envelope import Batcher, RawEnvelope
from services.recorder.service import Recorder, run
from services.runtime import Backoff, Heartbeater
from tests.fakes.pg import FakeDb

T0 = datetime(2026, 9, 28, 1, 0, tzinfo=UTC)


def _env(i: int, source: str = "kis_ws", tr_id: str = "H0IFCNT0") -> RawEnvelope:
    return RawEnvelope.model_validate(
        {
            "received_at": T0 + timedelta(milliseconds=i),
            "source": source,
            "tr_id": tr_id,
            "key": "A01612",
            "payload": f"0|{tr_id}|001|A01612^{i:06d}" if source == "kis_ws" else {"i": i},
            "trade_date": date(2026, 9, 28),
            "session": "day",
        }
    )


class MemStore:
    def __init__(self) -> None:
        self.raw: list[RawEnvelope] = []
        self.calls = 0
        self.flushes = 0
        self.reject: Callable[[Sequence[RawEnvelope]], bool] = lambda _: False

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None:
        self.calls += 1
        if self.reject(envelopes):
            raise StoreError("raw_messages: CheckViolation")
        self.raw.extend(envelopes)

    def flush_spool(self) -> bool:
        self.flushes += 1
        return True


class Tick:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _recorder(store: Any, **kw: Any) -> tuple[Recorder, MemoryHealthSink]:
    health = MemoryHealthSink()
    return Recorder(store, health, now=lambda: T0, **kw), health


def test_batches_by_count_and_by_age() -> None:
    store = MemStore()
    clock = Tick()
    rec, _ = _recorder(store, batcher=Batcher(max_items=3, max_age_s=1.0, clock=clock))
    for i in range(4):
        rec.accept(encode_envelope(_env(i)).encode())
    assert [e.payload for e in store.raw] == [_env(i).payload for i in range(3)]
    rec.tick()
    assert len(store.raw) == 3  # 넷째는 아직 1초가 안 됐다
    clock.t = 1.0
    rec.tick()
    assert len(store.raw) == 4 and store.calls == 2
    assert store.raw[3] == _env(3)  # 받은 그대로 (거래일·세션·수신 시각)


def test_bad_messages_are_counted_with_throttled_health() -> None:
    rec, health = _recorder(MemStore())
    rec.accept(b"not json")
    rec.accept(b'{"source":"kis_ws"}')
    assert rec.stats.bad == 2 and health.kinds() == ["recorder_bad_message"]
    assert "not json" not in health.events[0].detail  # 내용은 싣지 않는다


def test_a_rejected_batch_is_retried_one_by_one() -> None:
    store = MemStore()
    store.reject = lambda envs: any(e.tr_id == "BAD" for e in envs)
    rec, health = _recorder(store, batcher=Batcher(max_items=3))
    for env in (_env(0), _env(1, tr_id="BAD"), _env(2)):
        rec.accept(encode_envelope(env))
    assert [e.payload for e in store.raw] == [_env(0).payload, _env(2).payload]
    assert rec.stats.failed == 1 and rec.stats.written == 2
    (ev,) = health.of("recorder_write_failed")
    assert ev.severity == "critical" and "BAD" in ev.detail


def test_db_outage_goes_to_the_disk_spool_and_replays(tmp_path: Path) -> None:
    db = FakeDb(down=True)
    mono = [0.0]
    sink = PostgresSink(
        connect=db,
        service="recorder",
        spool=DiskSpool(tmp_path / "spool"),
        monotonic=lambda: mono[0],
    )
    rec, health = _recorder(sink, batcher=Batcher(max_items=2))
    for i in range(4):
        rec.accept(encode_envelope(_env(i)))
    assert rec.stats.written == 4 and rec.stats.failed == 0 and not health.events
    assert sink.degraded and db.of("raw_messages") == []
    db.down = False
    mono[0] = 60.0
    assert sink.flush_spool()
    assert len(db.of("raw_messages")) == 4


def _run_in_thread(
    rec: Recorder, factory: Callable[[], Any], stop: threading.Event, **kw: Any
) -> threading.Thread:
    t = threading.Thread(target=run, args=(rec, factory, stop), kwargs=kw, daemon=True)
    t.start()
    return t


def _wait(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, "시간 초과"
        time.sleep(0.01)


def test_run_records_both_channels_and_flushes_on_stop() -> None:
    server = fakeredis.FakeServer()
    r = fakeredis.FakeRedis(server=server)
    store = MemStore()
    rec, _ = _recorder(store, batcher=Batcher(max_items=100, max_age_s=60))
    stop = threading.Event()
    hb = Heartbeater(r, "recorder")
    t = _run_in_thread(
        rec, lambda: fakeredis.FakeRedis(server=server), stop, heartbeat=hb, poll_s=0.01
    )
    _wait(lambda: r.pubsub_numsub(WS_RAW)[0][1] == 1)  # type: ignore[index]
    assert r.publish(WS_RAW, encode_envelope(_env(0))) == 1
    assert r.publish(REST_RAW, encode_envelope(_env(1, "kis_rest", "FHPIF05030100"))) == 1
    _wait(lambda: rec.stats.received == 2)
    assert store.raw == []  # 아직 묶음 안
    stop.set()
    t.join(5)
    assert not t.is_alive()
    assert {e.source for e in store.raw} == {"kis_ws", "kis_rest"} and store.flushes >= 1
    assert r.get(heartbeat_key("recorder")) is not None


class FlakyRedis:
    """처음 n 번은 구독하자마자 끊긴다."""

    def __init__(self, server: fakeredis.FakeServer, fails: int) -> None:
        self.server = server
        self.fails = fails
        self.made = 0

    def __call__(self) -> Any:
        self.made += 1
        if self.made <= self.fails:
            raise RedisConnectionError("Connection refused")
        return fakeredis.FakeRedis(server=self.server)


def test_redis_outage_backs_off_and_resubscribes() -> None:
    server = fakeredis.FakeServer()
    r = fakeredis.FakeRedis(server=server)
    store = MemStore()
    rec, health = _recorder(store, batcher=Batcher(max_items=1))
    factory = FlakyRedis(server, fails=2)
    stop = threading.Event()
    t = _run_in_thread(rec, factory, stop, poll_s=0.01, backoff=Backoff(0.01, 0.05))
    _wait(lambda: r.pubsub_numsub(WS_RAW)[0][1] == 1)  # type: ignore[index]
    r.publish(WS_RAW, encode_envelope(_env(7)))
    _wait(lambda: len(store.raw) == 1)
    stop.set()
    t.join(5)
    assert factory.made == 3 and rec.stats.reconnects == 2
    assert health.kinds() == ["recorder_redis_down"]  # 1분에 한 번


@pytest.mark.parametrize("module", ["services.recorder.service"])
def test_entry_point_module_exists(module: str) -> None:
    import importlib.util

    assert importlib.util.find_spec("services.recorder.__main__") is not None
    assert importlib.util.find_spec(module) is not None

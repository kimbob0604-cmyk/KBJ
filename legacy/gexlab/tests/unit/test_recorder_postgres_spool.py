"""recorder + legacy `PostgresSink` 디스크 스풀 — GX `tests/unit/test_recorder_service.py` 에서 남긴 한 건.

KBJ P7 ING: recorder 서비스는 `kbj.services.recorder` 로 옮겼고(시험 6건은 kbj
`tests/unit/recorder/test_recorder_service.py`), 이 시험만 legacy 에 남는다 — legacy GX 싱크
(`data/store.py:PostgresSink`, D-P7-4 로 legacy 에 남는 쓰기)의 스풀을 KBJ `Recorder` 가 그대로 쓰는지
본다(GX 싱크와 KBJ 녹화 서비스의 다리). KBJ 싱크(`SpooledGexStore`)의 같은 시나리오는 kbj
`tests/unit/recorder/test_recorder_store.py`.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from data.spool import DiskSpool
from data.store import PostgresSink
from services.auth.health import MemoryHealthSink
from services.bus import encode_envelope
from services.recorder.envelope import Batcher, RawEnvelope
from services.recorder.service import Recorder
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


def _recorder(store: Any, **kw: Any) -> tuple[Recorder, MemoryHealthSink]:
    health = MemoryHealthSink()
    return Recorder(store, health, now=lambda: T0, **kw), health


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

from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from services.recorder.envelope import Batcher, RawEnvelope, SessionName, wrap

KST = ZoneInfo("Asia/Seoul")


def fake_tagger(ts: datetime) -> tuple[date | None, SessionName | None]:
    k = ts.astimezone(KST)
    return (k.date(), "day") if 8 <= k.hour < 16 else (None, None)


def test_wrap_tags_and_normalises_to_utc() -> None:
    ts = datetime(2026, 9, 28, 10, 0, tzinfo=KST)
    env = wrap("0|H0IFCNT0|001|A01612^...", source="kis_ws", tr_id="H0IFCNT0", received_at=ts,
               tagger=fake_tagger, key="A01612")  # fmt: skip
    assert env.received_at == datetime(2026, 9, 28, 1, 0, tzinfo=UTC)
    assert env.received_at.tzinfo == UTC
    assert (env.trade_date, env.session) == (date(2026, 9, 28), "day")
    assert env.payload.startswith("0|H0IFCNT0")  # type: ignore[union-attr]


def test_wrap_keeps_dict_payload_and_untagged_times() -> None:
    ts = datetime(2026, 9, 28, 3, 0, tzinfo=KST)
    env = wrap({"rt_cd": "0", "output1": []}, source="kis_rest", tr_id="FHPIF05030100",
               received_at=ts, tagger=fake_tagger)  # fmt: skip
    assert env.payload == {"rt_cd": "0", "output1": []}
    assert (env.trade_date, env.session) == (None, None)


def test_naive_time_rejected() -> None:
    with pytest.raises(ValueError, match="naive"):
        wrap("x", source="krx", tr_id="t", received_at=datetime(2026, 9, 28, 10, 0),  # noqa: DTZ001
             tagger=fake_tagger)  # fmt: skip
    with pytest.raises(ValidationError):
        RawEnvelope(received_at=datetime(2026, 9, 28, 10, 0), source="krx", tr_id="t",  # noqa: DTZ001
                    payload="x", trade_date=None, session=None)  # fmt: skip


def test_envelope_is_frozen_and_validates_source() -> None:
    env = wrap("x", source="krx", tr_id="t", received_at=datetime.now(UTC), tagger=fake_tagger)
    with pytest.raises(ValidationError):
        env.tr_id = "u"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RawEnvelope(received_at=datetime.now(UTC), source="bogus", tr_id="t", payload="x",  # type: ignore[arg-type]
                    trade_date=None, session=None)  # fmt: skip


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def env_at(i: int) -> RawEnvelope:
    ts = datetime(2026, 9, 28, 1, 0, tzinfo=UTC) + timedelta(seconds=i)
    return wrap(str(i), source="kis_ws", tr_id="H0IOCNT0", received_at=ts, tagger=fake_tagger)


def test_batcher_flushes_on_count() -> None:
    b = Batcher(max_items=3, max_age_s=10, clock=FakeClock())
    assert b.add(env_at(0)) is None
    assert b.add(env_at(1)) is None
    out = b.add(env_at(2))
    assert out is not None and [e.payload for e in out] == ["0", "1", "2"]
    assert len(b) == 0 and not b.due()


def test_batcher_due_by_age_of_first_item() -> None:
    clk = FakeClock()
    b = Batcher(max_items=100, max_age_s=1.0, clock=clk)
    assert not b.due()
    b.add(env_at(0))
    clk.t += 0.5
    b.add(env_at(1))
    assert not b.due()
    clk.t += 0.5
    assert b.due()
    assert len(b.flush()) == 2 and not b.due()


def test_batcher_rejects_bad_config() -> None:
    with pytest.raises(ValueError):
        Batcher(max_items=0)
    with pytest.raises(ValueError):
        Batcher(max_age_s=0)


def test_other_timezones_accepted() -> None:
    ts = datetime(2026, 9, 28, 20, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert wrap("x", source="krx", tr_id="t", received_at=ts, tagger=fake_tagger).received_at == (
        datetime(2026, 9, 29, 1, 0, tzinfo=UTC)
    )

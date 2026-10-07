"""발송 대기열(kbj.services.notifier.outbox) — fakeredis(Lua).

- 같은 중복 키를 두 번 넣으면 두 번째는 `duplicate`(설계 §1.6), 거절도 기록용으로 스트림에 남는다
- 문지기 키 수명 = 정책 수명, 넣는 일은 원자적(Lua 한 번)
- 첨부는 바이트로 실려 그대로 돌아온다, ack 하면 스트림에서 지워진다
- Redis 오류는 그대로 올라간다(shim 이 사유로 바꾼다)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from kbj.services.notifier.outbox import NotifyTicket, OutboundMessage, Outbox
from kbj.services.notifier.telegram_api import Attachment
from kbj.store.redis_keys import NOTIFY_OUTBOX, notify_dedup_key
from tests.fakes.clock import FakeClock
from tests.unit.notifier.conftest import T0


def _msg(key: str = "brief.morning:20261006", **kw: object) -> OutboundMessage:
    base: dict[str, object] = {
        "kind": "brief.morning",
        "source": "test",
        "requested_at": T0,
        "as_of": date(2026, 10, 6),
        "dedup_key": key,
        "log_key": key,
        "gate_ttl_s": 36 * 3600,
        "body_sha256": "a" * 64,
        "text": "<b>아침</b>",
    }
    base.update(kw)
    return OutboundMessage(**base)  # pyright: ignore[reportArgumentType]


def test_same_key_twice_second_is_duplicate(redis: fakeredis.FakeRedis, clock: FakeClock) -> None:
    box = Outbox(redis, now=clock)
    t1 = box.put(_msg())
    t2 = box.put(_msg(text="다른 본문이어도 키가 같으면"))
    assert t1 == NotifyTicket(True, t1.id, "queued", dedup_key="brief.morning:20261006")
    assert t2.ok and t2.duplicate and t2.reason == "duplicate" and t2.id != t1.id
    entries = box.read("c1", 10)
    assert [e.message is not None for e in entries] == [True, False]
    rej = entries[1].rejected
    assert rej is not None and rej.status == "duplicate" and rej.dedup_key == t1.dedup_key
    assert entries[0].message is not None and entries[0].message.text == "<b>아침</b>"


def test_cooldown_rejection_is_named(redis: fakeredis.FakeRedis, clock: FakeClock) -> None:
    box = Outbox(redis, now=clock)
    box.put(_msg("alert.rule:005930"), reject_status="cooldown")
    t = box.put(_msg("alert.rule:005930"), reject_status="cooldown")
    assert t.duplicate and t.reason == "cooldown"


def test_gate_ttl_follows_the_policy(redis: fakeredis.FakeRedis, clock: FakeClock) -> None:
    Outbox(redis, now=clock).put(_msg(gate_ttl_s=600))
    ttl = redis.pttl(notify_dedup_key("brief.morning:20261006"))
    assert isinstance(ttl, int) and 590_000 < ttl <= 600_000


def test_release_lets_the_same_key_in_again(redis: fakeredis.FakeRedis, clock: FakeClock) -> None:
    box = Outbox(redis, now=clock)
    box.put(_msg())
    box.release("brief.morning:20261006")
    assert not box.put(_msg()).duplicate


def test_attachments_round_trip_as_bytes(redis: fakeredis.FakeRedis, clock: FakeClock) -> None:
    box = Outbox(redis, now=clock)
    doc = Attachment("rankings-2026-10-06.xlsx", b"PK\x03\x04\x00\xff")
    box.put(_msg("board.files:x", kind="board.files", text=None, caption="엑셀", document=doc))
    photos = (Attachment("a.png", b"\x89PNG1"), Attachment("b.png", b"\x89PNG2"))
    box.put(_msg("flows.report:y", kind="flows.report", text=None, media=photos))
    got = [e.message for e in box.read("c", 10)]
    assert got[0] is not None and got[0].document == doc and got[0].caption == "엑셀"
    assert got[1] is not None and got[1].media == photos and got[1].document is None


def test_meta_round_trip(redis: fakeredis.FakeRedis, clock: FakeClock) -> None:
    box = Outbox(redis, now=clock)
    m = _msg(subject="u77", chat="-100", thread_id=5, parse_mode=None, silent=True, numbered=True)
    box.put(m)
    e = box.read("c", 1)[0]
    assert e.message == m


def test_ack_removes_the_entry_and_pending_comes_back(
    redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    box = Outbox(redis, now=clock)
    box.put(_msg("k1"))
    box.put(_msg("k2"))
    first = box.read("c", 10)
    assert len(first) == 2 and box.backlog() == 2
    box.ack(first[0].id)
    again = box.read("c", 10)  # ack 안 한 것은 같은 소비자에게 다시
    assert [e.id for e in again] == [first[1].id]
    box.ack(first[1].id)
    assert box.read("c", 10) == [] and box.backlog() == 0


def test_entries_queued_before_the_group_exists_are_read(
    redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    Outbox(redis, now=clock).put(_msg())  # 부른 쪽이 notifier 보다 먼저 넣음
    assert len(Outbox(redis, now=clock).read("notifier", 5)) == 1


def test_broken_entry_is_reported_not_raised(redis: fakeredis.FakeRedis, clock: FakeClock) -> None:
    redis.xadd(NOTIFY_OUTBOX, {"v": "1", "type": "message", "meta": "{깨진"})
    e = Outbox(redis, now=clock).read("c", 1)[0]
    assert e.message is None and e.error is not None and "형식" in e.error


def test_redis_errors_propagate(clock: FakeClock) -> None:
    server = fakeredis.FakeServer()
    box = Outbox(fakeredis.FakeRedis(server=server), now=clock)
    server.connected = False
    with pytest.raises(RedisConnectionError):
        box.put(_msg())


@pytest.mark.parametrize(
    "bad",
    [
        {"text": None},
        {"requested_at": datetime(2026, 10, 6, 8, 10)},  # noqa: DTZ001 — naive 는 거절
        {"gate_ttl_s": 0},
        {"document": Attachment("a", b"1"), "media": (Attachment("b", b"2"),)},
    ],
)
def test_message_shape_is_checked(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _msg(**bad)


def test_gate_expiry_follows_the_injected_clock(
    redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    """만료 판정은 주입한 시계로(가짜 시계 시뮬레이션에서도 쿨다운이 풀린다)."""
    box = Outbox(redis, now=clock)
    box.put(_msg("alert.rule:005930", gate_ttl_s=600))
    clock.advance(599)
    assert box.put(_msg("alert.rule:005930", gate_ttl_s=600)).duplicate
    clock.advance(1)
    assert not box.put(_msg("alert.rule:005930", gate_ttl_s=600)).duplicate

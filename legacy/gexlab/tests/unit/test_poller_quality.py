"""poller 레코드·싱크·품질 판정 (설계 §5: 90초 stale, 보강 2 는 자기 순환, 대체값만 estimated)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from services.poller.quality import ChainBook, judge_quality
from services.poller.records import ChainRecord, HealthEvent
from services.poller.sink import InMemorySink, Sink

KST = ZoneInfo("Asia/Seoul")
T = datetime(2026, 9, 28, 10, 0, tzinfo=KST)


def rec(
    ts: datetime = T,
    *,
    source: str = "board",
    session: str = "day",
    quality: str = "ok",
    strike: str = "1095.00",
) -> ChainRecord:
    return ChainRecord.model_validate(
        {
            "ts": ts,
            "trade_date": date(2026, 9, 28),
            "session": session,
            "mrkt_cls": "WKM",
            "expiry": "260904",
            "strike": Decimal(strike),
            "cp": "C",
            "source": source,
            "quality": quality,
        }
    )


def test_record_times_are_utc_and_naive_rejected() -> None:
    r = rec()
    assert r.ts == T and r.ts.tzinfo == UTC
    with pytest.raises(ValidationError):
        rec(datetime(2026, 9, 28, 10, 0))  # noqa: DTZ001
    with pytest.raises(ValidationError):
        ChainRecord.model_validate(rec().model_dump() | {"expiry": "0904"})  # 6자리만


def test_board_and_fill1_rows_ok_within_90s_then_stale() -> None:
    r = rec()
    assert judge_quality(r, T + timedelta(seconds=90), 90, "day") == "ok"
    assert judge_quality(r, T + timedelta(seconds=90, microseconds=1), 90, "day") == "stale"


def test_fill2_rows_judged_by_their_own_cycle() -> None:
    r = rec(source="fill")
    cycle = 444 / 1.0 + 60  # 대상 444건 ÷ 1건/초 + 여유
    assert judge_quality(r, T + timedelta(seconds=400), cycle, "day") == "ok"
    assert judge_quality(r, T + timedelta(seconds=505), cycle, "day") == "stale"


def test_previous_session_rows_are_estimated_and_invalid_stays() -> None:
    day_row = rec()
    night = T + timedelta(hours=9)
    assert judge_quality(day_row, night, 90, "night") == "estimated"
    assert judge_quality(day_row, night, 90, None) == "stale"
    assert judge_quality(rec(quality="invalid"), T, 90, "day") == "invalid"


def test_book_keeps_latest_per_key_and_source() -> None:
    book = ChainBook()
    book.update(rec(T), 90)
    book.update(rec(T + timedelta(seconds=30)), 90)
    book.update(rec(T + timedelta(seconds=10)), 90)  # 늦게 온 옛 값은 덮지 않는다
    book.update(rec(T, source="fill"), 150)
    assert len(book) == 2
    got = book.get("WKM", "260904", Decimal("1095.0"), "C", "board")
    assert got is not None and got.ts == T + timedelta(seconds=30)
    view = {r.source: r.quality for r in book.view(T + timedelta(seconds=120), "day")}
    assert view == {"board": "ok", "fill": "ok"}
    view = {r.source: r.quality for r in book.view(T + timedelta(seconds=151), "day")}
    assert view == {"board": "stale", "fill": "stale"}


def test_in_memory_sink_satisfies_protocol() -> None:
    sink: Sink = InMemorySink()
    sink.write_chain([rec()])
    sink.write_health(
        [
            HealthEvent(
                ts=T,
                trade_date=None,
                session=None,
                kind="board_coverage",
                level="warning",
                message="x",
            )
        ]
    )
    assert isinstance(sink, InMemorySink)
    assert len(sink.chain) == 1 and sink.health_kinds() == ["board_coverage"]

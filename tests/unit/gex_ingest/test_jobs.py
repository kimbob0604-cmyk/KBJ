"""P9 GXI 등록부 처리기 — `gex_minutes:run`·`gex_krx:run`·`gex_open_check:run`·`kis_master:run`.

GX scheduler 의 구동 클래스(분봉·무결측·KRX·개장 확인)를 등록부 실행 한 번에 한 step 씩 돌리고
결과를 `JobResult` 로 옮기는지만 본다(구동 규칙 자체는 승격한 test_scheduler_* 가 본다). 마스터
갱신은 GX `_master_step` 의 규칙(옵션 없는 마스터·zip 오류는 직전 마스터 유지, DB 실패는 Redis 에
둔다)을 본다. 가짜 KIS·가짜 KRX·fakeredis·메모리 저장소만 — 네트워크·DB 없음.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import pytest

from kbj.core.calendar import TradingCalendar
from kbj.core.gex.records import SessionLogRecord
from kbj.data.private.kis.master import MasterRow
from kbj.data.ratelimit import LocalRateLimiter
from kbj.services.collectors import gex_krx, gex_minutes, gex_open_check, kis_master
from kbj.services.collectors.gex_resources import GexResources, inline_submit
from kbj.services.runtime.bus import MASTER_KEY, MASTER_SHA_KEY, MasterSnapshot
from kbj.services.runtime.health import MemoryHealthSink
from kbj.services.scheduler.handlers import JobContext
from kbj.store.db import StoreError
from tests.unit.gex_ingest.fakes.gap_inputs import MemoryGapStore
from tests.unit.gex_ingest.fakes.kis_server import (
    FakeClock,
    FakeKisServer,
    MinuteMemoryStore,
    day_bar_times,
    default_chain,
    make_client,
)
from tests.unit.gex_ingest.fakes.krx_server import FakeKrx, KrxMemoryStore

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
D23, D28, D29 = date(2026, 9, 23), date(2026, 9, 28), date(2026, 9, 29)
SAT = date(2026, 9, 26)
CODE = "A01612"


def kst(d: date, h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime.combine(d, time(h, m, s), tzinfo=KST)


def ctx(job: str, now: datetime, **resources: Any) -> JobContext:
    resources.setdefault("kr", CAL)
    resources.setdefault("clock", lambda: now)
    resources.setdefault("redis", fakeredis.FakeRedis())
    resources.setdefault("health", MemoryHealthSink())
    return JobContext(
        job=job, as_of=now.date().isoformat(), run_id="r1", attempt=1, now=now, resources=resources
    )


def snapshot() -> MasterSnapshot:
    text = "\n".join(default_chain().master_lines()) + "\n"
    return MasterSnapshot(
        asof=kst(D28, 8),
        trade_date=D28,
        session="day",
        rows=text.count("\n"),
        sha256=MasterSnapshot.digest(text),
        text=text,
    )


def test_inline_submit_returns_the_result_or_the_error_in_a_future() -> None:
    assert inline_submit(lambda: 3).result() == 3

    def boom() -> int:
        raise RuntimeError("x")

    fut = inline_submit(boom)
    assert fut.done() and isinstance(fut.exception(), RuntimeError)


def test_resources_read_the_master_from_redis_and_ignore_a_broken_one() -> None:
    redis = fakeredis.FakeRedis()
    c = ctx("gex.day_minutes", kst(D28, 16), redis=redis)
    assert GexResources(c).master() is None
    snap = snapshot()
    redis.set(MASTER_KEY, snap.model_dump_json())
    got = GexResources(c).master()
    assert got is not None and got.sha256 == snap.sha256
    redis.set(MASTER_KEY, b"{not json")
    assert GexResources(c).master() is None


def test_resources_without_settings_fail_loudly() -> None:
    c = ctx("gex.day_minutes", kst(D28, 16))
    res = GexResources(c)
    with pytest.raises(RuntimeError, match="KBJ_DATABASE_URL"):
        res.store()
    with pytest.raises(RuntimeError, match="KBJ_KRX_API_KEY"):
        res.krx_client()
    with pytest.raises(RuntimeError, match="KBJ_KIS"), res.kis_client():
        pass


# ── 분봉(gex.day_minutes·gex.night_minutes) ──


class MinuteJobStore(MinuteMemoryStore):
    """분봉 쓰기 + 무결측 판정 읽기·교체 — 작업 하나가 함께 쓴다."""

    def __init__(self) -> None:
        super().__init__()
        self.gap = MemoryGapStore()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.gap, name)


def minute_ctx(job: str, now: datetime, store: MinuteJobStore, **kw: Any) -> JobContext:
    clock = FakeClock(now)
    server = kw.pop("server", None) or FakeKisServer(clock, default_chain(), CAL)
    kis = make_client(server, LocalRateLimiter(clock=clock))
    kw.setdefault("master", snapshot())
    return ctx(job, now, gex_store=store, kis=kis, server=server, **kw)


def test_day_minutes_load_the_session_and_judge_it_in_one_run() -> None:
    store = MinuteJobStore()
    c = minute_ctx("gex.day_minutes", kst(D28, 16), store)
    c.resources["server"].add_minute_bars(CODE, "F", day_bar_times(D28))
    res = gex_minutes.run(c)
    assert res.status == "ok", res.detail
    assert res.detail["session"] == "day" and res.detail["trade_date"] == "2026-09-28"
    assert res.rows == 411 and len(store.of(CODE, "F")) == 411
    assert store.gap.replaced == [(D28, "day")]  # 무결측 판정까지 같은 실행에서
    kinds = c.resources["health"].kinds()
    assert kinds[0] == "minute_bars_loaded"


def test_minutes_outside_the_window_or_on_a_closed_night_are_skipped() -> None:
    for at in (kst(D28, 12), kst(SAT, 16), kst(D28, 6, 30)):  # 09-23 밤은 추석 전날 — 없다
        store = MinuteJobStore()
        res = gex_minutes.run(minute_ctx("gex.night_minutes", at, store))
        assert res.status == "skipped" and store.bars == {}


def test_minutes_without_a_master_fail_so_the_registry_retries() -> None:
    store = MinuteJobStore()
    c = minute_ctx("gex.day_minutes", kst(D28, 16), store, master=None)
    res = gex_minutes.run(c)
    assert res.status == "failed" and "error" in res.detail
    assert c.resources["health"].kinds() == ["minute_bars_failed"]
    assert store.gap.replaced == []


def test_a_failed_gap_report_fails_the_run() -> None:
    store = MinuteJobStore()
    store.gap.fail_writes = 1
    c = minute_ctx("gex.day_minutes", kst(D28, 16), store)
    c.resources["server"].add_minute_bars(CODE, "F", day_bar_times(D28))
    res = gex_minutes.run(c)
    assert res.status == "failed" and res.detail["error"].startswith("무결측 판정 실패")


# ── KRX 전일(gex.krx_derivatives) ──


def krx_ctx(now: datetime, fake: FakeKrx, store: KrxMemoryStore) -> JobContext:
    return ctx("gex.krx_derivatives", now, krx=fake.client(), gex_store=store)


def test_krx_loads_the_previous_trading_day() -> None:
    fake, store = FakeKrx(), KrxMemoryStore()
    fake.publish(D23)
    res = gex_krx.run(krx_ctx(kst(D28, 8, 5), fake, store))
    assert res.status == "ok" and res.rows == 16 and res.detail["krx_date"] == "2026-09-23"
    assert len(store.options) == 13 and len(store.futures) == 3


def test_krx_not_yet_published_is_not_ready_and_errors_are_failed() -> None:
    fake, store = FakeKrx(), KrxMemoryStore()
    assert gex_krx.run(krx_ctx(kst(D28, 8, 5), fake, store)).status == "not_ready"
    fake.publish(D23)
    store.fail_write = True
    assert gex_krx.run(krx_ctx(kst(D28, 8, 15), fake, store)).status == "failed"


def test_krx_before_0805_or_on_a_holiday_is_skipped() -> None:
    fake, store = FakeKrx(), KrxMemoryStore()
    for at in (kst(D28, 8), kst(SAT, 9)):
        assert gex_krx.run(krx_ctx(at, fake, store)).status == "skipped"
    assert fake.calls() == 0


# ── 개장 확인(gex.open_check_day·gex.open_check_night) ──


class OpenStore:
    def __init__(self, count: int, fail: bool = False) -> None:
        self.count, self.fail = count, fail
        self.logged: list[SessionLogRecord] = []

    def fut_tick_count(self, start: datetime, end: datetime) -> int:
        if self.fail:
            raise StoreError("fut_ticks: OperationalError: connection refused")
        return self.count

    def write_session_log(self, rows: Sequence[SessionLogRecord]) -> None:
        self.logged.extend(rows)


def test_open_check_counts_the_first_trades() -> None:
    store = OpenStore(12)
    res = gex_open_check.run(ctx("gex.open_check_day", kst(D28, 8, 49), gex_store=store))
    assert res.status == "ok" and res.detail["calendar_mismatch"] == 0
    assert res.detail["trades"] == {"day:2026-09-28": 12}


def test_open_check_without_trades_is_a_calendar_mismatch() -> None:
    store, health = OpenStore(0), MemoryHealthSink()
    c = ctx("gex.open_check_night", kst(D28, 18, 4), gex_store=store, health=health)
    res = gex_open_check.run(c)
    assert res.status == "ok" and res.rows == 1 and res.detail["calendar_mismatch"] == 1
    assert "calendar_mismatch" in health.kinds() and store.logged


def test_open_check_read_failure_fails_and_too_early_is_skipped() -> None:
    bad = gex_open_check.run(
        ctx("gex.open_check_day", kst(D28, 8, 49), gex_store=OpenStore(1, True))
    )
    assert bad.status == "failed"
    early = gex_open_check.run(ctx("gex.open_check_day", kst(D28, 8, 46), gex_store=OpenStore(1)))
    assert early.status == "skipped"


# ── 마스터(kis.master·kis.master_night) ──


class MasterStore:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.writes: list[tuple[int, date, str]] = []

    def write_master(
        self, rows: Sequence[MasterRow], *, ts: datetime, trade_date: date, session: str
    ) -> None:
        if self.fail:
            raise StoreError("master_snapshots: OperationalError: connection refused")
        self.writes.append((len(rows), trade_date, session))


def master_zip(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fo_idx_code_mts.mst", ("\n".join(lines) + "\n").encode("cp949"))
    return buf.getvalue()


def master_ctx(job: str, now: datetime, data: bytes | Exception, **kw: Any) -> JobContext:
    def download() -> bytes:
        if isinstance(data, Exception):
            raise data
        return data

    kw.setdefault("gex_store", MasterStore())
    return ctx(job, now, master_downloader=download, **kw)


def test_master_refresh_writes_redis_and_the_session_snapshot() -> None:
    lines = default_chain().master_lines()
    redis = fakeredis.FakeRedis()
    store = MasterStore()
    c = master_ctx("kis.master", kst(D28, 8, 30), master_zip(lines), redis=redis, gex_store=store)
    res = kis_master.run(c)
    assert res.status == "ok" and res.rows == len(lines)
    assert res.detail["trade_date"] == "2026-09-28" and res.detail["session"] == "day"
    snap = MasterSnapshot.model_validate_json(redis.get(MASTER_KEY) or b"")
    assert redis.get(MASTER_SHA_KEY) == snap.sha256.encode()
    assert store.writes == [(len(lines), D28, "day")]
    assert c.resources["health"].kinds() == ["master_refreshed"]
    night = master_ctx("kis.master_night", kst(D28, 17, 55), master_zip(lines), gex_store=store)
    assert kis_master.run(night).status == "ok"
    assert store.writes[-1] == (len(lines), D29, "night")  # 그 밤은 다음 거래일 귀속


@pytest.mark.parametrize(
    "data",
    [
        b"not a zip",
        master_zip(["1|A01612|KR4A01612|F 202612| |00000.00|1|2001|KOSPI200"]),  # 옵션 없음
        ConnectionError("down"),
    ],
)
def test_a_bad_master_keeps_the_previous_one(data: bytes | Exception) -> None:
    redis = fakeredis.FakeRedis()
    redis.set(MASTER_KEY, b"previous")
    c = master_ctx("kis.master", kst(D28, 8, 30), data, redis=redis)
    res = kis_master.run(c)
    assert res.status == "failed" and "직전 마스터" in res.detail["error"]
    assert redis.get(MASTER_KEY) == b"previous"
    assert c.resources["health"].kinds() == ["master_refresh_failed"]


def test_a_db_failure_still_updates_redis() -> None:
    redis = fakeredis.FakeRedis()
    data = master_zip(default_chain().master_lines())
    c = master_ctx("kis.master", kst(D28, 8, 30), data, redis=redis, gex_store=MasterStore(True))
    assert kis_master.run(c).status == "ok" and redis.get(MASTER_KEY) is not None
    assert c.resources["health"].kinds() == ["master_store_failed", "master_refreshed"]


def test_a_redis_failure_fails_the_refresh() -> None:
    class DownRedis:
        def pipeline(self) -> Any:
            import redis.exceptions

            raise redis.exceptions.ConnectionError("down")

    data = master_zip(default_chain().master_lines())
    c = master_ctx("kis.master", kst(D28, 8, 30), data, redis=DownRedis())
    res = kis_master.run(c)
    assert res.status == "failed" and "Redis" in res.detail["error"]


def test_each_registry_job_has_its_own_owner_name_over_the_same_body() -> None:
    """등록부는 작업마다 owner 하나 — 주간·야간 이름은 같은 본문(세션은 시각으로 정한다)."""
    for fn in (gex_minutes.run_day, gex_minutes.run_night):
        res = fn(minute_ctx("gex.day_minutes", kst(D28, 12), MinuteJobStore()))
        assert res.status == "skipped"
    for fn in (gex_open_check.run_day, gex_open_check.run_night):
        c = ctx("gex.open_check_day", kst(D28, 8, 46), gex_store=OpenStore(1))
        assert fn(c).status == "skipped"
    data = master_zip(default_chain().master_lines())
    night = master_ctx("kis.master_night", kst(D28, 17, 55), data)
    assert kis_master.run_night(night).detail["session"] == "night"

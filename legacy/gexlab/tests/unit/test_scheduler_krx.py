"""scheduler KRX 전일 적재 — 한 번의 시도(KrxLoader)·KST 하루 호출 상한(KrxCallBudget)·하루
구동(KrxDaily: 전 거래일 선택·08:05 시작·10분 재시도·10:00 마감·상한·재기동·작업 스레드)·마스터 ⊇
KRX 대조를 언제 하는지(대조 규칙 자체는 tests/unit/test_master_check.py).

가짜 KRX(tests/fakes/krx_server.py, httpx.MockTransport + 원본 발췌 fixture)·fakeredis·메모리
저장소만 쓴다 — 네트워크·DB 없음. 실제 DB 쓰기는 tests/integration/test_scheduler_krx.py.
"""

from __future__ import annotations

import logging
import threading
import time as time_mod
from collections.abc import Callable
from concurrent.futures import Future
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import pytest
from pydantic import ValidationError

from config.settings import Settings
from core.calendar import TradingCalendar
from data.krx.eod import FUT_DAILY, OPT_DAILY
from services.auth.health import MemoryHealthSink
from services.bus import MasterSnapshot, krx_calls_key
from services.runtime import tagger_for
from services.scheduler.krx import (
    KindResult,
    KrxCallBudget,
    KrxCapReached,
    KrxDaily,
    KrxLoader,
    KrxLoadResult,
    krx_thread_submit,
)
from tests.fakes.kis_server import default_chain
from tests.fakes.krx_server import KEY, FakeKrx, KrxMemoryStore, fixture_rows

KST = ZoneInfo("Asia/Seoul")
D23 = date(2026, 9, 23)
D28 = date(2026, 9, 28)
AT = datetime(2026, 9, 28, 8, 5, tzinfo=KST)


class Now:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def rig(
    fake: FakeKrx | None = None,
    store: KrxMemoryStore | None = None,
    *,
    cap: int = 200,
    redis: Any = None,
    client: Any = None,
) -> tuple[KrxLoader, FakeKrx, KrxMemoryStore, KrxCallBudget]:
    fake = fake or FakeKrx()
    store = store or KrxMemoryStore()
    budget = KrxCallBudget(redis if redis is not None else fakeredis.FakeRedis(), cap)
    loader = KrxLoader(client or fake.client(), store, budget, now=Now(AT))
    return loader, fake, store, budget


# ── 호출 수 ──


def test_budget_counts_per_kst_day_and_survives_a_restart() -> None:
    r = fakeredis.FakeRedis()
    b = KrxCallBudget(r, cap=5)
    late = datetime(2026, 9, 28, 23, 59, tzinfo=KST)
    assert [b.take(late), b.take(late)] == [1, 2]
    midnight = datetime(2026, 9, 29, 0, 0, tzinfo=KST)  # UTC 로는 아직 09-28 15:00
    assert b.take(midnight) == 1 and b.used(midnight) == 1
    assert r.get(krx_calls_key(D28)) == b"2"
    ttl = r.ttl(krx_calls_key(D28))
    assert isinstance(ttl, int) and 0 < ttl <= 3 * 86400
    again = KrxCallBudget(r, cap=5)  # 재기동 — Redis 셈을 이어 받는다
    assert again.used(late) == 2 and again.take(late) == 3


def test_budget_hard_stops_at_the_cap_without_counting() -> None:
    r = fakeredis.FakeRedis()
    b = KrxCallBudget(r, cap=2)
    b.take(AT)
    b.take(AT)
    with pytest.raises(KrxCapReached, match="2/2"):
        b.take(AT)
    assert b.used(AT) == 2 and r.get(krx_calls_key(D28)) == b"2"
    with pytest.raises(ValueError):
        KrxCallBudget(r, cap=0)


def test_budget_keeps_its_own_count_when_redis_is_down(caplog: pytest.LogCaptureFixture) -> None:
    server = fakeredis.FakeServer()
    b = KrxCallBudget(fakeredis.FakeRedis(server=server), cap=2)
    server.connected = False
    with caplog.at_level(logging.WARNING, logger="services.scheduler.krx"):
        assert [b.take(AT), b.take(AT)] == [1, 2]
        with pytest.raises(KrxCapReached):
            b.take(AT)
    failed = [r for r in caplog.records if "krx_calls_redis_failed" in r.getMessage()]
    assert len(failed) == 1  # 한 번만 알린다
    server.connected = True
    assert b.used(AT) == 2  # 이 프로세스 셈이 Redis(0)보다 크다


def test_the_cap_setting_defaults_to_200_and_must_be_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KRX_DAILY_CALL_CAP", raising=False)
    assert Settings(_env_file=None).krx_daily_call_cap == 200  # pyright: ignore[reportCallIssue]
    monkeypatch.setenv("KRX_DAILY_CALL_CAP", "30")
    assert Settings(_env_file=None).krx_daily_call_cap == 30  # pyright: ignore[reportCallIssue]
    monkeypatch.setenv("KRX_DAILY_CALL_CAP", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_budget_rejects_naive_datetimes() -> None:
    b = KrxCallBudget(None, cap=1)
    with pytest.raises(ValueError, match="naive"):
        b.take(datetime(2026, 9, 28, 8, 5))  # noqa: DTZ001 — 일부러 naive


# ── 한 번의 적재 시도 ──


def test_loads_the_kospi200_rows_of_both_kinds_for_that_day() -> None:
    loader, fake, store, budget = rig()
    fake.publish(D23)
    res = loader.load(D23)
    assert res.done and res.trade_date == D23 and res.calls_today == 2
    opt, fut = res.of("options"), res.of("futures")
    assert (opt.status, opt.rows, opt.received, opt.invalid) == ("loaded", 13, 18, 0)
    assert (fut.status, fut.rows, fut.received) == ("loaded", 3, 3)
    assert fake.requested() == [(OPT_DAILY, "20260923"), (FUT_DAILY, "20260923")]
    assert {k[0] for k in store.options} == {D23} and len(store.options) == 13
    night = [r for (_, _, s), r in store.options.items() if s == "night"]
    assert len(night) == 6 and all(r.imp_volt is None for r in night)  # 야간 '0.00' → NULL
    assert {r.family for r in store.options.values()} == {
        "kospi200",
        "mini_kospi200",
        "kospi200_weekly_thu",
        "kospi200_weekly_mon",
    }
    assert [w[2] for w in store.writes] == [AT, AT]  # 받은 시각
    assert "옵션 13행(받은 18)" in res.describe() and budget.used(AT) == 2


def test_reloading_a_loaded_day_calls_nothing_even_after_a_restart() -> None:
    loader, fake, store, budget = rig()
    fake.publish(D23)
    loader.load(D23)
    again = loader.load(D23)
    assert [r.status for r in again.results] == ["present", "present"] and again.done
    restarted = KrxLoader(fake.client(), store, budget, now=Now(AT))  # 새 프로세스 — DB 를 본다
    res = restarted.load(D23)
    assert [r.status for r in res.results] == ["present", "present"]
    assert fake.calls() == 2 and budget.used(AT) == 2
    assert len(store.options) == 13 and len(store.futures) == 3
    assert [w[:2] for w in store.writes] == [("options", 13), ("futures", 3)]


def test_an_empty_answer_is_not_refreshed_yet() -> None:
    loader, fake, store, budget = rig(FakeKrx(stale="empty"))
    res = loader.load(D28)
    assert [(r.status, r.detail) for r in res.results] == [("not_refreshed", "빈 응답")] * 2
    assert not res.done and fake.calls() == 2 and budget.used(AT) == 2  # 부른 것은 센다
    assert store.writes == []


def test_rows_of_the_previous_day_are_not_refreshed_yet() -> None:
    loader, fake, store, _ = rig(FakeKrx(stale="previous"))
    fake.publish(D23)
    res = loader.load(D28)
    opt = res.of("options")
    assert opt.status == "not_refreshed" and opt.received == 18
    assert opt.detail == "BAS_DD 20260923 18행"
    assert store.writes == [] and not store.options


def test_rows_of_other_days_in_the_answer_are_dropped() -> None:
    loader, fake, store, _ = rig()
    mixed = fixture_rows(OPT_DAILY, D23) + fixture_rows(OPT_DAILY, date(2026, 9, 22))[:2]
    fake.publish(D23, options=mixed)
    opt = loader.load(D23).of("options")
    assert (opt.status, opt.rows, opt.received) == ("loaded", 13, 18)
    assert "다른 BAS_DD 2행 버림" in opt.detail
    assert {k[0] for k in store.options} == {D23}


def test_one_kind_failing_does_not_stop_the_other_and_only_it_is_retried() -> None:
    loader, fake, _, _ = rig()
    fake.publish(D23)
    fake.fail[FUT_DAILY] = 500
    res = loader.load(D23)
    assert res.of("options").status == "loaded" and res.of("futures").status == "failed"
    assert "HTTP 500" in res.of("futures").detail and not res.done
    del fake.fail[FUT_DAILY]
    res = loader.load(D23)
    assert [r.status for r in res.results] == ["present", "loaded"] and res.done
    assert fake.requested() == [
        (OPT_DAILY, "20260923"),
        (FUT_DAILY, "20260923"),
        (FUT_DAILY, "20260923"),
    ]


def test_the_key_never_reaches_the_result_or_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    loader, fake, _, _ = rig()
    fake.fail[OPT_DAILY] = 401  # 가짜 KRX 는 받은 인증키를 본문에 되풀이한다
    fake.publish(D23)
    with caplog.at_level(logging.INFO):
        res = loader.load(D23)
    opt = res.of("options")
    assert opt.status == "failed" and "HTTP 401" in opt.detail and "***" in opt.detail
    assert KEY not in res.describe() and KEY not in repr(res)
    assert caplog.records and all(KEY not in r.getMessage() for r in caplog.records)
    assert KEY not in repr(loader) and KEY not in repr(fake.client())


def test_the_daily_cap_stops_calls() -> None:
    loader, fake, store, budget = rig(cap=1)
    fake.publish(D23)
    res = loader.load(D23)
    assert res.of("options").status == "loaded"
    fut = res.of("futures")
    assert fut.status == "capped" and "1/1" in fut.detail and res.has("capped")
    assert fake.calls() == 1 and budget.used(AT) == 1 and not store.futures


def test_a_db_that_cannot_be_read_skips_the_call() -> None:
    loader, fake, store, budget = rig()
    fake.publish(D23)
    store.fail_read = True
    res = loader.load(D23)
    assert [(r.status, r.detail) for r in res.results] == [
        ("failed", "DB 확인 실패 — StoreError")
    ] * 2
    assert fake.calls() == 0 and budget.used(AT) == 0


def test_a_failed_write_is_failed_and_called_again_next_time() -> None:
    loader, fake, store, _ = rig()
    fake.publish(D23)
    store.fail_write = True
    res = loader.load(D23)
    opt = res.of("options")
    assert opt.status == "failed" and opt.detail.startswith("저장 실패 — StoreError")
    store.fail_write = False
    assert loader.load(D23).done
    assert fake.calls(OPT_DAILY) == 2 and len(store.options) == 13


def test_invalid_rows_are_dropped_and_counted() -> None:
    loader, fake, store, _ = rig()
    rows = fixture_rows(OPT_DAILY, D23)
    rows.append(rows[0] | {"ISU_NM": "코스피200 X 202610 1,100.0 (정규)", "ISU_CD": "BAD"})
    fake.publish(D23, options=rows)
    opt = loader.load(D23).of("options")
    assert (opt.status, opt.rows, opt.received, opt.invalid) == ("loaded", 13, 19, 1)
    assert "검증 실패 1행(첫 18번" in opt.detail and "BAD" not in {k[1] for k in store.options}


def test_a_day_without_kospi200_rows_is_failed() -> None:
    loader, fake, store, _ = rig()
    kosdaq = [r for r in fixture_rows(OPT_DAILY, D23) if r["PROD_NM"].startswith("코스닥")]
    fake.publish(D23, options=kosdaq)
    opt = loader.load(D23).of("options")
    assert opt.status == "failed" and opt.detail.startswith("코스피200 계열 행이 없다")
    assert not store.options


class Boom:
    """옵션만 뜻밖의 오류 — 한 종류의 오류가 다른 종류를 막지 않는지."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def daily(self, endpoint: str, bas_dd: date) -> list[dict[str, Any]]:
        if endpoint == OPT_DAILY:
            raise RuntimeError("boom")
        return self.inner.daily(endpoint, bas_dd)


def test_an_unexpected_error_in_one_kind_is_isolated(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeKrx()
    fake.publish(D23)
    loader, _, _, _ = rig(fake, client=Boom(fake.client()))
    with caplog.at_level(logging.WARNING, logger="services.scheduler.krx"):
        res = loader.load(D23)
    assert (res.of("options").status, res.of("options").detail) == ("failed", "RuntimeError")
    assert res.of("futures").status == "loaded"
    assert any('"status": "failed"' in r.getMessage() for r in caplog.records)


def test_each_kind_logs_one_structured_line(caplog: pytest.LogCaptureFixture) -> None:
    loader, fake, _, _ = rig()
    fake.publish(D23)
    with caplog.at_level(logging.INFO, logger="services.scheduler.krx"):
        loader.load(D23)
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 2
    assert all('"event": "krx_daily"' in m and '"krx_date": "2026-09-23"' in m for m in lines)
    assert '"kind": "options"' in lines[0] and '"rows": 13' in lines[0]


def test_calls_today_is_the_count_after_the_attempt() -> None:
    r = fakeredis.FakeRedis()
    r.set(krx_calls_key(D28), 7)  # 앞선 시도(재기동 전)
    loader, fake, _, _ = rig(redis=r)
    fake.publish(D23)
    assert loader.load(D23).calls_today == 9
    later = AT + timedelta(days=1)  # 다음 날은 새 셈
    fake2 = FakeKrx()
    fake2.publish(D28)
    loader3 = KrxLoader(fake2.client(), KrxMemoryStore(), KrxCallBudget(r, 200), now=Now(later))
    assert loader3.load(D28).calls_today == 2


# ── 하루 구동 (KrxDaily) ──

CAL = TradingCalendar.default()


def kst(*a: int) -> datetime:
    return datetime(*a, tzinfo=KST)  # type: ignore[misc]


def inline[T](fn: Callable[[], T], /) -> Future[T]:
    """작업을 그 자리에서(가짜 시계 시험) — 서비스 기본은 스레드."""
    fut: Future[T] = Future()
    try:
        fut.set_result(fn())
    except Exception as e:
        fut.set_exception(e)
    return fut


class RecordingLoader:
    """부른 거래일·시각을 남기고 다 받은 결과를 돌려준다(또는 raises)."""

    def __init__(self, now: Now, raises: Exception | None = None) -> None:
        self.now = now
        self.raises = raises
        self.calls: list[tuple[datetime, date]] = []
        self.listed: list[tuple[str, str, str, Decimal]] = []
        self.gate: threading.Event | None = None  # 있으면 상장 목록 읽기가 열릴 때까지 막힌다
        self.listing_started = threading.Event()

    def listing(self, trade_date: date) -> list[tuple[str, str, str, Decimal]]:
        self.listing_started.set()
        if self.gate is not None:
            self.gate.wait(5)
        return self.listed

    def load(self, trade_date: date) -> KrxLoadResult:
        self.calls.append((self.now.t, trade_date))
        if self.raises is not None:
            raise self.raises
        done = (KindResult("options", "loaded", 13, 18), KindResult("futures", "loaded", 3, 3))
        return KrxLoadResult(trade_date, done, 2)

    @property
    def targets(self) -> list[date]:
        return [d for _, d in self.calls]


class Day:
    """KrxDaily + 가짜 시계 + (가짜 KRX → 진짜 KrxLoader 또는 RecordingLoader)."""

    def __init__(
        self,
        start: datetime,
        *,
        loader: Any = None,
        fake: FakeKrx | None = None,
        store: KrxMemoryStore | None = None,
        cap: int = 200,
        redis: Any = None,
        submit: Any = inline,
    ) -> None:
        self.now = Now(start)
        self.fake = fake or FakeKrx()
        self.store = store or KrxMemoryStore()
        self.redis = redis if redis is not None else fakeredis.FakeRedis()
        self.budget = KrxCallBudget(self.redis, cap)
        self.at: list[datetime] = []  # KRX 를 부른 시각
        self.fake.on_request = lambda _: self.at.append(self.now.t)
        self.loader = loader or KrxLoader(self.fake.client(), self.store, self.budget, now=self.now)
        self.health = MemoryHealthSink()
        self.daily = KrxDaily(self.loader, CAL, self.health, submit=submit, tagger=tagger_for(CAL))
        self.master: MasterSnapshot | None = None  # scheduler 가 넘기는 지금 마스터

    def run_until(self, end: datetime, every: timedelta = timedelta(seconds=60)) -> None:
        while self.now.t < end:
            self.daily.step(self.now.t, self.master)
            self.now.t += every


@pytest.mark.parametrize(
    ("today", "target"),
    [
        (date(2026, 9, 28), date(2026, 9, 23)),  # 추석 09-24·25 + 주말 너머 수요일
        (date(2026, 9, 21), date(2026, 9, 18)),  # 월요일 → 금요일
        (date(2026, 9, 29), date(2026, 9, 28)),
        (date(2026, 9, 23), date(2026, 9, 22)),  # 다음 날 휴장이어도 전 거래일은 그대로
    ],
)
def test_the_target_is_the_previous_trading_day_from_0805(today: date, target: date) -> None:
    now = Now(datetime.combine(today, time(7, 59), tzinfo=KST))
    rec = RecordingLoader(now)
    d = Day(now.t, loader=rec)
    d.now = now
    d.run_until(datetime.combine(today, time(8, 30), tzinfo=KST), every=timedelta(seconds=30))
    assert rec.calls == [(datetime.combine(today, time(8, 5), tzinfo=KST), target)]
    assert d.health.kinds() == ["krx_daily_loaded"]
    assert d.daily.day is not None and d.daily.day.finished and d.daily.day.options_done


def test_nothing_on_holidays_and_weekends() -> None:
    now = Now(kst(2026, 9, 24, 0, 0))  # 목 추석 ~ 일
    rec = RecordingLoader(now)
    d = Day(now.t, loader=rec)
    d.now = now
    d.run_until(kst(2026, 9, 28, 8, 4), every=timedelta(minutes=5))
    assert rec.calls == [] and d.health.events == []
    d.run_until(kst(2026, 9, 28, 8, 10), every=timedelta(minutes=1))
    assert rec.targets == [date(2026, 9, 23)]


def test_not_refreshed_is_retried_every_10_minutes_until_10_then_health() -> None:
    d = Day(kst(2026, 9, 28, 8, 0))
    d.run_until(kst(2026, 9, 28, 12, 0))
    tries = [kst(2026, 9, 28, 8, 5) + timedelta(minutes=10 * i) for i in range(12)]
    assert d.at == [t for t in tries for _ in range(2)]  # 08:05 ~ 09:55, 옵션·선물
    assert d.budget.used(d.now.t) == 24
    (ev,) = d.health.events  # 갱신 전은 마감 때만 알린다
    assert (ev.kind, ev.severity, ev.at) == ("krx_daily_missing", "warning", tries[-1])
    assert "KRX 2026-09-23 일별을 10:00까지 못 받았다(시도 12회)" in ev.detail
    assert "옵션 아직 갱신 전: 빈 응답" in ev.detail
    d.fake.publish(date(2026, 9, 28))  # 다음 거래일은 새 대상으로 다시
    d.run_until(kst(2026, 9, 29, 8, 30))
    assert d.fake.requested()[-2:] == [(OPT_DAILY, "20260928"), (FUT_DAILY, "20260928")]
    assert d.health.kinds() == ["krx_daily_missing", "krx_daily_loaded"]


def test_a_late_refresh_is_picked_up_on_the_next_try() -> None:
    d = Day(kst(2026, 9, 28, 8, 0))
    d.run_until(kst(2026, 9, 28, 8, 30))
    d.fake.publish(D23)  # KRX 가 08:30 에 갱신
    d.run_until(kst(2026, 9, 28, 11, 0))
    assert sorted(set(d.at)) == [kst(2026, 9, 28, 8, m) for m in (5, 15, 25, 35)]
    assert d.fake.calls() == 8 and len(d.store.options) == 13 and len(d.store.futures) == 3
    (ev,) = d.health.events
    assert (ev.kind, ev.severity, ev.at) == ("krx_daily_loaded", "info", kst(2026, 9, 28, 8, 35))
    assert "KRX 2026-09-23 일별 옵션 13행(받은 18)·선물 3행(받은 3)" in ev.detail
    assert "오늘 호출 8회" in ev.detail


def test_a_failure_warns_and_only_the_missing_kind_is_retried() -> None:
    d = Day(kst(2026, 9, 28, 8, 0))
    d.fake.publish(D23)
    d.fake.fail[FUT_DAILY] = 500
    d.run_until(kst(2026, 9, 28, 8, 10))
    (ev,) = d.health.events
    assert (ev.kind, ev.severity) == ("krx_daily_failed", "warning")
    assert "선물 실패: /drv/fut_bydd_trd 20260923: HTTP 500" in ev.detail
    assert ev.detail.endswith("10분 뒤 다시") and KEY not in ev.detail
    assert d.daily.day is not None and d.daily.day.options_done  # 옵션은 받았다
    del d.fake.fail[FUT_DAILY]
    d.run_until(kst(2026, 9, 28, 11, 0))
    assert (d.fake.calls(OPT_DAILY), d.fake.calls(FUT_DAILY)) == (1, 2)
    assert d.health.kinds() == ["krx_daily_failed", "krx_daily_loaded"]
    assert "옵션 이미 적재됨·선물 3행" in d.health.events[-1].detail


def test_the_daily_cap_ends_the_day_and_the_next_day_starts_over() -> None:
    d = Day(kst(2026, 9, 28, 8, 0), cap=3)
    d.run_until(kst(2026, 9, 28, 12, 0))
    assert d.fake.calls() == 3  # 08:05 옵션·선물, 08:15 옵션 — 선물은 상한
    (ev,) = d.health.events
    assert (ev.kind, ev.severity, ev.at) == ("krx_daily_capped", "warning", kst(2026, 9, 28, 8, 15))
    assert "호출 상한" in ev.detail and "3/3" in ev.detail
    d.fake.publish(date(2026, 9, 28))
    d.run_until(kst(2026, 9, 29, 8, 10))
    assert d.fake.calls() == 5 and d.health.kinds()[-1] == "krx_daily_loaded"


def test_a_restart_on_a_loaded_day_calls_nothing() -> None:
    store = KrxMemoryStore()
    first = Day(kst(2026, 9, 28, 8, 0), store=store)
    first.fake.publish(D23)
    first.run_until(kst(2026, 9, 28, 8, 10))
    assert first.fake.calls() == 2
    again = Day(kst(2026, 9, 28, 9, 0), store=store)  # 재기동 — 새 프로세스
    again.run_until(kst(2026, 9, 28, 11, 0))
    assert again.fake.calls() == 0 and again.health.events == []  # 이미 DB 에 — 로그만
    assert again.daily.day is not None and again.daily.day.finished


@pytest.mark.parametrize("published", [True, False])
def test_a_restart_after_the_deadline_still_tries_once(published: bool) -> None:
    d = Day(kst(2026, 9, 28, 13, 0))
    if published:
        d.fake.publish(D23)
    d.run_until(kst(2026, 9, 28, 18, 0))
    assert d.at == [kst(2026, 9, 28, 13, 0)] * 2
    (ev,) = d.health.events
    if published:
        assert ev.kind == "krx_daily_loaded"
    else:
        assert ev.kind == "krx_daily_missing" and "(시도 1회)" in ev.detail


def test_a_loader_error_or_a_failed_start_is_retried() -> None:
    now = Now(kst(2026, 9, 28, 8, 0))
    rec = RecordingLoader(now, raises=RuntimeError("boom"))
    d = Day(now.t, loader=rec)
    d.now = now
    d.run_until(kst(2026, 9, 28, 8, 20))
    assert [t for t, _ in rec.calls] == [kst(2026, 9, 28, 8, 5), kst(2026, 9, 28, 8, 15)]
    assert d.health.kinds() == ["krx_daily_failed"] * 2
    assert "적재 오류: RuntimeError" in d.health.events[0].detail

    def refuse(fn: Any) -> Future[Any]:
        raise RuntimeError("can't start new thread")

    d2 = Day(kst(2026, 9, 28, 9, 50), submit=refuse)
    d2.run_until(kst(2026, 9, 28, 11, 0))
    assert d2.health.kinds() == ["krx_daily_failed", "krx_daily_missing"]  # 09:50, 10:00 넘김
    assert "시작 실패: RuntimeError" in d2.health.events[0].detail


def test_the_load_runs_in_a_worker_thread_and_step_does_not_wait() -> None:
    gate, started = threading.Event(), threading.Event()
    fake = FakeKrx()
    fake.publish(D23)
    d = Day(kst(2026, 9, 28, 8, 5), fake=fake, submit=krx_thread_submit)

    def hold(_: Any) -> None:
        started.set()
        gate.wait(5)

    fake.on_request = hold
    t0 = time_mod.monotonic()
    d.daily.step(d.now.t)  # 시작만 하고 돌아온다
    assert started.wait(5) and d.daily.busy
    for _ in range(5):
        d.now.t += timedelta(seconds=1)
        d.daily.step(d.now.t)  # 걸려 있는 동안 step 은 기다리지 않는다
    assert time_mod.monotonic() - t0 < 1.0 and d.health.events == []
    gate.set()
    deadline = time_mod.monotonic() + 5
    while not d.health.events:
        assert time_mod.monotonic() < deadline
        d.now.t += timedelta(seconds=1)
        d.daily.step(d.now.t)
        time_mod.sleep(0.01)
    assert d.health.kinds() == ["krx_daily_loaded"] and fake.calls() == 2
    assert not d.daily.busy


# ── 마스터 ⊇ KRX 대조 (KrxDaily) ──

MASTER_TEXT = "\n".join(default_chain().master_lines()) + "\n"  # KRX 발췌 09-23 을 덮는다


def snap(asof: datetime, text: str = MASTER_TEXT, sha: str | None = None) -> MasterSnapshot:
    return MasterSnapshot(
        asof=asof,
        trade_date=None,
        session=None,
        rows=text.count("\n"),
        sha256=sha or MasterSnapshot.digest(text),
        text=text,
    )


def with_strike(strike: str) -> list[dict[str, Any]]:
    """KRX 옵션 발췌 + 마스터(202610 745.0~1595.0)에 없는 행사가 한 줄."""
    rows = fixture_rows(OPT_DAILY, D23)
    extra = rows[0] | {"ISU_NM": f"코스피200 C 202610 {strike} (정규)", "ISU_CD": "B016AZZZ"}
    return [*rows, extra]


def test_the_check_runs_once_the_master_and_the_krx_options_are_both_in(
    caplog: pytest.LogCaptureFixture,
) -> None:
    d = Day(kst(2026, 9, 28, 8, 0))
    d.fake.publish(D23)
    d.master = snap(kst(2026, 9, 28, 8, 0))  # PRE_DAY 마스터
    with caplog.at_level(logging.INFO, logger="services.scheduler.krx"):
        d.run_until(kst(2026, 9, 28, 10, 0))
    assert d.daily.day is not None
    (rep,) = d.daily.day.reports
    assert rep.ok and rep.krx_date == D23 and rep.compared == 3  # 202610·2609W4(월)·2610W1(목)
    assert rep.master_only == (("kospi200", "202611"), ("kospi200_weekly_mon", "261001"))
    assert rep.krx_only == (
        ("kospi200", "202812"),  # 원월물 — 가짜 마스터에 없다
        ("kospi200_weekly_thu", "260904"),
        ("mini_kospi200", "202610"),
    )
    assert d.health.kinds() == ["krx_daily_loaded"]  # 모두 있으면 health 가 아니다
    (line,) = [r.getMessage() for r in caplog.records if "master_krx_checked" in r.getMessage()]
    assert '"ok": true' in line and '"missing": 0' in line and '"krx_date": "2026-09-23"' in line
    assert (
        '"trade_date": "2026-09-28"' in line and '"session": "day"' in line
    )  # PRE_DAY → 그날 주간


def test_a_krx_strike_missing_from_the_master_warns_once_per_master() -> None:
    d = Day(kst(2026, 9, 28, 8, 0))
    d.fake.publish(D23, options=with_strike("1,600.0"))
    d.master = snap(kst(2026, 9, 28, 8, 0))
    d.run_until(kst(2026, 9, 28, 17, 50))
    (ev,) = d.health.of("master_krx_missing")
    assert (ev.severity, ev.at) == ("warning", kst(2026, 9, 28, 8, 5))
    assert "마스터에 없는 KRX 2026-09-23 행사가 1개(시리즈 1개) — 코스피200 202610 C 1,600.0" in (
        ev.detail
    )
    assert "대조 만기 3개" in ev.detail and "(마스터 sha " in ev.detail
    d.master = snap(kst(2026, 9, 28, 17, 50), sha="night")  # PRE_NIGHT 새 마스터 — 다시 대조
    d.run_until(kst(2026, 9, 28, 18, 30))
    assert len(d.health.of("master_krx_missing")) == 2
    assert d.daily.day is not None
    assert d.daily.day.checked == {MasterSnapshot.digest(MASTER_TEXT), "night"}


def test_the_check_waits_for_the_master_of_that_day() -> None:
    d = Day(kst(2026, 9, 28, 8, 0))
    d.fake.publish(D23)
    d.master = snap(kst(2026, 9, 25, 17, 50))  # 지난 거래일 밤 것 — PRE_DAY 마스터가 아직(실패 중)
    d.run_until(kst(2026, 9, 28, 8, 20))
    assert d.daily.day is not None and d.daily.day.options_done and d.daily.day.reports == []
    d.master = snap(kst(2026, 9, 28, 8, 20))  # 마스터가 늦게 들어왔다
    d.run_until(kst(2026, 9, 28, 8, 22))
    assert len(d.daily.day.reports) == 1 and d.daily.day.reports[0].ok


def test_no_check_without_the_krx_options() -> None:
    d = Day(kst(2026, 9, 28, 8, 0))  # KRX 가 끝내 갱신되지 않는다
    d.master = snap(kst(2026, 9, 28, 8, 0))
    d.run_until(kst(2026, 9, 28, 12, 0))
    assert d.daily.day is not None and d.daily.day.reports == []
    assert d.health.kinds() == ["krx_daily_missing"] and d.store.reads == 24  # 적재 확인만


def test_a_failed_check_warns_and_is_retried() -> None:
    d = Day(kst(2026, 9, 28, 8, 0))
    d.fake.publish(D23)
    d.store.fail_listing = True
    d.master = snap(kst(2026, 9, 28, 8, 0))
    d.run_until(kst(2026, 9, 28, 8, 10))
    (ev,) = d.health.of("master_krx_check_failed")
    assert ev.severity == "warning" and ev.at == kst(2026, 9, 28, 8, 5)
    assert "대조 실패 — StoreError: krx_opt_daily: OperationalError" in ev.detail
    assert ev.detail.endswith("10분 뒤 다시")
    d.store.fail_listing = False
    d.run_until(kst(2026, 9, 28, 8, 20))
    assert d.daily.day is not None and [r.ok for r in d.daily.day.reports] == [True]
    assert len(d.health.of("master_krx_check_failed")) == 1


@pytest.mark.parametrize(
    ("text", "listed", "why"),
    [
        (MASTER_TEXT, [], "LookupError: KRX 2026-09-23 옵션 상장 목록이 비었다"),
        ("not|a|master\n", [("kospi200", "202610", "C", Decimal("1100"))], "ValueError: "),
    ],
)
def test_an_empty_listing_or_a_broken_master_is_a_failed_check(
    text: str, listed: list[tuple[str, str, str, Decimal]], why: str
) -> None:
    now = Now(kst(2026, 9, 28, 8, 0))
    rec = RecordingLoader(now)
    rec.listed = listed
    d = Day(now.t, loader=rec)
    d.now = now
    d.master = snap(kst(2026, 9, 28, 8, 0), text=text)
    d.run_until(kst(2026, 9, 28, 8, 6))
    (ev,) = d.health.of("master_krx_check_failed")
    assert why in ev.detail


def test_the_check_runs_in_a_worker_thread() -> None:
    now = Now(kst(2026, 9, 28, 8, 5))
    rec = RecordingLoader(now)
    rec.listed = [("kospi200", "202610", "C", Decimal("1100"))]
    rec.gate = threading.Event()
    d = Day(now.t, loader=rec, submit=krx_thread_submit)
    d.now = now
    d.master = snap(kst(2026, 9, 28, 8, 0))
    t0 = time_mod.monotonic()
    deadline = t0 + 5
    while not rec.listing_started.is_set():  # 적재(스레드)가 끝나면 대조가 스레드에서 시작
        assert time_mod.monotonic() < deadline
        d.daily.step(now.t, d.master)
        time_mod.sleep(0.01)
    for _ in range(5):
        now.t += timedelta(seconds=1)
        d.daily.step(now.t, d.master)  # 대조가 걸려 있는 동안 기다리지 않는다
    assert d.daily.busy and time_mod.monotonic() - t0 < 2.0
    rec.gate.set()
    while d.daily.busy:
        assert time_mod.monotonic() < deadline
        now.t += timedelta(seconds=1)
        d.daily.step(now.t, d.master)
        time_mod.sleep(0.01)
    assert d.daily.day is not None and [r.ok for r in d.daily.day.reports] == [True]

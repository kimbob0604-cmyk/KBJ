"""scheduler KIS 선물 분봉 적재 — 창·이어 가기 입력(미실측 가정 고정)·근월물·한 번의 시도
(MinuteLoader: 멈춤 조건·상한·실패 뒤 이어 가기·미래 입력 금지·격리·멱등)·하루 구동(MinuteDaily:
16:00 주간·06:10 야간·금요일 밤 → 월요일·휴장 전날 밤 없음·5분 재시도·health·작업 스레드).

가짜 KIS(tests/fakes/kis_server.py 분봉 경로 — 관측한 조회 의미를 흉내)·가짜 시계 레이트리미터·
메모리 저장소만 쓴다 — 네트워크·DB 없음. 실제 DB 쓰기는 tests/integration/test_scheduler_minute.py.
"""

from __future__ import annotations

import json
import logging
import threading
import time as time_mod
from collections.abc import Callable
from concurrent.futures import Future
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import pytest
from pydantic import ValidationError

from config.settings import Settings
from core.calendar import TradingCalendar
from data.kis.ratelimit import (
    LocalRateLimiter,
    Priority,
    RateLimitTimeout,
    Reason,
    limiter_key,
)
from data.kis.rest import MINUTE_TR
from services.auth.health import MemoryHealthSink
from services.bus import CHAIN_CONTEXT_KEY, ChainContextSnapshot, ExpiryEntry, MasterSnapshot
from services.chain_feed import load_context
from services.scheduler.minute import (
    Attempt,
    MinuteConfig,
    MinuteDaily,
    MinuteLoader,
    SessionJob,
    Window,
    continuation_hour,
    day_windows,
    futures_last_dates,
    input_at,
    minute_thread_submit,
    near_month_codes,
    night_windows,
    reader_kis_client,
)
from tests.fakes.kis_server import (
    CACHED_TOKEN,
    EPOCH,
    FakeChain,
    FakeClock,
    FakeKisServer,
    MinuteMemoryStore,
    StaticTokenProvider,
    day_bar_times,
    default_chain,
    fake_settings,
    make_client,
    night_bar_times,
    quarterly_expiry_chain,
    seed_cached_token,
)

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
CFG = MinuteConfig()
D23, D28, D29 = date(2026, 9, 23), date(2026, 9, 28), date(2026, 9, 29)
FRI, SAT, MON = date(2026, 9, 18), date(2026, 9, 19), date(2026, 9, 21)
CODE = "A01612"  # 가짜 체인 근월물 (F 202612)


def kst(d: date, h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime.combine(d, time(h, m, s), tzinfo=KST)


def snapshot(chain: FakeChain | None = None, at: datetime | None = None) -> MasterSnapshot:
    text = "\n".join((chain or default_chain()).master_lines()) + "\n"
    return MasterSnapshot(
        asof=at or kst(D28, 8),
        trade_date=None,
        session=None,
        rows=text.count("\n"),
        sha256=MasterSnapshot.digest(text),
        text=text,
    )


def day_job(d: date) -> SessionJob:
    return SessionJob("day", d, d, deadline=kst(d, 17, 50), next_try=kst(d, 16))


def night_job(d: date, t: date) -> SessionJob:
    return SessionJob("night", d, t, deadline=kst(t, 8), next_try=kst(t, 6, 10))


class Rig:
    def __init__(
        self,
        start: datetime,
        *,
        chain: FakeChain | None = None,
        config: MinuteConfig | None = None,
        context: Any = None,
        tokens: Any = None,
        limiter: Any = None,
        store: MinuteMemoryStore | None = None,
    ) -> None:
        self.clock = FakeClock(start)
        self.chain = chain or default_chain()
        self.server = FakeKisServer(self.clock, self.chain, CAL)
        self.kis = make_client(self.server, limiter or LocalRateLimiter(clock=self.clock), tokens)
        self.store = store or MinuteMemoryStore()
        kw: dict[str, Any] = {} if context is None else {"context": context}
        self.loader = MinuteLoader(
            self.kis, self.store, CAL, config=config, now=self.clock.now, **kw
        )

    def run(self, job: SessionJob, master: MasterSnapshot | None = None) -> Any:
        return self.loader.run(job, master if master is not None else snapshot(self.chain))


# ── 창·이어 가기 입력 (2026-09-29 실측 #17b — 야간은 (시작일, 확장 시각) 한 줄) ──

# 18:00~30:00 매분 721봉인 밤을 (D, 300000) 부터 102봉씩 — 가장 이른 봉 − 1분으로 이어 간 입력 8개
NIGHT_HOURS = ("300000", "281800", "263600", "245400", "231200", "213000", "194800", "180600")


def test_windows_start_from_the_measured_inputs() -> None:
    (day,) = day_windows(CODE, D28, CFG)
    assert (day.market, day.anchor, day.first_hour, day.target) == ("F", D28, "160000", D28)
    assert (day.start, day.max_calls) == (kst(D28, 8, 45), 5)
    (night,) = night_windows(CODE, FRI, CFG)  # 금요일 밤 — 날짜 입력도 금요일(귀속은 월요일)
    assert (night.market, night.anchor, night.first_hour, night.target) == (
        "CM",
        FRI,
        "300000",
        FRI,
    )
    assert (night.name, night.start, night.max_calls) == ("night", kst(FRI, 18), 8)


@pytest.mark.parametrize(
    ("which", "earliest", "want"),
    [
        ("day", kst(D28, 13, 54), "135300"),
        ("day", kst(D28, 8, 46), "084500"),
        ("day", kst(D28, 8, 45), None),  # 시작에 닿음
        ("night", kst(D29, 6, 0), "295900"),  # 30:00 봉 → 29:59
        ("night", kst(D29, 4, 7), "280600"),  # 28:07 → 확장 표기 '280600'(실측 — '040600' 은 딴 밤)
        ("night", kst(D29, 0, 1), "240000"),
        ("night", kst(D29, 0, 0), "235900"),  # 24:00 봉 다음은 23:59 — 자정을 넘어 한 줄
        ("night", kst(D28, 22, 19), "221800"),
        ("night", kst(D28, 18, 1), "180000"),
        ("night", kst(D28, 18, 0), None),  # 시작에 닿음
    ],
)
def test_continuation_is_the_earliest_bar_minus_a_minute(
    which: str, earliest: datetime, want: str | None
) -> None:
    """실측(#17b): 날짜는 창의 anchor(야간 시작일) 그대로, 시각은 anchor 자정부터 잰 HHMMSS."""
    windows = {"day": day_windows(CODE, D28, CFG)[0], "night": night_windows(CODE, D28, CFG)[0]}
    assert continuation_hour(windows[which], earliest.astimezone(UTC)) == want


def test_a_friday_night_keeps_the_friday_date_with_extended_hours() -> None:
    (night,) = night_windows(CODE, FRI, CFG)
    assert night.anchor == FRI and continuation_hour(night, kst(SAT, 4, 7)) == "280600"


def test_a_continuation_outside_its_window_is_a_bug() -> None:
    (night,) = night_windows(CODE, D28, CFG)
    with pytest.raises(ValueError, match="창 밖"):
        continuation_hour(night, kst(D29, 6, 5))  # 30:05 — 야간 창(18~30시) 밖
    (day,) = day_windows(CODE, D28, CFG)
    with pytest.raises(ValueError, match="창 밖"):
        continuation_hour(day, kst(D29, 0, 5))  # 다음 날 00:05 — 주간 창 밖


def test_input_at_reads_extended_hours_as_the_next_day() -> None:
    assert input_at(FRI, "300000") == kst(SAT, 6)
    assert input_at(D28, "235959") == kst(D28, 23, 59, 59)
    with pytest.raises(ValueError):
        input_at(D28, "310000")


def test_config_keeps_the_first_inputs_in_the_past() -> None:
    with pytest.raises(ValidationError, match="160000"):
        MinuteConfig(day_start=time(15, 50))
    with pytest.raises(ValidationError, match="06:00"):
        MinuteConfig(night_start=time(6, 0))
    with pytest.raises(ValidationError):
        MinuteConfig(night_max_calls=0)


# ── 근월물 ──


def test_the_near_month_is_the_first_contract_still_trading() -> None:
    futs = [("A01612", date(2026, 12, 10)), ("A01703", date(2027, 3, 11))]
    assert near_month_codes(futs, "day", D28) == ("A01612",)
    assert near_month_codes(futs, "night", D28) == ("A01612",)
    dec10, dec09 = date(2026, 12, 10), date(2026, 12, 9)
    # 분기 만기일 주간은 만기 종목 + 차월물(15:20 뒤 근월물), 그날 밤은 차월물, 전날 밤은 만기 종목
    assert near_month_codes(futs, "day", dec10) == ("A01612", "A01703")
    assert near_month_codes(futs, "night", dec10) == ("A01703",)
    assert near_month_codes(futs, "night", dec09) == ("A01612",)
    assert near_month_codes(futs, "day", date(2026, 12, 11)) == ("A01703",)
    assert near_month_codes([("X", None), *futs], "day", D28) == ("X",)  # 모르면 살아 있다
    assert near_month_codes(futs, "day", date(2027, 3, 12)) == ()


def test_last_trade_dates_come_from_the_poller_context_when_there_is_one() -> None:
    rows = default_chain().master_rows()
    assert futures_last_dates(rows, CAL, None) == [
        ("A01612", date(2026, 12, 10)),  # 캘린더(둘째 목요일)
        ("A01703", date(2027, 3, 11)),
    ]
    ctx = ChainContextSnapshot(
        at=kst(D28, 9),
        version=1,
        listed={"": ["202612"]},
        expiries=[ExpiryEntry(cls="", mtrt="202612", last_trade_date=D29, source="kis")],
        futures_codes=["A01612", "A01703"],
    )
    got = futures_last_dates(rows, CAL, ctx)
    assert got[0] == ("A01612", D29)  # KIS 값이 먼저
    assert near_month_codes(got, "night", D29) == ("A01703",)


# ── 한 번의 시도 ──


def test_a_day_session_takes_five_calls_back_to_the_open() -> None:
    rig = Rig(kst(D28, 16))
    rig.server.add_minute_bars(CODE, "F", day_bar_times(D23))  # 앞 거래일 — 섞여 와도 버린다
    rig.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    job = day_job(D28)
    res = rig.run(job)
    assert res.ok and res.calls == 5
    assert rig.server.minute_calls() == [
        ("F", CODE, "20260928", h) for h in ("160000", "135300", "121100", "102900", "084700")
    ]
    bars = rig.store.of(CODE, "F")
    assert len(bars) == 411 and {(b.trade_date, b.session) for b in bars} == {(D28, "day")}
    assert (bars[0].ts, bars[-1].ts) == (kst(D28, 8, 45), kst(D28, 15, 45))
    assert job.runs is not None and [r.outcome for r in job.runs] == ["complete"]
    assert len(rig.store.raw) == 5 and {e.tr_id for e in rig.store.raw} == {MINUTE_TR}
    assert {(e.trade_date, e.session, e.source) for e in rig.store.raw} == {
        (D28, "day", "kis_rest")
    }
    assert rig.server.max_in_window() <= 4  # 공용 리미터를 지난다


def test_a_night_takes_one_chain_of_eight_calls_and_lands_on_the_next_trading_day() -> None:
    rig = Rig(kst(D29, 6, 10))
    rig.server.add_minute_bars(CODE, "CM", night_bar_times(date(2026, 9, 22)))  # 앞 밤
    rig.server.add_minute_bars(CODE, "CM", night_bar_times(D28))
    job = night_job(D28, D29)
    res = rig.run(job)
    assert res.ok and res.calls == 8
    assert rig.server.minute_calls() == [("CM", CODE, "20260928", h) for h in NIGHT_HOURS]
    bars = rig.store.of(CODE, "CM")
    assert len(bars) == 721  # 18:00~30:00 매분
    assert {(b.trade_date, b.session, b.market) for b in bars} == {(D29, "night", "CM")}
    assert (bars[0].ts, bars[-1].ts) == (kst(D28, 18), kst(D29, 6))  # 300000 → 06:00
    assert job.runs is not None and [r.outcome for r in job.runs] == ["complete"]
    assert "FAKEFUTR" not in {c.msg_cd for c in rig.server.calls}


def test_a_friday_night_is_read_with_the_friday_date_and_attributed_to_monday() -> None:
    rig = Rig(kst(MON, 6, 10))
    rig.server.add_minute_bars(CODE, "CM", night_bar_times(date(2026, 9, 17)))
    rig.server.add_minute_bars(CODE, "CM", night_bar_times(FRI))
    assert rig.run(night_job(FRI, MON)).ok
    assert rig.server.minute_calls() == [("CM", CODE, "20260918", h) for h in NIGHT_HOURS]
    bars = rig.store.of(CODE, "CM")
    saturday = [b for b in bars if b.ts.astimezone(KST).date() == SAT]
    assert len(bars) == 721 and len(saturday) == 361  # 토 00:00~06:00
    assert {b.trade_date for b in bars} == {MON}


def test_a_window_stops_when_no_earlier_bars_come() -> None:
    """21:00 부터만 봉이 있고 앞 밤 봉도 없다 → 일곱 번째 조회가 비어 끝난다(상한 8 전에)."""
    rig = Rig(kst(D29, 6, 10))
    rig.server.add_minute_bars(CODE, "CM", night_bar_times(D28, first=(21, 0)))
    job = night_job(D28, D29)
    assert rig.run(job).ok
    assert job.runs is not None
    (night,) = job.runs
    assert (night.outcome, night.calls, night.earliest) == ("exhausted", 7, kst(D28, 21))
    assert [c[3] for c in rig.server.minute_calls()] == [
        "300000",
        "281800",
        "263600",
        "245400",
        "231200",
        "213000",
        "205900",
    ]


def test_a_window_stops_when_the_answer_crosses_into_the_previous_session() -> None:
    """08:50 부터 봉이 있는 날 — 네 번째 응답에 앞 거래일 봉이 섞이면 더 부르지 않는다."""
    rig = Rig(kst(D28, 16))
    rig.server.add_minute_bars(CODE, "F", day_bar_times(D23))
    rig.server.add_minute_bars(CODE, "F", [t for t in day_bar_times(D28) if t >= kst(D28, 8, 50)])
    job = day_job(D28)
    assert rig.run(job).ok and len(rig.server.minute_calls()) == 4
    assert job.runs is not None
    (run,) = job.runs
    assert (run.outcome, run.earliest, len(run.bars)) == ("exhausted", kst(D28, 8, 50), 406)


def test_the_call_cap_stops_a_window() -> None:
    rig = Rig(kst(D28, 16), config=MinuteConfig(day_max_calls=3))
    rig.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    job = day_job(D28)
    res = rig.run(job)
    assert res.ok and res.calls == 3 and job.runs is not None
    (run,) = job.runs
    assert (run.outcome, run.calls, run.earliest) == ("capped", 3, kst(D28, 10, 30))
    assert "호출 상한" in job.describe()


def test_loading_the_same_session_again_writes_each_bar_once_and_updates_values() -> None:
    rig = Rig(kst(D28, 16))
    rig.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    assert rig.run(day_job(D28)).ok
    first = {b.ts: b.close for b in rig.store.of(CODE, "F")}
    last = kst(D28, 15, 45)
    rig.server.add_minute_bars(CODE, "F", [last], base=Decimal("1200.00"))  # 마지막 봉이 고쳐졌다
    assert rig.run(day_job(D28)).ok
    again = {b.ts: b.close for b in rig.store.of(CODE, "F")}
    assert len(again) == len(first) == 411 and len(rig.store.batches) == 10
    assert again[last] != first[last] and again[last] >= Decimal("1200")
    assert {t: v for t, v in again.items() if t != last} == {
        t: v for t, v in first.items() if t != last
    }


def test_a_failed_call_stops_the_attempt_and_the_next_one_resumes_there() -> None:
    rig = Rig(kst(D28, 16))
    rig.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    rig.server.inject("http500", tr_id=MINUTE_TR, params={"FID_INPUT_HOUR_1": "121100"})
    job = day_job(D28)
    res = rig.run(job)
    assert not res.ok and res.calls == 3 and "HTTP 500" in res.detail
    assert job.runs is not None and job.runs[0].calls == 2 and not job.done
    res = rig.run(job)
    assert res.ok and res.calls == 3 and job.done
    assert [c[3] for c in rig.server.minute_calls()] == [
        "160000",
        "135300",
        "121100",  # 실패
        "121100",
        "102900",
        "084700",
    ]
    assert len(rig.store.of(CODE, "F")) == 411 and job.runs[0].calls == 5


def test_a_kis_error_answer_is_a_failure() -> None:
    rig = Rig(kst(D28, 16))
    rig.server.inject("rate_limit", tr_id=MINUTE_TR, times=2)  # 재시도까지 한도초과
    res = rig.run(day_job(D28))
    assert not res.ok and "EGW00201" in res.detail and res.calls == 1


class SkippingLimiter:
    """P4 는 늘 못 받는다(더 높은 등급이 기다린다)."""

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        assert priority is Priority.P4 and timeout == CFG.p4_timeout_s
        raise RateLimitTimeout(priority, tr_id, Reason.PRIORITY)

    def on_rate_limited(self) -> float:
        return 4.0


def test_a_p4_skip_by_the_rate_limiter_sends_nothing() -> None:
    rig = Rig(kst(D28, 16), limiter=SkippingLimiter())
    rig.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    job = day_job(D28)
    res = rig.run(job)
    assert (res.ok, res.calls, res.detail) == (False, 0, f"{CODE} F 주간: P4 건너뜀(PRIORITY)")
    assert rig.server.calls == [] and job.runs is not None and job.runs[0].calls == 0


def test_a_future_input_is_never_sent() -> None:
    """밤이 끝나기 전(금 23:00)에 금요일 밤을 부르면 (금, 300000) = 토 06:00 은 미래 — 안 보낸다."""
    rig = Rig(kst(FRI, 23))
    rig.server.add_minute_bars(CODE, "CM", night_bar_times(FRI))
    job = night_job(FRI, MON)
    res = rig.run(job)
    assert not res.ok and "미래 입력 2026-09-18 300000" in res.detail and res.calls == 0
    assert rig.server.minute_calls() == []
    assert job.runs is not None and job.runs[0].calls == 0 and not job.runs[0].done


def test_invalid_rows_are_quarantined_and_the_rest_is_kept() -> None:
    rig = Rig(kst(D28, 16))
    rig.server.add_minute_bars(CODE, "F", day_bar_times(D28))

    def corrupt(body: dict[str, Any]) -> None:
        body["output2"][0]["futs_prpr"] = "abc"  # 15:45 봉
        body["output2"][1]["stck_cntg_hour"] = "193400"  # 주간 봉인데 19:34 — 변환 실패

    rig.server.inject("malformed", tr_id=MINUTE_TR, mutate=corrupt)
    job = day_job(D28)
    assert rig.run(job).ok and job.runs is not None and job.runs[0].invalid == 2
    q = rig.store.quarantined
    assert len(q) == 2 and {(r.trade_date, r.session, r.tr_id) for r in q} == {
        (D28, "day", MINUTE_TR)
    }
    assert q[0].payload["futs_prpr"] == "abc" and "080000" in q[1].error
    assert len(rig.store.of(CODE, "F")) == 409


def test_a_failed_write_does_not_advance_and_a_failed_raw_write_is_ignored() -> None:
    rig = Rig(kst(D28, 16))
    rig.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    rig.store.fail_write = True
    job = day_job(D28)
    res = rig.run(job)
    assert not res.ok and res.detail == f"{CODE} F 주간: 저장 실패 — StoreError"
    assert job.runs is not None and job.runs[0].calls == 0 and job.runs[0].next_hour is None
    rig.store.fail_write, rig.store.fail_raw = False, True
    assert rig.run(job).ok and len(rig.store.of(CODE, "F")) == 411
    assert len(rig.store.raw) == 1  # 첫 시도의 원문만 — 그 뒤 원문 쓰기 실패는 적재를 막지 않는다


def test_without_a_master_nothing_is_called() -> None:
    rig = Rig(kst(D28, 16))
    job = day_job(D28)
    res = rig.loader.run(job, None)
    assert (res.ok, res.calls) == (False, 0) and "마스터가 없다" in res.detail
    assert job.runs is None and rig.server.calls == []
    broken = snapshot().model_copy(update={"text": "garbage"})
    assert "마스터 파싱 실패" in rig.loader.run(job, broken).detail


def test_the_quarterly_expiry_day_loads_the_expiring_and_the_next_contract() -> None:
    dec10 = date(2026, 12, 10)
    chain = quarterly_expiry_chain()
    rig = Rig(kst(dec10, 16), chain=chain)
    before_expiry = [t for t in day_bar_times(dec10) if t < kst(dec10, 15, 20)]
    rig.server.add_minute_bars("A01612", "F", before_expiry)  # 15:20 만기
    rig.server.add_minute_bars("A01703", "F", day_bar_times(dec10))
    job = day_job(dec10)
    assert rig.run(job).ok and job.runs is not None
    assert [r.window.code for r in job.runs] == ["A01612", "A01703"]
    assert len(rig.store.of("A01612", "F")) == 395 and len(rig.store.of("A01703", "F")) == 411


def test_a_broken_context_source_falls_back_to_the_calendar(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom() -> ChainContextSnapshot | None:
        raise ConnectionError("redis down")

    rig = Rig(kst(D28, 16), context=boom)
    rig.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    with caplog.at_level(logging.WARNING, logger="services.scheduler.minute"):
        assert rig.run(day_job(D28)).ok
    assert any("minute_context_failed" in r.getMessage() for r in caplog.records)


class LeakyTokens(StaticTokenProvider):
    def get(self) -> str:
        raise RuntimeError("토큰 캐시 오류 fake-key-for-tests")  # 앱키가 섞인 문구


def test_error_text_hides_the_app_key(caplog: pytest.LogCaptureFixture) -> None:
    rig = Rig(kst(D28, 16), tokens=LeakyTokens())
    with caplog.at_level(logging.INFO):
        res = rig.run(day_job(D28))
    assert not res.ok and "***" in res.detail and "fake-key-for-tests" not in res.detail
    assert all("fake-key-for-tests" not in r.getMessage() for r in caplog.records)


def test_each_page_logs_one_structured_line(caplog: pytest.LogCaptureFixture) -> None:
    rig = Rig(kst(D29, 6, 10))
    rig.server.add_minute_bars(CODE, "CM", night_bar_times(D28))
    with caplog.at_level(logging.INFO, logger="services.scheduler.minute"):
        rig.run(night_job(D28, D29))
    pages = [json.loads(r.getMessage()) for r in caplog.records]
    assert len(pages) == 8 and {p["event"] for p in pages} == {"minute_bars_page"}
    assert {(p["trade_date"], p["session"], p["service"]) for p in pages} == {
        ("2026-09-29", "night", "scheduler")
    }
    assert pages[7]["outcome"] == "complete" and pages[7]["earliest"].startswith("2026-09-28T18:00")
    assert {(p["window"], p["anchor"]) for p in pages} == {("night", "2026-09-28")}


def test_window_label_names_the_code_market_and_part() -> None:
    w = Window(CODE, "CM", "night", D28, "300000", D28, kst(D28, 18), 8)
    assert w.label == f"{CODE} CM 야간"


def test_bars_outside_the_night_are_quarantined_and_end_the_window() -> None:
    """첫 조회에 30:05 뒤 봉만 오면(관측과 다름) 세션 밖 시각이라 격리하고, 이어 갈 입력을 짓지 않고
    창을 끝낸다(창 밖 이어 가기 자체는 `continuation_hour` 가 ValueError — 위 단위 시험)."""
    rig = Rig(kst(D29, 6, 10))
    rig.server.add_minute_bars(CODE, "CM", night_bar_times(D28))

    def after_the_night(body: dict[str, Any]) -> None:
        tpl = body["output2"][0]
        body["output2"] = [tpl | {"stck_cntg_hour": h} for h in ("300600", "300500")]

    rig.server.inject(
        "malformed", tr_id=MINUTE_TR, params={"FID_INPUT_HOUR_1": "300000"}, mutate=after_the_night
    )
    job = night_job(D28, D29)
    assert rig.run(job).ok
    assert job.runs is not None
    (night,) = job.runs
    assert (night.outcome, night.calls, night.earliest, night.invalid) == ("exhausted", 1, None, 2)
    assert len(rig.server.minute_calls()) == 1 and len(rig.store.quarantined) == 2


# ── 하루 구동 (MinuteDaily) ──

OCT2, OCT6 = date(2026, 10, 2), date(2026, 10, 6)  # 금 · 화 (10-05 월 대체공휴일)


def inline[T](fn: Callable[[], T], /) -> Future[T]:
    """작업을 그 자리에서(가짜 시계 시험) — 서비스 기본은 스레드(`minute_thread_submit`)."""
    fut: Future[T] = Future()
    try:
        fut.set_result(fn())
    except Exception as e:
        fut.set_exception(e)
    return fut


class Daily:
    """MinuteDaily + 진짜 MinuteLoader + 가짜 KIS·시계. scheduler 처럼 step 에 마스터를 넘긴다."""

    def __init__(
        self,
        start: datetime,
        *,
        config: MinuteConfig | None = None,
        store: MinuteMemoryStore | None = None,
        submit: Any = inline,
        night_opens: Any = None,
    ) -> None:
        self.rig = Rig(start, config=config, store=store)
        self.t = start
        self.health = MemoryHealthSink()
        kw: dict[str, Any] = {} if night_opens is None else {"night_opens": night_opens}
        self.daily = MinuteDaily(
            self.rig.loader, CAL, self.health, config=config, submit=submit, **kw
        )
        self.master: MasterSnapshot | None = snapshot(self.rig.chain)

    @property
    def server(self) -> FakeKisServer:
        return self.rig.server

    @property
    def store(self) -> MinuteMemoryStore:
        return self.rig.store

    def run_until(self, end: datetime, every: timedelta = timedelta(minutes=1)) -> None:
        """step 시각은 every 간격 그대로 — 레이트리미터가 가짜 시계를 민 만큼은 다음 step 이
        되돌린다."""
        while self.t < end:
            self.rig.clock.set(self.t)
            self.daily.step(self.t, self.master)
            self.t += every

    def call_minutes(self) -> list[datetime]:
        """분봉을 부른 시각(KST, 분 단위로 내림) — 겹침 없이 차례로."""
        out: list[datetime] = []
        for c in self.server.calls:
            at = (EPOCH + timedelta(microseconds=c.t_us)).astimezone(KST)
            at = at.replace(second=0, microsecond=0)
            if not out or out[-1] != at:
                out.append(at)
        return out


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in caplog.records:
        try:
            rec = json.loads(r.getMessage())
        except ValueError:
            continue
        if rec.get("event") == name:
            out.append(rec)
    return out


def test_the_day_loads_from_1600_and_the_night_at_0610_of_its_trade_date(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """09-28(월) 06:00 ~ 09-29 07:00: 09-28 06:10 은 휴장 전날(09-23) 밤이라 없음, 16:00 주간 09-28,
    09-29 06:10 에 09-28 밤(귀속 09-29)."""
    d = Daily(kst(D28, 6))
    d.server.add_minute_bars(CODE, "F", day_bar_times(D23))
    d.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    d.server.add_minute_bars(CODE, "CM", night_bar_times(date(2026, 9, 22)))
    d.server.add_minute_bars(CODE, "CM", night_bar_times(D28))
    with caplog.at_level(logging.INFO, logger="services.scheduler.minute"):
        d.run_until(kst(D29, 7))
    assert d.call_minutes() == [kst(D28, 16), kst(D29, 6, 10)]
    assert [c[:3] for c in d.server.minute_calls()] == [
        *[("F", CODE, "20260928")] * 5,
        *[("CM", CODE, "20260928")] * 8,
    ]
    assert [(e.kind, e.severity, e.at) for e in d.health.events] == [
        ("minute_bars_loaded", "info", kst(D28, 16)),
        ("minute_bars_loaded", "info", kst(D29, 6, 10)),
    ]
    assert "2026-09-28 주간(귀속 2026-09-28)" in d.health.events[0].detail
    assert "2026-09-28 야간(귀속 2026-09-29)" in d.health.events[1].detail
    assert {(b.trade_date, b.session) for b in d.store.of(CODE, "F")} == {(D28, "day")}
    assert len(d.store.of(CODE, "F")) == 411 and len(d.store.of(CODE, "CM")) == 721
    assert {(b.trade_date, b.session) for b in d.store.of(CODE, "CM")} == {(D29, "night")}
    (closed,) = _events(caplog, "minute_night_closed")  # 그날 한 번만
    assert (closed["night_start"], closed["trade_date_of_night"]) == ("2026-09-23", "2026-09-28")
    sessions = _events(caplog, "minute_bars_session")
    assert [(s["trade_date"], s["session"], s["status"]) for s in sessions] == [
        ("2026-09-28", "day", "loaded"),
        ("2026-09-29", "night", "loaded"),
    ]
    assert sessions[1]["night_start"] == "2026-09-28" and sessions[1]["calls"] == 8


def test_a_friday_night_loads_on_monday_0610_not_on_saturday() -> None:
    d = Daily(kst(FRI, 15))
    d.server.add_minute_bars(CODE, "F", day_bar_times(FRI))
    d.server.add_minute_bars(CODE, "CM", night_bar_times(date(2026, 9, 17)))
    d.server.add_minute_bars(CODE, "CM", night_bar_times(FRI))
    d.run_until(kst(MON, 7))
    assert d.call_minutes() == [kst(FRI, 16), kst(MON, 6, 10)]  # 토 06:10 엔 부르지 않는다
    assert [c[2:] for c in d.server.minute_calls()[5:]] == [("20260918", h) for h in NIGHT_HOURS]
    bars = d.store.of(CODE, "CM")
    assert len(bars) == 721 and {b.trade_date for b in bars} == {MON}
    assert len([b for b in bars if b.ts.astimezone(KST).date() == SAT]) == 361
    assert d.health.kinds() == ["minute_bars_loaded"] * 2


def test_a_holiday_eve_night_is_never_loaded(caplog: pytest.LogCaptureFixture) -> None:
    """09-23(수) 밤은 열리지 않았다(다음 날 추석) — 09-24~27 휴장·주말엔 아무것도, 09-28 06:10 에도
    부르지 않고 로그만."""
    d = Daily(kst(D23, 15))
    d.server.add_minute_bars(CODE, "F", day_bar_times(D23))
    d.server.add_minute_bars(CODE, "CM", night_bar_times(date(2026, 9, 22)))
    with caplog.at_level(logging.INFO, logger="services.scheduler.minute"):
        d.run_until(kst(D28, 9))
    assert d.call_minutes() == [kst(D23, 16)]
    assert {c[0] for c in d.server.minute_calls()} == {"F"}
    assert d.health.kinds() == ["minute_bars_loaded"]
    (closed,) = _events(caplog, "minute_night_closed")
    assert (closed["night_start"], closed["trade_date_of_night"]) == ("2026-09-23", "2026-09-28")


def test_a_friday_night_before_a_monday_holiday_loads_on_tuesday() -> None:
    """10-02(금) 밤: 월 10-05 휴장이어도 열린다(2026-09-29 실측 — 05-22·08-14) → 화 10-06 06:10 에
    받아 화요일 귀속. 월 06:10(휴장일)엔 부르지 않는다."""
    d = Daily(kst(OCT2, 17, 50))
    d.server.add_minute_bars(CODE, "CM", night_bar_times(OCT2))
    d.run_until(kst(OCT6, 7))
    assert d.call_minutes() == [kst(OCT6, 6, 10)]
    assert [c[2:] for c in d.server.minute_calls()] == [("20261002", h) for h in NIGHT_HOURS]
    bars = d.store.of(CODE, "CM")
    assert len(bars) == 721 and {(b.trade_date, b.session) for b in bars} == {(OCT6, "night")}
    assert "FAKEFUTR" not in {c.msg_cd for c in d.server.calls}


def test_nothing_before_the_windows_open() -> None:
    """15:45~16:00(POST_DAY 앞부분)·06:00~06:10 엔 부르지 않는다 — 입력이 미래가 아니게."""
    d = Daily(kst(D28, 15, 45))
    d.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    d.server.add_minute_bars(CODE, "CM", night_bar_times(D28))
    d.run_until(kst(D28, 16), every=timedelta(seconds=10))
    d.t = kst(D29, 6)
    d.run_until(kst(D29, 6, 10), every=timedelta(seconds=10))
    assert d.server.calls == []
    d.run_until(kst(D29, 6, 11))
    assert d.call_minutes() == [kst(D29, 6, 10)] and d.health.kinds() == ["minute_bars_loaded"]


def test_a_failed_attempt_warns_and_resumes_five_minutes_later() -> None:
    d = Daily(kst(D28, 15, 59))
    d.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    d.server.inject("http500", tr_id=MINUTE_TR, params={"FID_INPUT_HOUR_1": "121100"})
    d.run_until(kst(D28, 17, 50))
    assert [(e.kind, e.at) for e in d.health.events] == [
        ("minute_bars_failed", kst(D28, 16)),
        ("minute_bars_loaded", kst(D28, 16, 5)),
    ]
    failed = d.health.events[0]
    assert failed.severity == "warning" and "HTTP 500" in failed.detail
    assert failed.detail.endswith("5분 뒤 멈춘 곳부터 다시")
    assert [c[3] for c in d.server.minute_calls()] == [
        "160000",
        "135300",
        "121100",  # 실패
        "121100",  # 5분 뒤 — 받은 것은 다시 부르지 않는다
        "102900",
        "084700",
    ]
    assert d.daily.job is not None and d.daily.job.attempts == 2
    assert len(d.store.of(CODE, "F")) == 411


def test_after_max_attempts_the_session_is_given_up() -> None:
    d = Daily(kst(D28, 15, 59), config=MinuteConfig(max_attempts=3))
    d.master = None  # 근월물을 모른다 — 부르지 않는다
    d.run_until(kst(D28, 17, 50))
    assert [(e.kind, e.at) for e in d.health.events] == [
        ("minute_bars_failed", kst(D28, 16)),
        ("minute_bars_failed", kst(D28, 16, 5)),
        ("minute_bars_missing", kst(D28, 16, 10)),
    ]
    missing = d.health.events[-1]
    assert "(시도 3회)" in missing.detail and "마스터가 없다" in missing.detail
    assert d.server.calls == []


def test_a_retry_past_the_window_end_gives_up_there() -> None:
    d = Daily(kst(D28, 17, 44))
    d.server.inject("http500", tr_id=MINUTE_TR, times=100)
    d.run_until(kst(D28, 18))
    assert [(e.kind, e.at) for e in d.health.events] == [
        ("minute_bars_failed", kst(D28, 17, 44)),
        ("minute_bars_missing", kst(D28, 17, 49)),  # 다음 17:54 는 창(17:50) 밖
    ]
    assert len(d.server.calls) == 2


def test_a_capped_window_is_a_partial_load() -> None:
    d = Daily(kst(D28, 16), config=MinuteConfig(day_max_calls=3))
    d.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    d.run_until(kst(D28, 16, 30))
    (ev,) = d.health.events
    assert (ev.kind, ev.severity) == ("minute_bars_partial", "warning")
    assert f"{CODE} F 주간 호출 상한 3회" in ev.detail


def test_a_late_first_bar_or_no_bars_is_a_partial_load() -> None:
    late = Daily(kst(D29, 6, 10))
    late.server.add_minute_bars(CODE, "CM", night_bar_times(D28, first=(21, 0)))
    late.run_until(kst(D29, 6, 30))
    (ev,) = late.health.events
    assert ev.kind == "minute_bars_partial"
    assert f"{CODE} CM 야간 첫 봉이 180분 늦다" in ev.detail

    empty = Daily(kst(D29, 6, 10))
    empty.run_until(kst(D29, 6, 30))
    (ev,) = empty.health.events
    assert ev.kind == "minute_bars_partial" and len(empty.server.calls) == 1
    assert "야간 봉 없음" in ev.detail


def test_a_restart_in_the_window_loads_the_session_again_without_new_rows() -> None:
    store = MinuteMemoryStore()
    first = Daily(kst(D28, 16), store=store)
    first.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    first.run_until(kst(D28, 16, 10))
    again = Daily(kst(D28, 16, 30), store=store)  # 재기동 — 새 프로세스
    again.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    again.run_until(kst(D28, 17, 50))
    assert len(first.server.calls) == len(again.server.calls) == 5
    assert len(store.of(CODE, "F")) == 411 and len(store.batches) == 10
    assert again.health.kinds() == ["minute_bars_loaded"]


class BoomLoader:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, job: SessionJob, master: MasterSnapshot | None) -> Attempt:
        self.calls += 1
        raise RuntimeError("boom")


def test_a_loader_error_or_a_failed_start_is_retried() -> None:
    health = MemoryHealthSink()
    boom = BoomLoader()
    daily = MinuteDaily(boom, CAL, health, submit=inline)
    t = kst(D28, 16)
    while t < kst(D28, 16, 11):
        daily.step(t)
        t += timedelta(minutes=1)
    assert boom.calls == 3 and health.kinds() == ["minute_bars_failed"] * 3
    assert "적재 오류: RuntimeError" in health.events[0].detail

    def refuse(fn: Any) -> Future[Any]:
        raise RuntimeError("can't start new thread")

    health2 = MemoryHealthSink()
    daily2 = MinuteDaily(boom, CAL, health2, submit=refuse)
    daily2.step(kst(D29, 7, 58))
    assert health2.kinds() == ["minute_bars_missing"]  # 다음 시도 08:03 은 창(08:00) 밖
    assert "시작 실패: RuntimeError" in health2.events[0].detail and boom.calls == 3


def test_a_broken_health_sink_does_not_break_the_step(caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        def emit(self, event: Any) -> None:
            raise ConnectionError("db down")

    d = Daily(kst(D28, 16))
    d.daily = MinuteDaily(d.rig.loader, CAL, Broken(), submit=inline)
    d.server.add_minute_bars(CODE, "F", day_bar_times(D28))
    with caplog.at_level(logging.ERROR, logger="services.scheduler.minute"):
        d.run_until(kst(D28, 16, 2))
    assert len(d.store.of(CODE, "F")) == 411
    assert [e["error"] for e in _events(caplog, "health_failed")] == ["ConnectionError"]


def test_the_load_runs_in_a_worker_thread_and_step_does_not_wait() -> None:
    gate, started = threading.Event(), threading.Event()
    d = Daily(kst(D28, 16), submit=minute_thread_submit)
    d.server.add_minute_bars(CODE, "F", day_bar_times(D28))

    def hold(_: dict[str, Any]) -> None:
        started.set()
        gate.wait(5)

    d.server.inject("malformed", tr_id=MINUTE_TR, mutate=hold)  # 첫 응답을 붙잡는다(본문은 그대로)
    t = kst(D28, 16)
    t0 = time_mod.monotonic()
    d.daily.step(t, d.master)  # 시작만 하고 돌아온다
    assert started.wait(5) and d.daily.busy
    for _ in range(5):
        t += timedelta(seconds=1)
        d.daily.step(t, d.master)  # 걸려 있는 동안 step 은 기다리지 않는다
    assert time_mod.monotonic() - t0 < 1.0 and d.health.events == []
    gate.set()
    deadline = time_mod.monotonic() + 5
    while not d.health.events:
        assert time_mod.monotonic() < deadline
        t += timedelta(seconds=1)
        d.daily.step(t, d.master)
        time_mod.sleep(0.01)
    assert d.health.kinds() == ["minute_bars_loaded"] and not d.daily.busy
    assert len(d.store.of(CODE, "F")) == 411


# ── scheduler 의 KIS 클라이언트 (reader_kis_client) ──


def reader_rig(r: Any, clock: FakeClock) -> tuple[FakeKisServer, MinuteLoader]:
    server = FakeKisServer(clock, default_chain(), CAL)
    kis = reader_kis_client(
        fake_settings(), r, clock=clock, now=clock.now, transport=server.transport
    )
    loader = MinuteLoader(
        kis, MinuteMemoryStore(), CAL, now=clock.now, context=lambda: load_context(r)
    )
    return server, loader


def test_the_scheduler_client_reads_the_auth_token_and_shares_the_redis_limiter() -> None:
    r, clock = fakeredis.FakeRedis(), FakeClock(kst(D28, 16))
    seed_cached_token(r, clock.now())
    server, loader = reader_rig(r, clock)
    server.add_minute_bars(CODE, "F", day_bar_times(D28))
    assert loader.run(day_job(D28), snapshot()).ok
    assert server.token_posts == 0 and set(server.bearers) == {f"Bearer {CACHED_TOKEN}"}
    assert r.exists(limiter_key("fake-key-for-tests"))  # poller·ws-gateway 와 같은 버킷
    assert len(server.minute_calls()) == 5 and server.max_in_window() <= 4


def test_the_poller_context_in_redis_picks_the_near_month() -> None:
    """poller 문맥(KIS 최종거래일)에서 A01612 가 09-29 만기면 09-29 밤은 차월물 A01703."""
    r, clock = fakeredis.FakeRedis(), FakeClock(kst(date(2026, 9, 30), 6, 10))
    seed_cached_token(r, clock.now())
    ctx = ChainContextSnapshot(
        at=kst(D29, 9),
        version=1,
        listed={"": ["202612"]},
        expiries=[ExpiryEntry(cls="", mtrt="202612", last_trade_date=D29, source="kis")],
        futures_codes=["A01612", "A01703"],
    )
    r.set(CHAIN_CONTEXT_KEY, ctx.model_dump_json())
    server, loader = reader_rig(r, clock)
    loader.run(night_job(D29, date(2026, 9, 30)), snapshot())
    assert {c[1] for c in server.minute_calls()} == {"A01703"}


def test_without_a_cached_token_nothing_is_issued_or_sent() -> None:
    r, clock = fakeredis.FakeRedis(), FakeClock(kst(D28, 16))
    server, loader = reader_rig(r, clock)
    res = loader.run(day_job(D28), snapshot())
    assert not res.ok and "TokenUnavailable" in res.detail
    assert server.token_posts == 0 and server.calls == []


def test_the_scheduler_client_needs_an_app_key() -> None:
    no_key = Settings(_env_file=None, kis_app_key=None)  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValueError, match="KIS_APP_KEY"):
        reader_kis_client(no_key, fakeredis.FakeRedis())

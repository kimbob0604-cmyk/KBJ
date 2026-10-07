"""poller 수집기 — 가짜 KIS 서버 + 가짜 시계 + 레이트리미터(프로세스 판·fakeredis 판).

설계 docs/phase1_design.md §3·§5·§7, PLAN §4.4·§6.1. 실제 KIS·네트워크는 부르지 않는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

import fakeredis
import pytest

from core.calendar import TradingCalendar
from data.kis.ratelimit import (
    US,
    LocalRateLimiter,
    Priority,
    RateLimiter,
    RateLimitTimeout,
    Reason,
    RedisRateLimiter,
)
from scripts.probe_common import (
    TR_CALLPUT,
    TR_FUT_BOARD,
    TR_INVESTOR,
    TR_OPTION_LIST,
    TR_PRICE,
    TR_TOP,
)
from services.poller.collector import Collector, ObservedLimiter
from services.poller.config import PollerConfig
from services.poller.context import Series
from services.poller.planner import fill_plan
from services.poller.records import InvestorRecord
from services.poller.sink import InMemorySink
from tests.fakes.kis_server import (
    Call,
    FakeChain,
    FakeClock,
    FakeKisServer,
    FakeSeries,
    StaticTokenProvider,
    default_chain,
    make_client,
    quarterly_expiry_chain,
)

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
DAY = (date(2026, 9, 28), "day")
NIGHT = (date(2026, 9, 29), "night")


def kst(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0, us: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, us, tzinfo=KST)


@dataclass
class Rig:
    clock: FakeClock
    server: FakeKisServer
    limiter: LocalRateLimiter | RedisRateLimiter
    tokens: StaticTokenProvider
    sink: InMemorySink
    col: Collector
    chain: FakeChain

    def run(self, until: datetime) -> None:
        self.col.run_until(until)

    def price_codes(self, since: datetime | None = None) -> list[str]:
        t0 = -1 if since is None else _us(since)
        calls = self.server.quote_calls(TR_PRICE)
        return [c.params["FID_INPUT_ISCD"] for c in calls if c.t_us >= t0]


def _us(ts: datetime) -> int:
    return round(ts.timestamp() * US)


def rig(
    start: datetime,
    cfg: PollerConfig | None = None,
    chain: FakeChain | None = None,
    redis: bool = False,
) -> Rig:
    clock = FakeClock(start)
    ch = chain or default_chain()
    server = FakeKisServer(clock, ch)
    limiter: LocalRateLimiter | RedisRateLimiter
    if redis:
        limiter = RedisRateLimiter(fakeredis.FakeRedis(), "fake-key", clock=clock)
    else:
        limiter = LocalRateLimiter(clock=clock)
    tokens = StaticTokenProvider()
    kis = make_client(server, limiter, tokens)
    sink = InMemorySink()
    col = Collector(kis, sink, calendar=CAL, clock=clock, config=cfg, master=ch.master_rows())
    return Rig(clock, server, limiter, tokens, sink, col, ch)


def board_calls(r: Rig, cls: str, mtrt: str) -> list[Call]:
    return r.server.quote_calls(TR_CALLPUT, FID_COND_MRKT_CLS_CODE=cls, FID_MTRT_CNT=mtrt)


def codes_of(chain: FakeChain, cls: str, mtrt: str) -> dict[str, tuple[str, Decimal]]:
    s = chain.find(cls, mtrt)
    assert s is not None
    return {s.code(cp, k): (cp, k) for k in s.strikes for cp in ("C", "P")}


def all_tags(sink: InMemorySink) -> set[tuple[date | None, str | None]]:
    rows: Sequence[Any] = [
        *sink.chain,
        *sink.futures,
        *sink.investor,
        *sink.expiries,
        *sink.raw,
        *sink.quarantine,
    ]
    return {(r.trade_date, r.session) for r in rows}


# ── 주간 2분 ──


def test_two_minute_day_run_matches_plan_4_4_and_stays_under_4_per_second() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    r.run(kst(2026, 9, 28, 10, 2))
    s = r.server
    # 세션 시작분: 월물리스트 3종 + 최종거래일(위클리 3 + 월물 2)
    assert len(s.quote_calls(TR_OPTION_LIST)) == 3
    assert r.col.stats.executed["expiry"] == 5
    # PLAN §4.4: 전광판 3만기 × 30초, 선물 전광판·기초자산 30초, 투자자 7조합 60초
    for cls, mtrt in [("WKM", "260904"), ("WKI", "261001"), ("", "202610")]:
        assert len(board_calls(r, cls, mtrt)) == 4
    assert len(s.quote_calls(TR_CALLPUT)) == 12
    assert len(s.quote_calls(TR_FUT_BOARD)) == 4 and len(s.quote_calls(TR_TOP)) == 4
    inv = Counter(
        (c.params["FID_INPUT_ISCD"], c.params["FID_INPUT_ISCD_2"])
        for c in s.quote_calls(TR_INVESTOR)
    )
    assert len(inv) == 7 and set(inv.values()) == {2}
    # 보강 1: 월물 ATM±20 × 콜풋 82건/60초, 보강 2: 약 1건/초
    ex = r.col.stats.executed
    assert ex["fill1"] == 164
    assert 110 <= ex["fill2"] <= 120  # 첫 전광판(약 2초) 뒤부터 1초 1건
    assert len(s.quote_calls(TR_PRICE)) == ex["expiry"] + ex["fill1"] + ex["fill2"]
    # 설계 §3: 어떤 반열린 1초 창에도 4건 이하, 오류 없음
    assert s.max_in_window(1.0) <= 4
    assert {c.status for c in s.calls} == {200} and {c.msg_cd for c in s.calls} == {"MCA00000"}
    assert r.col.stats.failed == Counter() and r.col.stats.skipped == Counter()
    # 원본은 응답마다 하나, 모든 레코드에 trade_date·session
    assert len(r.sink.raw) == len(s.calls)
    assert all_tags(r.sink) == {DAY}
    assert r.sink.health == [] and r.sink.quarantine == []
    assert len(r.sink.investor) == 14 * 12  # 7조합 × 2회 × 투자자 12종


def test_records_carry_board_and_fill_fields() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    r.run(kst(2026, 9, 28, 10, 0, 40))
    boards = [c for c in r.sink.chain if c.source == "board"]
    fills = [c for c in r.sink.chain if c.source == "fill"]
    assert boards and fills
    b = next(c for c in boards if c.mrkt_cls == "WKM" and c.strike == Decimal("1100.00"))
    assert b.bid is not None and b.ask is not None and b.oi is not None and b.gamma is not None
    assert b.expiry == "260904" and b.quality == "ok"
    f = fills[0]
    assert f.bid is None and f.ask is None  # 단건엔 호가가 없다 (#11a)
    assert f.oi is not None and f.iv_kis is not None and f.delta is not None
    fut = [x for x in r.sink.futures if x.code == "A01612"]
    assert fut and fut[0].price == Decimal("1095.10") and fut[0].source == "board"
    ex = {(e.mrkt_cls, e.expiry): e for e in r.sink.expiries}
    assert ex[("WKM", "261001")].last_trade_date == date(2026, 10, 6)
    assert ex[("WKM", "261001")].matches is True and ex[("WKM", "261001")].source == "kis"


def test_atm_follows_futures_price_not_board_display() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    r.server.futures_price = Decimal("1101.30")  # ATM 1102.5 — 전광판 'ATM' 표시는 1125.0
    r.run(kst(2026, 9, 28, 10, 1))
    fut = r.col.ctx.futures
    assert fut is not None and fut.price == Decimal("1101.30") and fut.code == "A01612"
    targets = r.col.ctx.targets(r.clock.now())
    assert targets is not None
    fp = fill_plan(r.col.ctx, targets, "A", r.col.cfg)
    assert fp is not None
    f1 = fp.fill1[0].targets
    strikes = sorted({t.strike for t in f1})
    assert (strikes[0], strikes[-1]) == (Decimal("1052.50"), Decimal("1152.50"))
    assert (f1[0].strike, f1[0].cp) == (Decimal("1102.50"), "C")
    f1_codes = {t.code for t in f1}
    called = [c for c in r.price_codes() if c in f1_codes]
    assert len(called) == 82 and called[0] == f1[0].code  # ATM 콜부터
    shown = {
        row["acpr"]
        for env in r.sink.raw
        if env.tr_id == TR_CALLPUT and isinstance(env.payload, dict)
        for row in env.payload["output1"]
        if row["atm_cls_name"] == "ATM"
    }
    assert shown == {"1125.00"}  # 전광판이 표시한 ATM 은 녹화만 되고 쓰이지 않는다


# ── 경합·한도초과 ──


def test_priorities_under_contention_p4_is_skipped_first() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    r.limiter.on_rate_limited()  # 같은 앱키의 다른 프로세스가 한도초과 → 60초간 2.0/s
    r.run(kst(2026, 9, 28, 10, 2))
    st = r.col.stats
    assert len(r.server.quote_calls(TR_CALLPUT)) == 12  # P1 은 기다려서라도 한다
    assert len(r.server.quote_calls(TR_FUT_BOARD)) == 4
    assert len(r.server.quote_calls(TR_INVESTOR)) == 14  # P2 도 이 정도 경합이면 제시간에
    assert st.skipped[Priority.P1] == st.skipped[Priority.P0] == 0
    assert st.skipped[Priority.P4] > 30 and st.skipped[Priority.P4] > st.skipped[Priority.P3]
    assert st.executed["fill1"] >= 150
    assert st.executed["fill2"] < 90
    first_minute = [c.t_us for c in r.server.calls if c.t_us < _us(kst(2026, 9, 28, 10, 1))]
    assert min(b - a for a, b in pairwise(first_minute)) >= 500_000


def test_rate_limited_board_slows_down_and_retries_once() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    r.server.inject("rate_limit", tr_id=TR_CALLPUT, params={"FID_MTRT_CNT": "202610"})
    r.run(kst(2026, 9, 28, 10, 0, 40))
    calls = board_calls(r, "", "202610")
    assert [c.msg_cd for c in calls[:2]] == ["EGW00201", "MCA00000"]
    # 다음 토큰에서 1회 — 반감(2.0/s) 간격 0.5초보다 전광판 TR 최소 간격 1초가 길다
    assert calls[1].t_us - calls[0].t_us == 1_000_000
    assert len(calls) == 3  # 재시도 1회 + 다음 주기
    hl = [e for e in r.sink.health if e.kind == "rate_limited"]
    assert len(hl) == 1 and hl[0].detail["rate"] == 2.0 and hl[0].detail["job"] == "board:M:202610"
    assert r.limiter.current_rate() == 2.0
    after = [c.t_us for c in r.server.calls if c.t_us >= calls[0].t_us]
    assert min(b - a for a, b in pairwise(after)) >= 500_000
    monthly_rows = [c for c in r.sink.chain if c.source == "board" and c.expiry == "202610"]
    assert len(monthly_rows) == 2 * 200  # 재시도 응답과 다음 주기 응답


def test_rate_limited_twice_gives_up_until_next_cycle() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    r.server.inject("rate_limit", tr_id=TR_CALLPUT, params={"FID_MTRT_CNT": "260904"}, times=2)
    r.run(kst(2026, 9, 28, 10, 0, 40))
    assert [c.msg_cd for c in board_calls(r, "WKM", "260904")] == [
        "EGW00201",
        "EGW00201",
        "MCA00000",
    ]  # 세 번째는 다음 주기(30초)
    assert r.col.stats.failed["board"] == 1
    assert [e.kind for e in r.sink.health].count("rate_limited") == 2


class _ContendedLimiter:
    """한도초과 뒤 P2 재시도 허가가 대기 한도까지 밀린다(공유 Redis 리미터에서 다른 프로세스의
    P1 대기열 뒤에 선 것처럼) — 그 재시도는 보내지 않는다."""

    def __init__(self, inner: LocalRateLimiter, clock: FakeClock) -> None:
        self.inner = inner
        self.clock = clock
        self.armed = False
        self.signals: list[float] = []

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        if self.armed and priority == Priority.P2:
            self.armed = False
            self.clock.sleep(timeout or 0.0)
            raise RateLimitTimeout(priority, tr_id, Reason.PRIORITY)
        self.inner.acquire(priority, tr_id, timeout)

    def on_rate_limited(self) -> float:
        rate = self.inner.on_rate_limited()
        self.signals.append(rate)
        self.armed = True
        return rate


def test_rate_limited_retry_not_sent_is_signalled_once() -> None:
    clock = FakeClock(kst(2026, 9, 28, 10, 0))
    ch = default_chain()
    server = FakeKisServer(clock, ch)
    inner = LocalRateLimiter(clock=clock)
    limiter = _ContendedLimiter(inner, clock)
    sink = InMemorySink()
    kis = make_client(server, limiter, StaticTokenProvider())
    col = Collector(kis, sink, calendar=CAL, clock=clock, master=ch.master_rows())
    server.inject("rate_limit", tr_id=TR_INVESTOR, params={"FID_INPUT_ISCD_2": "F001"})
    col.run_until(kst(2026, 9, 28, 10, 0, 20))
    f001 = server.quote_calls(TR_INVESTOR, FID_INPUT_ISCD_2="F001")
    assert [c.msg_cd for c in f001] == ["EGW00201"]  # 재시도는 허가를 못 받아 보내지 않았다
    # 한도초과 응답은 하나 — 감속도 한 번(2.0/s), 없는 두 번째 응답으로 1.0/s 까지 내리지 않는다
    assert limiter.signals == [2.0] and inner.current_rate() == 2.0
    assert [e.kind for e in sink.health].count("rate_limited") == 1
    assert col.stats.slowdowns == 1 and col.stats.failed["investor"] == 1


def test_rate_limited_p4_is_not_retried_and_signalled_once() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    monthly = r.chain.find("", "202610")
    assert monthly is not None
    first_fill2 = monthly.code("C", Decimal("1147.5"))  # 전광판·보강 1 밖에서 ATM 에 가장 가까운 행
    r.server.inject("rate_limit", tr_id=TR_PRICE, params={"FID_INPUT_ISCD": first_fill2})
    r.run(kst(2026, 9, 28, 10, 0, 10))
    calls = r.server.quote_calls(TR_PRICE, FID_INPUT_ISCD=first_fill2)
    assert [c.msg_cd for c in calls] == ["EGW00201"]  # P4 는 재시도 허가를 기다리지 않는다
    assert [e.kind for e in r.sink.health] == ["rate_limited"]  # 신호도 한 번
    assert r.col.stats.failed["fill2"] == 1
    assert "M:202610:1147.50:C" not in r.col.runs.fill2_done  # 받지 못한 대상은 다음 차례에
    r.run(kst(2026, 9, 28, 10, 1, 30))
    calls = r.server.quote_calls(TR_PRICE, FID_INPUT_ISCD=first_fill2)
    assert [c.msg_cd for c in calls] == ["EGW00201", "MCA00000"]
    assert r.col.stats.skipped[Priority.P4] > 0  # 감속 중엔 P4 부터 빠진다
    assert r.col.stats.skipped[Priority.P1] == 0


# ── 검증 실패·오류 격리 ──


def _break_board(body: dict[str, Any]) -> None:
    body["output1"][0]["acpr"] = "abc"  # 행사가를 못 읽는 행 — 격리만
    body["output2"][1]["hts_otst_stpl_qty"] = "x"  # 행사가는 읽힌다 — invalid 레코드


def test_malformed_rows_are_quarantined_and_cycle_continues() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    r.server.inject(
        "malformed", tr_id=TR_CALLPUT, params={"FID_MTRT_CNT": "260904"}, mutate=_break_board
    )
    r.server.inject(
        "malformed",
        tr_id=TR_INVESTOR,
        params={"FID_INPUT_ISCD_2": "F001"},
        mutate=lambda b: b["output"][0].pop("frgn_seln_vol"),
    )
    monthly = r.chain.find("", "202610")
    assert monthly is not None
    atm_call = monthly.code("C", Decimal("1095.0"))
    r.server.inject(
        "malformed",
        tr_id=TR_PRICE,
        params={"FID_INPUT_ISCD": atm_call},
        mutate=lambda b: b["output1"].update(acpr="1234.50"),
    )
    r.run(kst(2026, 9, 28, 10, 1))
    q = Counter(x.tr_id for x in r.sink.quarantine)
    assert q == {TR_CALLPUT: 2, TR_INVESTOR: 1, TR_PRICE: 1}
    assert all(x.trade_date == DAY[0] and x.session == "day" for x in r.sink.quarantine)
    wk = [c for c in r.sink.chain if c.expiry == "260904" and c.source == "board"]
    first_cycle = [c for c in wk if c.ts == wk[0].ts]
    assert len(first_cycle) == 199  # 200 − 행사가 못 읽은 1
    bad = [c for c in first_cycle if c.quality == "invalid"]
    assert len(bad) == 1 and bad[0].cp == "P" and bad[0].oi is None
    inv = Counter(x.sector_code for x in r.sink.investor)
    assert "F001" not in inv and set(inv.values()) == {12} and len(inv) == 6  # 그 조합만 격리
    fill_bad = [c for c in r.sink.chain if c.code == atm_call]
    assert [c.quality for c in fill_bad][:1] == ["invalid"]
    assert r.col.stats.executed["fill1"] == 82  # 주기는 계속
    qh = {e.detail["job"]: e.detail["rows"] for e in r.sink.health if e.kind == "quarantine"}
    assert qh == {"board:WKM:260904": 2, "investor:K2I/F001": 1, "fill1:M:202610:1095.00:C": 1}


def test_negative_iv_is_an_outlier_but_zero_iv_is_no_value() -> None:
    """PLAN §6.1 IV ≤ 0 — KIS 의 0 은 '값 없음'(None)으로 두고, 음수만 invalid + quarantine."""
    r = rig(kst(2026, 9, 28, 10, 0))

    def board(b: dict[str, Any]) -> None:
        b["output1"][0]["hts_ints_vltl"] = "-3.50"
        b["output1"][1]["hts_ints_vltl"] = "0.00"

    r.server.inject("malformed", tr_id=TR_CALLPUT, params={"FID_MTRT_CNT": "260904"}, mutate=board)
    monthly = r.chain.find("", "202610")
    assert monthly is not None
    atm_put = monthly.code("P", Decimal("1095.0"))
    r.server.inject(
        "malformed",
        tr_id=TR_PRICE,
        params={"FID_INPUT_ISCD": atm_put},
        mutate=lambda b: b["output1"].update(hts_ints_vltl="-0.01"),
    )
    r.run(kst(2026, 9, 28, 10, 0, 20))
    wk = [c for c in r.sink.chain if c.expiry == "260904" and c.source == "board" and c.cp == "C"]
    top = {c.strike: c for c in wk}
    neg, zero = top[Decimal("1245.00")], top[Decimal("1242.50")]
    assert (neg.quality, neg.iv_kis) == ("invalid", None)
    assert (zero.quality, zero.iv_kis) == ("ok", None)
    fill = next(c for c in r.sink.chain if c.code == atm_put)
    assert fill.quality == "invalid"
    assert Counter(q.error for q in r.sink.quarantine) == {"이상치: iv_kis": 2}


def test_http500_and_token_errors_are_isolated_per_job() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    r.server.inject("http500", tr_id=TR_INVESTOR, params={"FID_INPUT_ISCD_2": "OC05"}, times=2)
    r.server.inject("token", tr_id=TR_FUT_BOARD)
    r.run(kst(2026, 9, 28, 10, 2))
    assert r.tokens.invalidated == 1  # 토큰 거절 → 제공자에 알리고 1회 재시도
    assert len(r.server.quote_calls(TR_FUT_BOARD)) == 5
    assert len({x.ts for x in r.sink.futures}) == 4
    oc05 = [x for x in r.sink.investor if x.sector_code == "OC05"]
    assert oc05 == []  # 두 회차 모두 실패
    assert {x.sector_code for x in r.sink.investor} == {
        "F001",
        "OC01",
        "OP01",
        "OP05",
        "OC04",
        "OP04",
    }
    failed = [e for e in r.sink.health if e.kind == "job_failed"]
    assert len(failed) == 1 and failed[0].detail["consecutive"] == 2  # 연속 2회째에 health
    assert r.col.stats.executed["fill1"] == 164


def _reject_code(body: dict[str, Any]) -> None:
    body.clear()
    body.update({"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "없는 종목"})


@pytest.mark.parametrize("fault", ["http500", "rejected"])
def test_failing_fill2_target_does_not_stall_the_rotation(fault: str) -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    monthly = r.chain.find("", "202610")
    assert monthly is not None
    bad = monthly.code("C", Decimal("1147.5"))  # 보강 2 첫 대상
    if fault == "http500":
        r.server.inject("http500", tr_id=TR_PRICE, params={"FID_INPUT_ISCD": bad}, times=10_000)
    else:  # KIS 가 코드를 거절(rt_cd=1) — HTTP 200
        r.server.inject(
            "malformed",
            tr_id=TR_PRICE,
            params={"FID_INPUT_ISCD": bad},
            times=10_000,
            mutate=_reject_code,
        )
    r.run(kst(2026, 9, 28, 10, 2))
    # 실패한 대상도 이번 바퀴엔 끝난 것으로 치고 다음 대상으로 넘어간다 (설계 §2: 다음 주기에 다시)
    assert r.price_codes().count(bad) == 1  # 한 바퀴(444건 ≈ 7분) 안에 한 번
    assert r.col.stats.failed["fill2"] == 1
    assert 110 <= r.col.stats.executed["fill2"] <= 120
    assert "M:202610:1147.50:C" in r.col.runs.fill2_done
    assert "job_failed" not in r.sink.health_kinds()  # 연속 실패가 아니다


def test_skipped_fill2_slot_keeps_its_target_for_the_next_slot() -> None:
    """허가를 못 받아 건너뛴 슬롯(설계 §3 P4)은 호출이 없었으니 순서를 넘기지 않는다."""
    r = rig(kst(2026, 9, 28, 10, 0))
    r.limiter.on_rate_limited()  # 다른 프로세스 한도초과 → 2.0/s, P4 가 먼저 빠진다
    r.run(kst(2026, 9, 28, 10, 2))
    assert r.col.stats.skipped[Priority.P4] > 30
    targets = r.col.ctx.targets(r.clock.now())
    assert targets is not None
    fp = fill_plan(r.col.ctx, targets, "A", r.col.cfg)
    assert fp is not None
    order = [t.code for t in fp.fill2]
    f2 = set(order)
    after_expiry = r.price_codes()[r.col.stats.executed["expiry"] :]  # P0 최종거래일이 먼저
    called = [c for c in after_expiry if c in f2]
    assert len(called) == r.col.stats.executed["fill2"]
    assert called == order[: len(called)]  # 빠진 대상 없이 순서대로


class _BrokenInvestorSink(InMemorySink):
    def write_investor(self, rows: Sequence[InvestorRecord]) -> None:
        raise RuntimeError("db down")


def test_sink_failure_does_not_stop_collection() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    sink = _BrokenInvestorSink()
    r.col.sink = sink
    r.run(kst(2026, 9, 28, 10, 0, 30))
    assert r.col.stats.sink_errors == 7 and sink.investor == []
    assert sink.chain and sink.futures


# ── 만기 전환·커버리지 ──


def test_expiry_switch_at_1520_on_2026_09_28() -> None:
    r = rig(kst(2026, 9, 28, 15, 19, 40))
    r.run(kst(2026, 9, 28, 15, 20, 40))
    switch = _us(kst(2026, 9, 28, 15, 20))
    old = board_calls(r, "WKM", "260904")
    assert old and all(c.t_us < switch for c in old)
    new = board_calls(r, "WKM", "261001")
    assert new and all(c.t_us >= switch for c in new)
    assert [c.t_us for c in board_calls(r, "WKI", "261001")][-1] >= switch
    wk = codes_of(r.chain, "WKM", "260904")
    assert not any(c in wk for c in r.price_codes(since=kst(2026, 9, 28, 15, 20)))
    after = [c for c in r.sink.chain if c.ts >= kst(2026, 9, 28, 15, 20)]
    assert after and "260904" not in {c.expiry for c in after if c.mrkt_cls == "WKM"}


def test_quarterly_expiry_1520_moves_futures_reference_to_next_contract() -> None:
    """분기 만기일(2026-12-10) 15:20 뒤엔 만기 지난 12월물을 ATM 기준가로 쓰지 않는다.

    전광판은 15:20 뒤에도 12월물(잔존일수가 가장 짧다)을 보여 주지만 근월물은 3월물이다 — ws-gateway
    가 구독 선물을 바꾸는 규칙(`ChainContext.live_futures_codes`)과 같다.
    """
    r = rig(kst(2026, 12, 10, 15, 19, 40), chain=quarterly_expiry_chain())
    r.run(kst(2026, 12, 10, 15, 19, 59))
    ref = r.col.ctx.reference(r.clock.now(), CAL)
    assert ref is not None and (ref.code, ref.price) == ("A01612", Decimal("1095.10"))
    switch = kst(2026, 12, 10, 15, 20)
    # 15:20 이 지나면 새 시세가 오기 전이라도 12월물 시세는 기준가가 아니다
    assert r.col.ctx.reference(switch, CAL) is None
    r.run(kst(2026, 12, 10, 15, 21, 10))
    now = r.clock.now()
    ref = r.col.ctx.reference(now, CAL)
    assert ref is not None and (ref.code, ref.price) == ("A01703", Decimal("1085.00"))
    assert r.col.ctx.live_futures_codes(now, CAL) == ("A01703",)
    assert r.col.ctx.futures_codes == ("A01612", "A01703")  # 전광판 순서는 그대로 둔다
    # 12월물 행은 15:20 뒤에도 원문대로 적재한다
    assert any(f.code == "A01612" and f.ts >= switch for f in r.sink.futures)
    top = [
        c.params["FID_INPUT_ISCD"] for c in r.server.quote_calls(TR_TOP) if c.t_us >= _us(switch)
    ]
    assert top and set(top) == {"A01703"}
    # 보강 1: 월물이 202612 → 202701 로, ATM±20 은 3월물 1085.00 기준(12월물 기준이면 1045~1145)
    targets = r.col.ctx.targets(now)
    assert targets is not None and targets.monthly == Series("", "202701")
    fp = fill_plan(r.col.ctx, targets, "A", r.col.cfg)
    assert fp is not None
    f1 = fp.fill1[0].targets
    ks = sorted({t.strike for t in f1})
    assert (ks[0], ks[-1], f1[0].strike, f1[0].cp) == (
        Decimal("1035.0"),
        Decimal("1135.0"),
        Decimal("1085.0"),
        "C",
    )
    after = r.price_codes(since=switch)
    new_m = codes_of(r.chain, "", "202701")
    assert next(c for c in after if c in new_m) == f1[0].code  # 첫 보강부터 3월물 ATM 콜
    assert {t.code for t in f1} <= set(after)  # 15:20~15:21 한 주기를 다 불렀다
    old_m = codes_of(r.chain, "", "202612")
    assert not any(c in old_m for c in r.price_codes(since=switch))
    assert "no_futures_price" not in r.sink.health_kinds()


def test_quarterly_expiry_night_b_skips_expired_futures() -> None:
    """분기 만기일 밤(야간 B): 선물 단건 CM 은 3월물부터, 최근접 ATM 도 3월물 가격으로."""
    cfg = PollerConfig(night_mode="B")
    r = rig(kst(2026, 12, 10, 21, 0), cfg, chain=quarterly_expiry_chain())
    r.run(kst(2026, 12, 10, 21, 1))
    fut = r.server.quote_calls(TR_PRICE, FID_COND_MRKT_DIV_CODE="CM")
    assert fut and {c.params["FID_INPUT_ISCD"] for c in fut} == {"A01703"}
    now = r.clock.now()
    ref = r.col.ctx.reference(now, CAL)
    assert ref is not None and (ref.code, ref.price, ref.source) == (
        "A01703",
        Decimal("1085.00"),
        "single",
    )
    targets = r.col.ctx.targets(now)
    assert targets is not None and targets.nearest == Series("WKM", "261202")
    assert targets.monthly == Series("", "202701")
    fp = fill_plan(r.col.ctx, targets, "B", cfg)
    assert fp is not None
    near = fp.fill1[0].targets
    assert (near[0].series, near[0].strike, near[0].cp) == (
        Series("WKM", "261202"),
        Decimal("1085.0"),
        "C",
    )
    wk = codes_of(r.chain, "WKM", "261202")
    called = {wk[c][1] for c in r.price_codes() if c in wk}
    assert Decimal("1035.0") in called and Decimal("1135.0") in called
    assert all_tags(r.sink) == {(date(2026, 12, 11), "night")}


def test_quarterly_expiry_master_reload_without_expired_futures_keeps_next_contract() -> None:
    """분기 만기일 밤 새 마스터에 만기 지난 12월물(`F 202612`)이 빠졌어도 되살리지 않는다.

    선물 전광판 순서(`futures_codes`)는 주간 마지막 응답의 12월물·3월물 그대로다(야간 B 는 전광판을
    부르지 않는다). 마스터에 코스피200 선물이 있는데 그 종목이 없으면 상장 종목이 아니다 — 결제월을
    몰라 살아 있다고 보면 12월물이 다시 근월물이 되어 CM 단건·ATM 기준가가 만기 지난 종목으로 간다.
    """
    cfg = PollerConfig(night_mode="B")
    ch = quarterly_expiry_chain()
    r = rig(kst(2026, 12, 10, 15, 19, 40), cfg, chain=ch)
    r.run(kst(2026, 12, 10, 15, 21, 10))
    assert r.col.ctx.futures_codes == ("A01612", "A01703")
    r.run(kst(2026, 12, 10, 17, 50))  # PRE_NIGHT — scheduler 가 새 마스터를 둔다
    r.col.set_master([m for m in ch.master_rows() if m.code != "A01612"])
    night = kst(2026, 12, 10, 18, 0)
    r.run(kst(2026, 12, 10, 18, 1))
    now = r.clock.now()
    assert r.col.ctx.live_futures_codes(now, CAL) == ("A01703",)
    fut = [
        c.params["FID_INPUT_ISCD"]
        for c in r.server.quote_calls(TR_PRICE, FID_COND_MRKT_DIV_CODE="CM")
        if c.t_us >= _us(night)
    ]
    assert fut and set(fut) == {"A01703"}
    ref = r.col.ctx.reference(now, CAL)
    assert ref is not None and (ref.code, ref.price, ref.source) == (
        "A01703",
        Decimal("1085.00"),
        "single",
    )
    # 보강 ATM 도 3월물 가격(1085.00) 기준 — 12월물(1095.10) 기준이면 첫 대상이 1095.0
    targets = r.col.ctx.targets(now)
    assert targets is not None
    fp = fill_plan(r.col.ctx, targets, "B", cfg)
    assert fp is not None and fp.fill1[0].targets[0].strike == Decimal("1085.0")
    old_m = codes_of(r.chain, "", "202612")
    assert not any(c in old_m for c in r.price_codes(since=night))


def test_weekly_board_coverage_warning_when_atm_is_outside() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    r.server.futures_price = Decimal("990.00")  # 위클리 전광판은 997.5~1245.0 (111개 중 위 100)
    r.run(kst(2026, 9, 28, 10, 0, 20))
    cov = [e for e in r.sink.health if e.kind == "board_coverage"]
    # 월물은 전광판 밖이어도 보강 1 범위가 덮는다
    assert {e.detail["series"] for e in cov} == {"WKM:260904", "WKI:261001"}
    assert cov[0].detail["atm"] == "990.00" and cov[0].detail["board_min"] == "997.50"
    assert any(c.source == "board" and c.expiry == "260904" for c in r.sink.chain)


@pytest.mark.parametrize(("keep", "dropped"), [(79, True), (80, False)])
def test_board_strike_drop_over_20pct_marks_snapshot_invalid(keep: int, dropped: bool) -> None:
    """PLAN §6.1: 스냅샷 간 행사가 개수 급감(> 20%) — 같은 시리즈 직전 전광판과 비교."""
    r = rig(kst(2026, 9, 28, 10, 0))

    def truncate(b: dict[str, Any]) -> None:
        b["output1"] = b["output1"][:keep]
        b["output2"] = b["output2"][:keep]

    r.server.inject(
        "malformed",
        tr_id=TR_CALLPUT,
        params={"FID_COND_MRKT_CLS_CODE": "WKI", "FID_MTRT_CNT": "261001"},
        mutate=truncate,
        after=kst(2026, 9, 28, 10, 0, 20),
    )
    r.run(kst(2026, 9, 28, 10, 1, 10))
    rows = [c for c in r.sink.chain if c.mrkt_cls == "WKI" and c.source == "board"]
    snaps = sorted({c.ts for c in rows})
    assert len(snaps) == 3
    by_snap = [Counter(c.quality for c in rows if c.ts == t) for t in snaps]
    drop = [e for e in r.sink.health if e.kind == "board_strike_drop"]
    if dropped:
        assert by_snap == [{"ok": 200}, {"invalid": 2 * keep}, {"ok": 200}]
        assert len(drop) == 1 and drop[0].level == "warning"
        d = drop[0].detail
        assert (d["series"], d["previous"], d["strikes"]) == ("WKI:261001", 100, keep)
    else:
        assert by_snap == [{"ok": 200}, {"ok": 2 * keep}, {"ok": 200}]
        assert drop == []
    # 다른 시리즈는 그대로
    others = [c for c in r.sink.chain if c.mrkt_cls != "WKI" and c.source == "board"]
    assert others and {c.quality for c in others} == {"ok"}


# ── 야간 A/B/C ──


def test_night_mode_a_tags_next_trading_day_and_skips_expired_weekly() -> None:
    r = rig(kst(2026, 9, 28, 21, 0), PollerConfig(night_mode="A"))
    r.run(kst(2026, 9, 28, 21, 0, 40))
    assert all_tags(r.sink) == {NIGHT}
    assert board_calls(r, "WKM", "260904") == []
    assert {
        (c.params["FID_COND_MRKT_CLS_CODE"], c.params["FID_MTRT_CNT"])
        for c in r.server.quote_calls(TR_CALLPUT)
    } == {("WKI", "261001"), ("WKM", "261001"), ("", "202610")}


def test_night_mode_b_uses_single_price_eu_and_futures_cm() -> None:
    r = rig(kst(2026, 9, 28, 21, 0), PollerConfig(night_mode="B"))
    r.run(kst(2026, 9, 28, 21, 1))
    s = r.server
    assert s.quote_calls(TR_CALLPUT) == [] and s.quote_calls(TR_FUT_BOARD) == []
    fut = [c for c in s.quote_calls(TR_PRICE) if c.params["FID_INPUT_ISCD"].startswith("A0")]
    assert {c.params["FID_COND_MRKT_DIV_CODE"] for c in fut} == {"CM"} and len(fut) == 4
    opt = [c for c in s.quote_calls(TR_PRICE, FID_COND_MRKT_DIV_CODE="EU")]
    near = codes_of(r.chain, "WKI", "261001")
    monthly = codes_of(r.chain, "", "202610")
    ex = r.col.stats.executed
    assert ex["fill1"] == 82 + 41  # 최근접 82건/60초 + 월물 82건/120초의 앞 절반
    assert sum(c.params["FID_INPUT_ISCD"] in near for c in opt) >= 82
    assert sum(c.params["FID_INPUT_ISCD"] in monthly for c in opt) >= 41
    assert r.col.ctx.futures is not None and r.col.ctx.futures.source == "single"
    assert {x.source for x in r.sink.futures} == {"single"}
    assert {c.source for c in r.sink.chain} == {"fill"}
    assert all_tags(r.sink) == {NIGHT}


def test_night_mode_c_only_investor_and_day_rows_become_estimated() -> None:
    r = rig(kst(2026, 9, 28, 10, 0), PollerConfig(night_mode="C"))
    r.run(kst(2026, 9, 28, 10, 0, 40))
    day_calls = len(r.server.calls)
    r.clock.set(kst(2026, 9, 28, 21, 0))
    r.run(kst(2026, 9, 28, 21, 1))
    night = Counter(c.tr_id for c in r.server.calls[day_calls:])
    # 설계 §7 C: 야간 옵션 REST 없음 — 최종거래일도 부르지 않고 주간에 받은 KIS 값을 그대로 쓴다
    assert night == {TR_OPTION_LIST: 3, TR_INVESTOR: 7}
    assert {e.source for e in r.col.ctx.expiries.values()} == {"kis"}
    assert "expiry_unresolved" not in r.sink.health_kinds()
    view = r.col.chain_view()
    assert view and {c.quality for c in view} == {"estimated"}  # 직전 주간 값의 대체 (설계 §7 C)


def test_night_mode_c_fresh_start_uses_calendar_expiries_without_calls() -> None:
    r = rig(kst(2026, 9, 28, 17, 55), PollerConfig(night_mode="C"))  # PRE_NIGHT 에 새로 뜬 poller
    r.run(kst(2026, 9, 28, 18, 5))
    assert r.server.quote_calls(TR_PRICE) == []
    assert Counter(c.tr_id for c in r.server.calls) == {TR_OPTION_LIST: 6, TR_INVESTOR: 35}
    ex = {(e.mrkt_cls, e.expiry): e for e in r.sink.expiries}
    wki = ex[("WKI", "261001")]
    assert (wki.source, wki.last_trade_date, wki.quality) == (
        "calendar",
        date(2026, 10, 1),
        "estimated",
    )
    assert {e.source for e in r.sink.expiries} == {"calendar"} and len(r.sink.expiries) == 5
    unresolved = [e for e in r.sink.health if e.kind == "expiry_unresolved"]
    assert len(unresolved) == 5
    assert {e.level for e in unresolved} == {"info"}  # 설정대로 부르지 않았다 — 경고 아님
    assert all_tags(r.sink) == {NIGHT}


# ── 쉬는 때 ──


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (kst(2026, 9, 24, 10, 0), kst(2026, 9, 24, 10, 5)),  # 추석 전날 휴장
        (kst(2026, 9, 28, 6, 30), kst(2026, 9, 28, 7, 30)),  # IDLE
        (kst(2026, 9, 28, 16, 0), kst(2026, 9, 28, 17, 0)),  # POST_DAY
        (kst(2026, 9, 23, 20, 0), kst(2026, 9, 23, 21, 0)),  # 휴장 전날 밤
    ],
)
def test_no_calls_when_idle_or_closed(start: datetime, end: datetime) -> None:
    r = rig(start)
    r.run(end)
    assert r.server.calls == [] and r.sink.raw == []
    assert r.clock.now() == end


def test_pre_day_resolves_expiries_once_for_the_day_session() -> None:
    r = rig(kst(2026, 9, 28, 8, 40))
    r.run(kst(2026, 9, 28, 8, 45, 10))
    pre = [c for c in r.server.calls if c.t_us < _us(kst(2026, 9, 28, 8, 45))]
    assert Counter(c.tr_id for c in pre) == {TR_OPTION_LIST: 3, TR_PRICE: 5}
    assert r.col.stats.executed["expiry"] == 5  # DAY 에 다시 부르지 않는다
    assert len(r.server.quote_calls(TR_OPTION_LIST)) == 6  # 상태 진입마다 목록은 다시
    assert {(e.trade_date, e.session) for e in r.sink.expiries} == {DAY}


# ── 최종거래일 ──


def test_expiry_mismatch_uses_kis_and_warns() -> None:
    base = default_chain()
    series = tuple(
        FakeSeries(s.cls, s.mtrt, date(2026, 10, 2), s.lo, s.hi) if s.cls == "WKI" else s
        for s in base.series
    )
    r = rig(kst(2026, 9, 28, 10, 0), chain=FakeChain(series))
    r.run(kst(2026, 9, 28, 10, 0, 10))
    mm = [e for e in r.sink.health if e.kind == "expiry_mismatch"]
    assert (
        len(mm) == 1
        and mm[0].detail["kis"] == "2026-10-02"
        and mm[0].detail["calendar"] == ("2026-10-01")
    )
    info = r.col.ctx.expiries[Series("WKI", "261001")]
    assert (info.last_trade_date, info.source) == (date(2026, 10, 2), "kis")


def test_expiry_without_master_code_falls_back_to_calendar() -> None:
    base = default_chain()
    r = rig(kst(2026, 9, 28, 10, 0), chain=base)
    rows = [m for m in base.master_rows() if "2610W1" not in m.name or m.name.startswith("위클리C")]
    r.col.set_master(rows)  # WKM 261001 이 마스터에 없다
    r.run(kst(2026, 9, 28, 10, 0, 10))
    ex = {(e.mrkt_cls, e.expiry): e for e in r.sink.expiries}
    wkm = ex[("WKM", "261001")]
    assert (wkm.source, wkm.last_trade_date, wkm.quality) == (
        "calendar",
        date(2026, 10, 6),
        "estimated",
    )
    assert "expiry_unresolved" in r.sink.health_kinds()
    assert r.col.stats.executed["expiry"] == 4


def _expiry_code(r: Rig, s: Series) -> str:
    """planner 가 최종거래일 조회에 쓰는 코드 — 마스터 가운데 행사가 콜."""
    ch = r.col.ctx.chain(s)
    assert ch is not None
    code = ch.code(ch.strikes[len(ch.strikes) // 2], "C")
    assert code is not None
    return code


def test_failed_expiry_lookup_is_retried_in_the_same_session() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    wki = Series("WKI", "261001")
    code = _expiry_code(r, wki)
    r.server.inject("http500", tr_id=TR_PRICE, params={"FID_INPUT_ISCD": code})
    r.run(kst(2026, 9, 28, 10, 2))
    calls = r.server.quote_calls(TR_PRICE, FID_INPUT_ISCD=code)
    assert [c.status for c in calls] == [500, 200]  # 설계 §2: 실패 호출은 다음 주기에 다시
    assert calls[1].t_us - calls[0].t_us == 60 * US  # option_list 재시도와 같은 60초
    info = r.col.ctx.expiries[wki]
    assert (info.last_trade_date, info.source) == (date(2026, 10, 1), "kis")
    recs = [(e.source, e.quality) for e in r.sink.expiries if e.mrkt_cls == "WKI"]
    assert recs == [("calendar", "estimated"), ("kis", "ok")]  # 그 사이엔 캘린더 값
    assert r.col.stats.executed["expiry"] == 5  # 받은 뒤엔 이 세션에서 다시 부르지 않는다


def test_persistent_expiry_failure_retries_quietly() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    code = _expiry_code(r, Series("WKI", "261001"))
    r.server.inject("http500", tr_id=TR_PRICE, params={"FID_INPUT_ISCD": code}, times=10_000)
    r.run(kst(2026, 9, 28, 10, 5))
    assert len(r.server.quote_calls(TR_PRICE, FID_INPUT_ISCD=code)) == 5  # 60초마다
    kinds = r.sink.health_kinds()
    assert kinds.count("expiry_unresolved") == 1  # 세션에 한 번 — 계속되는 실패는 job_failed
    assert kinds.count("job_failed") == 4
    assert [e.source for e in r.sink.expiries if e.mrkt_cls == "WKI"] == ["calendar"]


def test_master_arriving_late_resolves_expiries_from_kis() -> None:
    r = rig(kst(2026, 9, 28, 8, 40))
    r.col.set_master([])  # scheduler 가 아직 마스터를 주지 않았다
    r.run(kst(2026, 9, 28, 8, 40, 30))
    assert {e.source for e in r.col.ctx.expiries.values()} == {"calendar"}
    assert r.col.stats.executed["expiry"] == 0
    r.col.set_master(r.chain.master_rows())
    r.run(kst(2026, 9, 28, 8, 41))  # 재시도 시각을 기다리지 않고 곧바로
    assert r.col.stats.executed["expiry"] == 5
    assert {e.source for e in r.col.ctx.expiries.values()} == {"kis"}
    r.run(kst(2026, 9, 28, 9, 0))
    assert r.col.stats.executed["expiry"] == 5
    kis = [e for e in r.sink.expiries if e.source == "kis"]
    assert len(kis) == 5 and {(e.trade_date, e.session) for e in kis} == {DAY}


# ── Redis 리미터 ──


def test_same_cadence_with_shared_redis_limiter() -> None:
    r = rig(kst(2026, 9, 28, 10, 0), redis=True)
    r.run(kst(2026, 9, 28, 10, 1))
    assert len(r.server.quote_calls(TR_CALLPUT)) == 6
    assert r.col.stats.executed["fill1"] == 82
    assert r.server.max_in_window(1.0) <= 4


def test_collector_requires_a_rate_limiter() -> None:
    clock = FakeClock(kst(2026, 9, 28, 10, 0))
    kis = make_client(FakeKisServer(clock), LocalRateLimiter(clock=clock))
    kis.rate_limiter = None
    with pytest.raises(ValueError, match="레이트리미터"):
        Collector(kis, InMemorySink(), calendar=CAL, clock=clock)


def test_limiter_is_wrapped_once_and_both_collectors_hear_slowdowns() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    wrapped: RateLimiter | None = r.col.kis.rate_limiter
    again = Collector(r.col.kis, InMemorySink(), calendar=CAL, clock=r.clock)
    assert again.kis.rate_limiter is wrapped
    assert isinstance(wrapped, ObservedLimiter) and len(wrapped.listeners) == 2
    assert wrapped.inner is r.limiter


def test_chain_view_quality_ages_after_run() -> None:
    r = rig(kst(2026, 9, 28, 10, 0))
    r.run(kst(2026, 9, 28, 10, 0, 40))
    later = kst(2026, 9, 28, 10, 0, 40) + timedelta(seconds=120)
    q = Counter((c.source, c.quality) for c in r.col.chain_view(later))
    assert q[("board", "stale")] > 0 and q[("board", "ok")] == 0

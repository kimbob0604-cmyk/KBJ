"""poller 일정(순수 함수) — 대상 만기·만기 전환·주기·우선순위·야간 A/B/C·IDLE·휴장.

설계 docs/phase1_design.md §3·§5·§7, PLAN §4.4. 네트워크 없음 — 문맥은 가짜 체인의 마스터로 채운다.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from core.calendar import TradingCalendar, state_at
from data.kis.models import FuturesBoardRow
from data.kis.ratelimit import Priority
from services.poller.config import PollerConfig
from services.poller.context import (
    BoardSeen,
    ChainContext,
    ExpiryInfo,
    FuturesQuote,
    Series,
    board_covers,
    estimate_last_trade_date,
    near_month,
    resolve_candidates,
    select_targets,
    session_tag,
)
from services.poller.planner import (
    OptionListRun,
    Plan,
    RunState,
    fill_plan,
    plan,
    state_key,
    to_us,
)
from tests.fakes.kis_server import default_chain

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
WKM_0904 = Series("WKM", "260904")
WKI_1001 = Series("WKI", "261001")
WKM_1001 = Series("WKM", "261001")
M_10, M_11 = Series("", "202610"), Series("", "202611")
LAST = {
    WKM_0904: date(2026, 9, 28),
    WKI_1001: date(2026, 10, 1),
    WKM_1001: date(2026, 10, 6),
    M_10: date(2026, 10, 8),
    M_11: date(2026, 11, 12),
}
F = Decimal("1095.10")


def kst(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0, us: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, us, tzinfo=KST)


def context(boards: bool = True) -> ChainContext:
    chain = default_chain()
    ctx = ChainContext(chain.master_rows())
    ctx.set_listed("", ["202610", "202611", "202612", "202701"])
    ctx.set_listed("WKM", ["260904", "261001"])
    ctx.set_listed("WKI", ["261001"])
    for s, d in LAST.items():
        ctx.set_expiry(s, ExpiryInfo(d, "kis"))
    ctx.set_futures(FuturesQuote("A01612", F, 0, "board"))
    ctx.set_futures_codes(["A01612", "A01703"])
    if boards:
        for fs in chain.series:
            top = sorted(fs.strikes, reverse=True)[:100]
            keys = frozenset((k, cp) for k in top for cp in ("C", "P"))
            ctx.set_board(Series(fs.cls, fs.mtrt), BoardSeen(keys, 0))
    return ctx


def steady(ctx: ChainContext, now: datetime) -> RunState:
    """월물리스트·최종거래일을 이번 상태·세션에서 이미 받은 기록."""
    info, tag = state_at(now, CAL), session_tag(now, CAL)
    assert tag is not None
    runs = RunState()
    sk = state_key(info, tag)
    for cls in ("M", "WKM", "WKI"):
        runs.option_list[f"option_list:{cls}"] = OptionListRun(sk, to_us(now), True)
    for s in resolve_candidates(ctx.listed, PollerConfig().monthly_resolve):
        runs.expiry_resolved[s] = tag
    return runs


def plan_at(
    now: datetime, ctx: ChainContext, runs: RunState | None = None, cfg: PollerConfig | None = None
) -> Plan:
    return plan(
        to_us(now),
        state_at(now, CAL),
        session_tag(now, CAL),
        ctx,
        runs if runs is not None else RunState(),
        cfg or PollerConfig(),
        calendar=CAL,
    )


def kinds(p: Plan) -> Counter[str]:
    return Counter(j.kind for j in p.jobs)


# ── 문맥 도우미 ──


def test_session_tag_follows_state_at_and_pre_states_point_forward() -> None:
    assert session_tag(kst(2026, 9, 28, 10, 0), CAL) == (date(2026, 9, 28), "day")
    assert session_tag(kst(2026, 9, 28, 8, 10), CAL) == (date(2026, 9, 28), "day")  # PRE_DAY
    assert session_tag(kst(2026, 9, 28, 17, 55), CAL) == (date(2026, 9, 29), "night")  # PRE_NIGHT
    assert session_tag(kst(2026, 9, 29, 1, 0), CAL) == (date(2026, 9, 29), "night")
    assert session_tag(kst(2026, 9, 18, 23, 0), CAL) == (date(2026, 9, 21), "night")  # 금요일 밤
    assert session_tag(kst(2026, 9, 28, 16, 0), CAL) is None  # POST_DAY
    assert session_tag(kst(2026, 9, 24, 10, 0), CAL) is None  # 추석 전날 휴장


def test_estimated_last_trade_dates_match_kis_measurements() -> None:
    # 실측: 2609W4 09-28, WKI 2610W1 10-01, WKM 2610W1 10-06(10-05 대체공휴일 순연), 202610 10-08
    for s, d in LAST.items():
        assert estimate_last_trade_date(s, CAL) == d
    assert estimate_last_trade_date(Series("WKM", "261006"), CAL) is None  # 6번째 월요일 없음
    assert estimate_last_trade_date(Series("", "2610"), CAL) is None
    assert estimate_last_trade_date(Series("MKI", "202610"), CAL) is None


def test_resolve_candidates_are_all_weeklies_and_first_monthlies() -> None:
    ctx = context()
    got = resolve_candidates(ctx.listed, 2)
    assert got == [M_10, M_11, WKM_0904, WKM_1001, WKI_1001]


def test_targets_switch_after_1520_on_expiry_day() -> None:
    before = select_targets(LAST, kst(2026, 9, 28, 15, 19, 59, 999_999))
    after = select_targets(LAST, kst(2026, 9, 28, 15, 20))
    assert before is not None and after is not None
    assert (before.nearest, before.next, before.monthly) == (WKM_0904, WKI_1001, M_10)
    assert (after.nearest, after.next, after.monthly) == (WKI_1001, WKM_1001, M_10)
    # 월물이 최근접·차기와 겹치면 한 번만
    late = select_targets(LAST, kst(2026, 10, 7, 10, 0))
    assert late is not None and late.tracked == (M_10, M_11)
    assert select_targets({M_10: date(2026, 10, 8)}, kst(2026, 10, 8, 15, 20)) is None


def test_board_jobs_follow_expiry_switch() -> None:
    ctx = context()
    t0 = kst(2026, 9, 28, 15, 19, 30, 250_000)
    t1 = kst(2026, 9, 28, 15, 20, 0, 250_000)
    b0 = [j.series for j in plan_at(t0, ctx, steady(ctx, t0)).jobs if j.kind == "board"]
    b1 = [j.series for j in plan_at(t1, ctx, steady(ctx, t1)).jobs if j.kind == "board"]
    assert b0 == [WKM_0904]  # 첫 전광판(오프셋 0.25초)만 이 시각에 예정
    assert b1 == [WKI_1001]


def test_near_month_by_remaining_days_and_price() -> None:
    def row(code: str, px: str, days: int | None) -> FuturesBoardRow:
        return FuturesBoardRow.model_validate(
            {"futs_shrn_iscd": code, "futs_prpr": px, "hts_rmnn_dynu": days}
        )

    got = near_month([row("A01703", "1085.00", 165), row("A01612", "1095.10", 74)])
    assert got is not None and got.futs_shrn_iscd == "A01612"
    got = near_month([row("A01612", "0.00", 74), row("A01703", "1085.00", 165)])
    assert got is not None and got.futs_shrn_iscd == "A01703"
    assert near_month([row("A01612", "", 74)]) is None


def test_live_futures_and_reference_drop_quarterly_expiry_at_1520() -> None:
    ctx = context()  # 마스터 선물 A01612(F 202612)·A01703(F 202703), 기준가 A01612
    # 월물 202612 의 KIS 값이 없으면 캘린더(12월 둘째 목요일). 마스터에 없는 종목은 모른다
    assert ctx.futures_last_trade_date("A01612", CAL) == date(2026, 12, 10)
    assert ctx.futures_last_trade_date("A01703", CAL) == date(2027, 3, 11)
    assert ctx.futures_last_trade_date("X99999", CAL) is None
    before, after = kst(2026, 12, 10, 15, 19, 59), kst(2026, 12, 10, 15, 20)
    assert ctx.live_futures_codes(before, CAL) == ("A01612", "A01703")
    assert ctx.live_futures_codes(after, CAL) == ("A01703",)
    assert ctx.reference(before, CAL) == ctx.futures
    assert ctx.reference(after, CAL) is None  # 만기 지난 근월물 시세는 기준가가 아니다
    # 마스터에 코스피200 선물이 있는데 없는 종목은 상장 종목이 아니다(만기 지나 빠진 근월물 등)
    ctx.set_futures_codes(["A01612", "X99999", "A01703"])
    assert ctx.live_futures_codes(before, CAL) == ("A01612", "A01703")
    assert ctx.live_futures_codes(after, CAL) == ("A01703",)
    # 월물리스트·KIS 최종거래일이 있으면 그것이 먼저다 (합성: 하루 당겨진 경우)
    ctx.set_expiry(Series("", "202612"), ExpiryInfo(date(2026, 12, 9), "kis"))
    assert ctx.live_futures_codes(kst(2026, 12, 9, 15, 20), CAL) == ("A01703",)
    # 마스터에 선물이 하나도 없으면(모른다) 전광판 종목을 그대로 살아 있다고 본다
    no_fut = ChainContext([m for m in ctx.master if not m.name.startswith("F ")])
    no_fut.set_futures_codes(["A01612", "X99999"])
    assert no_fut.live_futures_codes(after, CAL) == ("A01612", "X99999")
    # 마스터에 있는데 결제월 형식이 달라 최종거래일을 못 구하면 살아 있다고 본다
    odd = next(m for m in ctx.master if m.code == "A01703").model_copy(update={"name": "F 202713"})
    assert odd.expiry == "202713"
    odd_ctx = ChainContext([odd])
    assert odd_ctx.futures_last_trade_date("A01703", CAL) is None
    assert odd_ctx.live_futures_codes(after, CAL) == ("A01703",)


def test_plan_after_quarterly_expiry_uses_next_futures_contract() -> None:
    ctx = context()
    ctx.set_expiry(M_11, ExpiryInfo(date(2026, 12, 17), "kis"))  # 합성: 살아 있는 대상 만기
    day = kst(2026, 12, 10, 15, 20, 10)
    p = plan_at(day, ctx, steady(ctx, day))
    top = [j for j in p.jobs if j.kind == "underlying"]
    assert [j.request.as_dict()["FID_INPUT_ISCD"] for j in top if j.request] == ["A01703"]
    assert not {"fill1", "fill2"} & set(
        kinds(p)
    )  # 3월물 시세가 오기 전 — 12월물 가격으로 짜지 않는다
    ctx.set_futures(FuturesQuote("A01703", Decimal("1085.00"), to_us(day), "board"))
    assert kinds(plan_at(day, ctx, steady(ctx, day)))["fill1"] > 0
    night = kst(2026, 12, 10, 21, 0)
    p = plan_at(night, ctx, steady(ctx, night), PollerConfig(night_mode="B"))
    assert {j.key for j in p.jobs if j.kind == "futures_single"} == {"futures_single:A01703"}


def test_board_coverage_weekly_board_monthly_board_or_fill1() -> None:
    wk = [Decimal(k) / 2 for k in range(1995, 2491, 5)]  # 997.5 ~ 1245.0
    assert board_covers(WKM_0904, wk, Decimal("1095.10"))
    assert not board_covers(WKM_0904, wk, Decimal("990.00"))  # ATM 990.0 이 전광판 밖
    top100 = [Decimal("1347.5") + Decimal("2.5") * i for i in range(100)]
    fill1 = [Decimal("1045.0") + Decimal("2.5") * i for i in range(41)]
    assert board_covers(M_10, top100, F, fill1)
    assert not board_covers(M_10, top100, F)
    assert not board_covers(M_10, top100, Decimal("1250.0"), fill1)  # 두 구간 사이 빈 곳


# ── 일정 ──


def test_fresh_day_start_puts_p0_first_then_priority_order() -> None:
    ctx = context()
    p = plan_at(kst(2026, 9, 28, 9, 0, 5, 500_000), ctx)
    k = kinds(p)
    assert k["option_list"] == 3 and k["expiry"] == 5
    assert k["futures_board"] == k["underlying"] == 1 and k["board"] == 3
    assert k["investor"] == 1  # 첫 조합(오프셋 5초)만
    assert k["fill1"] == 8  # 60/82 초 간격 — 5.5초까지 8건
    assert k["fill2"] == 1
    prios = [j.priority for j in p.jobs]
    assert prios == sorted(prios) and prios[0] == Priority.P0 and prios[-1] == Priority.P4
    tags = {j.tag for j in p.jobs}
    assert tags == {(date(2026, 9, 28), "day")}


def test_day_cadence_matches_plan_4_4_over_two_minutes() -> None:
    """실행을 흉내 내어(즉시 완료) 2분 동안 흐름별 횟수를 센다 — PLAN §4.4 표."""
    ctx = context()
    start = kst(2026, 9, 28, 10, 0)
    runs = steady(ctx, start)
    counts: Counter[str] = Counter()
    t = to_us(start)
    end = t + 120_000_000
    while t < end:
        now = datetime.fromtimestamp(t / 1e6, tz=KST)
        p = plan(
            t, state_at(now, CAL), session_tag(now, CAL), ctx, runs, PollerConfig(), calendar=CAL
        )
        for j in p.jobs:
            counts[j.kind] += 1
            runs.done[j.key] = j.cycle
            if j.kind == "fill2" and j.target is not None:
                runs.fill2_done.add(j.target.key)
        t += 250_000
    assert counts == {
        "board": 12,
        "futures_board": 4,
        "underlying": 4,
        "investor": 14,
        "fill1": 164,
        "fill2": 120,
    }


def test_fill1_is_monthly_atm20_around_futures_not_display_atm() -> None:
    ctx = context()
    targets = ctx.targets(kst(2026, 9, 28, 10, 0))
    assert targets is not None
    fp = fill_plan(ctx, targets, "A", PollerConfig())
    assert fp is not None and len(fp.fill1) == 1
    g = fp.fill1[0]
    assert g.period_s == 60 and g.market == "O" and len(g.targets) == 82
    assert {t.series for t in g.targets} == {M_10}
    strikes = sorted({t.strike for t in g.targets})
    assert (strikes[0], strikes[-1]) == (Decimal("1045.00"), Decimal("1145.00"))
    # 첫 대상은 선물가 ATM 1095.0 — 전광판 ATM 표시(1125.0)가 아니다
    assert (g.targets[0].strike, g.targets[0].cp) == (Decimal("1095.00"), "C")


def test_fill2_is_the_rest_of_tracked_expiries_nearest_first() -> None:
    ctx = context()
    targets = ctx.targets(kst(2026, 9, 28, 10, 0))
    assert targets is not None
    fp = fill_plan(ctx, targets, "A", PollerConfig())
    assert fp is not None
    by_series = Counter(t.series for t in fp.fill2)
    # 위클리 111 − 전광판 100 = 11, 월물 341 − 100 − 41 = 200 (× 콜·풋)
    assert by_series == {WKM_0904: 22, WKI_1001: 22, M_10: 400}
    board = ctx.boards[M_10].keys
    f1 = {(t.strike, t.cp) for t in fp.fill1[0].targets}
    assert not any((t.strike, t.cp) in board | f1 for t in fp.fill2 if t.series == M_10)
    dist = [abs(t.strike - F) for t in fp.fill2]
    assert dist == sorted(dist)
    assert (fp.fill2[0].series, fp.fill2[0].strike) == (M_10, Decimal("1147.50"))


def test_fill2_rotates_and_starts_a_new_round() -> None:
    ctx = context()
    now = kst(2026, 9, 28, 10, 0, 0)
    runs = steady(ctx, now)
    targets = ctx.targets(now)
    assert targets is not None
    fp = fill_plan(ctx, targets, "A", PollerConfig())
    assert fp is not None
    first = next(j for j in plan_at(now, ctx, runs).jobs if j.kind == "fill2")
    assert first.target == fp.fill2[0] and not first.new_round
    assert first.stale_after_s == pytest.approx(len(fp.fill2) + 60)
    runs.fill2_done.add(fp.fill2[0].key)
    second = next(j for j in plan_at(now, ctx, runs).jobs if j.kind == "fill2")
    assert second.target == fp.fill2[1]
    runs.fill2_done.update(t.key for t in fp.fill2)
    again = next(j for j in plan_at(now, ctx, runs).jobs if j.kind == "fill2")
    assert again.target == fp.fill2[0] and again.new_round


def test_no_fill2_before_boards_seen_in_day_mode() -> None:
    ctx = context(boards=False)
    targets = ctx.targets(kst(2026, 9, 28, 10, 0))
    assert targets is not None
    fp = fill_plan(ctx, targets, "A", PollerConfig())
    assert fp is not None and fp.fill2 == () and len(fp.fill1[0].targets) == 82


def test_night_mode_job_sets() -> None:
    ctx = context()
    now = kst(2026, 9, 28, 21, 0, 10)  # 09-28 밤 → 09-29 귀속, WKM 260904 는 15:20 에 만기
    runs = steady(ctx, now)
    a = plan_at(now, ctx, runs, PollerConfig(night_mode="A"))
    assert set(kinds(a)) == {"board", "futures_board", "underlying", "investor", "fill1", "fill2"}
    assert {j.series for j in a.jobs if j.kind == "board"} <= {WKI_1001, WKM_1001, M_10}
    assert {j.tag for j in a.jobs} == {(date(2026, 9, 29), "night")}

    b = plan_at(now, ctx, runs, PollerConfig(night_mode="B"))
    assert set(kinds(b)) == {"futures_single", "investor", "fill1", "fill2"}
    assert {j.market for j in b.jobs if j.kind == "futures_single"} == {"CM"}
    assert {j.market for j in b.jobs if j.kind in ("fill1", "fill2")} == {"EU"}
    targets = ctx.targets(now)
    assert targets is not None
    fp = fill_plan(ctx, targets, "B", PollerConfig(night_mode="B"))
    assert fp is not None
    assert [(g.targets[0].series, g.period_s, len(g.targets)) for g in fp.fill1] == [
        (WKI_1001, 60, 82),
        (M_10, 120, 82),
    ]

    c = plan_at(now, ctx, runs, PollerConfig(night_mode="C"))
    assert set(kinds(c)) == {"investor"}
    none = plan_at(now, ctx, runs, PollerConfig(night_mode="C", night_investor=False))
    assert none.jobs == ()


def test_night_b_futures_are_the_two_nearest_codes() -> None:
    ctx = context()
    now = kst(2026, 9, 28, 21, 0, 0, 600_000)
    p = plan_at(now, ctx, steady(ctx, now), PollerConfig(night_mode="B"))
    fut = [j.request for j in p.jobs if j.kind == "futures_single" and j.request is not None]
    got = sorted(r.as_dict()["FID_INPUT_ISCD"] for r in fut)
    assert got == ["A01612", "A01703"]


@pytest.mark.parametrize(
    "when",
    [
        kst(2026, 9, 28, 7, 0),  # IDLE (06:00~08:00)
        kst(2026, 9, 28, 16, 0),  # POST_DAY
        kst(2026, 9, 24, 10, 0),  # 추석 전날 휴장
        kst(2026, 9, 23, 21, 0),  # 휴장 전날 밤 — 야간장 없음
        kst(2026, 9, 26, 10, 0),  # 토요일
    ],
)
def test_idle_post_and_holidays_have_no_jobs(when: datetime) -> None:
    p = plan_at(when, context())
    assert p.jobs == () and p.next_due_us is None


@pytest.mark.parametrize("when", [kst(2026, 9, 28, 8, 10), kst(2026, 9, 28, 17, 55)])
def test_pre_states_only_refresh_option_list_and_expiries(when: datetime) -> None:
    cfg = PollerConfig(night_mode="A")
    assert set(kinds(plan_at(when, context(), cfg=cfg))) == {"option_list", "expiry"}


@pytest.mark.parametrize("when", [kst(2026, 9, 28, 17, 55), kst(2026, 9, 28, 21, 0)])
def test_night_mode_c_never_calls_option_single_price_for_expiries(when: datetime) -> None:
    ctx = context()  # 주간에 받은 KIS 최종거래일
    c_cfg = PollerConfig(night_mode="C")
    p = plan_at(when, ctx, cfg=c_cfg)  # C — PRE_NIGHT 도 야간 세션 몫이다
    assert kinds(p)["expiry"] == 0 and kinds(p)["option_list"] == 3
    ctx.expiries.pop(WKI_1001)  # 모르는 시리즈는 부르지 않고 캘린더로 (collector)
    ex = [j for j in plan_at(when, ctx, cfg=c_cfg).jobs if j.kind == "expiry"]
    assert [(j.series, j.request) for j in ex] == [(WKI_1001, None)]
    a = [j for j in plan_at(when, ctx, cfg=PollerConfig(night_mode="A")).jobs if j.kind == "expiry"]
    assert len(a) == 5 and all(j.request is not None for j in a)  # A 는 세션마다 조회


def test_option_list_on_state_change_hourly_and_retry() -> None:
    ctx = context()
    t = kst(2026, 9, 28, 9, 0)
    runs = steady(ctx, t)
    assert kinds(plan_at(t + timedelta(minutes=59, seconds=59), ctx, runs))["option_list"] == 0
    assert kinds(plan_at(t + timedelta(hours=1), ctx, runs))["option_list"] == 3
    # PRE_DAY 에 받은 목록은 DAY 가 되면 다시 받는다
    pre = kst(2026, 9, 28, 8, 30)
    runs_pre = steady(ctx, pre)
    assert kinds(plan_at(kst(2026, 9, 28, 8, 45), ctx, runs_pre))["option_list"] == 3
    assert kinds(plan_at(kst(2026, 9, 28, 8, 45), ctx, runs_pre))["expiry"] == 0  # 같은 세션
    # 실패는 60초 뒤 다시
    sk = state_key(state_at(t, CAL), (date(2026, 9, 28), "day"))
    runs.option_list["option_list:WKM"] = OptionListRun(sk, to_us(t), False)
    assert kinds(plan_at(t + timedelta(seconds=59), ctx, runs))["option_list"] == 0
    assert kinds(plan_at(t + timedelta(seconds=60), ctx, runs))["option_list"] == 1


def test_expiry_job_without_master_code_has_no_request() -> None:
    ctx = context()
    ctx.set_master([])
    p = plan_at(kst(2026, 9, 28, 9, 0), ctx)
    ex = [j for j in p.jobs if j.kind == "expiry"]
    assert len(ex) == 5 and all(j.request is None for j in ex)
    assert kinds(p)["fill1"] == 0  # 마스터가 없으면 보강 대상도 없다


def test_failed_expiry_waits_for_its_retry_time() -> None:
    ctx = context()
    t = kst(2026, 9, 28, 8, 10)  # PRE_DAY — 월물리스트·최종거래일만
    runs = steady(ctx, t)
    del runs.expiry_resolved[WKI_1001]  # 이 세션에 KIS 값을 못 받았다
    runs.expiry_retry[WKI_1001] = to_us(t + timedelta(seconds=60))

    def expiry_jobs(now: datetime) -> list[Series | None]:
        return [j.series for j in plan_at(now, ctx, runs).jobs if j.kind == "expiry"]

    assert expiry_jobs(t + timedelta(seconds=59)) == []
    assert expiry_jobs(t + timedelta(seconds=60)) == [WKI_1001]
    p = plan_at(t, ctx, runs)
    assert p.jobs == () and p.next_due_us == to_us(t + timedelta(seconds=60))  # 그때 깬다


def test_next_due_is_the_nearest_future_slot() -> None:
    ctx = context()
    now = kst(2026, 9, 28, 10, 0, 0, 100_000)
    runs = steady(ctx, now)
    p = plan_at(now, ctx, runs)
    for j in p.jobs:
        runs.done[j.key] = j.cycle
        if j.target is not None and j.kind == "fill2":
            runs.fill2_done.add(j.target.key)
    p2 = plan_at(now, ctx, runs)
    assert p2.jobs == ()
    assert p2.next_due_us == to_us(kst(2026, 9, 28, 10, 0, 0, 250_000))  # 첫 전광판


def test_config_defaults_and_priority_timeouts() -> None:
    cfg = PollerConfig()
    assert cfg.night_mode == "B"  # 설계 §7 분기 B — #19 야간 실측(2026-09-29)으로 확정
    assert [cfg.timeout(p) for p in Priority] == [None, None, 2.0, 1.0, 0.0]
    with pytest.raises(ValueError):
        PollerConfig(night_mode="D")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PollerConfig(board_period_s=0)


def test_fill2_catches_up_one_missed_slot_only() -> None:
    ctx = context()
    now = kst(2026, 9, 28, 10, 0, 7, 300_000)
    c = to_us(now) // 1_000_000
    runs = steady(ctx, now)
    runs.done["fill2"] = c - 5  # 높은 등급에 밀려 여러 슬롯을 놓쳤다

    def fill2_cycle(cfg: PollerConfig) -> int | None:
        j = next((j for j in plan_at(now, ctx, runs, cfg).jobs if j.kind == "fill2"), None)
        return None if j is None else j.cycle

    assert fill2_cycle(PollerConfig()) == c - 1  # 한 슬롯만 따라잡고
    runs.done["fill2"] = c - 1
    assert fill2_cycle(PollerConfig()) == c
    runs.done["fill2"] = c
    assert fill2_cycle(PollerConfig()) is None  # 이번 슬롯은 끝
    runs.done["fill2"] = c - 5
    assert fill2_cycle(PollerConfig(fill2_backlog=0)) == c  # 따라잡지 않으면 놓친 슬롯은 버린다

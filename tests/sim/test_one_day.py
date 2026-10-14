"""하루 운영 시뮬레이션 — 24시간 창 2026-10-06(화) 05:00 ~ 10-07(수) 05:00 KST.

근거: 메인 결정 D3(23시간 창 대신 24시간 창), 설계 §10. 이 날을 고른 이유(§10.3): 전날
10-05 가 대체공휴일이라 `prev_trading_day` = 10-02(금), KRX 는 10-02 분을 10-06 08:00 에
공표, 간밤 미국(10-05 월) 장 마감 16:10 ET = 10-06 05:10 KST 가 창 안이다.

PLAN §8 P2 완료 기준: **토큰 발급은 auth 한 곳**(auth 밖 소비자의 발급 0, 어떤 23시간
구간에도 접근토큰 발급 ≤ 1 — 창 안 발급은 05:00 첫 발급과 다음 날 04:00 만료 60분 전 갱신
두 번), **중복 수집 0**.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from itertools import pairwise

import pytest

from kbj.config.markets import load_markets
from kbj.services.auth.issuer import APPROVAL_PATH, TOKEN_PATH
from tests.fakes.kis_server import SYMBOLS
from tests.sim.conftest import KST, at
from tests.sim.harness import OFFLINE_SOURCES, SimDay, SimOptions

pytestmark = pytest.mark.sim

START = at(10, 6, 5)
DAY = date(2026, 10, 6)
PREV = "2026-10-02"
NON_AUTH = ("scheduler", "gx", "reader")  # P9: legacy 브리지 → reader(harness)


@pytest.fixture(scope="module")
def day() -> Iterator[SimDay]:
    sim = SimDay(SimOptions(start=START, hours=24))
    try:
        yield sim.run()
    finally:
        sim.close()


def _hm(t: datetime) -> str:
    return t.astimezone(KST).strftime("%m-%d %H:%M")


# ── 1. 발급은 auth 한 곳 ─────────────────────────────────────────────────────────────────
def test_access_token_issued_by_auth_at_0500_and_renewed_0400(day: SimDay) -> None:
    posts = day.kis.posts(TOKEN_PATH)
    assert [(_hm(p.at), p.consumer, p.status) for p in posts] == [
        ("10-06 05:00", "auth", 200),
        ("10-07 04:00", "auth", 200),  # 만료(10-07 05:00) 60분 전 갱신
    ]
    assert posts[0].at.astimezone(KST) == START  # 창의 첫 tick 에 바로
    assert day.kis.token_posts == 2


def test_no_23_hour_window_has_two_token_issues(day: SimDay) -> None:
    times = sorted(p.at for p in day.kis.posts(TOKEN_PATH))
    for a, b in pairwise(times):
        assert b - a >= timedelta(hours=23)  # 반열린 23시간 창에는 많아야 1회


def test_ws_approval_key_issued_by_auth_every_11_hours(day: SimDay) -> None:
    posts = day.kis.posts(APPROVAL_PATH)
    assert [(_hm(p.at), p.consumer) for p in posts] == [
        ("10-06 05:00", "auth"),
        ("10-06 16:00", "auth"),
        ("10-07 03:00", "auth"),
    ]  # 수명 12시간 가정 — 11시간마다 갱신 [확인 필요: 미실측]


def test_no_consumer_outside_auth_ever_issues(day: SimDay) -> None:
    by_consumer = Counter((c.consumer, c.method) for c in day.kis.calls)
    for who in NON_AUTH:
        assert by_consumer[(who, "POST")] == 0
        assert by_consumer[(who, "GET")] > 0  # 모두 실제로 KIS 를 불렀다(읽기만)
    assert {c.consumer for c in day.kis.posts()} == {"auth"}
    assert by_consumer[("auth", "GET")] == 0
    assert not any(e.kind.endswith("_expiring") for e in day.auth_health.events)


def test_every_kis_request_carries_the_token_auth_issued(day: SimDay) -> None:
    first, second = day.kis.tokens()
    gets = day.kis.gets()
    assert {c.bearer for c in gets} == {first.value, second.value}
    assert all(c.status == 200 for c in gets)
    assert all(c.bearer == first.value for c in gets if c.at < second.at)
    # 갱신 뒤(야간 세션 04:00~05:00 poller)는 새 토큰 — 읽는 쪽이 auth 의 새 값을 집어 간다
    after = [c for c in gets if c.at >= second.at]
    assert after and all(c.bearer == second.value for c in after)


def test_app_key_limiter_never_exceeds_4_per_second(day: SimDay) -> None:
    assert day.kis.max_in_window(1.0) <= 4  # 발급 요청 포함, 모든 프로세스가 한 버킷


# ── 2. 중복 수집 0 ───────────────────────────────────────────────────────────────────────
def test_each_data_key_is_collected_exactly_once(day: SimDay) -> None:
    done = day.done_keys()
    assert done and max(done.values()) == 1
    assert day.claims.refused == []  # 선점 충돌 0
    assert [r for r in day.claims.rows if r.status != "done"] == []


def test_one_job_per_dataset(day: SimDay) -> None:
    owners = day.jobs_per_dataset()
    assert {ds: jobs for ds, jobs in owners.items() if len(jobs) > 1} == {}
    assert owners["KRX:drv/opt_bydd_trd"] == {"gex.krx_derivatives"}  # external 도 같은 장부
    assert owners["KRX:sto/stk_bydd_trd"] == {"krx.daily"}


def test_venue_split_keys(day: SimDay) -> None:
    """거래대금·순매수는 거래소마다 데이터 키(docs/metrics.md §1, 메인 결정 D7).

    P3(D-P3-9 — 묶음 S): 실측 전에는 KIS 를 config/markets.yaml kis.venues(기본 [KRX])로만 부른다 —
    등록부 collects.venues 가 같은 값이라 선점 장부에 NXT·TOTAL 키가 생기지 않는다."""
    want = tuple(load_markets().kis.venues)
    venues = Counter(
        (k.dataset, k.venue) for k in day.done_keys() if k.dataset == "stock_quote_eod"
    )
    assert venues == {("stock_quote_eod", v): 1 for v in want}
    krx = {k.dataset: k.venue for k in day.done_keys() if k.source == "KRX"}
    # 체결 일별(주식·ETF·ETN)은 KRX 시장 체결분 — 데이터 키에 KRX. 기본정보·지수·파생은 구분 없음
    assert {ds for ds, v in krx.items() if v == "KRX"} == {
        "sto/stk_bydd_trd",
        "sto/ksq_bydd_trd",
        "sto/knx_bydd_trd",
        "etp/etf_bydd_trd",
        "etp/etn_bydd_trd",
    }
    assert set(krx.values()) == {"KRX", ""}


def test_krx_each_endpoint_and_day_requested_once(day: SimDay) -> None:
    req = Counter(day.krx.requested())
    # 12 + P7 krx.vkospi(/idx/drvprod_dd_trd — 운영 등록부에서는 꺼짐 #P7-1, 하네스는 꺼진 작업도
    # 시뮬레이션 처리기로 돌린다 — 하네스 머리말)
    assert len(req) == 13 and set(req.values()) == {1}
    assert {b for _, b in req} == {"20261002"}
    # P9 WRAP: KRX 파생(/drv/)도 등록부 gex.krx_derivatives(runner kbj) — 옛 GX 흉내(gx) 자리
    assert {s.consumer for s in day.krx.seen} == {"scheduler"}


def test_dart_list_polled_once_per_minute_0700_to_1959(day: SimDay) -> None:
    calls = day.dart.calls("list.json")
    keys = [k for k in day.done_keys() if k.dataset_id == "DART:list"]
    assert len(calls) == len(keys) == 13 * 60
    minutes = sorted(k.as_of for k in keys)
    assert minutes[0] == "2026-10-06T07:00" and minutes[-1] == "2026-10-06T19:59"
    assert len(day.dart.calls("corpCode.xml")) == 1  # 10-07 03:05 filings.corp_code


def test_close_collect_requests_once_per_tr_venue_symbol(day: SimDay) -> None:
    lo, hi = at(10, 6, 15, 35), at(10, 6, 16, 0)  # market.close_collect(정규장 마감 + 5분)
    calls = [
        c
        for c in day.kis.gets("scheduler")
        if lo <= c.at < hi and c.tr_id in ("FHKST01010100", "FHKST01010900")
    ]
    eod = Counter(
        (c.tr_id, c.params["FID_COND_MRKT_DIV_CODE"], c.params["FID_INPUT_ISCD"]) for c in calls
    )
    # 2 TR × 거래소 × 20 종목 + ETF 투자자(P3 KIS:etf_investor_daily — 합성 ETF 1 × 거래소).
    # 거래소 = config/markets.yaml kis.venues(P3 기본 [KRX] — D-P3-9. P2 는 KRX·NXT·TOTAL 셋이었다)
    n_venues = len(load_markets().kis.venues)
    assert len(eod) == 2 * n_venues * len(SYMBOLS) + n_venues
    assert set(eod.values()) == {1}
    reqs = Counter((job, key, target) for job, key, target in day.kis_requests)
    assert set(reqs.values()) == {1}  # (데이터 키, 대상) 마다 한 번


def test_daily_budgets_within_caps(day: SimDay) -> None:
    # 주식·지수·ETP 10 + 파생 2 + VKOSPI 1(P7 krx.vkospi — 운영은 꺼짐, 하네스는 돈다) — 상한 8,000
    assert day.budget_used("krx", DAY) == 13
    assert 13 * 60 <= day.budget_used("dart", DAY) <= 18_000
    for ds in ("15094808", "15094807", "15094809"):
        assert 1 <= day.budget_used("datago", DAY, scope=ds) <= 9_500


# ── 3. 알림 ──────────────────────────────────────────────────────────────────────────────
def test_u2_one_morning_and_one_closing_brief_both_suppressed(day: SimDay) -> None:
    morning = day.notify_log.of("brief.morning")
    closing = day.notify_log.of("brief.closing")
    assert [(r.status, _hm(r.requested_at)) for r in morning] == [("suppressed", "10-06 08:10")]
    assert [(r.status, _hm(r.requested_at)) for r in closing] == [("suppressed", "10-06 16:00")]
    assert {r.status for r in day.notify_log.rows} == {"suppressed"}


def test_post_close_sends_fire_at_1600_after_the_board(day: SimDay) -> None:  # ADR 0018
    """장 마감 뒤 발송 작업(마감 요약·수급 리포트·리비전)은 16:00 에 돌고, 마감 요약·수급 리포트는
    같은 16:00 의 보드가 끝난 뒤에 시작한다(무른 의존 wait_min)."""
    board = day.final_runs()[("board.daily", "2026-10-06")]
    assert board.status == "ok" and board.started_at is not None and board.finished_at is not None
    assert _hm(board.started_at) == "10-06 16:00"
    for job in ("brief.closing", "flows.report", "consensus.snapshot"):
        (run,) = [r for r in day.runs.history if r.job == job and r.status == "running"]
        assert run.started_at is not None and _hm(run.started_at) == "10-06 16:00", job
        if job != "consensus.snapshot":
            assert run.started_at >= board.finished_at, job
            # P7(D-P7-16): 마감 요약은 diag.daily(16:00 — 보드 뒤 진단)도 무른 의존으로 기다린다
            soft = {"board.daily": "ok"} | ({"diag.daily": "ok"} if job == "brief.closing" else {})
            assert run.detail["soft_deps"] == soft, job
    flows = day.notify_log.of("flows.report")
    assert [(r.status, _hm(r.requested_at)) for r in flows] == [("suppressed", "10-06 16:00")]


def test_disabled_mode_calls_telegram_zero_times(day: SimDay) -> None:
    assert day.tg.calls == []


# ── 4. 세션·실행 기록 ─────────────────────────────────────────────────────────────────────
def test_session_transitions_in_order(day: SimDay) -> None:
    states = [(e["state"], e["trade_date"]) for e in day.session_events]
    assert states == [
        ("IDLE", None),
        ("PRE_DAY", None),
        ("DAY", "2026-10-06"),
        ("POST_DAY", None),
        ("PRE_NIGHT", None),
        ("NIGHT", "2026-10-07"),  # 야간 세션은 다음 거래일 귀속(GX 와 같다)
    ]
    assert [r.state.value for r in day.session_log.rows if r.state] == [s for s, _ in states]


def test_every_run_finished_ok(day: SimDay) -> None:
    final = day.final_runs()
    bad = {k: (r.status, dict(r.detail)) for k, r in final.items() if r.status != "ok"}
    assert bad == {}
    assert not [r for r in day.runs.records.values() if r.detail.get("reason") == "planned"]
    assert day.unsupported == []
    assert {s for _, k, _ in day.offline_calls for s in [k.source]} <= OFFLINE_SOURCES


def test_enabled_jobs_ran_with_real_handlers(day: SimDay) -> None:
    final = day.final_runs()
    assert final[("ops.nightly", "2026-10-07")].status == "ok"
    assert day.backups == ["sim-backup-20261007.dump"]
    assert any(q.startswith("DELETE FROM prv_alerts.tg_inbox") for q in day.pg_log)
    corp = final[("filings.corp_code", "2026-10-07")]
    assert corp.status == "ok" and corp.detail.get("rows") == day.dart.corps
    assert day.corp_store.count() == day.dart.corps
    watch = [r for (job, _), r in final.items() if job == "ops.watchdog"]
    # P9 WRAP(ADR 0037 §3): 5분마다·매일 24시간(cron '*/5 * * * *') — 24시간 창에 288회
    assert len(watch) == 24 * 12
    assert all(r.detail.get("problems") == 0 for r in watch)
    assert day.notify_log.of("ops.watchdog") == []


def test_job_count_matches_the_registry_for_a_trading_day(day: SimDay) -> None:
    final = day.final_runs()
    per_job = Counter(job for job, _ in final)
    assert per_job["filings.dart_feed"] == 13 * 60
    assert per_job["rules.intraday"] == per_job["flows.intraday"] == 40  # 09:00~15:30 10분
    for once in (
        "krx.daily",
        "market.close_collect",
        "board.daily",
        "brief.morning",
        "brief.closing",
        "macro.monthly",
        "macro.evening",
        "market.fsc_daily",
        "market_stats.kofia",
        "us.eod",
        "consensus.snapshot",
    ):
        assert per_job[once] == 1, once
    assert final[("krx.daily", PREV)].status == "ok"  # 실행 as_of = 수집 키 as_of(전 거래일)


# ── 5. GX 수집(등록부 작업)·상시 서비스 흉내·reader ─────────────────────────────────────
def test_gx_collect_jobs_run_on_the_registry_and_share_the_claim_ledger(day: SimDay) -> None:
    """P9 WRAP(ADR 0036): 옛 GX external 단계(마스터·KRX 파생·분봉)는 등록부 작업(runner kbj)이다 —
    실행기가 같은 선점 장부로 돌린다. 상시 서비스(poller·ws) 흉내만 external_runs 에 남는다."""
    final = day.final_runs()
    assert final[("kis.master", "2026-10-06")].status == "ok"  # PRE_DAY
    assert [r.status for (job, _), r in final.items() if job == "kis.master_night"] == ["ok"]
    assert final[("gex.krx_derivatives", PREV)].status == "ok"
    assert final[("gex.day_minutes", "2026-10-06")].status == "ok"
    # 마스터 zip 은 PRE_DAY(kis.master)·PRE_NIGHT(kis.master_night) 두 번, 같은 프로세스(scheduler)
    assert [c for _, c in day.kis.master_downloads] == ["scheduler", "scheduler"]
    ext = Counter((r.job, r.status) for r in day.external_runs)
    assert ext[("gex.ws_gateway", "ok")] == 2
    assert ext[("gex.poller", "ok")] > 0
    assert {j for j, _ in ext} == {"gex.poller", "gex.ws_gateway"}
    assert not [r for r in day.external_runs if r.status != "ok"]


def test_ws_gateway_subscribes_with_auth_issued_approval_keys(day: SimDay) -> None:
    assert day.ws is not None
    got = [(_hm(t), a["body"]["msg1"], b["body"]["msg1"]) for t, a, b in day.ws_results]
    assert got == [
        ("10-06 08:44", "SUBSCRIBE SUCCESS", "UNSUBSCRIBE SUCCESS"),
        ("10-06 17:59", "SUBSCRIBE SUCCESS", "UNSUBSCRIBE SUCCESS"),
    ]
    keys = [k.value for k in day.kis.approval_keys()]
    used = [r.approval_key for r in day.ws.received]
    assert used[:2] == [keys[0]] * 2 and used[2:] == [keys[1]] * 2  # 16:00 갱신 값으로 바뀜
    assert day.ws.errors == []


def test_reader_process_reads_the_token_without_issuing(day: SimDay) -> None:
    """P9: 옛 legacy 브리지 시험 — 브리지는 ET·SD 와 함께 지웠고 같은 자리(발급하지 않는 두 번째
    프로세스)를 harness 'reader' 가 맡는다. 호출자 헤더를 갈아 끼우던 브리지 단언은 없어졌다."""
    assert [(s, tok) for _, s, tok in day.reader_results] == [(200, day.kis.tokens()[0].value)]
    reader = day.kis.gets("reader")
    assert len(reader) == 1 and reader[0].bearer == day.kis.tokens()[0].value


def test_health_has_only_routine_refresh_events(day: SimDay) -> None:
    kinds = Counter(day.health_kinds())
    assert kinds == {"token_refreshed": 2, "ws_key_refreshed": 3}

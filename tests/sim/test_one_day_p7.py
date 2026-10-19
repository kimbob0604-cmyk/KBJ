"""하루 운영 확장(P7) — 2026-12-03(목) 05:00 ~ 12-04(금) 05:00 KST, 발송 켬
(docs/p7_design.md §1.7-2·§8 — 웨이브 3 WRAP).

창: 위클리 목요일 만기일(WKI)이 든 거래일. P5 창과 같은 합성 P5 세계·같은 실제 P5 처리기에 더해
(`p5=True` — 하네스 머리말), 켜진 P7 작업(`diag.public` 08:40·`diag.daily` 16:00·`gex.alert` 정규장
매분)은 실제 처리기, `gex.engine` 은 실제 `EngineService` 가 GX poller 흉내의 10분 회차마다
합성 체인(월물 202612)으로 주간·야간 사이클을 돈다(`p7=True`).

단언
- 작업: 모든 실행이 ok·skipped. 예외 — 합성 P5 세계는 11-17 에 고정이라(미국 일봉 마지막 11-16,
  FRED 일별은 다음 뉴욕 날 공표 — P5 창 머리말) `us.eod`·`macro.morning`·`macro.morning_prv` 는
  `not_ready` 로 끝난다(받은 척하지 않는다). 그 실패 알림(ops.job_failed)만 허용.
- 엔진: 주간·야간 사이클이 모두 돌고 품질 ok(장 밖 회차 없음), 산출은 메모리 GEX 저장소에.
- `diag.public` 08:40 한 번(공개 표만), `diag.daily` 16:00 뒤 한 번, `brief.closing` 1통 — 16:00 뒤,
  ⑥ GEX·진단 줄(`<b>GEX</b>`·`<b>진단</b>`)이 들어 있다.
- `gex.alert`: 정규장 391분 매분 실행, 보낸 알림은 모두 09:00~15:30(15:30 뒤 0통), 같은 주제는
  쿨다운(10분) 안에 다시 보내지 않는다.
- `prv_gex.investor_flow` 쓰기는 `flows.market_minute` 하나(엔진·poller 는 쓰지 않는다).
- KIS 어떤 1초에도 ≤ 4, 토큰 발급은 auth 만. 하트비트 끊김 알림 0(GEX 셋 포함).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import datetime, timedelta
from datetime import time as dtime
from itertools import pairwise

import pytest

from kbj.services.engine.gex import EngineSink
from kbj.services.scheduler.claims import RunRecord
from tests.sim.conftest import KST, at
from tests.sim.harness import SimDay, SimOptions, load_registry
from tests.sim.test_one_day_p5 import ENABLE

pytestmark = pytest.mark.sim

START = at(12, 3, 5)
DATA_LAG = ("us.eod", "macro.morning", "macro.morning_prv")  # 합성 P5 세계 고정(머리말)
P7_REAL = ("diag.public", "diag.daily", "gex.alert")
REGULAR_MINUTES = 391  # 09:00~15:30
ALERT_COOLDOWN = timedelta(minutes=10)


@pytest.fixture(scope="module")
def day() -> Iterator[SimDay]:
    sim = SimDay(
        SimOptions(
            start=START, hours=24, p5=True, p7=True, enable=ENABLE, ws=False,
            reader_calls=False, notify_enabled=True,
        )
    )  # fmt: skip
    try:
        yield sim.run()
    finally:
        sim.close()


def _runs(sim: SimDay, job: str) -> list[RunRecord]:
    return sorted((r for (j, _a), r in sim.final_runs().items() if j == job), key=lambda r: r.as_of)


def _kst(t: datetime | None) -> datetime:
    assert t is not None
    return t.astimezone(KST)


# ── 작업 ───────────────────────────────────────────────────────────────────────────────
def test_every_run_finished_and_p7_handlers_are_real(day: SimDay) -> None:
    bad = [
        (j, a, r.status, str(dict(r.detail))[:200])
        for (j, a), r in day.final_runs().items()
        if r.status not in ("ok", "skipped")
        and not (j in DATA_LAG and r.detail.get("result") == "not_ready")
    ]
    assert bad == []
    assert day.unsupported == []
    for job in P7_REAL:
        runs = _runs(day, job)
        assert runs, job
        assert not any(r.detail.get("sim") for r in runs), job  # 실제 처리기
    # 운영 등록부에서 켜진 P7 작업(WRAP — §1.7-1). VKOSPI 는 2026-10-20 사용자 요청으로 켰다
    # (전에는 실측 #P7-1 전 꺼짐 — 하네스가 시뮬레이션 처리기로 돌렸다). 이제 실제 수집기
    prod = load_registry()
    assert all(prod.by_name(n).enabled for n in P7_REAL)
    assert prod.by_name("krx.vkospi").enabled
    vk = _runs(day, "krx.vkospi")
    assert vk and not any(r.detail.get("sim") for r in vk)


def test_diag_jobs_run_once_at_their_times(day: SimDay) -> None:
    (pub,) = _runs(day, "diag.public")
    assert _kst(pub.started_at).strftime("%H:%M") == "08:40"
    assert pub.as_of == "2026-12-03" and pub.status == "ok"
    assert int(pub.detail.get("rows", 0)) > 0
    (daily,) = _runs(day, "diag.daily")
    assert _kst(daily.started_at).time() >= dtime(16, 0)
    assert daily.status == "ok" and daily.detail.get("verdict") is not None
    # 공개 표에는 공개 지표만, 로그인 표에는 판정 — 같은 날
    d = _kst(pub.started_at).date()
    assert day.repos.diag_pub.indicators_on(d) and day.repos.diag_pub.gauge_on(d) is not None
    assert day.repos.diag.verdict_on(d) is not None


def test_gex_engine_cycles_day_and_night(day: SimDay) -> None:
    sessions = Counter((s, q) for _, s, q in day.gex_cycles)
    assert sessions[("day", "ok")] > 0 and sessions[("night", "ok")] > 0
    assert {q for _, _, q in day.gex_cycles} == {"ok"}, sessions
    # 산출은 저장소에 — 주간 마지막 사이클 레벨
    rows = day.repos.gex.levels_latest("all", upto=at(12, 3, 15, 31))
    assert rows and {r.session for r in rows} == {"day"}
    assert {r.name for r in rows} >= {"flip", "call_wall", "put_wall"}


def test_closing_brief_once_with_gex_and_diag_lines(day: SimDay) -> None:
    kinds = Counter((r.kind, r.status) for r in day.notify_log.rows)
    assert kinds[("brief.closing", "sent")] == 1
    assert kinds[("brief.morning", "sent")] == 1
    others = {k for k, _ in kinds} - {
        "brief.morning", "brief.closing", "flows.report", "etf.report", "alert.gex_level",
        "ops.job_failed",
    }  # fmt: skip
    assert others == set()
    assert {r.status for r in day.notify_log.rows} == {"sent"}
    failed = [r for r in day.notify_log.rows if r.kind == "ops.job_failed"]
    assert len(failed) <= len(DATA_LAG)
    (closing,) = _runs(day, "brief.closing")
    assert "gex_diag" in closing.detail.get("sections", [])
    texts = [str(c.data.get("text", "")) for c in day.tg.sent("sendMessage")]
    (msg,) = [t for t in texts if "마감 요약" in t]
    assert "<b>GEX</b>" in msg and "<b>진단</b>" in msg
    assert _kst(closing.started_at).time() >= dtime(16, 0)


def test_gex_alerts_only_in_regular_session_with_cooldown(day: SimDay) -> None:
    runs = _runs(day, "gex.alert")
    assert len(runs) == REGULAR_MINUTES
    assert {r.status for r in runs} == {"ok"}
    sent = [c for c in day.tg.sent("sendMessage") if "GEX" in str(c.data.get("text", ""))]
    alerts = [c for c in sent if str(c.data.get("text", "")).startswith("⚡")]
    for c in alerts:
        t = c.at.astimezone(KST).time()
        assert dtime(9, 0) <= t <= dtime(15, 31), c.at  # 15:30 뒤 0통(§10 Q5)
    by_subject: dict[str, list[datetime]] = {}
    for r in day.notify_log.rows:
        if r.kind == "alert.gex_level":
            by_subject.setdefault(r.subject or "", []).append(r.requested_at)
    for subject, times in by_subject.items():
        times.sort()
        assert all(b - a >= ALERT_COOLDOWN for a, b in pairwise(times)), subject


def test_investor_flow_has_one_writer(day: SimDay) -> None:
    reg = load_registry()
    assert [j.name for j in reg.jobs if "prv_gex.investor_flow" in j.writes] == [
        "flows.market_minute"
    ]
    assert not any("prv_gex.investor_flow" in s.writes for s in reg.services)
    assert not hasattr(EngineSink, "write_investor")  # 엔진 쓰기 표에 투자자별 없음
    minute = _runs(day, "flows.market_minute")
    assert minute and all(r.status == "ok" for r in minute)


def test_kis_limits_and_heartbeats(day: SimDay) -> None:
    assert day.kis.max_in_window(1.0) <= 4
    assert {c.consumer for c in day.kis.posts()} == {"auth"}
    watchdog = [r for r in day.notify_log.rows if r.kind == "ops.watchdog"]
    assert watchdog == []

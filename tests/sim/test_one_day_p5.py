"""하루 운영 확장(P5) — 2026-11-17(화) 05:00 ~ 11-18(수) 05:00 KST, 발송 켬
(docs/p5_design.md §1.8-3·§8.4, D-P5-17 ③ — 웨이브 3 WRAP).

창: 서머타임 끝(11-01) 뒤라 `us.eod` 06:10 KST. 합성 P5 세계
(`tests/fixtures/synthetic/p5_world.py`)를 창 앞 저장소에 싣고(하네스 머리말), 등록부에서
**켜진 P5 작업**(`etf.report`·`brief.*`·`flows.report`·`us.*`·`flows.market_minute`)과
실측 전이라 운영에서 꺼 두었던 P5 작업(`macro.*`·`market_stats.kofia`·`market.fsc_daily`·
`program.*` — 2026-10-20 사용자 요청으로 운영에서도 켬)은 실제 처리기, P3·P4 작업은
시뮬레이션 처리기(같은 KIS 버킷·같은 선점 장부). `public.export` 는 실제 처리기
(P5 공개 파일). 테마(`themes.*`)는 사전 통합을 사용자가 따로 진행해(2026-10-13 Q1)
P5 에서 뺐다 — 꺼진 채.

단언
- 작업: 모든 실행이 ok·skipped(실측 전 키는 skipped). `macro.morning`·`macro.morning_prv` 는
  06:20 KST(= 16:20 ET 전날)에 그날 FRED 일별이 아직 없어 전 미국 영업일 값을 받는다(2026-10-20 —
  전에는 날마다 재시도 뒤 `not_ready` 로 끝났다; 가짜 FRED 도 다음 뉴욕 날에 공표).
- 데이터 키 각 done 1회, KIS 어떤 1초에도 ≤ 4, 토큰 발급은 auth 만.
- F2 §5: 정규장 391분 × 켠 조합(K2I 3) 빈 분 0, 검산 ①~④ 합성 원장 0 차이, 순위 표 범위
  꼬리표.
- 순서: `program.stock_sweep` 18:10 시작, 16:00 발송 셋(brief.closing·flows.report)이
  `board.daily` 뒤.
- 발송: `brief.morning` 접수 1·수신 1, `brief.closing` 1, `flows.report` 16:00(대상 종목마다
  차트 + 본문 — 하루 1회 규칙은 브리핑만), `etf.report` 08:20 1(브리핑에 세지 않음 — §10 Q5).
- 공개 산출물: `macro/`·`credit.json` 이 생기고 공개 안전 검사(`check_tree`) 통과, 로그인
  출처 이름 0.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import date, datetime

import pytest

from kbj.config.markets import load_calendar_events, load_markets
from kbj.core.calendar import TradingCalendar
from kbj.services.api.readers._common import ReadContext
from kbj.services.api.readers.program import program_top
from kbj.services.engine.verify import verify_program_day
from kbj.services.public_export.manifest import check_tree
from kbj.services.scheduler.claims import RunRecord
from tests.sim.conftest import KST, at
from tests.sim.harness import SimDay, SimOptions, load_registry

pytestmark = pytest.mark.sim

START = at(11, 17, 5)
TODAY = date(2026, 11, 17)
# 실측 #39~#49 전이라 꺼 두었던 P5 작업 — 운영도 2026-10-18·2026-10-20(사용자 요청)에 켰다.
# 사본에서도 켠다
ENABLE = (
    "macro.morning", "macro.morning_prv", "macro.monthly", "macro.monthly_prv", "macro.evening",
    "macro.evening_prv", "market_stats.kofia", "market.fsc_daily", "program.intraday",
    "program.stock_scan", "program.daily", "program.stock_sweep",
)  # fmt: skip
P5_REAL = (
    "us.eod", "etf.report", "brief.morning", "brief.closing", "flows.report", "flows.market_minute",
    *ENABLE,
)  # fmt: skip
FRED_LAG = ("macro.morning", "macro.morning_prv")  # 06:20 KST — 전 미국 영업일 FRED 일별(머리말)
PAIRS = (("K2I", "F001"), ("K2I", "OC01"), ("K2I", "OP01"))
REGULAR_MINUTES = 391  # 09:00~15:30


def options(**kw: object) -> SimOptions:
    base: dict[str, object] = dict(
        start=START, hours=24, p5=True, enable=ENABLE, ws=False, reader_calls=False,
        notify_enabled=True,
    )  # fmt: skip
    base.update(kw)
    return SimOptions(**base)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def day() -> Iterator[SimDay]:
    sim = SimDay(options())
    try:
        yield sim.run()
    finally:
        sim.close()


def _runs(sim: SimDay, job: str) -> list[RunRecord]:
    return sorted((r for (j, _a), r in sim.final_runs().items() if j == job), key=lambda r: r.as_of)


def _started(r: RunRecord) -> datetime:
    assert r.started_at is not None, r
    return r.started_at


# ── 작업 ───────────────────────────────────────────────────────────────────────────────
def test_every_run_finished_and_p5_handlers_are_real(day: SimDay) -> None:
    bad = [
        (j, a, r.status, str(dict(r.detail))[:200])
        for (j, a), r in day.final_runs().items()
        if r.status not in ("ok", "skipped")
    ]
    assert bad == []
    assert day.unsupported == []
    for job in P5_REAL:
        runs = _runs(day, job)
        assert runs, job
        assert not any(r.detail.get("sim") for r in runs), job  # 실제 처리기
    for job in FRED_LAG:  # 공표 전으로 끝나지 않는다(전 미국 영업일 값 — 이미 받았으면 skipped)
        runs = _runs(day, job)
        assert all(r.status in ("ok", "skipped") for r in runs), job
        assert all(r.detail.get("result") != "not_ready" for r in runs), job
    # 운영 등록부: macro.*·kofia·fsc_daily 는 2026-10-18 에 켰고, program.* 도 2026-10-20
    # 사용자 요청 "꺼 둔 것도 전부 활성화"로 켰다(전에는 D-P5-23 으로 꺼진 채)
    prod = load_registry()
    assert all(prod.by_name(n).enabled for n in ENABLE)
    # 테마 강도는 API 가 읽을 때 계산(ADR 0039) — 옛 계획 작업 themes.monitor 는 등록부에서 뺐다
    assert "themes.monitor" not in {j.name for j in prod.jobs}


def test_data_keys_done_once_and_kis_limits(day: SimDay) -> None:
    done = day.done_keys()
    assert done and all(n == 1 for n in done.values()), [k for k, n in done.items() if n != 1][:5]
    assert day.kis.max_in_window(1.0) <= 4
    assert {c.consumer for c in day.kis.posts()} == {"auth"}  # 발급은 auth 만


# ── F2 §5 ────────────────────────────────────────────────────────────────────────────────
def test_regular_session_minutes_have_no_gap(day: SimDay) -> None:
    runs = _runs(day, "flows.market_minute")
    assert len(runs) == 416 and {r.status for r in runs} == {"ok"}  # 09:00~15:55 매분(ADR 0044)
    rows = day.repos.flows.investor_minutes(TODAY, list(PAIRS))
    for pair in PAIRS:
        ts = {
            m.ts.astimezone(KST).strftime("%H:%M")
            for m in rows
            if (m.market_code, m.sector_code) == pair
        }
        regular = {t for t in ts if "09:00" <= t <= "15:30"}
        assert len(regular) == REGULAR_MINUTES, pair  # 빈 분 0


def test_program_checks_have_zero_difference(day: SimDay) -> None:
    rep = verify_program_day(day.repos, TODAY, load_markets())
    for pid, r in rep["minute"].items():
        for c in ("c1", "c2", "c3"):
            assert r[c].get("failed", 0) == 0, (pid, c, r[c])
    assert rep["program"]
    for key, r in rep["program"].items():
        assert r["daily"]["failed"] == 0 and r["intraday"]["failed"] == 0, key
    assert any(r["intraday"]["checked"] for r in rep["program"].values())


def test_program_top_shows_the_scope(day: SimDay) -> None:
    ctx = ReadContext(
        repos=day.repos,
        now=lambda: at(11, 17, 18, 40),
        cal=TradingCalendar.default(),
        markets=load_markets(),
        events=load_calendar_events(),
    )
    intraday = program_top(ctx, market="K", side="buy", scope="intraday")
    close = program_top(ctx, market="K", side="buy", scope="close")
    assert "상위" in intraday.data.scope_label and "관심종목" in intraday.data.scope_label
    assert close.data.scope_label.startswith("전 종목")


# ── 순서 ─────────────────────────────────────────────────────────────────────────────────
def test_sweep_starts_1810_and_1600_sends_follow_the_board(day: SimDay) -> None:
    (sweep,) = _runs(day, "program.stock_sweep")
    assert _started(sweep).astimezone(KST).strftime("%H:%M") == "18:10"
    (board,) = _runs(day, "board.daily")
    assert board.finished_at is not None
    for job in ("brief.closing", "flows.report"):
        (r,) = _runs(day, job)
        t = _started(r)
        assert t.astimezone(KST).strftime("%H:%M") >= "16:00", job
        assert t >= board.finished_at, job  # ADR 0018 — 보드 뒤


# ── 발송(D-P5-17 ③) ──────────────────────────────────────────────────────────────────────
def test_brief_once_and_daily_reports(day: SimDay) -> None:
    rows = day.notify_log.rows
    kinds = Counter((r.kind, r.status) for r in rows)
    assert kinds[("brief.morning", "sent")] == 1
    assert kinds[("brief.closing", "sent")] == 1
    assert kinds[("etf.report", "sent")] == 1
    assert {r.status for r in rows} == {"sent"}
    # flows.report: 대상 종목마다 차트(사진) + 본문 — 대상이 없거나 모두 건너뛴 날만 한 건
    flows = [r for r in rows if r.kind == "flows.report"]
    assert flows and len({r.dedup_key for r in flows}) == len(flows)
    # 텔레그램 수신 = 발송 기록(같은 dedup 키의 부분 발송은 한 기록)
    ok_msgs = day.tg.sent("sendMessage")
    assert len(ok_msgs) >= kinds[("brief.morning", "sent")] + kinds[("brief.closing", "sent")]
    # 아침 브리핑 08:10, ETF 리포트 08:20(브리핑과 따로 — §10 Q5)
    (morning,) = _runs(day, "brief.morning")
    (etf,) = _runs(day, "etf.report")
    assert _started(morning).astimezone(KST).strftime("%H:%M") == "08:10"
    assert _started(etf).astimezone(KST).strftime("%H:%M") == "08:20"


# ── 공개 산출물 ──────────────────────────────────────────────────────────────────────────
def test_public_export_has_p5_files_and_is_safe(day: SimDay) -> None:
    out = day.public_out
    assert (out / "credit.json").exists()
    assert any((out / "macro").glob("*.json"))
    assert not (out / "themes").exists()  # 테마 사전은 P5 에서 뺐다
    assert check_tree(out) == []

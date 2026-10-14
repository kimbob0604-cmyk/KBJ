"""하루 운영 확장(P4) — 분기 보고 마감 직후 하루에 P4 실제 처리기를 붙인다(docs/p4_design.md §8.5,
묶음 S).

창: 2026-11-16(월) 05:00 ~ 11-17(화) 05:00 KST — 3분기 보고 마감일(11-14 토 → 11-16 월), 시즌 최대.
가짜 DART·KIS 는 합성 20종목 세계(`tests/fixtures/synthetic/p4_world.py`)를 서빙한다. 켜진 P4 작업
(`filings.dart_feed`·`fin.quarterly`·`filings.derive`·`earnings.alerts`·`earnings.backfill`·
`fin.valuation_band`·`reports.dart_excel`)과 `public.export` 는 실제 처리기, `consensus.snapshot`
은 운영 등록부에서 꺼져 있으므로(D-P4-10 — 실측 #29 전) **이 시뮬레이션의 등록부 사본에서만**
켠다. 나머지 작업은 P2 시뮬레이션 처리기(같은 KIS 버킷·같은 선점 장부). 창 앞 이력·재무 5년 백필은
하네스 머리말.

단언(§8.5)
- DART: `filings.dart_feed` 780회(평일 07:00~19:59 매분) 모두 ok, 데이터 키(분·다시 훑기·
  접수번호)마다 done 1회·선점 거절 0, 어떤 1초 창에도 DART ≤ 리미터(초당 8), 하루 합 ≤ 일 예산.
- KIS: P3 단언 그대로(어떤 1초에도 ≤ 4, 발급은 auth 만) + `consensus.snapshot` 호출 = 2 × 유니버스.
- 알림: 관심종목 잠정실적 → `alert.earnings` 1통(같은 실적의 정정 공시 16:20 → 여전히 1통), 리비전 →
  하루 1통(16:00 — ADR 0018), 관심 밖 → 0.
- 순서: `fin.valuation_band` 08:50 이 `krx.daily`(08:05) 뒤, `reports.dart_excel` 21:00 이 그날
  `fin.quarterly` 가 받은 3분기 보고서를 쓴다.
- 공개 내보내기: P4 파일(`fin/<코드>.json`)이 생기고 공개 안전 검사(`check_tree`) 통과.

변형(`test_dart_quota_*`): 14:00 에 DART `020`(일 한도 초과)을 넣으면 그날 피드가 멈추고(이후
DART 호출 0), 다음 날 07:00 다시 훑기가 그날 공시를 빠짐 0 으로 채운다.
"""

from __future__ import annotations

import bisect
from collections import Counter
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from kbj.data.limits import load_limits
from kbj.data.private.kis.consensus import TR_ESTIMATE, TR_OPINION
from kbj.services.public_export.manifest import check_tree
from kbj.services.scheduler.claims import RunRecord
from tests.sim.conftest import KST, at
from tests.sim.harness import SimDay, SimOptions, load_registry
from tests.unit.api.api_world_p4 import expect

pytestmark = pytest.mark.sim

START = at(11, 16, 5)
TODAY, PREV, NEXT = date(2026, 11, 16), date(2026, 11, 13), date(2026, 11, 17)
ENABLE = ("consensus.snapshot",)
P4_JOBS = (
    "filings.dart_feed",
    "fin.quarterly",
    "filings.derive",
    "earnings.alerts",
    "earnings.backfill",
    "fin.valuation_band",
    "consensus.snapshot",
    "reports.dart_excel",
)


def _options(start: datetime, hours: float, **kw: Any) -> SimOptions:
    return SimOptions(
        start=start,
        hours=hours,
        p4=True,
        enable=ENABLE,
        ws=False,  # 웹소켓·legacy 조회는 P2 시뮬레이션이 본다 — 이 창은 P4 작업
        reader_calls=False,
        notify_enabled=True,
        **kw,
    )


@pytest.fixture(scope="module")
def day() -> Iterator[SimDay]:
    sim = SimDay(_options(START, 24))
    try:
        yield sim.run()
    finally:
        sim.close()


def _runs(sim: SimDay, job: str) -> list[RunRecord]:
    """창 안의 마지막 시도(씨앗 기록 제외) — 실행 as_of 순."""
    return sorted((r for (j, _a), r in sim.final_runs().items() if j == job), key=lambda r: r.as_of)


def _started(r: RunRecord) -> datetime:
    assert r.started_at is not None, r
    return r.started_at


# ── 작업이 돌았다 ─────────────────────────────────────────────────────────────────────────
def test_every_run_finished_without_failure(day: SimDay) -> None:
    bad = [(j, a, r.status, r.detail) for (j, a), r in day.final_runs().items()
           if r.status not in ("ok", "skipped")]  # fmt: skip
    assert bad == []
    assert day.unsupported == []
    for job in P4_JOBS:
        runs = _runs(day, job)
        assert runs and any(r.status == "ok" for r in runs), job
        # 실제 처리기(시뮬레이션 처리기 아님)
        assert not any(r.detail.get("sim") for r in runs), job
    # 재무 5년 백필(씨앗 — 창 1시간 전, 따로 둔 가짜 DART)이 상세 층·주요계정 층을 채웠다
    assert [r.status for r in day.p4_seed_runs] == ["ok", "ok"]
    assert all(int(r.detail.get("failures", 0)) == 0 for r in day.p4_seed_runs)


def test_consensus_is_enabled_only_in_this_simulation(day: SimDay) -> None:
    """운영 등록부는 consensus.snapshot 이 꺼진 채(D-P4-10) — 이 시뮬레이션만 사본에서 켰다."""
    assert not load_registry().by_name("consensus.snapshot").enabled
    assert day.registry.by_name("consensus.snapshot").enabled
    assert [r.as_of for r in _runs(day, "consensus.snapshot")] == [TODAY.isoformat()]


# ── DART ─────────────────────────────────────────────────────────────────────────────────
def test_dart_feed_runs_every_minute_once(day: SimDay) -> None:
    runs = _runs(day, "filings.dart_feed")
    assert len(runs) == 13 * 60  # 07:00~19:59 매분(등록부 cron "* 7-19 * * 1-5")
    assert {r.as_of[:10] for r in runs} == {TODAY.isoformat()}
    assert all(r.status == "ok" for r in runs)
    assert runs[0].as_of == f"{TODAY}T07:00" and runs[-1].as_of == f"{TODAY}T19:59"
    # 07:00 첫 실행이 전 거래일(11-13)부터 어제까지 다시 훑었다(하루 한 번)
    assert runs[0].detail.get("rescan") is not None, runs[0].detail


def test_dart_data_keys_are_done_once(day: SimDay) -> None:
    rows = [r for r in day.claims.rows if r.key.source == "DART"]
    assert rows and {r.status for r in rows} == {"done"}
    keys = Counter(r.key for r in rows)
    assert max(keys.values()) == 1
    assert [k for k, j in day.claims.refused if k.source == "DART"] == []
    lists = [r.key for r in rows if r.key.dataset == "list"]
    minutes = [k for k in lists if "~" not in k.as_of]
    rescans = [k for k in lists if "~" in k.as_of]
    assert len(minutes) == 13 * 60 and len(rescans) == 1
    assert rescans[0].as_of == f"{PREV}~{TODAY - timedelta(days=1)}"
    # 접수번호 키(처리기가 claims 로 직접 — D-P4-25 ②)도 한 번씩
    feed_event = [r for r in rows if r.job == "filings.dart_feed" and r.key.dataset != "list"]
    assert feed_event and all(r.key.as_of.isdigit() and len(r.key.as_of) == 14 for r in feed_event)
    # 한 데이터셋은 한 작업만
    owners = {ds: jobs for ds, jobs in day.jobs_per_dataset().items() if ds.startswith("DART:")}
    assert {ds: js for ds, js in owners.items() if len(js) > 1} == {}


def test_dart_rate_and_daily_budget(day: SimDay) -> None:
    dart = load_limits(settings=day.sched_settings).source("dart")
    calls = day.dart.calls()
    ts = sorted(c.at for c in calls)
    worst = max(bisect.bisect_left(ts, t + timedelta(seconds=1)) - i for i, t in enumerate(ts))
    assert worst <= dart.rate  # 어떤 1초 창에도 ≤ 8(GCRA 버킷 1)
    on_day = [c for c in calls if c.at.astimezone(KST).date() == TODAY]
    used = day.budget_used("dart", TODAY)
    assert used == len(on_day)  # 예산 카운터 = 실제 호출(빠짐·이중 계산 없음)
    assert dart.daily_cap is not None and used <= dart.daily_cap
    assert {c.status for c in on_day} <= {"000", "013"}  # 013 = 데이터 없음(정상)


def test_fin_quarterly_loads_only_the_reports_of_the_day(day: SimDay) -> None:
    """창 앞 정기보고서는 백필이 받았다 — 창의 fin.quarterly 는 그날 접수된 3분기 보고서만."""
    loaded = [r for r in _runs(day, "fin.quarterly") if int(r.detail.get("loaded", 0)) > 0]
    assert loaded
    w = day.world
    assert w is not None
    periodic = {d.rcept_no for d in w.disclosures_on(TODAY) if d.kind == "periodic"}
    assert periodic
    assert day.repos.filings.fin_unloaded(kinds=("periodic",), limit=10_000) == []
    # 그날 보고서를 촉발한 원시 재무 키에만 그날 접수번호가 붙는다
    raw = [r.key for r in day.claims.rows if r.key.dataset == "fnlttSinglAcntAll"]
    assert raw and {k.as_of.rsplit(":", 1)[-1] for k in raw} <= periodic


# ── KIS ──────────────────────────────────────────────────────────────────────────────────
def test_kis_limit_only_auth_issues_and_consensus_calls(day: SimDay) -> None:
    assert day.kis.max_in_window(1.0) <= 4
    assert {c.consumer for c in day.kis.posts()} == {"auth"}
    assert all(c.status == 200 for c in day.kis.gets())
    (run,) = _runs(day, "consensus.snapshot")
    n = int(run.detail["universe"]["codes"])
    assert n == 15  # 관심 19 → 보통주·코스피/코스닥 15(스팩·리츠·코넥스·우선주 짝 제외 — K 확인 값)
    by_tr = Counter(c.tr_id for c in day.kis.gets())
    assert by_tr[TR_ESTIMATE.tr_id] == n and by_tr[TR_OPINION.tr_id] == n  # 2 × 유니버스


# ── 알림 ────────────────────────────────────────────────────────────────────────────────
def _sent(sim: SimDay, kind: str) -> list[Any]:
    return [r for r in sim.notify_log.rows if r.kind == kind and r.status == "sent"]


def test_alert_earnings_once_even_with_a_correction(day: SimDay) -> None:
    want = expect()["alerts"]["alert.earnings"]
    sent = _sent(day, "alert.earnings")
    assert [r.subject for r in sent] == [want["subject"]] * want["count"]
    assert sent[0].sent_at is not None and sent[0].sent_at.astimezone(KST).date() == TODAY
    # 같은 실적의 정정 공시(16:20)도 들어왔다 — 처리기가 이미 알린 대상이라 다시 보내지 않았다
    corp = want["subject"].split(":", 1)[0]
    prelims = day.repos.filings.prelims(start=TODAY, end=TODAY, corp_code=corp)
    rows = [day.repos.filings.disclosure(p.rcept_no) for p in prelims]
    assert len(prelims) >= 2 and any(d is not None and d.is_correction for d in rows)
    runs = _runs(day, "earnings.alerts")
    again = sum(int(r.detail.get("already_alerted", 0)) + int(r.detail.get("duplicates", 0))
                for r in runs)  # fmt: skip
    assert again >= 1
    # 그 밖의 알림 종류는 하루 1회 묶음·브리핑뿐(일반 공시 push 없음 — D-P4-14)
    kinds = {r.kind for r in day.notify_log.rows}
    assert {k for k in kinds if k.startswith("alert.")} == {"alert.earnings", "alert.revision"}


def test_alert_revision_once_at_1600(day: SimDay) -> None:
    want = expect()["alerts"]["alert.revision"]
    sent = _sent(day, "alert.revision")
    assert len(sent) == want["count"]
    assert sent[0].sent_at is not None
    local = sent[0].sent_at.astimezone(KST)
    assert local.date() == TODAY and local.hour == 16  # ADR 0018
    texts = [str(c.data.get("text", "")) for c in day.tg.sent()]
    hits = [t for t in texts if all(code in t for code in want["codes"])]
    assert len(hits) == 1
    w = day.world
    assert w is not None
    others = {s.code for s in w.stocks} - set(want["codes"])
    assert [c for c in others if c in hits[0]] == []  # 관심 밖·그날 스냅 없는 종목은 싣지 않는다


# ── 순서 ────────────────────────────────────────────────────────────────────────────────
def test_valuation_band_runs_after_krx_daily(day: SimDay) -> None:
    (krx,) = _runs(day, "krx.daily")
    (band,) = _runs(day, "fin.valuation_band")
    assert krx.as_of == band.as_of == PREV.isoformat()  # 둘 다 전 거래일(굳은 의존 — D-P4-25 ④)
    assert krx.finished_at is not None and _started(band) >= krx.finished_at
    assert _started(band).astimezone(KST).strftime("%H:%M") == "08:50"
    assert int(band.detail["codes"]) == len(expect()["stocks"])


def test_dart_report_at_2100_uses_the_quarter_loaded_that_day(day: SimDay) -> None:
    (rep,) = _runs(day, "reports.dart_excel")
    assert _started(rep).astimezone(KST).strftime("%H:%M") == "21:00"
    assert int(rep.detail["built"]) > 0
    w = day.world
    assert w is not None
    watch = {i.code for i in day.repos.journal.watchlist("main")}
    filed = {d.stock_code for d in w.disclosures_on(TODAY) if d.kind == "periodic"}
    targets = sorted(c for c in filed & watch if c is not None and day.repos.fin.report(c))
    assert targets
    last_fin = max(r.finished_at for r in _runs(day, "fin.quarterly")
                   if int(r.detail.get("loaded", 0)) > 0 and r.finished_at is not None)  # fmt: skip
    assert last_fin <= _started(rep)
    for code in targets:
        report = day.repos.fin.report(code)
        assert report is not None and report.built_on == TODAY, code
        assert report.payload["quarterly"]["rows"][-1]["label"] == "3Q26", code


# ── 공개 내보내기 ─────────────────────────────────────────────────────────────────────────
def test_public_export_writes_p4_files_safely(day: SimDay) -> None:
    (run,) = _runs(day, "public.export")
    assert run.status == "ok" and int(run.detail["dirs"]["fin"]) > 0
    assert check_tree(day.public_out) == []
    got = {p.stem for p in (day.public_out / "fin").glob("*.json")}
    want = set(expect()["public_fin_codes"])  # DART 재무가 있는 회사(05:30 — 백필분)
    assert want <= got, sorted(want - got)
    # 공개 파일에 관심종목 흔적이 없다(D-P4-13)
    for p in (day.public_out / "fin").glob("*.json"):
        text = p.read_text(encoding="utf-8").lower()
        assert "watch" not in text and "관심" not in text, p.name


# ── 변형: DART 일 한도(020) ───────────────────────────────────────────────────────────────
QUOTA_START = at(11, 16, 13)
QUOTA_AT = at(11, 16, 14, 0, 30)


@pytest.fixture(scope="module")
def quota() -> Iterator[SimDay]:
    def inject(sim: SimDay) -> None:
        sim.dart.inject("020", 1, endpoint="list.json")

    sim = SimDay(_options(QUOTA_START, 19, hooks=((QUOTA_AT, inject),)))  # 다음 날 08:00 까지
    try:
        yield sim.run()
    finally:
        sim.close()


def test_dart_quota_stops_the_day(quota: SimDay) -> None:
    calls = quota.dart.calls()
    (hit,) = [c for c in calls if c.status == "020"]
    assert hit.at.astimezone(KST).date() == TODAY
    # 그날 예산을 닫았다 — 그 뒤 그날 DART 호출 0(로컬에서 막힌다)
    later = [c for c in calls if c.at > hit.at and c.at.astimezone(KST).date() == TODAY]
    assert later == []
    feed = _runs(quota, "filings.dart_feed")
    after = [r for r in feed if r.as_of[:10] == TODAY.isoformat() and _started(r) > hit.at]
    assert after and all(r.status == "failed" for r in after)


def test_dart_quota_next_day_rescan_misses_nothing(quota: SimDay) -> None:
    feed = _runs(quota, "filings.dart_feed")
    first = [r for r in feed if r.as_of == f"{NEXT}T07:00"]
    assert len(first) == 1 and first[0].status == "ok", first
    assert first[0].detail.get("rescan") is not None
    w = quota.world
    assert w is not None
    want = {d.rcept_no for d in w.disclosures_on(TODAY)}
    assert want
    missing = want - quota.repos.filings.seen(want)
    assert missing == set()

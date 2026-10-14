"""성과 표가 자동으로 쌓인다 — 여러 날 시뮬레이션
(docs/p8_design.md §0.3 '성과 표'·'16:00' 행 — 웨이브 3 WRAP).

틀: `tests/sim/p8.py` — 실제 실행기 + 운영 등록부 다섯 줄 + 실제 처리기 `rules.close`·
`rules.outcome`·`rules.backtest_nightly`, 재료 작업은 합성 세계의 그날 값을 공표하는 대역.
창 = 합성 세계 마지막 25 거래일(추석 연휴 포함), 마감 규칙 7개(E1~E7).

단언
① k 일째 아침 `signal_outcome` 행 수 = Σ(허락 신호마다 D+h ≤ 그 아침 as_of 인 기간 h 수)
   — 날마다 단조 증가, 손계산과 같다.
② 같은 날 재실행(16:00 마감·08:30 성과·08:45 야간) 뒤 신호·성과 행이 늘지 않고(멱등)
   16:00 묶음도 다시 나가지 않는다.
③ `GET /api/rules/stats` 의 표본 수가 아침마다 줄지 않고 마지막 날 > 0, 한 칸(E1 1일)의
   평균·승률·초과 평균이 성과 행 손계산과 같다.
④ 휴장일(정본 캘린더 — 창 안 추석 연휴)에 다섯 작업 실행 0.
⑤ 16:00 규칙: `alert.rule_close` 는 16:00~16:05 접수, 하루 많아야 1통(0건인 날은 0),
   그 밖 시각 발송 0. 야간 일치 감시는 같은 원장이라 `logic` 0.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta

import fakeredis
import pytest

from kbj.core.time import KST
from kbj.services.engine import rules as R
from kbj.services.engine import rules_perf as RP
from kbj.services.scheduler.handlers import JobContext
from tests.sim.p8 import JOBS, RulesDays
from tests.unit.api.api_world import FakeClock as ApiClock
from tests.unit.api.web_fixtures_p8.backtest import BacktestApiWorld

pytestmark = pytest.mark.sim

ALLOWED = frozenset({"sent", "suppressed", "dry"})


@pytest.fixture(scope="module")
def sim() -> Iterator[RulesDays]:
    yield RulesDays().run()


def _expected_outcomes(sim: RulesDays, as_of: date) -> int:
    hs = sim.cfg.horizons
    k = sim.trading_index(as_of)
    n = 0
    for s in sim.all_signals():
        if s.status not in ALLOWED or s.entry_price is None or s.trade_date > as_of:
            continue
        i = sim.trading_index(s.trade_date)
        n += sum(1 for h in hs if i + h <= k)
    return n


def test_every_run_ok_and_handlers_real(sim: RulesDays) -> None:
    bad = [(r.job, r.as_of, r.status, dict(r.detail)) for r in sim.runs.records.values()
           if r.status != "ok"]  # fmt: skip
    assert bad == []
    for job in ("rules.close", "rules.outcome", "rules.backtest_nightly"):
        assert sim.records(job), job
    # 창 안 거래일마다 16:00 마감 평가 한 번
    closes = {r.as_of for r in sim.records("rules.close")}
    assert closes == {d.isoformat() for d in sim.days}


def test_outcomes_accumulate_monotonically_and_match_hand_count(sim: RulesDays) -> None:
    """① 아침마다 성과 행 수 = 손계산, 단조 증가."""
    assert sim.outcome_rows, "rules.outcome 이 한 번도 돌지 않았다"
    prev = -1
    for as_of in sorted(sim.outcome_rows):
        got = sim.outcome_rows[as_of]
        assert got == _expected_outcomes(sim, as_of), as_of
        assert got >= prev, as_of
        prev = got
    assert prev > 0


def test_rerun_same_day_is_idempotent(sim: RulesDays) -> None:
    """② 같은 날 재실행 — 신호·성과 행·발송이 늘지 않는다."""
    last = sim.days[-1]
    sigs = sorted((s.signal_id, s.status) for s in sim.all_signals())
    outs = len(sim.all_outcomes())
    sent = len(sim.notify.sent)
    res_base = {"repos": sim.repos, "kr": sim.kr, "split_ratio": 0.31, "rules_config": sim.cfg,
                "notify": sim.notify}  # fmt: skip

    def ctx(job: str, as_of: date, at: time) -> JobContext:
        now = datetime.combine(as_of + timedelta(days=0 if job == "rules.close" else 1), at,
                               tzinfo=KST)  # fmt: skip
        return JobContext(job=job, as_of=as_of.isoformat(), run_id=f"rerun:{job}", attempt=2,
                          now=now, resources=res_base)  # fmt: skip

    assert R.close(ctx("rules.close", last, time(16, 3))).status == "ok"
    assert RP.outcome(ctx("rules.outcome", last, time(8, 31))).status == "ok"
    assert RP.nightly(ctx("rules.backtest_nightly", last, time(8, 46))).status == "ok"
    assert sorted((s.signal_id, s.status) for s in sim.all_signals()) == sigs
    assert len(sim.all_outcomes()) == outs
    assert len(sim.notify.sent) == sent


def test_stats_api_samples_grow_and_hand_check_one_cell(sim: RulesDays) -> None:
    """③ 표본 수가 아침마다 줄지 않고, E1 1일 칸이 성과 행 손계산과 같다."""
    w = sim.world
    redis = fakeredis.FakeRedis()
    totals: list[int] = []
    rid = sim.rule_ids["E1"]
    c = None
    for as_of in sorted(sim.outcome_rows):
        at = datetime.combine(as_of + timedelta(days=1), time(9, 0), tzinfo=KST)
        c, _ = BacktestApiWorld(w, sim.repos, ApiClock(at), sim.cfg, redis).authed()
        r = c.get("/api/rules/stats")
        if r.status_code == 404:  # 아직 성과 없음(안내) — 표본 0
            totals.append(0)
            continue
        assert r.status_code == 200, r.text
        rows = r.json()["data"]["rows"]
        totals.append(sum(cell["n"] for row in rows for cell in row["cells"]))
    assert totals == sorted(totals) and totals[-1] > 0
    # 손계산 — E1 의 1일 칸(metrics §30.4: 값 단위 %, 표본 = ret 이 있는 성과 행)
    sids = {s.signal_id for s in sim.all_signals() if s.rule_id == rid}
    h1 = [o for o in sim.all_outcomes() if o.signal_id in sids and o.horizon == 1]
    rets = [o.ret * 100 for o in h1 if o.ret is not None]
    exc = [o.excess * 100 for o in h1 if o.ret is not None and o.excess is not None]
    assert rets, "E1 이 창 안에서 1일 성과를 만들어야 한다(심은 칸 Q80010 칸 300)"
    assert c is not None
    body = c.get("/api/rules/stats").json()["data"]
    row = next(x for x in body["rows"] if x["rule_id"] == rid)
    cell = next(x for x in row["cells"] if x["horizon"] == 1)
    assert cell["n"] == len(rets)
    assert cell["mean"] == pytest.approx(statistics.fmean(rets), abs=1e-9)
    assert cell["win_rate"] == pytest.approx(sum(1 for x in rets if x > 0) / len(rets) * 100)
    if exc:
        assert cell["excess_mean"] == pytest.approx(statistics.fmean(exc), abs=1e-9)
    else:
        assert cell["excess_mean"] is None


def test_no_runs_on_holidays(sim: RulesDays) -> None:
    """④ 창 안의 휴장 평일(정본 캘린더)에 다섯 작업 실행 0."""
    first, last = sim.days[0], sim.days[-1]
    d = first
    holidays: list[date] = []
    while d <= last:
        if d.weekday() < 5 and not sim.kr.is_trading_day(d):
            holidays.append(d)
        d += timedelta(days=1)
    assert holidays, "창 안에 휴장 평일이 있어야 한다(추석 연휴)"
    called_on = {at.date() for _job, _as_of, at in sim.calls}
    assert not called_on & set(holidays)
    assert {r.job for r in sim.runs.records.values()} <= set(JOBS)


def test_close_digest_only_at_1600_once_a_day_and_no_logic_diff(sim: RulesDays) -> None:
    """⑤ alert.rule_close 는 16:00~16:05 하루 많아야 1통, 그 밖 발송 0. 야간 일치 logic 0."""
    assert sim.notify.sent, "창 안에서 마감 묶음이 한 번은 나가야 한다(심은 칸)"
    per_day = Counter(s.at.date() for s in sim.notify.sent)
    assert all(n == 1 for n in per_day.values())
    for s in sim.notify.sent:
        assert s.kind == "alert.rule_close"
        assert time(16, 0) <= s.at.time() <= time(16, 5), s
    for r in sim.records("rules.backtest_nightly"):
        assert not r.detail.get("logic"), (r.as_of, r.detail)

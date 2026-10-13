"""브리핑 하루 1회 변형(docs/p5_design.md D-P5-17 ④ — 웨이브 3 WRAP).

`test_one_day_p5` 가 평일 하루(발송 켬)에 아침·마감 각 1통을 본다. 여기서는 1회를 깨뜨릴 수 있는
경우만 짧은 창으로 본다(발송 켬 — 가짜 텔레그램 수신까지):

1. 재기동: 08:12 scheduler 재기동 — 08:10 에 이미 보낸 아침 브리핑을 다시 보내지 않는다.
2. 캐치업: scheduler 가 08:10 을 놓치고 08:30 에 뜬다 — 캐치업 시한(09:00) 안이라 한 번 보낸다.
3. legacy shim 이중 접수: SD `alert_morning_briefing`(notify.yaml legacy_kinds →
   brief.morning)이 같은
   날 한 번 더 넣으면 `duplicate`(중복) — 텔레그램 수신은 그대로 1통.
4. 국내 휴장 + 간밤 미국 거래일(2026-10-09 금 한글날, 미국 10-08 목 거래): 아침 1통(kr_or_after_us),
   마감 요약·수급 리포트 0(거래일 작업).
5. 둘 다 휴장(2026-10-05 월 — 국내 휴장, 간밤 미국 일요일): 브리핑 0(tests/sim/test_holiday.py 도
   같은 날 발송 0 을 본다).

처리기는 시뮬레이션 처리기(같은 실행기·같은 dedup 정책 — 1회 보장은 등록부·실행기·notifier 몫이다).
본문은 `test_one_day_p5`(실제 처리기)와 BRF 단위 시험이 본다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import datetime

import pytest

from kbj.services.notifier.client import legacy_send
from tests.sim.conftest import at
from tests.sim.harness import ROOT, SimDay, SimOptions

pytestmark = pytest.mark.sim

BRIEFS = ("brief.morning", "brief.closing")


def _sim(start: datetime, hours: float, **kw: object) -> SimDay:
    opts: dict[str, object] = dict(
        start=start, hours=hours, notify_enabled=True, ws=False, legacy_calls=False
    )
    opts.update(kw)
    return SimDay(SimOptions(**opts)).run()  # type: ignore[arg-type]


def _sent(sim: SimDay) -> Counter[str]:
    return Counter(r.kind for r in sim.notify_log.rows if r.status == "sent")


def _morning_runs(sim: SimDay) -> list[str]:
    return [r.status for (j, _a), r in sim.final_runs().items() if j == "brief.morning"]


# ── 1. 재기동 ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def restarted() -> Iterator[SimDay]:
    sim = _sim(at(11, 17, 5), 5, restarts=(at(11, 17, 8, 12),))
    try:
        yield sim
    finally:
        sim.close()


def test_restart_after_send_does_not_send_again(restarted: SimDay) -> None:
    assert restarted.restarted_at == [at(11, 17, 8, 12)]
    assert _sent(restarted)["brief.morning"] == 1
    assert len([r for r in restarted.notify_log.rows if r.kind == "brief.morning"]) == 1
    assert _morning_runs(restarted) == ["ok"]


# ── 2. 캐치업 ─────────────────────────────────────────────────────────────────────────────
def _forget_morning(sim: SimDay) -> None:
    """창 앞 기록 씨앗(seed_runs)에서 아침 브리핑만 뺀다 — 08:10 에 scheduler 가 없었던 날."""
    for k in [k for k, r in sim.runs.records.items() if r.job == "brief.morning"]:
        del sim.runs.records[k]


def test_catch_up_before_0900_sends_once() -> None:
    sim = _sim(at(11, 17, 8, 30), 2, prepare=(_forget_morning,))
    try:
        runs = [r for (j, _a), r in sim.final_runs().items() if j == "brief.morning"]
        assert [r.status for r in runs] == ["ok"]
        assert runs[0].started_at is not None and runs[0].started_at < at(11, 17, 9)
        assert _sent(sim)["brief.morning"] == 1
    finally:
        sim.close()


# ── 3. legacy shim 이중 접수 ──────────────────────────────────────────────────────────────
def alert_morning_briefing() -> tuple[bool, str]:
    """SD 함수 이름 그대로(notify.yaml legacy_kinds 가 프레임 이름으로 종류를 찾는다)."""
    return legacy_send("<b>legacy 아침 브리핑</b>", source="SD:server.py")


def test_legacy_shim_second_submit_is_duplicate() -> None:
    """전환 기간 shim: 같은 legacy 함수가 같은 날 두 번 넣으면 둘째는 duplicate(ADR 0005 — legacy
    함수마다 하루 1회). SD 아침 계열 함수는 P5 WRAP 에 server.py 에서 지웠다(아래 확인) — 운영에서
    아침 브리핑을 보내는 길은 brief.morning 하나다."""
    got: list[tuple[bool, str]] = []

    def twice(_s: SimDay) -> None:
        got.extend([alert_morning_briefing(), alert_morning_briefing()])

    sim = _sim(at(11, 17, 5), 5, hooks=((at(11, 17, 8, 30), twice),))
    try:
        assert [ok for ok, _ in got] == [True, True]
        assert "대기열" in got[0][1] and "중복" in got[1][1], got
        mine = "brief.morning:20261117"  # kbj 작업 키(origin 없음 — 종류당 하루 1회)
        rows = [r for r in sim.notify_log.rows if r.kind == "brief.morning"]
        legacy = sorted(r.status for r in rows if r.dedup_key != mine)
        assert legacy.count("sent") == 1 and len(legacy) <= 2, legacy  # 둘째는 발송 없음
        assert [r.status for r in rows if r.dedup_key == mine] == ["sent"]  # kbj 경로 1통
    finally:
        sim.close()
    server = ROOT / "legacy" / "stock_dashboard" / "server.py"
    text = server.read_text(encoding="utf-8")
    for fn in ("alert_morning_briefing", "alert_overnight_prediction",
               "send_us_market_summary_telegram"):  # fmt: skip
        assert f"def {fn}(" not in text, fn


# ── 4·5. 휴장 ─────────────────────────────────────────────────────────────────────────────
def test_kr_holiday_after_us_session_sends_morning_only() -> None:
    sim = _sim(at(10, 9, 5), 13)  # 2026-10-09(금) 한글날 — 미국 10-08(목) 거래
    try:
        sent = _sent(sim)
        assert sent["brief.morning"] == 1
        assert sent["brief.closing"] == 0 and sent["flows.report"] == 0 and sent["etf.report"] == 0
    finally:
        sim.close()


def test_both_closed_sends_no_brief() -> None:
    sim = _sim(at(10, 5, 5), 13)  # 2026-10-05(월) 국내 휴장, 간밤 미국 일요일
    try:
        assert not any(_sent(sim)[k] for k in (*BRIEFS, "flows.report", "etf.report"))
        assert _morning_runs(sim) == []
    finally:
        sim.close()

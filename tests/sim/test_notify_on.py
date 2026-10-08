"""발송 켬 변형 — `KBJ_NOTIFY_ENABLED=true`, 24시간 창 2026-10-06 05:00 KST 부터(설계 §10.5).

기대: 가짜 텔레그램의 sendMessage 성공 수 = 발송 기록 `sent` 수 = 브리핑 2건(U2) + 하루 1회
리포트(수급 — 장 마감 뒤 발송은 16:00, ADR 0018). 첫 발송에 429 를 한 번 주면 `retry_after` 를
따라 다시 보내고 한 번만 나간다.
"""

from __future__ import annotations

from collections import Counter

import pytest

from tests.sim.conftest import KST, at
from tests.sim.harness import TG_CHAT, SimDay, SimOptions

pytestmark = pytest.mark.sim


@pytest.fixture(scope="module")
def notify_on() -> SimDay:
    def first_send_throttled(sim: SimDay) -> None:
        sim.tg.inject_429(1, retry_after=2)

    sim = SimDay(
        SimOptions(
            start=at(10, 6, 5),
            hours=24,
            notify_enabled=True,
            ws=False,
            hooks=((at(10, 6, 5), first_send_throttled),),
        )
    )
    try:
        return sim.run()
    finally:
        sim.close()


def test_send_count_equals_briefs_plus_daily_reports(notify_on: SimDay) -> None:
    sent = notify_on.notify_log.rows
    # P3(묶음 M): etf.collect 의 ETF 리포트 발송(etf.report)은 P5 로 — 등록부에서 notify 를 뺐다
    assert Counter((r.kind, r.status) for r in sent) == {
        ("brief.morning", "sent"): 1,
        ("brief.closing", "sent"): 1,
        ("flows.report", "sent"): 1,
    }
    ok = notify_on.tg.sent("sendMessage")
    assert len(ok) == 3
    assert {str(c.data.get("chat_id")) for c in ok} == {TG_CHAT}
    times = [c.at.astimezone(KST).strftime("%H:%M") for c in ok]
    assert times == ["08:10", "16:00", "16:00"]  # 마감 요약·수급 리포트 모두 16:00(ADR 0018)


def test_429_is_retried_and_sent_once(notify_on: SimDay) -> None:
    attempts = notify_on.tg.sent("sendMessage", ok_only=False)
    assert [c.status for c in attempts][:2] == [429, 200]  # 첫 발송(08:10 아침 브리핑 — P3 부터)
    assert len(attempts) == 4  # 성공 3 + 거절 1
    first, retry = attempts[0], attempts[1]
    assert (retry.at - first.at).total_seconds() >= 2  # retry_after 를 지켰다
    assert first.data.get("text") == retry.data.get("text")


def test_u2_still_one_morning_and_one_closing(notify_on: SimDay) -> None:
    for kind in ("brief.morning", "brief.closing"):
        rows = notify_on.notify_log.of(kind)
        assert [r.status for r in rows] == ["sent"], kind
        assert len(rows[0].message_ids) == 1


def test_webhook_is_checked_only_when_enabled(notify_on: SimDay) -> None:
    methods = Counter(notify_on.tg.methods())
    assert methods["getWebhookInfo"] >= 1  # 켜짐이면 10분마다 점검(꺼짐 변형은 0)
    assert set(methods) <= {"sendMessage", "getWebhookInfo"}

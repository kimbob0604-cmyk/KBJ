"""처리기 `brief.morning`·`brief.closing` — 알림 한 통·중복·실패(docs/p5_design.md §4.5).

하루 1회 = 그날 dedup 키 접수 한 건(notifier 정책 daily — 중복은 성공으로 끝난다).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC
from types import SimpleNamespace
from typing import Any

import pytest

from kbj.core.calendar import TradingCalendar
from kbj.reports.briefs import closing, morning
from kbj.reports.briefs.render import HEADS
from kbj.services.scheduler.handlers import JobContext, resolve
from tests.unit.reports.briefs.brief_world import CLOSING, MORNING, closing_repos, morning_repos


class Notify:
    def __init__(self, *, ok: bool = True, duplicate: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.ok, self.duplicate = ok, duplicate

    def __call__(self, text: str, **kw: Any) -> SimpleNamespace:
        self.calls.append((text, kw))
        return SimpleNamespace(ok=self.ok, duplicate=self.duplicate, reason="합성 거절")


def job_ctx(name: str, now: Any, repos: Any, notify: Callable[..., Any] | None) -> JobContext:
    res: dict[str, Any] = {"repos": repos, "kr": TradingCalendar.default()}
    if notify is not None:
        res["notify"] = notify
    day = now.date().isoformat()
    return JobContext(name, day, "r1", 1, now.astimezone(UTC), resources=res)


def test_등록부_owner_가_처리기로_풀린다() -> None:
    assert resolve("kbj.reports.briefs:morning") is morning
    assert resolve("kbj.reports.briefs:closing") is closing


def test_아침은_brief_morning_한_통_기준일_as_of() -> None:
    n = Notify()
    res = morning(job_ctx("brief.morning", MORNING, morning_repos(), n))
    assert res.status == "ok" and res.rows == 1
    assert len(n.calls) == 1
    text, kw = n.calls[0]
    assert kw["kind"] == "brief.morning"
    assert kw["as_of"] == MORNING.date()
    assert "subject" not in kw  # dedup daily 키 = kind:날짜 — 그날 한 통
    assert text.startswith(f"<b>{HEADS['morning']}</b>")
    assert res.detail["duplicate"] is False
    assert "us" in res.detail["sections"]


def test_마감도_한_통() -> None:
    n = Notify()
    res = closing(job_ctx("brief.closing", CLOSING, closing_repos(), n))
    assert res.status == "ok" and [kw["kind"] for _, kw in n.calls] == ["brief.closing"]
    assert res.detail["not_ready"] == []


def test_같은_날_두번째는_중복이고_성공으로_끝난다() -> None:
    n = Notify(duplicate=True)
    res = morning(job_ctx("brief.morning", MORNING, morning_repos(), n))
    assert res.status == "ok" and res.detail["duplicate"] is True


def test_대기열이_거절하면_실패다() -> None:
    with pytest.raises(RuntimeError, match="합성 거절"):
        morning(job_ctx("brief.morning", MORNING, morning_repos(), Notify(ok=False)))


def test_notify_자원이_없으면_실패다() -> None:
    with pytest.raises(RuntimeError, match="notify"):
        closing(job_ctx("brief.closing", CLOSING, closing_repos(), None))


def test_detail_에는_값이_없다() -> None:
    n = Notify()
    res = closing(job_ctx("brief.closing", CLOSING, closing_repos(), n))
    blob = repr(res.detail)
    for v in ("2,650", "+800억", "합성전자"):
        assert v not in blob

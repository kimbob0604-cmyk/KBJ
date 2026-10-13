"""작업 `etf.report`(kbj.reports.etf:report — 08:20, notify etf.report) 처리기.

합성 세계(p5_world — ETF 8개, 구성종목 11-13·11-16, 변동 실행 11-17)를 메모리 저장소에 올려
처리기를 부른다. 단언: 한 통(대기열 한 건)·kind·subject = 기준일(직전 완료 영업일)·as_of = 실행일,
변동 없는 날도 한 통('감지된 변동 없음'), krx.daily 가
늦으면 있는 마지막 날 + 머리 안내, 대기열 거절·
notify 없음·ETF 일별 없음은 실패(삼키지 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pytest

from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.reports import etf as job
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.p5_world import build_world
from tests.unit.api.web_fixtures_p5.etf import RUN_DATE, load_etf

NOW = datetime(2026, 11, 17, 8, 20, tzinfo=KST)


@dataclass
class Ticket:
    ok: bool = True
    id: str | None = "t-1"
    reason: str = ""
    duplicate: bool = False


@dataclass
class FakeNotify:
    ticket: Ticket = field(default_factory=Ticket)
    calls: list[tuple[str, dict[str, Any]]] = field(
        default_factory=list[tuple[str, dict[str, Any]]]
    )

    def __call__(self, text: str, **kw: Any) -> Ticket:
        self.calls.append((text, kw))
        return self.ticket


@pytest.fixture(scope="module")
def loaded() -> MemoryRepos:
    repos = memory_repos()
    load_etf(repos, build_world())
    return repos


def ctx(repos: Any, notify: Any, as_of: str = RUN_DATE.isoformat()) -> JobContext:
    res: dict[str, Any] = {"repos": repos, "kr": TradingCalendar.default()}
    if notify is not None:
        res["notify"] = notify
    return JobContext(job="etf.report", as_of=as_of, run_id="r1", attempt=1, now=NOW, resources=res)


def test_한_통을_넣는다(loaded: MemoryRepos) -> None:
    n = FakeNotify()
    res = job.report(ctx(loaded, n))
    assert res.status == "ok"
    assert len(n.calls) == 1
    text, kw = n.calls[0]
    assert kw == {
        "kind": "etf.report",
        "source": "etf.report",
        "as_of": RUN_DATE,
        "subject": "2026-11-16",
    }
    assert text.startswith("<b>📊 ETF 리포트</b>  2026-11-16")
    assert "합성로봇" in text and "2개 ETF" in text  # 액티브 A·B 동시 편입
    assert res.detail["base"] == "2026-11-16" and res.detail["changes"] == 3
    assert res.rows == res.detail["messages"] >= 1


def test_변동_없는_날도_한_통(loaded: MemoryRepos) -> None:
    n = FakeNotify()
    job.report(ctx(loaded, n, as_of="2026-11-18"))  # 그날 change_log 없음
    assert len(n.calls) == 1
    text, kw = n.calls[0]
    assert "감지된 변동 없음" in text and "🆕 신규 편입" not in text
    assert kw["subject"] == "2026-11-16"  # 11-17 ETF 일별은 아직 없다 — 있는 마지막 날
    assert "전 영업일 2026-11-17 아직 없음" in text


def test_대기열이_거절하면_실패(loaded: MemoryRepos) -> None:
    n = FakeNotify(Ticket(ok=False, id=None, reason="본문이 비어 있다"))
    with pytest.raises(RuntimeError, match="알림을 넣지 못했다"):
        job.report(ctx(loaded, n))


def test_notify_가_없으면_실패(loaded: MemoryRepos) -> None:
    with pytest.raises(RuntimeError, match="notify"):
        job.report(ctx(loaded, None))


def test_ETF_일별이_없으면_실패() -> None:
    with pytest.raises(RuntimeError, match="ETF 일별이 없다"):
        job.report(ctx(memory_repos(), FakeNotify()))


def test_입력은_직전_완료_영업일_기준(loaded: MemoryRepos) -> None:
    inp = job.make_input(loaded.etf, date(2026, 11, 17), TradingCalendar.default())
    assert inp is not None
    assert inp.base == date(2026, 11, 16) and inp.notes == ()
    assert inp.pair_count == 4 and len(inp.changes) == 3
    assert all(k in inp.snaps for c in inp.changes for k in ((c.fund_id, c.prev_asof),))

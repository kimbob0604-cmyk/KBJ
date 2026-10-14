"""`gex.alert` 처리기(kbj.services.engine.gex_alert — D-P7-16·D-P7-17, §10 Q5).

합성 레벨·메모리 저장소·가짜 Redis."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fakeredis
import pytest

from kbj.config.gex import load_gex
from kbj.core.calendar import TradingCalendar
from kbj.reports.gex.alert import KIND, AlertState
from kbj.services.engine.gex_alert import STALE_AFTER, STATE_KEY, in_window, run
from kbj.services.scheduler.handlers import JobContext, resolve
from kbj.services.scheduler.registry import Registry
from kbj.store.repos.memory import MemoryRepos, memory_repos
from tests.unit.reports.gex.gex_world import at, levels

DAY = date(2026, 12, 10)  # 목(거래일)
CFG = load_gex()


class Notify:
    def __init__(self, *, ok: bool = True) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.ok = ok

    def __call__(self, text: str, **kw: Any) -> SimpleNamespace:
        self.calls.append((text, kw))
        return SimpleNamespace(ok=self.ok, duplicate=False, reason="합성 거절")


def ctx(now: datetime, repos: MemoryRepos, redis: Any, notify: Any = None) -> JobContext:
    res: dict[str, Any] = {"repos": repos, "redis": redis, "kr": TradingCalendar.default(),
                           "gex_config": CFG}  # fmt: skip
    if notify is not None:
        res["notify"] = notify
    return JobContext("gex.alert", now.isoformat(), "r1", 1, now.astimezone(UTC), resources=res)


def test_등록부_owner_가_처리기로_풀리고_정규장_창은_설정과_같다() -> None:
    assert resolve("kbj.services.engine.gex_alert:run") is run
    job = next(
        j
        for j in Registry.load(Path(__file__).parents[4] / "config" / "jobs.yaml").jobs
        if j.name == "gex.alert"
    )
    assert job.notify is not None and job.notify.kind == KIND
    assert (CFG.alert.window.start, CFG.alert.window.end) == ("09:00", "15:30")
    assert CFG.alert.cooldown_s == 600 and CFG.alert.scope == "all"


def test_창_밖_휴장은_건너뛴다() -> None:
    cal = TradingCalendar.default()
    assert in_window(at(DAY, 9, 0), CFG, cal) and in_window(at(DAY, 15, 30), CFG, cal)
    assert not in_window(at(DAY, 8, 59), CFG, cal)
    assert not in_window(at(DAY, 15, 31), CFG, cal)  # 15:30 뒤 — 마감 요약·아침 브리핑에만
    assert not in_window(at(DAY, 20, 0), CFG, cal)  # 야간 — 보내지 않는다
    assert not in_window(at(date(2026, 12, 12), 10, 0), CFG, cal)  # 토요일
    repos, r, n = memory_repos(), fakeredis.FakeRedis(), Notify()
    repos.gex.write_levels(levels(at(DAY, 15, 40)))
    res = run(ctx(at(DAY, 15, 41), repos, r, n))
    assert res.status == "skipped" and n.calls == [] and r.get(STATE_KEY) is None


def test_첫_분은_기준만_다음_분에_건넘을_보낸다() -> None:
    repos, r, n = memory_repos(), fakeredis.FakeRedis(), Notify()
    repos.gex.write_levels(levels(at(DAY, 10, 0), f=351.2, flip=350.0))
    res = run(ctx(at(DAY, 10, 0) + timedelta(seconds=20), repos, r, n))
    assert res.status == "ok" and res.detail["sent"] == 0 and res.detail["baseline"] is True
    assert n.calls == []
    st = AlertState.loads(r.get(STATE_KEY))
    assert st is not None and st.side == 1 and r.ttl(STATE_KEY) > 0

    repos.gex.write_levels(levels(at(DAY, 10, 1), f=349.0, flip=350.0, call_wall=362.5))
    res = run(ctx(at(DAY, 10, 1) + timedelta(seconds=20), repos, r, n))
    assert res.status == "ok" and res.rows == 2
    assert [kw["subject"] for _, kw in n.calls] == ["flip_cross", "wall_move:call"]
    for _, kw in n.calls:
        assert (
            kw["kind"] == "alert.gex_level" and kw["as_of"] == DAY and kw["source"] == "gex.alert"
        )
    # 같은 사이클을 다시 읽어도 다시 보내지 않는다
    run(ctx(at(DAY, 10, 1) + timedelta(seconds=50), repos, r, n))
    assert len(n.calls) == 2


@pytest.mark.parametrize("quality", ["invalid", "stale"])
def test_불량_품질_산출로는_보내지_않고_상태도_그대로(quality: str) -> None:
    repos, r, n = memory_repos(), fakeredis.FakeRedis(), Notify()
    repos.gex.write_levels(levels(at(DAY, 10, 0), f=351.2, flip=350.0))
    run(ctx(at(DAY, 10, 0), repos, r, n))
    before = r.get(STATE_KEY)
    repos.gex.write_levels(levels(at(DAY, 10, 1), f=349.0, flip=350.0, quality=quality))
    res = run(ctx(at(DAY, 10, 1), repos, r, n))
    assert res.detail["sent"] == 0 and res.detail["blocked"]
    assert n.calls == [] and r.get(STATE_KEY) == before


def test_지연_사이클과_산출_없음은_보내지_않는다() -> None:
    repos, r, n = memory_repos(), fakeredis.FakeRedis(), Notify()
    res = run(ctx(at(DAY, 10, 0), repos, r, n))
    assert res.detail["blocked"] == "레벨 산출 없음"
    repos.gex.write_levels(levels(at(DAY, 10, 0)))
    late = at(DAY, 10, 0) + STALE_AFTER + timedelta(seconds=1)
    res = run(ctx(late, repos, r, n))
    assert "지연" in res.detail["blocked"] and r.get(STATE_KEY) is None


def test_넣기_실패는_올리고_상태를_저장하지_않는다() -> None:
    repos, r = memory_repos(), fakeredis.FakeRedis()
    repos.gex.write_levels(levels(at(DAY, 10, 0), f=351.2, flip=350.0))
    run(ctx(at(DAY, 10, 0), repos, r, Notify()))
    before = r.get(STATE_KEY)
    repos.gex.write_levels(levels(at(DAY, 10, 1), f=349.0, flip=350.0))
    with pytest.raises(RuntimeError, match="합성 거절"):
        run(ctx(at(DAY, 10, 1), repos, r, Notify(ok=False)))
    assert r.get(STATE_KEY) == before
    with pytest.raises(RuntimeError, match="notify"):
        run(ctx(at(DAY, 10, 1), repos, r, None))

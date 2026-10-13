"""`trade.derive` 작업(묶음 EXP — docs/p6_design.md §1.4·§3.2): not_ready·급등·급락·
104품목(fixed·top_n)·멱등·설정 오류. 합성 세계(p6_world — 고정 시드, 실데이터 아님) +
메모리 저장소."""

from __future__ import annotations

from datetime import UTC
from decimal import Decimal
from typing import Any

import pytest

from kbj.config.trade import TradeItems, load_trade, load_trade_items, parse_trade_items
from kbj.core.trade_rows import ItemGroupMonthRow
from kbj.engines.trade.items104 import GroupOverlap
from kbj.services.engine.trade import GROUP_MONTHS, JOB, derive
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos import memory_repos
from kbj.store.repos.trade import MemoryTradeRepo
from tests.fixtures.synthetic.p6_world import CLOCK, LAST_YM, build_world


def _ctx(repo: MemoryTradeRepo, as_of: str = LAST_YM, **res: Any) -> JobContext:
    return JobContext(
        job=JOB,
        as_of=as_of,
        run_id="r1",
        attempt=1,
        now=CLOCK.astimezone(UTC),
        resources={"trade_repo": repo, **res},
    )


@pytest.fixture
def repo() -> MemoryTradeRepo:
    r = MemoryTradeRepo()
    build_world().fill(r)
    return r


def test_월간이_없으면_not_ready() -> None:
    res = derive(_ctx(MemoryTradeRepo()))
    assert res.status == "not_ready"


def test_기준_월이_앞서면_not_ready(repo: MemoryTradeRepo) -> None:
    assert derive(_ctx(repo, as_of="2026-11")).status == "not_ready"


def test_as_of_형식_오류는_failed(repo: MemoryTradeRepo) -> None:
    assert derive(_ctx(repo, as_of="2026-13")).status == "failed"


def test_급등·급락·104품목_fixed(repo: MemoryTradeRepo) -> None:
    res = derive(_ctx(repo))
    assert res.status == "ok" and res.detail["mode"] == "fixed" and res.detail["groups"] == 104
    surge = repo.surge(ym=LAST_YM)
    by = {(s.level, s.flow, s.rule, s.key): s for s in surge}
    assert by[("hs4", "exp", "surge", "8502")].yoy == pytest.approx(1.0)
    assert by[("hs4", "exp", "plunge", "2701")].yoy == pytest.approx(-0.5)
    assert {s.level for s in surge} <= set(load_trade().surge.levels)
    assert all(s.source == "KBJ:trade.derive" for s in surge)
    rows = repo.item_group_monthly(ym_from=LAST_YM, ym_to=LAST_YM)
    # 세계에 있는 HS4 가 걸린 묶음만 행이 있다(값 없는 묶음은 0 으로 채우지 않는다)
    world_hs4 = {r.hs for r in build_world().items if r.hs_len == 4}
    items = load_trade_items()
    want = {g.id for g in items.groups if any(p in world_hs4 for p in g.hs)}
    assert {r.group_id for r in rows} == want
    g = next(r for r in rows if r.group_id == next(x.id for x in items.groups if x.hs == ("8502",)))
    assert g.exp_usd == Decimal(120_000_000) and g.yoy == pytest.approx(1.0) and g.mode == "fixed"
    months = {r.ym for r in repo.item_group_monthly(group_id=g.group_id)}
    assert len(months) == GROUP_MONTHS


def test_파일이_비면_top_n_자동_선택(repo: MemoryTradeRepo) -> None:
    empty = TradeItems(version=1, groups=())
    res = derive(_ctx(repo, trade_items=empty))
    assert res.status == "ok" and res.detail["mode"] == "top_n" and res.detail["groups"] == 24
    rows: list[ItemGroupMonthRow] = repo.item_group_monthly(ym_from=LAST_YM, ym_to=LAST_YM)
    assert {r.mode for r in rows} == {"top_n"} and all(r.group_id.startswith("hs") for r in rows)


def test_다시_돌려도_같은_행(repo: MemoryTradeRepo) -> None:
    derive(_ctx(repo))
    a = (repo.surge(), repo.item_group_monthly())
    derive(_ctx(repo))
    assert (repo.surge(), repo.item_group_monthly()) == a


def test_묶음_겹침은_오류를_올린다(repo: MemoryTradeRepo) -> None:
    bad = TradeItems.model_construct(
        version=1, groups=parse_trade_items({"version": 1, "groups": [
            {"id": "a", "name": "가", "hs": ["85"]},
        ]}).groups + parse_trade_items({"version": 1, "groups": [
            {"id": "b", "name": "나", "hs": ["8501"]},
        ]}).groups,
    )  # fmt: skip
    with pytest.raises(GroupOverlap):
        derive(_ctx(repo, trade_items=bad))


def test_repos_자원도_받는다() -> None:
    repos = memory_repos()
    build_world().fill(repos.trade)
    res = derive(
        JobContext(
            job=JOB, as_of=LAST_YM, run_id="r", attempt=1, now=CLOCK, resources={"repos": repos}
        )
    )
    assert res.status == "ok"

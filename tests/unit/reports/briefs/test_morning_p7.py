"""아침 ④ 야간선물·GEX(P7 OUT — D-P7-16): 간밤 야간 세션 마지막 엔진 산출(범위 all), visible 만.

합성 레벨(tests/unit/reports/gex/gex_world.py). 아침 브리핑은 하루 1회(U2) — 새 발송 없이 줄만.
"""

from __future__ import annotations

from datetime import date, timedelta

from kbj.core.quality import Quality
from kbj.reports.briefs import morning_brief as M
from kbj.reports.briefs.job import BUILDERS, build
from kbj.reports.briefs.render import assemble
from kbj.store.repos.memory import memory_repos
from tests.unit.reports.briefs.brief_world import MORNING, ctx, morning_repos
from tests.unit.reports.gex.gex_world import at, levels

DAY = MORNING.date()  # 2026-11-17(화)


def test_야간_마지막_사이클을_싣는다() -> None:
    r = memory_repos()
    r.gex.write_levels(levels(at(DAY, 4, 0), session="night", f=1.0, flip=2.0))
    r.gex.write_levels(levels(at(DAY, 5, 59), session="night", f=351.2, flip=350.0))
    r.gex.write_levels(levels(at(DAY, 8, 30), session="night", f=9.0))  # 발송 뒤 — 안 쓴다
    out = M.night_gex(ctx(r, MORNING))
    assert not out.not_ready
    assert out.lines[0] == "<i>11/17 05:59 KST 야간 마지막 사이클</i>"
    assert out.lines[1] == "F 351.20 · Flip 350.00(Flip 위 +0.34%)"
    assert "콜월 360.00 · 풋월 340.00" in out.lines
    assert out.source == "KBJ:gex.engine" and out.as_of == at(DAY, 5, 59)
    assert out.quality is Quality.OK and out.materials[0].status == "ok"


def test_야간_산출이_아니거나_오래되면_마지막_산출과_지연() -> None:
    r = memory_repos()
    prev = date(2026, 11, 16)
    r.gex.write_levels(levels(at(prev, 15, 44), session="day"))
    out = M.night_gex(ctx(r, MORNING))
    assert out.lines[0] == "<i>간밤 야간 세션 산출 없음 — 마지막 산출을 싣는다</i>"
    assert "11/16 15:44 KST 주간" in out.lines[1]
    m = out.materials[0]
    assert m.status == "late" and m.basis == prev
    text = build("morning", ctx(r, MORNING))[1]
    assert "지연: 야간 GEX(기준일 2026-11-16)" in text


def test_섀도는_값_없이_자리만_재료가_없으면_미준비() -> None:
    r = memory_repos()
    names = ("flip", "flip_distance", "call_wall", "put_wall", "expected_move")
    r.gex.write_levels(
        levels(at(DAY, 5, 59), session="night", flags=dict.fromkeys(names, "shadow"))
    )
    out = M.night_gex(ctx(r, MORNING))
    assert out.lines[-1] == "GEX 레벨 섀도 검증 중 — 싣지 않는다"
    assert not any("351.20" in x for x in out.lines)
    empty = M.night_gex(ctx(memory_repos(), MORNING))
    assert empty.not_ready and empty.materials[0].job == "gex.engine"


def test_섹션_순서는_금리_뒤_일정_앞() -> None:
    r = morning_repos()
    r.gex.write_levels(levels(at(DAY, 5, 59) - timedelta(seconds=1), session="night"))
    brief = assemble("morning", ctx(r, MORNING), BUILDERS["morning"])
    keys = [s.key for s in brief.sections if s.status != "omitted"]
    i = keys.index("night_futures_gex")
    assert keys[i + 1] == "calendar" and "rates_fx_commodities" in keys[:i]
    sec = brief.section("night_futures_gex")
    assert sec is not None and sec.status == "ok" and sec.title == "야간선물·GEX"

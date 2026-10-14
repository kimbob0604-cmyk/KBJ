"""아침 브리핑(08:10) 섹션 — 손 입력(합성 p5_world) 시험(metrics §20, docs/p5_design.md §4.5)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from kbj.core.p5_rows import CalendarEvent, MacroSeriesRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.reports.briefs import morning_brief as M
from kbj.reports.briefs.job import BUILDERS, build
from kbj.reports.briefs.render import assemble
from kbj.store.repos.memory import memory_repos
from tests.fixtures.synthetic.p5_world import TODAY, US_DAY
from tests.unit.reports.briefs.brief_world import (
    BY,
    MORNING,
    cfg,
    ctx,
    morning_repos,
    us_brief_artifact,
)
from tests.unit.reports.gex.gex_world import at, levels


def text_of(repos: object, **kw: Any) -> str:
    return build("morning", ctx(repos, MORNING, **kw))[1]


# ── ① 간밤 미국 ────────────────────────────────────────────────────────────────────────


def test_간밤_미국은_지수_줄과_US_산출을_그대로_싣는다() -> None:
    out = M.us_overnight(ctx(morning_repos(), MORNING))
    assert not out.not_ready
    assert out.lines[0].startswith("합성지수1 ")
    assert out.lines[0].endswith(f"<i>({US_DAY.strftime('%m/%d')} 종가)</i>")
    assert any("간밤 미국장 신고가" in x for x in out.lines)
    assert {m.status for m in out.materials} == {"ok"}
    assert out.source and out.as_of and out.quality is Quality.OK


def test_재료가_없으면_미준비_한_줄이다() -> None:
    brief = assemble("morning", ctx(memory_repos(), MORNING), BUILDERS["morning"])
    sec = brief.section("us")
    assert sec is not None and sec.status == "not_ready"
    assert sec.lines == ("미준비 — us.eod(06:10)",)


def test_US_산출이_늦으면_실제_기준일을_적고_품질_줄에_남긴다() -> None:
    repos = morning_repos(brief=False)
    old = date(2026, 11, 13)
    repos.board.put_us_artifacts([us_brief_artifact(old)], loaded_by=BY)
    text = text_of(repos)
    assert "기준일 2026-11-13" in text
    assert "지연: 간밤 미국 브리프(기준일 2026-11-13)" in text


def test_지수는_없고_산출만_있어도_싣는다() -> None:
    out = M.us_overnight(ctx(morning_repos(us=False), MORNING))
    assert not out.not_ready
    assert out.lines[0].startswith("미국 지수 수신 실패 — 합성지수1, 합성지수2")


# ── ② 금리·달러·원자재 ─────────────────────────────────────────────────────────────────


def test_표는_config_순서이고_美10년은_한_줄이다() -> None:
    out = M.rates_table(ctx(morning_repos(), MORNING))
    body = out.lines[0]
    assert body.startswith("<pre>") and body.endswith("</pre>")
    assert body.count("미 국채 10년") == 1
    assert body.index("미 국채 10년") < body.index("미 국채 2년") < body.index("국고채 3년")


def test_없는_칸은_대시와_품질_줄() -> None:
    text = text_of(morning_repos())
    assert "YAHOO:CL=F         —" in text
    assert "미준비: YAHOO:CL=F(값 없음)" in text


def test_전일_대비는_앞_기간_값과의_차이다() -> None:
    repos = memory_repos()
    w = morning_repos()
    meta = [m for m in w.macro.meta() if m.series_id == "FRED:DGS10"]
    repos.macro.upsert_meta(meta, loaded_by=BY, now=MORNING)

    def row(period: str, v: str) -> MacroSeriesRow:
        at = datetime.combine(date.fromisoformat(period) + timedelta(days=1), time(6), tzinfo=KST)
        return MacroSeriesRow("FRED:DGS10", period, Decimal(v), "%", "FRED", at, Quality.OK,
                              released_at=at)  # fmt: skip

    repos.macro.upsert_series([row("2026-11-13", "4.10"), row("2026-11-16", "4.25")],
                              loaded_by=BY, received_at=MORNING)  # fmt: skip
    c = cfg().model_copy(update={"table_series": ("FRED:DGS10",)})
    body = M.rates_table(ctx(repos, MORNING, cfg_=c)).lines[0]
    assert "4.25%  +0.15  (11/16)" in body


def test_표_재료가_하나도_없으면_섹션_미준비() -> None:
    out = M.rates_table(ctx(memory_repos(), MORNING))
    assert out.not_ready
    assert [m.name for m in out.materials] == ["금리·달러·원자재"]


# ── ③ 한국 매크로(새 공표) ───────────────────────────────────────────────────────────


def _release(repos: object, at: datetime) -> None:
    w = morning_repos()
    meta = [m for m in w.macro.meta() if m.series_id == "KOSIS:DT_1J22003:cpi"]
    r = repos  # type: ignore[assignment]
    r.macro.upsert_meta(meta, loaded_by=BY, now=MORNING)  # type: ignore[attr-defined]
    rows = [
        MacroSeriesRow("KOSIS:DT_1J22003:cpi", "2026-09", Decimal("116.10"), "2020=100", "KOSIS",
                       at - timedelta(days=30), Quality.OK, released_at=at - timedelta(days=30)),
        MacroSeriesRow("KOSIS:DT_1J22003:cpi", "2026-10", Decimal("116.85"), "2020=100", "KOSIS",
                       at, Quality.OK, released_at=at),
    ]  # fmt: skip
    r.macro.upsert_series(rows, loaded_by=BY, received_at=at)  # type: ignore[attr-defined]


def test_새_공표가_있으면_싣고_앞_기간_값을_붙인다() -> None:
    repos = memory_repos()
    _release(repos, datetime.combine(TODAY, time(8, 0), tzinfo=KST))
    out = M.kr_macro(ctx(repos, MORNING))
    assert out.lines == ("소비자물가지수 2026-10: 116.85 2020=100 (전기 116.10)",)


def test_전일_0810_이전_공표는_새_공표가_아니다() -> None:
    repos = memory_repos()
    _release(repos, datetime.combine(TODAY - timedelta(days=1), time(8, 0), tzinfo=KST))
    assert M.kr_macro(ctx(repos, MORNING)).lines == ()


def test_새_공표가_없으면_제목째_빠지고_경고_목록에_없다() -> None:
    brief = assemble("morning", ctx(morning_repos(), MORNING), BUILDERS["morning"])
    assert brief.section("kr_macro") is None
    assert "한국 매크로(새 공표)" in brief.dropped
    assert brief.empty_titles == ()


# ── ⑤ 일정 ─────────────────────────────────────────────────────────────────────────


def _event(d: date, *, forecast: str | None = None, actual: str | None = None) -> CalendarEvent:
    src = "ff" if forecast else "fomc"
    return CalendarEvent(
        f"US:payrolls:{d.isoformat()}:{src}", "payrolls", d, time(22, 30), "미 비농업 고용", "US",
        3, src, Quality.OK, actual=Decimal(actual) if actual else None, unit="천명",
        forecast=Decimal(forecast) if forecast else None,
    )  # fmt: skip


def test_오늘_이번주_어제결과_금통위_DN() -> None:
    repos = memory_repos()
    repos.macro.upsert_calendar([_event(TODAY - timedelta(days=1), actual="120")],
                                loaded_by=BY, received_at=MORNING)  # fmt: skip
    repos.macro_prv.upsert_calendar([_event(TODAY + timedelta(days=2), forecast="150")],
                                    loaded_by=BY, received_at=MORNING)  # fmt: skip
    lines = M.calendar_section(ctx(repos, MORNING)).lines
    assert "<b>어제 발표 결과</b>" in lines
    assert any("결과 120.00천명" in x for x in lines)
    assert any("11/19(목) 22:30" in x and "예상 150.00천명" in x for x in lines)
    assert lines[-1] == "금통위 통화정책방향 결정 D-10 (11/27)"


def test_일정이_없으면_일정_없음_한_줄() -> None:
    brief = assemble("morning", ctx(memory_repos(), MORNING, bok_meetings=()), BUILDERS["morning"])
    sec = brief.section("calendar")
    assert sec is not None and sec.lines == ("일정 없음",)


# ── 단계 밖·품질 줄·격리 ───────────────────────────────────────────────────────────────


def test_구루는_없고_P7_야간_GEX_는_재료가_없으면_미준비() -> None:
    # P7 OUT 이 ④ 야간선물·GEX 를 켰다(D-P7-16) — 내용은 test_morning_p7.py
    text = text_of(morning_repos())
    assert "구루" not in text
    assert "<b>야간선물·GEX</b>\n미준비 — gex.night_minutes(06:00)" in text


def test_모두_ok_면_자료_이상_없음() -> None:
    c = cfg().model_copy(update={"table_series": ("FRED:DGS10", "ECOS:817Y002:ktb3y")})
    repos = morning_repos()
    repos.gex.write_levels(levels(at(MORNING.date(), 5, 59), session="night"))  # ④ 간밤 야간 산출
    text = text_of(repos, cfg_=c)
    assert text.rstrip().endswith("<b>데이터 품질</b>\n자료 이상 없음")


def test_한_섹션이_터져도_나머지는_나간다() -> None:
    def boom(_: object) -> M.SectionOut:
        raise RuntimeError("합성 실패")

    builders = {**BUILDERS["morning"], "rates_fx_commodities": boom}
    brief = assemble("morning", ctx(morning_repos(), MORNING), builders)
    sec = brief.section("rates_fx_commodities")
    assert sec is not None and sec.status == "not_ready"
    us = brief.section("us")
    assert us is not None and us.status == "ok"
    q = brief.section("quality")
    assert q is not None and any("RuntimeError" in x for x in q.lines)


def test_ok_섹션은_출처_기준시각_quality_를_갖는다() -> None:
    brief = assemble("morning", ctx(morning_repos(), MORNING), BUILDERS["morning"])
    for s in brief.sections:
        if s.status == "ok":
            assert s.source and s.as_of is not None and s.quality is not None


def test_같은_입력이면_같은_본문() -> None:
    assert text_of(morning_repos()) == text_of(morning_repos())

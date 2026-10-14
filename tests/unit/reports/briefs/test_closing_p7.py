"""마감 ⑥ GEX·진단(P7 OUT — D-P7-16): 주간 마지막 GEX 사이클 + diag.daily 종합 판정·공포탐욕.

16:00 한 통(ADR 0018) — 새 발송 없이 줄만. diag.daily 는 무른 의존(wait_min 10)이라 없으면 '미준비'.
합성 입력(tests/unit/reports/gex/gex_world.py).
"""

from __future__ import annotations

from datetime import date

from kbj.core.quality import Quality
from kbj.reports.briefs import closing_brief as C
from kbj.reports.briefs.job import build
from kbj.store.repos.memory import MemoryRepos, memory_repos
from tests.unit.reports.briefs.brief_world import CLOSING, closing_repos, ctx
from tests.unit.reports.gex.gex_world import BY, at, fear_greed, levels, verdict

DAY = CLOSING.date()  # 2026-11-17(화)
PREV = date(2026, 11, 16)


def seed(r: MemoryRepos, *, gex: bool = True, diag: bool = True) -> MemoryRepos:
    if gex:
        r.gex.write_levels(levels(at(DAY, 15, 44), f=349.0, flip=350.0))
    if diag:
        r.diag.upsert_verdict(verdict(DAY, "risk_off", -2, parts={"fear_greed": -1, "gex": -1}),
                              computed_at=at(DAY, 16, 0), loaded_by=BY)  # fmt: skip
        for d, s in ((PREV, "40.0"), (DAY, "30.5")):
            r.diag.upsert_fear_greed(fear_greed(d, s), computed_at=at(DAY, 16, 0), loaded_by=BY)
    return r


def test_GEX_와_진단_줄() -> None:
    out = C.gex_diag_section(ctx(seed(memory_repos()), CLOSING))
    assert not out.not_ready
    assert out.lines == (
        "📐 <b>GEX</b> <i>11/17 15:44 KST 주간 마지막 사이클</i>",
        "F 349.00 · Flip 350.00(Flip 아래 -0.29%)",
        "콜월 360.00 · 풋월 340.00",
        "±1σ 345.00~353.00(±4.00pt, 달력 기준)",
        "🧭 <b>진단</b>",
        "종합 판정: 위험 회피(점수 -2) — 근거 −공포탐욕 · −GEX 체제 <i>(잠정)</i>",
        "공포탐욕 30.5 공포(전일 40.0, -9.5)",
    )
    assert out.quality is Quality.ESTIMATED  # 그날 진단은 잠정(D-P7-15)
    assert [m.status for m in out.materials] == ["ok", "ok"]


def test_진단이_아직이면_GEX_만_싣고_미준비_한_줄() -> None:
    out = C.gex_diag_section(ctx(seed(memory_repos(), diag=False), CLOSING))
    assert out.lines[-1] == "미준비 — diag.daily(16:00)"
    assert [m.status for m in out.materials] == ["ok", "not_ready"]


def test_둘_다_없으면_섹션_미준비() -> None:
    out = C.gex_diag_section(ctx(memory_repos(), CLOSING))
    assert out.not_ready and {m.job for m in out.materials} == {"gex.engine", "diag.daily"}


def test_오늘_주간_산출이_아니면_지연() -> None:
    r = memory_repos()
    r.gex.write_levels(levels(at(PREV, 15, 44)))
    out = C.gex_diag_section(ctx(r, CLOSING))
    assert out.lines[1] == "<i>오늘 주간 산출 없음 — 마지막 산출을 싣는다</i>"
    assert out.materials[0].status == "late" and out.materials[0].basis == PREV


def test_마감_요약_한_통에_들어간다() -> None:
    text = build("closing", ctx(seed(closing_repos()), CLOSING))[1]
    assert "<b>GEX·진단</b>\n📐 <b>GEX</b>" in text
    assert (
        text.index("<b>주요 공시</b>")
        < text.index("<b>GEX·진단</b>")
        < text.index("<b>데이터 품질</b>")
    )
    assert "시그널" not in text  # P8 은 아직

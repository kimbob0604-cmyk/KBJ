"""마감 요약(16:00 — ADR 0018) 섹션 — 손 입력 시험(metrics §20, docs/p5_design.md §4.5)."""

from __future__ import annotations

from datetime import timedelta

from kbj.reports.briefs import closing_brief as C
from kbj.reports.briefs.job import BUILDERS, build
from kbj.reports.briefs.render import assemble
from kbj.store.repos.memory import memory_repos
from tests.fixtures.synthetic.p5_world import TODAY
from tests.unit.reports.briefs.brief_world import CLOSING, closing_repos, ctx


def text_of(repos: object) -> str:
    return build("closing", ctx(repos, CLOSING))[1]


def test_지수는_마지막_장중값_환율_시장수급은_기준일_원천_quality() -> None:
    out = C.market_section(ctx(closing_repos(), CLOSING))
    assert out.lines[0] == "코스피 2,650.12 +0.53% (15:30 기준 · kis)"
    assert out.lines[1].startswith("코스닥 850.50 -0.40%")
    assert (
        "코스피 수급 개인 -1,200억 · 외국인 +800억 · 기관 +400억 (기준일 11/17 · kis · 잠정)"
        in (out.lines)
    )


def test_수급이_전_거래일_값이면_그_기준일을_적는다() -> None:
    prev = TODAY - timedelta(days=1)
    text = text_of(closing_repos(flows_day=prev))
    assert "(기준일 11/16 · kis · 잠정)" in text
    assert "지연: 코스피 시장 수급(기준일 2026-11-16)" in text


def test_업종_상위5_하위3_특징주는_거래량_이상_순() -> None:
    lines = C.sectors_section(ctx(closing_repos(), CLOSING)).lines
    assert lines[0].count("업종") == 5 and lines[0].startswith("<b>상위</b> 업종0 +3.00%")
    assert lines[1] == "<b>하위</b> 업종9 -1.50% · 업종8 -1.00% · 업종7 -0.50%"
    assert lines[2] == "<b>특징주</b> 합성역사 거래량 5.5배 · 합성오이 거래량 3.2배"


def test_신고가는_3축_종가기준_표기는_한_번() -> None:
    lines = C.newhigh_section(ctx(closing_repos(), CLOSING)).lines
    text = "\n".join(lines)
    assert text.count("종가 기준") == 1
    assert "🏔 역사적 신고가 1종목" in lines
    assert "📈 52주 신고가 3종목" in lines
    assert "📊 120일 신고가 7종목" in lines
    assert "  … 외 2종목" in lines
    assert "60일" not in text  # ADR 0017 — 120일


def test_보드가_없으면_미준비_board_daily() -> None:
    brief = assemble("closing", ctx(closing_repos(board=False), CLOSING), BUILDERS["closing"])
    sec = brief.section("newhigh")
    assert sec is not None and sec.status == "not_ready"
    assert sec.lines == ("미준비 — board.daily(16:00)",)


def test_수급_동향은_순매수_상위와_연속일수() -> None:
    lines = C.flows_trend(ctx(closing_repos(), CLOSING)).lines
    foreign = lines[0]
    assert foreign.startswith("<b>외국인</b> 합성오이B +30억")
    assert "합성오이 +20억·5일 연속" in foreign
    assert "합성오이B +30억 ·" in foreign  # 오늘만 순매수 — 연속 표기 없음
    assert not any(x.startswith("<b>기관</b>") for x in lines)  # 기관은 전부 순매도
    assert lines[-1] == "<i>기준일 11/17 · kis · 잠정</i>"


def test_주요_공시는_중요도_높음_이상만() -> None:
    lines = C.filings_section(ctx(closing_repos(), CLOSING)).lines
    assert lines == ("합성전자 — 최대주주변경", "합성화학 — 단일판매·공급계약체결")


def test_공시가_없으면_한_줄() -> None:
    assert C.filings_section(ctx(memory_repos(), CLOSING)).lines == (C.NO_FILINGS,)


def test_프로그램_P7_P8_줄은_없다() -> None:
    text = text_of(closing_repos())
    for word in ("프로그램", "GEX", "시그널", "추천", "초안"):
        assert word not in text


def test_전부_있으면_섹션_순서대로_나간다() -> None:
    text = text_of(closing_repos())
    order = ["지수·환율·시장 수급", "업종 상위·하위·특징주", "신고가(120일·52주·역사적)",
             "수급 동향", "주요 공시", "데이터 품질"]  # fmt: skip
    pos = [text.index(f"<b>{t}</b>") for t in order]
    assert pos == sorted(pos)
    assert text.startswith("<b>🔔 마감 요약</b> · 2026-11-17(화) 16:00")

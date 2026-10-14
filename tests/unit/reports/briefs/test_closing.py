"""마감 요약(16:00 — ADR 0018) 섹션 — 손 입력 시험(metrics §20, docs/p5_design.md
§4.5)·수출 속보 한 줄(P6 FLASH — D-P6-8)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import TenDayRow
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


def test_프로그램_P8_줄은_없다() -> None:
    # P7 OUT 이 ⑥ GEX·진단 섹션을 켰다(D-P7-16) — 그 내용은 test_closing_p7.py 가 본다
    text = text_of(closing_repos())
    for word in ("프로그램", "시그널", "추천", "초안"):
        assert word not in text


def test_전부_있으면_섹션_순서대로_나간다() -> None:
    text = text_of(closing_repos())
    order = ["지수·환율·시장 수급", "업종 상위·하위·특징주", "신고가(120일·52주·역사적)",
             "수급 동향", "주요 공시", "데이터 품질"]  # fmt: skip
    pos = [text.index(f"<b>{t}</b>") for t in order]
    assert pos == sorted(pos)
    assert text.startswith("<b>🔔 마감 요약</b> · 2026-11-17(화) 16:00")


# ── 수출 속보 한 줄(P6 FLASH — D-P6-8, metrics §25) ───────────────────────────────────────


def _ten(ym: str, usd: int, seen: datetime, *, first: int | None = None) -> TenDayRow:
    return TenDayRow(
        kind="exp_item", ym=ym, seg=1, col_no=0, name="전체", usd=Decimal(usd),
        first_usd=Decimal(first if first is not None else usd), first_seen_at=seen,
        span_text="01~10", source="DATAGO:15157908", as_of=seen, quality=Quality.ESTIMATED,
    )  # fmt: skip


def _with_flash(rows: list[TenDayRow]) -> object:
    r = closing_repos()
    r.trade.upsert_ten_day(rows, loaded_by="test.briefs", received_at=CLOSING)
    return r


SEEN = datetime(2026, 11, 17, 10, 47, tzinfo=KST)  # 실행일 오전 — 직전 마감 요약(11-16 16:00) 뒤


def test_수출_속보_한_줄은_처음_본_구간_발표_당시_YoY() -> None:
    repos = _with_flash(
        [_ten("2025-11", 20_000_000_000, SEEN - timedelta(days=365)),
         _ten("2026-11", 22_500_000_000, SEEN, first=22_000_000_000)]
    )  # fmt: skip
    out = C.market_section(ctx(repos, CLOSING))
    line = next(x for x in out.lines if x.startswith("수출 속보"))
    assert line == "수출 속보 11월 1~10일 220.0억 달러 · 전년 대비 +10.0% (관세청 잠정 · 발표 당시)"
    assert "DATAGO:15157908" in (out.source or "")
    assert line in text_of(repos)


def test_직전_마감_요약_전에_본_구간은_줄_없음() -> None:
    old = datetime(2026, 11, 16, 15, 59, tzinfo=KST)
    repos = _with_flash([_ten("2026-11", 22_000_000_000, old)])
    assert not any(x.startswith("수출 속보") for x in C.market_section(ctx(repos, CLOSING)).lines)
    assert C.export_flash_line(ctx(memory_repos(), CLOSING)) is None


def test_휴장일_공표는_다음_거래일_마감_요약에() -> None:
    sat = datetime(2026, 11, 21, 11, 0, tzinfo=KST)  # 토요일 공표
    mon = datetime(2026, 11, 23, 16, 0, tzinfo=KST)
    repos = _with_flash([_ten("2026-11", 22_000_000_000, sat)])
    got = C.export_flash_line(ctx(repos, mon))
    assert got is not None and "전년 같은 구간 값 없음" in got[0]
    # 그다음 거래일에는 다시 싣지 않는다(직전 마감 요약 이후 처음 본 것만)
    assert C.export_flash_line(ctx(repos, datetime(2026, 11, 24, 16, 0, tzinfo=KST))) is None

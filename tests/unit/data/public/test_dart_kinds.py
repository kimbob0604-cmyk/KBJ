"""피드 종류 — kbj.data.public.dart.disclosures.feed_kind · FEED_KINDS(묶음 F, §1.3·§4.1).

P2 `KINDS`·`kind_of` 는 바꾸지 않는다(승격 원본 시험 ET `test_dart_for.py::test_mapping` 과 legacy
board 표시가 그 값을 쓴다). 피드는 잘게 나눈 종류를 먼저 보고 안 걸리면 `kind_of` 로 떨어진다. 합성
세계의 공시 종류 (세계 분류)와 맞는지도 본다.
"""

from __future__ import annotations

import pytest

import kbj.data.public.dart.disclosures as D
from tests.fixtures.synthetic.p4_world import build_world


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("결산실적공시예고", "pre_announce"),
        ("연결재무제표기준영업(잠정)실적(공정공시)", "prelim"),
        ("영업(잠정)실적(공정공시)", "prelim"),
        ("[기재정정]연결재무제표기준영업(잠정)실적(공정공시)", "prelim"),
        ("연결재무제표기준영업(잠정)실적(공정공시)(자회사의 주요경영사항)", "prelim"),
        ("실적등에대한전망(공정공시)", "earnings"),  # 가이던스 — 잠정실적이 아니다(kind_of 로)
        ("전환청구권행사", "overhang_exercise"),
        ("신주인수권행사", "overhang_exercise"),
        ("교환청구권행사", "overhang_exercise"),
        ("전환가액의조정", "price_adjust"),
        ("행사가액조정", "price_adjust"),
        ("주요사항보고서(전환사채권발행결정)", "overhang"),
        ("주요사항보고서(신주인수권부사채권발행결정)", "overhang"),
        ("주요사항보고서(교환사채권발행결정)", "overhang"),
        ("주요사항보고서(유상증자결정)", "overhang"),
        ("주요사항보고서(무상증자결정)", "overhang"),
        ("주요사항보고서(유무상증자결정)", "overhang"),
        ("주요사항보고서(자기주식취득결정)", "buyback"),
        ("자기주식취득 신탁계약 체결 결정", "buyback"),
        ("자기주식처분결과보고서", "buyback"),  # kind_of 로
        ("임원ㆍ주요주주특정증권등소유상황보고서", "insider"),
        ("임원·주요주주특정증권등소유상황보고서", "insider"),
        ("주식등의대량보유상황보고서(일반)", "major_holder"),
        ("[기재정정]주식등의대량보유상황보고서(약식)", "major_holder"),
        ("사업보고서 (2025.12)", "periodic"),
        ("반기보고서 (2026.06)", "periodic"),
        ("[첨부정정]분기보고서 (2026.09)", "periodic"),
        ("감사보고서제출", "other"),
        ("단일판매ㆍ공급계약체결", "contract"),
        ("최대주주변경", "owner"),
        ("주권매매거래정지", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_feed_kind(title: str | None, kind: str) -> None:
    assert D.feed_kind(title) == kind


def test_p2_kind_of_is_unchanged_for_the_titles_the_feed_splits() -> None:
    # 피드가 잘게 나눈 제목도 kind_of(보드 표시)는 P2 그대로
    assert D.kind_of("연결재무제표기준영업(잠정)실적(공정공시)") == "earnings"
    assert D.kind_of("임원ㆍ주요주주특정증권등소유상황보고서") == "owner"
    assert D.kind_of("주요사항보고서(전환사채권발행결정)") == "capital"
    assert [k for k, _ in D.KINDS] == [
        "inquiry", "buyback", "capital", "owner", "clinical", "earnings", "contract",
    ]  # fmt: skip


def test_world_kinds_agree_with_the_feed() -> None:
    """세계 분류(합성 세계가 붙인 종류)와 피드 종류가 같은 공시를 같은 경로로 보낸다."""
    same = {"prelim", "pre_announce", "overhang", "overhang_exercise", "price_adjust", "buyback",
            "insider", "major_holder", "periodic", "contract"}  # fmt: skip
    for d in build_world().disclosures:
        if d.kind in same:
            assert D.feed_kind(d.title) == d.kind, (d.ident, d.title)
        elif d.kind == "outlook":
            assert D.feed_kind(d.title) == "earnings"

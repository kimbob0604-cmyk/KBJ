"""공시 중요도(kbj.engines.filings.importance) — SD `score_disclosure`:9974 표 그대로.

docs/p4_design.md §4.1·§8.3('test_importance — SD 표 그대로'): 맞은 키워드 중 최댓값(합 아님), 공백
무시, 등급 경계(10·6·4), 로그인 가산(관심 +3, 시총 '초과' 경계 10조·1조·1,000억).
"""

from __future__ import annotations

import pytest

from kbj.config.fin import FinConfig, load_fin
from kbj.engines.filings.importance import (
    KeywordScore,
    importance_of,
    keyword_score,
    login_score,
)

CFG: FinConfig = load_fin()
KW = CFG.filings.keywords

# SD _DISCLOSURE_SCORE_TABLE:9920 — 설정이 그 표를 그대로 옮겼는지(점수가 바뀌면 이 시험이 먼저
# 깨진다)
SD_TABLE = {
    "상장폐지": 10, "거래정지": 10, "최대주주변경": 10, "최대주주 변경": 10, "공개매수": 10,
    "회생": 10, "파산": 10, "경영권": 10,
    "대규모": 8, "공급계약": 8, "수주": 8, "합병": 8, "분할": 8, "영업양수": 8, "유상증자": 8,
    "감자": 8, "전환사채": 8, "신주인수권": 8,
    "자기주식": 6, "자사주": 6, "배당": 6, "흑자전환": 6, "적자전환": 6, "실적": 6, "영업이익": 6,
    "주식분할": 4, "액면분할": 4, "대표이사": 4, "임원변경": 4, "소송": 4, "제재": 4, "처분": 4,
    "특허": 4,
    "정기주주총회": 2, "이사회": 2, "감사보고서": 2, "분기보고서": 2, "반기보고서": 2,
    "사업보고서": 2,
}  # fmt: skip


def test_config_keywords_are_the_sd_table() -> None:
    assert dict(KW) == SD_TABLE
    assert list(KW) == list(SD_TABLE)  # 순서도 같다(맞은 키워드는 표 순서로 남긴다)


@pytest.mark.parametrize(
    ("title", "score"),
    [
        ("주권매매거래정지", 10),
        ("단일판매ㆍ공급계약체결", 8),
        ("현금ㆍ현물배당결정", 6),
        ("주식분할결정", 8),  # '분할'(8)이 '주식분할'(4)보다 크다 — 최댓값
        ("특허권취득", 4),
        ("정기주주총회결과", 2),
        ("기타시장안내", 0),
        ("", 0),
    ],
)
def test_keyword_score_is_the_max_not_the_sum(title: str, score: int) -> None:
    assert keyword_score(title, KW).score == score


def test_spaces_are_ignored_and_matches_kept_in_table_order() -> None:
    ks = keyword_score("최대 주주 변경 및 유상 증자 결정", KW)
    assert ks == KeywordScore(10, ("최대주주변경", "최대주주 변경", "유상증자"))
    # 합이었다면 28 — 최댓값 10
    assert keyword_score("상장폐지 거래정지 공개매수", KW).score == 10


def test_none_title_is_zero() -> None:
    assert keyword_score(None, KW) == KeywordScore(0, ())


@pytest.mark.parametrize(
    ("total", "level"),
    [(13, "critical"), (10, "critical"), (9, "high"), (6, "high"), (5, "medium"), (4, "medium"),
     (3, "low"), (0, "low")],
)  # fmt: skip
def test_importance_levels_include_the_boundary(total: int, level: str) -> None:
    assert importance_of(total, CFG.filings.importance_levels) == level


@pytest.mark.parametrize(
    ("mktcap", "bonus"),
    [
        (None, 0),
        (10_000_000_000_001, 3),
        (10_000_000_000_000, 2),  # '초과' — 정확히 10조는 2
        (1_000_000_000_001, 2),
        (1_000_000_000_000, 1),
        (100_000_000_001, 1),
        (100_000_000_000, 0),
        (5, 0),
    ],
)
def test_cap_bonus_uses_strictly_greater(mktcap: int | None, bonus: int) -> None:
    s = login_score(0, is_watch=False, mktcap=mktcap, cfg=CFG.filings)
    assert s.cap_bonus == bonus and s.total == bonus


def test_login_score_adds_watch_and_cap_and_grades_the_total() -> None:
    kw = keyword_score("영업(잠정)실적(공정공시)", KW)  # '실적' 6
    assert kw.score == 6
    s = login_score(kw, is_watch=True, mktcap=2_000_000_000_000, cfg=CFG.filings)
    assert (s.keyword, s.watch_bonus, s.cap_bonus, s.total) == (6, 3, 2, 11)
    assert s.importance == "critical"
    plain = login_score(kw, is_watch=False, mktcap=None, cfg=CFG.filings)
    assert (plain.total, plain.importance) == (6, "high")


def test_login_score_rejects_negative() -> None:
    with pytest.raises(ValueError, match="0 이상"):
        login_score(-1, is_watch=False, mktcap=None, cfg=CFG.filings)

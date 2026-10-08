"""ETF 테마 분류(ET themes.py 승격)·유형 7분류(docs/metrics.md §8.1).

ET `etf_tracker_v9` 에는 원본 시험이 없다(docs/p3_design.md §1.11 — E3 0개). 그래서 옮긴 규칙의
동작을 표로 고정한다. Q8 비교: ET `monitor/kr/etf.py:classify`(네이버 탭코드 + 15 테마)와의 차이는
아래 표의 '비고' 로 기록만 한다(ADR 0001 Q8 — 정본은 etf_tracker_v9 쪽).
"""

from __future__ import annotations

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import EtfMeta, EtfType
from kbj.engines.etf.types import (
    EXCLUDE,
    INDEX_THEMES,
    RULES,
    brand_of,
    classify,
    etf_type,
    is_active,
    is_excluded,
    issuer_of,
    leverage_of,
    strip_brand,
    tag,
    typed_meta,
)

# (이름, 기초지수, classify, 유형, 배수, 비고 — ET monitor/kr/etf.py 와의 차이)
TABLE: list[tuple[str, str | None, str | None, EtfType, float | None, str]] = [
    ("KODEX 200", None, "시장대표", EtfType.KR_INDEX, 1.0, "monitor: 대표지수"),
    ("TIGER 코스닥150", None, "코스닥", EtfType.KR_INDEX, 1.0, "monitor: 대표지수"),
    ("KODEX 고배당", None, "배당", EtfType.KR_THEME, 1.0, "monitor: 고배당·가치(우리: 테마)"),
    ("KODEX 반도체", None, "반도체", EtfType.KR_THEME, 1.0, "같음"),
    ("SOL 조선TOP3플러스", None, "조선", EtfType.KR_THEME, 1.0, "monitor: 조선·방산"),
    ("KODEX 은행", None, "금융", EtfType.KR_THEME, 1.0, "은행 ≠ 원자재 '은'"),
    ("TIGER 코스닥글로벌", None, "중소형", EtfType.KR_THEME, 1.0, "'글로벌' 이지만 국내 지수"),
    ("ACE 밸류업액티브", None, "밸류업주주환원", EtfType.KR_THEME, 1.0, "monitor: 기타"),
    ("KODEX 레버리지", None, None, EtfType.LEVERAGED_INVERSE, 2.0, "monitor lev=2"),
    ("KODEX 200선물인버스2X", None, None, EtfType.LEVERAGED_INVERSE, -2.0, "monitor lev=-2"),
    ("KODEX 인버스", None, None, EtfType.LEVERAGED_INVERSE, -1.0, "monitor lev=-1"),
    ("TIGER 곱버스", None, "멀티전략", EtfType.LEVERAGED_INVERSE, -2.0, "monitor: 일반"),
    ("KODEX CD금리액티브(합성)", None, None, EtfType.BOND_CASH, 1.0, "monitor: 탭 6 채권"),
    ("TIGER 미국채10년선물", None, None, EtfType.BOND_CASH, 1.0, "'미국' 보다 채권이 먼저"),
    ("KODEX 종합채권(AA-이상)액티브", None, "멀티전략", EtfType.BOND_CASH, 1.0, "테마≠유형"),
    ("ACE KRX금현물", None, "멀티전략", EtfType.COMMODITY, 1.0, "monitor: 탭 5 원자재"),
    ("KODEX 은선물(H)", None, None, EtfType.COMMODITY, 1.0, ""),
    ("TIGER 금은선물(H)", None, None, EtfType.COMMODITY, 1.0, ""),
    ("KODEX WTI원유선물(H)", None, None, EtfType.COMMODITY, 1.0, ""),
    ("KODEX 미국S&P500", None, "멀티전략", EtfType.OVERSEAS, 1.0, "monitor: 탭 4 해외"),
    ("TIGER 차이나전기차SOLACTIVE", None, "멀티전략", EtfType.OVERSEAS, 1.0, ""),
    ("ACE 미국빅테크TOP7 Plus", None, "인터넷플랫폼", EtfType.OVERSEAS, 1.0, ""),
    ("TIGER 합성테마", "S&P 500", "멀티전략", EtfType.OVERSEAS, 1.0, "기초지수 우선"),
    ("KODEX 합성글로벌", "KOSPI 200", "멀티전략", EtfType.KR_THEME, 1.0, "기초지수가 국내"),
    (
        "KODEX 합성",
        "KOSPI 200 Futures 2X Leverage",
        "멀티전략",
        EtfType.LEVERAGED_INVERSE,
        2.0,
        "기초지수 배수 표시",
    ),
    ("KODEX 합성", "코스피 200 선물 -1X", "멀티전략", EtfType.LEVERAGED_INVERSE, -1.0, ""),
    ("KODEX 삼성전자단일종목", None, None, EtfType.OTHER, 1.0, "classify 제외·유형 규칙 없음"),
    ("KODEX 200선물", None, None, EtfType.OTHER, 1.0, ""),
]


@pytest.mark.parametrize(("name", "base", "theme", "kind", "lev", "_note"), TABLE)
def test_type_table(
    name: str, base: str | None, theme: str | None, kind: EtfType, lev: float | None, _note: str
) -> None:
    assert classify(name) == theme
    assert etf_type(name, base) is kind
    assert leverage_of(name, base) == lev


def test_every_type_is_reachable() -> None:
    """화면 유형 필터에 7개가 다 나오고 '기타' 가 0 이 아닌지(분류 구멍 감시)."""
    got = {etf_type(n, b) for n, b, *_ in TABLE}
    assert got == set(EtfType)


def test_classify_rules_kept_from_et() -> None:
    assert (
        len(RULES) == 31
        and RULES[0][0] == "조선"
        and RULES[-1] == ("멀티전략", ["액티브", "플러스", "Plus", "TR", ""])
    )
    assert EXCLUDE[:6] == ["레버리지", "인버스", "선물", "숏", "2X", "단일종목"]
    assert INDEX_THEMES == {"시장대표", "코스닥", "팩터"}
    # 위에서 먼저 맞는 것: '반도체' 와 '200' 이 다 있으면 반도체
    assert classify("KODEX 반도체200") == "반도체"
    # 브랜드를 떼고 본다: 'PLUS' 브랜드가 '플러스' 규칙에 걸리지 않는다
    assert classify("PLUS 고배당주") == "배당"
    assert classify("어느브랜드 아무거나") == "멀티전략"
    assert is_excluded("KODEX 2x 무엇") and not is_excluded("KODEX 200")


def test_brand_and_issuer() -> None:
    assert strip_brand("TIMEFOLIO 코스피액티브") == "코스피액티브"  # 긴 브랜드 먼저
    assert brand_of("TIME 코스피액티브") == "TIME"
    assert brand_of("모르는 ETF") == "모르는"
    assert brand_of("") == "?"
    assert issuer_of("KODEX 200") == "삼성"
    assert issuer_of("UNICORN 무엇") == "현대차증권"  # ET 의 한자 오타를 고쳤다(ADR 0010)
    assert issuer_of("모르는 ETF") == "모르는"
    assert is_active("TIME 코스피액티브") and not is_active("KODEX 200")
    assert tag("반도체") == "#반도체ETF" and tag(None) == ""


def test_typed_meta_fills_classification_only() -> None:
    m = EtfMeta("069500", "KODEX 200", None, None, None, None, None, "코스피 200", None, None,
                "krx", Quality.OK)  # fmt: skip
    t = typed_meta(m)
    assert (t.issuer, t.brand, t.theme, t.etf_type, t.leverage) == (
        "삼성",
        "KODEX",
        "시장대표",
        EtfType.KR_INDEX,
        1.0,
    )
    assert (t.base_index, t.source, t.code) == (m.base_index, m.source, m.code)
    nameless = EtfMeta("X", None, None, None, None, None, None, None, None, None, "k", Quality.OK)
    assert typed_meta(nameless) is nameless
    with pytest.raises(ValueError):
        etf_type("  ")

"""ETF 테마 분류·유형 7분류(docs/metrics.md §8.1 정본, docs/p3_design.md §4.3·D-P3-13).

승격 원본: ET `etf_tracker_v9/themes.py` — `EXCLUDE`:6, `RULES`:13, `BRANDS`·`ISSUER_OF_BRAND`,
`strip_brand`:70, `brand_of`:77, `issuer_of`:84, `is_excluded`:88, `is_active`:92, `classify`:96,
`tag`:109. 이름·규칙·순서를 그대로 옮겼다(legacy `themes.py` 는 이 모듈을 다시 내보내는 shim).
바꾼 것 하나: `ISSUER_OF_BRAND['UNICORN']` 의 첫 글자가 한자 '现' 으로 잘못 들어가 있던 것을
'현대차증권' 으로 고쳤다(ADR 0010).

테마(`classify`)와 유형(`etf_type`)은 1:1 이 아니다. `classify` 는 레버리지·채권 등을 `None` 으로
빼고 해외·원자재 구분이 없다. 유형은 metrics §8.1 의 순서(위에서 먼저 맞는 것):

1. 레버리지·인버스 — 이름(또는 기초지수 이름)에 `레버리지`·`인버스`·`2X`·`숏`·`곱버스`, 기초지수
   이름의 배수 표시(`2X`·`-1X` …)
2. 채권·현금 — `채권`·`국고채`·`통안채`·`회사채`·`은행채`·`금리`·`머니마켓`·`CD`·`KOFR`·`단기자금`
   (+ `국채`·`단기채`·`특수채`·`크레딧` — [확인 필요] 이 모듈에서 더한 낱말)
3. 원자재 — `골드`·`금현물`·`은`·`원유`·`WTI`·`구리`·`농산물`·`원자재`(+ `금은`·`천연가스`·
   `브렌트`·`팔라듐`·`니켈`·`비철금속` [확인 필요]). `은` 은 낱말 앞(한글 뒤가 아님)이고 뒤가
   `행` 이 아닐 때만(은행주 ETF 를 원자재로 보지 않게)
4. 해외주식 — 해외 지수 낱말. **기초지수 이름 우선**: 기초지수 이름에 해외 낱말이 있으면 해외,
   국내 표시(`코스피`·`KOSPI`·`코스닥`·`KOSDAQ`·`KRX`·`FnGuide`·`에프앤가이드`·`WISE`)가 있으면
   해외가 아니다. 기초지수 이름이 없거나 어느 쪽도 아니면 ETF 이름으로 본다. `코스닥글로벌` 은
   국내 지수라 지우고 본다
5. 국내 대표지수 — `classify` 가 `시장대표`·`코스닥`·`팩터`
6. 국내 테마 — 그 밖의 `classify` 테마(`멀티전략` 포함)
7. 기타 — 위에 안 걸리고 `classify` 도 `None`(예: 이름에 `선물`·`단일종목` 만 있는 상품)

영문 낱말은 대소문자를 가리지 않고, 영문자에 붙은 경우는 낱말로 보지 않는다(`CD` 가 `ABCD` 에
걸리지 않게). 규칙은 [확인 필요] — 실데이터 분류표는 키 수령 뒤 체크리스트(#20)에서 검토한다.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Final

from kbj.core.rows import EtfMeta, EtfType

__all__ = [
    "BRANDS",
    "EXCLUDE",
    "INDEX_THEMES",
    "ISSUER_OF_BRAND",
    "PASSIVE_THEMES",
    "RULES",
    "brand_of",
    "classify",
    "etf_type",
    "is_active",
    "is_excluded",
    "issuer_of",
    "leverage_of",
    "strip_brand",
    "tag",
    "typed_meta",
]

# ── ET themes.py 그대로 ──────────────────────────────────────────────────────────────────

# 이 키워드가 있으면 종목 트래킹 대상에서 제외 (파생·구조화 상품)
EXCLUDE: Final[list[str]] = [
    "레버리지", "인버스", "선물", "숏", "2X", "단일종목",
    "채권혼합", "금리", "머니마켓", "TR채권", "국고채", "통안채", "회사채", "은행채",
]  # fmt: skip

# 순서 중요 — 위에서부터 먼저 매칭되는 테마를 채택
# (테마명, [키워드...])
RULES: Final[list[tuple[str, list[str]]]] = [
    ("조선",        ["조선", "해운"]),
    ("방산우주",     ["방산", "우주항공", "우주테크", "우주"]),
    ("원자력",       ["원자력", "SMR"]),
    ("AI전력인프라",  ["AI전력", "전력인프라", "전력설비", "전력핵심", "전력기기", "AI인프라"]),
    ("반도체",       ["반도체", "HBM", "SK하이닉스밸류", "비메모리"]),
    ("2차전지",      ["2차전지", "배터리", "전고체", "양극재", "음극재", "리사이클링"]),
    ("로봇휴머노이드",  ["로봇", "로보틱스", "휴머노이드", "피지컬AI"]),
    ("바이오헬스",    ["바이오", "헬스케어", "의료기기", "의료AI", "CDMO", "제약"]),
    ("AI소프트웨어",  ["AI", "인공지능", "소버린AI", "온디바이스", "생성형"]),
    ("신재생수소",    ["신재생", "친환경에너지", "수소", "태양광", "ESS", "기후변화", "탄소"]),
    ("자동차",       ["자동차", "스마트카", "자율주행", "모빌리티", "전기&수소차"]),
    ("게임",        ["게임"]),
    ("엔터콘텐츠",    ["K-POP", "KPOP", "K컬처", "K콘텐츠", "엔터", "미디어", "웹툰", "드라마",
                    "콘텐츠"]),
    ("화장품뷰티",    ["화장품", "K-뷰티", "뷰티"]),
    ("소비재",       ["K-푸드", "푸드", "농업", "필수소비재", "생활소비재", "경기소비재",
                    "경기방어", "소비", "성장소비"]),
    ("인터넷플랫폼",   ["인터넷", "플랫폼", "e커머스", "메타버스", "소프트웨어", "5G", "네트워크",
                    "광통신", "IT", "테크", "BBIG", "뉴딜디지털", "혁신기술", "미래전략기술",
                    "이노베이션", "메가테크"]),
    ("금융",        ["금융", "은행", "증권", "보험", "지주", "IB"]),
    ("건설기계",     ["건설", "기계장비", "중공업", "산업재", "CAPEX", "설비투자"]),
    ("소재",        ["철강", "에너지화학", "소재"]),
    ("운송여행레저",  ["운송", "여행레저", "여행", "레저", "골프"]),
    ("리츠부동산",    ["리츠", "부동산", "인프라"]),
    ("그룹주",       ["삼성그룹", "현대차그룹", "SK그룹", "LG그룹", "한화그룹", "포스코그룹",
                    "두산그룹", "카카오그룹", "5대그룹"]),
    ("배당",        ["배당", "커버드콜"]),
    ("밸류업주주환원", ["밸류업", "주주가치", "주주환원", "자사주"]),
    ("ESG",        ["ESG", "사회책임"]),
    ("중소형",       ["중소형", "중형주", "코스닥글로벌", "포스트IPO", "IPO"]),
    ("수출",        ["수출", "내수주", "제조업핵심", "전략산업", "주도업종"]),
    ("코스닥",       ["코스닥"]),
    ("시장대표",     ["200", "KRX100", "KRX300", "KTOP30", "MSCI", "코스피", "TOP10", "대형주",
                    "우량주", "블루칩", "동학개미", "TOP5", "Top5", "우량업종", "대장장이",
                    "수급상위", "베스트일레븐", "업종대표"]),
    ("팩터",        ["가치", "성장", "모멘텀", "퀄리티", "로우볼", "저변동", "멀티팩터",
                    "최소변동성", "동일가중", "고배당", "밸류", "퀀트", "우선주", "R&D", "셀렉트"]),
    # 위 어느 것에도 안 걸리는 다중전략·비섹터 상품. '기타'라는 이름보다 성격이 분명하다.
    ("멀티전략",     ["액티브", "플러스", "Plus", "TR", ""]),
]  # fmt: skip

BRANDS: Final[list[str]] = [
    "KODEX", "TIGER", "RISE", "ACE", "PLUS", "SOL", "KIWOOM", "HANARO", "KoAct",
    "TIMEFOLIO", "TIME", "WON", "1Q", "IBK", "BNK", "HK", "MIDAS", "UNICORN",
    "TRUSTON", "DAISHIN343", "FOCUS", "TREX", "VITA", "KCGI", "ARIRANG", "KBSTAR",
    "에셋플러스", "마이티", "마이다스", "파워", "히어로즈", "우리", "아이엠에셋", "더제이",
    "DS", "V&S", "R&D", "KEDI", "S&P",
]  # fmt: skip

ISSUER_OF_BRAND: Final[dict[str, str]] = {
    "KODEX": "삼성", "TIGER": "미래에셋", "RISE": "KB", "ACE": "한국투자", "PLUS": "한화",
    "SOL": "신한", "KIWOOM": "키움", "HANARO": "NH아문디", "KoAct": "삼성액티브",
    "TIME": "타임폴리오", "TIMEFOLIO": "타임폴리오", "WON": "우리", "1Q": "하나",
    "BNK": "BNK", "IBK": "IBK", "TRUSTON": "트러스톤", "DAISHIN343": "대신",
    "UNICORN": "현대차증권", "MIDAS": "마이다스", "VITA": "비티에스", "KCGI": "KCGI",
}  # fmt: skip

# 지수를 그대로 복제하는 테마 — 변동이 운용사 판단이 아니라 지수 이벤트라 화면에서 후순위
# (ET tracker.py PASSIVE_THEMES)
PASSIVE_THEMES: Final[frozenset[str]] = frozenset({"시장대표", "코스닥", "팩터", "ESG", "기타"})
# 유형 '국내 대표지수' 로 보는 테마(metrics §8.1 순서 5)
INDEX_THEMES: Final[frozenset[str]] = frozenset({"시장대표", "코스닥", "팩터"})

_BRANDS_LONGEST: Final = sorted(BRANDS, key=len, reverse=True)


def strip_brand(name: str) -> str:
    for b in _BRANDS_LONGEST:
        if name.upper().startswith(b.upper()):
            return name[len(b) :].strip()
    return name


def brand_of(name: str) -> str:
    for b in _BRANDS_LONGEST:
        if name.upper().startswith(b.upper()):
            return b
    return name.split()[0] if name.split() else "?"


def issuer_of(name: str) -> str:
    return ISSUER_OF_BRAND.get(brand_of(name), brand_of(name))


def is_excluded(name: str) -> bool:
    return any(k.upper() in name.upper() for k in EXCLUDE)


def is_active(name: str) -> bool:
    return "액티브" in name


def classify(name: str) -> str | None:
    """ETF명 → 테마명. 섹터에 안 걸리는 다중전략 상품은 '멀티전략'."""
    if is_excluded(name):
        return None
    body = strip_brand(name)
    up = body.upper()
    for theme, kws in RULES:
        for k in kws:
            if k.upper() in up:
                return theme
    return "멀티전략"


def tag(theme: str | None) -> str:
    return f"#{theme}ETF" if theme else ""


# ── 유형 7분류 (metrics §8.1) ───────────────────────────────────────────────────────────


def _words(*words: str) -> re.Pattern[str]:
    """낱말 목록 → 하나의 정규식. 영문 낱말은 영문자에 붙지 않을 때만(대소문자 무시)."""
    parts: list[str] = []
    for w in words:
        p = re.escape(w)
        if w[:1].isascii() and w[:1].isalpha():
            p = r"(?<![A-Za-z])" + p
        if w[-1:].isascii() and w[-1:].isalpha():
            p = p + r"(?![A-Za-z])"
        parts.append(p)
    return re.compile("|".join(parts), re.IGNORECASE)


_LEV_INV: Final = _words("레버리지", "인버스", "2X", "숏", "곱버스", "LEVERAGE", "INVERSE")
# 기초지수 이름의 배수 표시: 2X·-1X·1.5X·-2X (1X 는 배수가 아니다)
_MULT: Final = re.compile(r"(?<![A-Za-z0-9.])([-−]?)(\d+(?:\.\d+)?)\s?[Xx](?![A-Za-z])")
_BOND_CASH: Final = _words(
    "채권", "국고채", "통안채", "회사채", "은행채", "금리", "머니마켓", "CD", "KOFR", "단기자금",
    "국채", "단기채", "특수채", "크레딧",
)  # fmt: skip
_COMMODITY: Final = _words(
    "골드", "금현물", "금은", "원유", "WTI", "구리", "농산물", "원자재",
    "천연가스", "브렌트", "팔라듐", "니켈", "비철금속",
)  # fmt: skip
_SILVER: Final = re.compile(r"(?<![가-힣])은(?!행)")
_OVERSEAS: Final = _words(
    "미국", "S&P", "나스닥", "차이나", "중국", "일본", "인도", "베트남", "유럽", "글로벌",
    "MSCI ACWI", "필라델피아", "선진국", "신흥국", "이머징", "대만", "홍콩", "항셍", "니케이",
    "독일", "아시아", "라틴", "브라질", "다우존스", "NASDAQ", "NYSE", "NIKKEI", "HANG SENG",
    "CSI", "STOXX", "PHLX", "MSCI WORLD", "MSCI EM", "DOW JONES", "GLOBAL", "US", "CHINA",
    "JAPAN", "INDIA", "EUROPE",
)  # fmt: skip
_DOMESTIC_INDEX: Final = _words(
    "코스피", "KOSPI", "코스닥", "KOSDAQ", "KRX", "FnGuide", "에프앤가이드", "WISE", "KOREA",
)  # fmt: skip
_KOSDAQ_GLOBAL: Final = re.compile(r"코스닥\s?글로벌", re.IGNORECASE)


def _clean(text: str | None) -> str:
    return (text or "").strip()


def _lev_inv(name: str, base_index: str) -> bool:
    if _LEV_INV.search(name) or _LEV_INV.search(base_index):
        return True
    for sign, num in _MULT.findall(base_index):
        if sign or float(num) != 1.0:
            return True
    return False


def _commodity(text: str) -> bool:
    return bool(_COMMODITY.search(text) or _SILVER.search(strip_brand(text)))


def _overseas(name: str, base_index: str) -> bool:
    base = _KOSDAQ_GLOBAL.sub("", base_index)
    if base:
        if _OVERSEAS.search(base):
            return True
        if _DOMESTIC_INDEX.search(base):
            return False
    return bool(_OVERSEAS.search(_KOSDAQ_GLOBAL.sub("", name)))


def etf_type(name: str, base_index: str | None = None) -> EtfType:
    """ETF 이름(+ 기초지수 이름) → 유형 7분류(metrics §8.1 — 위에서 먼저 맞는 것)."""
    nm = _clean(name)
    base = _clean(base_index)
    if not nm:
        raise ValueError("ETF 이름이 비었다")
    if _lev_inv(nm, base):
        return EtfType.LEVERAGED_INVERSE
    if _BOND_CASH.search(nm) or _BOND_CASH.search(base):
        return EtfType.BOND_CASH
    if _commodity(nm) or (base and _commodity(base)):
        return EtfType.COMMODITY
    if _overseas(nm, base):
        return EtfType.OVERSEAS
    theme = classify(nm)
    if theme is None:
        return EtfType.OTHER
    if theme in INDEX_THEMES:
        return EtfType.KR_INDEX
    return EtfType.KR_THEME


_INV_WORDS: Final = _words("인버스", "INVERSE", "숏")
_TWO_WORDS: Final = _words("레버리지", "LEVERAGE")


def leverage_of(name: str, base_index: str | None = None) -> float | None:
    """레버리지 배수(2.0·−1.0·−2.0 …). 레버리지·인버스가 아니면 1.0, 표시는 있는데 배수를 못 읽으면
    None(지어내지 않는다). 규칙은 ET `monitor/kr/etf.py:_leverage`(2X·레버리지·(2) → 2배, 인버스 →
    −1, 둘 다 → −2) + 기초지수의 배수 표시(`-1X`·`2X`) + `곱버스`(−2)."""
    nm = _clean(name)
    base = _clean(base_index)
    if not _lev_inv(nm, base):
        return 1.0
    both = f"{nm} {base}"
    inverse = bool(_INV_WORDS.search(both))
    mults = [(s, float(n)) for s, n in _MULT.findall(both) if s or float(n) != 1.0]
    if mults:
        k = max(n for _, n in mults)
        return -k if inverse or any(s for s, _ in mults) else k
    if "곱버스" in both:
        return -2.0
    two = bool(_TWO_WORDS.search(both)) or "(2)" in both
    if inverse:
        return -2.0 if two else -1.0
    if two:
        return 2.0
    return None


def typed_meta(m: EtfMeta) -> EtfMeta:
    """메타에 운용사·브랜드·테마·유형·배수를 채운다(이름이 없으면 그대로). 상장일·기초지수 등
    다른 칸은 건드리지 않는다. 수집 처리기(`etf.collect`)·KRX 메타 갱신이 같이 쓴다(두 벌 금지)."""
    if not m.name or not m.name.strip():
        return m
    name = m.name.strip()
    return replace(
        m,
        issuer=issuer_of(name),
        brand=brand_of(name),
        theme=classify(name),
        etf_type=etf_type(name, m.base_index),
        leverage=leverage_of(name, m.base_index),
    )

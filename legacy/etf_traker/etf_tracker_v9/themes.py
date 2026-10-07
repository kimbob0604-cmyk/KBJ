"""ETF 테마 분류 — ETF명 키워드 규칙 기반.
텔레그램 리포트의 #해시태그 단위가 된다."""
import re

# 이 키워드가 있으면 종목 트래킹 대상에서 제외 (파생·구조화 상품)
EXCLUDE = [
    '레버리지', '인버스', '선물', '숏', '2X', '단일종목',
    '채권혼합', '금리', '머니마켓', 'TR채권', '국고채', '통안채', '회사채', '은행채',
]

# 순서 중요 — 위에서부터 먼저 매칭되는 테마를 채택
# (테마명, [키워드...])
RULES = [
    ('조선',        ['조선', '해운']),
    ('방산우주',     ['방산', '우주항공', '우주테크', '우주']),
    ('원자력',       ['원자력', 'SMR']),
    ('AI전력인프라',  ['AI전력', '전력인프라', '전력설비', '전력핵심', '전력기기', 'AI인프라']),
    ('반도체',       ['반도체', 'HBM', 'SK하이닉스밸류', '비메모리']),
    ('2차전지',      ['2차전지', '배터리', '전고체', '양극재', '음극재', '리사이클링']),
    ('로봇휴머노이드',  ['로봇', '로보틱스', '휴머노이드', '피지컬AI']),
    ('바이오헬스',    ['바이오', '헬스케어', '의료기기', '의료AI', 'CDMO', '제약']),
    ('AI소프트웨어',  ['AI', '인공지능', '소버린AI', '온디바이스', '생성형']),
    ('신재생수소',    ['신재생', '친환경에너지', '수소', '태양광', 'ESS', '기후변화', '탄소']),
    ('자동차',       ['자동차', '스마트카', '자율주행', '모빌리티', '전기&수소차']),
    ('게임',        ['게임']),
    ('엔터콘텐츠',    ['K-POP', 'KPOP', 'K컬처', 'K콘텐츠', '엔터', '미디어', '웹툰', '드라마', '콘텐츠']),
    ('화장품뷰티',    ['화장품', 'K-뷰티', '뷰티']),
    ('소비재',       ['K-푸드', '푸드', '농업', '필수소비재', '생활소비재', '경기소비재',
                    '경기방어', '소비', '성장소비']),
    ('인터넷플랫폼',   ['인터넷', '플랫폼', 'e커머스', '메타버스', '소프트웨어', '5G', '네트워크',
                    '광통신', 'IT', '테크', 'BBIG', '뉴딜디지털', '혁신기술', '미래전략기술',
                    '이노베이션', '메가테크']),
    ('금융',        ['금융', '은행', '증권', '보험', '지주', 'IB']),
    ('건설기계',     ['건설', '기계장비', '중공업', '산업재', 'CAPEX', '설비투자']),
    ('소재',        ['철강', '에너지화학', '소재']),
    ('운송여행레저',  ['운송', '여행레저', '여행', '레저', '골프']),
    ('리츠부동산',    ['리츠', '부동산', '인프라']),
    ('그룹주',       ['삼성그룹', '현대차그룹', 'SK그룹', 'LG그룹', '한화그룹', '포스코그룹',
                    '두산그룹', '카카오그룹', '5대그룹']),
    ('배당',        ['배당', '커버드콜']),
    ('밸류업주주환원', ['밸류업', '주주가치', '주주환원', '자사주']),
    ('ESG',        ['ESG', '사회책임']),
    ('중소형',       ['중소형', '중형주', '코스닥글로벌', '포스트IPO', 'IPO']),
    ('수출',        ['수출', '내수주', '제조업핵심', '전략산업', '주도업종']),
    ('코스닥',       ['코스닥']),
    ('시장대표',     ['200', 'KRX100', 'KRX300', 'KTOP30', 'MSCI', '코스피', 'TOP10', '대형주',
                    '우량주', '블루칩', '동학개미', 'TOP5', 'Top5', '우량업종', '대장장이',
                    '수급상위', '베스트일레븐', '업종대표']),
    ('팩터',        ['가치', '성장', '모멘텀', '퀄리티', '로우볼', '저변동', '멀티팩터', '최소변동성',
                    '동일가중', '고배당', '밸류', '퀀트', '우선주', 'R&D', '셀렉트']),
    # 위 어느 것에도 안 걸리는 다중전략·비섹터 상품. '기타'라는 이름보다 성격이 분명하다.
    ('멀티전략',     ['액티브', '플러스', 'Plus', 'TR', '']),
]

BRANDS = ['KODEX', 'TIGER', 'RISE', 'ACE', 'PLUS', 'SOL', 'KIWOOM', 'HANARO', 'KoAct',
          'TIMEFOLIO', 'TIME', 'WON', '1Q', 'IBK', 'BNK', 'HK', 'MIDAS', 'UNICORN',
          'TRUSTON', 'DAISHIN343', 'FOCUS', 'TREX', 'VITA', 'KCGI', 'ARIRANG', 'KBSTAR',
          '에셋플러스', '마이티', '마이다스', '파워', '히어로즈', '우리', '아이엠에셋', '더제이',
          'DS', 'V&S', 'R&D', 'KEDI', 'S&P']

ISSUER_OF_BRAND = {
    'KODEX': '삼성', 'TIGER': '미래에셋', 'RISE': 'KB', 'ACE': '한국투자', 'PLUS': '한화',
    'SOL': '신한', 'KIWOOM': '키움', 'HANARO': 'NH아문디', 'KoAct': '삼성액티브',
    'TIME': '타임폴리오', 'TIMEFOLIO': '타임폴리오', 'WON': '우리', '1Q': '하나',
    'BNK': 'BNK', 'IBK': 'IBK', 'TRUSTON': '트러스톤', 'DAISHIN343': '대신',
    'UNICORN': '现대차증권', 'MIDAS': '마이다스', 'VITA': '비티에스', 'KCGI': 'KCGI',
}


def strip_brand(name):
    for b in sorted(BRANDS, key=len, reverse=True):
        if name.upper().startswith(b.upper()):
            return name[len(b):].strip()
    return name


def brand_of(name):
    for b in sorted(BRANDS, key=len, reverse=True):
        if name.upper().startswith(b.upper()):
            return b
    return name.split()[0] if name.split() else '?'


def issuer_of(name):
    return ISSUER_OF_BRAND.get(brand_of(name), brand_of(name))


def is_excluded(name):
    return any(k.upper() in name.upper() for k in EXCLUDE)


def is_active(name):
    return '액티브' in name


def classify(name):
    """ETF명 → 테마명. 섹터에 안 걸리는 다중전략 상품은 '멀티전략'."""
    if is_excluded(name):
        return None
    body = strip_brand(name)
    up = body.upper()
    for theme, kws in RULES:
        for k in kws:
            if k.upper() in up:
                return theme
    return '멀티전략'


def tag(theme):
    return f'#{theme}ETF' if theme else ''

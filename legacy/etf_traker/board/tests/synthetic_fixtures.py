#!/usr/bin/env python3
"""합성 픽스처 생성기 — KBJ P1 이식 (ADR 0001 U3, legacy/etf_traker/board/MIGRATION.md).

원본 ETF-Traker 의 아래 시험 입력은 로그인 등급 실데이터(보드 산출 랭킹)이거나 개인
데이터(블루스카이 계정 핸들·게시물 요약, 사용자 노트, 그날 발송본 state)였다. 공개
레포에 넣지 않고 **같은 모양**(키·필드·자료형·행 수 규모)의 합성 데이터로 바꿨다.
이 스크립트가 그 합성 데이터를 결정론적으로(시드 고정) 만든다. 기대 텍스트는 손으로
맞추지 않는다 — board 의 렌더러(`xdigest.render.compose`)와 랭킹 엔진
(`engine.rankings`)으로 다시 만든다.

  tests/fixtures/rankings_sample.json                  engine.rankings 로 계산(시드 20260826)
  tests/fixtures/rankings_tg_sample.json               경계 조건은 손 설계, 값은 난수(시드 20260827)
  tests/fixtures/xdigest_render_posts.json             가상 계정 12개 · 게시물 156건
  tests/fixtures/xdigest_render_facts.json             아래 EXAMPLE(9장 합성 예시)에 허용 차이를 적용
  tests/fixtures/xdigest_render_expected.txt           xdigest.render.compose 출력
  tests/fixtures/xdigest_render_sections_expected.txt  같음 — 별도 구획(test_xdigest_render 의
                                                       SEC_POSTS·SEC_FACTS·_sec_fixture 를 그대로 씀)
  tests/fixtures/note_2026-09-21.yaml                  합성 일일 노트 (test_note)
  tests/fixtures/xdigest_state/                        합성 state/xdigest (test_xdigest_preview)
  docs/XDIGEST.md 9장 예시 블록                         --doc 일 때만 다시 쓴다

계정 이름은 전부 지어낸 것이다(`fx` 가 들어간다). 회사는 '가상 ○○사' 로 적었다 —
예외는 시험 코드가 이름으로 찾는 종목(삼성중공업·네오이뮨텍·제닉, 한미반도체,
소프트캠프·선도전기·KBI메탈)뿐이고, 그 이름에 붙은 값·문장은 전부 합성이다.

사용 (작업 디렉터리 = legacy/etf_traker):
    python -m board.tests.synthetic_fixtures           # 다시 만든다
    python -m board.tests.synthetic_fixtures --check   # 디스크와 같은지만 본다 (다르면 종료 코드 1)
    python -m board.tests.synthetic_fixtures --doc     # XDIGEST.md 9장 예시 블록도 다시 쓴다
    python -m board.tests.synthetic_fixtures --measure # 예시·분할 길이(XDIGEST.md 1장 숫자)를 찍는다
"""
import argparse
import ast
import copy
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.dirname(HERE)
FIX = os.path.join(HERE, 'fixtures')
DOC = os.path.join(BOARD, 'docs', 'XDIGEST.md')
TEST_RENDER = os.path.join(HERE, 'test_xdigest_render.py')

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(BOARD))
    __package__ = 'board.tests'                 # noqa: A001 — `python board/tests/…py` 로도 돈다

from ..engine import rankings as RK             # noqa: E402
from ..xdigest import render as XR              # noqa: E402
from ..xdigest import send as XS                # noqa: E402


def _json(obj, indent):
    return json.dumps(obj, ensure_ascii=False, indent=indent) + '\n'


# ═══════════════════════════ 1. 랭킹 (test_telegram) ═══════════════════════════
# 원본 픽스처의 계약(옛 열 이름 ret_1w·ret_1m·n_newhigh)을 그대로 따른다. 표 모양은
# engine.rankings 가 만들고, 이 파일은 합성 유니버스와 표 정의만 준다.
SAMPLE_SEED = 20260826
EDGE_SEED = 20260827

SAMPLE_SECTORS = [  # (섹터, 구성종목 수) — 원본과 같은 규모(13섹터 · 626종목)
    ('반도체', 128), ('2차전지', 96), ('전력기기', 73), ('바이오', 62), ('조선', 54),
    ('화장품', 45), ('방산', 41), ('인터넷', 31), ('게임', 28), ('원전', 23),
    ('은행', 19), ('해운', 17), ('우주항공', 9),
]
NAME_SUFFIX = ['테크', '바이오', '전자', '소재', '정밀', '에너지', '로직', '케미칼', '시스템',
               '메디칼', '파마', '모빌리티', '중공업', '건설', '물산', '식품', '게임즈',
               '네트웍스', '솔루션', '해운']
SAMPLE_COLUMNS = [('mktcap', '시가총액', 'amount'), ('ret_1d', '1d', 'pct'),
                  ('ret_1w', '1w', 'pct'), ('ret_1m', '1m', 'pct'),
                  ('vol_3d_1m', '거래량 3일/1달', 'ratio'), ('n_newhigh', '신고가', 'int')]
SAMPLE_BOARDS = [
    dict(key='ret_1w', title='1w 주가수익률 순위', sort_by='ret_1w', desc=True,
         columns=SAMPLE_COLUMNS),
    dict(key='vol_3d_1m', title='거래량 3일/1달 순위', sort_by='vol_3d_1m', desc=True,
         columns=SAMPLE_COLUMNS),
]
SAMPLE_ROWS = 15
HEAD = dict(as_of='2026-08-26', prev_asof='2026-08-25', market='KR', source='synthetic',
            generated_at='2026-08-26T18:00:00+09:00', taxonomy='wics_like')


def _name(i, rng):
    # '가상' + 일련번호 3자리 + 업종 꼬리. 번호가 고정 폭이라 한 이름이 다른 이름의
    # 부분 문자열이 되지 않는다(test_non_cross_stocks_are_absent 가 부분 문자열로 본다).
    return f'가상{i:03d}{rng.choice(NAME_SUFFIX)}'


def _universe(rng):
    rows, i = [], 0
    for sec, n in SAMPLE_SECTORS:
        drift = rng.gauss(0, 2.2)
        drift7 = drift * 1.8 + rng.gauss(0, 2.0)
        for k in range(n):
            i += 1
            cap = max(round(math.exp(rng.gauss(math.log(2500), 1.1)), 1), 320.0)
            chg = round(max(-29.9, min(29.9, drift + rng.gauss(0, 3.2))), 2)
            r7 = round(drift7 + rng.gauss(0, 6.5), 2)
            r1w = round(r7 * 0.8 + rng.gauss(0, 3.0), 2)
            r1m = round(r1w * 0.9 + rng.gauss(0, 9.0), 2)
            vol = round(100 * math.exp(rng.gauss(0, 0.45) + 0.05 * r1w), 1)
            nh = rng.choice([0, 0, 0, 0, 1, 1, 2, 3])
            row = dict(code=f'S{i:05d}', name=_name(i, rng), sector=sec, mktcap=cap,
                       chg_pct=chg, ret_7d=r7, ret_1d=chg, ret_1w=r1w, ret_1m=r1m,
                       vol_3d_1m=vol, n_newhigh=nh)
            if n < 10 and k >= 3:
                # 값을 못 받은 종목 — 섹터 1~5등 목록이 5개보다 짧아지는 경우(원본에도 있었다)
                row.update(chg_pct=None, ret_7d=None, ret_1d=None)
            rows.append(row)
    # 1w 상위 6등 하나는 1m·거래량 배수를 못 받은 종목으로 둔다 — 종목 표의 null 셀(원본에도 있었다)
    sixth = sorted((r for r in rows if r['ret_1w'] is not None), key=lambda r: -r['ret_1w'])[5]
    sixth.update(ret_1m=None, vol_3d_1m=None)
    return rows


def _slim_sector_board(b):
    """engine 의 섹터 표에서 픽스처 계약의 키만 남긴다."""
    return dict(key=b['key'], label=b['label'], ret_label=b['ret_label'],
                weighting=b['weighting'],
                sectors=[dict(rank=s['rank'], name=s['name'], ret=s['ret'],
                              ret_raw=s['ret_raw'], n=s['n'],
                              breadth=dict(up=s['breadth']['up'], flat=s['breadth']['flat'],
                                           down=s['breadth']['down']),
                              top=s['top'])
                         for s in b['sectors']])


def rankings_sample():
    rng = random.Random(SAMPLE_SEED)
    rows = _universe(rng)
    sboards = [_slim_sector_board(RK.sector_board(rows, spec, cfg={}))
               for spec in RK.SECTOR_PERIODS]
    kboards = [RK.stock_board(rows, spec, limit=SAMPLE_ROWS) for spec in SAMPLE_BOARDS]
    cross = RK.mark_cross(kboards)
    out = dict(HEAD, missing=[
        '투자자별 수급: 합성 픽스처 — 소스 미확보 상태를 흉내 낸 결손 문구',
        '뉴스: 합성 픽스처 — 수집 소스가 없어 섹터 트리거를 적을 수 없다는 결손 문구',
    ], sector_boards=sboards, stock_boards=kboards, cross_codes=cross)
    return out


def _pct(rng, lo, hi):
    """2자리 소수의 등락률. 끝자리가 0 이면 한 칸 민다 — `+41.20%` 는 '0%' 를 품는다
    (test_null_cell_is_dash_not_zero 가 그 문자열이 없는지 본다)."""
    v = round(rng.uniform(lo, hi), 2)
    if round(abs(v) * 100) % 10 == 0:
        v = round(v + 0.01, 2)
    return v


def rankings_edge():
    """경계 조건 전용 — 계약이 다루는 이상한 모양을 일부러 넣는다.

    - 섹터 1등 종목의 등락률이 null(마지막 섹터 '삼성중공업')
    - cross 행의 셀이 null(네오이뮨텍 vol_3d_1m)
    - 표시 문자열은 있는데 원값이 null(제닉 ret_1w '0.00%')
    - 마크다운 문법 글자가 든 종목명('…2차전지_소재[합성]*')
    """
    rng = random.Random(EDGE_SEED)
    f = RK._fmt
    sec_names = ['비철금속', '원전', '건설', '전기가스', '화장품', '은행', '반도체', '해운',
                 '게임', '조선']
    rets = sorted((_pct(rng, -6.5, 8.5) for _ in sec_names), reverse=True)
    sectors = []
    for i, (nm, r) in enumerate(zip(sec_names, rets), 1):
        n = rng.randint(9, 90)
        up = int(n * min(0.95, max(0.05, 0.5 + r / 15)))      # 등락률 부호와 상승 종목 비율을 맞춘다
        flat = rng.randint(0, (n - up) // 5)
        cap = round(rng.uniform(900, 400000), 1)
        if nm == '조선':
            top = dict(rank=1, code='E00010', name='삼성중공업', ret=None, ret_raw=None,
                       mktcap=f('amount', cap), mktcap_raw=cap)
        else:
            tr = _pct(rng, r, r + 25) if r > 0 else _pct(rng, r - 10, r)
            top = dict(rank=1, code=f'E{i:05d}', name=f'가상{100 + i:03d}{rng.choice(NAME_SUFFIX)}',
                       ret=f('pct', tr), ret_raw=tr, mktcap=f('amount', cap), mktcap_raw=cap)
        sectors.append(dict(rank=i, name=nm, ret=f('pct', r), ret_raw=r, n=n,
                            breadth=dict(up=up, flat=flat, down=n - up - flat), top=[top]))

    cols = [('mktcap', '시가총액', 'amount'), ('ret_1d', '1d', 'pct'), ('ret_1w', '1w', 'pct'),
            ('ret_1m', '1m', 'pct'), ('vol_3d_1m', '거래량 3일/1달', 'ratio')]

    def row(code, name, cross, raw, cells=None):
        c = {k: f(kind, raw[k]) for k, _l, kind in cols}
        c.update(cells or {})
        return dict(code=code, name=name, cross=cross, cells=c, cells_raw=dict(raw))

    def raw(vol=True):
        return dict(mktcap=round(rng.uniform(900, 9000), 1), ret_1d=_pct(rng, 1, 29.5),
                    ret_1w=_pct(rng, 8, 45), ret_1m=_pct(rng, 5, 60),
                    vol_3d_1m=round(rng.uniform(150, 1200), 1) if vol else None)

    neo = row('E00201', '네오이뮨텍', True, raw(vol=False))
    etf = row('E00202', '가상ETF 2차전지_소재[합성]*', True, raw())
    big = row('E00203', '가상대형전자', False, dict(raw(), mktcap=5123000.0))
    alu = row('E00204', '가상204소재', True, raw())
    gr = raw()
    gr['ret_1w'] = None                        # 원값은 계산된 적이 없다
    genic = row('E00205', '제닉', True, gr, cells={'ret_1w': '0.00%'})
    surge = row('E00206', '가상206에너지', False, dict(raw(), vol_3d_1m=4660.0))

    def board(key, title, sort_by, rows):
        out = []
        for i, r in enumerate(rows, 1):
            out.append(dict(rank=i, **copy.deepcopy(r)))
        return dict(key=key, title=title, sort_by=sort_by,
                    columns=[dict(key=k, label=lb, kind=kd) for k, lb, kd in cols], rows=out)

    kb = [board('ret_1w', '1w 주가수익률 순위', 'ret_1w', [neo, etf, big, alu, genic]),
          board('vol_3d_1m', '거래량 급증 순위', 'vol_3d_1m', [surge, alu, genic, etf, neo])]
    return dict(HEAD, missing=[
        '투자자별 수급: 합성 — 소스 미확보 결손 문구',
        '뉴스: 합성 — 상승·하락의 트리거를 적을 수 없다는 결손 문구',
        '수정주가 미반영 의심 3종목 — 역사적 신고가 판정 제외(합성 문구)',
    ], sector_boards=[dict(key='1d', label='금일', ret_label='금일 상승률', weighting='mktcap',
                           sectors=sectors)],
        stock_boards=kb, cross_codes=['E00201', 'E00204', 'E00202', 'E00205'])


# ═══════════════════════════ 2. X 다이제스트 (test_xdigest_*) ═══════════════════════════
# 가상 계정. 키는 X 식 표기(9장 예시의 `└ @…`), 블루스카이 핸들은 소문자 + `_`→`-`
# (test_example_diff_is_zero 가 둘을 그렇게 맞춘다). 12개 중 3개에 `_` 가 있다(1장 파스 모드).
ACCOUNTS = ['ChipWireFx', 'fxequitylab', 'LumenCapFx', 'NorthStarLogFx', 'DrFxMem',
            'fxstreetwire', 'OmegaFxNotes', 'fxwafer_tw', 'The_Fx_Investor', 'fxkan07',
            'ParallaxFxLabs', 'Trader_Fxie_']
MAIN = 'ChipWireFx'                     # 채움 게시물의 계정(원본도 한 계정이 대부분이었다)
FOCUS = ('FXKAN', 'fxkan07')            # 넷째 구획(계정 인사이트) — 렌더하지 않는다(허용 차이 8)
N_POSTS = 156
ANALYZED = 68
ASOF = '2026-09-21'
NAMES = {'한미반도체': '042700'}         # test_xdigest_render.NAMES 와 같은 값(아래에서 대조)


def bsky(acct):
    return acct.lower().replace('_', '-') + '.bsky.social'


# 9장 합성 예시. 문서 순서 그대로다(소주제·단독 소식은 facts.json 에 **거꾸로** 들어간다 —
# 1장 정렬이 실제로 도는지 보려면 입력 순서가 기대 순서와 달라야 한다).
#   fact: (본문, [계정…])  계정이 None 이면 검증(3-3)에서 탈락한 줄 — 예시에만 있고 facts 에 없다.
#   standalone: (예시 본문, 계정, 잘라낸 평가 문구 또는 None)
#   views: 예시 본문(예시는 `•`, 렌더는 `»` — 허용 차이 1). None 표시는 검증 탈락.
EXAMPLE = dict(
    head=['X 24시간 다이제스트 · 2026-09-21 09:00 KST',
          '게시물 156건 수집 / 반도체·AI 관련 68건 분석',
          '(커버 구간: 9/20 18:00 ~ 9/21 08:48 KST. X 리스트 타임라인 페이지네이션이 '
          '14시간 지점에서 멈춰 그 이전 구간은 미수집)'],
    themes=[
        ('가상 HBM 수출단가, 5개월 만에 첫 하락', [
            ('가상 무역통계 기준 8월 HBM 평균 수출단가 $61.27, 전월 대비 -2.9%',
             ['fxequitylab', 'OmegaFxNotes']),
            ('3월 $33.70 → 4월 $41.25 → 5월 $47.10 → 6월 $56.40 → 7월 $63.03로 4개월 연속 '
             '오른 뒤 반전 (3~7월 약 +87%)', None),
            ('가상대학 소속 교수 A: "공급 물량 증가 때문일 수 있다. 후발 공급사 물량이 늘면 '
             '단가 하방 압력이 커질 것"', ['NorthStarLogFx']),
            ('다만 복수 계정이 "수요가 꺾인 것과는 다르다"며 단가≠수요 해석에 선을 그음',
             ['ChipWireFx']),
        ]),
        ('가상 메모리 3사, 장기공급계약(LTA) 체제로 재편', [
            ('가상 메모리사 A·B·C가 3~5년 장기 공급계약 체결 중. C사는 take-or-pay 방식 '
             '전략 고객 계약까지 진행', ['DrFxMem']),
            ('가상 메모리사 D는 이미 캐파의 약 절반이 기간 계약으로 묶임', ['ChipWireFx']),
            ('기존 분기·반기·연간 단위 단기 거래 관행이 빠르게 LTA 중심으로 전환', ['fxkan07']),
            ('가상 증권사 E 애널리스트: LTA 비중이 높아지는데도 3Q ASP는 견조. 대부분 DRAM '
             '제품 QoQ +18~25%, NAND +12% 이상', ['DrFxMem']),
        ]),
        ('메모리 시장 규모·Capex 전망 동반 상향', [
            ('가상 증권사 F: 2030년 글로벌 DRAM+NAND TAM 1.9조 달러 전망 (기존 1.6조 달러에서 '
             '상향)', ['NorthStarLogFx']),
            ('비교치 — 2018년 클라우드 붐 정점 약 1,500억 달러, 2025년 약 1,900억 달러',
             ['DrFxMem']),
            ('가상 증권사 G: 글로벌 메모리 Capex 2026년 $48.2B(+31.7% YoY) → 2027년 '
             '$66.9B(+38.8% YoY). 수혜 후보로 가상 장비사 H·I·J 제시', ['fxequitylab']),
            ('가상 증권사 K: 가상 메모리사 A 매수 의견, 목표주가 보통주 41만원 / 우선주 30만원. '
             '"2027년이 2026년보다 더 타이트"', ['NorthStarLogFx']),
        ]),
        ('CPU 수요, 갑자기 메인 토픽으로', [
            ('학습 → 추론 → 에이전틱 AI로 워크로드가 이동하면서 GPU 개수보다 CPU:GPU 비율이 '
             '핵심 지표로 부상', ['LumenCapFx']),
            ('가상 칩 설계사 L 자료 기준 구간별 비율: 학습기 6~8 GPU:1 CPU → 추론기 3~4:1 → '
             '에이전틱기 1:1', ['ChipWireFx']),
            ('"CPU가 GPU 일을 뺏는다는 게 아니라, GPU 공장 옆에 훨씬 큰 CPU 공장이 필요해졌다는 '
             '얘기"', ['ParallaxFxLabs']),
            ('@ParallaxFxLabs: "CPU 성장률이 HBM 포함 거의 모든 데이터센터 부품보다 높을 수 '
             '있다"', ['OmegaFxNotes']),
            ('9/14~20 주간 가상 칩 설계사 L 관련 화제는 ①메모리 제휴 가능성 ②CPU 수급 타이트 '
             '③공정 로드맵 가속 3가지', ['LumenCapFx']),
        ]),
        ('가상 메모리사 A, 차세대 HBM 증산·후공정은 외주로', [
            ('가상 메모리사 A, 내년 차세대 HBM 생산량을 올해 대비 최소 2배로 확대 계획',
             ['NorthStarLogFx']),
            ('HBM 공정 소모재인 캐리어 외주 세정 물량을 올해 월 2만 장 → 내년 월 5만 장으로 증량 '
             '(작년 월 1만 장)', ['ChipWireFx']),
            ('동시에 DDR5 모듈·SSD 백엔드 캐파를 외부 파트너로 넘기고 자사 패키징 캐파는 HBM에 '
             '집중 재배치', ['fxwafer_tw']),
        ]),
        ('가상 후발 메모리사 M 신세대 DRAM 양산 — 위협인가', [
            ('가상 후발 메모리사 M, 신세대 DRAM 플랫폼 양산 개시 발표. 셀 간격 12.3nm, DUV 다중 '
             '패터닝 적용. 24Gb LPDDR5X 2종 공개', ['fxstreetwire']),
            ('9/20 가상 산업박람회 부스에서 실물 전시', ['ChipWireFx']),
            ('반론: "세대 격차가 줄었다는 주장은 실제 스택을 보면 성립하지 않는다. 웨이퍼당 그로스 '
             '다이 +40%는 공정 주장이지 출하 양품 기준이 아님"', ['OmegaFxNotes']),
            ('가상 PC 제조사 N 대표: "신규 DRAM 캐파가 계속 나온다, 이미 공급 부족은 없다" / '
             "후발 업체를 '가격 파괴자'로 지칭", ['fxstreetwire']),
        ]),
        ('첨단 패키징·광 인터커넥트 병목 이동', [
            ('가상 칩 설계사 L, 가상 파운드리 O의 2.5D 패키징 오버플로 물량을 노리고 브리지 계열 '
             '기술에 집중. 별도로 가상 디스플레이사와 글라스 기판 협의설', ['ChipWireFx']),
            ('가상 광부품사 P의 광회로 스위치 분기 매출이 첫 매출 인식(작년 8월) 1년 만에 1억 달러 '
             '돌파', ['LumenCapFx']),
            ('광 인터커넥트 전환 경로: Pluggable Optics → NPO → CPO', ['fxwafer_tw']),
            ('성숙 공정 웨이퍼도 다시 타이트 — AI 서버당 전력관리칩·MCU·센서·MOSFET 수요 급증, '
             '가상 파운드리 O 물량이 다른 파운드리로 넘치고 일부는 가격 인상 준비',
             ['ChipWireFx']),
        ]),
        ('가상 GPU사 대표 방송 인터뷰 (다수 계정 인용)', [
            ('"올해 들어 추론 수요가 학습 수요를 앞질렀다. 토큰 단가가 내려가며 사용량이 더 빨리 '
             '늘고 있다"', ['fxstreetwire']),
            ('규제론: "새 법보다 기존 사이버보안·제조물책임·계약법을 먼저 적용하는 편이 빠르다"',
             ['The_Fx_Investor']),
            ('수출: 차세대 플랫폼은 양산 단계이며 경쟁사보다 "한 세대 앞서" 있다는 주장. 판매 제한 '
             '확대 주장에는 반대', ['fxequitylab']),
            ('데이터센터 입지 이슈: "지역사회와 먼저 이야기했어야 했다"', ['fxstreetwire']),
            ('향후 5년 설비 투자 약 60억 달러 계획 — "공급망 병목은 전력이지 칩이 아니다"',
             ['The_Fx_Investor']),
        ]),
    ],
    standalone=[
        ('한미반도체(042700), 합성 시나리오 속 가상 고객사向 패키징 장비 수주', 'LumenCapFx',
         '하이브리드 본딩 투자 맵에서 구조적 포지션과 본딩 지연 민감도를 동시에 갖춘 유일 종목으로 '
         '꼽혔던 이름'),
        ('가상 낸드사 Q(가상 메모리사 A 자회사), 해외 첫 NAND 생산시설 설립 검토', 'LumenCapFx',
         '생산 거점 다변화 신호'),
        ('가상 NPU사 R의 추론 칩, 가상 통신사 4개 AI 서비스 핵심 작업 담당 — 통화 요약·음성, '
         'AI 컨택센터 등 일 1,200만 건 처리', 'ChipWireFx', None),
        ('가상 파운드리 O 2nm급 라인 가동. 이전 세대 대비 동일 전력에서 속도 +10~15%, 동일 속도에서 '
         '전력 -20~25%, 밀도 +15% 이상. 나노시트 GAA로 식각·증착·계측 스텝 증가 → 장비 수요 확대',
         'ChipWireFx', None),
        ('가상 기기 제조사 S, 자체 칩 2~4개를 얹은 엔터프라이즈급 AI 서버 개발설', 'ChipWireFx',
         None),
        ('가상 소재사 T × 가상 전자그룹 U, 해외 7,500만 달러 소재 공장 MoU. 포토레지스트·CMP·'
         '고순도 화학을 300mm 팹 옆에 직결', 'fxwafer_tw', None),
        ('가상 보험사 V, AI 데이터센터 건설 등에 1.5조 엔 투입 계획. 생보업계 AI 인프라 투자 확대',
         'Trader_Fxie_', None),
        ('가상 리서치사 W: 가상 GPU사 2027년 점유율 약 75%에서 안정 또는 소폭 상승 전망. 주요 고객 '
         '컴퓨트 플릿의 70% 안팎도 GPU 유지', 'fxequitylab', None),
        ('가상 증권사 X, 가상 광통신칩사 Y 비중확대·목표가 $210. 신형 서버 램프가 기대보다 크게 '
         '강하고 2H26~2027까지 이어진다는 채널 체크', 'ChipWireFx', None),
        ('가상 증권사 Z: MLCC·ABF 기판은 사이클 초입, 메모리는 고점 근접', 'fxequitylab', None),
        ('가상 GPU사 차세대 제품, 하이엔드 MLCC 수요 견인 — 가상 부품사 2곳 우위 부각',
         'DrFxMem', None),
        ('가상 업종 통계: 전자·통신업 월평균 총임금 912.6만원, 전년 상반기 대비 +21.4%. 고정급 '
         '+1.2%인 반면 특별급여 +61.8%', 'ChipWireFx', None),
    ],
    views=[
        "메모리는 '가격 지표 혼조 vs 물량·계약 강세' 국면. HBM 수출단가 -2.9%는 첫 균열이지만 3Q "
        'DRAM ASP +18~25%, NAND +12%가 함께 나온 만큼 단가 하락은 수요 훼손보다 후발 공급 증가 쪽 '
        '해석이 우세. 단기 헤드라인 리스크는 있으나 LTA 확산이 실적 가시성을 높이는 구조.',
        '수혜 — 메모리 Capex 사이클: 2027년 $66.9B(+38.8%) 전망에 직결되는 가상 장비사 H·I·J. '
        '가상 메모리사 A 차세대 HBM 2배 증산과 캐리어 월 5만 장은 국내 후공정·소재 밸류체인'
        '(한미반도체 042700 포함)에 직접 수치로 연결됨.',
        '수혜 — CPU 리레이팅: CPU:GPU 비율이 1:1로 수렴한다는 프레임이 확산되면 가상 칩 설계사 L의 '
        '데이터센터 TAM 가정이 상향. 메모리 제휴·CPU 타이트·공정 가속 3개 촉매가 겹친 상태.',
        '수혜 — 광/패키징 병목: 가상 광부품사 P 분기 1억 달러 돌파, CPO 전환, 2.5D 패키징 '
        '오버플로 → 광부품·브리지 패키징 장비군.',
        '피해·주의 — 컨슈머 실리콘: 성숙 공정 웨이퍼 타이트와 가격 인상은 스마트폰·가전 칩 원가 '
        '압박. 가상 후발 메모리사 M 양산과 "이미 공급 부족 없다" 발언은 레거시 DRAM 가격에 하방 요인.',
        ('밸류에이션 참고 — 가상 메모리사 C 컨센서스는 매출 $10.1B·EPS $2.80에서 약 $45.4B·'
         'EPS $28.76(+349%/+927%)로 이미 슈퍼사이클을 반영 중. 기대치 자체가 높다는 점이 리스크.',
         None),
    ],
    focus=['(수집 구간 내 @fxkan07 게시물은 1건뿐이었습니다)',
           '• 가상 증권사 E 리포트 인용 — 메모리 메이커·현물 유통·OEM 채널 체크 결과 3Q ASP는 견조. '
           'LTA 비중이 올라가는데도 대부분 DRAM 제품 ASP가 QoQ 18~25% 상승했다는 점을 핵심으로 '
           '지목. 이 리포트는 이후 여러 계정이 재인용하며 하루 동안 메모리 가격 논쟁의 기준점 '
           '역할을 함.'],
)


def _pid(n):
    return f'at://did:plc:fixture{n:04d}/app.bsky.feed.post/3l4x{n:04d}'


def _hhmm(base_h, base_m, add_min):
    t = base_h * 60 + base_m + add_min
    day = 20 + t // (24 * 60)
    t %= 24 * 60
    return f'2026-09-{day:02d}T{t // 60:02d}:{t % 60:02d}:00+09:00'


def _strip_code(text):
    for name, code in NAMES.items():
        text = text.replace(f'{name}({code})', name).replace(f'{name} {code}', name)
    return text


def xdigest_posts_facts():
    """게시물 156건과 facts.json. 게시물 번호·시각 규칙은 원본 픽스처와 같다.

    인용된 게시물: 소주제 사실은 문서 순서대로 1번부터(18:07 부터 7분 간격),
    단독 소식은 그 뒤 번호(문서 첫 줄이 가장 최근 — 9/21 08:31 부터 11분씩 이르게).
    나머지는 채움 게시물(MAIN 계정, 9/20 19:03 부터 3분 간격) — 2행 '수집 156건' 을
    코드가 세게 하려고 있다. 120~125·130~133 번은 별도 구획 시험이 구획 게시물로 바꿔 쓴다.
    """
    posts, n = [], 0

    def post(acct, at):
        nonlocal n
        n += 1
        posts.append(dict(id=_pid(n), account=bsky(acct), display_name=acct,
                          text=f'[픽스처] {acct} 게시물 {n}',
                          url=f'https://bsky.app/profile/{bsky(acct)}/post/3l4x{n:04d}',
                          posted_at=at, source='bluesky', query='semiconductor', langs=['en'],
                          in_window=True))
        return _pid(n)

    themes = []
    for title, facts in EXAMPLE['themes']:
        kept = []
        for text, accts in facts:
            if accts is None:
                continue                                    # 검증 탈락(허용 차이 4)
            ids = [post(a, _hhmm(18, 7, 7 * n)) for a in accts]
            kept.append(dict(text=_strip_code(text), post_ids=ids))
        themes.append(dict(title=title, facts=kept))
    alone = []
    for k, (text, acct, _cut) in enumerate(EXAMPLE['standalone']):
        pid = post(acct, _hhmm(8 + 24, 31, -11 * k))
        alone.append(dict(text=_strip_code(text), post_ids=[pid]))
    first_filler = n + 1
    while n < N_POSTS:
        post(MAIN, _hhmm(19, 3, 3 * (n + 1 - first_filler)))
    views = []
    for v in EXAMPLE['views']:
        if isinstance(v, tuple):
            continue                                        # 검증 탈락(허용 차이 4)
        views.append(dict(text=_strip_code(v), fact_ids=[f'v{len(views) + 1}']))

    posts_doc = dict(
        asof=ASOF,
        window=dict(start='2026-09-20T08:45:00+09:00', end='2026-09-21T08:45:00+09:00'),
        collected_at='2026-09-21T08:48:00+09:00',
        sources=dict(bluesky=dict(queries=7, calls=9, got=363, in_window=N_POSTS, kept=N_POSTS,
                                  blocked=0, cut=None, errors=[])),
        coverage=dict(first='2026-09-20T18:00:00+09:00', last='2026-09-21T08:48:00+09:00',
                      gaps=[dict(scope='bluesky', why='X 리스트 타임라인 페이지네이션이 14시간 '
                                                     '지점에서 멈춰 그 이전 구간은 미수집')]),
        posts=posts)
    facts_doc = dict(asof=ASOF, counts=dict(analyzed=ANALYZED), missing=[],
                     themes=themes[::-1], standalone=alone[::-1], views=views)
    return posts_doc, facts_doc


def example_block():
    """XDIGEST.md 9장의 예시 본문(코드 블록 안). 예시는 렌더 전의 모양이다 — 1행 시각
    09:00, 인사이트 기호 `•`, X 식 계정 표기, 종목코드, 검증 탈락 줄, 넷째 구획."""
    L = list(EXAMPLE['head'])
    L.append('■ 공통 테마')
    for title, facts in EXAMPLE['themes']:
        L.append(f'▸ {title}')
        seen = []
        for text, accts in facts:
            L.append(f'• {text}')
            for a in accts or []:
                if a not in seen:
                    seen.append(a)
        L.append('└ ' + ' '.join('@' + a for a in seen))
    L.append('■ 주목할 단독 소식')
    for text, acct, cut in EXAMPLE['standalone']:
        body = text if not cut else f'{text}. {cut}'
        L.append(f'• {body} (@{acct})')
    L.append('■ 투자 인사이트')
    for v in EXAMPLE['views']:
        L.append('• ' + (v[0] if isinstance(v, tuple) else v))
    L.append(f'■ {FOCUS[0]}의 24시간 인사이트')
    L += EXAMPLE['focus']
    return '\n'.join(L)


def _test_defs():
    """test_xdigest_render.py 의 NAMES·SEC_POSTS·SEC_FACTS·_pid·_sec_fixture 를 그대로 쓴다.

    모듈을 import 하면 픽스처 파일을 읽으므로(아직 없을 수 있다) 그 정의만 떼어 실행한다.
    """
    with open(TEST_RENDER, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    want = {'NAMES', 'SEC_POSTS', 'SEC_FACTS', '_pid', '_sec_fixture'}
    nodes = [nd for nd in tree.body
             if (isinstance(nd, ast.FunctionDef) and nd.name in want)
             or (isinstance(nd, ast.Assign) and any(getattr(t, 'id', None) in want
                                                    for t in nd.targets))]
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), TEST_RENDER, 'exec'), ns)  # noqa: S102
    return ns


def xdigest_outputs():
    posts, facts = xdigest_posts_facts()
    ns = _test_defs()
    if ns['NAMES'] != NAMES:
        raise SystemExit(f'test_xdigest_render.NAMES 가 바뀌었다: {ns["NAMES"]}')
    expected = XR.compose(facts, posts, {}, names=NAMES)
    ns.update(FACTS=facts, POSTS=posts)
    sf, sp = ns['_sec_fixture']()
    sections = XR.compose(sf, sp, {}, names=NAMES)
    return posts, facts, expected, sections


def xdigest_state(posts, facts, expected):
    """합성 state/xdigest — 그날 발송본의 근거 파일 모양(posts·facts·digest·sent)."""
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))
    parts = XR.split(expected)
    _ok, sent = XS.send_parts(parts, ASOF, cfg={}, sender=lambda text, **kw: (True, '1건 발송'),
                              chat_id='-100', now=datetime(2026, 9, 21, 7, 58, tzinfo=kst))
    for r in sent['results']:
        r['at'] = '2026-09-21T07:58:30+09:00'          # 벽시계 값을 남기지 않는다
    day = ASOF.replace('-', '')
    return {f'xdigest_state/{day}/posts.json': _json(posts, 1),
            f'xdigest_state/{day}/facts.json': _json(facts, 1),
            f'xdigest_state/{day}/digest.txt': expected + '\n',
            f'xdigest_state/{day}/sent.json': _json(sent, 1)}


# ═══════════════════════════ 3. 일일 노트 (test_note) ═══════════════════════════
NOTE_HEADER = ('# 합성 일일 노트 — board/tests/synthetic_fixtures.py 가 만든다. 사람이 쓴 노트가 아니다.\n'
               '# 모양은 docs/NOTE.md 1장 계약과 같다. 종목명 셋(선도전기·KBI메탈·소프트캠프)은\n'
               '# test_note 가 이름으로 찾는 것이고, 붙은 문장은 전부 합성이다.\n')


def note():
    import datetime as dt
    return dict(
        date=dt.date(2026, 9, 21),
        verdict='합성 판단 — 금리 이벤트 소화 뒤 인프라 업종 위주의 상승',
        index=['코스피 가상지수 +2.31% — 전기전자 주도(합성)',
               '코스닥 — 소부장·전력 인프라 강세(합성)'],
        macro=[dict(what='가상 중앙은행 정책금리 25bp 인상(합성)', note='반대 2표, 추가 경로 언급 없음',
                    view=['합성 판단: 긴축 의지는 남겼지만 연속 인상 예고는 아님']),
               dict(what='가상 국채 10년물 금리 하락(합성)',
                    view=['합성 판단: 성장주 할인율 부담이 줄어든 하루'])],
        themes=[
            dict(name='전선', driver=['가상 데이터센터 운영사, 비상발전·송배전 설비 대량 발주(합성)'],
                 names=['선도전기', '가상전선01', '가상전선02', '가상전선03', 'KBI메탈'],
                 view=['합성 판단: 영향이 서버를 넘어 발전·송배전 인프라까지 번지는 흐름',
                       '합성 판단: 단기 과열 구간']),
            dict(name='원전', driver=['가상 원전 수주 기대 보도(합성)'],
                 names=['가상원전01', '가상원전02', '가상원전03', '가상원전04'],
                 view=['합성 판단: 설계에서 기자재·정비 단으로 확산되는지 지켜볼 구간']),
            dict(name='반도체 소부장', driver=['가상 메모리사 증설 계획 보도(합성)'],
                 names=['가상장비01', '가상장비02', '가상소재01'],
                 view=['합성 판단: 증설 수혜 순서를 확인']),
            dict(name='보안', driver=['가상 보안 행사에서 AI 접속보안 신제품 공개(합성)'],
                 names=['가상보안01', '가상보안02', '소프트캠프'],
                 view=['합성 판단: 행사 일정까지는 기대가 남는 흐름']),
            dict(name='조선', driver=['가상 선사 대형 발주(합성)'],
                 names=['가상조선01', '가상조선02'],
                 view=['합성 판단: 수주 공시가 확인되기 전까지는 기대 구간이고 비중을 늘릴 근거는 아님']),
            dict(name='기타',
                 names=['가상기타01', '가상기타02', '가상기타03', '가상기타04'],
                 view=['합성 판단: 개별 재료 위주라 테마로 묶기 어려움',
                       '합성 판단: 다음 날 거래대금 유지 여부 확인']),
        ],
        picks=dict(
            w52=[dict(name='가상신고01', why='합성 사유 — 장비사 증설 사이클 수혜',
                      view='합성 판단: 공시 종목보다 이런 종목을 더 볼 필요'),
                 dict(name='가상신고02', why='합성 사유 — 수주 잔고 증가'),
                 dict(name='가상신고03', why='합성 사유 — 신제품 출시')],
            watch=[dict(name='가상관심01', why='합성 사유 — 검사장비로 확장'),
                   dict(name='가상관심02', why='합성 사유 — 고객사 다변화'),
                   dict(name='가상관심03', why='합성 사유 — 자사주 소각'),
                   dict(name='가상관심04', why='합성 사유 — 실적 턴어라운드'),
                   dict(name='가상관심05', why='합성 사유 — 신규 수주')],
            surge=[dict(name='소프트캠프', why='합성 사유 — 가상 행사에서 신제품 공개',
                        view='합성 판단: 행사 기간 뒤 고점 가능성'),
                   dict(name='가상급등01', why='합성 사유 — 테마 편입'),
                   dict(name='가상급등02', why='합성 사유 — 거래량 급증')]),
    )


def note_text():
    import yaml
    return NOTE_HEADER + yaml.safe_dump(note(), allow_unicode=True, sort_keys=False, width=200)


# ═══════════════════════════ 조립 ═══════════════════════════
def build():
    """{fixtures 기준 상대경로: 내용} — 디스크에 쓰기 전의 전부."""
    posts, facts, expected, sections = xdigest_outputs()
    out = {
        'rankings_sample.json': _json(rankings_sample(), 2),
        'rankings_tg_sample.json': _json(rankings_edge(), 2),
        'xdigest_render_posts.json': _json(posts, 1),
        'xdigest_render_facts.json': _json(facts, 1),
        'xdigest_render_expected.txt': expected,
        'xdigest_render_sections_expected.txt': sections,
        'note_2026-09-21.yaml': note_text(),
    }
    out.update(xdigest_state(posts, facts, expected))
    return out


def _doc_replace(text, block):
    head, sep, rest = text.partition('## 9. 예시')
    if not sep:
        raise SystemExit('XDIGEST.md 에 9장 예시가 없다')
    pre, fence, after = rest.partition('```')
    _old, fence2, tail = after.partition('```')
    if not (fence and fence2):
        raise SystemExit('XDIGEST.md 9장 예시 코드 블록을 찾지 못했다')
    return head + sep + pre + '```\n' + block + '\n```' + tail


def measure(expected, sections):
    """1장 '4,096자 분할' 의 측정값 — 문서 숫자를 손으로 맞추지 않으려고 출력한다."""
    ex = example_block()
    secs, cur = {}, None
    for line in ex.split('\n'):
        if line.startswith('■ '):
            cur = line
            secs[cur] = len(line)
        elif cur:
            secs[cur] += 1 + len(line)
    return dict(example_chars=len(ex), head=len('\n'.join(EXAMPLE['head'])), sections=secs,
                parts=[len(p) for p in XR.split(expected)],
                section_parts=[len(p) for p in XR.split(sections)])


def main(argv=None):
    ap = argparse.ArgumentParser(description='KBJ P1 합성 픽스처 생성')
    ap.add_argument('--check', action='store_true', help='디스크와 같은지만 본다(다르면 종료 코드 1)')
    ap.add_argument('--doc', action='store_true', help='docs/XDIGEST.md 9장 예시 블록도 다시 쓴다')
    ap.add_argument('--measure', action='store_true', help='예시·분할 길이를 출력한다')
    a = ap.parse_args(argv)

    files = build()
    with open(DOC, encoding='utf-8') as f:
        doc_now = f.read()
    doc_new = _doc_replace(doc_now, example_block())
    if a.measure:
        print(json.dumps(measure(files['xdigest_render_expected.txt'],
                                 files['xdigest_render_sections_expected.txt']),
                         ensure_ascii=False, indent=1))
    if a.check:
        bad = []
        for rel, body in sorted(files.items()):
            p = os.path.join(FIX, rel)
            try:
                with open(p, encoding='utf-8') as f:
                    same = f.read() == body
            except OSError:
                same = False
            if not same:
                bad.append(rel)
        if doc_new != doc_now:
            bad.append('docs/XDIGEST.md (9장 예시)')
        for rel in bad:
            print(f'다름: {rel}')
        print('같다' if not bad else f'{len(bad)}개 다름')
        return 1 if bad else 0
    for rel, body in sorted(files.items()):
        p = os.path.join(FIX, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(body)
        print(f'썼다: tests/fixtures/{rel}')
    if a.doc and doc_new != doc_now:
        with open(DOC, 'w', encoding='utf-8') as f:
            f.write(doc_new)
        print('썼다: docs/XDIGEST.md 9장 예시')
    return 0


if __name__ == '__main__':
    sys.exit(main())

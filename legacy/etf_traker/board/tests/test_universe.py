#!/usr/bin/env python3
"""
유니버스 위생 — 2026-08-27 첫 실데이터 실행에서 터진 세 가지를 고정한다.

  1. ETF·ETN 이 섞여 역사적 신고가 49종목 중 44개가 채권형 ETF 였다
  2. 목록 API 가 거래대금을 안 줘서 전 종목이 유동성 하한에 걸렸다
  3. 시가총액 단위 추정이 시총 100조 넘는 종목만 골라 망가뜨렸다
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine import build as B                # noqa: E402
from board.ingest import funds as F                # noqa: E402

# 2026-08-27 실행의 역사적 신고가 표에 실제로 올라온 이름들
REAL_FUNDS = [
    'KODEX CD금리액티브(합성)', 'TIGER KOFR금리액티브(합성)', 'SOL 머니마켓액티브',
    'ACE 5월만기자동연장회사채AA-이상액티브', 'RISE 금융채액티브',
    '메리츠 KAP 인버스 2X 일본 엔화 ETN', 'N2 KIS CD금리투자 ETN',
    'KB KIS CD금리투자 ETN', '1Q 머니마켓액티브', 'KIWOOM 통안채1년',
    'PLUS 국공채머니마켓액티브', 'HANARO 머니마켓액티브', 'WON 국공채머니마켓액티브',
    '마이티 26-09 특수채(AAA)액티브', '미래에셋-0.5X S&P500 VIX S/T선물 ETN(H)B',
]
REAL_STOCKS = [
    'GS', 'GS우', '자이에스앤디', '탑코미디어', '한미글로벌', 'DB손해보험',
    '상신이디피', '넥슨게임즈', 'GS건설', '삼성전자', 'SK하이닉스', '현대차',
    '메리츠금융지주', '미래에셋증권', '삼성화재', '한화솔루션', '두산에너빌리티',
    '신한지주', 'LG이노텍', '한국항공우주',
]


class TestFundFilter(unittest.TestCase):
    def test_real_funds_are_caught(self):
        missed = [n for n in REAL_FUNDS if not F.looks_like_fund(n)]
        self.assertEqual(missed, [], f'못 잡은 펀드: {missed}')

    def test_real_stocks_survive(self):
        wrong = [n for n in REAL_STOCKS if F.looks_like_fund(n)]
        self.assertEqual(wrong, [], f'주식을 펀드로 오인: {wrong}')

    def test_brand_prefix_needs_a_space(self):
        """브랜드 접두사에 공백을 요구하지 않으면 실제 주식이 빠진다."""
        for n in ['BNK금융지주', '파워로직스', '파워넷', 'WONIK', 'TRUE인베스트']:
            self.assertFalse(F.looks_like_fund(n), n)
        for n in ['KODEX 200', 'WON 국공채머니마켓액티브', '1Q 머니마켓액티브']:
            self.assertTrue(F.looks_like_fund(n), n)

    def test_name_heuristic_is_skipped_when_etf_list_exists(self):
        """목록을 받았으면 이름 판정을 쓰지 않는다. ETN 만 이름으로 거른다."""
        uni = {'1': dict(name='BNK금융지주'), '2': dict(name='KODEX 200'),
               '3': dict(name='키움 CD금리투자 ETN')}
        stocks, dropped, how = F.split(uni, etf_codes={'2'})
        self.assertEqual([v['name'] for v in stocks.values()], ['BNK금융지주'])
        self.assertEqual(how['by_code'], 1)
        self.assertEqual(how['by_name'], ['키움 CD금리투자 ETN'])

    def test_dropped_names_are_listed_for_review(self):
        """이름으로 걸린 것은 오탐일 수 있으니 목록을 남긴다."""
        uni = {'1': dict(name='KODEX 200'), '2': dict(name='삼성전자')}
        _, _, how = F.split(uni, etf_codes=None)
        self.assertEqual(how['by_name'], ['KODEX 200'])

    def test_etf_code_list_wins_over_name(self):
        # 목록에 있으면 이름이 주식처럼 생겨도 뺀다
        uni = {'069500': dict(name='아무이름'), '005930': dict(name='삼성전자')}
        stocks, dropped, how = F.split(uni, etf_codes={'069500'})
        self.assertEqual(list(stocks), ['005930'])
        self.assertEqual(list(dropped), ['069500'])
        self.assertIn('ETF 목록 1', how['text'])

    def test_split_without_code_list_falls_back_to_name(self):
        uni = {'1': dict(name='KODEX 200'), '2': dict(name='삼성전자')}
        stocks, dropped, how = F.split(uni, etf_codes=None)
        self.assertEqual(list(stocks), ['2'])
        self.assertIn('ETF 목록 미확보', how['text'])

    def test_empty_name_is_not_a_fund(self):
        self.assertFalse(F.looks_like_fund(None))
        self.assertFalse(F.looks_like_fund(''))


class TestEtnGluedToKorean(unittest.TestCase):
    """ETN 이 한글에 붙어 있어도 잡는지.

    `\\bETN\\b` 는 한글을 낱말 문자로 보기 때문에 '단일종목ETN' 에서 경계를 못
    찾고 그냥 지나쳤다. 그 구멍으로 520101 '미래에셋 레버리지 SK하이닉스
    단일종목ETN' 이 주식 유니버스에 들어와, 가드가 잡은 계단을 DART 에
    물어보다 corp_code 를 못 찾아 배너에 며칠 남아 있었다(2026-09-03 실측).
    """

    GLUED = ('미래에셋 레버리지 SK하이닉스 단일종목ETN',
             '삼성 인버스 2X 코스닥150선물ETN(H)',
             '한투 삼성전자 목표전환형ETN')
    SPACED = ('KB BYD 밸류체인 ETN', 'KB S&P 레버리지 은 선물 ETN B')
    NOT_ETN = ('에이치엘비', 'BNK금융지주', '파워로직스', 'ETNA바이오', '한국ETNC')

    def test_glued_names_are_caught(self):
        for n in self.GLUED + self.SPACED:
            self.assertTrue(F.is_etn(n), n)

    def test_latin_words_containing_etn_are_not(self):
        # 'ETNA' 처럼 다른 낱말 속의 세 글자를 잡으면 실제 주식이 빠진다.
        for n in self.NOT_ETN:
            self.assertFalse(F.is_etn(n), n)

    def test_split_puts_glued_etn_in_funds(self):
        # ETF 목록을 받은 경로에서도 ETN 은 이름으로만 걸린다. 목록에 없으니
        # 이 판정이 마지막 방어선이다.
        uni = {'520101': dict(name='미래에셋 레버리지 SK하이닉스 단일종목ETN'),
               '005930': dict(name='삼성전자')}
        stocks, funds, how = F.split(uni, etf_codes={'069500'})
        self.assertIn('005930', stocks)
        self.assertIn('520101', funds)
        self.assertNotIn('520101', stocks)


class TestTurnoverFallback(unittest.TestCase):
    """목록 API 가 거래대금을 안 주면 일봉에서 채운다."""

    def setUp(self):
        self.rows = [dict(asof='2026-08-27', close=10000.0, volume=1_000_000.0)]

    def test_uses_snapshot_when_present(self):
        v, est = B._turnover(self.rows, 0, 123.4)
        self.assertEqual(v, 123.4)
        self.assertFalse(est)

    def test_falls_back_to_close_times_volume(self):
        v, est = B._turnover(self.rows, 0, None)
        self.assertEqual(v, 100.0)          # 10,000원 x 100만주 = 100억
        self.assertTrue(est)

    def test_zero_with_volume_means_source_omitted(self):
        # 처음에는 '0 = 거래 없음이라는 사실' 로 봤는데 실데이터가 그걸 뒤집었다.
        # 네이버 목록 API 는 거래대금을 키에서 빼는 게 아니라 **0 을 채워** 보낸다.
        # 이 행은 거래량이 100만주라 거래대금 0 이 물리적으로 불가능하다.
        v, est = B._turnover(self.rows, 0, 0.0)
        self.assertEqual(v, 100.0)          # 종가x거래량으로 채운다
        self.assertTrue(est)

    def test_zero_without_volume_is_a_real_zero(self):
        # 거래량도 0 이면 0 이 사실이다. 거래정지 종목을 추정으로 덮지 않는다.
        rows = [dict(asof='2026-08-27', close=10000.0, volume=0)]
        v, est = B._turnover(rows, 0, 0.0)
        self.assertEqual(v, 0.0)
        self.assertFalse(est)

    def test_none_when_bar_has_no_volume(self):
        rows = [dict(asof='2026-08-27', close=10000.0, volume=None)]
        v, est = B._turnover(rows, 0, None)
        self.assertIsNone(v)
        self.assertTrue(est)


class TestVolRatio(unittest.TestCase):
    def test_three_day_average_over_month(self):
        rows = [dict(volume=100.0) for _ in range(17)] + [dict(volume=400.0)] * 3
        # 3일 평균 400, 20일 평균 (17*100+3*400)/20 = 145
        self.assertAlmostEqual(B._vol_ratio(rows, 19), round(400 / 145 * 100, 1))

    def test_single_spike_is_diluted(self):
        """하루만 터진 것과 사흘 연속의 차이 — 이 지표를 쓰는 이유다."""
        flat = [dict(volume=100.0) for _ in range(19)]
        one = B._vol_ratio(flat + [dict(volume=1000.0)], 19)
        three = B._vol_ratio([dict(volume=100.0)] * 17 + [dict(volume=1000.0)] * 3, 19)
        self.assertLess(one, three)


if __name__ == '__main__':
    unittest.main(verbosity=2)


class Coverage(unittest.TestCase):
    """전 종목 수가 조용히 줄면 빠진 종목은 보드에서 통째로 사라진다.

    fetch_universe 는 페이지를 훑다가 빈 페이지에서 멈춘다. 중간 한 페이지가
    일시적으로 비면 그 뒤가 다 빠지는데 **예외가 나지 않는다** — 보드는 종목이
    적은 채로 정상 생성된다. 2026-08-31 에 LIG넥스원이 그렇게 없었다(D-051).
    """

    def setUp(self):
        import shutil
        import tempfile

        from board.engine import db as DB
        self.d = tempfile.mkdtemp()
        self._rm = lambda: shutil.rmtree(self.d, ignore_errors=True)
        self.conn = DB.connect(os.path.join(self.d, 't.db'))

    def tearDown(self):
        self.conn.close()
        self._rm()

    def _snap(self, asof, n):
        self.conn.executemany(
            'INSERT OR REPLACE INTO snap(code,asof,name,market,close) '
            'VALUES(?,?,?,?,?)',
            [(f'{i:06d}', asof, f'종목{i}', 'KOSPI', 1000.0) for i in range(n)])
        self.conn.commit()

    def test_first_run_has_nothing_to_compare(self):
        from board.ingest import pipeline as P
        self.assertIsNone(P.coverage_drop(self.conn, '2026-08-31', 2700))

    def test_an_absolute_floor_catches_a_persistently_short_list(self):
        # 어제와의 비교만으로는 못 잡는다. 매일 똑같이 덜 받으면 변화가 없다.
        from board.ingest import pipeline as P
        self._snap('2026-08-28', 1500)
        got = P.coverage_drop(self.conn, '2026-08-31', 1500)
        self.assertIsNotNone(got)
        self.assertIn('덜 받았습니다', got)

    def test_the_floor_is_checked_before_the_comparison(self):
        # 어제가 없어도(첫 실행) 하한은 본다.
        from board.ingest import pipeline as P
        self.assertIsNotNone(P.coverage_drop(self.conn, '2026-08-31', 800))

    def test_a_normal_day_is_quiet(self):
        from board.ingest import pipeline as P
        self._snap('2026-08-28', 2700)
        self.assertIsNone(P.coverage_drop(self.conn, '2026-08-31', 2698))

    def test_a_big_drop_is_reported(self):
        # 절대 하한(2,000) 위에서 떨어뜨린다. 하한에 걸리면 다른 검사가 되고,
        # 이 시험이 보려는 것은 '어제보다 줄었나' 쪽이다.
        from board.ingest import pipeline as P
        self._snap('2026-08-28', 2700)
        got = P.coverage_drop(self.conn, '2026-08-31', 2400)
        self.assertIsNotNone(got)
        self.assertIn('2,700', got)
        self.assertIn('2,400', got)

    def test_growth_is_never_a_problem(self):
        from board.ingest import pipeline as P
        self._snap('2026-08-28', 2700)
        self.assertIsNone(P.coverage_drop(self.conn, '2026-08-31', 2900))

    def test_only_earlier_days_are_compared(self):
        # 같은 날 다시 돌리면 자기 자신과 비교해 늘 0% 가 된다.
        from board.ingest import pipeline as P
        self._snap('2026-08-31', 2700)
        self.assertIsNone(P.coverage_drop(self.conn, '2026-08-31', 2100))

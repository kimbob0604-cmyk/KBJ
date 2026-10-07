#!/usr/bin/env python3
"""
미국장 수집기 파서 검증 — 픽스처만 쓴다. 네트워크가 필요 없다.

수집기는 '받아서 정규화만' 하므로 시험할 것도 그것뿐이다.
못 받은 값을 0 으로 채우지 않는지가 핵심이다 — 0 은 '거래 없음' 이라는 사실이고
None 은 '못 받았다' 는 사실이라 섞이면 브레드스가 통째로 틀린다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine.config import load                      # noqa: E402
from board.us import sources as S                          # noqa: E402

CFG = load(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'config', 'us.yaml'))

SCREENER = {'data': {'rows': [
    {'symbol': 'AAPL', 'name': 'Apple Inc. Common Stock', 'lastsale': '$226.80',
     'netchange': '1.50', 'pctchange': '0.666%', 'volume': '41,000,000',
     'marketCap': '3450000000000', 'sector': 'Technology',
     'industry': 'Computer Manufacturing', 'country': 'United States'},
    {'symbol': 'SPY', 'name': 'SPDR S&P 500 ETF Trust', 'lastsale': '$500.00',
     'pctchange': '0.10%', 'volume': '1000', 'marketCap': '',
     'sector': '', 'industry': ''},
    {'symbol': 'ZZZ', 'name': 'Zed Inc. Common Stock', 'lastsale': '',
     'pctchange': '', 'volume': '', 'marketCap': '', 'sector': '', 'industry': ''},
    {'symbol': 'LOSS', 'name': 'Loss Corp Common Stock', 'lastsale': '$10.00',
     'pctchange': '-4.50%', 'netchange': '-0.47', 'volume': '2,000,000',
     'marketCap': '900000000', 'sector': 'Finance', 'industry': 'Major Banks'},
]}}


class TestNum(unittest.TestCase):
    def test_strips_symbols(self):
        self.assertEqual(S._num('$226.80'), 226.8)
        self.assertEqual(S._num('1,234,567'), 1234567.0)
        self.assertEqual(S._num('0.666%'), 0.666)
        self.assertEqual(S._num('-4.50%'), -4.5)

    def test_missing_is_none_not_zero(self):
        for v in ('', None, '--', 'N/A'):
            self.assertIsNone(S._num(v), v)

    def test_parenthesis_is_negative(self):
        self.assertEqual(S._num('(1.25)'), -1.25)


class TestScreener(unittest.TestCase):
    def test_parses_and_drops_funds(self):
        rows, drop = S.parse_screener(SCREENER, CFG)
        tickers = [r['ticker'] for r in rows]
        self.assertIn('AAPL', tickers)
        self.assertNotIn('SPY', tickers)          # ETF 는 기업이 아니다
        self.assertNotIn('ZZZ', tickers)          # 가격이 없으면 담지 않는다
        self.assertEqual(drop['fund'], 1)
        self.assertEqual(drop['no_price'], 1)

    def test_turnover_is_marked_estimate(self):
        rows, _ = S.parse_screener(SCREENER, CFG)
        a = next(r for r in rows if r['ticker'] == 'AAPL')
        self.assertEqual(a['turnover'], 226.8 * 41_000_000)
        self.assertTrue(a['turnover_is_estimate'])

    def test_negative_change_kept(self):
        rows, _ = S.parse_screener(SCREENER, CFG)
        r = next(r for r in rows if r['ticker'] == 'LOSS')
        self.assertEqual(r['chg_pct'], -4.5)

    def test_empty_payload_is_empty_not_crash(self):
        rows, _ = S.parse_screener({}, CFG)
        self.assertEqual(rows, [])


class TestNasdaqHistory(unittest.TestCase):
    """러너에서 stooq 가 빈 응답, yahoo 가 429 라 이쪽이 주 경로가 됐다 (D-077)."""
    PAYLOAD = {'data': {'tradesTable': {'rows': [
        {'date': '09/12/2025', 'close': '$229.35', 'volume': '55,000,000',
         'open': '$228.00', 'high': '$230.00', 'low': '$227.00'},
        {'date': '09/11/2025', 'close': '$227.00', 'volume': '--',
         'open': '$226.00', 'high': '$228.00', 'low': '$225.00'},
    ]}}}

    def test_parse_and_sort(self):
        rows = S.parse_nasdaq_history(self.PAYLOAD)
        self.assertEqual([r['asof'] for r in rows], ['2025-09-11', '2025-09-12'])
        self.assertEqual(rows[-1]['close'], 229.35)
        self.assertEqual(rows[-1]['volume'], 55_000_000.0)

    def test_missing_volume_is_none(self):
        rows = S.parse_nasdaq_history(self.PAYLOAD)
        self.assertIsNone(rows[0]['volume'])       # '--' 는 0 이 아니다

    def test_bad_date_dropped(self):
        p = {'data': {'tradesTable': {'rows': [{'date': 'N/A', 'close': '$1'}]}}}
        self.assertEqual(S.parse_nasdaq_history(p), [])

    def test_empty_payload(self):
        self.assertEqual(S.parse_nasdaq_history({}), [])

    def test_symbol_forms(self):
        """클래스주 표기가 소스마다 다르다. 한 곳에서 바꾼다.

        나스닥 쪽은 **인코딩까지** 해야 한다. 슬래시를 그대로 두면 URL 경로가
        갈라져 엉뚱한 주소가 된다 — run #2 에서 BRK/B 한 종목만 실패했다.
        """
        self.assertEqual(S.nasdaq_symbol('BRK.B'), 'BRK%2FB')
        self.assertEqual(S.nasdaq_symbol('BRK/B'), 'BRK%2FB')
        self.assertEqual(S.nasdaq_symbol('aapl'), 'AAPL')
        self.assertEqual(S.stooq_symbol('BRK.B'), 'brk-b.us')


class TestStooq(unittest.TestCase):
    CSV = ('Date,Open,High,Low,Close,Volume\n'
           '2026-09-11,10,11,9.5,10.5,1000\n'
           '2026-09-14,10.5,12,10.4,11.8,2200\n')

    def test_parse(self):
        rows = S.parse_stooq(self.CSV)
        self.assertEqual([r['asof'] for r in rows], ['2026-09-11', '2026-09-14'])
        self.assertEqual(rows[-1]['close'], 11.8)
        self.assertEqual(rows[-1]['source'], 'stooq')

    def test_sorted_ascending(self):
        rows = S.parse_stooq('Date,Open,High,Low,Close,Volume\n'
                             '2026-09-14,1,1,1,1,1\n2026-09-11,1,1,1,1,1\n')
        self.assertEqual([r['asof'] for r in rows], ['2026-09-11', '2026-09-14'])

    def test_garbage_lines_dropped(self):
        self.assertEqual(S.parse_stooq('No data\n'), [])
        self.assertEqual(S.parse_stooq(''), [])


class TestYahoo(unittest.TestCase):
    PAYLOAD = {'chart': {'result': [{
        'timestamp': [1757548800, 1757808000],
        'indicators': {'quote': [{'open': [1, 2], 'high': [2, 3], 'low': [0.5, 1.5],
                                  'close': [1.5, 2.5], 'volume': [100, 200]}]}}]}}

    def test_parse(self):
        rows = S.parse_yahoo(self.PAYLOAD)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]['close'], 2.5)

    def test_null_close_rows_dropped(self):
        p = {'chart': {'result': [{'timestamp': [1757548800],
                                   'indicators': {'quote': [{'close': [None]}]}}]}}
        self.assertEqual(S.parse_yahoo(p), [])

    def test_empty_result(self):
        self.assertEqual(S.parse_yahoo({'chart': {'result': []}}), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestSymbolFallback(unittest.TestCase):
    """클래스주는 표기가 소스마다 다르다. 추측해서 하나를 고르지 않는다.

    인코딩만으로는 안 됐다 — run #4 에서 BRK/A·BRK/B 가 HTTP 404 였다.
    순서대로 물어보고 되는 표기를 쓴다.
    """
    def test_class_share_has_several_forms(self):
        forms = S.nasdaq_symbol_forms('BRK/B')
        self.assertEqual(forms[0], 'BRK%2FB')
        self.assertIn('BRK.B', forms)
        self.assertIn('BRK-B', forms)

    def test_plain_ticker_has_exactly_one_form(self):
        self.assertEqual(S.nasdaq_symbol_forms('AAPL'), ['AAPL'])

    def test_forms_are_deduped_and_ordered(self):
        forms = S.nasdaq_symbol_forms('BRK.B')
        self.assertEqual(len(forms), len(set(forms)))
        self.assertEqual(forms[0], 'BRK.B')

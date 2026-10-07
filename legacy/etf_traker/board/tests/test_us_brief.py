#!/usr/bin/env python3
"""
미국장 브리프 검증 — 표기 규칙과 '지어내지 않음'.

브리프는 계산하지 않는다. 그러므로 시험할 것은 두 가지다.
  1. 없는 값을 지어내지 않는가 (None → '–', 빈 절은 그렇게 말하는가)
  2. board.json 의 수와 브리프에 적힌 수가 같은가
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine.config import load                       # noqa: E402
from board.us import brief as BR                            # noqa: E402
from board.us import build as BUILD                         # noqa: E402
from board.us import demo                                   # noqa: E402

CFG = load(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'config', 'us.yaml'))


class TestFormat(unittest.TestCase):
    def test_minus_sign_is_unicode(self):
        self.assertTrue(BR.pct(-7.2).startswith(BR.MINUS))
        self.assertTrue(BR.pct(7.2).startswith('+'))

    def test_zero_is_flat(self):
        self.assertEqual(BR.pct(0.0), '보합')
        self.assertEqual(BR.pct(0.04), '보합')          # 한 자리로는 0.0%

    def test_none_is_dash_not_zero(self):
        self.assertEqual(BR.pct(None), '–')
        self.assertEqual(BR.money(None), '–')
        self.assertEqual(BR.mult(None), '')

    def test_money_units(self):
        self.assertEqual(BR.money(6.0e9), '$6B')
        self.assertEqual(BR.money(6.04e9), '$6B')
        self.assertEqual(BR.money(6.3e9), '$6.3B')
        self.assertEqual(BR.money(7.26e8), '$726M')
        self.assertEqual(BR.money(1e11), '$100B')

    def test_daylabel(self):
        self.assertEqual(BR.daylabel('2026-09-14'), '9/14(월)')


class TestEmptyBoard(unittest.TestCase):
    """값이 없는 날에도 절을 지어내지 않는다."""
    BOARD = dict(asof='2026-09-14', basis='close',
                 source=dict(universe='nasdaq', history='stooq'),
                 universe=dict(n=0, filters=dict(min_mktcap_usd=5e8,
                                                 min_turnover_usd=3e7,
                                                 min_price_usd=5.0)),
                 summary=dict(newhigh=0, newhigh_delta=None, w52_high=0, w52_low=0,
                              up=0, down=0, flat=0, unknown=0, up_ratio=None,
                              median=None, surge=0, plunge=0,
                              mega=dict(n=0, up_count=0, down_count=0, up=[], down=[]),
                              strong=[], weak=[]),
                 trend=[], sectors=[], tiers=[], mega=dict(n=0, up_count=0, up=[], down=[]),
                 leaders=[], streaks=[], fresh52=[],
                 movers=dict(up=[], down=[]), my=dict(label='내 섹터', tickers=[]),
                 missing=['유니버스가 비었다'])

    def test_renders_without_values(self):
        txt = BR.render(self.BOARD, [], CFG)
        self.assertIn('추이 없음', txt)
        self.assertIn('52주 신고가 없음', txt)
        self.assertIn('신고가 없음', txt)
        self.assertIn('유니버스가 비었다', txt)

    def test_unknown_ratio_is_dash(self):
        txt = BR.render(self.BOARD, [], CFG)
        self.assertIn('상승 비율 –', txt)
        self.assertIn('판정 불가', txt)


class TestAgreesWithBoard(unittest.TestCase):
    def setUp(self):
        series, snaps, asof = demo.make(n_tickers=80, seed=42)
        self.rows, self.board = BUILD.build(series, snaps, asof, CFG,
                                            log=lambda *a: None)
        self.txt = BR.render(self.board, self.rows, CFG)

    def test_headline_count_matches(self):
        s = self.board['summary']
        self.assertIn(f'신고가 {s["newhigh"]}종목', self.txt)
        self.assertIn(f'52주 신고가 {s["w52_high"]} vs 신저가 {s["w52_low"]}', self.txt)

    def test_list_section_sums_to_headline(self):
        block = self.txt.split('📋')[1]
        total = 0
        for line in block.splitlines():
            if ':' in line and line.split(':')[0].split(' ')[-1].isdigit():
                total += int(line.split(':')[0].split(' ')[-1])
        self.assertEqual(total, self.board['summary']['newhigh'])

    def test_no_naked_hyphen_minus(self):
        """음수 부호는 유니코드 마이너스 하나로 통일한다."""
        for line in self.txt.splitlines():
            self.assertNotIn(' -', line.replace(' --', ''))

    def test_universe_line_reports_floors(self):
        self.assertIn('시총 $500M↑', self.txt)
        self.assertIn('거래대금 $30M↑', self.txt)


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestTelegramHtml(unittest.TestCase):
    """발송본은 HTML 이다. 태그가 안 닫히면 텔레그램이 메시지를 통째로 거절한다."""

    def setUp(self):
        series, snaps, asof = demo.make(n_tickers=80, seed=9)
        self.rows, self.board = BUILD.build(series, snaps, asof, CFG,
                                            log=lambda *a: None)

    def test_escapes_markup_in_names(self):
        rows = list(self.rows)
        rows[0] = dict(rows[0], ticker='<b>X</b>', name='A & B', label='d60',
                       sector='소프트웨어·인터넷')
        html = BR.telegram_html(self.board, rows, CFG)
        self.assertNotIn('<b>X</b>', html.replace('<b>①', ''))
        self.assertIn('&lt;b&gt;X&lt;/b&gt;', html)

    def test_tags_balanced(self):
        html = BR.telegram_html(self.board, self.rows, CFG)
        for tag in ('b', 'i', 'pre'):
            self.assertEqual(html.count(f'<{tag}>'), html.count(f'</{tag}>'), tag)

    def test_trend_table_columns_align(self):
        """한글 라벨은 두 칸을 차지한다. 그걸 안 세면 고정폭에서도 어긋난다."""
        block = BR.trend_block(self.board)
        widths = {BR._w(line) for line in block.split('\n')}
        self.assertEqual(len(widths), 1, f'행마다 너비가 다르다: {widths}')

    def test_chunks_keep_tags_whole(self):
        # 📋 목록이 상한을 넘도록 종목을 부풀린다 — 절 안에서 갈리는 경로를 태운다.
        rows = []
        for i in range(1200):
            rows.append(dict(ticker=f'TCK{i:04d}', label='d60', turnover=1e8,
                             sector=f'섹터{i % 7}', chg_pct=1.0))
        chunks = BR.telegram_chunks(self.board, rows, CFG)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), BR.TG_LIMIT + 40)   # 조각 표시 여유
            for tag in ('b', 'pre'):
                self.assertEqual(c.count(f'<{tag}>'), c.count(f'</{tag}>'), c[:80])

    def test_chunk_index_only_when_split(self):
        one = BR.telegram_chunks(self.board, self.rows, CFG)
        if len(one) == 1:
            self.assertNotIn('(1/', one[0])

    def test_same_numbers_as_plain_text(self):
        """서식만 다르고 값은 같아야 한다. 발송본이 따로 계산하면 안 된다."""
        html = BR.telegram_html(self.board, self.rows, CFG)
        s = self.board['summary']
        self.assertIn(f'신고가 {s["newhigh"]:,}종목', html)
        self.assertIn(f'52주 신고가 {s["w52_high"]:,}', html)

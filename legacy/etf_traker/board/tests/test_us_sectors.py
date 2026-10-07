#!/usr/bin/env python3
"""미국장 섹터 분류 검증 — 규칙의 우선순위와 폴백만 본다."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.us import sectors as SEC                       # noqa: E402


class TestClassify(unittest.TestCase):
    def test_industry_beats_sector(self):
        # 스크리너 sector 는 'Technology' 하나로 뭉뚱그린다. industry 가 이긴다.
        sec, ind, why = SEC.classify('NVDA', 'Technology', 'Semiconductors')
        self.assertEqual((sec, why), ('반도체', 'industry'))
        self.assertEqual(ind, '반도체')

    def test_override_beats_rules(self):
        # CRWD 의 industry 는 'Prepackaged Software' 라 규칙만으로는 보안이 안 갈린다.
        sec, ind, why = SEC.classify(
            'CRWD', 'Technology', 'Computer Software: Prepackaged Software')
        self.assertEqual(ind, 'Cybersecurity')
        self.assertEqual(sec, '소프트웨어·인터넷')

    def test_sector_fallback(self):
        sec, _, why = SEC.classify('XYZ', 'Health Care', '')
        self.assertEqual((sec, why), ('헬스케어', 'sector'))

    def test_unknown_stays_unmapped(self):
        """모르는 것은 '미분류' 다. 그럴듯한 섹터에 넣지 않는다."""
        sec, _, _ = SEC.classify('QQQQ', '', '')
        self.assertEqual(sec, SEC.UNMAPPED)

    def test_unknown_industry_label_is_passed_through(self):
        _, ind, _ = SEC.classify('AAA', 'Finance', 'Some New Industry')
        self.assertEqual(ind, 'Some New Industry')

    def test_apply_counts_unmapped(self):
        rows = [dict(ticker='NVDA', sector_raw='Technology', industry_raw='Semiconductors'),
                dict(ticker='???', sector_raw='', industry_raw='')]
        n = SEC.apply(rows)
        self.assertEqual(n, 1)
        self.assertEqual(rows[0]['sector'], '반도체')


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestNameFallback(unittest.TestCase):
    """소스가 섹터·산업을 비워 보낸 행은 이름으로만 풀 수 있다 (run #3).

    이름은 **약한 신호**라 순서가 마지막이고, 근거에 'name' 이 남아야 한다.
    """
    def test_name_used_when_source_is_blank(self):
        self.assertEqual(SEC.classify('ABCB', '', '', name='Ameris Bancorp Common Stock'),
                         ('금융', '', 'name'))
        self.assertEqual(SEC.classify('XYZ', '', '', name='Zeta Therapeutics Inc')[0],
                         '헬스케어')

    def test_industry_still_wins_over_name(self):
        sec, _, why = SEC.classify('NVDA', 'Technology', 'Semiconductors',
                                   name='NVIDIA Bancorp Common Stock')
        self.assertEqual((sec, why), ('반도체', 'industry'))

    def test_ambiguous_name_stays_unmapped(self):
        """'Technologies'·'Holdings' 는 업종을 안 알려 준다. 지어내지 않는다."""
        sec, _, why = SEC.classify('QQQX', '', '', name='Acme Technologies Holdings Inc')
        self.assertEqual((sec, why), (SEC.UNMAPPED, ''))

    def test_source_reported_per_row(self):
        rows = [dict(ticker='ABCB', sector_raw='', industry_raw='',
                     name='Ameris Bancorp Common Stock'),
                dict(ticker='NVDA', sector_raw='Technology',
                     industry_raw='Semiconductors', name='NVIDIA Corp')]
        SEC.apply(rows)
        self.assertEqual(SEC.by_source(rows), {'name': 1, 'industry': 1})

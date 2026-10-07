#!/usr/bin/env python3
"""히트맵 재무 툴팁 (D-075) — 누적→분기 차분, 연결/별도 폴백, 캐시 갱신, 렌더 부착.

DART 손익은 누적이다. Q2 = 반기 − 1Q, Q4 = 연간 − 3Q누적. 차분할 직전 보고서가
없으면 0 이 아니라 None 이어야 한다 (CLAUDE.md 2장 1번 — 계산 안 된 값은 내지 않는다).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.ingest import financials as F          # noqa: E402
from board.web import render as RD                # noqa: E402

E = F.EOK


def row(year, rc, fs, nm, cur, add=None, prev=None, prev2=None, sj='IS'):
    return dict(bsns_year=str(year), reprt_code=rc, fs_div=fs, sj_div=sj, account_nm=nm,
                thstrm_amount=f'{cur:,}', thstrm_add_amount=(f'{add:,}' if add is not None else ''),
                frmtrm_amount=(f'{prev:,}' if prev is not None else ''),
                bfefrmtrm_amount=(f'{prev2:,}' if prev2 is not None else ''))


def rows_for(fs='CFS'):
    """2025 사업보고서 + 2026 1Q·반기·3Q. 매출 100/220/360, 연간 2025=500(전기 450, 전전기 400)."""
    r = []
    r += [row(2025, '11011', fs, '매출액', 500 * E, prev=450 * E, prev2=400 * E),
          row(2025, '11011', fs, '영업이익', 50 * E, prev=45 * E, prev2=40 * E),
          row(2025, '11014', fs, '매출액', 130 * E, add=360 * E),
          row(2025, '11014', fs, '영업이익', 13 * E, add=36 * E)]
    r += [row(2026, '11013', fs, '매출액', 100 * E, add=100 * E),
          row(2026, '11013', fs, '영업이익', 10 * E, add=10 * E),
          row(2026, '11012', fs, '매출액', 120 * E, add=220 * E),
          row(2026, '11012', fs, '영업이익', 12 * E, add=22 * E),
          row(2026, '11014', fs, '매출액', 140 * E, add=360 * E),
          row(2026, '11014', fs, '영업이익', 14 * E, add=36 * E)]
    return r


class TestBuildCorp(unittest.TestCase):
    def test_quarterly_is_difference_of_cumulatives(self):
        b = F.build_corp(rows_for())
        q = {x['label']: x for x in b['quarterly']}
        self.assertAlmostEqual(q['1Q26']['rev'], 100)
        self.assertAlmostEqual(q['2Q26']['rev'], 120)     # 220 − 100
        self.assertAlmostEqual(q['3Q26']['rev'], 140)     # 360 − 220
        self.assertAlmostEqual(q['3Q26']['op'], 14)
        self.assertAlmostEqual(q['4Q25']['rev'], 140)     # 연간 500 − 3Q누적 360
        self.assertFalse(q['2Q26']['derived'])

    def test_missing_previous_report_uses_company_value_or_none(self):
        """1Q 보고서가 없어도 반기 보고서의 3개월값이 있으면 그것을 쓴다.
        3개월값도 없으면(누적만 있으면) 0 이 아니라 None 이고 derived 로 표시한다."""
        rs = [r for r in rows_for() if not (r['bsns_year'] == '2026' and r['reprt_code'] == '11013')]
        q = {x['label']: x for x in F.build_corp(rs)['quarterly']}
        self.assertAlmostEqual(q['2Q26']['rev'], 120)      # 회사가 적은 3개월값
        self.assertIsNone(q['2Q26']['rev_diff'])           # 차분은 불가
        self.assertFalse(q['2Q26']['derived'])
        # 3개월값이 누적과 같게 와서(=3개월값 없음) 차분도 안 되면 None
        for r in rs:
            if r['bsns_year'] == '2026' and r['reprt_code'] == '11012':
                r['thstrm_amount'] = r['thstrm_add_amount']
        q = {x['label']: x for x in F.build_corp(rs)['quarterly']}
        self.assertIsNone(q['2Q26']['rev'])
        self.assertTrue(q['2Q26']['derived'])
        self.assertAlmostEqual(q['3Q26']['rev'], 140)      # 3Q 는 3개월값이 있다

    def test_annual_three_years_from_one_business_report(self):
        b = F.build_corp(rows_for())
        a = {x['year']: x for x in b['annual']}
        self.assertEqual(sorted(a), [2023, 2024, 2025])
        self.assertAlmostEqual(a[2025]['rev'], 500)
        self.assertAlmostEqual(a[2023]['op'], 40)

    def test_cfs_preferred_ofs_fallback(self):
        both = rows_for('CFS') + [row(2026, '11013', 'OFS', '매출액', 1 * E, add=1 * E)]
        self.assertEqual(F.build_corp(both)['fs_div'], 'CFS')
        self.assertAlmostEqual({x['label']: x for x in F.build_corp(both)['quarterly']}['1Q26']['rev'], 100)
        only_ofs = rows_for('OFS')
        self.assertEqual(F.build_corp(only_ofs)['fs_div'], 'OFS')

    def test_synonym_account_names(self):
        rs = [row(2026, '11013', 'CFS', '수익(매출액)', 77 * E, add=77 * E),
              row(2026, '11013', 'CFS', '영업이익(손실)', -7 * E, add=-7 * E)]
        q = F.build_corp(rs)['quarterly'][0]
        self.assertAlmostEqual(q['rev'], 77)
        self.assertAlmostEqual(q['op'], -7)

    def test_derived_quarter_matches_dart_three_month_value(self):
        """반기 보고서의 3개월값(thstrm_amount)과 차분(반기누적 − 1Q)이 같아야 한다."""
        b = F.build_corp(rows_for())
        c = F.quarters_vs_dart(b)                 # 2Q26: 220−100=120 vs 3개월 120 · 3Q26: 140 vs 140
        self.assertGreaterEqual(c['n'], 2)
        self.assertAlmostEqual(c['max_diff_pct'], 0.0)

    def test_check_catches_wrong_difference_and_prefers_company_value(self):
        rs = rows_for()
        for r in rs:                              # 반기 보고서의 3개월 영업이익이 차분과 다르게
            if r['bsns_year'] == '2026' and r['reprt_code'] == '11012' and r['account_nm'] == '영업이익':
                r['thstrm_amount'] = f'{9 * E:,}'
        b = F.build_corp(rs)
        c = F.quarters_vs_dart(b)
        self.assertEqual(c['worst'], '2Q26')
        self.assertGreater(abs(c['max_diff_pct']), 1.0)
        q = {x['label']: x for x in b['quarterly']}
        self.assertAlmostEqual(q['2Q26']['op'], 9)          # 화면값은 회사가 적은 3개월값
        self.assertAlmostEqual(q['2Q26']['op_diff'], 12)    # 차분은 대조용으로만 남는다
        self.assertAlmostEqual(q['4Q25']['op'], 14)         # 사업보고서엔 3개월값이 없어 차분

    def test_check_skips_first_quarter_and_missing(self):
        rs = [r for r in rows_for() if r['reprt_code'] == '11013']   # 1Q 만
        self.assertEqual(F.quarters_vs_dart(F.build_corp(rs))['n'], 0)

    def test_insurer_revenue_synonym(self):
        rs = [row(2026, '11013', 'CFS', '보험수익', 55 * E, add=55 * E)]
        self.assertAlmostEqual(F.build_corp(rs)['quarterly'][0]['rev'], 55)

    def test_empty_rows(self):
        b = F.build_corp([])
        self.assertEqual(b['annual'], [])
        self.assertEqual(b['quarterly'], [])


class TestRefreshAndRender(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'financials.json')
        self.cfg = {'financials': dict(annual_years=3, quarters=4, stale_days=7,
                                       batch_size=100, pause_sec=0, fs_div='CFS')}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        RD.FIN_PATH = None

    def test_refresh_only_fetches_stale_or_missing(self):
        fresh = date.today().isoformat()
        old = (date.today() - timedelta(days=30)).isoformat()
        F.save({'by_code': {'A': {'as_of': fresh, 'annual': [], 'quarterly': []},
                            'B': {'as_of': old, 'annual': [], 'quarterly': []}}}, self.path)
        asked = []

        def fake_collect(codes, cfg, asof=None, log=print):
            asked.extend(codes)
            return {c: {'code': c, 'as_of': fresh, 'annual': [], 'quarterly': [], 'source': 'dart'}
                    for c in codes}
        orig = F.collect
        F.collect = fake_collect
        try:
            n = F.refresh(['A', 'B', 'C'], self.cfg, log=lambda *a: None, path=self.path)
        finally:
            F.collect = orig
        self.assertEqual(sorted(asked), ['B', 'C'])       # A 는 최신이라 안 받는다
        self.assertEqual(n, 2)
        self.assertEqual(sorted(F.load(self.path)['by_code']), ['A', 'B', 'C'])

    def test_render_attaches_fin_to_heatmap_cells(self):
        F.save({'source': 'dart', 'by_code': {'000001': dict(
            code='000001', fs_div='CFS', as_of='2026-09-07', source='dart',
            annual=[dict(year=2025, rev=500.0, op=50.0, ni=40.0)],
            quarterly=[dict(label='1Q26', year=2026, q='1Q', rev=100.0, op=10.0, ni=8.0, derived=False)])}},
            self.path)
        RD.FIN_PATH = self.path
        hm = {'theme': [dict(group='g', key='g', cells=[dict(code='000001', name='x'),
                                                        dict(code='000002', name='y')])],
              'sector': []}
        RD._attach_financials(hm)
        c1, c2 = hm['theme'][0]['cells']
        self.assertEqual(c1['fin']['a'], [[2025, 500.0, 50.0, 40.0]])
        self.assertEqual(c1['fin']['q'], [['1Q26', 100.0, 10.0, False]])
        self.assertEqual(c1['fin']['fs'], 'CFS')
        self.assertNotIn('fin', c2)                       # 없는 종목은 만들어 넣지 않는다


if __name__ == '__main__':
    unittest.main()

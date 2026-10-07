#!/usr/bin/env python3
"""랭킹 계산 단위 시험 — 정렬·집계·교집합이 맞는지."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine import rankings as R          # noqa: E402
from board.engine.config import load            # noqa: E402
from board.web import render as RD             # noqa: E402

CFG = load()


def stock(code, name, sector, **kw):
    d = dict(code=code, name=name, sector=sector, mktcap=1000.0, turnover=50.0,
             chg_pct=0.0, ret_5d=0.0, ret_7d=0.0, ret_10d=0.0, ret_21d=0.0,
             vol_3d_1m=100.0)
    d.update(kw)
    return d


# 아래 시험들은 구성종목 하한이 아니라 가중·브레드스·정렬을 본다.
# 작은 표본으로 짜여 있으므로 하한을 명시적으로 끄고 부른다.
NO_MIN = {'rankings': {'min_sector_members': 1}}


class TestSectorBoard(unittest.TestCase):
    def setUp(self):
        self.rows = [
            stock('001', '가', '반도체', chg_pct=10.0, mktcap=1000.0),
            stock('002', '나', '반도체', chg_pct=-2.0, mktcap=9000.0),
            stock('003', '다', '보험', chg_pct=5.0, mktcap=1000.0),
            stock('004', '라', '보험', chg_pct=3.0, mktcap=1000.0),
        ]
        self.spec = R.SECTOR_PERIODS[0]

    def test_mktcap_weighted_not_simple_average(self):
        # 반도체: 단순평균이면 +4.0%, 시총가중이면 (10*1000 + -2*9000)/10000 = -0.8%
        b = R.sector_board(self.rows, self.spec, cfg=NO_MIN)
        semi = next(s for s in b['sectors'] if s['name'] == '반도체')
        self.assertAlmostEqual(semi['ret_raw'], -0.8, places=2)
        self.assertEqual(b['weighting'], 'mktcap')

    def test_rank_is_assigned_after_sort(self):
        b = R.sector_board(self.rows, self.spec, cfg=NO_MIN)
        self.assertEqual([s['rank'] for s in b['sectors']], [1, 2])
        self.assertEqual(b['sectors'][0]['name'], '보험')     # +4.0% > -0.8%

    def test_top_is_sorted_desc_and_capped(self):
        rows = [stock(f'{i:03d}', f'종목{i}', '반도체', chg_pct=float(i))
                for i in range(8)]
        b = R.sector_board(rows, self.spec, cfg=NO_MIN)
        top = b['sectors'][0]['top']
        self.assertEqual(len(top), 5)
        self.assertEqual([t['name'] for t in top],
                         ['종목7', '종목6', '종목5', '종목4', '종목3'])
        self.assertEqual([t['rank'] for t in top], [1, 2, 3, 4, 5])

    def test_short_sector_keeps_fewer_than_five(self):
        b = R.sector_board(self.rows, self.spec, cfg=NO_MIN)
        self.assertEqual(len(b['sectors'][0]['top']), 2)

    def test_breadth_counts(self):
        b = R.sector_board(self.rows, self.spec, cfg=NO_MIN)
        semi = next(s for s in b['sectors'] if s['name'] == '반도체')
        self.assertEqual(semi['breadth'],
                         dict(up=1, flat=0, down=1, total=2, unknown=0))

    def test_unknown_return_is_not_counted_as_flat(self):
        # 등락률을 모르는 종목이 보합으로 잡히면 브레드스가 중립으로 보인다
        rows = [stock('001', '가', '반도체', chg_pct=5.0),
                stock('002', '나', '반도체', chg_pct=None),
                stock('003', '다', '반도체', chg_pct=None)]
        b = R.sector_board(rows, self.spec, cfg=NO_MIN)['sectors'][0]
        self.assertEqual(b['breadth'],
                         dict(up=1, flat=0, down=0, total=1, unknown=2))

    def test_sector_with_no_value_sinks_to_bottom(self):
        rows = self.rows + [stock('009', '마', '미지', chg_pct=None)]
        b = R.sector_board(rows, self.spec, cfg=NO_MIN)
        self.assertEqual(b['sectors'][-1]['name'], '미지')
        self.assertIsNone(b['sectors'][-1]['ret_raw'])
        self.assertIsNone(b['sectors'][-1]['ret'])

    def test_equal_weight_fallback_is_reported(self):
        rows = [stock('001', '가', '반도체', chg_pct=10.0, mktcap=0),
                stock('002', '나', '반도체', chg_pct=0.0, mktcap=0)]
        b = R.sector_board(rows, self.spec, cfg=NO_MIN)
        self.assertEqual(b['weighting'], 'equal')
        self.assertAlmostEqual(b['sectors'][0]['ret_raw'], 5.0)


class TestStockBoard(unittest.TestCase):
    def test_sorted_desc_and_limited(self):
        rows = [stock(f'{i:03d}', f'종목{i}', '반도체', ret_5d=float(i))
                for i in range(40)]
        b = R.stock_board(rows, R.STOCK_BOARDS[0], limit=10)
        self.assertEqual(len(b['rows']), 10)
        self.assertEqual(b['rows'][0]['name'], '종목39')
        self.assertEqual(b['rows'][0]['rank'], 1)

    def test_null_cell_stays_null_not_zero(self):
        rows = [stock('001', '가', '반도체', ret_5d=5.0, ret_21d=None)]
        b = R.stock_board(rows, R.STOCK_BOARDS[0])
        r = b['rows'][0]
        self.assertIsNone(r['cells']['ret_21d'])
        self.assertIsNone(r['cells_raw']['ret_21d'])
        self.assertEqual(r['cells']['ret_5d'], '+5.00%')

    def test_rows_missing_sort_key_are_dropped(self):
        rows = [stock('001', '가', '반도체', vol_3d_1m=None),
                stock('002', '나', '반도체', vol_3d_1m=300.0)]
        b = R.stock_board(rows, R.STOCK_BOARDS[1])
        self.assertEqual([r['code'] for r in b['rows']], ['002'])

    def test_columns_carry_kind(self):
        b = R.stock_board([stock('001', '가', '반도체')], R.STOCK_BOARDS[0])
        kinds = {c['key']: c['kind'] for c in b['columns']}
        self.assertEqual(kinds['mktcap'], 'amount')
        self.assertEqual(kinds['chg_pct'], 'pct')
        self.assertEqual(kinds['vol_3d_1m'], 'ratio')


class TestCross(unittest.TestCase):
    def test_intersection_is_marked_on_both_boards(self):
        rows = [stock('001', '가', '반도체', ret_5d=9.0, vol_3d_1m=900.0),
                stock('002', '나', '반도체', ret_5d=8.0, vol_3d_1m=100.0),
                stock('003', '다', '반도체', ret_5d=-9.0, vol_3d_1m=800.0)]
        boards = [R.stock_board(rows, R.STOCK_BOARDS[0], limit=2),
                  R.stock_board(rows, R.STOCK_BOARDS[1], limit=2)]
        cross = R.mark_cross(boards)
        # 수익률 상위2 = 001,002 / 거래량 상위2 = 001,003 → 교집합 001
        self.assertEqual(cross, ['001'])
        for b in boards:
            for r in b['rows']:
                self.assertEqual(r['cross'], r['code'] == '001')

    def test_no_cross_when_disjoint(self):
        rows = [stock('001', '가', '반도체', ret_5d=9.0, vol_3d_1m=100.0),
                stock('002', '나', '반도체', ret_5d=-9.0, vol_3d_1m=900.0)]
        boards = [R.stock_board(rows, R.STOCK_BOARDS[0], limit=1),
                  R.stock_board(rows, R.STOCK_BOARDS[1], limit=1)]
        self.assertEqual(R.mark_cross(boards), [])


class TestFormat(unittest.TestCase):
    def test_none_stays_none(self):
        for kind in ('pct', 'ratio', 'amount', 'int'):
            self.assertIsNone(R._fmt(kind, None), kind)

    def test_pct_keeps_sign_and_two_decimals(self):
        self.assertEqual(R._fmt('pct', 7.921), '+7.92%')
        self.assertEqual(R._fmt('pct', -6.76), '-6.76%')
        self.assertEqual(R._fmt('pct', 0.0), '+0.00%')

    def test_ratio_has_no_sign(self):
        self.assertEqual(R._fmt('ratio', 162.4), '162%')

    def test_amount_folds_to_jo(self):
        self.assertEqual(R._fmt('amount', 1043.0), '1,043억')
        self.assertEqual(R._fmt('amount', 32000.0), '3.20조')


class TestTaxonomy(unittest.TestCase):
    # 48개에서 시작했지만 반도체를 다섯으로 나눠(D-061) 52개다. 수를 박아 두면
    # 분류 체계를 만질 때마다 이 시험이 따라와야 하므로, 고정하는 것은 '개수'가
    # 아니라 '중복 없음'과 '반도체 세분류가 실제로 있음' 이다.
    def test_sectors_no_duplicates(self):
        tax = R.load_taxonomy()
        names = [s['name'] for s in tax['sectors']]
        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len(names), 48)

    def test_semiconductor_is_subdivided(self):
        names = {s['name'] for s in R.load_taxonomy()['sectors']}
        self.assertNotIn('반도체', names, '뭉뚱그린 반도체가 되살아났다')
        for sub in ('반도체제조', '반도체장비', '반도체후공정',
                    '반도체소재부품', '반도체설계'):
            self.assertIn(sub, names)

    def test_krx_hint_narrows_candidates(self):
        tax = R.load_taxonomy()
        from board.classify.sectors import candidates
        cand = candidates('보험', tax)
        self.assertIn('보험', cand)
        self.assertNotIn('반도체장비', cand)

    def test_unknown_krx_sector_falls_back_to_all(self):
        tax = R.load_taxonomy()
        from board.classify.sectors import candidates
        self.assertEqual(len(candidates('없는업종', tax)),
                         len(tax['sectors']))


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestThinSectors(unittest.TestCase):
    """구성종목이 적은 섹터는 순위에서 뺀다.

    3종목짜리 섹터의 시총가중 등락률은 48종목짜리와 같은 표에 못 놓는다 —
    한 종목이 순위를 통째로 만든다. 2026-08-28 보드에서 '컴퓨터'(3종목)가
    금일 1위로 올라온 것이 그 형태였다.
    """
    spec = dict(key='d1', label='금일', ret_label='금일 상승률', field='chg_pct')

    def _rows(self, sector, n, ret=1.0):
        return [dict(code=f'{i:06d}', name=f'{sector}{i}', sector=sector,
                     chg_pct=ret, mktcap=1000.0) for i in range(n)]

    def test_하한_미만은_빠진다(self):
        rows = self._rows('큰섹터', 8, 1.0) + self._rows('작은섹터', 3, 99.0)
        b = R.sector_board(rows, self.spec)
        self.assertEqual([s['name'] for s in b['sectors']], ['큰섹터'])

    def test_뺀_섹터를_이름과_수로_알려준다(self):
        rows = self._rows('큰섹터', 8) + self._rows('작은섹터', 3, 99.0)
        b = R.sector_board(rows, self.spec)
        self.assertEqual(b['thin_sectors'], [dict(name='작은섹터', n=3)])

    def test_하한_이상은_남는다(self):
        rows = self._rows('큰섹터', 8) + self._rows('딱맞는섹터', 5, 99.0)
        b = R.sector_board(rows, self.spec)
        self.assertIn('딱맞는섹터', [s['name'] for s in b['sectors']])

    def test_미분류는_수가_적어도_남긴다(self):
        """미분류는 섹터가 아니라 버킷이다. 몇 종목인지 보여야 한다."""
        rows = self._rows('큰섹터', 8) + self._rows(R.UNMAPPED, 2)
        b = R.sector_board(rows, self.spec)
        self.assertIn(R.UNMAPPED, [s['name'] for s in b['sectors']])

    def test_설정으로_끌_수_있다(self):
        rows = self._rows('큰섹터', 8) + self._rows('작은섹터', 3, 99.0)
        b = R.sector_board(rows, self.spec, cfg=NO_MIN)
        self.assertEqual([s['name'] for s in b['sectors']], ['작은섹터', '큰섹터'])
        self.assertEqual(b['thin_sectors'], [])


class RankDelta(unittest.TestCase):
    """섹터 순위 옆의 전일 대비 변동.

    순위만 있으면 '오늘 3등' 까지만 안다. 어제 12등이었는지 2등이었는지에 따라
    같은 3등이 다른 이야기다. 다만 **어제 표에 없던 섹터를 0 으로 적으면 안 된다** —
    구성종목 하한 때문에 섹터가 표를 드나드는데 그걸 '변동 없음' 이라고 하면
    없는 사실을 단정하는 것이다 (CLAUDE.md 2장 1번).
    """

    def board(self, key, names):
        return dict(key=key, sectors=[dict(name=n, rank=i + 1)
                                      for i, n in enumerate(names)])

    def prev(self, key, names):
        return dict(sector_boards=[self.board(key, names)])

    def test_올라가면_양수다(self):
        b = self.board('1d', ['반도체', '보험'])
        R.mark_rank_delta([b], self.prev('1d', ['보험', '반도체']))
        by = {s['name']: s for s in b['sectors']}
        self.assertEqual(by['반도체']['rank_delta'], 1)   # 2위 → 1위
        self.assertEqual(by['반도체']['rank_prev'], 2)
        self.assertEqual(by['보험']['rank_delta'], -1)

    def test_같으면_0이다(self):
        b = self.board('1d', ['반도체'])
        R.mark_rank_delta([b], self.prev('1d', ['반도체']))
        self.assertEqual(b['sectors'][0]['rank_delta'], 0)

    def test_어제_없던_섹터는_None이다(self):
        b = self.board('1d', ['반도체', '조선'])
        R.mark_rank_delta([b], self.prev('1d', ['반도체']))
        by = {s['name']: s for s in b['sectors']}
        self.assertIsNone(by['조선']['rank_delta'], '없던 것을 0 으로 적으면 안 된다')
        self.assertIsNone(by['조선']['rank_prev'])

    def test_전일_파일이_없으면_대조하지_않았다고_적는다(self):
        b = self.board('1d', ['반도체'])
        n = R.mark_rank_delta([b], None)
        self.assertEqual(n, 0)
        self.assertFalse(b['has_prev_rank'])
        self.assertIsNone(b['sectors'][0]['rank_delta'])

    def test_표는_key로_짝지어진다(self):
        # 금일 표의 순위를 7거래일 표에 갖다 붙이면 안 된다.
        b = self.board('7d', ['반도체', '보험'])
        R.mark_rank_delta([b], self.prev('1d', ['보험', '반도체']))
        self.assertFalse(b['has_prev_rank'])
        self.assertIsNone(b['sectors'][0]['rank_delta'])


class RankDeltaRender(unittest.TestCase):
    def cell(self, x, has_prev=True):
        return RD._rank_delta(x, has_prev)

    def test_상승은_삼각형과_칸수(self):
        h = self.cell(dict(rank=1, rank_prev=4, rank_delta=3))
        self.assertIn('▲3', h)
        self.assertIn('up', h)
        self.assertIn('전일 4위', h)

    def test_하락은_반대_색이다(self):
        h = self.cell(dict(rank=5, rank_prev=2, rank_delta=-3))
        self.assertIn('▼3', h)
        self.assertIn('down', h)

    def test_어제_없던_섹터는_대시다(self):
        h = self.cell(dict(rank=5, rank_prev=None, rank_delta=None))
        self.assertIn('–', h)
        self.assertNotIn('▲', h)
        self.assertNotIn('▼', h)

    def test_전일이_통째로_없으면_칸이_빈다(self):
        self.assertEqual(self.cell(dict(rank=5, rank_delta=None), has_prev=False), '')

    def test_표에_실린다(self):
        b = dict(key='1d', label='금일', ret_label='금일 상승률', has_prev_rank=True,
                 sectors=[dict(name='반도체', n=30, rank=1, rank_prev=4, rank_delta=3,
                               ret='+1.20%', ret_raw=1.2,
                               breadth=dict(up=20, flat=2, down=8, total=30, unknown=0),
                               top=[])])
        self.assertIn('▲3', RD._sector_board(b))

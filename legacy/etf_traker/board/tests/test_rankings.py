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


class TestTaxonomy(unittest.TestCase):
    # 48개에서 시작했지만 반도체를 다섯으로 나눠(D-061) 52개다. 수를 박아 두면
    # 분류 체계를 만질 때마다 이 시험이 따라와야 하므로, 고정하는 것은 '개수'가
    # 아니라 '중복 없음'과 '반도체 세분류가 실제로 있음' 이다.
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

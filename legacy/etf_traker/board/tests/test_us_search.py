#!/usr/bin/env python3
"""미국장 조합 탐색 — 국장과 같은 봉인 규칙이 미국 DB·SPY 게이트로 도는지."""
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board import run as R                         # noqa: E402
from board.tests.test_signals import days          # noqa: E402
from board.us import btsearch as BS                 # noqa: E402
from board.us import db as UDB                      # noqa: E402

CFG = R.us_cfg()


class TestUSSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = UDB.connect(os.path.join(self.tmp.name, 'u.db'))
        self.ds = days(1100)
        random.seed(7)
        for t in ('AAA', 'BBB', 'CCC', 'SPY'):
            c, pc, rows = 50.0, 50.0, []
            for k, d in enumerate(self.ds):
                o = pc * (1 + random.gauss(0, 0.004))
                c = o * (1 + random.gauss(0.0004, 0.02))
                # 거래량이 빠진 봉이 섞여 온다 (나스닥 히스토리 실측)
                v = None if k % 97 == 5 else 2e6
                rows.append(dict(asof=d, open=o, high=max(o, c) * 1.008,
                                 low=min(o, c) * 0.992, close=c, volume=v, source='t'))
                pc = c
            UDB.put_px(self.conn, t, rows)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_grid(self):
        vs = BS.variants()
        self.assertEqual(len(vs), 44)
        self.assertEqual(len({v['id'] for v in vs}), 44)
        self.assertEqual(sum(v['gate'] for v in vs), 22)

    def test_missing_volume_keeps_bar(self):
        data = BS.load(self.conn, self.ds[0], self.ds[-1], skip={'SPY'})
        self.assertEqual(set(data), {'AAA', 'BBB', 'CCC'})
        self.assertEqual(len(data['AAA'].dates), len(self.ds))

    def test_holdout_sealed(self):
        p = BS.run(self.conn, CFG, log=lambda *a: None)
        self.assertEqual(p['holdout_start'], self.ds[-CFG['search']['holdout_days']])
        self.assertEqual(p['n_codes'], 3)          # 게이트 지수는 종목이 아니다
        for vid, r in p['results'].items():
            self.assertEqual('holdout_ok' in r, r['search_ok'])
        self.assertTrue(set(p['adopted']) <= set(p['candidates']))
        lines = BS.summary_lines(p, CFG)
        self.assertIn('SPY', lines[1])


if __name__ == '__main__':
    unittest.main()

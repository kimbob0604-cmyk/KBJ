#!/usr/bin/env python3
"""조합 탐색 — 봉인 규칙과 파라미터 전달이 정의대로인지."""
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine import db as DB                  # noqa: E402
from board.engine import search as SE              # noqa: E402
from board.engine import signals as S              # noqa: E402
from board.engine import systems as SY             # noqa: E402
from board.tests.test_backtest import series       # noqa: E402
from board.tests.test_signals import days          # noqa: E402

SC = S.load_cfg()


class TestVariants(unittest.TestCase):
    def test_grid_is_fixed_and_unique(self):
        vs = SE.variants()
        self.assertEqual(len(vs), 36)
        self.assertEqual(len({v['id'] for v in vs}), 36)
        self.assertEqual(sum(v['gate'] for v in vs), 18)

    def test_params_reach_signal(self):
        cl = [100 + 0.5 * i for i in range(220)] + [205, 203, 201]
        s = series([(c, c * 1.01, c * 0.99, c) for c in cl])
        ind = SY.Ind(s)
        r = ind.rsi2[222]
        self.assertTrue(2 < r < 10)
        self.assertIsNone(SY.signal('connors_rsi2', s, ind, 222, dict(rsi_th=2)))
        self.assertIsNotNone(SY.signal('connors_rsi2', s, ind, 222, dict(rsi_th=10)))

    def test_trend_filter(self):
        cl = [200 - i for i in range(100)]
        s = series([(c, c * 1.01, c * 0.99, c) for c in cl])
        ind = SY.Ind(s)
        self.assertIsNotNone(SY.signal('larry_07', s, ind, 90))
        self.assertIsNone(SY.signal('larry_07', s, ind, 90, dict(trend_ma=20)))

    def test_rsi_exit(self):
        cl = [100 + 0.5 * i for i in range(220)] + [205, 200, 196, 199, 204, 210, 215]
        s = series([(c, c * 1.01, c * 0.99, c) for c in cl])
        ind = SY.Ind(s)
        sig = SY.signal('connors_rsi2', s, ind, 222)
        t, _ = SY.trade('connors_rsi2', s, ind, 222, sig, SC['backtest'], dict(exit='rsi'))
        self.assertEqual(t['reason'], 'rule')
        self.assertGreater(ind.rsi2[222 + t['bars'] - 1], 70)


class TestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = DB.connect(os.path.join(self.tmp.name, 't.db'))
        self.ds = days(1100)
        random.seed(11)
        for k in range(6):
            c, pc, rows = 10000.0, 10000.0, []
            for d in self.ds:
                o = pc * (1 + random.gauss(0, 0.004))
                c = o * (1 + random.gauss(0.0004, 0.02))
                rows.append((f'00000{k}', d, o, max(o, c) * 1.008, min(o, c) * 0.992, c, 1e6, 't'))
                pc = c
            self.conn.executemany('INSERT INTO px VALUES(?,?,?,?,?,?,?,?)', rows)
            self.conn.execute('INSERT INTO snap(code,asof,name,market) VALUES(?,?,?,?)',
                              (f'00000{k}', self.ds[-1], '가', 'KOSPI'))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_holdout_is_sealed(self):
        p = SE.run(self.conn, SC, log=lambda *a: None)
        self.assertEqual(p['holdout_start'], self.ds[-SC['search']['holdout_days']])
        self.assertLess(p['search_end'], p['holdout_start'])
        for vid, r in p['results'].items():
            # 봉인 구간 판정은 1단계 통과 조합에만 붙는다 — 봉인 성적으로 후보를 고르지 않는다
            self.assertEqual('holdout_ok' in r, r['search_ok'])
            self.assertEqual(vid in p['adopted'], bool(r.get('holdout_ok')))
        self.assertTrue(set(p['adopted']) <= set(p['candidates']))
        lines = SE.summary_lines(p, SC)
        self.assertIn('봉인', lines[0])

    def test_too_short_is_refused(self):
        sc = dict(SC, search=dict(SC['search'], years=1.2))
        with self.assertRaises(RuntimeError):
            SE.run(self.conn, sc, log=lambda *a: None)


if __name__ == '__main__':
    unittest.main()

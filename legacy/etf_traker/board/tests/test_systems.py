#!/usr/bin/env python3
"""매매 시스템 점검 — 지표·신호·청산·통과 판정이 정의대로인지."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine import db as DB                  # noqa: E402
from board.engine import signals as S              # noqa: E402
from board.engine import systems as SY             # noqa: E402
from board.tests.test_backtest import series       # noqa: E402
from board.tests.test_signals import days          # noqa: E402

SC = S.load_cfg()
BTC = SC['backtest']


class TestIndicators(unittest.TestCase):
    def test_sma_ema(self):
        self.assertEqual(SY.sma_arr([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        e = SY.ema_arr([1.0, 1.0, 1.0, 4.0], 3)
        self.assertEqual(e[2], 1.0)
        self.assertAlmostEqual(e[3], 2.5)

    def test_rsi2_extremes(self):
        up = SY.rsi_arr([1, 2, 3, 4, 5], 2)
        self.assertEqual(up[4], 100.0)
        dn = SY.rsi_arr([5, 4, 3, 2, 1], 2)
        self.assertEqual(dn[4], 0.0)

    def test_adx_trend_is_high(self):
        n = 80
        h = [100 + i * 2 + 1 for i in range(n)]
        l = [100 + i * 2 - 1 for i in range(n)]
        c = [100 + i * 2 for i in range(n)]
        a = SY.adx_arr(h, l, c, 14)
        self.assertGreater(a[-1], 50)
        self.assertIsNone(a[10])


def bars_from(closes, spread=0.01):
    return [(c, c * (1 + spread), c * (1 - spread), c) for c in closes]


class TestSignalsAndTrades(unittest.TestCase):
    def test_connors_buys_close_and_exits_above_sma5(self):
        cl = [100 + 0.5 * i for i in range(220)] + [205, 200, 196] + [199, 204, 210]
        s = series(bars_from(cl))
        ind = SY.Ind(s)
        i = 222
        self.assertLess(ind.rsi2[i], 5)
        sig = SY.signal('connors_rsi2', s, ind, i)
        self.assertEqual(sig['mode'], 'close')
        t, why = SY.trade('connors_rsi2', s, ind, i, sig, BTC)
        self.assertIsNone(why)
        self.assertEqual(t['entry_date'], s.dates[i])     # 판정과 체결이 같은 종가 (#14)
        self.assertEqual(t['reason'], 'rule')
        self.assertGreater(t['ret_pct'], 0)

    def test_connors_needs_200ma(self):
        cl = [300 - 0.5 * i for i in range(220)] + [180, 175, 170]
        s = series(bars_from(cl))
        ind = SY.Ind(s)
        self.assertIsNone(SY.signal('connors_rsi2', s, ind, 222))

    def test_larry_first_profitable_open(self):
        s = series([(100, 105, 95, 100), (100, 110, 99, 109), (111, 112, 110, 111)])
        ind = SY.Ind(s)
        sig = SY.signal('larry_07', s, ind, 0)            # 전일 변동폭 10
        t, why = SY.trade('larry_07', s, ind, 0, sig, BTC)
        self.assertIsNone(why)
        self.assertEqual(t['reason'], 'rule')             # 이튿날 시가 111 > 매수 107
        self.assertEqual(t['exit_date'], s.dates[2])

    def test_entry_day_low_is_not_a_stop_unless_close_breaks(self):
        # 진입일 저가 99 는 체결(107) 전일 수 있다 — 종가 109 가 손절 102 위면 살아 있다
        s = series([(100, 105, 95, 100), (100, 110, 99, 109), (111, 112, 110, 111)])
        ind = SY.Ind(s)
        t, _ = SY.trade('larry_07', s, ind, 0, SY.signal('larry_07', s, ind, 0), BTC)
        self.assertEqual(t['reason'], 'rule')
        s = series([(100, 105, 95, 100), (100, 108, 99, 101), (103, 104, 102, 103)])
        t, _ = SY.trade('larry_07', s, SY.Ind(s), 0, SY.signal('larry_07', s, SY.Ind(s), 0), BTC)
        self.assertEqual(t['reason'], 'stop')          # 종가 101 ≤ 손절 102

    def test_larry_no_fill(self):
        s = series([(100, 105, 95, 100), (100, 104, 99, 101), (101, 102, 100, 101)])
        ind = SY.Ind(s)
        sig = SY.signal('larry_07', s, ind, 0)
        self.assertEqual(SY.trade('larry_07', s, ind, 0, sig, BTC), (None, 'no_fill'))

    def test_cooper_pattern(self):
        up = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(60)]
        down = [(158, 159, 157, 158), (157, 158, 156, 157), (156, 157, 155, 156)]
        s = series(up + down + [(156, 160, 155, 159)] * 3)
        ind = SY.Ind(s)
        sig = SY.signal('cooper', s, ind, 62)
        self.assertEqual(sig['mode'], 'stop')
        self.assertAlmostEqual(sig['level'], 157 * 1.001)
        t, _ = SY.trade('cooper', s, ind, 62, sig, BTC)
        self.assertEqual(t['entry_date'], s.dates[63])

    def test_every_system_survives_short_history(self):
        # 봉 150개짜리 종목 — 136봉째부터 띠 폭 창의 앞쪽이 비어 있다
        cl = [100 + (i % 7) for i in range(150)]
        cl[140] = 130                  # 상단 띠 돌파 — 띠 폭 창을 읽는 줄까지 간다
        s = series(bars_from(cl))
        ind = SY.Ind(s)
        for key in SY.SYSTEMS:
            for i in range(1, 150):
                SY.signal(key, s, ind, i)

    def test_moon_hold_30(self):
        cl = [200 - i for i in range(60)] + [100] * 5 + [110] * 40
        s = series(bars_from(cl))
        ind = SY.Ind(s)
        i = next((k for k in range(60, 66) if SY.signal('moon_disparity', s, ind, k)), None)
        self.assertIsNotNone(i)
        t, _ = SY.trade('moon_disparity', s, ind, i, SY.signal('moon_disparity', s, ind, i), BTC)
        self.assertEqual(t['bars'], 30)


class TestVerdict(unittest.TestCase):
    CRIT = dict(min_trades=200, min_win_rate=55, min_profit_factor=1.2)

    def r(self, n, wr, mean, pf):
        st = dict(n=n, win_rate=wr, mean=mean, profit_factor=pf)
        return dict(all=st, first=dict(st, n=n // 2), second=dict(st, n=n // 2),
                    gate_open=st, gate_open_first=st, gate_open_second=st)

    def test_pass(self):
        ok, why = SY.verdict(self.r(400, 60, 0.8, 1.5), self.CRIT)
        self.assertTrue(ok, why)

    def test_high_win_rate_but_negative_mean_fails(self):
        ok, why = SY.verdict(self.r(400, 70, -0.2, 0.9), self.CRIT)
        self.assertFalse(ok)
        self.assertTrue(any('평균' in w for w in why))

    def test_one_half_fails(self):
        r = self.r(400, 60, 0.8, 1.5)
        r['second'] = dict(n=200, win_rate=50, mean=0.1, profit_factor=1.1)
        ok, why = SY.verdict(r, self.CRIT)
        self.assertFalse(ok)
        self.assertTrue(any('뒤 절반' in w for w in why))


class TestRun(unittest.TestCase):
    def test_run_small_db(self):
        tmp = tempfile.TemporaryDirectory()
        conn = DB.connect(os.path.join(tmp.name, 't.db'))
        ds = days(500)
        import random
        random.seed(7)
        for k in range(5):
            c = 10000.0
            rows = []
            for d in ds:
                c *= 1 + random.gauss(0.0005, 0.02)
                rows.append((f'00000{k}', d, c, c * 1.01, c * 0.99, c, 1e6, 't'))
            conn.executemany('INSERT INTO px VALUES(?,?,?,?,?,?,?,?)', rows)
            conn.execute('INSERT INTO snap(code,asof,name,market) VALUES(?,?,?,?)',
                         (f'00000{k}', ds[-1], '가', 'KOSPI'))
        conn.commit()
        sc = dict(SC, backtest=dict(BTC, years=0.7))
        p = SY.run(conn, sc, log=lambda *a: None)
        conn.close()
        tmp.cleanup()
        self.assertEqual(set(p['results']), set(SY.SYSTEMS))
        self.assertGreater(sum(r['all'].get('n', 0) for r in p['results'].values()), 0)
        for r in p['results'].values():
            if r['all'].get('n'):
                self.assertEqual(r['all']['n'], r['first'].get('n', 0) + r['second'].get('n', 0))
        lines = SY.summary_lines(p, SC['screen'])
        self.assertIn('원작 트레이더의 성과가 아니다', lines[-1])


if __name__ == '__main__':
    unittest.main()

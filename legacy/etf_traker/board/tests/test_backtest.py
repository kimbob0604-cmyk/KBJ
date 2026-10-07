#!/usr/bin/env python3
"""
백테스트 검증 — 시점 규칙과 청산 규칙이 정의대로 도는지 합성 일봉으로 본다.

성과를 보는 시험이 아니다. d일 판정 → d+1 체결, 손절·목표·시간 청산,
상한가 갭 제외, 감시 미체결이 적힌 대로 되는지만 본다.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine import backtest as BT            # noqa: E402
from board.engine import db as DB                  # noqa: E402
from board.engine import signals as S              # noqa: E402
from board.engine.config import load               # noqa: E402
from board.tests.test_signals import days, uptrend_then   # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = load(os.path.join(HERE, 'config', 'settings.yaml'))
SC = S.load_cfg()


def series(bars):
    """[(o,h,l,c)] → Series. 날짜는 영업일."""
    s = BT.Series()
    for d, (o, h, l, c) in zip(days(len(bars)), bars):
        s.add(dict(asof=d, open=o, high=h, low=l, close=c, volume=1e6))
    return s


def flat(n, px=100.0):
    return [(px, px * 1.005, px * 0.995, px)] * n


class TestSimulate(unittest.TestCase):
    def test_entry_is_next_open_not_signal_close(self):
        s = series(flat(3) + [(105, 106, 104, 105)] + flat(20, 105))
        t, why = BT.simulate(s, 2, 'breakout', 100.0, SC)
        self.assertIsNone(why)
        self.assertEqual(t['entry_date'], s.dates[3])
        self.assertAlmostEqual(t['entry'], 105 * (1 + SC['backtest']['slippage_pct'] / 100),
                               places=1)

    def test_stop_loss(self):
        s = series(flat(3) + [(100, 101, 99, 100), (100, 100, 90, 91)] + flat(5, 91))
        t, _ = BT.simulate(s, 2, 'breakout', 100.0, SC)
        self.assertEqual(t['reason'], 'stop')
        # 손절 −8% 에 비용·슬리피지를 더한 만큼 잃는다 — −8% 보다 조금 더 나쁘다
        self.assertLess(t['ret_pct'], -8.0)
        self.assertGreater(t['ret_pct'], -9.0)

    def test_gap_down_through_stop_fills_at_open(self):
        s = series(flat(3) + [(100, 101, 99, 100), (80, 81, 79, 80)] + flat(5, 80))
        t, _ = BT.simulate(s, 2, 'breakout', 100.0, SC)
        self.assertLess(t['ret_pct'], -19.0)          # 시가 80 에 나간다 — 92 가 아니다

    def test_target_then_trail(self):
        up = [(100 + 3 * k, 101 + 3 * k, 99 + 3 * k, 100 + 3 * k) for k in range(15)]
        down = [(140 - 4 * k, 141 - 4 * k, 139 - 4 * k, 140 - 4 * k) for k in range(8)]
        s = series(flat(25) + up + down + flat(5, 110))
        t, _ = BT.simulate(s, 24, 'breakout', 100.0, SC)
        self.assertTrue(t['hit_target'])
        self.assertIn(t['reason'], ('target_then_trail', 'target_then_be'))
        self.assertGreater(t['ret_pct'], 0)

    def test_time_stop(self):
        s = series(flat(3) + flat(40, 101))
        t, _ = BT.simulate(s, 2, 'breakout', 100.0, SC)
        self.assertEqual(t['reason'], 'time')
        self.assertEqual(t['bars'], SC['backtest']['time_stop_bars'])

    def test_limit_up_gap_is_not_tradeable(self):
        s = series(flat(3) + [(130, 130, 130, 130)] + flat(5, 130))
        t, why = BT.simulate(s, 2, 'breakout', 100.0, SC)
        self.assertIsNone(t)
        self.assertEqual(why, 'limit_gap')

    def test_watch_needs_trigger_touch(self):
        s = series(flat(3) + [(98, 99, 97, 98)] + flat(5, 98))
        t, why = BT.simulate(s, 2, 'watch', 100.0, SC)
        self.assertIsNone(t)
        self.assertEqual(why, 'no_fill')
        s = series(flat(3) + [(98, 101, 97, 100.5)] + flat(20, 100.5))
        t, _ = BT.simulate(s, 2, 'watch', 100.0, SC)
        self.assertAlmostEqual(t['entry'], 100 * (1 + SC['backtest']['slippage_pct'] / 100),
                               places=4)

    def test_zero_price_bar_is_not_a_bar(self):
        # 거래정지일에 소스가 0 을 주는 봉 — 첫 실데이터 실행이 0 으로 나눠 죽었다
        s = BT.Series()
        s.add(dict(asof='2025-01-02', open=0.0, high=0.0, low=0.0, close=100.0, volume=0))
        s.add(dict(asof='2025-01-03', open=100.0, high=101.0, low=99.0, close=100.0, volume=1))
        self.assertEqual(s.dates, ['2025-01-03'])

    def test_no_next_bar(self):
        s = series(flat(3))
        self.assertEqual(BT.simulate(s, 2, 'breakout', 100.0, SC), (None, 'no_next_bar'))


class TestStats(unittest.TestCase):
    def test_stats(self):
        ts = [dict(ret_pct=r, bars=5, reason='stop', hit_target=False) for r in (-8, -8, 20, 4)]
        st = BT.stats(ts)
        self.assertEqual(st['n'], 4)
        self.assertEqual(st['win_rate'], 50.0)
        self.assertEqual(st['mean'], 2.0)
        self.assertEqual(st['profit_factor'], 1.5)

    def test_gate_at_uses_only_past(self):
        ds = days(70)
        idx = (ds, [100.0] * 60 + [200.0] * 10)
        self.assertFalse(BT.gate_at(idx, ds[59], 60))
        self.assertTrue(BT.gate_at(idx, ds[60], 60))
        self.assertIsNone(BT.gate_at(idx, ds[10], 60))


class TestRun(unittest.TestCase):
    def test_short_history_is_refused_not_faked(self):
        tmp = tempfile.TemporaryDirectory()
        conn = DB.connect(os.path.join(tmp.name, 't.db'))
        conn.executemany('INSERT INTO px VALUES(?,?,?,?,?,?,?,?)',
                         [('A00001', d, 100, 101, 99, 100, 1e6, 't') for d in days(200)])
        conn.commit()
        with self.assertRaises(RuntimeError):
            BT.run(conn, SC, CFG, log=lambda *a: None)
        conn.close()
        tmp.cleanup()

    def test_end_to_end_small_db(self):
        tmp = tempfile.TemporaryDirectory()
        conn = DB.connect(os.path.join(tmp.name, 't.db'))
        base = [0.955 + 0.001 * i + (0.001 if i % 2 else -0.001) for i in range(30)]
        # 좁게 쉬다가 뚫고 계속 오르는 종목, 그리고 RS 모집단
        closes = {'A00001': uptrend_then(base + [1.01 + 0.01 * k for k in range(40)], n=400)}
        for k in range(8):
            closes[f'F0000{k}'] = [10000 + (50 if i % 2 else -50) for i in range(400)]
        ds = days(400)
        for code, cs in closes.items():
            conn.executemany('INSERT INTO px VALUES(?,?,?,?,?,?,?,?)',
                             [(code, d, c, c * 1.004, c * 0.996, c, 1e6, 't')
                              for d, c in zip(ds, cs)])
            conn.execute('INSERT INTO snap(code,asof,name,market) VALUES(?,?,?,?)',
                         (code, ds[-1], f'종목{code}', 'KOSPI'))
        conn.commit()
        sc = dict(SC, backtest=dict(SC['backtest'], years=0.2, warmup_bars=300))
        p = BT.run(conn, sc, CFG, log=lambda *a: None)
        conn.close()
        tmp.cleanup()
        self.assertGreater(p['results']['breakout']['all']['n'], 0)
        self.assertGreaterEqual(p['results']['base']['all']['n'],
                                p['results']['breakout']['all']['n'])
        for t in p['trades']['breakout']:
            self.assertGreater(t['entry_date'], t['signal_date'])   # 미래참조 없음
        lines = BT.summary_lines(p)
        self.assertTrue(any('돌파 확인' in x for x in lines))
        self.assertIn('원작 트레이더의 성과가 아니다', lines[-1])
        # 검증 시작은 워밍업 뒤다 — 앞 구간을 검증 기간으로 적지 않는다
        self.assertGreaterEqual(p['test_start'], ds[sc['backtest']['warmup_bars']])
        self.assertTrue(p['warning'])


if __name__ == '__main__':
    unittest.main()

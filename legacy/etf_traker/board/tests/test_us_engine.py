#!/usr/bin/env python3
"""
미국장 엔진 검증 — 합성 일봉으로 정의를 직접 확인한다.

실행: python3 -m board.run --test (전체) 또는 python3 -m board.tests.test_us_engine
네트워크가 필요 없다. 소스 연결 검증은 --us-check 다.
"""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine.config import load                     # noqa: E402
from board.us import build as BUILD                       # noqa: E402
from board.us import engine as E                          # noqa: E402

CFG = load(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'config', 'us.yaml'))


def bars(closes, vols=None, start=0):
    """오름차순 일봉. 고가는 종가와 같게 둔다 — 정의만 본다."""
    d0 = date(2024, 1, 1)
    vols = vols or [1_000_000.0] * len(closes)
    return [dict(asof=(d0 + timedelta(days=i + start)).isoformat(),
                 open=c, high=c, low=c * 0.99, close=c, volume=v)
            for i, (c, v) in enumerate(zip(closes, vols))]


class TestHits(unittest.TestCase):
    def test_d60_needs_full_window(self):
        # 창(60)을 못 채우면 판정하지 않는다. 빈칸이 아니라 판정 보류다.
        rows = bars([10 + i for i in range(40)])
        self.assertIsNone(E.hits_on(rows, len(rows) - 1, CFG)['kind'])

    def test_d60_hit(self):
        rows = bars([100.0] * 70 + [101.0])
        self.assertEqual(E.hits_on(rows, len(rows) - 1, CFG)['kind'], 'd60')

    def test_w52_outranks_d60(self):
        # 252일 창을 넘기면 상위 라벨이 붙는다. 상위가 하위를 포함한다.
        rows = bars([100.0] * 260 + [101.0])
        self.assertEqual(E.hits_on(rows, len(rows) - 1, CFG)['kind'], 'w52')

    def test_equal_is_not_a_high(self):
        # '갱신' 이다. 같은 값은 갱신이 아니다.
        rows = bars([100.0] * 70 + [100.0])
        self.assertIsNone(E.hits_on(rows, len(rows) - 1, CFG)['kind'])

    def test_w52_low(self):
        rows = bars([100.0] * 260 + [99.0])
        self.assertTrue(E.hits_on(rows, len(rows) - 1, CFG)['low52'])
        rows2 = bars([100.0] * 260 + [100.5])
        self.assertFalse(E.hits_on(rows2, len(rows2) - 1, CFG)['low52'])

    def test_split_floor_blocks_stale_window(self):
        """수정주가 미반영 구간은 룩백에서 잘린다.

        1000 → 200 계단(5:1 분할 미반영) 뒤 210 은 분할 전 1000 을 못 넘지만,
        가드가 창을 계단 이후로 자르므로 '창을 못 채움'(판정 보류)이 된다.
        분할 전 가격을 최고가로 들고 몇 달간 신고가가 안 뜨는 것을 막는다.
        """
        rows = bars([1000.0] * 300 + [200.0] * 10 + [210.0])
        floor = E.guard_floor(rows, CFG)
        self.assertGreater(floor, 0)
        self.assertIsNone(E.hits_on(rows, len(rows) - 1, CFG, floor=floor)['kind'])


class TestStreak(unittest.TestCase):
    def test_streak_counts_back_until_break(self):
        rows = bars([100.0] * 70 + [101.0, 102.0, 103.0])
        sf = E.streak_and_fresh(rows, len(rows) - 1, CFG)
        self.assertEqual(sf['streak'], 3)

    def test_streak_zero_without_label(self):
        rows = bars([100.0] * 70 + [99.0])
        self.assertEqual(E.streak_and_fresh(rows, len(rows) - 1, CFG)['streak'], 0)

    def test_fresh52_only_on_first_entry(self):
        rows = bars([100.0] * 260 + [101.0])
        self.assertTrue(E.streak_and_fresh(rows, len(rows) - 1, CFG)['fresh52'])

    def test_not_fresh_when_w52_seen_before_window(self):
        # 오래전에 이미 52주를 뚫은 적이 있으면 '첫 진입' 이 아니다.
        rows = bars([100.0] * 253 + [101.0] + [100.0] * 20 + [102.0])
        self.assertFalse(E.streak_and_fresh(rows, len(rows) - 1, CFG)['fresh52'])


class TestStats(unittest.TestCase):
    def test_median_ignores_none(self):
        self.assertEqual(E.median([1.0, None, 3.0]), 2.0)
        self.assertIsNone(E.median([None, None]))

    def test_breadth_excludes_unknown(self):
        b = E.breadth([{'chg_pct': 1.0}, {'chg_pct': -1.0}, {'chg_pct': 0.0},
                       {'chg_pct': None}])
        self.assertEqual((b['up'], b['down'], b['flat'], b['unknown']), (1, 1, 1, 1))
        self.assertEqual(b['up_ratio'], round(1 / 3 * 100, 1))

    def test_universe_floor(self):
        r = dict(mktcap=6e8, close=10.0, turnover=4e7)
        self.assertTrue(E.in_universe(r, CFG))
        self.assertFalse(E.in_universe(dict(r, mktcap=1e8), CFG))
        self.assertFalse(E.in_universe(dict(r, close=2.0), CFG))
        self.assertFalse(E.in_universe(dict(r, turnover=1e6), CFG))


class TestAggregates(unittest.TestCase):
    def rows(self):
        return [dict(ticker='A', chg_pct=5.0, ret_5d=1.0, mktcap=2e11,
                     sector='반도체', industry='반도체', turnover=1e9),
                dict(ticker='B', chg_pct=-3.0, ret_5d=-2.0, mktcap=1.5e11,
                     sector='반도체', industry='반도체', turnover=5e8),
                dict(ticker='C', chg_pct=1.0, ret_5d=0.5, mktcap=5e9,
                     sector='보험', industry='손해보험', turnover=1e8)]

    def test_mega_movers_keep_sign(self):
        """▲ 에는 오른 종목만, ▼ 에는 내린 종목만."""
        m = E.mega_movers(self.rows(), CFG)
        self.assertEqual([r['ticker'] for r in m['up']], ['A'])
        self.assertEqual([r['ticker'] for r in m['down']], ['B'])
        self.assertEqual(m['n'], 2)

    def test_industry_groups_order_by_best_member(self):
        g = E.industry_groups(self.rows(), CFG, 'up')
        self.assertEqual(g[0]['industry'], '반도체')
        self.assertEqual([r['ticker'] for r in g[0]['rows']], ['A'])

    def test_cap_tiers_partition(self):
        # A·B 는 $10B+, C(5e9)는 $2~10B. 구간은 겹치지 않고 빠짐도 없다.
        t = E.cap_tiers(self.rows(), CFG)
        self.assertEqual([x['n'] for x in t], [2, 1, 0])
        self.assertEqual(sum(x['n'] for x in t), len(self.rows()))

    def test_summary_strong_weak_signs(self):
        s = E.summary(self.rows(), CFG)
        self.assertTrue(all(x['median'] > 0 for x in s['strong']))
        self.assertTrue(all(x['median'] < 0 for x in s['weak']))


class TestConsistency(unittest.TestCase):
    """①(요약)과 ②(추이)가 같은 날의 신고가를 같은 수로 세야 한다.

    두 절이 서로 다른 경로로 판정하므로, 한쪽만 수정주가 가드를 적용하면
    같은 날을 50 과 59 로 센다. 실제로 그랬고 이 시험이 그걸 잡는다.
    """
    def test_summary_matches_trend_today(self):
        from board.us import demo
        series, snaps, asof = demo.make(n_tickers=60, seed=7)
        rows, board = BUILD.build(series, snaps, asof, CFG, log=lambda *a: None)
        self.assertEqual(board['summary']['newhigh'], board['trend'][-1]['newhigh'])
        self.assertEqual(board['summary']['w52_high'], board['trend'][-1]['w52_high'])
        self.assertEqual(board['summary']['w52_low'], board['trend'][-1]['w52_low'])
        self.assertEqual(board['summary']['newhigh'],
                         sum(1 for r in rows if r.get('label')))

    def test_w52_is_subset_of_newhigh(self):
        from board.us import demo
        series, snaps, asof = demo.make(n_tickers=60, seed=11)
        rows, board = BUILD.build(series, snaps, asof, CFG, log=lambda *a: None)
        self.assertLessEqual(board['summary']['w52_high'], board['summary']['newhigh'])

    def test_sector_newhigh_sums_to_total(self):
        from board.us import demo
        series, snaps, asof = demo.make(n_tickers=60, seed=13)
        rows, board = BUILD.build(series, snaps, asof, CFG, log=lambda *a: None)
        self.assertEqual(sum(s['newhigh'] for s in board['sectors']),
                         board['summary']['newhigh'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

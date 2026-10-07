#!/usr/bin/env python3
"""
스윙 시그널 검증 — 합성 일봉으로 factor 정의와 구획 배정을 확인한다.

규칙의 근거는 config/signals.yaml 에 값마다 적혀 있다. 여기서는 **정의대로
계산되는지**만 본다. 규칙이 돈을 버는지는 이 시험이 답하지 않는다.
"""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine import build as B                 # noqa: E402
from board.engine import db as DB                   # noqa: E402
from board.engine import kinds as K                 # noqa: E402
from board.engine import signals as S               # noqa: E402
from board.engine.config import load                # noqa: E402
from board.report import signals_tg as ST           # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = load(os.path.join(HERE, 'config', 'settings.yaml'))
SC = S.load_cfg()
D0 = date(2025, 1, 1)


def days(n):
    """주말을 건너뛴 영업일 n개."""
    out, d = [], D0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def bars(closes, spread=0.01, vols=None):
    ds = days(len(closes))
    vols = vols or [100000.0] * len(closes)
    return [dict(asof=a, open=c, high=c * (1 + spread), low=c * (1 - spread), close=c,
                 volume=v) for a, c, v in zip(ds, closes, vols)]


def uptrend_then(tail, n=300, start=10000.0, step=0.004):
    base = [start * (1 + step) ** i for i in range(n - len(tail))]
    last = base[-1]
    return base + [last * t for t in tail]


class TestIndicators(unittest.TestCase):
    def test_sma_needs_full_window(self):
        self.assertIsNone(S.sma([1, 2], 3))
        self.assertEqual(S.sma([1, 2, 3], 3), 2)
        self.assertIsNone(S.sma([1, None, 3], 3))

    def test_bb_width_is_zero_when_flat(self):
        self.assertEqual(S.bb_width([10.0] * 20, 19, 20, 2), 0)

    def test_nr_and_inside(self):
        rows = [dict(high=110, low=90), dict(high=108, low=92), dict(high=107, low=93)]
        self.assertTrue(S.is_inside(rows, 2))
        self.assertTrue(S.is_nr(rows, 2, 3))
        rows.append(dict(high=120, low=80))
        self.assertFalse(S.is_inside(rows, 3))
        self.assertFalse(S.is_nr(rows, 3, 3))

    def test_ma_stack(self):
        up = [float(i) for i in range(1, 200)]
        self.assertTrue(S.ma_stack(up, [5, 20, 60, 120]))
        self.assertFalse(S.ma_stack(up[::-1], [5, 20, 60, 120]))
        self.assertIsNone(S.ma_stack(up[:50], [5, 20, 60, 120]))

    def test_weekly_closes_last_day_of_week(self):
        rows = bars([float(i) for i in range(1, 11)])     # 2025-01-01(수) 부터 10영업일
        # 1주: 1/1~1/3 → 3, 2주: 1/6~1/10 → 8, 3주: 1/13~1/14 → 10
        self.assertEqual(S.weekly_closes(rows), [3.0, 8.0, 10.0])

    def test_percentile(self):
        p = S.percentile_ranks([('a', 1), ('b', 2), ('c', 3), ('d', 4)])
        self.assertEqual(p, {'a': 25.0, 'b': 50.0, 'c': 75.0, 'd': 100.0})

    def test_max_daily_ret(self):
        rows = bars([100, 100, 110, 99])
        self.assertEqual(S.max_daily_ret(rows, 3), 10.0)
        self.assertIsNone(S.max_daily_ret(rows, 5))

    def test_new_low(self):
        rows = bars([10, 9, 8, 7])
        self.assertTrue(S.new_low_at(rows, 3, 3))
        self.assertIsNone(S.new_low_at(rows, 2, 3))


class TestContraction(unittest.TestCase):
    def test_tight_base_flags_contraction(self):
        # 넓게 흔들리다가 마지막 20일을 좁게 쉰다 — BB 폭이 6개월 최저권이어야 한다
        wide = [100 + (8 if i % 2 else -8) for i in range(200)]
        tight = [100 + (0.3 if i % 2 else -0.3) for i in range(25)]
        rows = bars(wide + tight, spread=0.002)
        closes = [r['close'] for r in rows]
        st = S.contraction_at(rows, closes, len(rows) - 1, SC['contraction'])
        self.assertTrue(st['bw_near_min'])
        self.assertTrue(st['squeeze'])

    def test_fast_trend_is_not_squeeze(self):
        # 하루 변동폭은 좁은데 종가가 빠르게 움직이면 띠가 채널 밖으로 나간다
        wide = [100 + 2 * i for i in range(225)]
        rows = bars(wide, spread=0.001)
        closes = [r['close'] for r in rows]
        st = S.contraction_at(rows, closes, len(rows) - 1, SC['contraction'])
        self.assertFalse(st['squeeze'])


class TestFlows(unittest.TestCase):
    def test_shares_over_volume(self):
        f = S.flow_share({'기관': 6000, '외국인': 5000, 'unit': '주'}, 100000, 50)
        self.assertEqual(f['pct'], 11.0)
        self.assertEqual(f['basis'], '순매수량 ÷ 거래량')

    def test_amount_over_turnover(self):
        f = S.flow_share({'기관': 3.0, '외국인': 2.0, 'unit': '억원'}, 100000, 50.0)
        self.assertEqual(f['pct'], 10.0)

    def test_missing_side_is_none(self):
        # 한쪽이 없으면 합을 만들지 않는다 — 0 으로 채우지 않는다
        self.assertIsNone(S.flow_share({'기관': 3.0, 'unit': '억원'}, 1, 1))
        self.assertIsNone(S.flow_share(None, 1, 1))


class TestRegime(unittest.TestCase):
    def test_above_and_short_history(self):
        asof = days(130)[-1]
        ser = [dict(asof=a, close=100 + i) for i, a in enumerate(days(130))]
        reg, miss = S.regime(dict(source='naver', series={'KOSPI': ser, 'KOSDAQ': ser[:50]}),
                             SC['regime'], asof)
        self.assertTrue(reg['KOSPI']['above'])
        self.assertIsNone(reg['KOSDAQ'])
        self.assertTrue(any('KOSDAQ' in m for m in miss))


# ─────────────────────────── 조립 ───────────────────────────
def _row(code, rows, kind=K.COMMON, market='KOSPI', mktcap=5000.0):
    """시계열에서 universe.json 한 줄을 만든다 — 엔진과 같은 정의(종가 기준)."""
    c = rows[-1]['close']
    prior = [r['close'] for r in rows[-253:-1]]
    ref = max(prior)
    gap = round((ref - c) / ref * 100, 2)
    label = 'w52' if c > ref else None

    def ret(n):
        return round((c / rows[-1 - n]['close'] - 1) * 100, 2)
    return dict(code=code, name=f'종목{code}', market=market, kind=kind, sector='시험',
                close=c, low=rows[-1]['low'], volume=rows[-1]['volume'], turnover=100.0,
                turnover_avg20=100.0, mktcap=mktcap, chg_pct=1.0, vol_mult=2.0,
                ret_63d=ret(63), ret_126d=ret(126), ret_250d=ret(250), suspect=False,
                status='신규' if label else None, refs={'w52': ref, 'hist': None},
                close_basis=dict(label=label, gap={'w52': max(gap, 0.0) if not label else 0.0,
                                                    'hist': None}))


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = B.STATE
        B.STATE = self.tmp.name
        self.conn = DB.connect(os.path.join(self.tmp.name, 't.db'))
        # 전고점 5% 아래로 한 번 밀린 뒤 30일 동안 좁게 천천히 올라온다
        base = [0.955 + 0.001 * i + (0.001 if i % 2 else -0.001) for i in range(30)]
        series = {
            # 좁게 쉬다가 오늘 전고점을 뚫었다
            'A00001': uptrend_then(base + [1.01]),
            # 전고점 1.6% 아래에서 좁게 쉬고 있다
            'B00002': uptrend_then(base),
            # 내리는 종목 — 추세에서 떨어진다
            'C00003': [30000 * 0.997 ** i for i in range(300)],
            # 우선주는 신고가여도 대상이 아니다
            'D00005': uptrend_then(base + [1.01]),
        }
        for k in range(8):                        # 상대강도 모집단용 — 제자리
            series[f'F0000{k}'] = [10000 + (k % 3) * 10 + (50 if i % 2 else -50)
                                   for i in range(300)]
        stocks = []
        for code, closes in series.items():
            rows = bars(closes, spread=0.004)
            self.conn.executemany(
                'INSERT INTO px VALUES(?,?,?,?,?,?,?,?)',
                [(code, r['asof'], r['open'], r['high'], r['low'], r['close'],
                  r['volume'], 'test') for r in rows])
            stocks.append(_row(code, rows, kind=K.PREF if code == 'D00005' else K.COMMON))
        self.conn.commit()
        self.asof = rows[-1]['asof']
        B.write(self.asof, 'universe.json', dict(source='test', close_confirmed=True,
                                                 stocks=stocks))

    def tearDown(self):
        self.conn.close()
        B.STATE = self.saved
        self.tmp.cleanup()

    def _build(self, **kw):
        return S.build(self.asof, CFG, SC, self.conn, log=lambda *a: None, **kw)

    def test_sections(self):
        p = self._build()
        self.assertEqual([x['code'] for x in p['breakout']], ['A00001'])
        self.assertEqual([x['code'] for x in p['watch']], ['B00002'])
        a = p['breakout'][0]
        self.assertTrue(a['trend']['daily'])
        self.assertTrue(a['contraction']['recent'])
        self.assertGreaterEqual(a['rs_pct'], SC['rs']['min_pct'])
        w = p['watch'][0]
        self.assertAlmostEqual(w['trigger'], w['levels']['target'] / 1.24, places=1)
        self.assertTrue(w['in_alert_zone'])

    def test_levels_follow_42(self):
        a = self._build()['breakout'][0]
        lv = a['levels']
        self.assertAlmostEqual(lv['stop'], a['close'] * 0.92, places=1)
        # 목표는 돌파가(전고점) 기준 손절폭의 세 배 (#42)
        self.assertAlmostEqual(lv['target'], a['ref'] * 1.24, places=1)
        self.assertEqual(lv['max_weight_pct'], 12.5)

    def test_no_index_is_reported_not_guessed(self):
        p = self._build()
        self.assertIsNone(p['breakout'][0]['gate_open'])
        self.assertTrue(any('지수 일봉' in m for m in p['missing']))

    def test_gate_closed_is_marked(self):
        ser = [dict(asof=a, close=1000 - i) for i, a in enumerate(days(300))]
        p = self._build(index_hist=dict(source='t', series={'KOSPI': ser, 'KOSDAQ': ser}))
        self.assertIs(p['breakout'][0]['gate_open'], False)
        text = ST.message(p, SC)
        self.assertIn('신규 매수 보류', text)

    def test_flows_tag(self):
        p = self._build(flows_by_code={'A00001': {'기관': 8000, '외국인': 4000, 'unit': '주',
                                                  'as_of': self.asof, 'source': 'kis'}})
        a = p['breakout'][0]
        self.assertEqual(a['flows']['pct'], 12.0)
        self.assertTrue(a['flows_pass'])
        self.assertFalse(a['flows_stale'])

    def test_stale_flows_are_dated(self):
        p = self._build(flows_by_code={'A00001': {'기관': 8000, '외국인': 4000, 'unit': '주',
                                                  'as_of': '2020-01-01', 'source': 'naver'}})
        self.assertTrue(p['breakout'][0]['flows_stale'])
        self.assertIn('2020-01-01 자', ST.message(p, SC))
        self.assertEqual(S.need_flows(p, {'A00001': {}}), ['B00002'])

    def test_kind_filter_uses_engine_codes(self):
        # universe.json 의 kind 는 engine/kinds.py 의 코드다. 설정이 화면 라벨('보통주')을
        # 적으면 전 종목이 빠진다 — 첫 실데이터 실행이 그랬다.
        self.assertEqual(SC['universe']['kinds'], [K.COMMON])

    def test_zero_eligible_is_a_fault_not_a_quiet_day(self):
        sc = dict(SC, universe=dict(SC['universe'], kinds=['보통주']))
        p = S.build(self.asof, CFG, sc, self.conn, log=lambda *a: None)
        self.assertEqual(p['counts']['eligible'], 0)
        self.assertTrue(any('필터 오류' in m for m in p['missing']))
        self.assertEqual(p['funnel']['kind'], p['counts']['universe'])

    def test_funnel_counts_every_drop(self):
        p = self._build()
        c = p['counts']
        self.assertEqual(sum(p['funnel'].values()) + c['breakout'] + c['watch'], c['universe'])
        self.assertEqual(p['funnel'].get('kind'), 1)          # 우선주 하나

    def test_backtest_line(self):
        p = self._build()
        st = dict(n=10, mean=-0.55, profit_factor=0.9, win_rate=32.0)
        p['backtest'] = dict(as_of='2026-09-23', test_start='2023-09-25',
                             breakout=dict(all=st, gate_open=dict(st, mean=-0.19)),
                             watch=dict(all=st, gate_open=dict(n=0)),
                             base=dict(all=st, gate_open=dict(n=0)))
        text = ST.message(p, SC)
        self.assertIn('백테스트(2023-09-25~2026-09-23, 비용 포함): 돌파 PF 0.9', text)
        self.assertIn('게이트 위 -0.19%', text)

    def test_message(self):
        text = ST.message(self._build(), SC)
        self.assertIn('스윙 시그널', text)
        self.assertIn('돌파가', text)
        self.assertIn('성과 검증 아님', text)
        self.assertLessEqual(len(text), 4096)
        # 이름의 밑줄은 파서가 집지 않는 글자로 바뀐다
        p = self._build()
        p['breakout'][0]['name'] = 'A_B*C'
        self.assertIn('A＿B＊C', ST.message(p, SC))


if __name__ == '__main__':
    unittest.main()

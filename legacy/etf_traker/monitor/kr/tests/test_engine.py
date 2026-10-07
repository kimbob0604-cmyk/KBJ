"""
계산 엔진 시험.

두 종류를 섞었다.

1. 골든 대조 — [KBJ P1] 원본은 rsm0kk/kr-sector 배포본(2026-09-14)의 실제 값과
   맞춰 봤다(독립 구현 대조). 공개 레포에는 그 골든을 넣지 않으므로(U3) 지금 골든은
   합성 입력을 이 엔진에 넣어 낸 **회귀 골든**이다(섹터 12개·종목 160개,
   tests/fixtures/make_synthetic.py). 시험 이름의 '배포본' 은 원본 이름을 그대로 둔 것이다.
   집계식은 원본에서 배포본과 대조해 확정했다(README·legacy/etf_traker/MIGRATION.md).
2. 합성 단위 시험 — 골든에는 원자료(일별 봉)가 없어 누적지수·낙폭·결측 처리는
   대조할 수 없다. 그 부분은 손으로 만든 표본으로 잰다.
"""
import json
import os
import unittest

from .. import engine as E

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'golden.json')


def golden():
    with open(FIX, encoding='utf-8') as f:
        return json.load(f)


class 골든대조(unittest.TestCase):
    """골든(원본: 배포본 실제 숫자 / KBJ: 합성 회귀 골든)과 맞는지."""

    @classmethod
    def setUpClass(cls):
        cls.g = golden()

    def test_섹터집계가_배포본과_같다(self):
        st = self.g['stocks']
        for sec in self.g['sectors']:
            members = [v for v in st.values() if sec['name'] in v['sec']]
            for p in ('1D', '1W', '1M', '3M', '1Y'):
                want = sec['stats'].get(p)
                if not want or want.get('n') in (0, None):
                    continue
                got = E.sector_stats(members, p)
                with self.subTest(sector=sec['name'], period=p):
                    self.assertEqual(got['n'], want['n'])
                    self.assertEqual(got['up'], want['up'])
                    self.assertEqual(got['down'], want['down'])
                    self.assertEqual(got['flat'], want['flat'])
                    for k in ('mean', 'median', 'max', 'min', 'breadth'):
                        self.assertAlmostEqual(got[k], want[k], places=9,
                                               msg=f'{sec["name"]} {p} {k}')

    def test_상대강도_가중치가_배포본과_같다(self):
        """rs = 0.2·1M + 0.2·3M + 0.2·6M + 0.4·1Y."""
        n = 0
        for v in self.g['stocks'].values():
            if v.get('rs') is None or not v.get('rsp'):
                continue
            if any(v['rsp'].get(p) is None for p in E.RS_WEIGHTS):
                continue
            calc = sum(v['rsp'][p] * w for p, w in E.RS_WEIGHTS.items())
            self.assertAlmostEqual(calc, v['rs'], places=9, msg=v['n'])
            n += 1
        self.assertGreater(n, 100, '대조한 종목이 너무 적다')

    def test_sg_는_급증배수다(self):
        """sg = tl(오늘) / t(20일 중앙값). 2 이상이 급증이다.

        한 번 이걸 뒤집어 읽어서 급증 배수가 역수로 나왔다. 배포본의
        surge.turnLast('오늘 섹터 전체 거래대금') 가 sum(tl) 과 일치하는 것으로
        방향을 확정했다.
        """
        n = 0
        for v in self.g['stocks'].values():
            if not v.get('t') or v.get('tl') is None or v.get('sg') is None:
                continue
            self.assertAlmostEqual(v['tl'] / v['t'], v['sg'], places=6, msg=v['n'])
            n += 1
        self.assertGreater(n, 100)

    def _members(self, name):
        return [v for v in self.g['stocks'].values() if name in v['sec']]

    def test_모멘텀이_배포본과_같다(self):
        """rr = 기간 평균수익률 ÷ 영업일 수, 판정은 c1·c2 조합."""
        for sec in self.g['sectors']:
            want = self.g['sectorExtra'][sec['name']]['momentum']
            got = E.momentum(sec['stats'])
            with self.subTest(sector=sec['name']):
                for k in ('rr1W', 'rr2W', 'rr1M', 'rr3M'):
                    if want[k] is None:
                        self.assertIsNone(got[k])
                    else:
                        self.assertAlmostEqual(got[k], want[k], places=9, msg=k)
                self.assertEqual(got['c1'], want['c1'])
                self.assertEqual(got['c2'], want['c2'])
                self.assertEqual(got['verdict'], want['verdict'])

    def test_로테이션_사분면이_배포본과_같다(self):
        for sec in self.g['sectors']:
            want = self.g['sectorExtra'][sec['name']]['rot']
            got = E.rotation(sec['stats'])
            for p, w in want.items():
                if p not in got:
                    continue
                with self.subTest(sector=sec['name'], period=p):
                    self.assertEqual(got[p]['vs'], w['vs'])
                    self.assertEqual(got[p]['quad'], w['quad'])
                    for k in ('x', 'y'):
                        if w[k] is None:
                            self.assertIsNone(got[p][k])
                        else:
                            self.assertAlmostEqual(got[p][k], w[k], places=9, msg=k)

    def test_섹터_상대강도는_구성종목_중앙값이다(self):
        """평균이 아니다. 한쪽으로 쏠린 종목 하나가 섹터를 끌고 가지 않는다."""
        for sec in self.g['sectors']:
            want = self.g['sectorExtra'][sec['name']]
            rs, top = E.sector_rs(self._members(sec['name']))
            with self.subTest(sector=sec['name']):
                self.assertAlmostEqual(rs, want['rs'], places=9)
                self.assertAlmostEqual(top, want['rsTop'], places=9)

    def test_급증집계가_배포본과_같다(self):
        for sec in self.g['sectors']:
            want = self.g['sectorExtra'][sec['name']]['surge']
            got = E.surge_stats(self._members(sec['name']))
            with self.subTest(sector=sec['name']):
                self.assertEqual(got['n2x'], want['n2x'])
                self.assertEqual(got['n3x'], want['n3x'])
                self.assertAlmostEqual(got['median'], want['median'], places=9)
                self.assertAlmostEqual(got['sectorRatio'], want['sectorRatio'], places=9)
                # turnLast 는 '오늘 섹터 전체 거래대금' = sum(tl) 이다.
                self.assertAlmostEqual(got['turnLast'], want['turnLast'], places=2)

    def test_낙폭요약이_배포본과_같다(self):
        for sec in self.g['sectors']:
            want = self.g['sectorExtra'][sec['name']]['ddStat']
            got = E.dd_stats(self._members(sec['name']))
            if not want.get('n'):
                continue
            with self.subTest(sector=sec['name']):
                self.assertEqual(got['n'], want['n'])
                for k in ('curMean', 'curMedian', 'curWorst', 'curBest', 'mddWorst'):
                    if want[k] is None:
                        continue
                    self.assertAlmostEqual(got[k], want[k], places=9, msg=k)
                # 둘 다 비율(%)이다. 개수로 두면 하락장 표본에서 양쪽 다 0 이라
                # 틀린 구현이 통과한다 — 실제로 한 번 그렇게 속았다.
                self.assertAlmostEqual(got['nearHigh'], want['nearHigh'], places=9)
                self.assertAlmostEqual(got['deep'], want['deep'], places=9)

    def test_1D수익률은_당일등락률과_같다(self):
        for v in self.g['stocks'].values():
            if v['r'].get('1D') is None or v.get('f') is None:
                continue
            self.assertAlmostEqual(v['r']['1D'], v['f'], places=6, msg=v['n'])


class 누적지수(unittest.TestCase):

    def test_등락률을_곱해_쌓는다(self):
        dates = ['1', '2', '3']
        idx = E.cumulative_index({'1': 5.0, '2': 10.0, '3': -10.0}, dates)
        # 첫날 등락률은 적용하지 않는다 — 우리가 못 본 전일 대비라서다.
        self.assertAlmostEqual(idx['1'], 100.0)
        self.assertAlmostEqual(idx['2'], 110.0)
        self.assertAlmostEqual(idx['3'], 99.0)

    def test_권리락을_타도_수익률이_맞다(self):
        """종가로 재면 -50%, 등락률로 재면 0%.

        2:1 액면분할 당일 원주가는 10,000 → 5,000 이지만 기준가 대비 등락률은
        0% 다. 이 계열을 쓰는 이유 전체가 이 시험이다.
        """
        dates = ['1', '2', '3']
        idx = E.cumulative_index({'1': 0.0, '2': 0.0, '3': 0.0}, dates)
        r, _ = E.period_return(idx, dates, 2)
        self.assertAlmostEqual(r, 0.0)

    def test_빠진_날은_건너뛴다(self):
        """거래정지 이틀은 보합 이틀이 아니다."""
        dates = ['1', '2', '3', '4']
        idx = E.cumulative_index({'1': 0.0, '4': 10.0}, dates)
        self.assertNotIn('2', idx)
        self.assertNotIn('3', idx)
        self.assertAlmostEqual(idx['4'], 110.0)

    def test_결측이_한도를_넘으면_None(self):
        n = E.GAP_LIMIT + 5
        dates = [str(i) for i in range(n + 2)]
        # 맨 앞과 맨 뒤에만 관측이 있다. 기준일 근처가 통째로 비어 있다.
        idx = E.cumulative_index({dates[0]: 0.0, dates[-1]: 1.0}, dates)
        r, _ = E.period_return(idx, dates, E.GAP_LIMIT + 1)
        self.assertIsNone(r)

    def test_기준일_하루만_비면_그_이전으로_물러난다(self):
        """물러난 칸수와 구간 결측은 다른 것이다. 기준일만 비었으면 정상이다."""
        dates = [str(i) for i in range(10)]
        chg = {d: 0.0 for d in dates}
        del chg['7']  # 기준일 하루만 거래정지
        idx = E.cumulative_index(chg, dates)
        r, base = E.period_return(idx, dates, 2)
        self.assertIsNotNone(r)
        self.assertEqual(base, '6')

    def test_구간이_모자라면_None(self):
        dates = ['1', '2']
        idx = E.cumulative_index({'1': 0.0, '2': 1.0}, dates)
        r, _ = E.period_return(idx, dates, 252)
        self.assertIsNone(r)


class 낙폭(unittest.TestCase):

    def test_고점과_저점을_짚는다(self):
        dates = list('abcde')
        idx = {'a': 100.0, 'b': 120.0, 'c': 60.0, 'd': 90.0, 'e': 90.0}
        dd = E.drawdown(idx, dates)
        self.assertEqual(dd['peakDate'], 'b')
        self.assertAlmostEqual(dd['mdd'], -50.0)
        self.assertEqual(dd['mddPeakDate'], 'b')
        self.assertEqual(dd['mddTroughDate'], 'c')
        self.assertAlmostEqual(dd['curDD'], -25.0)
        self.assertAlmostEqual(dd['recovery'], 50.0)

    def test_계속_오르면_낙폭이_없다(self):
        dates = list('abc')
        dd = E.drawdown({'a': 100.0, 'b': 110.0, 'c': 120.0}, dates)
        self.assertAlmostEqual(dd['curDD'], 0.0)
        self.assertAlmostEqual(dd['mdd'], 0.0)


class 백분위(unittest.TestCase):

    def test_동점은_같은_순위를_받는다(self):
        r = E.percentile_ranks({'a': 1.0, 'b': 1.0, 'c': 5.0})
        self.assertEqual(r['a'], r['b'])
        self.assertEqual(r['c'], 100.0)

    def test_결측은_빠진다(self):
        r = E.percentile_ranks({'a': 1.0, 'b': None})
        self.assertNotIn('b', r)

    def test_한_기간이라도_없으면_rs가_None(self):
        """1Y 가 없는 신규 상장을 있는 기간만으로 평가하지 않는다."""
        out = E.relative_strength({
            '1M': {'a': 1.0, 'b': 2.0}, '3M': {'a': 1.0, 'b': 2.0},
            '6M': {'a': 1.0, 'b': 2.0}, '1Y': {'a': 1.0}})
        self.assertIsNone(out['b'][0])
        self.assertIsNotNone(out['a'][0])


class 유니버스(unittest.TestCase):

    def base(self, **kw):
        # t = 20일 거래대금 중앙값, tl = 오늘 거래대금. 하한은 t 에 건다.
        d = dict(c='000001', n='보통주', m='KOSPI', p=1000, k=10 ** 12,
                 t=10 ** 10, tl=10 ** 10, pref=False, spac=False, reit=False)
        d.update(kw)
        return d

    def test_제외사유를_센다(self):
        rows = [self.base(), self.base(c='2', pref=True), self.base(c='3', spac=True)]
        keep, ex = E.filter_universe(rows, dict(includePreferred=False, includeSpac=False))
        self.assertEqual(len(keep), 1)
        self.assertEqual(ex['pref'], 1)
        self.assertEqual(ex['spac'], 1)

    def test_유동성_하한은_오늘이_아니라_20일중앙값에_건다(self):
        """오늘 하루 조용했다고 유니버스에서 빼면 매일 구성이 출렁인다."""
        rows = [self.base(), self.base(c='2', t=1)]
        keep, ex = E.filter_universe(rows, dict(minTurnover=10 ** 9))
        self.assertEqual([s['c'] for s in keep], ['000001'])
        self.assertEqual(ex['illiquid'], 1)

    def test_오늘_거래대금이_적어도_중앙값이_높으면_남는다(self):
        keep, ex = E.filter_universe([self.base(tl=1)], dict(minTurnover=10 ** 9))
        self.assertEqual(len(keep), 1)
        self.assertEqual(ex['illiquid'], 0)

    def test_걸린게_없어도_사유키를_전부_채운다(self):
        """없는 키를 주면 화면 배너에 undefined 가 찍힌다. 0 도 정보다."""
        _, ex = E.filter_universe([self.base()], {})
        for r in E.EXCLUDE_REASONS:
            self.assertIn(r, ex, r)

    def test_코스닥은_시총하한_또는_상위N(self):
        rows = [self.base(c=f'{i:06d}', m='KOSDAQ', k=(10 ** 11 if i < 2 else 10 ** 9))
                for i in range(6)]
        keep, ex = E.filter_universe(rows, dict(kosdaqMinCap=10 ** 11, kosdaqTopN=3))
        # 시총 하한을 넘은 2개 + 상위 N 안에 든 1개.
        self.assertEqual(len(keep), 3)
        self.assertEqual(ex['smallKosdaq'], 3)

    def test_가격이_없으면_뺀다(self):
        keep, ex = E.filter_universe([self.base(p=None)], {})
        self.assertEqual(keep, [])
        self.assertEqual(ex['noData'], 1)


class 종목종류(unittest.TestCase):

    def test_우선주는_코드와_이름이_둘다_맞을때만(self):
        self.assertTrue(E.classify_kind('005935', '삼성전자우')['pref'])
        self.assertFalse(E.classify_kind('005930', '삼성전자')['pref'])

    def test_스팩(self):
        self.assertTrue(E.classify_kind('123456', '엔에이치스팩29호')['spac'])


if __name__ == '__main__':
    unittest.main()

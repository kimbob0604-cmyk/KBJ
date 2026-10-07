"""
수급 시험.

화면 계약(배포본에서 확인):
    stocks[c].fl   = {'1': {f,o,p,n}, '5': …, '20': …, streak, lastDate}
    sectors[*].flow = 구성종목 fl 의 합 + bothCount·bothRatio·n

f·o·p 는 외국인·기관·개인 **순매수 금액**이다(화면 툴팁이 '순매수 금액 합계').
동반 비중은 5일 창 기준('외국인과 기관이 둘 다 순매수한 종목 비중 (5일 기준)').
"""
import json
import os
import unittest

from .. import flows as FL

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'golden.json')


class 창(unittest.TestCase):

    def days(self, n):
        return [f'2026{i // 28 + 1:02d}{i % 28 + 1:02d}' for i in range(n)]

    def test_최근_N일을_더한다(self):
        dates = self.days(25)
        by = {d: dict(f=1.0, o=2.0, p=-3.0) for d in dates}
        w = FL.windows(by, dates)
        self.assertEqual(w['1']['f'], 1.0)
        self.assertEqual(w['5']['f'], 5.0)
        self.assertEqual(w['20']['f'], 20.0)
        self.assertEqual(w['20']['n'], 20)
        self.assertEqual(w['lastDate'], dates[-1])

    def test_쉰_날은_0이_아니라_창에서_빠진다(self):
        """없는 날을 0 으로 세면 '그날 아무도 안 샀다' 가 된다."""
        dates = self.days(25)
        by = {d: dict(f=1.0, o=0.0, p=0.0) for d in dates[::2]}  # 격일만 거래
        w = FL.windows(by, dates)
        self.assertLess(w['20']['n'], 20)
        self.assertEqual(w['20']['f'], w['20']['n'])

    def test_관측이_아예_없으면_None(self):
        self.assertIsNone(FL.windows({}, self.days(5)))

    def test_연속_순매수_일수(self):
        dates = self.days(6)
        by = {d: dict(f=1.0, o=0.0, p=0.0) for d in dates}
        by[dates[-3]] = dict(f=-1.0, o=0.0, p=0.0)   # 중간에 한 번 끊긴다
        self.assertEqual(FL.windows(by, dates)['streak'], 2)

    def test_마지막날_순매도면_연속은_0(self):
        dates = self.days(4)
        by = {d: dict(f=1.0, o=0.0, p=0.0) for d in dates}
        by[dates[-1]] = dict(f=-1.0, o=0.0, p=0.0)
        self.assertEqual(FL.windows(by, dates)['streak'], 0)

    def test_수량단위_응답은_버린다(self):
        """네이버·KIS 가 수량만 줄 때가 있다. 금액 칸에 수량을 넣지 않는다."""
        got = FL._amounts({'2026-09-14': {'_unit': '주', '외국인': 1000, '기관': 0, '개인': 0}})
        self.assertEqual(got, {})

    def test_금액단위만_받는다(self):
        got = FL._amounts({'2026-09-14': {'_unit': '억원', '외국인': 1.5,
                                          '기관': -2.0, '개인': 0.5}})
        self.assertEqual(got['20260914'], dict(f=1.5, o=-2.0, p=0.5))


class 섹터합(unittest.TestCase):

    def member(self, f5, o5, f1=0.0):
        fl = {str(w): dict(f=f5, o=o5, p=-(f5 + o5), n=w) for w in FL.WINDOWS}
        fl['1'] = dict(f=f1, o=0.0, p=0.0, n=1)
        fl['streak'] = 0
        fl['lastDate'] = '20260914'
        return {'c': 'X', 'fl': fl}

    def test_구성종목_합이다(self):
        mem = [self.member(1.0, 2.0), self.member(3.0, 4.0)]
        fl = FL.sector_flow(mem)
        self.assertEqual(fl['5']['f'], 4.0)
        self.assertEqual(fl['5']['o'], 6.0)
        self.assertEqual(fl['n'], 2)

    def test_동반비중은_5일창에서_둘다_순매수(self):
        mem = [self.member(1.0, 1.0),    # 둘 다 +
               self.member(1.0, -1.0),   # 기관 −
               self.member(-1.0, 1.0),   # 외국인 −
               self.member(2.0, 2.0)]    # 둘 다 +
        fl = FL.sector_flow(mem)
        self.assertEqual(fl['bothCount'], 2)
        self.assertAlmostEqual(fl['bothRatio'], 50.0)

    def test_수급이_하나도_없으면_None(self):
        """빈 dict 를 주면 화면이 그 칸을 감추지 못한다."""
        self.assertIsNone(FL.sector_flow([{'c': 'X'}, {'c': 'Y', 'fl': None}]))

    def test_일부만_있어도_있는_것만_합친다(self):
        mem = [self.member(1.0, 1.0), {'c': 'Y'}]
        fl = FL.sector_flow(mem)
        self.assertEqual(fl['n'], 1)
        self.assertEqual(fl['5']['f'], 1.0)


class 골든대조(unittest.TestCase):
    """골든의 섹터 flow 가 구성종목 fl 의 합인지(KBJ: 합성 회귀 골든)."""

    def test_섹터합이_배포본과_같다(self):
        with open(FIX, encoding='utf-8') as f:
            g = json.load(f)
        extra = g.get('sectorExtra') or {}
        checked = 0
        for name, ex in extra.items():
            want = ex.get('flow')
            if not want:
                continue
            members = [v for v in g['stocks'].values() if name in v['sec'] and v.get('fl')]
            if not members:
                continue
            got = FL.sector_flow(members)
            with self.subTest(sector=name):
                for w in ('1', '5', '20'):
                    for k in ('f', 'o', 'p'):
                        self.assertAlmostEqual(got[w][k], want[w][k], places=4,
                                               msg=f'{name} w{w}.{k}')
                self.assertEqual(got['bothCount'], want['bothCount'])
                self.assertAlmostEqual(got['bothRatio'], want['bothRatio'], places=6)
            checked += 1
        self.assertGreater(checked, 0, '대조한 섹터가 없다 — 골든에 flow 가 없나')


if __name__ == '__main__':
    unittest.main()

"""
수급 분석 시험.

원본 골든은 사용자가 준 실제 워크북(KRX 원자료, 2026.08.18~09.14 20거래일)이었다.
[KBJ P1] 공개 레포에는 실데이터를 넣지 않으므로 같은 형태의 합성 종목·합성 수급으로
바꿨다(tests/fixtures/make_synthetic.py). 기대값(expect)은 워크북 정의를 그대로 따른
**독립 계산**으로 다시 만들었다 — analyze 의 출력을 베낀 것이 아니다. 그래서 '표와 숫자가
같다' 는 대조의 성격은 유지된다. 시험 이름의 '워크북' 은 원본 이름을 그대로 둔 것이다.
"""
import json
import os
import unittest

from .. import analyze as A

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'golden.json')


def golden():
    with open(FIX, encoding='utf-8') as f:
        return json.load(f)


class 골든대조(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        g = golden()
        cls.g = g
        cls.rep = A.analyze(
            g['code'], g['name'], g['amt'], g['qty'], g['totals'],
            g['closes'], g['dates'], g['baseDate'])

    def test_검산이_전부_통과한다(self):
        """일별 합과 KRX 기간합계가 원 단위까지 맞아야 한다."""
        for label, diff, ok in self.rep['checks']:
            self.assertTrue(ok, f'{label}: 차이 {diff}')
        self.assertTrue(self.rep['ok'])

    def test_투자자표가_워크북과_같다(self):
        want = self.g['expect']['main']
        got = {r['who']: r for r in self.rep['main']}
        for who, w in want.items():
            with self.subTest(who=who):
                self.assertAlmostEqual(got[who]['month'], w['month'], places=6)
                self.assertAlmostEqual(got[who]['recent'], w['recent'], places=6)
                self.assertEqual(got[who]['qty'], w['qty'])
                self.assertEqual(got[who]['days'], w['days'], '매수일수')

    def test_기관_구분표가_워크북과_같다(self):
        want = self.g['expect']['inst']
        got = {r['who']: r for r in self.rep['inst']}
        for who, w in want.items():
            with self.subTest(who=who):
                self.assertAlmostEqual(got[who]['month'], w['month'], places=6)
                self.assertAlmostEqual(got[who]['recent'], w['recent'], places=6)

    def test_기관_기여율이_워크북과_같다(self):
        want = self.g['expect']['inst']
        for who, w in want.items():
            if w['share'] is None:
                continue
            with self.subTest(who=who):
                self.assertAlmostEqual(self.rep['share'][who], w['share'], places=9)

    def test_기여율_합이_1이다(self):
        vals = [v for v in self.rep['share'].values() if v is not None]
        self.assertAlmostEqual(sum(vals), 1.0, places=9)

    def test_누적은_기준일_0에서_시작한다(self):
        for who, series in self.rep['cum'].items():
            self.assertEqual(series[0][0], self.g['baseDate'], who)
            self.assertEqual(series[0][1], 0.0, who)
            self.assertEqual(len(series), len(self.g['dates']) + 1, who)

    def test_누적_마지막값이_기간합계와_같다(self):
        for who in ('개인', '외국인', '기관합계'):
            want = A.eok(self.g['totals'][who]['net_amt'])
            self.assertAlmostEqual(self.rep['cum'][who][-1][1], want, places=6, msg=who)

    def test_가격지수는_기준일_100이다(self):
        p = self.rep['price']
        self.assertEqual(p[0], (self.g['baseDate'], 100.0))
        # 워크북 3행: '가격 수익률 기준 08.14 종가=100'
        base = self.g['closes'][self.g['baseDate']]
        last = self.g['closes'][self.g['dates'][-1]]
        self.assertAlmostEqual(p[-1][1], last / base * 100.0, places=9)

    def test_일별_막대는_거래일수와_같다(self):
        self.assertEqual(len(self.rep['bars']), len(self.g['dates']))


class 단위(unittest.TestCase):

    def test_억원_변환(self):
        self.assertAlmostEqual(A.eok(15_056_393_175), 150.56393175)

    def test_None은_0이_아니다(self):
        """0 으로 바꾸면 '값이 없다' 가 '순매수 0원' 이 된다."""
        self.assertIsNone(A.eok(None))


class 매수일수(unittest.TestCase):

    def test_보합은_매수로_세지_않는다(self):
        daily = {'1': {'개인': 10}, '2': {'개인': 0}, '3': {'개인': -5}}
        self.assertEqual(A.buy_days(daily, ['1', '2', '3'], '개인'), 1)

    def test_값이_없는_날은_건너뛴다(self):
        daily = {'1': {'개인': 10}, '2': {}}
        self.assertEqual(A.buy_days(daily, ['1', '2'], '개인'), 1)


class 기여율(unittest.TestCase):

    def test_기관합계가_0이면_내지_않는다(self):
        """분모가 0 에 가까우면 비율이 폭발하고 아무것도 설명하지 못한다."""
        got = A.institution_share({'기관합계': 0, '투신': 5.0})
        self.assertTrue(all(v is None for v in got.values()))

    def test_음수_기관합계에서도_부호가_보존된다(self):
        got = A.institution_share({'기관합계': -10.0, '투신': -8.0, '사모': -2.0})
        self.assertAlmostEqual(got['투신'], 0.8)
        self.assertAlmostEqual(got['사모'], 0.2)


class 검산(unittest.TestCase):

    def test_어긋나면_실패로_표시한다(self):
        daily = {'1': {'기관합계': 100, '외국인': -100, '개인': 0, '전체': 0}}
        totals = {'기관합계': {'net_amt': 999}, '외국인': {'net_amt': -100},
                  '개인': {'net_amt': 0}}
        checks = A.reconcile(daily, {}, totals, ['1'])
        bad = [c for c in checks if c[0] == '기관 금액 vs 기간합계'][0]
        self.assertFalse(bad[2])
        self.assertAlmostEqual(bad[1], 100 - 999)

    def test_기간합계가_없으면_통과로_치지_않는다(self):
        checks = A.reconcile({'1': {'기관합계': 1}}, {}, {}, ['1'])
        self.assertTrue(any(not ok for _, _, ok in checks))


if __name__ == '__main__':
    unittest.main()

"""
문장 생성 시험.

규칙 기반이라 같은 입력이면 같은 문장이 나온다. 골든(원본: 실제 워크북)의 문장에
적힌 숫자가 그대로 재현되는지 본다 — 문장 자체를 글자까지 맞추지는 않는다.
표현은 바꿀 수 있지만 **숫자는 바뀌면 안 된다.**

[KBJ P1] 골든은 합성 종목·합성 수급이다(tests/fixtures/make_synthetic.py). 원본에서
숫자 리터럴(워크북 문장의 숫자)이던 기대값은 생성기가 워크북 정의로 따로 계산해 둔
golden['expect'](burst·top2·recent_driver·texts)에서 읽는다. 날짜·구분 이름 같은
정성 판정은 합성 데이터가 원본과 같은 이야기 구조를 갖도록 만들어 그대로 뒀다.
"""
import json
import os
import unittest

from .. import analyze as A
from .. import narrative as N

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'golden.json')


def report():
    with open(FIX, encoding='utf-8') as f:
        g = json.load(f)
    return g, A.analyze(g['code'], g['name'], g['amt'], g['qty'], g['totals'],
                        g['closes'], g['dates'], g['baseDate'])


class 골든대조(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.g, cls.rep = report()
        cls.text = '\n'.join(N.lines(cls.rep))

    def test_집중구간을_9월_1일부터_4일로_잡는다(self):
        b = N.burst(self.rep)
        self.assertEqual((b[0], b[1]), ('20260901', '20260904'))
        self.assertAlmostEqual(b[2], self.g['expect']['burst']['amt'], places=0)

    def test_집중구간_가격은_직전거래일_기준이다(self):
        """워크북은 8/31 종가 → 9/4 종가로 적는다. 당일(9/1) 기준으로 재면 다른 값이 나온다.

        그날의 매수는 그날 장중에 일어났으므로 그 구간의 가격 변화에 포함돼야
        한다. 여기서 한 번 틀렸다. (기대값: 생성기의 독립 계산 expect.burst.price_pct)
        """
        pr = N.price_between(self.rep, '20260901', '20260904')
        self.assertAlmostEqual(pr, self.g['expect']['burst']['price_pct'], places=1)
        self.assertEqual(N.prev_trading_day(self.rep, '20260901'), '20260831')

    def test_구간_첫날이_조회_첫날이면_기준일로_떨어진다(self):
        first = self.rep['dates'][0]
        self.assertEqual(N.prev_trading_day(self.rep, first), self.rep['baseDate'])

    def test_워크북의_숫자가_문장에_그대로_나온다(self):
        # 기관·외국인 한 달, 기관·외국인 최근 5일, 집중 금액, 집중 가격, 이후 가격,
        # 상위 둘 기여율, 최근 주동 비중 — 원본 리터럴 9개와 같은 자리.
        want_all = self.g['expect']['texts']
        self.assertEqual(len(want_all), 9)
        for want in want_all:
            self.assertIn(want, self.text, want)

    def test_교차와_전환을_짚는다(self):
        self.assertTrue(N.crossed(self.rep), '기관 매도 · 외국인 매수 교차')
        self.assertTrue(N.turned(self.rep, '외국인'))
        self.assertIn('교차', self.text)

    def test_기관_쏠림_상위_둘이_투신과_사모다(self):
        top, tot = N.top_institutions(self.rep)
        self.assertEqual([k for k, _ in top], ['투신', '사모'])
        self.assertAlmostEqual(tot, self.g['expect']['top2']['share'], places=3)

    def test_최근_기관_움직임의_주동자는_사모다(self):
        who, w = N.recent_driver(self.rep)
        self.assertEqual(who, '사모')
        self.assertAlmostEqual(abs(w), abs(self.g['expect']['recent_driver']['share']),
                               places=3)

    def test_인과로_단정하지_않는다(self):
        """이 데이터로는 어느 쪽이 원인인지 식별되지 않는다."""
        for bad in ('때문에', '때문', '이끌었', '견인했'):
            self.assertNotIn(bad, self.text, bad)
        self.assertIn('동행', self.text)

    def test_숫자표에_두_표가_다_들어간다(self):
        t = N.numbers(self.rep)
        for who in ('개인', '외국인', '기관합계', '투신', '사모'):
            self.assertIn(who, t, who)
        self.assertIn('매수일수', t)
        self.assertIn('기여율', t)


class 작은값(unittest.TestCase):

    def base(self, cum_inst, closes=None, dates=None):
        dates = dates or [f'2026090{i}' for i in range(1, 6)]
        return dict(
            code='000000', name='시험', dates=dates, baseDate='20260831',
            recent=dates, closes=closes or {},
            cum={'기관합계': [('20260831', 0.0)] + list(zip(dates, cum_inst))},
            main=[dict(who='기관합계', month=1.0, recent=1.0, qty=0, days=1),
                  dict(who='외국인', month=1.0, recent=1.0, qty=0, days=1)],
            inst=[], share={})

    def test_금액이_작으면_집중이라고_부르지_않는다(self):
        """작은 값에 서사를 붙이지 않는다."""
        rep = self.base([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertIsNone(N.burst(rep))

    def test_거래일이_창보다_짧으면_None(self):
        rep = self.base([1.0, 2.0], dates=['20260901', '20260902'])
        self.assertIsNone(N.burst(rep))

    def test_계산_안_된_것은_문장을_안_만든다(self):
        rep = self.base([1.0, 2.0, 3.0, 4.0, 5.0])
        rep['main'] = [dict(who='기관합계', month=None, recent=None, qty=None, days=0),
                       dict(who='외국인', month=None, recent=None, qty=None, days=0)]
        text = '\n'.join(N.lines(rep))
        self.assertIn('N/A', text)
        self.assertNotIn('교차', text)


if __name__ == '__main__':
    unittest.main()


class PeriodLabelTest(unittest.TestCase):
    """제목의 기간은 **받은 거래일 수**에서 만든다 (2026-09-17).

    '한 달' 을 박아 두면 60거래일 리포트도 '최근 한 달' 이라고 적힌다.
    소스가 요청보다 적게 주는 일도 있어서, 요청한 일수를 적으면 거짓이 된다.
    """

    def _rep(self, n):
        return dict(dates=[f'2026{i:04d}' for i in range(n)], recent=['x'] * 5)

    def test_스무날_남짓은_한_달(self):
        for n in (20, 21, 22):
            self.assertEqual(N.period_label(self._rep(n)), '한 달')

    def test_그_밖은_거래일_수를_그대로(self):
        self.assertEqual(N.period_label(self._rep(60)), '60거래일')
        self.assertEqual(N.period_label(self._rep(31)), '31거래일')

    def test_소스가_적게_주면_받은_수로_적는다(self):
        """60을 요청했는데 30일치만 왔으면 30이라고 적어야 한다."""
        self.assertEqual(N.period_label(self._rep(30)), '30거래일')

    def test_표_머리가_같은_라벨을_쓴다(self):
        rep = dict(self._rep(60),
                   main=[dict(who='개인', month=-4.6, recent=-3.1, days=5)],
                   inst=[], share={})
        head = N.numbers(rep).split('\n')[0]
        self.assertIn('60거래일', head)
        self.assertNotIn('한 달', head)

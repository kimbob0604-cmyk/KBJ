"""
ETF 탭 시험.

수익률·낙폭·상대강도는 주식과 같은 엔진을 쓰므로 test_engine.py 가 이미 본다.
여기서는 ETF 고유한 것만 — 분류(이름에서 읽기), 유니버스 기준, 묶음 구성,
그리고 화면 계약.
"""
import unittest

from .. import etf as T


def days(n=400):
    """연속된 영업일 n 개. 1Y(252일)를 넘겨야 상대강도가 나오고, 연도도 넘겨야
    YTD 가 나온다 — 짧게 잡으면 엔진이 맞게 None 을 내는데 시험이 헷갈린다."""
    import datetime as dt
    out, d = [], dt.date(2026, 12, 31)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime('%Y%m%d'))
        d -= dt.timedelta(days=1)
    return sorted(out)


class 분류(unittest.TestCase):

    def test_운용사를_이름_앞에서_읽는다(self):
        self.assertEqual(T.classify('KODEX 200', 1)['brand'], 'KODEX')
        self.assertEqual(T.classify('KODEX 200', 1)['company'], '삼성자산운용')
        self.assertEqual(T.classify('TIGER 반도체TOP10', 2)['brand'], 'TIGER')

    def test_레버리지와_인버스(self):
        self.assertEqual(T.classify('KODEX 200', 1)['lev'], 1)
        self.assertEqual(T.classify('KODEX 레버리지', 3)['lev'], 2)
        self.assertEqual(T.classify('KODEX 인버스', 3)['lev'], -1)
        self.assertEqual(T.classify('KODEX 200선물인버스2X', 3)['lev'], -2)

    def test_환헤지와_합성과_액티브(self):
        self.assertTrue(T.classify('TIGER 미국S&P500(H)', 4)['hedged'])
        self.assertTrue(T.classify('KODEX 골드선물(H)', 5)['hedged'])
        self.assertTrue(T.classify('TIGER 원유선물Enhanced(H) 합성', 5)['synthetic'])
        self.assertTrue(T.classify('TIMEFOLIO K바이오액티브', 2)['active'])
        self.assertFalse(T.classify('KODEX 200', 1)['active'])

    def test_자산군은_탭코드에서_온다(self):
        """우리가 이름으로 짐작하지 않는다 — 소스가 준 값이다."""
        self.assertEqual(T.classify('아무이름', 4)['asset'], '해외주식')
        self.assertEqual(T.classify('아무이름', 6)['asset'], '채권')
        self.assertEqual(T.classify('아무이름', 99)['asset'], '기타')

    def test_국내주식만_테마를_배정한다(self):
        self.assertEqual(T.classify('TIGER 반도체TOP10', 2)['theme'], '반도체')
        # 해외 ETF 이름에 '반도체' 가 있어도 국내테마로 넣지 않는다.
        self.assertEqual(T.classify('TIGER 미국필라델피아반도체', 4)['theme'], '기타')

    def test_모르면_기타로_둔다(self):
        """억지로 배정하지 않는다."""
        self.assertEqual(T.classify('KODEX 알수없는무언가', 1)['theme'], '기타')


class 유니버스(unittest.TestCase):

    def item(self, **kw):
        d = dict(c='069500', p=1000.0, r={'1D': 1.0}, net=10 ** 11, t=10 ** 10)
        d.update(kw)
        return d

    def test_순자산과_거래대금_하한(self):
        items = {'a': self.item(c='a'),
                 'b': self.item(c='b', net=10 ** 8),      # 순자산 1억 — 미달
                 'c': self.item(c='c', t=10 ** 6),        # 거래대금 100만 — 미달
                 'd': self.item(c='d', p=None)}           # 시세 없음
        keep, ex = T.filter_universe(items)
        self.assertEqual(list(keep), ['a'])
        self.assertEqual(ex, {'noData': 1, 'illiquid': 1, 'small': 1})


class 조립(unittest.TestCase):

    def setUp(self):
        self.dates = days()
        self.listing = {}
        self.hist = {}
        for i, (code, name, tab) in enumerate([
                ('069500', 'KODEX 200', 1), ('102110', 'TIGER 200', 1),
                ('091160', 'KODEX 반도체', 2), ('396500', 'TIGER 반도체TOP10', 2),
                ('122630', 'KODEX 레버리지', 3), ('114800', 'KODEX 인버스', 3),
                ('360750', 'TIGER 미국S&P500', 4), ('114260', 'KODEX 국고채3년', 6)]):
            self.listing[code] = dict(name=name, tab=tab, price=10000 + i * 10,
                                      chg=0.5, nav=10000.0 + i * 9,
                                      mktcap=5000.0, units=1e6,
                                      volume=1e5, turnover=500.0)
            self.hist[code] = {d: 10000.0 + i + j for j, d in enumerate(self.dates)}

    def test_화면_계약대로_나온다(self):
        e = T.build(self.listing, self.hist, self.dates)
        for k in ('meta', 'periods', 'groups', 'items', 'flow', 'overlap', 'charts'):
            self.assertIn(k, e, k)
        self.assertEqual(e['meta']['latestTradingDay'], self.dates[-1])
        self.assertEqual(e['meta']['universeCount'], len(e['items']))
        self.assertEqual(len(e['periods']), 8)

    def test_괴리율은_가격과_NAV의_차다(self):
        e = T.build(self.listing, self.hist, self.dates)
        x = e['items']['069500']
        self.assertAlmostEqual(x['prem'], (x['p'] / x['nav'] - 1) * 100.0)

    def test_묶음이_세개고_자산군이_탭을_따른다(self):
        e = T.build(self.listing, self.hist, self.dates)
        keys = [g['key'] for g in e['groups']]
        self.assertEqual(keys, ['자산군', '국내테마', '운용사'])
        assets = {s['name'] for s in e['groups'][0]['sectors']}
        self.assertIn('국내주식', assets)
        self.assertIn('채권', assets)

    def test_국내테마_묶음에_해외ETF가_안_들어온다(self):
        e = T.build(self.listing, self.hist, self.dates)
        theme = [g for g in e['groups'] if g['key'] == '국내테마'][0]
        members = {c for s in theme['sectors'] for c in s['members']}
        self.assertNotIn('360750', members, '해외 ETF 가 국내테마에 들어왔다')
        self.assertNotIn('114260', members, '채권 ETF 가 국내테마에 들어왔다')

    def test_상대강도가_붙는다(self):
        e = T.build(self.listing, self.hist, self.dates)
        self.assertTrue(any(x['rs'] is not None for x in e['items'].values()))

    def test_받은게_없으면_빈dict가_아니라_None(self):
        """{} 는 자바스크립트에서 참이라 renderETF 가 E.meta 에서 죽는다."""
        self.assertIsNone(T.build({}, {}, self.dates))
        self.assertIsNone(T.build(self.listing, self.hist, []))

    def test_일봉이_모자란_ETF는_뺀다(self):
        listing = dict(self.listing)
        listing['999999'] = dict(name='신규상장', tab=1, price=100, chg=0, nav=100,
                                 mktcap=5000.0, units=1e6, volume=1, turnover=500.0)
        e = T.build(listing, self.hist, self.dates)
        self.assertNotIn('999999', e['items'])

    def test_구성종목은_아직_없고_그_사실을_싣는다(self):
        e = T.build(self.listing, self.hist, self.dates)
        self.assertIsNone(e['flow'])
        self.assertIsNone(e['overlap'])
        self.assertFalse(e['meta']['pdf']['available'])
        self.assertEqual(e['meta']['pdf']['withHoldings'], 0)


if __name__ == '__main__':
    unittest.main()

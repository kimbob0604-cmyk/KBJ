"""
KIS 3구분 경로 시험.

KRX 가 막혀 있는 동안 이 경로로 리포트를 낸다. 그러면 **검산을 못 한다.**
여기서 지키는 것은 하나다 — 하지 않은 대조를 했다고 말하지 않는 것.
"""
import unittest

from .. import analyze as A
from .. import kissrc as KIS
from .. import narrative as N
from .. import telegram as T

DATES = ['20260901', '20260902', '20260903']


def three_way():
    """KIS 가 주는 모양: 3구분, 금액은 원."""
    amt = {d: {'개인': -1_000_000_000.0 + i, '외국인': 600_000_000.0,
               '기관합계': 400_000_000.0} for i, d in enumerate(DATES)}
    qty = {d: {'개인': -50_000.0, '외국인': 30_000.0, '기관합계': 20_000.0}
           for d in DATES}
    closes = {d: 20_000.0 for d in DATES}
    closes['20260831'] = 19_000.0
    return amt, qty, closes


class 검산을_못_한_것을_숨기지_않는다(unittest.TestCase):

    def setUp(self):
        amt, qty, closes = three_way()
        self.rep = A.analyze('092870', '엑시콘', amt, qty, None, closes,
                             DATES, '20260831', source='kis')

    def test_기간합계가_없으면_verified_가_False다(self):
        self.assertFalse(self.rep['verified'])

    def test_검산을_통과했다고_적지_않는다(self):
        """하지 않은 대조를 '0 차이로 통과' 로 적으면 그게 제일 나쁘다."""
        labels = [c[0] for c in self.rep['checks']]
        self.assertNotIn('기관 금액 vs 기간합계', labels)

    def test_그래도_리포트는_나온다(self):
        """검산을 못 한 것과 검산이 깨진 것은 다르다."""
        self.assertTrue(self.rep['ok'])

    def test_리포트가_출처와_검산여부를_들고_있다(self):
        """본문에서는 뺐다(사용자 요청). 사실은 데이터에 남아 로그가 찍는다."""
        self.assertEqual(self.rep['source'], 'kis')
        self.assertFalse(self.rep['verified'])

    def test_붙이기로_하면_본문이_못_했다고_말한다(self):
        T.FOOTER = True
        try:
            text = T.compose(self.rep, N.lines(self.rep), N.numbers(self.rep))
        finally:
            T.FOOTER = False
        self.assertIn(A.UNVERIFIED, text)

    def test_KRX_경로는_여전히_검산한다(self):
        amt, qty, closes = three_way()
        totals = {'기관합계': {'net_amt': 1_200_000_000.0}}
        rep = A.analyze('x', 'x', amt, qty, totals, closes, DATES, '20260831')
        self.assertTrue(rep['verified'])
        self.assertTrue(rep['checks'])


class 없는_구분은_표에서_뺀다(unittest.TestCase):

    def setUp(self):
        amt, qty, closes = three_way()
        self.rep = A.analyze('x', 'x', amt, qty, None, closes, DATES,
                             '20260831', source='kis')

    def test_기관_세부는_아예_안_나온다(self):
        """'N/A' 로 남겨 두면 '0 이었나' 로 읽힌다."""
        self.assertEqual(self.rep['inst'], [])

    def test_기타법인도_빠진다(self):
        self.assertEqual([r['who'] for r in self.rep['main']],
                         ['개인', '외국인', '기관합계'])

    def test_빈_기관표_머리만_남기지_않는다(self):
        self.assertNotIn('기관 구분', N.numbers(self.rep))


class 단위를_믿지_않고_확인한다(unittest.TestCase):
    """금액 필드가 백만원이라는 건 문서 기준 가정이다. 틀리면 100만 배로 틀린다."""

    def setUp(self):
        self.amt, self.qty, self.closes = three_way()

    def test_자릿수가_맞으면_통과(self):
        label, ratio, ok = KIS.unit_check(self.amt, self.qty, self.closes)
        self.assertTrue(ok, f'{label} {ratio}')

    def test_백만배_틀리면_잡는다(self):
        bad = {d: {k: v * 1_000_000 for k, v in row.items()}
               for d, row in self.amt.items()}
        _, _, ok = KIS.unit_check(bad, self.qty, self.closes)
        self.assertFalse(ok)

    def test_백만분의_일이어도_잡는다(self):
        bad = {d: {k: v / 1_000_000 for k, v in row.items()}
               for d, row in self.amt.items()}
        _, _, ok = KIS.unit_check(bad, self.qty, self.closes)
        self.assertFalse(ok)

    def test_대조할_게_없으면_통과라고_하지_않는다(self):
        _, _, ok = KIS.unit_check(self.amt, {}, {})
        self.assertFalse(ok)

    def test_자릿수가_틀리면_리포트를_쓰지_않는다(self):
        bad = {d: {k: v * 1_000_000 for k, v in row.items()}
               for d, row in self.amt.items()}
        rep = A.analyze('x', 'x', bad, self.qty, None, self.closes, DATES,
                        '20260831', source='kis',
                        extra_checks=(KIS.unit_check(bad, self.qty, self.closes),))
        self.assertFalse(rep['ok'])


if __name__ == '__main__':
    unittest.main()

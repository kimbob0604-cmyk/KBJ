#!/usr/bin/env python3
"""보통주 / 우선주 / 스팩 / 리츠 구분.

신고가 표에 넷이 섞여 있으면 읽는 사람이 매번 손으로 걸러낸다. 그렇다고 빼면
안 된다 — 리츠만 보고 싶은 날이 있다. 그래서 표시만 붙이고 화면에서 끈다.

**틀린 표시는 빈칸보다 나쁘다.** 우선주는 코드와 이름 둘 다 맞을 때만 단정한다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine import kinds as K              # noqa: E402
from board.web import render as RD               # noqa: E402

JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  'web', 'board.js')


class Classify(unittest.TestCase):
    def test_보통주(self):
        for code, name in (('005930', '삼성전자'), ('000660', 'SK하이닉스'),
                           ('000020', '동화약품'), ('009540', 'HD한국조선해양')):
            self.assertEqual(K.of(code, name), K.COMMON, f'{name} 이 보통주가 아니라고 나왔다')

    def test_우선주는_코드와_이름이_모두_맞을_때만(self):
        self.assertEqual(K.of('005935', '삼성전자우'), K.PREF)
        self.assertEqual(K.of('005385', '현대차우'), K.PREF)
        self.assertEqual(K.of('005387', '현대차2우B'), K.PREF)
        self.assertEqual(K.of('001529', '동양3우B'), K.PREF)

    def test_이름만_우로_끝나도_코드가_0이면_단정하지_않는다(self):
        # 끝 글자가 '우' 인 보통주가 실제로 있다. 확신이 없으면 두는 쪽이 맞다.
        self.assertEqual(K.of('123450', '가나다우'), K.COMMON)

    def test_코드만_0이_아니면_단정하지_않는다(self):
        # 신주인수권증서·구형 코드가 여기로 온다.
        self.assertEqual(K.of('005931', '삼성전자'), K.COMMON)

    def test_스팩(self):
        self.assertEqual(K.of('456400', '교보14호스팩'), K.SPAC)
        self.assertEqual(K.of('123456', 'NH스팩25호'), K.SPAC)

    def test_리츠는_이름_끝일_때만(self):
        self.assertEqual(K.of('330590', '롯데리츠'), K.REIT)
        self.assertEqual(K.of('293940', '신한알파리츠'), K.REIT)
        self.assertEqual(K.of('123450', '리츠상사'), K.COMMON)

    def test_스팩이_우선주보다_먼저다(self):
        self.assertEqual(K.of('123455', '가나스팩우'), K.SPAC)

    def test_이름이_없어도_터지지_않는다(self):
        self.assertEqual(K.of('005930', None), K.COMMON)
        self.assertEqual(K.of(None, None), K.COMMON)

    def test_보통주_이름(self):
        for pref, common in (('삼성전자우', '삼성전자'), ('현대차2우B', '현대차'),
                             ('동양3우B', '동양'), ('LG화학우', 'LG화학'), ('삼성전자', '삼성전자')):
            self.assertEqual(K.common_name(pref), common)
        self.assertEqual(K.common_name(None), '')

    def test_모든_종류에_한국어_이름이_있다(self):
        for k in (K.COMMON, K.PREF, K.SPAC, K.REIT):
            self.assertTrue(K.LABEL[k])


class Counts(unittest.TestCase):
    def test_보통주는_세지_않는다(self):
        rows = [dict(code='005930', name='삼성전자'),
                dict(code='005935', name='삼성전자우'),
                dict(code='330590', name='롯데리츠')]
        self.assertEqual(K.counts(rows), {K.PREF: 1, K.REIT: 1})

    def test_행에_박힌_값을_먼저_쓴다(self):
        # universe.json 이 이미 판정해 실어 보낸다. 화면이 다시 판정하면
        # 두 곳의 규칙이 갈라질 수 있다.
        self.assertEqual(K.counts([dict(code='005930', name='삼성전자', kind=K.PREF)]),
                         {K.PREF: 1})

    def test_빈_목록(self):
        self.assertEqual(K.counts([]), {})


class RenderTag(unittest.TestCase):
    def row(self, **kw):
        d = dict(code='005930', name='삼성전자', label='hist', chg_pct=1.0,
                 turnover=100.0, mktcap=1000.0, vol_mult=1.0)
        d.update(kw)
        return d

    def test_보통주는_꼬리표가_없다(self):
        self.assertEqual(RD._kind_tag(self.row()), '')

    def test_우선주는_꼬리표가_붙는다(self):
        h = RD._kind_tag(self.row(code='005935', name='삼성전자우'))
        self.assertIn('우선주', h)

    def test_행이_종류를_실어_보낸다(self):
        tr = RD._row_achieved(self.row(code='005935', name='삼성전자우'))
        self.assertIn('data-skind="pref"', tr)
        self.assertIn('우선주', tr)

    def test_보통주_행도_종류를_적는다(self):
        # 값이 없으면 화면이 '모르는 것' 과 '보통주' 를 구분하지 못한다.
        self.assertIn('data-skind="common"', RD._row_achieved(self.row()))

    def test_알약은_숨길_종목수를_적는다(self):
        h = RD._pills({'hist': 3}, 2, ['hist'], {}, {K.PREF: 4, K.REIT: 1})
        self.assertIn('data-filter="common"', h)
        self.assertIn('보통주만', h)
        self.assertIn('>5<', h)
        self.assertIn('우선주 4', h)

    def test_숨길_것이_없으면_알약도_없다(self):
        h = RD._pills({'hist': 3}, 2, ['hist'], {}, {})
        self.assertNotIn('data-filter="common"', h)


class ProximityRowsAreNotHidden(unittest.TestCase):
    """근접 표의 행은 라벨이 없다. 라벨 알약을 조건 없이 걸면 통째로 사라진다.

    실제로 그랬다 — `#p1 tbody tr` 전체에 '고른 기준의 라벨이 없으면 숨김'을
    걸어 두어서, data-hi 가 아예 없는 근접 행이 로드되자마자 전부 display:none
    이 됐다. 자바스크립트는 여기서 돌릴 수 없으니 그 조건이 붙어 있는지를 본다.
    """

    def setUp(self):
        with open(JS, encoding='utf-8') as f:
            self.js = f.read()

    def test_라벨_검사가_라벨_있는_행으로_제한된다(self):
        self.assertIn("'hi' in tr.dataset || 'cl' in tr.dataset", self.js,
                      '라벨 검사를 조건 없이 걸면 근접 표가 통째로 숨는다')

    def test_근접_행에는_라벨_속성이_없다(self):
        rows = [dict(code='005930', name='삼성전자', near_kind='w52', near_gap=1.0,
                     near_narrow5=-0.5, resistance={}, resistance_label={},
                     vol_mult=1.2, turnover=100.0, mktcap=1000.0)]
        html = RD._proximity_table(rows)
        self.assertNotIn('data-hi=', html)
        self.assertIn('data-skind=', html)

    def test_보통주만_필터가_붙어_있다(self):
        self.assertIn("tr.dataset.skind || 'common'", self.js)


if __name__ == '__main__':
    unittest.main(verbosity=2)

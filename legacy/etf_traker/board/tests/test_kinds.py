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

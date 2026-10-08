#!/usr/bin/env python3
"""보드에 올릴 최소 시가총액 — 화면 토글이 아니라 생성 단계에서 건다 (D-072).

토글로 두면 newhigh.json 에는 남아 있어서 엑셀·텔레그램·아티팩트가 각각 다른
목록을 들고 나간다. 화면에서만 안 보이고 파일에는 있는 상태가 제일 나쁘다.

그리고 **하한 미달과 시총을 모르는 것은 다르다.** 합쳐 세면 소스가 시총을 안 준
날에도 '하한으로 걸렀다' 는 회색 안내만 나가고 진짜 실패가 묻힌다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine import build as B               # noqa: E402
from board.engine.config import load              # noqa: E402
from board.web import render as RD                # noqa: E402

CFG = load()


def row(code, cap):
    return dict(code=code, name=code, mktcap=cap)


class Banner(unittest.TestCase):
    TH = dict(proximity=dict(max_gap_pct=5.0, min_mktcap_eok=200.0, narrow_days=5))

    def page(self, **nh):
        d = dict(as_of='2026-09-03', labels={}, counts={}, achieved=[], proximity=[],
                 thresholds=self.TH)
        d.update(nh)
        return RD.build(newhigh=d, sectors=dict(themes=[]), market=dict(missing=[]),
                        events=dict(events=[]), universe=dict(n=10), meta={},
                        rankings=dict(missing=[], scope=[]))

    def test_하한_미달은_회색이다(self):
        html = self.page(min_mktcap_eok=1000.0, n_below_mktcap=1234)
        info = html.split('banner info', 1)[1].split('</div>', 1)[0]
        self.assertIn('1,234종목', info)
        self.assertIn('1,000억', info)
        self.assertNotIn('banner warn', html)

    def test_시총을_모르는_것은_빨간색이다(self):
        html = self.page(min_mktcap_eok=1000.0, n_mktcap_unknown=7)
        warn = html.split('banner warn', 1)[1].split('</div>', 1)[0]
        self.assertIn('7', warn)
        self.assertIn('값을 모르는 것', warn)

    def test_거른_것이_없으면_줄도_없다(self):
        html = self.page(min_mktcap_eok=1000.0, n_below_mktcap=0, n_mktcap_unknown=0)
        self.assertNotIn('신고가·근접 표에서', html)

    def test_시총_토글은_사라졌다(self):
        # 표에 실린 종목이 이미 전부 하한 위라 토글이 할 일이 없다.
        self.assertNotIn('data-filter="mktcap"', RD._pills({'hist': 1}, 0, ['hist']))


class Labels(unittest.TestCase):
    """20일 신고가를 없앴다 (D-071)."""

    def test_화면_꼬리표에도_20일이_없다(self):
        self.assertNotIn('d20', RD.TAG)


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TurnoverToggle(unittest.TestCase):
    """알약 문구와 판정이 같은 값에서 나와야 한다 (D-074).

    예전에는 board.js 에 50 이 박혀 있고 settings.yaml 에 5.0 이 따로 있었다.
    설정을 고쳐도 화면이 꿈쩍하지 않고, 둘이 갈리면 알약이 '50억' 이라 적어 놓고
    다른 숫자로 거르게 된다.
    """

    def test_알약이_설정값을_문구와_속성에_함께_싣는다(self):
        h = RD._pills({'hist': 1}, 0, ['hist'], turn_min=50.0)
        self.assertIn('data-min="50"', h)
        self.assertIn('거래대금 50억↑', h)

    def test_값을_바꾸면_문구도_따라온다(self):
        h = RD._pills({'hist': 1}, 0, ['hist'], turn_min=5.0)
        self.assertIn('data-min="5"', h)
        self.assertIn('거래대금 5억↑', h)

    def test_값이_없으면_알약도_없다(self):
        self.assertNotIn('data-filter="turnover"', RD._pills({'hist': 1}, 0, ['hist']))

    def test_자바스크립트가_박힌_숫자를_안_쓴다(self):
        import os as _os
        js = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                           'web', 'board.js')
        with open(js, encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('TURN_MIN', src, '하한을 코드에 박으면 설정이 죽는다')
        self.assertIn('byTurn.dataset.min', src)

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


class Floor(unittest.TestCase):
    def test_하한_이상만_남는다(self):
        keep, small, unk = B.by_mktcap(
            [row('1', 5000.0), row('2', 999.0), row('3', 1000.0)], 1000.0)
        self.assertEqual([r['code'] for r in keep], ['1', '3'], '경계값은 남긴다')
        self.assertEqual((small, unk), (1, 0))

    def test_시총을_모르면_따로_센다(self):
        keep, small, unk = B.by_mktcap([row('1', None), row('2', 10.0)], 1000.0)
        self.assertEqual(keep, [])
        self.assertEqual((small, unk), (1, 1), '모르는 것을 하한 미달로 세면 안 된다')

    def test_하한이_0이면_아무것도_거르지_않는다(self):
        rows = [row('1', None), row('2', 1.0)]
        keep, small, unk = B.by_mktcap(rows, 0)
        self.assertEqual(len(keep), 2)
        self.assertEqual((small, unk), (0, 0))

    def test_빈_목록(self):
        self.assertEqual(B.by_mktcap([], 1000.0), ([], 0, 0))

    def test_설정값이_천억이다(self):
        self.assertEqual(CFG['display']['min_mktcap_eok'], 1000.0)

    def test_근접_하한도_같은_값이다(self):
        # 근접 판정은 200억으로 해 놓고 표에서 1,000억으로 다시 거르면
        # 화면 머리말의 '시총 200억 이상' 이 거짓이 된다.
        self.assertEqual(CFG['proximity']['min_mktcap_eok'],
                         CFG['display']['min_mktcap_eok'])

    def test_랭킹_하한도_같은_값이다(self):
        # 한 화면에서 표마다 하한이 다르면 같은 종목이 어떤 표에는 있고 어떤
        # 표에는 없는 이유를 설명할 수 없다.
        self.assertEqual(CFG['rankings']['min_mktcap_eok'],
                         CFG['display']['min_mktcap_eok'])


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

    def test_창은_60일과_252일뿐이다(self):
        self.assertEqual(CFG['newhigh']['lookback'], dict(d60=60, w52=252))

    def test_라벨과_우선순위에도_없다(self):
        self.assertNotIn('d20', CFG['newhigh']['labels'])
        self.assertNotIn('d20', CFG['newhigh']['priority'])

    def test_탐지기_6_집합에서도_빠졌다(self):
        from board.engine import aggregate as agg
        self.assertEqual(agg.MULTI_LABEL_SET, ('d60', 'w52', 'hist'))

    def test_탐지기_집합은_계산하는_라벨과_같다(self):
        # 이 둘이 어긋나면 '3종 이상'의 뜻이 조용히 바뀐다 (D-010 이 그랬다).
        from board.engine import aggregate as agg
        self.assertEqual(set(agg.MULTI_LABEL_SET), set(CFG['newhigh']['priority']))

    def test_계산한_라벨이_전부_표에_오른다(self):
        from board.engine import newhigh as nh
        self.assertEqual(nh.displayable(CFG), set(CFG['newhigh']['priority']))

    def test_화면_꼬리표에도_20일이_없다(self):
        self.assertNotIn('d20', RD.TAG)


if __name__ == '__main__':
    unittest.main(verbosity=2)


class Detector6(unittest.TestCase):
    """탐지기 6은 '몇 종'이 아니라 '어느 등급 이상'을 고르는 손잡이다 (D-073).

    라벨이 중첩이라(52주를 뚫으면 60일도 뚫린다) 라벨을 하나 빼면 같은 숫자가
    다른 등급을 가리킨다. D-071 로 20일을 빼면서 3 이 '52주 이상'에서 '역사적'으로
    조용히 옮겨 갔다. 2 가 원래 동작이다.
    """

    from board.engine import aggregate as agg

    def fires(self, hits, cfg=None):
        cfg = cfg or CFG
        n = len([k for k in self.agg.MULTI_LABEL_SET if hits.get(k)])
        return n >= cfg['detect']['multi_label_min']

    def test_60일만_뚫으면_안_잡힌다(self):
        self.assertFalse(self.fires(dict(d60=True)))

    def test_52주를_뚫으면_잡힌다(self):
        # 52주가 뚫리면 60일은 자동으로 따라온다.
        self.assertTrue(self.fires(dict(d60=True, w52=True)))

    def test_역사적도_당연히_잡힌다(self):
        self.assertTrue(self.fires(dict(d60=True, w52=True, hist=True)))

    def test_이력이_짧아_52주를_못_센_역사적도_잡힌다(self):
        # 상장 70일짜리는 w52 를 계산할 수 없다. 그렇다고 빠지면 안 된다.
        self.assertTrue(self.fires(dict(d60=True, hist=True)))

    def test_20일이_있던_시절과_같은_것을_잡는다(self):
        # 옛 구성(4라벨·min 3)과 지금 구성(3라벨·min 2)이 같은 집합을 낸다는 것을
        # 네 가지 경우로 못 박는다. 여기가 깨지면 D-071 이 의미를 또 바꾼 것이다.
        old_set = ('d20', 'd60', 'w52', 'hist')
        cases = [dict(d20=True, d60=True),                          # 60일만
                 dict(d20=True, d60=True, w52=True),                # 52주
                 dict(d20=True, d60=True, w52=True, hist=True),     # 역사적
                 dict(d20=True, d60=True, hist=True)]               # 이력 부족
        for h in cases:
            old = len([k for k in old_set if h.get(k)]) >= 3
            self.assertEqual(self.fires(h), old, h)

    def test_설정값이_2다(self):
        self.assertEqual(CFG['detect']['multi_label_min'], 2)


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

    def test_설정과_파일이_이어져_있다(self):
        # build 가 이 값을 newhigh.json 에 실어야 화면까지 닿는다.
        import inspect
        src = inspect.getsource(B.run)
        self.assertIn("min_turnover_eok=float(d.get('min_turnover_eok') or 0)", src)

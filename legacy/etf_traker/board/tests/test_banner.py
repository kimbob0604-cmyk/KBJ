#!/usr/bin/env python3
"""빠진 데이터 배너가 오류와 설계상 제외를 섞지 않는지 본다.

둘을 한 목록에 담으면 매일 같은 문구로 나오는 'ETF 제외' '유동성 하한' 옆에서
진짜 실패가 묻힌다. 실제로 520101 의 corp_code 실패가 아홉 줄 가운데 하나로
며칠 동안 그대로 있었다. 그래서 나눈 것이고, 다시 합쳐지지 않도록 못을 박는다.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine import build as B             # noqa: E402
from board.engine import rankings as R          # noqa: E402
from board.engine.config import load            # noqa: E402
from board.web import render as RD              # noqa: E402

CFG = load()
ASOF = '2026-09-02'


def stock(code, name, sector, **kw):
    d = dict(code=code, name=name, sector=sector, mktcap=5000.0, turnover=200.0,
             chg_pct=0.0, ret_5d=0.0, ret_7d=0.0, ret_10d=0.0, ret_21d=0.0,
             vol_3d_1m=100.0, themes=[])
    d.update(kw)
    return d


class StateFixture:
    """rankings.build 이 읽는 state 를 임시 폴더에 깔아 준다."""

    def __init__(self, stocks, **uni):
        self.stocks = stocks
        self.uni = uni

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = B.STATE
        B.STATE = self.tmp.name
        d = os.path.join(self.tmp.name, ASOF.replace('-', ''))
        os.makedirs(d, exist_ok=True)
        payload = dict(source='naver', as_of=ASOF, stocks=self.stocks)
        payload.update(self.uni)
        for name, obj in (('universe.json', payload),
                          ('newhigh.json', dict(as_of=ASOF)),
                          ('sectors.json', dict(taxonomy_layer1='board48')),
                          ('market.json', dict(missing=[]))):
            with open(os.path.join(d, name), 'w', encoding='utf-8') as f:
                json.dump(obj, f, ensure_ascii=False)
        return self

    def __exit__(self, *a):
        B.STATE = self.saved
        self.tmp.cleanup()
        return False


def joined(xs):
    return ' || '.join(xs)


class TestScopeVsMissing(unittest.TestCase):
    def build(self, stocks, **uni):
        with StateFixture(stocks, **uni):
            return R.build(ASOF, CFG, log=lambda *a, **k: None)

    def test_designed_exclusions_are_scope_not_missing(self):
        # 유동성 미달 한 종목 + 정상 두 종목. ETF 제외 기록도 정상으로 남긴다.
        rows = [stock('001', '가', '반도체'), stock('002', '나', '반도체'),
                stock('003', '다', '보험', mktcap=10.0, turnover=0.1)]
        out = self.build(rows, funds_excluded='1,236종목 제외 / ETF 목록 864종목')
        miss, scope = joined(out['missing']), joined(out['scope'])
        for word in ('ETF·ETN', '유동성 하한'):
            self.assertIn(word, scope, f'{word} 는 설계상 제외라 scope 여야 한다')
            self.assertNotIn(word, miss, f'{word} 가 오류 목록에 들어갔다')

    def test_filter_that_did_not_run_is_an_error(self):
        # 제외 기록이 없다는 건 필터가 안 돌았을 수 있다는 뜻이다. 이건 오류다.
        out = self.build([stock('001', '가', '반도체')])
        self.assertIn('필터가 돌지 않았을 수 있습니다', joined(out['missing']))
        self.assertNotIn('필터가 돌지 않았을', joined(out['scope']))

    def test_unclassified_below_confidence_is_scope(self):
        # 분류는 돌았고 신뢰도가 기준 미만이라 미분류로 남은 것 — 규칙 5 를 지킨 결과다.
        rows = [stock('001', '가', '미분류'), stock('002', '나', '반도체')]
        out = self.build(rows, funds_excluded='0종목 제외 / -')
        self.assertIn('섹터 미배정', joined(out['scope']))
        self.assertNotIn('섹터 미배정', joined(out['missing']))

    def test_missing_price_fields_stay_an_error(self):
        # 하한 미달이 아니라 **값을 모르는 것**이다. 색을 내리면 안 된다.
        rows = [stock('001', '가', '반도체'), stock('002', '나', '반도체', mktcap=None)]
        out = self.build(rows, funds_excluded='0종목 제외 / -')
        self.assertIn('값을 모르는 것', joined(out['missing']))


class TestBannerRender(unittest.TestCase):
    # 근접 표 머리말이 임계값을 그대로 찍으므로 최소값을 채워 준다.
    TH = dict(proximity=dict(max_gap_pct=5.0, min_mktcap_eok=200.0, narrow_days=5))

    def nh(self, **kw):
        d = dict(as_of=ASOF, labels={}, counts={}, achieved=[], proximity=[],
                 thresholds=self.TH)
        d.update(kw)
        return d

    def page(self, **kw):
        base = dict(
            newhigh=self.nh(),
            sectors=dict(themes=[]), market=dict(missing=[]), events=dict(events=[]),
            universe=dict(n=10), meta={}, rankings=dict(missing=[], scope=[]))
        base.update(kw)
        return RD.build(**base)

    def test_two_banners_carry_their_own_colour(self):
        html = self.page(rankings=dict(missing=['수집이 실패했다'],
                                       scope=['유동성 하한으로 5종목 제외']))
        self.assertIn('banner warn', html)
        self.assertIn('banner info', html)
        # 각 문구가 제 배너 안에 있는지 — 순서가 아니라 소속을 본다.
        warn = html.split('banner warn', 1)[1].split('</div>', 1)[0]
        info = html.split('banner info', 1)[1].split('</div>', 1)[0]
        self.assertIn('수집이 실패했다', warn)
        self.assertNotIn('유동성 하한', warn)
        self.assertIn('유동성 하한', info)

    def test_no_errors_means_no_red_banner(self):
        html = self.page(rankings=dict(missing=[], scope=['유동성 하한으로 5종목 제외']))
        self.assertNotIn('banner warn', html)
        self.assertIn('banner info', html)

    def test_split_guard_results_are_scope(self):
        # 가드가 걸러낸 것과 공시로 푼 것은 정상 동작이다.
        html = self.page(newhigh=self.nh(n_suspect=6, n_split_cleared=2))
        self.assertNotIn('banner warn', html)
        info = html.split('banner info', 1)[1].split('</div>', 1)[0]
        self.assertIn('수정주가 미반영 의심 6종목', info)
        self.assertIn('D-056', info)


if __name__ == '__main__':
    unittest.main(verbosity=2)

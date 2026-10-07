#!/usr/bin/env python3
"""시장 전체 투자자별 수급 — 새 주소로 갈아탄 뒤의 계약 (D-082).

옛 주소(`sise/investorDealTrendDay.naver`)가 2026-09-18 에 HTTP 410 으로
없어졌다. 새 주소는 `m.stock.naver.com/api/index/{시장}/integration` 의
`dealTrendInfo` 다. 바뀐 것이 셋이고, 셋 다 조용히 틀리기 쉬운 자리라 못을 박는다.

  1. **구분이 넷에서 셋으로 줄었다.** 기타법인을 새 소스가 안 준다. 없는 것을
     역산해 채우면 안 된다 — 네 구분의 합이 0 이라는 항등식은 그 넷이 전체를
     나눌 때만 성립하고, 새 소스가 같은 방식으로 나눈다는 보장이 없다.
  2. **단위 표기가 없다.** 옛 페이지는 머리말에 '백만원/억원' 을 적어 줬다.
     새 JSON 은 맨 숫자다. 짐작하지 않고 거래대금으로 상한을 건다.
  3. **하루치뿐이다.** 옛 페이지는 며칠치를 줬다.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.ingest import flows as F            # noqa: E402
from board.ingest.http import Fetch            # noqa: E402

# 2026-09-18 러너 실측 응답을 줄인 것. 값은 그날 코스피 실제 값이다.
REAL = {
    'dealTrendInfo': {'bizdate': '20260918', 'personalValue': '-36,019',
                      'foreignValue': '+4,394', 'institutionalValue': '+15,063'},
    'totalInfos': [
        {'code': 'accumulatedTradingVolume', 'key': '거래량', 'value': '321,351천주'},
        {'code': 'accumulatedTradingValue', 'key': '대금', 'value': '25,563,397백만'},
    ],
}


def patched(payload):
    """flows.get 을 갈아끼워 네트워크 없이 fetch_market 을 돌린다."""
    class Ctx:
        def __enter__(self):
            self.old = F.get
            F.get = lambda s, url, **kw: payload
            return self

        def __exit__(self, *a):
            F.get = self.old
            return False
    return Ctx()


class Contract(unittest.TestCase):
    def run_it(self, payload=None, market='KOSPI'):
        with patched(json.loads(json.dumps(payload or REAL))):
            return F.fetch_market(market)

    def test_세_구분을_억원으로_읽는다(self):
        r = self.run_it()
        self.assertEqual(r['unit'], '억원')
        self.assertEqual(r['by_date'], {'2026-09-18': {
            '개인': -36019.0, '외국인': 4394.0, '기관계': 15063.0}})

    def test_기타법인은_없다고_적고_역산하지_않는다(self):
        r = self.run_it()
        self.assertEqual(r['columns'], ['개인', '외국인', '기관계'])
        self.assertEqual(r['columns_absent'], ['기타법인'])
        self.assertNotIn('기타법인', r['by_date']['2026-09-18'])

    def test_날짜는_응답이_싣고_온_것을_쓴다(self):
        # 우리가 원하는 날짜로 덮어쓰면, 휴장일에 전일 값이 오늘 값으로 둔갑한다.
        p = json.loads(json.dumps(REAL))
        p['dealTrendInfo']['bizdate'] = '20260917'
        self.assertEqual(list(self.run_it(p)['by_date']), ['2026-09-17'])

    def test_하루치만_온다(self):
        self.assertEqual(len(self.run_it()['by_date']), 1)


class ScaleGuard(unittest.TestCase):
    """단위를 짐작하지 않는다 — 거래대금으로 상한을 건다."""

    def test_거래대금을_억원으로_환산한다(self):
        self.assertAlmostEqual(F._turnover_eok(REAL['totalInfos']), 255633.97, places=1)

    def test_단위_글자가_없으면_모른다고_한다(self):
        self.assertIsNone(F._turnover_eok(
            [{'code': 'accumulatedTradingValue', 'value': '25,563,397'}]))

    def test_조_단위도_읽는다(self):
        self.assertEqual(F._turnover_eok(
            [{'code': 'accumulatedTradingValue', 'value': '25.5조'}]), 255000.0)

    def test_실측값은_상한_안에_든다(self):
        # 2026-09-18 코스피: 거래대금 255,634억 · 순매수 절대합 55,476억 (21.7%)
        self.assertIsNone(F._check_scale(
            {'개인': -36019.0, '외국인': 4394.0, '기관계': 15063.0},
            255633.97, 'KOSPI', '2026-09-18'))

    def test_눈금이_백배_어긋나면_막는다(self):
        # 같은 숫자를 백만원으로 읽었다면 거래대금도 100배로 커졌어야 한다.
        # 그 불일치를 잡는 것이 이 검사다.
        with self.assertRaises(Fetch) as e:
            F._check_scale({'개인': -3601900.0, '외국인': 439400.0},
                           255633.97, 'KOSPI', '2026-09-18')
        self.assertIn('두 배를 넘는다', str(e.exception))

    def test_거래대금을_못_읽으면_검산을_건너뛰되_사유를_남긴다(self):
        note = F._check_scale({'개인': 1.0}, None, 'KOSPI', '2026-09-18')
        self.assertIn('검산을 못 했다', note)

    def test_값이_하나도_없으면_실패다(self):
        with self.assertRaises(Fetch):
            F._check_scale({'개인': None}, 255633.97, 'KOSPI', '2026-09-18')


class Failures(unittest.TestCase):
    """조용히 넘어가지 않는다 (CLAUDE.md 2장 6번)."""

    def bad(self, payload, market='KOSPI'):
        with patched(payload):
            with self.assertRaises(Fetch) as e:
                F.fetch_market(market)
        return str(e.exception)

    def test_dealTrendInfo_가_없으면_실패다(self):
        self.assertIn('dealTrendInfo', self.bad({'totalInfos': []}))

    def test_구분이_빠지면_무엇이_빠졌는지_적는다(self):
        p = json.loads(json.dumps(REAL))
        del p['dealTrendInfo']['foreignValue']
        msg = self.bad(p)
        self.assertIn('외국인', msg)

    def test_날짜가_이상하면_실패다(self):
        p = json.loads(json.dumps(REAL))
        p['dealTrendInfo']['bizdate'] = '2026-09-18'
        self.assertIn('bizdate', self.bad(p))

    def test_모르는_시장은_실패다(self):
        with self.assertRaises(Fetch):
            F.fetch_market('NASDAQ')

    def test_응답이_객체가_아니면_실패다(self):
        self.assertIn('객체가 아니다', self.bad([1, 2, 3]))


class Url(unittest.TestCase):
    def test_옛_주소를_더_쓰지_않는다(self):
        src = open(F.__file__, encoding='utf-8').read()
        self.assertNotIn("get(s, 'https://finance.naver.com/sise/investorDealTrendDay",
                         src)
        self.assertIn('m.stock.naver.com/api/index/{code}/integration', src)

    def test_두_시장을_모두_안다(self):
        self.assertEqual(sorted(F.MARKET_CODE), ['KOSDAQ', 'KOSPI'])


if __name__ == '__main__':
    unittest.main(verbosity=2)


class BoardSurfaces(unittest.TestCase):
    """없는 구분이 머리말에서 조용히 사라지면 '오늘은 0 이었나' 로 읽힌다."""

    TH = dict(proximity=dict(max_gap_pct=5.0, min_mktcap_eok=1000.0, narrow_days=5))

    def page(self, flows):
        from board.web import render as RD
        return RD.build(
            newhigh=dict(as_of='2026-09-18', labels={}, counts={}, achieved=[],
                         proximity=[], thresholds=self.TH),
            sectors=dict(themes=[]), market=dict(missing=[], flows=flows),
            events=dict(events=[]), universe=dict(n=10), meta={},
            rankings=dict(missing=[], scope=[]))

    def test_빠진_구분을_집계_범위에_적는다(self):
        html = self.page({'KOSPI': dict(
            unit='억원', columns=['개인', '외국인', '기관계'],
            columns_absent=['기타법인'],
            by_date={'2026-09-18': {'개인': -36019.0, '외국인': 4394.0,
                                    '기관계': 15063.0}})})
        info = html.split('banner info', 1)[1].split('</div>', 1)[0]
        self.assertIn('기타법인', info)
        self.assertIn('역산하면', info)

    def test_머리말에는_받은_셋만_나온다(self):
        html = self.page({'KOSPI': dict(
            unit='억원', columns=['개인', '외국인', '기관계'],
            columns_absent=['기타법인'],
            by_date={'2026-09-18': {'개인': -36019.0, '외국인': 4394.0,
                                    '기관계': 15063.0}})})
        strip = html.split('<ul class="strip">', 1)[1].split('</ul>', 1)[0]
        for who in ('개인', '외국인', '기관계'):
            self.assertIn(who, strip)
        self.assertNotIn('기타법인', strip)

    def test_눈금_검산을_못_한_날은_빨간_배너다(self):
        html = self.page({'KOSPI': dict(
            unit='억원', columns=['개인'], columns_absent=[],
            scale_note='거래대금을 못 읽어 눈금 검산을 못 했다',
            by_date={'2026-09-18': {'개인': 1.0}})})
        warn = html.split('banner warn', 1)[1].split('</div>', 1)[0]
        self.assertIn('눈금 검산', warn)

"""거래대금 단위 — 값 하나만 보면 안 걸리고, 시총과의 관계로만 잡힌다.

2026-08-28 실행에서 네이버 목록 API 의 거래대금(백만원)을 원으로 보고 1e8 로
나눠 1e6 배 작게 들어왔다. 화면에는 '0억' 으로 찍혔고, 유동성 하한에 전 종목이
걸려 랭킹 표 두 장이 통째로 비었다. 아무 예외도 안 났다.
"""
import unittest

from ..engine.build import unit_sanity
from ..ingest.naver import MWON_PER_EOK


def big(n, turnover):
    return [dict(code=f'{i:06d}', mktcap=20000.0, turnover=turnover)
            for i in range(n)]


class UnitSanityTest(unittest.TestCase):

    def test_정상이면_조용하다(self):
        self.assertIsNone(unit_sanity(big(50, 300.0)))

    def test_1e6배_작으면_잡는다(self):
        note = unit_sanity(big(50, 0.0011))
        self.assertIsNotNone(note)
        self.assertIn('단위', note)

    def test_표본이_적으면_판단하지_않는다(self):
        """소형주만 있는 날에 헛경보를 내면 안 된다."""
        self.assertIsNone(unit_sanity(big(5, 0.0011)))

    def test_소형주는_보지_않는다(self):
        small = [dict(code='000001', mktcap=500.0, turnover=0.001)] * 50
        self.assertIsNone(unit_sanity(small))

    def test_값이_없는_종목은_세지_않는다(self):
        rows = big(50, 300.0) + [dict(code='x', mktcap=20000.0, turnover=None)]
        self.assertIsNone(unit_sanity(rows))

    def test_중앙값으로_본다(self):
        """한두 종목이 거래정지라 0 이어도 전체를 의심하지 않는다."""
        rows = big(45, 300.0) + big(5, 0.0)
        self.assertIsNone(unit_sanity(rows))


class ConversionTest(unittest.TestCase):

    def test_백만원을_억원으로(self):
        # 실측: 실리콘투 110,338백만원 = 1,103.38억, 시총 34,952억 대비 3.2%
        self.assertAlmostEqual(110338 / MWON_PER_EOK, 1103.38)

    def test_예전_환산은_말이_안_된다(self):
        """1e8 로 나누면 3.5조 기업의 하루 거래대금이 11만원이 된다."""
        self.assertLess(110338 / 1e8, 0.01)

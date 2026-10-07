"""수익률 기준 시험 — 무엇과 견주는가만 본다.

숫자 자체는 나눗셈이라 틀릴 데가 없다. 틀리는 것은 늘 '기준일' 이다.
"""
import unittest

from .. import returns as R
from .. import telegram as T


def rows(*pairs):
    return [dict(asof=d, close=float(c)) for d, c in pairs]


class 기준일(unittest.TestCase):

    def setUp(self):
        self.rows = rows(('2025-12-29', 8_000), ('2025-12-30', 10_000),
                         ('2026-01-02', 11_000), ('2026-08-14', 12_000),
                         ('2026-08-17', 12_500), ('2026-09-14', 15_000),
                         ('2026-09-15', 16_500))
        self.got = R.compute(self.rows)

    def test_일간은_직전_거래일이다(self):
        self.assertEqual(self.got['daily']['base'], '2026-09-14')
        self.assertAlmostEqual(self.got['daily']['pct'], 10.0)

    def test_1개월은_달력으로_한_달_전이다(self):
        """21거래일이 아니다. 휴장이 낀 달에도 사람이 말하는 '한 달 전' 과 같다."""
        self.assertEqual(self.got['mom']['base'], '2026-08-14')
        self.assertAlmostEqual(self.got['mom']['pct'], 37.5)

    def test_한_달_전이_휴장이면_그_이전_거래일(self):
        got = R.compute(rows(('2026-08-13', 10_000), ('2026-09-15', 11_000)))
        self.assertEqual(got['mom']['base'], '2026-08-13')

    def test_연초는_작년_마지막_거래일이다(self):
        """올해 첫 거래일 종가로 재면 1월 2일의 등락이 통째로 빠진다."""
        self.assertEqual(self.got['ytd']['base'], '2025-12-30')
        self.assertAlmostEqual(self.got['ytd']['pct'], 65.0)

    def test_말일이_없는_달도_넘어간다(self):
        """3/31 의 한 달 전은 2/31 이 아니라 2월의 마지막 날이다."""
        got = R.compute(rows(('2026-02-27', 100), ('2026-03-31', 120)))
        self.assertEqual(got['mom']['base'], '2026-02-27')


class 못_재는_것(unittest.TestCase):

    def test_작년_종가가_없으면_연초를_내지_않는다(self):
        """올해 상장한 종목이다. 상장 후 수익률을 연초 대비로 적으면 거짓이 된다."""
        got = R.compute(rows(('2026-03-02', 100), ('2026-09-15', 200)))
        self.assertIsNone(got['ytd']['pct'])
        self.assertIn('작년 종가', got['ytd']['why'])

    def test_한_달_전이_없으면_1개월을_내지_않는다(self):
        got = R.compute(rows(('2026-09-14', 100), ('2026-09-15', 110)))
        self.assertIsNone(got['mom']['pct'])

    def test_일봉이_한_줄이면_아무것도_못_잰다(self):
        for k, v in R.compute(rows(('2026-09-15', 100))).items():
            self.assertIsNone(v['pct'], k)

    def test_전일_종가가_0이면_나누지_않는다(self):
        got = R.compute(rows(('2026-09-14', 0), ('2026-09-15', 100)))
        self.assertIsNone(got['daily']['pct'])


class 본문_한_줄(unittest.TestCase):

    def test_기준일을_같이_적는다(self):
        line = T.return_line(R.compute(rows(
            ('2025-12-30', 10_000), ('2026-08-14', 12_000),
            ('2026-09-14', 15_000), ('2026-09-15', 16_500))))
        self.assertIn('일간 +10.0%(09.14)', line)
        self.assertIn('1개월 +37.5%(08.14)', line)
        self.assertIn('연초 대비 +65.0%(25.12.30)', line)

    def test_연초_기준은_연도를_붙인다(self):
        """'12.30' 만 적으면 올해 12월로 읽힌다."""
        line = T.return_line(R.compute(rows(('2025-12-30', 100), ('2026-09-15', 150))))
        self.assertIn('(25.12.30)', line)

    def test_못_잰_항목은_적지_않는다(self):
        line = T.return_line(R.compute(rows(('2026-09-14', 100), ('2026-09-15', 110))))
        self.assertIn('일간', line)
        self.assertNotIn('연초', line)

    def test_하나도_못_재면_줄_자체가_없다(self):
        self.assertEqual(T.return_line(R.compute([])), '')


class 고가_기준_신고가(unittest.TestCase):
    """종가는 내렸는데 장중 고가로 52주를 뚫은 날. 보드 기본 기준이 고가다."""

    def setUp(self):
        self.ret = R.compute([
            dict(asof='2026-09-14', close=15_000.0, high=15_100.0),
            dict(asof='2026-09-15', close=14_700.0, high=15_800.0),
        ])

    def test_일간은_마이너스다(self):
        self.assertLess(self.ret['daily']['pct'], 0)

    def test_장중_고가는_플러스다(self):
        """이 줄이 없으면 '마이너스인데 왜 신고가냐' 가 설명되지 않는다."""
        self.assertGreater(self.ret['high']['pct'], 0)
        self.assertEqual(self.ret['high']['base'], '2026-09-14')

    def test_본문에_둘_다_적는다(self):
        line = T.return_line(self.ret)
        self.assertIn('일간 -2.0%', line)
        self.assertIn('장중 고가 +5.3%', line)

    def test_고가가_없으면_내지_않는다(self):
        ret = R.compute([dict(asof='2026-09-14', close=100.0),
                         dict(asof='2026-09-15', close=110.0)])
        self.assertIsNone(ret['high']['pct'])

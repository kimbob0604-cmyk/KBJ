# 원본(ET) 시험 본문은 타입 표시 없는 dict 를 그대로 쓴다 — 본문을 고치지 않고 이 파일만 완화한다.
# pyright: reportArgumentType=false, reportCallIssue=false
"""유니버스 위생 중 엔진 부분 — 거래대금 폴백·거래량 3일/1달.

KBJ P3 묶음 E1 — ET `board/tests/test_universe.py` 의 TestTurnoverFallback·TestVolRatio(7) 에서
승격했다(import 경로만 바꿨다, docs/p3_design.md §1.3·§1.11). ETF·ETN 판정(ingest.funds) 시험은
legacy 에 남는다. 대상 모듈 `kbj.engines.board.build`.
"""

import unittest

from kbj.engines.board import build as B


class TestTurnoverFallback(unittest.TestCase):
    """목록 API 가 거래대금을 안 주면 일봉에서 채운다."""

    def setUp(self):
        self.rows = [dict(asof="2026-08-27", close=10000.0, volume=1_000_000.0)]

    def test_uses_snapshot_when_present(self):
        v, est = B._turnover(self.rows, 0, 123.4)
        self.assertEqual(v, 123.4)
        self.assertFalse(est)

    def test_falls_back_to_close_times_volume(self):
        v, est = B._turnover(self.rows, 0, None)
        self.assertEqual(v, 100.0)  # 10,000원 x 100만주 = 100억
        self.assertTrue(est)

    def test_zero_with_volume_means_source_omitted(self):
        # 처음에는 '0 = 거래 없음이라는 사실' 로 봤는데 실데이터가 그걸 뒤집었다.
        # 네이버 목록 API 는 거래대금을 키에서 빼는 게 아니라 **0 을 채워** 보낸다.
        # 이 행은 거래량이 100만주라 거래대금 0 이 물리적으로 불가능하다.
        v, est = B._turnover(self.rows, 0, 0.0)
        self.assertEqual(v, 100.0)  # 종가x거래량으로 채운다
        self.assertTrue(est)

    def test_zero_without_volume_is_a_real_zero(self):
        # 거래량도 0 이면 0 이 사실이다. 거래정지 종목을 추정으로 덮지 않는다.
        rows = [dict(asof="2026-08-27", close=10000.0, volume=0)]
        v, est = B._turnover(rows, 0, 0.0)
        self.assertEqual(v, 0.0)
        self.assertFalse(est)

    def test_none_when_bar_has_no_volume(self):
        rows = [dict(asof="2026-08-27", close=10000.0, volume=None)]
        v, est = B._turnover(rows, 0, None)
        self.assertIsNone(v)
        self.assertTrue(est)


class TestVolRatio(unittest.TestCase):
    def test_three_day_average_over_month(self):
        rows = [dict(volume=100.0) for _ in range(17)] + [dict(volume=400.0)] * 3
        # 3일 평균 400, 20일 평균 (17*100+3*400)/20 = 145
        self.assertAlmostEqual(B._vol_ratio(rows, 19), round(400 / 145 * 100, 1))

    def test_single_spike_is_diluted(self):
        """하루만 터진 것과 사흘 연속의 차이 — 이 지표를 쓰는 이유다."""
        flat = [dict(volume=100.0) for _ in range(19)]
        one = B._vol_ratio(flat + [dict(volume=1000.0)], 19)  # noqa: RUF005 — 원본 그대로(승격)
        three = B._vol_ratio([dict(volume=100.0)] * 17 + [dict(volume=1000.0)] * 3, 19)
        self.assertLess(one, three)

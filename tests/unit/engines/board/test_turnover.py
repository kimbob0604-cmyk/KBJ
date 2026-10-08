# 원본(ET) 시험 본문은 타입 표시 없는 dict 를 그대로 쓴다 — 본문을 고치지 않고 이 파일만 완화한다.
# pyright: reportArgumentType=false, reportCallIssue=false
"""거래대금 0 — '거래 없음'과 '소스가 안 줌'을 가른다.

이 구분을 놓치면 유동성 하한에 전 종목이 걸려 랭킹 표 네 장이 통째로 빈다.
2026-08-27 과 2026-08-28 실행에서 연달아 그랬다.

KBJ P3 묶음 E1 — ET `board/tests/test_turnover.py` 의 TurnoverTest(6) — NaverUniverseTest(네이버
수집 소스 검사)는 legacy 에 남김 — 에서 승격했다(import 경로만 바꿨다, docs/p3_design.md
§1.3·§1.11). 대상 모듈 `kbj.engines.board`.
"""

import unittest

from kbj.engines.board.build import _turnover


def rows(close=10000, volume=1000):
    return [dict(asof="2026-08-28", open=close, high=close, low=close, close=close, volume=volume)]


class TurnoverTest(unittest.TestCase):
    def test_소스값이_있으면_그대로_쓴다(self):
        v, est = _turnover(rows(), 0, 123.4)
        self.assertEqual(v, 123.4)
        self.assertFalse(est)

    def test_소스가_없으면_종가x거래량(self):
        v, est = _turnover(rows(close=10000, volume=1000), 0, None)
        self.assertAlmostEqual(v, 10000 * 1000 / 1e8)
        self.assertTrue(est, "추정치는 표시돼야 한다")

    def test_거래량이_있는데_0이면_안_준_것이다(self):
        """이번 사고의 핵심. 0 을 사실로 받으면 폴백이 안 돈다."""
        v, est = _turnover(rows(close=10000, volume=1000), 0, 0)
        self.assertAlmostEqual(v, 0.1)
        self.assertTrue(est)

    def test_거래량이_0이면_거래대금_0은_사실이다(self):
        """거래정지 종목까지 추정으로 덮으면 안 된다."""
        v, est = _turnover(rows(volume=0), 0, 0)
        self.assertEqual(v, 0)
        self.assertFalse(est)

    def test_둘_다_없으면_None(self):
        """모르는 것은 0 이 아니라 None 이다 (CLAUDE.md 2장 1번)."""
        v, est = _turnover(rows(volume=None), 0, None)  # noqa: RUF059 — 원본 그대로(승격)
        self.assertIsNone(v)

    def test_0억으로_찍히면_하한에_걸린다(self):
        """회귀 방어 — 실제로 화면에 '0억' 이 뜬 그 상태를 재현한다."""
        v, _ = _turnover(rows(close=53000, volume=250000), 0, 0)
        self.assertGreater(v, 1.0, "1억 하한을 넘어야 랭킹에 남는다")

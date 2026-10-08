# 원본(ET) 시험 본문은 타입 표시 없는 dict 를 그대로 쓴다 — 본문을 고치지 않고 이 파일만 완화한다.
# pyright: reportArgumentType=false
"""산출물이 스스로와 맞는지 보는 교차 검사.

이 프로젝트를 반복해서 문 것은 '필드 하나만 보면 멀쩡한데 서로 안 맞는' 부류다 —
거래대금 단위(D-035), 밀린 열(D-049), 낡은 과거 구간(D-056). 값 하나를 보는
검사로는 하나도 못 잡았고 전부 교차 검사가 잡았다.

KBJ P3 묶음 E1 — ET `board/tests/test_consistency.py` 의
ConsistencyTest·HistRefContainsWindowsTest(20) — LabelKindSyncTest(sqlite 라벨 표 정리 — legacy
build shim)는 legacy 에 남김 — 에서 승격했다(import 경로만 바꿨다, docs/p3_design.md §1.3·§1.11).
대상 모듈 `kbj.engines.board`.
"""

import unittest

from kbj.engines.board import build as B
from kbj.engines.board.config import BoardConfig

CFG = BoardConfig.load()


def row(code="000001", hits=None, gap=None, label=None, near=None, refs=None):
    return dict(
        code=code, hits=hits or {}, gap=gap or {}, label=label, near_kind=near, refs=refs or {}
    )


def ok(**kw):
    """정상 행 — 52주 갱신, 하위도 갱신, 갭 음수."""
    base = dict(
        hits={"hist": False, "w52": True, "d60": True, "d20": True},
        gap={"w52": -1.2, "d60": -5.0, "d20": -6.0},
        label="w52",
    )
    base.update(kw)
    return row(**base)


class ConsistencyTest(unittest.TestCase):
    def _why(self, rows):
        return " / ".join(B.consistency_notes(rows, CFG))

    def test_clean_rows_pass(self):
        self.assertEqual(B.consistency_notes([ok(), ok(code="000002")], CFG), [])

    def test_no_rows_pass(self):
        self.assertEqual(B.consistency_notes([], CFG), [])

    def test_upper_label_without_lower(self):
        r = ok(hits={"hist": False, "w52": True, "d60": False, "d20": True})
        self.assertIn("하위가 미갱신", self._why([r]))

    def test_uncomputed_lower_kind_is_not_a_violation(self):
        """px 가 420일치뿐이라 w52 를 계산 못 했는데 hist 는 스칼라로 판정된다.

        실측에서 090410 이 이걸로 잘못 찍혔다. 엔진이 아니라 검사가 틀렸다.
        """
        r = row(
            hits={"hist": True, "w52": False, "d60": True, "d20": True},
            gap={"hist": -0.5, "w52": None, "d60": -5.0, "d20": -6.0},
            label="hist",
        )
        self.assertEqual(B.consistency_notes([r], CFG), [])

    def test_computed_lower_kind_still_flags(self):
        """계산은 됐는데 갱신이 없으면 여전히 모순이다. 구멍을 만들지 않는다."""
        r = row(
            hits={"hist": True, "w52": False, "d60": True, "d20": True},
            gap={"hist": -0.5, "w52": +2.0, "d60": -5.0, "d20": -6.0},
            label="hist",
        )
        self.assertIn("하위가 미갱신", self._why([r]))

    def test_label_is_not_the_top(self):
        r = ok(label="d60")
        self.assertIn("최상위가 아님", self._why([r]))

    def test_label_without_any_hit(self):
        r = row(hits={"w52": False}, label="w52")
        self.assertIn("갱신이 없는데", self._why([r]))

    def test_positive_gap_on_a_hit(self):
        r = ok(gap={"w52": +2.5, "d60": -5.0, "d20": -6.0})
        self.assertIn("갭이 양수", self._why([r]))

    def test_proximity_outside_the_threshold(self):
        mx = CFG["proximity"]["max_gap_pct"]
        r = row(hits={}, gap={"w52": mx + 1.0}, near="w52")
        self.assertIn("임계 밖", self._why([r]))

    def test_proximity_inside_is_fine(self):
        mx = CFG["proximity"]["max_gap_pct"]
        r = row(hits={}, gap={"w52": mx - 0.1}, near="w52")
        self.assertEqual(B.consistency_notes([r], CFG), [])

    def test_proximity_without_a_gap_is_flagged(self):
        # 근접이라고 적어 놓고 갭이 없으면 무엇으로 판정했는지 알 수 없다.
        r = row(hits={}, gap={}, near="w52")
        self.assertIn("임계 밖", self._why([r]))

    def test_examples_are_bounded(self):
        rows = [ok(code=f"{i:06d}", label="d60") for i in range(50)]
        note = self._why(rows)
        self.assertIn("50종목", note)
        self.assertEqual(note.count(" / "), B.CONSIST_EX - 1)

    def test_message_names_the_kind_and_value(self):
        """'그렇다' 만 적으면 다음 사람이 할 수 있는 게 없다.

        첫 판은 코드만 적어서, 실측 위반을 보고도 어느 종류가 왜 어긋났는지
        몰라 두 번 헛다리를 짚었다.
        """
        r = ok(
            hits={"hist": False, "w52": True, "d60": False, "d20": True},
            gap={"w52": -1.2, "d60": +0.8, "d20": -6.0},
        )
        note = self._why([r])
        self.assertIn("d60", note)
        self.assertIn("+0.80%", note)
        self.assertIn("w52 인데", note)

    def test_proximity_message_says_the_threshold(self):
        mx = CFG["proximity"]["max_gap_pct"]
        note = self._why([row(hits={}, gap={"w52": mx + 3}, near="w52")])
        self.assertIn(f"임계 {mx}", note)
        self.assertIn("w52", note)

    def test_build_calls_it(self):
        import inspect

        src = inspect.getsource(B)
        self.assertIn("consistency_notes(rows, cfg)", src)


if __name__ == "__main__":
    unittest.main()


class HistRefContainsWindowsTest(unittest.TestCase):
    """역사적 최고가는 어떤 창의 최고가보다도 작을 수 없다.

    상장 이후 전체가 직전 252일을 포함하기 때문이다. 작다면 alltime 스칼라와
    px 일봉이 서로 다른 시계열을 가리키는 것이고, 그러면 라벨 판정이 통째로
    어긋난다. 실측에서 090410 이 hist 기준 42% 낮았다.
    """

    def _why(self, r):
        return " / ".join(B.consistency_notes([r], CFG))

    def test_hist_below_a_window_is_flagged(self):
        r = row(refs={"hist": 1000.0, "w52": 1730.0, "d60": 1200.0})
        note = self._why(r)
        self.assertIn("역사적 최고가가 창 최고가보다 낮음", note)
        self.assertIn("1,730", note)
        self.assertIn("w52", note)

    def test_hist_above_all_windows_is_fine(self):
        self.assertEqual(B.consistency_notes([row(refs={"hist": 2000.0, "w52": 1730.0})], CFG), [])

    def test_equal_is_fine(self):
        # 오늘 새로 쓴 최고가가 곧 창 최고가인 것은 정상이다.
        self.assertEqual(B.consistency_notes([row(refs={"hist": 1730.0, "w52": 1730.0})], CFG), [])

    def test_missing_hist_is_not_flagged(self):
        # 이력이 짧아 hist 를 계산 못 한 것은 모순이 아니다.
        self.assertEqual(B.consistency_notes([row(refs={"hist": None, "w52": 1730.0})], CFG), [])

    def test_missing_window_is_not_flagged(self):
        self.assertEqual(B.consistency_notes([row(refs={"hist": 1000.0, "w52": None})], CFG), [])

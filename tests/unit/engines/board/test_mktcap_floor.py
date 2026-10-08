"""보드에 올릴 최소 시가총액 — 화면 토글이 아니라 생성 단계에서 건다 (D-072).

토글로 두면 newhigh.json 에는 남아 있어서 엑셀·텔레그램·아티팩트가 각각 다른
목록을 들고 나간다. 화면에서만 안 보이고 파일에는 있는 상태가 제일 나쁘다.

그리고 **하한 미달과 시총을 모르는 것은 다르다.** 합쳐 세면 소스가 시총을 안 준
날에도 '하한으로 걸렀다' 는 회색 안내만 나가고 진짜 실패가 묻힌다.

KBJ P3 묶음 E1 — ET `board/tests/test_mktcap_floor.py` 의 Floor·Labels·Detector6(18) —
Banner·TurnoverToggle·Labels 의 화면 꼬리표 시험(web.render)은 legacy 에 남김 — 에서
승격했다(import 경로만 바꿨다, docs/p3_design.md §1.3·§1.11). 대상 모듈 `kbj.engines.board`.
"""

import unittest

from kbj.engines.board import build as B
from kbj.engines.board.config import BoardConfig

CFG = BoardConfig.load()


def row(code, cap):
    return dict(code=code, name=code, mktcap=cap)


class Floor(unittest.TestCase):
    def test_하한_이상만_남는다(self):
        keep, small, unk = B.by_mktcap(
            [row("1", 5000.0), row("2", 999.0), row("3", 1000.0)], 1000.0
        )
        self.assertEqual([r["code"] for r in keep], ["1", "3"], "경계값은 남긴다")
        self.assertEqual((small, unk), (1, 0))

    def test_시총을_모르면_따로_센다(self):
        keep, small, unk = B.by_mktcap([row("1", None), row("2", 10.0)], 1000.0)
        self.assertEqual(keep, [])
        self.assertEqual((small, unk), (1, 1), "모르는 것을 하한 미달로 세면 안 된다")

    def test_하한이_0이면_아무것도_거르지_않는다(self):
        rows = [row("1", None), row("2", 1.0)]
        keep, small, unk = B.by_mktcap(rows, 0)
        self.assertEqual(len(keep), 2)
        self.assertEqual((small, unk), (0, 0))

    def test_빈_목록(self):
        self.assertEqual(B.by_mktcap([], 1000.0), ([], 0, 0))

    def test_설정값이_천억이다(self):
        self.assertEqual(CFG["display"]["min_mktcap_eok"], 1000.0)

    def test_근접_하한도_같은_값이다(self):
        # 근접 판정은 200억으로 해 놓고 표에서 1,000억으로 다시 거르면
        # 화면 머리말의 '시총 200억 이상' 이 거짓이 된다.
        self.assertEqual(CFG["proximity"]["min_mktcap_eok"], CFG["display"]["min_mktcap_eok"])

    def test_랭킹_하한도_같은_값이다(self):
        # 한 화면에서 표마다 하한이 다르면 같은 종목이 어떤 표에는 있고 어떤
        # 표에는 없는 이유를 설명할 수 없다.
        self.assertEqual(CFG["rankings"]["min_mktcap_eok"], CFG["display"]["min_mktcap_eok"])


class Labels(unittest.TestCase):
    """20일 신고가를 없앴다 (D-071)."""

    def test_창은_120거래일과_달력_52주뿐이다(self):
        # ADR 0017: 60일(직전 60봉)·252봉 → 120 시장 거래일·달력 364일
        self.assertEqual(CFG["newhigh"]["lookback_trading_days"], dict(d120=120))
        self.assertEqual(CFG["newhigh"]["lookback_calendar_days"], dict(w52=364))

    def test_라벨과_우선순위에도_없다(self):
        self.assertNotIn("d20", CFG["newhigh"]["labels"])
        self.assertNotIn("d20", CFG["newhigh"]["priority"])

    def test_탐지기_6_집합에서도_빠졌다(self):
        from kbj.engines.board import aggregate as agg

        self.assertEqual(agg.MULTI_LABEL_SET, ("d120", "w52", "hist"))
        self.assertNotIn("d60", agg.MULTI_LABEL_SET)  # ADR 0017

    def test_탐지기_집합은_계산하는_라벨과_같다(self):
        # 이 둘이 어긋나면 '3종 이상'의 뜻이 조용히 바뀐다 (D-010 이 그랬다).
        from kbj.engines.board import aggregate as agg

        self.assertEqual(set(agg.MULTI_LABEL_SET), set(CFG["newhigh"]["priority"]))

    def test_계산한_라벨이_전부_표에_오른다(self):
        from kbj.engines.board import newhigh as nh

        self.assertEqual(nh.displayable(CFG), set(CFG["newhigh"]["priority"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class Detector6(unittest.TestCase):
    """탐지기 6은 '몇 종'이 아니라 '어느 등급 이상'을 고르는 손잡이다 (D-073).

    라벨이 중첩이라(52주를 뚫으면 120일도 뚫린다 — ADR 0017 전에는 60일) 라벨을 하나 빼면 같은
    숫자가 다른 등급을 가리킨다. D-071 로 20일을 빼면서 3 이 '52주 이상'에서 '역사적'으로 조용히
    옮겨 갔다. 2 가 원래 동작이다.
    """

    from kbj.engines.board import aggregate as agg

    def fires(self, hits, cfg=None):
        cfg = cfg or CFG
        n = len([k for k in self.agg.MULTI_LABEL_SET if hits.get(k)])
        return n >= cfg["detect"]["multi_label_min"]

    def test_120일만_뚫으면_안_잡힌다(self):
        self.assertFalse(self.fires(dict(d120=True)))

    def test_52주를_뚫으면_잡힌다(self):
        # 52주가 뚫리면 120일은 자동으로 따라온다.
        self.assertTrue(self.fires(dict(d120=True, w52=True)))

    def test_역사적도_당연히_잡힌다(self):
        self.assertTrue(self.fires(dict(d120=True, w52=True, hist=True)))

    def test_이력이_짧아_52주를_못_센_역사적도_잡힌다(self):
        # 상장 130거래일짜리는 w52 를 계산할 수 없다. 그렇다고 빠지면 안 된다.
        self.assertTrue(self.fires(dict(d120=True, hist=True)))

    def test_20일이_있던_시절과_같은_것을_잡는다(self):
        # 옛 구성(4라벨·min 3)과 지금 구성(3라벨·min 2)이 같은 집합을 낸다는 것을
        # 네 가지 경우로 못 박는다. 여기가 깨지면 D-071 이 의미를 또 바꾼 것이다.
        # ADR 0017: 최하위 창 라벨 자리(옛 60일)는 120일이다 — 구조(4라벨·min 3 = 3라벨·min 2)는
        # 같다.
        old_set = ("d20", "d120", "w52", "hist")
        cases = [
            dict(d20=True, d120=True),  # 120일만
            dict(d20=True, d120=True, w52=True),  # 52주
            dict(d20=True, d120=True, w52=True, hist=True),  # 역사적
            dict(d20=True, d120=True, hist=True),
        ]  # 이력 부족
        for h in cases:
            old = len([k for k in old_set if h.get(k)]) >= 3
            self.assertEqual(self.fires(h), old, h)

    def test_설정값이_2다(self):
        self.assertEqual(CFG["detect"]["multi_label_min"], 2)

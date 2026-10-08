"""보통주 / 우선주 / 스팩 / 리츠 구분.

신고가 표에 넷이 섞여 있으면 읽는 사람이 매번 손으로 걸러낸다. 그렇다고 빼면
안 된다 — 리츠만 보고 싶은 날이 있다. 그래서 표시만 붙이고 화면에서 끈다.

**틀린 표시는 빈칸보다 나쁘다.** 우선주는 코드와 이름 둘 다 맞을 때만 단정한다.

KBJ P3 묶음 E1 — ET `board/tests/test_kinds.py` 의 Classify·Counts(13) —
RenderTag·ProximityRowsAreNotHidden(web.render)은 legacy 에 남김 — 에서 승격했다(import 경로만
바꿨다, docs/p3_design.md §1.3·§1.11). 대상 모듈 `kbj.engines.board`.
"""

import os
import unittest

from kbj.engines.board import kinds as K

JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "board.js")


class Classify(unittest.TestCase):
    def test_보통주(self):
        for code, name in (
            ("005930", "삼성전자"),
            ("000660", "SK하이닉스"),
            ("000020", "동화약품"),
            ("009540", "HD한국조선해양"),
        ):
            self.assertEqual(K.of(code, name), K.COMMON, f"{name} 이 보통주가 아니라고 나왔다")

    def test_우선주는_코드와_이름이_모두_맞을_때만(self):
        self.assertEqual(K.of("005935", "삼성전자우"), K.PREF)
        self.assertEqual(K.of("005385", "현대차우"), K.PREF)
        self.assertEqual(K.of("005387", "현대차2우B"), K.PREF)
        self.assertEqual(K.of("001529", "동양3우B"), K.PREF)

    def test_이름만_우로_끝나도_코드가_0이면_단정하지_않는다(self):
        # 끝 글자가 '우' 인 보통주가 실제로 있다. 확신이 없으면 두는 쪽이 맞다.
        self.assertEqual(K.of("123450", "가나다우"), K.COMMON)

    def test_코드만_0이_아니면_단정하지_않는다(self):
        # 신주인수권증서·구형 코드가 여기로 온다.
        self.assertEqual(K.of("005931", "삼성전자"), K.COMMON)

    def test_스팩(self):
        self.assertEqual(K.of("456400", "교보14호스팩"), K.SPAC)
        self.assertEqual(K.of("123456", "NH스팩25호"), K.SPAC)

    def test_리츠는_이름_끝일_때만(self):
        self.assertEqual(K.of("330590", "롯데리츠"), K.REIT)
        self.assertEqual(K.of("293940", "신한알파리츠"), K.REIT)
        self.assertEqual(K.of("123450", "리츠상사"), K.COMMON)

    def test_스팩이_우선주보다_먼저다(self):
        self.assertEqual(K.of("123455", "가나스팩우"), K.SPAC)

    def test_이름이_없어도_터지지_않는다(self):
        self.assertEqual(K.of("005930", None), K.COMMON)
        self.assertEqual(K.of(None, None), K.COMMON)

    def test_보통주_이름(self):
        for pref, common in (
            ("삼성전자우", "삼성전자"),
            ("현대차2우B", "현대차"),
            ("동양3우B", "동양"),
            ("LG화학우", "LG화학"),
            ("삼성전자", "삼성전자"),
        ):
            self.assertEqual(K.common_name(pref), common)
        self.assertEqual(K.common_name(None), "")

    def test_모든_종류에_한국어_이름이_있다(self):
        for k in (K.COMMON, K.PREF, K.SPAC, K.REIT):
            self.assertTrue(K.LABEL[k])


class Counts(unittest.TestCase):
    def test_보통주는_세지_않는다(self):
        rows = [
            dict(code="005930", name="삼성전자"),
            dict(code="005935", name="삼성전자우"),
            dict(code="330590", name="롯데리츠"),
        ]
        self.assertEqual(K.counts(rows), {K.PREF: 1, K.REIT: 1})

    def test_행에_박힌_값을_먼저_쓴다(self):
        # universe.json 이 이미 판정해 실어 보낸다. 화면이 다시 판정하면
        # 두 곳의 규칙이 갈라질 수 있다.
        self.assertEqual(K.counts([dict(code="005930", name="삼성전자", kind=K.PREF)]), {K.PREF: 1})

    def test_빈_목록(self):
        self.assertEqual(K.counts([]), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)

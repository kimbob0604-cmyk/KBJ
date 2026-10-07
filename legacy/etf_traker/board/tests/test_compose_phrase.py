"""
초안 조립의 금지 문구 경로 — board/writer/compose._one (D-085).

종목명·수치 위반은 재시도 뒤 섹션을 뺀다(기존). 금지 문구만 남았을 때는 **한 번 더**
부르고, 그래도 남으면 그 문장만 지우고 '문장 N개 제거(금지 문구)' 를 결손에 적는다.
가짜 클라이언트로 호출 횟수와 두 번째 프롬프트의 내용을 본다.
"""
import unittest
from unittest import mock

from ..writer import compose as CP
from ..writer import verify as V

CFG = dict(writer=dict(retries=2, strict_numbers=True, models=dict(narrate='m'),
                       parallel=1))
PACK = {'name': '수소', 'chg': '-2.10%', 'turnover': '478억',
        'stocks': [{'name': '상아프론테크', 'chg': '-4.33%', 'vol_mult': '2.1배'}]}
UNI = ['상아프론테크', '두산퓨얼셀']

PHRASED = ('- 수소 테마 전 종목 하락, 트리거는 확인되지 않음. 거래대금 478억.\n'
           '- 상아프론테크(-4.33%) 거래량 2.1배.')
CLEAN = '- 수소 테마 -2.10%.\n- 상아프론테크(-4.33%) 거래량 2.1배.'
HARD = '- 두산퓨얼셀(-4.33%) 하락.'          # 팩에 없는 종목에 수치를 붙였다


class Scripted:
    """C.ask 대역. 순서대로 답하고 받은 프롬프트를 기억한다."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, cl, cfg, system, prompt, usage=None, **kw):
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError('예정보다 많이 불렸다')
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _one(ask):
    with mock.patch.object(CP.C, 'ask', ask):
        return CP._one(None, CFG, [], '수소', 'PROMPT', PACK, UNI, None, lambda *a: None)


class PhrasePath(unittest.TestCase):
    def test_phrase_triggers_exactly_one_more_call_with_the_note(self):
        ask = Scripted(PHRASED, CLEAN)
        txt, err, notes = _one(ask)
        self.assertEqual(txt, CLEAN)
        self.assertIsNone(err)
        self.assertEqual(notes, [])
        self.assertEqual(len(ask.prompts), 2)
        self.assertTrue(ask.prompts[1].startswith('PROMPT'))
        self.assertIn(CP.PHRASE_NOTE.strip(), ask.prompts[1])
        self.assertIn('[phrase]', ask.prompts[1])

    def test_if_it_stays_the_sentence_is_removed_and_noted(self):
        ask = Scripted(PHRASED, PHRASED)
        txt, err, notes = _one(ask)
        self.assertEqual(len(ask.prompts), 2, '금지 문구 재시도는 한 번뿐이다')
        self.assertIsNone(err)
        # 메모는 검수용이라 {note, lost} 로 온다. '금지 문구' 는 우리 검증
        # 절차의 말이라 리포트에 싣지 않고, 종목 사실이 함께 지워진
        # 수(lost)만 결손이 된다.
        self.assertEqual(notes, [dict(note='문장 1개 제거(금지 문구)', lost=0)])
        self.assertNotIn('확인되지 않음', txt)
        self.assertIn('거래대금 478억', txt, '같은 줄의 다른 문장은 남는다')
        self.assertIn('상아프론테크(-4.33%)', txt)
        ok, v = V.check(txt, PACK, UNI)
        self.assertTrue(ok, V.report(v))

    def test_clean_first_try_makes_one_call(self):
        ask = Scripted(CLEAN)
        txt, err, notes = _one(ask)
        self.assertEqual((txt, err, notes), (CLEAN, None, []))
        self.assertEqual(len(ask.prompts), 1)

    def test_hard_violation_keeps_the_old_path(self):
        # 종목명·수치 위반은 retries 만큼 재시도하고 섹션을 뺀다. 지우지 않는다.
        ask = Scripted(HARD, HARD, HARD)
        txt, err, notes = _one(ask)
        self.assertIsNone(txt)
        self.assertIn('두산퓨얼셀', err)
        self.assertEqual(notes, [])
        self.assertEqual(len(ask.prompts), 3)

    def test_hard_then_phrase_then_clean(self):
        # 재시도로 종목명 위반이 사라졌는데 금지 문구가 남았다 → 한 번 더 → 통과
        ask = Scripted(HARD, PHRASED, CLEAN)
        txt, err, notes = _one(ask)
        self.assertEqual(txt, CLEAN)
        self.assertEqual(len(ask.prompts), 3)

    def test_everything_removed_is_a_skip_not_an_empty_section(self):
        only = '- 트리거는 확인되지 않음.'
        ask = Scripted(only, only)
        txt, err, notes = _one(ask)
        self.assertIsNone(txt)
        self.assertIn('남는 문장이 없다', err)


class Assemble(unittest.TestCase):
    def _md(self, notes=None, skipped=None):
        pack = dict(title_date='260921', as_of='2026-09-21', basis='종가', missing=[])
        jobs = [('수소', 'p', {}, '#수소')]
        return CP._assemble(pack, jobs, {'수소': '- 본문'}, skipped or {}, notes)

    def test_검수용_메모는_리포트에_실리지_않는다(self):
        """금지 문구 메모는 우리 검증 절차의 말이라 meta.notes 로만 남는다."""
        md = self._md({'수소': [dict(note='문장 1개 제거(금지 문구)', lost=0)]})
        self.assertNotIn('금지 문구', md)
        self.assertNotIn('빠진 것', md)
        self.assertIn('#수소\n- 본문', md)

    def test_종목_사실이_함께_지워지면_결손으로_올린다(self):
        """문장이 지워지며 사실이 함께 나갔으면 그만큼 리포트에서 빠진 것이다."""
        md = self._md({'수소': [dict(note='문장 2개 제거(금지 문구)', lost=2)]})
        self.assertIn('> - 수소 섹션에서 종목 사실이 담긴 문장 2개가 빠졌습니다', md)
        self.assertNotIn('금지 문구', md)

    def test_섹션_실패는_사실만_적고_사유는_뺀다(self):
        """'생성 실패 — 검증 실패 1건' 은 우리 검증 절차의 말이다."""
        md = self._md(skipped={'장 흐름': '검증 실패 1건'})
        self.assertIn('> - 장 흐름 섹션은 이번 회차에 없습니다', md)
        self.assertNotIn('검증 실패', md)
        self.assertNotIn('생성 실패', md)

    def test_꼬리말에_구현_이야기가_없다(self):
        """board 엔진 운운은 우리가 어떻게 만드는지에 대한 말이다."""
        md = self._md()
        self.assertNotIn('board 엔진', md)
        self.assertNotIn('옮긴 것이다', md)
        self.assertNotIn('기준일 2026-09-21', md)
        # 언제 무엇을 기준으로 한 값인지는 읽는 데 필요하다.
        self.assertIn('2026-09-21 · 종가 기준', md)

    def test_without_notes_nothing_is_added(self):
        pack = dict(title_date='260921', as_of='2026-09-21', basis='종가', missing=[])
        md = CP._assemble(pack, [('수소', 'p', {}, '#수소')], {'수소': '- 본문'}, {})
        self.assertNotIn('빠진 것', md)


if __name__ == '__main__':
    unittest.main()

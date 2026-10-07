#!/usr/bin/env python3
"""일일 노트 → 텔레그램 단위 시험. 계약은 `docs/NOTE.md`.

여기서 지키는 것은 **계약이 약속한 것들**이다. 수치를 보드가 채우는가(D2),
못 찾은 이름에서 멈추는가(D3), 추려내지 않는가(D4), 계산 안 된 값을 0 으로
채우지 않는가(2장 1번), 종목명에 섞인 마크다운 문법 글자.

입력은 `board/notes/2026-09-21.yaml` 을 쓴다 — 계약서의 예시와 같은 파일이라
스키마가 어긋나면 여기서 먼저 깨진다.
[KBJ P1] 그 노트는 사용자가 쓴 개인 노트라 옮기지 않았다. 같은 모양의 합성 노트
`tests/fixtures/note_2026-09-21.yaml`(synthetic_fixtures.py 가 만든다)을 읽는다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.report import note as N              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, 'tests', 'fixtures', 'note_2026-09-21.yaml')   # [KBJ P1] 합성 노트


def _note():
    import yaml
    with open(SAMPLE, encoding='utf-8') as f:
        return yaml.safe_load(f)


def _all_names(note):
    names = [n for t in note.get('themes') or [] for n in t.get('names') or []]
    names += [i['name'] for g in (note.get('picks') or {}).values() for i in g or []]
    return list(dict.fromkeys(names))


def _universe(names, chg=1.5):
    return {'stocks': [{'code': f'{i:06d}', 'name': n, 'chg_pct': chg}
                       for i, n in enumerate(names, 1)]}


class TestGuards(unittest.TestCase):
    """보내지 않고 멈춰야 하는 경우 (D3 · 계약 4장)."""

    def test_date_missing(self):
        msgs, errs = N.note_messages({'verdict': 'x'}, _universe(['삼성전자']))
        self.assertEqual(msgs, [])
        self.assertTrue(any('date' in e for e in errs), errs)

    def test_verdict_missing(self):
        msgs, errs = N.note_messages({'date': '2026-09-21'}, _universe(['삼성전자']))
        self.assertEqual(msgs, [])
        self.assertTrue(any('verdict' in e for e in errs), errs)

    def test_no_universe(self):
        msgs, errs = N.note_messages({'date': '2026-09-21', 'verdict': 'x'}, {})
        self.assertEqual(msgs, [])
        self.assertTrue(any('universe' in e for e in errs), errs)

    def test_unknown_name_blocks_send(self):
        """오타 하나가 조용히 빠지지 않고 발송 자체를 막는다."""
        note = {'date': '2026-09-21', 'verdict': 'x',
                'themes': [{'name': '전선', 'names': ['가온전선', '없는종목이름']}]}
        msgs, errs = N.note_messages(note, _universe(['가온전선']))
        self.assertEqual(msgs, [])
        self.assertTrue(any('없는종목이름' in e for e in errs), errs)

    def test_unknown_name_suggests_rename(self):
        """사명 변경으로 볼 만한 후보를 함께 적는다 — themes.near_names 와 같은 판정."""
        note = {'date': '2026-09-21', 'verdict': 'x',
                'themes': [{'name': 't', 'names': ['한국조선해양']}]}
        _, errs = N.note_messages(note, _universe(['HD한국조선해양']))
        self.assertTrue(any('HD한국조선해양' in e for e in errs), errs)

    def test_unrelated_name_gets_no_candidate(self):
        """글자가 우연히 겹친 것은 후보로 내지 않는다 (2026-08-31 실측)."""
        note = {'date': '2026-09-21', 'verdict': 'x',
                'themes': [{'name': 't', 'names': ['삼화에이스']}]}
        _, errs = N.note_messages(note, _universe(['에이럭스']))
        self.assertTrue(errs)
        self.assertNotIn('에이럭스', errs[0])


class TestBoardFillsNumbers(unittest.TestCase):
    """D2 — 등락률·신고가 라벨은 보드가 채운다."""

    def test_chg_pct_comes_from_universe(self):
        note = {'date': '2026-09-21', 'verdict': 'x',
                'themes': [{'name': '전선', 'names': ['가온전선']}]}
        msgs, errs = N.note_messages(note, _universe(['가온전선'], chg=24.0))
        self.assertEqual(errs, [])
        self.assertIn('가온전선 +24.0%', '\n'.join(msgs))

    def test_null_chg_is_omitted_not_zero(self):
        """계산되지 않은 값을 0 으로 채우지 않는다 (2장 1번). 대신 결손을 적는다."""
        note = {'date': '2026-09-21', 'verdict': 'x',
                'themes': [{'name': '전선', 'names': ['가온전선']}]}
        uni = {'stocks': [{'code': '000001', 'name': '가온전선', 'chg_pct': None}]}
        msgs, errs = N.note_messages(note, uni)
        self.assertEqual(errs, [])
        body = '\n'.join(msgs)
        self.assertNotIn('+0', body)
        self.assertIn('가온전선', body)
        self.assertIn('등락률을 못 채운 종목 1건', msgs[0])

    def test_newhigh_label(self):
        note = {'date': '2026-09-21', 'verdict': 'x',
                'picks': {'w52': [{'name': '비엠티'}]}}
        uni = _universe(['비엠티'], chg=8.0)
        nh = {'achieved': [{'code': '000001', 'hits': {'w52': True, 'd60': True}}]}
        msgs, _ = N.note_messages(note, uni, nh)
        self.assertIn('(52주)', '\n'.join(msgs))

    def test_strongest_hit_only(self):
        """역사적이면 52주·60일도 참이다. 가장 센 것 하나만 적는다."""
        note = {'date': '2026-09-21', 'verdict': 'x',
                'picks': {'w52': [{'name': '비엠티'}]}}
        nh = {'achieved': [{'code': '000001',
                            'hits': {'hist': True, 'w52': True, 'd60': True}}]}
        msgs, _ = N.note_messages(note, _universe(['비엠티']), nh)
        body = '\n'.join(msgs)
        self.assertIn('(역사적)', body)
        self.assertNotIn('52주)', body)


class TestNoCurating(unittest.TestCase):
    """D4 — 사람이 쓴 것을 추려내지 않는다."""

    def test_every_name_and_view_survives(self):
        note = _note()
        names = _all_names(note)
        msgs, errs = N.note_messages(note, _universe(names))
        self.assertEqual(errs, [])
        body = '\n'.join(msgs)
        for n in names:
            self.assertIn(n, body, f'{n} 이 빠졌다')
        for t in note['themes']:
            for v in t.get('view') or []:
                self.assertIn(v.split('.')[0][:20], body, f'판단이 빠졌다: {v}')

    def test_duplicate_name_kept_in_both_sections(self):
        """같은 종목이 두 구획에 나오는 것은 관점이 둘인 것이다 (계약 1장)."""
        note = _note()
        msgs, _ = N.note_messages(note, _universe(_all_names(note)))
        self.assertIn('소프트캠프', msgs[1])      # 보안 테마
        self.assertIn('소프트캠프', msgs[2])      # 급등 포착


class TestShape(unittest.TestCase):
    """계약 3장 — 3통, 기호 셋, 한도."""

    def setUp(self):
        self.note = _note()
        self.msgs, self.errs = N.note_messages(self.note, _universe(_all_names(self.note)))

    def test_three_messages_in_order(self):
        self.assertEqual(self.errs, [])
        self.assertEqual(len(self.msgs), 3)
        self.assertIn('국장 신고가', self.msgs[0])
        self.assertTrue(self.msgs[1].startswith('*테마*'))
        self.assertIn('*52주 신고가*', self.msgs[2])

    def test_verdict_is_second_line(self):
        self.assertEqual(self.msgs[0].splitlines()[1], self.note['verdict'])

    def test_each_message_under_limit(self):
        from board.report.telegram import TG_LIMIT
        for m in self.msgs:
            self.assertLessEqual(len(m), TG_LIMIT, f'{len(m)}자')

    def test_views_use_the_same_mark_as_draft(self):
        self.assertIn('\n  » ', self.msgs[1])

    def test_names_joined_with_middot(self):
        """열 종목이면 열 줄이 된다. 중점으로 묶어 판단이 화면 밖으로 밀리지 않게 한다."""
        line = next(l for l in self.msgs[1].splitlines() if '선도전기' in l)
        self.assertIn(' · ', line)
        self.assertIn('KBI메탈', line)


class TestMarkdownSafety(unittest.TestCase):
    """레거시 Markdown 은 이스케이프가 없다. 종목명의 문법 글자를 중화한다."""

    def test_special_chars_in_name_are_neutralised(self):
        note = {'date': '2026-09-21', 'verdict': 'x',
                'themes': [{'name': 't', 'names': ['미래_에셋*증권']}]}
        msgs, errs = N.note_messages(note, _universe(['미래_에셋*증권']))
        self.assertEqual(errs, [])
        body = msgs[1]
        self.assertNotIn('_에셋', body)
        self.assertNotIn('셋*증', body)

    def test_title_weekday(self):
        self.assertIn('9/21(월)', N._title('2026-09-21'))

    def test_bad_date_kept_verbatim(self):
        self.assertIn('언젠가', N._title('언젠가'))

    def test_yaml_date_object(self):
        """yaml 은 `date: 2026-09-21` 을 datetime.date 로 읽는다.

        문자열로 오지 않는다는 것을 여기서 못 박는다 — run.py 가 이 값을
        state_dir 에 그대로 넘겼다가 date.replace 가 불려 깨졌다(2026-09-21).
        """
        import datetime as dt
        self.assertIn('9/21(월)', N._title(dt.date(2026, 9, 21)))
        note = {'date': dt.date(2026, 9, 21), 'verdict': 'x'}
        msgs, errs = N.note_messages(note, _universe(['가온전선']))
        self.assertEqual(errs, [])
        self.assertIn('9/21(월)', msgs[0])


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""
텔레그램 리포트 단위 시험 — 발송은 못 하니 문자열을 검증한다.

이 환경은 외부 접속이 막혀 있고 토큰도 없다. 그래서 여기서 지키는 것은
발송 결과가 아니라 **발송 전에 깨질 수 있는 것들**이다. 결손 고지, 교차 필터,
계산 안 된 값의 표기, 4,096자 한도, 자격증명 없을 때의 반환값, 종목명에 섞인
마크다운 문법 글자.

입력은 `fixtures/rankings_sample.json` 이 있으면 그걸 쓴다(다른 소비자와 같은
계약을 보기 위해서다). 샘플이 다루지 않는 경계 조건 — cross 행의 `null` 셀,
마크다운 특수문자가 든 종목명 — 은 `fixtures/rankings_tg_sample.json` 으로 본다.
"""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.report import telegram as T          # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def _load(name):
    with open(os.path.join(FIX, name), encoding='utf-8') as f:
        return json.load(f)


# 계약 공용 샘플이 있으면 그것을, 없으면 우리 것을 쓴다.
SAMPLE = _load('rankings_sample.json' if os.path.exists(
    os.path.join(FIX, 'rankings_sample.json')) else 'rankings_tg_sample.json')
EDGE = _load('rankings_tg_sample.json')          # 경계 조건 전용


def _section(msg, head_mark):
    """머리말 다음부터 다음 머리말 전까지. 구획 밖의 종목명과 섞이지 않게 한다."""
    out, on = [], False
    for line in msg.split('\n'):
        if line.startswith('*'):
            on = head_mark in line
            continue
        if on and line.strip():
            out.append(line)
    return out


def _rows(board):
    return [r for r in (board.get('rows') or []) if isinstance(r, dict)]


class TestMissing(unittest.TestCase):
    def test_missing_is_the_very_first_line(self):
        msg = T.rankings_message(SAMPLE)
        first = msg.split('\n')[0]
        self.assertIn('빠진 데이터', first)
        self.assertIn(str(len(SAMPLE['missing'])), first)

    def test_no_missing_no_line(self):
        r = copy.deepcopy(SAMPLE)
        r['missing'] = []
        self.assertNotIn('빠진 데이터', T.rankings_message(r))

    def test_missing_stays_one_line(self):
        r = copy.deepcopy(SAMPLE)
        r['missing'] = [f'결손 항목 {i} — 사유가 길게 붙는다' * 3 for i in range(9)]
        msg = T.rankings_message(r)
        self.assertIn('빠진 데이터 9건', msg.split('\n')[0])
        self.assertIn('외 6건', msg.split('\n')[0])      # 이름은 3건까지만


class TestSectors(unittest.TestCase):
    def setUp(self):
        self.msg = T.rankings_message(SAMPLE)
        self.lines = _section(self.msg, '섹터')
        self.board = SAMPLE['sector_boards'][0]

    def test_top_and_bottom_only(self):
        secs = self.board['sectors']
        self.assertEqual(len(self.lines), T.SECTOR_TOP_N + T.SECTOR_BOTTOM_N)
        self.assertIn(secs[0]['name'], self.lines[0])
        self.assertIn(secs[-1]['name'], self.lines[-1])
        # 6등부터 하위권 직전까지는 안 나온다
        mid = secs[T.SECTOR_TOP_N:-T.SECTOR_BOTTOM_N]
        for s in mid:
            self.assertNotIn(f' {s["name"]} ', '\n'.join(self.lines))

    def test_leader_stock_is_attached(self):
        top = self.board['sectors'][0]['top'][0]
        self.assertIn(top['name'], self.lines[0])
        self.assertIn(top['ret'], self.lines[0])
        # 1등만 곁들인다. 2등은 대시보드 몫이다
        if len(self.board['sectors'][0]['top']) > 1:
            self.assertNotIn(self.board['sectors'][0]['top'][1]['name'], self.lines[0])

    def test_weighting_is_shown(self):
        # 계약이 '화면에 표기해야 한다'고 못 박은 값
        self.assertIn(T.WEIGHTING_KO[self.board['weighting']], self.msg)

    def test_first_sector_board_wins(self):
        # 보드가 여럿이어도 텔레그램은 1d 하나만 쓴다
        b1d = [b for b in SAMPLE['sector_boards'] if b.get('key') == '1d'][0]
        self.assertIn(b1d['sectors'][0]['name'], self.lines[0])

    def test_null_sector_return_is_dash(self):
        msg = T.rankings_message(EDGE)
        line = [x for x in _section(msg, '섹터') if '삼성중공업' in x][0]
        self.assertIn(T.DASH, line)


class TestCrossOnly(unittest.TestCase):
    def setUp(self):
        self.msg = T.rankings_message(SAMPLE)
        self.lines = '\n'.join(_section(self.msg, '교차 시그널'))

    def test_cross_codes_appear(self):
        by_code = {}
        for b in SAMPLE['stock_boards']:
            for r in _rows(b):
                by_code[str(r['code'])] = r['name']
        for code in SAMPLE['cross_codes'][:T.CROSS_MAX]:
            self.assertIn(by_code[code], self.lines, code)

    def test_non_cross_stocks_are_absent(self):
        cross = set(SAMPLE['cross_codes'])
        for b in SAMPLE['stock_boards']:
            for r in _rows(b):
                if str(r['code']) not in cross:
                    self.assertNotIn(r['name'], self.lines, r['name'])

    def test_both_boards_sort_values_are_merged(self):
        keys = [b['sort_by'] for b in SAMPLE['stock_boards']]
        code = SAMPLE['cross_codes'][0]
        row = {str(r['code']): r for r in _rows(SAMPLE['stock_boards'][0])}[code]
        line = [x for x in self.lines.split('\n') if row['name'] in x][0]
        for b, k in zip(SAMPLE['stock_boards'], keys):
            r = {str(x['code']): x for x in _rows(b)}[code]
            self.assertIn(r['cells'][k], line)          # 표시용 문자열 그대로

    def test_empty_cross_says_so(self):
        r = copy.deepcopy(SAMPLE)
        r['cross_codes'] = []
        self.assertIn('겹치는 종목 없음', T.rankings_message(r))


class TestNulls(unittest.TestCase):
    """계산되지 않은 값은 0 이 아니다 (CLAUDE.md 2장 1번)."""

    def setUp(self):
        self.msg = T.rankings_message(EDGE)
        self.lines = _section(self.msg, '교차 시그널')

    def test_null_cell_is_dash_not_zero(self):
        line = [x for x in self.lines if '네오이뮨텍' in x][0]   # vol_3d_1m 이 null
        self.assertIn(T.DASH, line)
        self.assertNotIn('0%', line)
        self.assertNotIn(' 0 ', line)

    def test_raw_null_wins_over_display_string(self):
        # 제닉은 cells 에 '0.00%' 가 있지만 cells_raw 가 null 이다. 계산된 적이 없다
        line = [x for x in self.lines if '제닉' in x][0]
        self.assertIn(T.DASH, line)
        self.assertNotIn('0.00%', line)


class TestMarkdown(unittest.TestCase):
    def test_special_chars_in_name_do_not_leak(self):
        msg = T.rankings_message(EDGE)
        line = [x for x in _section(msg, '교차 시그널') if '2차전지' in x][0]
        for ch in '_*[]`':
            self.assertNotIn(ch, line, ch)
        self.assertIn('합성', line)                     # 이름 자체는 남아 있다

    def test_bold_markers_stay_balanced(self):
        msg = T.rankings_message(EDGE)
        self.assertEqual(msg.count('*') % 2, 0)

    def test_safe_keeps_length(self):
        self.assertEqual(len(T._safe('KODEX 200*_[합성]`')), len('KODEX 200*_[합성]`'))


class TestLimit(unittest.TestCase):
    def _bulky(self):
        r = copy.deepcopy(EDGE)
        for s in r['sector_boards'][0]['sectors']:
            s['name'] = s['name'] + '가' * 400
        for b in r['stock_boards']:
            for row in b['rows']:
                row['name'] = row['name'] + '나' * 400
        return r

    def test_under_limit_has_no_note(self):
        msg = T.rankings_message(SAMPLE)
        self.assertLessEqual(len(msg), T.TG_LIMIT)
        self.assertNotIn('생략', msg.split('\n')[-1])

    def test_over_limit_reports_what_was_dropped(self):
        msg = T.rankings_message(self._bulky())
        last = msg.split('\n')[-1]
        self.assertLessEqual(len(msg), T.TG_LIMIT)
        self.assertIn('생략', last)
        self.assertRegex(last, r'(섹터|종목|줄) \d+건')

    def test_sectors_are_dropped_before_cross_stocks(self):
        msg = T.rankings_message(self._bulky())
        self.assertIn('교차 시그널', msg)               # 텔레그램의 존재 이유는 남긴다
        self.assertIn('섹터', msg.split('\n')[-1])

    def test_cross_overflow_is_counted(self):
        r = copy.deepcopy(EDGE)
        board = r['stock_boards'][0]
        rows, codes = board['rows'], list(r['cross_codes'])
        for i in range(T.CROSS_MAX + 5):
            code = f'9{i:05d}'
            row = dict(copy.deepcopy(rows[1]), code=code, name=f'가짜{i}', rank=20 + i)
            rows.append(row)
            r['stock_boards'][1]['rows'].append(copy.deepcopy(row))
            codes.append(code)
        r['cross_codes'] = codes
        msg = T.rankings_message(r)
        self.assertIn(f'외 {len(codes) - T.CROSS_MAX}종목', msg)


class TestSplit(unittest.TestCase):
    def test_chunks_cut_at_line_boundaries(self):
        lines = [f'{i}번째 줄 ' + '가' * 40 for i in range(200)]
        chunks = T._split('\n'.join(lines), 500)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 500)
        self.assertEqual('\n'.join(chunks).split('\n'), lines)   # 문장 중간에서 안 자른다

    def test_single_overlong_line_is_not_lost(self):
        chunks = T._split('가' * 130, 50)
        self.assertEqual(''.join(chunks), '가' * 130)


class TestSend(unittest.TestCase):
    """발송 실패가 파이프라인을 멈추면 안 된다. 예외 대신 (False, 사유)."""

    def setUp(self):
        self._orig = T._cred
        T._cred = lambda name: ''      # 자격증명이 있는 환경에서도 같은 결과를 봐야 한다

    def tearDown(self):
        T._cred = self._orig

    def test_send_without_token(self):
        ok, why = T.send('본문')
        self.assertFalse(ok)
        self.assertIn('TELEGRAM_BOT_TOKEN', why)

    def test_send_without_chat_id(self):
        ok, why = T.send('본문', token='1234:abcd')
        self.assertFalse(ok)
        self.assertIn('TELEGRAM_CHAT_ID', why)

    def test_send_with_empty_body(self):
        ok, why = T.send('   ', token='1234:abcd', chat_id='-100')
        self.assertFalse(ok)
        self.assertIn('본문', why)

    def test_check_without_token(self):
        ok, why = T.check()
        self.assertFalse(ok)
        self.assertIn('TELEGRAM_BOT_TOKEN', why)

    def test_check_reports_missing_chat_id_as_failure(self):
        ok, why = T.check(token='')
        self.assertFalse(ok)
        self.assertIsInstance(why, str)


class TestDraft(unittest.TestCase):
    MD = ('#260826_신고가 및 등락률 Top 랭킹 코멘트\n'
          '\n'
          '- 코스피 6,808.21(+0.97%)로 마감\n'
          '» 한전기술 — 거래량 8.2배\n'
          '\n'
          '**전일 주장 유지**. `python3 -m board.run --write` 로 생성함\n'
          '\n'
          '| 테마 | 등락률 | 신고가 | 거래대금 |\n'
          '|---|---:|---:|---:|\n'
          '| 철강 | +15.07% | 2 | 1,234억 |\n'
          '| 로봇 | +2.31% | – | 987억 |\n')

    def setUp(self):
        self.msg = T.draft_message(self.MD, url='https://example.com/board/')
        self.lines = self.msg.split('\n')

    def test_heading_becomes_bold_and_underscore_is_neutralised(self):
        first = self.msg.split('\n')[0]
        self.assertTrue(first.startswith('*') and first.endswith('*'))
        self.assertNotIn('_', first)
        self.assertIn('신고가', first)

    def test_bullets_and_bold_are_converted(self):
        self.assertIn('• 코스피 6,808.21(+0.97%)로 마감', self.msg)
        self.assertNotIn('**', self.msg)
        self.assertIn('*전일 주장 유지*', self.msg)

    # 표는 버리지 않고 행마다 한 줄로 푼다. 첫 두 칸은 그대로, 셋째부터는 열 이름을
    # 앞에 붙인다 — 헤더가 없어지면 '2' 가 무엇의 2인지 알 수 없다.
    def test_table_rows_survive_as_lines_with_column_names(self):
        self.assertIn('• 철강 +15.07% · 신고가 2 · 거래대금 1,234억', self.lines)

    def test_table_rule_is_not_a_line(self):
        for ln in self.lines:
            self.assertNotIn('---', ln)
            self.assertNotIn('|', ln)

    def test_no_dropped_table_notice(self):
        self.assertNotIn('뺐다', self.msg)
        self.assertNotIn('표 2행', self.msg)

    # '–' 는 계산되지 않은 값의 표식이라 지우지 않는다. 표에서 보이던 '없다' 가 줄에서
    # 사라지면 그 열이 원래 없던 표와 구분이 안 된다 (CLAUDE.md 2장 6번)
    def test_dash_cell_is_kept_with_its_column_name(self):
        self.assertIn('• 로봇 +2.31% · 신고가 – · 거래대금 987억', self.lines)

    def test_em_dash_and_hyphen_are_kept_like_en_dash(self):
        md = '| 테마 | 등락률 | 신고가 |\n|---|---:|---:|\n| 로봇 | +1% | — |\n| 철강 | - | 2 |\n'
        lines = T.draft_message(md).split('\n')
        self.assertIn('• 로봇 +1% · 신고가 —', lines)
        self.assertIn('• 철강 - · 신고가 2', lines)

    def test_empty_cell_is_omitted(self):
        # 빈 칸은 표식이 아니다. 열 이름만 붙여 줄을 늘릴 이유가 없다
        md = '| 테마 | 등락률 | 신고가 | 거래대금 |\n|---|---:|---:|---:|\n| 로봇 | +2.31% | | 987억 |\n'
        self.assertIn('• 로봇 +2.31% · 거래대금 987억', T.draft_message(md).split('\n'))

    def test_headless_table_joins_values_only(self):
        msg = T.draft_message('| 철강 | +15.07% | 2 | 1,234억 |\n| 로봇 | +2.31% | - | |')
        lines = msg.split('\n')
        self.assertIn('• 철강 +15.07% · 2 · 1,234억', lines)
        self.assertIn('• 로봇 +2.31% · -', lines)

    # 이름 옆에 이름 없이 붙는 건 등락률뿐이다. 둘째 칸이 신고가 종목수면 '2' 만
    # 내보낼 수 없다 — 순위인지 종목수인지 모른다
    def test_second_column_that_is_not_pct_gets_its_name(self):
        md = '| 테마 | 신고가 | 거래대금 |\n|---|---:|---:|\n| 철강 | 2 | 1,234억 |\n'
        self.assertIn('• 철강 · 신고가 2 · 거래대금 1,234억', T.draft_message(md).split('\n'))
        md = '| 순위 | 테마 | 등락률 |\n|---|---|---:|\n| 1 | 철강 | +15% |\n'
        self.assertIn('• 1 · 테마 철강 · 등락률 +15%', T.draft_message(md).split('\n'))

    # 빈 줄 없이 붙은 두 표. 다음 줄이 구분선이면 그 줄은 새 표의 헤더다 — 앞 표의
    # 열 이름을 단 데이터 행으로 나가면 뒤 행에 '신고가 8.2배' 같은 틀린 라벨이 붙는다
    def test_tables_split_at_header_even_without_blank_line(self):
        md = ('| 테마 | 등락률 | 신고가 |\n|---|---:|---:|\n| 로봇 | +2% | 5 |\n'
              '| 종목 | 등락률 | 거래량 |\n|---|---:|---:|\n| 한전기술 | +3% | 8.2배 |\n')
        msg = T.draft_message(md)
        lines = msg.split('\n')
        self.assertIn('• 로봇 +2% · 신고가 5', lines)
        self.assertIn('• 한전기술 +3% · 거래량 8.2배', lines)
        self.assertNotIn('• 종목 등락률 · 신고가 거래량', lines)
        self.assertNotIn('신고가 8.2배', msg)

    def test_rule_without_leading_pipe_still_marks_header(self):
        # GFM 은 양 끝 파이프를 요구하지 않는다
        md = '| 테마 | 등락률 | 신고가 |\n--- | ---: | ---:\n| 철강 | +1% | 2 |\n'
        lines = T.draft_message(md).split('\n')
        self.assertIn('• 철강 +1% · 신고가 2', lines)
        self.assertNotIn('• 테마 등락률 · 신고가', lines)
        self.assertFalse(any('---' in ln for ln in lines))

    def test_indented_rule_is_not_a_line(self):
        md = '| 테마 | 등락률 |\n  |---|---:|\n  | 철강 | +1% |\n'
        lines = T.draft_message(md).split('\n')
        self.assertEqual(lines[0], '• 철강 +1%')
        for ln in lines:
            self.assertNotIn('|', ln)

    def test_lone_pipe_is_not_a_rule(self):
        # `|` 한 줄을 구분선으로 읽으면 앞 행이 헤더로 삼켜져 한 행이 조용히 빠진다
        md = '| 철강 | +1% | 2 |\n|\n| 로봇 | +2% | 5 |\n'
        lines = T.draft_message(md).split('\n')
        self.assertIn('• 철강 +1% · 2', lines)
        self.assertIn('• 로봇 +2% · 5', lines)

    def test_wide_row_is_noted_not_silently_unlabeled(self):
        md = ('| 테마 | 등락률 |\n|---|---:|\n| 철강 | +1% | 2 | 1,234억 |\n'
              '| 로봇 | +2% |\n\n본문\n')
        lines = T.draft_message(md).split('\n')
        self.assertIn('• 철강 +1% · 2 · 1,234억', lines)
        self.assertIn('• 로봇 +2%', lines)          # 칸이 모자란 행은 빈 칸일 뿐이다
        note = [ln for ln in lines if '칸이 많다' in ln]
        self.assertEqual(len(note), 1)
        self.assertIn('1행', note[0])
        self.assertLess(lines.index(note[0]), lines.index('본문'))

    def test_escaped_pipe_stays_inside_the_cell(self):
        md = '| 테마 | 등락률 |\n|---|---:|\n| A\\|B | +1% |\n'
        self.assertIn('• A|B +1%', T.draft_message(md).split('\n'))

    def test_prose_with_a_pipe_is_not_a_table(self):
        msg = T.draft_message('코스피 | 코스닥 나란히 마감\n')
        self.assertIn('코스피 | 코스닥 나란히 마감', msg.split('\n'))

    def test_column_names_do_not_leak_into_next_table(self):
        md = ('| 테마 | 등락률 | 신고가 |\n|---|---:|---:|\n| 철강 | +1% | 2 |\n'
              '\n본문 한 줄\n\n'
              '| 종목 | 등락률 | 거래량 |\n|---|---:|---:|\n| 한전기술 | +3% | 8.2배 |\n'
              '\n'
              '| 로봇 | +2% | 5 |\n')
        lines = T.draft_message(md).split('\n')
        self.assertIn('• 철강 +1% · 신고가 2', lines)
        self.assertIn('• 한전기술 +3% · 거래량 8.2배', lines)
        self.assertIn('• 로봇 +2% · 5', lines)       # 헤더가 없는 세 번째 표

    def test_table_cells_are_parser_safe(self):
        md = '| 테마 | 등락률 |\n|---|---:|\n| SK_하이닉스 [반도체] | +1% |\n'
        msg = T.draft_message(md)
        self.assertIn('SK＿하이닉스 ［반도체］ +1%', msg)
        self.assertNotIn('_', msg)

    def test_link_is_appended(self):
        self.assertIn('(https://example.com/board/)', self.msg)

    def test_empty_draft_does_not_explode(self):
        self.assertIsInstance(T.draft_message(''), str)

    def test_long_draft_is_not_truncated(self):
        # 초안은 검수 대상이라 버리지 않는다. 넘치는 건 send() 가 나눠 보낸다
        md = '\n'.join(f'- {i}번째 문단 ' + '가' * 60 for i in range(120))
        msg = T.draft_message(md)
        self.assertGreater(len(msg), T.TG_LIMIT)
        self.assertNotIn('생략', msg)
        self.assertIn('119번째 문단', msg)
        chunks = T._split(msg)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), T.TG_LIMIT)


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestCheckDestination(unittest.TestCase):
    """봇이 살아 있는 것과 그 방에 보낼 수 있는 것은 다르다.

    봇이 방에서 쫓겨났거나 chat_id 가 틀리면 getMe 는 통과하고 발송만 실패한다.
    18시에 조용히 실패하지 않으려면 점검에서 목적지까지 봐야 한다.
    """

    def setUp(self):
        import requests
        self.real = requests.get
        self.calls = []

    def tearDown(self):
        import requests
        requests.get = self.real

    def _serve(self, me=200, chat=200, chat_body=None):
        import requests

        class R:
            def __init__(self, code, body):
                self.status_code, self._b = code, body
                self.text = str(body)

            def json(self):
                return self._b

        def fake(url, params=None, timeout=None):
            self.calls.append(url.rsplit('/', 1)[-1])
            if url.endswith('getMe'):
                return R(me, {'result': {'username': 'board_bot'}})
            return R(chat, chat_body or {'result': {'title': '리서치방'}})
        requests.get = fake

    def test_봇과_방을_모두_확인한다(self):
        self._serve()
        ok, why = T.check(token='t', chat_id='1')
        self.assertTrue(ok)
        self.assertIn('board_bot', why)
        self.assertIn('리서치방', why)
        self.assertEqual(self.calls, ['getMe', 'getChat'])

    def test_방이_없으면_실패다(self):
        self._serve(chat=400)
        ok, why = T.check(token='t', chat_id='1')
        self.assertFalse(ok)
        self.assertIn('대화방', why)

    def test_메시지를_보내지_않는다(self):
        """점검이 발송으로 새면 안 된다."""
        self._serve()
        T.check(token='t', chat_id='1')
        self.assertNotIn('sendMessage', self.calls)


class TestPlainTextMode(unittest.TestCase):
    """미국장 브리프는 평문으로 보낸다.

    브리프 본문에는 `**유니버스를 …**` 와 `—` 가 섞여 있다. Markdown 으로 보내면
    텔레그램이 '엔티티가 안 닫혔다' 며 400 으로 거절한다. 국장 리포트는 우리가
    서식을 붙여 만들므로 기본값은 Markdown 그대로여야 한다.
    """
    def _capture(self, **kw):
        import requests
        sent = {}

        class R:
            status_code = 200

            @staticmethod
            def json():
                return {'ok': True}

        def fake(url, data=None, timeout=None, **_):
            sent.update(data or {})
            return R()

        real = requests.post
        requests.post = fake
        try:
            ok, why = T.send('본문 **굵게** — 대시', token='t', chat_id='c', **kw)
        finally:
            requests.post = real
        return ok, sent

    def test_plain_mode_sends_no_parse_mode(self):
        ok, sent = self._capture(parse_mode=None)
        self.assertTrue(ok)
        self.assertNotIn('parse_mode', sent)

    def test_default_is_still_markdown(self):
        ok, sent = self._capture()
        self.assertTrue(ok)
        self.assertEqual(sent.get('parse_mode'), 'Markdown')


def _nh_row(code, name, close=None, high=None, **kw):
    return dict(code=code, name=name, close_basis=dict(label=close),
                high_basis=dict(label=high), **kw)


class TestTriggerLines(unittest.TestCase):
    """52주 이상 신고가 줄 아래의 재료 줄 (D-085).

    링크 없음 · 종목당 두 줄 · 길이 초과 시 뒤 종목의 재료 줄부터 · 결손 첫 줄 합류 ·
    파일 없음은 '수집되지 않음(단계 실패)'.
    """
    RANK = dict(as_of='2026-09-21', sector_boards=[], stock_boards=[], cross_codes=[], missing=[])
    NH = dict(as_of='2026-09-21', labels=dict(hist='역사적', w52='52주', d60='60일'), achieved=[
        _nh_row('029460', '케이씨', close='w52', high='w52', chg_pct=5.43, vol_mult=1.3),
        _nh_row('086670', '비엠티', close='hist', high='hist', chg_pct=2.0, vol_mult=0.9),
        _nh_row('000500', '가온전선', close='hist', high='d60', chg_pct=-0.31, vol_mult=1.0),
    ])
    LONG = '케이씨 _반도체_ 장비 *대형* 수주 ' + '아' * 80
    TRIG = dict(source='triggers', as_of='2026-09-21',
                collected_at='2026-09-21T17:05:00+09:00', title_chars_tg=60, by_code={
                    '029460': dict(code='029460', name='케이씨', items=[
                        dict(title='단일판매ㆍ공급계약체결', link='https://dart.fss.or.kr/x?rcpNo=1',
                             publisher='DART', published_at='2026-09-21', source='dart',
                             kind='contract', matched_by='corp_code'),
                        dict(title=LONG, link='https://mk.co.kr/1', publisher='매일경제',
                             published_at='2026-09-21T09:10+09:00', source='naver_news',
                             matched_by='title'),
                        dict(title='셋째 재료는 화면에만', link='https://g/3', publisher='연합뉴스',
                             published_at='2026-09-21', source='google_news', matched_by='title')]),
                    '086670': dict(code='086670', name='비엠티', items=[
                        dict(title='비엠티 신고가 갱신 대박', link='https://x.com/a/status/1',
                             publisher='@acct', published_at='2026-09-21T08:00:00+09:00',
                             source='telegram_x', matched_by='name')]),
                    '000500': dict(code='000500', name='가온전선', items=[])},
                sources=dict(dart=dict(ok=3, failed=0, skipped=0, cut=None)),
                inbox_hours=24,
                missing=['X 포워딩 인박스 없음 — 봇에 게시물을 공유하면 붙는다'])

    def msg(self, triggers='default'):
        tr = self.TRIG if triggers == 'default' else triggers
        return T.rankings_message(self.RANK, newhigh=self.NH, triggers=tr)

    def _after(self, msg, name):
        lines = msg.split('\n')
        i = next(i for i, x in enumerate(lines) if x.startswith('· ' + name))
        out = []
        for x in lines[i + 1:]:
            if not x.startswith('  ↳'):
                break
            out.append(x)
        return out

    def test_three_source_formats(self):
        kc = self._after(self.msg(), '케이씨')
        self.assertEqual(kc[0], '  ↳ 공시: 단일판매ㆍ공급계약체결 (DART · 9/21)')
        self.assertTrue(kc[1].startswith('  ↳ 재료: 케이씨 ＿반도체＿ 장비 ＊대형＊ 수주 '), kc[1])
        self.assertTrue(kc[1].endswith('… (매일경제 · 9/21)'), kc[1])
        bm = self._after(self.msg(), '비엠티')
        self.assertEqual(bm, ['  ↳ X: 비엠티 신고가 갱신 대박 (@acct · 9/21)'])

    def test_title_is_cut_to_the_configured_length(self):
        kc = self._after(self.msg(), '케이씨')
        title = kc[1][len('  ↳ 재료: '):kc[1].rindex(' (')]
        self.assertEqual(len(title), 60)
        self.assertTrue(title.endswith('…'))

    def test_two_lines_per_stock_at_most(self):
        m = self.msg()
        self.assertEqual(len(self._after(m, '케이씨')), 2)
        self.assertNotIn('셋째 재료는 화면에만', m)

    def test_no_links_in_the_message(self):
        m = self.msg()
        self.assertNotIn('http', m)
        self.assertNotIn('dart.fss.or.kr', m)
        self.assertNotIn('[', m)

    def test_stock_without_items_has_no_trigger_line(self):
        self.assertEqual(self._after(self.msg(), '가온전선'), [])

    def _head(self, triggers='default'):
        return next(x for x in self.msg(triggers).split('\n') if x.startswith('*52주 이상 신고가'))

    def test_head_explains_the_lines_and_the_disclosure_cutoff(self):
        head = self._head()
        # X 포워딩은 인박스 창(24시간)이라 '당일' 이 아니다 — 머리가 범위를 맞게 말한다
        self.assertIn('재료는 당일 기사·공시 · X 포워딩은 최근 24시간', head)
        self.assertIn('공시는 17:05 접수분까지', head)

    def test_disclosure_cutoff_only_when_dart_actually_ran(self):
        # DART 를 접은 날(키 없음)에 '접수분까지' 가 서면 결손 줄과 모순된다.
        tr = dict(self.TRIG, sources=dict(dart=dict(ok=0, failed=0, skipped=0,
                                                    cut='DART_API_KEY 없음')),
                  missing=['DART 종목 공시 — DART_API_KEY 없음'])
        head = self._head(tr)
        self.assertIn('재료는 당일 기사·공시', head)
        self.assertNotIn('접수분까지', head)
        tr = dict(self.TRIG, sources={})               # 옛 파일 — sources 가 비어 있다
        self.assertNotIn('접수분까지', self._head(tr))
        tr = dict(self.TRIG, inbox_hours=None)
        self.assertIn('X 포워딩', self._head(tr))
        self.assertNotIn('최근 None', self._head(tr))

    def test_failed_file_gets_no_trigger_head_and_no_lines(self):
        # 단계가 통째로 죽은 파일. 재료를 모은 적이 없으니 '재료는 …' 도 '접수분까지' 도 없다.
        from board.ingest import triggers as TR
        tr = TR.failed('2026-09-21', {}, RuntimeError('newhigh.json 이 없다'))
        m = self.msg(tr)
        self.assertIn('재료: 수집되지 않음(단계 실패', m.split('\n')[0])
        head = self._head(tr)
        self.assertNotIn('재료는', head)
        self.assertNotIn('접수분까지', head)
        self.assertNotIn('↳ 공시', m)

    def test_trigger_missing_joins_the_first_line(self):
        first = self.msg().split('\n')[0]
        self.assertIn('빠진 데이터 1건', first)
        self.assertIn('X 포워딩 인박스 없음', first)

    def test_absent_file_is_said_as_a_stage_failure(self):
        m = self.msg(triggers=None)
        first = m.split('\n')[0]
        self.assertIn('재료: 수집되지 않음(단계 실패)', first)
        self.assertNotIn('재료는 당일', m)
        self.assertNotIn('↳ 재료', m)

    def test_absent_standin_behaves_like_no_file(self):
        from board.ingest import triggers as TR
        self.assertEqual(T.TRIGGER_ABSENT_LINE, TR.ABSENT_LINE)
        m = self.msg(triggers=TR.absent('2026-09-21'))
        self.assertIn('재료: 수집되지 않음(단계 실패)', m.split('\n')[0])
        self.assertNotIn('재료는 당일', m)

    def test_no_newhigh_means_no_trigger_missing_line(self):
        # 신고가를 싣지 않는 호출(랭킹만)에는 재료 결손을 붙일 이유가 없다.
        self.assertNotIn('수집되지 않음', T.rankings_message(self.RANK))

    def test_trigger_lines_are_dropped_first_from_the_last_stock(self):
        g = T._newhigh_group(self.NH, None, triggers=self.TRIG)
        # 거래량 배수 순: 케이씨(1.3) · 가온전선(1.0) · 비엠티(0.9). 뒤 종목의 재료부터.
        self.assertEqual(T._drop_one(g), '재료')
        self.assertEqual(g['items'][2]['trig'], [])            # 비엠티 X 줄
        self.assertEqual(T._drop_one(g), '재료')
        self.assertEqual(len(g['items'][0]['trig']), 1)         # 케이씨 둘째 재료
        self.assertEqual(T._drop_one(g), '재료')
        self.assertEqual(g['items'][0]['trig'], [])
        self.assertEqual(T._drop_one(g), '신고가')               # 그제야 종목 하나
        self.assertEqual(len(g['items']), 2)
        self.assertTrue(all(isinstance(e, dict) and e['lines'] for e in g['items']))

    def test_fit_reports_dropped_trigger_lines(self):
        g = T._newhigh_group(self.NH, None, triggers=self.TRIG)
        full = T._fit([], [g], [], limit=10 ** 6)
        got = T._fit([], [T._newhigh_group(self.NH, None, triggers=self.TRIG)], [],
                     limit=len(full) - 1)
        # 생략 안내 줄이 붙으므로 한 줄로는 모자라 두 줄이 빠진다. 무엇이 몇 건인지 적힌다.
        self.assertRegex(got, r'길이 제한으로 재료 \d건 생략')
        self.assertNotIn('신고가 1건', got)                      # 종목은 하나도 안 뺐다
        self.assertNotIn('↳ X:', got)                            # 뒤 종목의 재료가 먼저
        self.assertIn('· 비엠티', got)                           # 종목 줄은 남는다
        self.assertIn('↳ 공시:', got)                            # 앞 종목의 첫 재료는 마지막까지

    def test_trigger_lines_go_before_any_other_group(self):
        # drop_rank 순이면 섹터(0)·교차(1) 구획이 신고가(2) 구획의 재료 줄보다 먼저 빠진다.
        # 재료는 대시보드에도 있고 랭킹은 이 메시지의 본체다 — 재료부터 뺀다.
        def groups():
            return [T._newhigh_group(self.NH, None, triggers=self.TRIG),
                    T._sector_group(SAMPLE), T._cross_group(SAMPLE)]
        full = T._fit([], groups(), [], limit=10 ** 6)
        n_trig = sum(len(e['trig']) for e in groups()[0]['items'] if isinstance(e, dict))
        self.assertEqual(n_trig, 3)
        got = T._fit([], groups(), [], limit=len(full) - 1)
        last = got.split('\n')[-1]
        self.assertRegex(last, r'길이 제한으로 재료 \d건 생략')
        self.assertNotIn('섹터', last)
        self.assertNotIn('종목', last)
        for s in SAMPLE['sector_boards'][0]['sectors'][:T.SECTOR_TOP_N]:
            self.assertIn(s['name'], got)
        # 재료가 다 빠진 뒤에야 drop_rank 순서 — 섹터부터
        tiny = T._fit([], groups(), [], limit=len(full) // 2)
        self.assertRegex(tiny.split('\n')[-1], r'재료 3건')
        self.assertIn('섹터', tiny.split('\n')[-1])

    def test_omit_line_names_where_the_whole_actually_is(self):
        # 재료는 엑셀에 열이 없다 — '엑셀에 있다' 고 적으면 찾다가 끝난다.
        self.assertEqual(T._omit_line({'재료': 2}), '— 길이 제한으로 재료 2건 생략. 재료 전체는 대시보드에 있다')
        self.assertIn('전체는 대시보드와 엑셀에 있다', T._omit_line({'섹터': 1}))
        mixed = T._omit_line({'재료': 3, '섹터': 1})
        self.assertIn('재료 3건 · 섹터 1건', mixed)
        self.assertIn('섹터·종목은 엑셀에도', mixed)

    def test_newhigh_line_with_triggers_returns_all_lines(self):
        labels = self.NH['labels']
        lines = T._newhigh_line(self.NH['achieved'][0], None, labels, triggers=self.TRIG)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith('· 케이씨 52주'))
        self.assertTrue(lines[1].startswith('  ↳ 공시:'))

    def test_md_and_cut_helpers(self):
        self.assertEqual(T._md('2026-09-21T09:10+09:00'), '9/21')
        self.assertEqual(T._md('2026-12-05'), '12/5')
        self.assertIsNone(T._md(None))
        self.assertIsNone(T._md('어제'))
        self.assertEqual(T._cut('a' * 10, 5), 'aaaa…')
        self.assertEqual(T._cut('  두  칸 ', 60), '두 칸')

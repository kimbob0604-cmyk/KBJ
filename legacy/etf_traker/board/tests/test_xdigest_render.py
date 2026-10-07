#!/usr/bin/env python3
"""
X 다이제스트 렌더·분할·발송 단위 시험 (XDIGEST.md 7장 T4·T5).

이 환경은 외부 접속이 막혀 있어 발송은 못 한다. 그래서 지키는 것은 발송 결과가
아니라 **발송 전에 깨질 수 있는 것들**이다 — 구획·기호·순서, 4,096자 경계,
빈 구획의 사유 줄, 평문 발송, 중간 실패 뒤의 `sent.json`.

기준은 `docs/XDIGEST.md` 9장 예시다. 픽스처 셋이 그것을 옮긴 것이다.

  `fixtures/xdigest_render_facts.json`  검증(3-3)을 통과한 `facts.json` 모양.
    소주제와 단독 소식은 **거꾸로** 들어 있다 — 1장 정렬이 실제로 도는지
    보려면 입력 순서가 기대 순서와 달라야 한다.
  `fixtures/xdigest_render_posts.json`  블루스카이 게시물 156건. 본문은 합성이다
    (T4 가 보는 것은 렌더이고, 본문 대조는 검증(3-3)의 일이다). 근거로 인용되지
    않은 106건은 2행 '수집 156건' 을 코드가 세게 하려고 채운 것이고, '분석 68건'
    은 `counts.analyzed`(themes.json 이 센 값)에서 온다 — 검증에서 빠진 줄이
    있어 인용된 게시물 수와 같지 않다.
  `fixtures/xdigest_render_expected.txt`  기대 본문. 예시에 7장 T4 의 허용 차이
    목록을 적용한 것이고, 이 파일과의 diff 가 0 이어야 한다.

`test_example_diff_is_zero` 는 그 기대 파일을 믿지 않고 `XDIGEST.md` 9장 원문을
직접 읽어, 허용 차이만 되돌린 뒤 줄 단위로 대조한다. 기대 파일이 렌더 결과를
그대로 떠 온 것이라 그것만으로는 계약을 지키는지 알 수 없어서다.
"""
import json
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.xdigest import render as R          # noqa: E402
from board.xdigest import send as S            # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'docs', 'XDIGEST.md')
KST = timezone(timedelta(hours=9))
NAMES = {'한미반도체': '042700'}                 # 별칭 사전(3-3 4번)에서 오는 것


def _load(name):
    with open(os.path.join(FIX, name), encoding='utf-8') as f:
        return json.load(f)


FACTS = _load('xdigest_render_facts.json')
POSTS = _load('xdigest_render_posts.json')
with open(os.path.join(FIX, 'xdigest_render_expected.txt'), encoding='utf-8') as f:
    EXPECTED = f.read().rstrip('\n').split('\n')


def _digest(facts=None, posts=None, cfg=None):
    return R.compose(facts or FACTS, posts or POSTS, cfg or {}, names=NAMES)


def _post(pid, account, posted_at, url=None):
    return {'id': pid, 'account': account, 'text': f'[시험] {pid}',
            'url': url or f'https://bsky.app/profile/{account}/post/{pid}',
            'posted_at': posted_at, 'source': 'bluesky', 'in_window': True}


def _posts(items, start='2026-09-21T08:45:00+09:00', gaps=None):
    return {'asof': '2026-09-22',
            'window': {'start': start, 'end': '2026-09-22T08:45:00+09:00'},
            'coverage': {'first': '2026-09-21T09:00:00+09:00',
                         'last': '2026-09-22T08:31:00+09:00',
                         'gaps': gaps if gaps is not None else []},
            'posts': items}


class TestExample(unittest.TestCase):
    """9장 예시와의 대조 (T4)."""

    def test_snapshot_diff_is_zero(self):
        got = _digest().split('\n')
        self.assertEqual(EXPECTED, got)

    def test_example_diff_is_zero(self):
        """예시 원문에서 허용 차이만 되돌리면 렌더 결과와 같은 줄들이 나온다.

        줄 순서는 여기서 보지 않는다 — 소주제 순서(허용 차이 8번)와 `└` 계정
        순서(5번)가 1장 정렬로 다시 잡히기 때문이다. 순서는 아래 정렬 시험들이
        본다. 여기가 보는 것은 **어느 줄이 나가고 어느 줄이 빠졌나**다.
        """
        with open(DOC, encoding='utf-8') as f:
            raw = f.read().split('## 9. 예시')[1].split('```')[1]
        ex = [l for l in raw.split('\n') if l.strip()]
        ex = ex[:ex.index('■ FXKAN의 24시간 인사이트')]          # 허용 차이: 넷째 구획 없음
        ex = [l for l in ex                                      # 허용 차이 4: 검증 탈락
              if '(3~7월 약 +87%)' not in l and '(+349%/+927%)' not in l]

        def norm(line):
            # 허용 차이 11: 1행 시각은 설정 send_at 이다. 예시는 09:00 인 날의 원본이다.
            line = re.sub(r'^(X 24시간 다이제스트 · \d{4}-\d{2}-\d{2}) \d{2}:\d{2} KST$',
                          r'\1 HH:MM KST', line)
            line = re.sub(r'\s*\(@[^)]*\)$', '', line)           # `(@계정)` 은 아래에서 따로
            if line.startswith('└'):                             # 허용 차이 5·9: 순서·핸들
                return ('└', frozenset(a.split('.')[0].lower().replace('-', '_')
                                       for a in line[1:].split()))
            line = line.replace('»', '•')                        # 허용 차이 1: `•` → `»`
            line = (line.replace('한미반도체(042700)', '한미반도체')
                        .replace('한미반도체 042700', '한미반도체'))   # 허용 차이 3
            # 허용 차이 6: 단독 소식의 평가 문구는 잘린 채 들어온다
            for cut in ('하이브리드 본딩 투자 맵에서 구조적 포지션과 본딩 지연 '
                        '민감도를 동시에 갖춘 유일 종목으로 꼽혔던 이름',
                        '생산 거점 다변화 신호'):
                line = line.replace(cut, '')
            return ('L', re.sub(r'\.\s*$', '', line.strip()).strip())

        from collections import Counter
        want, got = Counter(map(norm, ex)), Counter(map(norm, _digest().split('\n')))
        self.assertEqual([], sorted(want - got), '예시에만 있는 줄')
        self.assertEqual([], sorted(got - want), '렌더에만 있는 줄')

    def test_sections_are_three_in_order(self):
        marks = [l for l in _digest().split('\n') if l.startswith('■')]
        self.assertEqual(['■ 공통 테마', '■ 주목할 단독 소식', '■ 투자 인사이트'], marks)

    def test_insight_section_is_all_views(self):
        """그 구획은 전부 해석이고, 기호가 그 사실을 말해야 한다 (1장)."""
        body = [l for l in _digest().split('■ 투자 인사이트\n')[1].split('\n') if l.strip()]
        self.assertTrue(body)
        self.assertEqual([], [l for l in body if not l.startswith('» ')])

    def test_head_three_lines(self):
        lines = _digest().split('\n')
        self.assertEqual('X 24시간 다이제스트 · 2026-09-21 08:00 KST', lines[0])
        self.assertEqual('게시물 156건 수집 / 반도체·AI 관련 68건 분석', lines[1])
        self.assertTrue(lines[2].startswith('(커버 구간: 9/20 18:00 ~ 9/21 08:48 KST.'), lines[2])
        self.assertNotIn('빠진 것', '\n'.join(lines[:1]))     # 예시에는 결손이 없다


class TestOrder(unittest.TestCase):
    """1장 '건수 상한과 정렬' — 전부 코드가 한다."""

    def test_account_line_is_count_desc_then_name_asc_ignoring_case(self):
        idx = R.index(_posts([
            _post('a1', 'Beta.bsky.social', '2026-09-22T01:00:00+09:00'),
            _post('a2', 'alpha.bsky.social', '2026-09-22T02:00:00+09:00'),
            _post('a3', 'alpha.bsky.social', '2026-09-22T03:00:00+09:00'),
            _post('a4', 'Alpha2.bsky.social', '2026-09-22T04:00:00+09:00'),
        ]))
        # alpha 2건 → Alpha2·Beta 1건. 대소문자를 무시하므로 Alpha2 가 Beta 앞이다.
        self.assertEqual(['alpha.bsky.social', 'Alpha2.bsky.social', 'Beta.bsky.social'],
                         R.accounts(['a1', 'a2', 'a3', 'a4'], idx))

    def test_theme_order_is_accounts_then_posts(self):
        posts = _posts([_post(f'p{i}', f'a{i}.bsky.social', '2026-09-22T01:00:00+09:00')
                        for i in range(1, 6)])
        facts = {'asof': '2026-09-22', 'counts': {'analyzed': 5},
                 'themes': [
                     {'title': '계정 1개', 'facts': [{'text': 'x', 'post_ids': ['p1']}]},
                     {'title': '계정 2개 · 게시물 2건',
                      'facts': [{'text': 'x', 'post_ids': ['p2']},
                                {'text': 'y', 'post_ids': ['p3']}]},
                     {'title': '계정 2개 · 게시물 3건',
                      'facts': [{'text': 'x', 'post_ids': ['p4', 'p5']},
                                {'text': 'y', 'post_ids': ['p2']}]}]}
        got = [l for l in R.compose(facts, posts, {}, names={}).split('\n') if l.startswith('▸')]
        self.assertEqual(['▸ 계정 2개 · 게시물 3건', '▸ 계정 2개 · 게시물 2건', '▸ 계정 1개'], got)

    def test_standalone_order_is_newest_first(self):
        posts = _posts([_post('old', 'a.bsky.social', '2026-09-21T10:00:00+09:00'),
                        _post('new', 'b.bsky.social', '2026-09-22T07:00:00+09:00')])
        facts = {'asof': '2026-09-22', 'counts': {'analyzed': 2},
                 'standalone': [{'text': '오래된 것', 'post_ids': ['old']},
                                {'text': '새 것', 'post_ids': ['new']}]}
        got = [l for l in R.compose(facts, posts, {}, names={}).split('\n')
               if l.startswith('•')]
        self.assertEqual(['• 새 것 (@b.bsky.social)', '• 오래된 것 (@a.bsky.social)'], got)

    def test_limits_drop_from_the_tail_and_say_so(self):
        posts = _posts([_post(f'p{i}', f'a{i}.bsky.social', '2026-09-22T01:00:00+09:00')
                        for i in range(1, 40)])
        facts = {'asof': '2026-09-22', 'counts': {'analyzed': 39},
                 'themes': [{'title': f'주제{i}',
                             'facts': [{'text': 'x', 'post_ids': ['p1']},
                                       {'text': 'y', 'post_ids': ['p2']}]}
                            for i in range(1, 11)],
                 'standalone': [{'text': f'단독{i}', 'post_ids': [f'p{i}']}
                                for i in range(1, 15)],
                 'views': [{'text': f'해석{i}'} for i in range(1, 9)]}
        out = R.compose(facts, posts, {}, names={})
        first = out.split('\n')[0]
        self.assertIn('소주제 2건 생략', first)
        self.assertIn('단독 소식 2건 생략', first)
        self.assertIn('투자 인사이트 2건 생략', first)
        self.assertEqual(8, sum(1 for l in out.split('\n') if l.startswith('▸')))
        self.assertEqual(6, sum(1 for l in out.split('\n') if l.startswith('»')))

    def test_facts_cap_is_five_per_theme(self):
        posts = _posts([_post('p1', 'a.bsky.social', '2026-09-22T01:00:00+09:00'),
                        _post('p2', 'b.bsky.social', '2026-09-22T01:00:00+09:00')])
        facts = {'asof': '2026-09-22', 'counts': {'analyzed': 2},
                 'themes': [{'title': '주제',
                             'facts': [{'text': f'사실{i}', 'post_ids': ['p1', 'p2']}
                                       for i in range(1, 8)]}]}
        out = R.compose(facts, posts, {}, names={})
        self.assertEqual(5, sum(1 for l in out.split('\n') if l.startswith('•')))
        self.assertIn('소주제 안 사실 2건 생략', out.split('\n')[0])


class TestLines(unittest.TestCase):
    """줄 만들기 — `(게시 M/D)` · 종목코드 · 빈 구획 · 결손."""

    def test_posted_before_window_gets_a_date(self):
        posts = _posts([_post('early', 'a.bsky.social', '2026-09-18T10:00:00+09:00'),
                        _post('now', 'b.bsky.social', '2026-09-22T01:00:00+09:00')],
                       start='2026-09-21T08:45:00+09:00')
        facts = {'asof': '2026-09-22', 'counts': {'analyzed': 2},
                 'themes': [{'title': '주제', 'facts': [
                     {'text': '창보다 이른 글', 'post_ids': ['early']},
                     {'text': '창 안의 글', 'post_ids': ['now']}]}],
                 'standalone': [{'text': '단독인데 이른 글', 'post_ids': ['early']}]}
        lines = R.compose(facts, posts, {}, names={}).split('\n')
        self.assertIn('• 창보다 이른 글 (게시 9/18)', lines)
        self.assertIn('• 창 안의 글', lines)
        # 단독 소식은 `(게시 M/D)` 가 `(@계정)` 앞이다.
        self.assertIn('• 단독인데 이른 글 (게시 9/18) (@a.bsky.social)', lines)

    def test_code_comes_from_the_dictionary_not_the_model(self):
        posts = _posts([_post('p1', 'a.bsky.social', '2026-09-22T01:00:00+09:00')])
        facts = {'asof': '2026-09-22', 'counts': {'analyzed': 1},
                 'standalone': [{'text': '한미반도체, 장비 수주. 한미반도체 재인용',
                                 'post_ids': ['p1']}]}
        out = R.compose(facts, posts, {}, names=NAMES)
        self.assertIn('• 한미반도체(042700), 장비 수주. 한미반도체 재인용 (@a.bsky.social)',
                      out.split('\n'))
        # 사전이 없으면 코드를 지어내지 않는다.
        self.assertIn('• 한미반도체, 장비 수주. 한미반도체 재인용 (@a.bsky.social)',
                      R.compose(facts, posts, {}, names={}).split('\n'))

    def test_empty_sections_keep_the_title_and_say_why(self):
        out = R.compose({'asof': '2026-09-22', 'counts': {'analyzed': 0}},
                        _posts([]), {}, names={}).split('\n')
        self.assertEqual(['■ 공통 테마', '해당 없음 — 2개 이상 계정이 겹친 주제 없음',
                          '■ 주목할 단독 소식', '해당 없음 — 단독으로 남은 게시물 없음',
                          '■ 투자 인사이트', '해당 없음 — 종합할 사실 줄 없음'], out[3:])

    def test_coverage_line_keeps_source_errors_verbatim(self):
        posts = _posts([], gaps=[{'scope': 'bluesky', 'why': '질의 4개 · 질의당 상위 100건까지'}])
        posts['sources'] = {'bluesky': {'errors': ['HTTP 429 · Too Many Requests']}}
        line = R.compose({'asof': '2026-09-22'}, posts, {}, names={}).split('\n')[2]
        self.assertIn('질의 4개 · 질의당 상위 100건까지', line)
        self.assertIn('bluesky HTTP 429 · Too Many Requests', line)

    def test_coverage_line_keeps_blocked_and_cut(self):
        """429 로 막힌 질의는 `errors` 없이 `blocked`·`cut` 으로만 온다(공통 계약).

        그것을 안 보면 막힌 날에도 '미수집 구간 없음' 이 나가 막힌 것과 없는 것을
        섞는다(2장 1번).
        """
        posts = _posts([_post('p1', 'a.bsky.social', '2026-09-22T01:00:00+09:00')])
        posts['sources'] = {'bluesky': {'queries': 7, 'got': 1, 'blocked': 3,
                                        'cut': 'HBM 질의는 429 로 3페이지에서 멈췄다',
                                        'errors': []}}
        line = R.compose({'asof': '2026-09-22'}, posts, {}, names={}).split('\n')[2]
        self.assertIn('bluesky 막힌 질의 3건', line)
        self.assertIn('bluesky HBM 질의는 429 로 3페이지에서 멈췄다', line)
        self.assertNotIn('미수집 구간 없음', line)

    def test_coverage_line_names_blocked_queries_without_raw_html(self):
        """2026-09-23 미리보기 — 막힌 질의 7개의 403 HTML 이 gaps·errors 로 두 번씩
        실려 1통이 6,000자를 넘었다. 이름만 모아 한 조각으로 적는다."""
        html = ('api.bsky.app: HTTP 403 · text/html · <html><body><h1>403 Forbidden</h1>'
                ' Request forbidden by administrative rules.</body></html>')
        fails = [f'질의 "{q}" 막힘 — {html}' for q in ('LLM', 'backtest')]
        posts = _posts([], gaps=[{'scope': 'bluesky', 'why': '질의 13개 · 질의당 상위 100건까지'}]
                       + [{'scope': 'bluesky', 'why': f} for f in fails])
        posts['sources'] = {'bluesky': {'blocked': 2, 'cut': None, 'errors': fails + [
            '질의 "HBM" 폴백으로 받음 — api.bsky.app: HTTP 429',
            '응답 32건은 uri·핸들·createdAt·text 중 하나가 없어 파싱에서 뺐다 — 근거가 없다']}}
        line = R.compose({'asof': '2026-09-22'}, posts, {}, names={}).split('\n')[2]
        self.assertIn('접속 제한으로 못 받은 질의 2개: LLM, backtest', line)
        self.assertIn('형식이 맞지 않는 응답 32건 제외', line)
        self.assertIn('질의 13개 · 질의당 상위 100건까지', line)
        self.assertNotIn('<html', line)
        self.assertNotIn('Forbidden', line)
        self.assertNotIn('폴백', line)                 # 받은 것은 결손이 아니다
        self.assertEqual(line.count('LLM'), 1)
        self.assertLess(len(line), 300)

    def test_item_drops_a_bullet_the_model_added(self):
        """2026-09-23 미리보기 — AI 뉴스 8줄이 전부 `• • …` 로 나갔다."""
        self.assertEqual(R._item('• Anthropic가 모델을 공개함'), 'Anthropic가 모델을 공개함')
        self.assertEqual(R._item(' - · 줄'), '줄')
        # 본문 안의 기호·음수는 건드리지 않는다.
        self.assertEqual(R._item('-3.6% 하락 · 전월 대비'), '-3.6% 하락 · 전월 대비')
        self.assertEqual(R._item('HBM • DRAM'), 'HBM • DRAM')

    def test_no_gaps_line_needs_a_source_block_that_says_so(self):
        clean = _posts([])
        clean['sources'] = {'bluesky': {'blocked': 0, 'cut': None, 'errors': []}}
        self.assertIn('미수집 구간 없음',
                      R.compose({'asof': '2026-09-22'}, clean, {}, names={}).split('\n')[2])
        # 소스 블록이 없으면 막혔는지 아닌지 우리가 모른다.
        self.assertIn('미수집 구간을 알 수 없음',
                      R.compose({'asof': '2026-09-22'}, _posts([]), {}, names={}).split('\n')[2])

    def test_span_is_counted_here_when_coverage_is_missing(self):
        """`coverage` 를 못 믿을 때 '창 안 게시물 없음' 이라 단정하지 않는다 (2장 1번)."""
        posts = _posts([_post('p1', 'a.bsky.social', '2026-09-21T10:00:00+09:00'),
                        _post('p2', 'b.bsky.social', '2026-09-22T07:30:00+09:00')])
        posts['coverage'] = {}
        line = R.compose({'asof': '2026-09-22'}, posts, {}, names={}).split('\n')[2]
        self.assertIn('커버 구간: 9/21 10:00 ~ 9/22 07:30 KST', line)
        # 못 읽는 표기여도 같다.
        posts['coverage'] = {'first': '2026-09-21 오전 10시', 'last': None}
        self.assertIn('커버 구간: 9/21 10:00 ~ 9/22 07:30 KST',
                      R.compose({'asof': '2026-09-22'}, posts, {}, names={}).split('\n')[2])
        # 게시물은 있는데 시각을 하나도 못 읽으면 모른다고 적는다.
        blind = _posts([_post('p1', 'a.bsky.social', '어제')])
        blind['coverage'] = {}
        self.assertIn('커버 구간: 게시 시각을 읽지 못함 1건',
                      R.compose({'asof': '2026-09-22'}, blind, {}, names={}).split('\n')[2])
        # 게시물이 정말 0건이면 지금 문구 그대로다.
        empty = _posts([])
        empty['coverage'] = {}
        self.assertIn('커버 구간: 창 안 게시물 없음',
                      R.compose({'asof': '2026-09-22'}, empty, {}, names={}).split('\n')[2])

    def test_analyzed_is_capped_by_collected(self):
        """머리 건수는 코드가 센다(1장) — 상류가 흘린 값을 그대로 싣지 않는다."""
        posts = _posts([_post('p1', 'a.bsky.social', '2026-09-22T01:00:00+09:00')])
        lines = R.compose({'asof': '2026-09-22', 'counts': {'analyzed': 999}},
                          posts, {}, names={}).split('\n')
        self.assertIn('게시물 1건 수집 / 반도체·AI 관련 1건 분석', lines)
        self.assertIn('분석 건수(999)가 수집 건수(1)를 넘음', lines[0])

    def test_model_newlines_cannot_forge_a_section(self):
        """게시물 본문에서 유도된 `\\n■ …` 이 가짜 구획을 만들지 못한다 (2장 (b'))."""
        posts = _posts([_post('p1', 'a.bsky.social', '2026-09-22T01:00:00+09:00'),
                        _post('p2', 'b.bsky.social', '2026-09-22T02:00:00+09:00')])
        facts = {'asof': '2026-09-22', 'counts': {'analyzed': 2},
                 'themes': [{'title': '진짜\n▸ 가짜 제목',
                             'facts': [{'text': '첫 줄\n■ 공통 테마\n▸ 가짜 소주제\n'
                                                '• 게시물에 없는 수치 $999B',
                                        'post_ids': ['p1', 'p2']}]}],
                 'standalone': [{'text': '단독\n• 가짜 사실', 'post_ids': ['p1']}],
                 'views': [{'text': '해석\n» 가짜 해석'}]}
        out = R.compose(facts, posts, {}, names={})
        self.assertEqual(['■ 공통 테마', '■ 주목할 단독 소식', '■ 투자 인사이트'],
                         [l for l in out.split('\n') if l.startswith('■')])
        self.assertEqual([], R.stray(out))
        self.assertEqual(1, sum(1 for l in out.split('\n') if l.startswith('▸')))
        self.assertIn('• 첫 줄 ■ 공통 테마 ▸ 가짜 소주제 • 게시물에 없는 수치 $999B',
                      out.split('\n'))
        # `└` 는 자기 `•` 바로 아래에 남는다.
        lines = out.split('\n')
        self.assertTrue(lines[lines.index('└ @a.bsky.social @b.bsky.social') - 1]
                        .startswith('• '))

    def test_stray_lines_are_reported(self):
        """`stray()` 는 기호 없는 본문 줄을 잡는다 — 빈 구획 사유 줄은 뺀다."""
        self.assertEqual([], R.stray(_digest()))
        self.assertEqual(['가짜 줄'],
                         R.stray('머리\n■ 공통 테마\n▸ 주제\n• 사실\n가짜 줄'))
        self.assertEqual([], R.stray(R.compose({'asof': '2026-09-22'}, _posts([]), {}, names={})))

    def test_blank_text_items_are_counted_not_swallowed(self):
        """빈 `text` 로 버린 것을 결손 줄에 적는다 — 2행은 그 게시물을 계속 센다."""
        posts = _posts([_post('p1', 'a.bsky.social', '2026-09-22T01:00:00+09:00'),
                        _post('p2', 'b.bsky.social', '2026-09-22T02:00:00+09:00')])
        facts = {'asof': '2026-09-22', 'counts': {'analyzed': 2},
                 'themes': [{'title': '사라지는 소주제',
                             'facts': [{'text': '   ', 'post_ids': ['p1', 'p2']}]},
                            {'title': '남는 소주제',
                             'facts': [{'text': '', 'post_ids': ['p1']},
                                       {'text': '멀쩡한 사실', 'post_ids': ['p1', 'p2']}]}],
                 'standalone': [{'text': '', 'post_ids': ['p1']}],
                 'views': [{'text': ' '}]}
        first = R.compose(facts, posts, {}, names={}).split('\n')[0]
        self.assertIn("'•' 가 없어 뺀 소주제 1건(사라지는 소주제)", first)
        self.assertIn('본문이 빈 사실 1건 제외', first)
        self.assertIn('본문이 빈 단독 소식 1건 제외', first)
        self.assertIn('본문이 빈 투자 인사이트 1건 제외', first)

    def test_missing_post_ids_are_counted_not_swallowed(self):
        posts = _posts([_post('p1', 'a.bsky.social', '2026-09-22T01:00:00+09:00')])
        facts = {'asof': '2026-09-22', 'counts': {'analyzed': 1},
                 'themes': [{'title': '주제', 'facts': [
                     {'text': '근거가 사라진 줄', 'post_ids': ['없는id']},
                     {'text': '멀쩡한 줄', 'post_ids': ['p1']}]}],
                 'standalone': [{'text': '출처 없는 단독', 'post_ids': ['없는id2']}]}
        first = R.compose(facts, posts, {}, names={}).split('\n')[0]
        self.assertIn('출처 게시물을 찾지 못한 줄 1건', first)
        self.assertIn('출처 게시물을 찾지 못한 단독 소식 1건', first)

    def test_missing_line_is_above_the_title(self):
        facts = dict(FACTS, missing=['본문 미수집 6건'])
        lines = _digest(facts=facts).split('\n')
        self.assertEqual('빠진 것 1건 — 본문 미수집 6건', lines[0])
        self.assertTrue(lines[1].startswith('X 24시간 다이제스트'), lines[1])


class TestNames(unittest.TestCase):
    """별칭 사전 (1장 문체 · 3-3 4·5번)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix='xdigest-names-')
        self.log = []

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, body):
        p = os.path.join(self.tmp, 'names.yaml')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(body)
        return p

    def test_meta_key_is_not_a_headword(self):
        """`names:` 래퍼 없는 파일의 메타 한 줄이 종목코드를 만들어 내지 않는다."""
        p = self._write('version: 1\n한미반도체:\n  code: "042700"\n  aliases: [Hanmi]\n')
        names = R.load_names(p, log=self.log.append)
        self.assertEqual({'한미반도체': '042700'}, names)
        self.assertEqual('version 관리와 한미반도체(042700)',
                         R.with_codes('version 관리와 한미반도체', names))

    def test_wrapper_keeps_the_short_form(self):
        p = self._write('names:\n  한미반도체: "042700"\n  이상한거: 1\n')
        self.assertEqual({'한미반도체': '042700'}, R.load_names(p, log=self.log.append))
        self.assertIn('6자리가 아닌 코드 1건', ' '.join(self.log))

    def test_non_mapping_file_is_a_reason_not_a_crash(self):
        """리스트로 쓴 파일에서 터지면 그날 다이제스트가 없다."""
        p = self._write('- 한미반도체\n- 삼성전자\n')
        self.assertEqual({}, R.load_names(p, log=self.log.append))
        self.assertIn('매핑이 아니다', ' '.join(self.log))

    def test_one_code_once_per_line(self):
        """표제어 둘이 같은 코드를 가리켜도 한 줄에 괄호는 하나다."""
        names = {'하이닉스': '000660', 'SK하이닉스': '000660'}
        self.assertEqual('하이닉스가 SK하이닉스(000660)와',
                         R.with_codes('하이닉스가 SK하이닉스와', names))


# ─────────────────────────── 별도 구획 (D-NEXT-Q) ───────────────────────────
# 9장 예시에 AI 최신 뉴스 · 퀀트·백테스트를 얹은 모양. 예시 픽스처의 **인용되지
# 않은 채움 게시물**을 구획 게시물로 바꿔 쓴다 — 그래야 2행 '수집 156건' 과 기본
# 구획 '68건' 이 예시 그대로 남고, 달라지는 것이 새 구획뿐이다. 본문은 합성이다.
# 실재 회사·모델의 발표처럼 읽히지 않게 이름을 쓰지 않았다.
SEC_POSTS = {
    # 채움 게시물 번호: (구획, 계정, 게시 시각)
    120: ('ai_news', 'llmwatch.bsky.social', '2026-09-21T06:10:00+09:00'),
    121: ('ai_news', 'ainewsdesk.bsky.social', '2026-09-21T06:40:00+09:00'),
    122: ('ai_news', 'ainewsdesk.bsky.social', '2026-09-21T02:05:00+09:00'),
    123: ('ai_news', 'agentlab.bsky.social', '2026-09-20T23:30:00+09:00'),
    124: ('ai_news', 'promo.bsky.social', '2026-09-21T01:00:00+09:00'),
    125: ('ai_news', 'llmwatch.bsky.social', '2026-09-20T21:15:00+09:00'),
    130: ('quant', 'quantnotes.bsky.social', '2026-09-21T05:20:00+09:00'),
    131: ('quant', 'systematicdesk.bsky.social', '2026-09-21T04:00:00+09:00'),
    132: ('quant', 'quantnotes.bsky.social', '2026-09-20T22:45:00+09:00'),
    133: ('quant', 'signals4u.bsky.social', '2026-09-21T03:00:00+09:00'),
}


def _pid(n):
    return f'at://did:plc:fixture{n:04d}/app.bsky.feed.post/3l4x{n:04d}'


SEC_FACTS = [
    {'key': 'ai_news', 'title': 'AI 최신 뉴스', 'label': 'AI뉴스', 'pool': 6,
     'analyzed': 5, 'error': None, 'items': [
         {'text': '오픈 가중치 LLM 새 버전 공개 — 컨텍스트 128K, 상업 이용 허용',
          'post_ids': [_pid(120), _pid(121)]},
         {'text': '코딩 에이전트 벤치마크 새 판 공개 — 과제 500개, 사람 검수 통과분만 채점',
          'post_ids': [_pid(123)]},
         {'text': '추론 모델 API 가격 인하 — 입력 100만 토큰당 $1.25',
          'post_ids': [_pid(122), _pid(125)]},
     ]},
    {'key': 'quant', 'title': '퀀트·백테스트', 'label': '퀀트', 'pool': 4,
     'analyzed': 3, 'error': None, 'items': [
         {'text': '모멘텀 팩터 백테스트 2000~2025 — 거래비용 전 연 8.1%, 반영 후 5.4%',
          'post_ids': [_pid(130)]},
         {'text': '워크포워드 검증 구성 공유 — 학습 3년·검증 1년 롤링, 파라미터 고정 뒤 표본 외 성과만 집계',
          'post_ids': [_pid(131), _pid(132)]},
     ]},
]


def _sec_fixture():
    """예시 픽스처 + 별도 구획. 원본을 고치지 않게 깊은 사본을 쓴다."""
    import copy
    facts, posts = copy.deepcopy(FACTS), copy.deepcopy(POSTS)
    facts['sections'] = copy.deepcopy(SEC_FACTS)
    by_id = {p['id']: p for p in posts['posts']}
    cited = {i for t in FACTS['themes'] for f in t['facts'] for i in f['post_ids']}
    cited |= {i for x in FACTS['standalone'] for i in x['post_ids']}
    for n, (topic, acct, at) in SEC_POSTS.items():
        p = by_id[_pid(n)]
        assert p['id'] not in cited, n        # 예시가 인용한 게시물은 건드리지 않는다
        p.update(topic=topic, account=acct, posted_at=at,
                 url=f'https://bsky.app/profile/{acct}/post/3l4x{n:04d}')
    return facts, posts


with open(os.path.join(FIX, 'xdigest_render_sections_expected.txt'), encoding='utf-8') as f:
    SEC_EXPECTED = f.read().rstrip('\n').split('\n')


class TestSections(unittest.TestCase):
    """AI 최신 뉴스 · 퀀트·백테스트 — 투자 인사이트 뒤에 `■` 하나씩 (D-NEXT-Q)."""

    def _digest(self, facts=None, posts=None, cfg=None):
        f, p = _sec_fixture()
        return R.compose(facts or f, posts or p, cfg or {}, names=NAMES)

    def test_snapshot_diff_is_zero(self):
        self.assertEqual(SEC_EXPECTED, self._digest().split('\n'))

    def test_sections_come_after_the_insight(self):
        marks = [l for l in self._digest().split('\n') if l.startswith('■')]
        self.assertEqual(['■ 공통 테마', '■ 주목할 단독 소식', '■ 투자 인사이트',
                          '■ AI 최신 뉴스', '■ 퀀트·백테스트'], marks)

    def test_head_counts_each_section(self):
        lines = self._digest().split('\n')
        self.assertEqual('게시물 156건 수집 / 반도체·AI 68건 · AI뉴스 5건 · 퀀트 3건 분석',
                         lines[1])

    def test_label_comes_from_topics_label(self):
        lines = self._digest(cfg={'topics_label': '반도체·AI 인프라'}).split('\n')
        self.assertTrue(lines[1].endswith('/ 반도체·AI 인프라 68건 · AI뉴스 5건 · 퀀트 3건 분석'),
                        lines[1])

    def test_lines_carry_the_handles_in_model_order(self):
        body = self._digest().split('■ AI 최신 뉴스\n')[1].split('\n■')[0].split('\n')
        self.assertEqual(body[0], '• 오픈 가중치 LLM 새 버전 공개 — 컨텍스트 128K, 상업 이용 허용 '
                                  '(@ainewsdesk.bsky.social @llmwatch.bsky.social)')
        self.assertEqual(3, len(body))
        self.assertTrue(all(l.startswith('• ') and l.endswith(')') for l in body))

    def test_zero_posts_says_so_in_one_line(self):
        f, p = _sec_fixture()
        f['sections'][1].update(pool=0, analyzed=0, items=[])
        out = self._digest(facts=f, posts=p).split('\n')
        i = out.index('■ 퀀트·백테스트')
        self.assertEqual(out[i + 1:], ['해당 없음 — 수집 구간 내 게시물 0건'])
        self.assertIn('퀀트 0건', out[1])

    def test_nothing_relevant_and_nothing_left_are_told_apart(self):
        f, p = _sec_fixture()
        f['sections'][0].update(analyzed=0, items=[])
        f['sections'][1].update(items=[])
        out = self._digest(facts=f, posts=p).split('\n')
        self.assertIn('해당 없음 — 게시물 6건 중 이 구획 소식 없음', out)
        self.assertIn('해당 없음 — 관련 게시물 3건에서 옮길 소식 없음', out)

    def test_failure_is_a_reason_on_top_not_a_silent_blank(self):
        f, p = _sec_fixture()
        f['sections'][0].update(analyzed=0, items=[], error='RateLimitError: 429 too many')
        out = self._digest(facts=f, posts=p).split('\n')
        self.assertTrue(out[0].startswith('빠진 것 1건 — AI 최신 뉴스 분석 실패 — '), out[0])
        self.assertIn('해당 없음 — 게시물 6건을 받았으나 분석하지 못함', out)

    def test_section_count_is_capped_by_what_was_collected(self):
        f, p = _sec_fixture()
        f['sections'][1]['analyzed'] = 99
        out = self._digest(facts=f, posts=p).split('\n')
        self.assertIn('퀀트 4건 분석', out[2])      # 결손 줄이 맨 위에 붙어 2행이 한 칸 내려갔다
        self.assertIn('퀀트 분석 건수(99)가 수집 건수(4)를 넘음', out[0])

    def test_empty_lines_are_not_stray_but_forgery_still_is(self):
        f, p = _sec_fixture()
        f['sections'][1].update(pool=0, analyzed=0, items=[])
        text = self._digest(facts=f, posts=p)
        self.assertEqual([], R.stray(text))
        self.assertEqual(['가짜 줄'], R.stray(text + '\n가짜 줄'))

    def test_model_newlines_cannot_forge_a_section(self):
        f, p = _sec_fixture()
        f['sections'][0]['items'][0]['text'] = '첫 줄\n■ 가짜 구획\n• 가짜'
        text = self._digest(facts=f, posts=p)
        self.assertNotIn('\n■ 가짜 구획', text)
        self.assertEqual([], R.stray(text))

    def test_split_keeps_every_section_line(self):
        """새 구획이 붙어도 4,096자 안이고 한 줄도 조용히 잘리지 않는다."""
        text = self._digest()
        parts = R.split(text)
        self.assertEqual([], R.over(parts))
        self.assertTrue(parts[-1].endswith(SEC_EXPECTED[-1]))
        joined = '\n'.join(parts).split('\n')
        for ln in text.split('\n'):
            self.assertIn(ln, joined)

    def test_oversized_section_splits_at_bullet_boundaries(self):
        f, p = _sec_fixture()
        f['sections'][0]['items'] = [{'text': '라' * 600, 'post_ids': [_pid(120)]}
                                     for _ in range(8)]
        text = self._digest(facts=f, posts=p)
        parts = R.split(text)
        self.assertEqual([], R.over(parts))
        heads = [x.split('\n')[0] for x in parts]
        self.assertIn('X 다이제스트 (이어서) · ■ AI 최신 뉴스', heads)
        joined = '\n'.join(parts)
        self.assertEqual(8, joined.count('• ' + '라' * 600))

    def test_old_facts_without_sections_render_as_before(self):
        """구획이 생기기 전의 facts.json 은 예전 모양 그대로다 — 예시 스냅숏이 그 증거다."""
        self.assertEqual(EXPECTED, _digest().split('\n'))
        self.assertIn('반도체·AI 관련 68건 분석', _digest().split('\n')[1])


class TestFallback(unittest.TestCase):
    """분석이 실패한 날 (3-6)."""

    def test_analysis_failure_sends_counts_and_urls(self):
        out = R.compose({'asof': '2026-09-21', 'error': 'HTTP 429 · rate limit'},
                        POSTS, {}, names={}).split('\n')
        self.assertEqual('빠진 것 1건 — 분석 실패 — HTTP 429 · rate limit', out[0])
        self.assertIn('게시물 156건 수집 / 반도체·AI 관련 0건 분석', out)
        self.assertIn('■ 계정별 건수', out)
        self.assertIn('■ 창 안 URL', out)
        urls = [l for l in out if l.startswith('• https://')]
        self.assertEqual(R.FALLBACK_URL_MAX, len(urls))
        self.assertIn(f'• 외 {156 - R.FALLBACK_URL_MAX}건 생략', out)
        self.assertEqual([], [l for l in out if l.startswith('▸') or l.startswith('»')])


class TestSplit(unittest.TestCase):
    """4,096자 분할 (1장)."""

    @staticmethod
    def _unbroken(case, parts):
        """`▸` 제목과 첫 `•` 사이, `•` 와 `└` 사이에서 자르지 않았나."""
        for i, p in enumerate(parts, 1):
            lines = p.split('\n')
            for j, ln in enumerate(lines):
                if ln.startswith('▸'):
                    case.assertTrue(j + 1 < len(lines) and lines[j + 1].startswith('•'),
                                    f'{i}통 끝이 ▸ 제목이다: {ln}')
                if ln.startswith('└'):
                    case.assertTrue(any(x.startswith('•') for x in lines[:j]),
                                    f'{i}통이 └ 로 시작한다: {ln}')

    def test_example_is_two_parts(self):
        parts = R.split(_digest())
        self.assertEqual(2, len(parts))
        self.assertEqual([], R.over(parts))
        self.assertTrue(parts[0].startswith('X 24시간 다이제스트'))
        self.assertIn('■ 공통 테마', parts[0])
        self.assertNotIn('■ 주목할 단독 소식', parts[0])
        self.assertTrue(parts[1].startswith('■ 주목할 단독 소식'))
        self.assertIn('■ 투자 인사이트', parts[1])
        self._unbroken(self, parts)

    def test_no_part_numbers_anywhere(self):
        """통 번호를 붙이지 않는다 — `send()` 의 `_split` 이 한 번 더 나누면 틀린 번호가 된다."""
        for p in R.split(_digest()):
            self.assertIsNone(re.search(r'\(\d+/\d+\)', p.split('\n')[0]), p.split('\n')[0])

    def test_oversized_section_splits_at_theme_boundaries(self):
        posts = _posts([_post(f'p{i}', f'a{i}.bsky.social', '2026-09-22T01:00:00+09:00')
                        for i in range(1, 4)])
        facts = {'asof': '2026-09-22', 'counts': {'analyzed': 3},
                 'themes': [{'title': f'주제{i}',
                             'facts': [{'text': '가' * 300, 'post_ids': ['p1', 'p2']},
                                       {'text': '나' * 300, 'post_ids': ['p3']}]}
                            for i in range(1, 9)]}
        parts = R.split(R.compose(facts, posts, {}, names={}))
        self.assertGreater(len(parts), 2)
        self.assertEqual([], R.over(parts))
        self._unbroken(self, parts)
        cont = [p for p in parts if p.startswith('X 다이제스트 (이어서)')]
        self.assertTrue(cont, [p.split('\n')[0] for p in parts])
        self.assertEqual('X 다이제스트 (이어서) · ■ 공통 테마', cont[0].split('\n')[0])

    def test_oversized_standalone_splits_at_bullet_boundaries(self):
        posts = _posts([_post('p1', 'a.bsky.social', '2026-09-22T01:00:00+09:00')])
        facts = {'asof': '2026-09-22', 'counts': {'analyzed': 1},
                 'standalone': [{'text': '다' * 400, 'post_ids': ['p1']} for _ in range(12)]}
        parts = R.split(R.compose(facts, posts, {}, names={}))
        self.assertEqual([], R.over(parts))
        heads = [p.split('\n')[0] for p in parts]
        self.assertIn('X 다이제스트 (이어서) · ■ 주목할 단독 소식', heads)

    def test_split_reads_digest_text_only(self):
        """발송 재시도가 다시 렌더하지 않고 같은 통 목록을 얻는다 (3-5)."""
        text = _digest()
        self.assertEqual(R.split(text), R.split(text))
        self.assertEqual(R.render(FACTS, POSTS, {}, names=NAMES), R.split(text))


class TestSend(unittest.TestCase):
    """발송 (3-5)."""

    def setUp(self):
        self.calls = []

        def sender(text, **kw):
            self.calls.append((text, kw))
            return True, '1건 발송'
        self.sender = sender
        self.parts = R.split(_digest())

    def test_plain_mode_and_chat_id(self):
        ok, rec = S.send_parts(self.parts, '2026-09-21', cfg={}, sender=self.sender,
                               chat_id='-100123', now=datetime(2026, 9, 21, 7, 55, tzinfo=KST))
        self.assertTrue(ok)
        self.assertEqual(2, len(self.calls))
        for _, kw in self.calls:
            self.assertIsNone(kw['parse_mode'])     # 평문. send() 가 그 키를 요청에서 뺀다
            self.assertEqual('-100123', kw['chat_id'])
        self.assertIsNone(rec['note'])
        self.assertEqual(2, rec['sent'])

    def test_xdigest_chat_id_wins(self):
        with mock.patch.dict(os.environ, {'XDIGEST_CHAT_ID': '-100999',
                                          'TELEGRAM_CHAT_ID': '-100111'}):
            S.send_parts(self.parts[:1], '2026-09-21', sender=self.sender)
        self.assertEqual('-100999', self.calls[0][1]['chat_id'])

    def test_late_note_goes_on_the_title_line_only(self):
        late = datetime(2026, 9, 21, 14, 2, tzinfo=KST)
        ok, rec = S.send_parts(self.parts, '2026-09-21', cfg={}, sender=self.sender,
                               chat_id='-1', now=late)
        self.assertEqual('(실제 발송 14:02)', rec['note'])
        self.assertTrue(self.calls[0][0].split('\n')[0].endswith('(실제 발송 14:02)'))
        self.assertNotIn('실제 발송', self.calls[1][0])
        # digest.txt 는 부기 없는 본문이다.
        self.assertNotIn('실제 발송', _digest())

    def test_stops_at_the_failed_part_and_resumes_there(self):
        def flaky(text, **kw):
            self.calls.append((text, kw))
            n = len(self.calls)
            return (False, 'HTTP 400 · Bad Request') if n == 2 else (True, '1건 발송')
        ok, rec = S.send_parts(self.parts, '2026-09-21', sender=flaky, chat_id='-1')
        self.assertFalse(ok)
        self.assertEqual(1, rec['sent'])
        self.assertEqual([1, 2], [r['n'] for r in rec['results']])
        self.assertEqual('HTTP 400 · Bad Request', rec['results'][1]['why'])

        self.calls = []
        ok2, rec2 = S.send_parts(self.parts, '2026-09-21', sender=self.sender,
                                 sent=rec, chat_id='-1')
        self.assertTrue(ok2)
        self.assertEqual(1, len(self.calls))                 # 2통만 다시 보냈다
        self.assertEqual('앞 실행에서 보냈다', rec2['results'][0]['why'])

    def test_late_note_says_the_date_when_the_day_changed(self):
        """지난 기준일을 손으로 다시 보낼 때 시각만 적으면 그날 늦게 보낸 것으로 읽힌다."""
        self.assertEqual('(실제 발송 14:02)',
                         S._late('2026-09-20', {}, datetime(2026, 9, 20, 14, 2, tzinfo=KST)))
        self.assertEqual('(실제 발송 9/22 14:02)',
                         S._late('2026-09-20', {}, datetime(2026, 9, 22, 14, 2, tzinfo=KST)))
        # 07:55 는 '정시 안' 이다 — send_at(08:00) 앞. 2026-09-23 에 09:00 → 08:00 으로
        # 당기며 이 시계들도 08:55 에서 옮겼다. 08:55 는 이제 지연이다.
        self.assertEqual('', S._late('2026-09-20', {},
                                     datetime(2026, 9, 20, 7, 55, tzinfo=KST)))
        self.assertEqual('(실제 발송 08:55)',
                         S._late('2026-09-20', {}, datetime(2026, 9, 20, 8, 55, tzinfo=KST)))

    def test_resume_needs_the_same_parts(self):
        """통 수가 줄어든 날, 앞 기록의 통 번호로 새 1통을 건너뛰지 않는다.

        건너뛰면 보낸 통이 0인데 `ok` 가 참이 되어 표식이 찍히고, 그날
        다이제스트는 조용히 아무것도 안 나간다(3-5).
        """
        _, prev = S.send_parts(self.parts, '2026-09-21', sender=self.sender, chat_id='-1')
        self.calls = []
        ok, rec = S.send_parts(['새 1통'], '2026-09-21', cfg={}, sent=prev, chat_id='-1',
                               sender=self.sender,
                               now=datetime(2026, 9, 21, 7, 55, tzinfo=KST))
        self.assertEqual(['새 1통'], [t for t, _ in self.calls])
        self.assertTrue(ok)
        self.assertTrue(rec['stale'])
        self.assertEqual(1, rec['sent_now'])
        # 지문이 같으면 그대로 건너뛴다(재시도).
        self.calls = []
        ok2, rec2 = S.send_parts(self.parts, '2026-09-21', sent=prev, chat_id='-1',
                                 sender=self.sender)
        self.assertEqual([], self.calls)
        self.assertTrue(ok2)
        self.assertIsNone(rec2['stale'])
        self.assertEqual(0, rec2['sent_now'])

    def test_fingerprint_changes_with_order_and_content(self):
        fp = S.fingerprint(self.parts)
        self.assertEqual(fp, S.fingerprint(list(self.parts)))
        self.assertNotEqual(fp, S.fingerprint(self.parts[::-1]))
        self.assertNotEqual(fp, S.fingerprint(self.parts[:1]))
        self.assertEqual(set(), S.done({'results': [{'n': 1, 'ok': True}]}, fp))
        self.assertEqual({1}, S.done({'fingerprint': fp,
                                      'results': [{'n': 1, 'ok': True}]}, fp))

    def test_note_length_is_measured_after_stamping(self):
        """부기를 붙인 뒤 한도를 넘으면 그때 적는다 — `R.over` 는 붙이기 전을 봤다."""
        part = 'X 24시간 다이제스트 · 2026-09-21 09:00 KST\n' + '가' * 40
        limit = len(part) + 5                      # 부기 17자를 더하면 넘는 길이
        self.assertEqual([], R.over([part], limit=limit))
        ok, rec = S.send_parts([part], '2026-09-21', cfg={}, sender=self.sender,
                               chat_id='-1', limit=limit,
                               now=datetime(2026, 9, 21, 14, 2, tzinfo=KST))
        self.assertTrue(ok)
        self.assertEqual([1], rec['over'])

    def test_no_chat_id_is_a_reason_not_a_crash(self):
        with mock.patch.dict(os.environ, {'XDIGEST_CHAT_ID': '', 'TELEGRAM_CHAT_ID': ''}):
            with mock.patch.object(S.creds, 'get', return_value=''):
                ok, rec = S.send_parts(self.parts, '2026-09-21', sender=self.sender)
        self.assertFalse(ok)
        self.assertIn('보낼 대화방이 없다', rec['results'][0]['why'])
        self.assertEqual([], self.calls)


class TestRun(unittest.TestCase):
    """`run()` — 파일·표식 (3-5·3-6·4장)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix='xdigest-test-')
        self.state = mock.patch.object(R, 'STATE', os.path.join(self.tmp, 'state'))
        self.site = mock.patch.object(S, 'SITE', os.path.join(self.tmp, 'docs'))
        # 받는 방이 없으면 발송 전에 사유로 끝난다 — 여기서 보려는 것은 그 뒤다.
        self.env = mock.patch.dict(os.environ, {'XDIGEST_CHAT_ID': '-100test'})
        self.state.start()
        self.site.start()
        self.env.start()
        self.log = []

    def tearDown(self):
        self.env.stop()
        self.state.stop()
        self.site.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_digest_today_is_a_no_op(self):
        self.assertEqual(0, S.run('2026-09-21', log=self.log.append))
        self.assertIn('오늘 digest 없음', ' '.join(self.log))
        self.assertIsNone(S.mark())

    def test_failure_keeps_sent_json_and_no_mark(self):
        R.write_digest('2026-09-21', _digest())
        ok_first = {'n': 0}

        def flaky(text, **kw):
            ok_first['n'] += 1
            return (True, '1건 발송') if ok_first['n'] == 1 else (False, 'HTTP 400')
        rc = S.run('2026-09-21', log=self.log.append, sender=flaky)
        self.assertEqual(1, rc)
        sent = R.read('2026-09-21', 'sent.json')
        self.assertEqual(1, sent['sent'])
        self.assertFalse(sent['ok'])
        self.assertIsNone(S.mark())                          # 표식은 끝까지 성공한 뒤에만

        # 재시도는 못 보낸 통부터. 그리고 표식을 남긴다.
        rc2 = S.run('2026-09-21', log=self.log.append, sender=lambda t, **k: (True, '1건 발송'))
        self.assertEqual(0, rc2)
        self.assertEqual('2026-09-21', S.mark())
        self.assertEqual(2, R.read('2026-09-21', 'sent.json')['sent'])

    def test_once_skips_an_already_sent_day(self):
        R.write_digest('2026-09-21', _digest())
        S.mark('2026-09-21', 2)
        calls = []
        rc = S.run('2026-09-21', once=True, log=self.log.append,
                   sender=lambda t, **k: calls.append(t) or (True, 'x'))
        self.assertEqual(0, rc)
        self.assertEqual([], calls)
        self.assertIn('이미 보냈다', ' '.join(self.log))


if __name__ == '__main__':
    unittest.main()

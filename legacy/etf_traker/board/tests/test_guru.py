#!/usr/bin/env python3
"""구루 브리핑 — 출처 대조·숫자 검사·양식·분할·예산·발송을 가짜 클라이언트로 본다."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.guru import pipeline as GP        # noqa: E402
from board.guru import render as RD          # noqa: E402
from board.guru import research as RS        # noqa: E402
from board.guru import synth as SY           # noqa: E402

NOW = datetime(2026, 9, 29, 6, 40, tzinfo=RS.KST)          # 화요일
CFG = GP.cfg_load()
URL = 'https://www.cnbc.com/2026/09/28/burry-ai.html'


def usage(searches=1):
    return {'input_tokens': 1000, 'output_tokens': 200,
            'server_tool_use': {'web_search_requests': searches}}


def search_block(*urls):
    return {'type': 'web_search_tool_result',
            'content': [{'type': 'web_search_result', 'url': u, 'title': 't', 'page_age': None}
                        for u in urls]}


def answer(obj):
    return {'type': 'text', 'text': 'done\n```json\n' + json.dumps(obj, ensure_ascii=False) + '\n```'}


class FakeClient:
    """프롬프트로 어느 호출인지 가려 정해 둔 응답을 돌려준다."""

    def __init__(self, people=None, market=None, synth=None, pause=False):
        self.people, self.market, self.synth = people or {}, market, synth or []
        self.pause = pause
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        prompt = kw['messages'][0]['content']
        if 'output_config' in kw and 'format' in kw['output_config']:
            obj = self.synth.pop(0)
            return {'stop_reason': 'end_turn', 'usage': usage(0),
                    'content': [{'type': 'text', 'text': json.dumps(obj, ensure_ascii=False)}]}
        if '미국장 마감 상황' in prompt:
            obj = self.market or {'line': '', 'sources': []}
            return {'stop_reason': 'end_turn', 'usage': usage(),
                    'content': [search_block(URL), answer(obj)]}
        for g in CFG['groups']:
            if g['people'][0]['name'] in prompt:
                rows = [self.people[p['name']] for p in g['people'] if p['name'] in self.people]
                if self.pause and len(kw['messages']) == 1:
                    return {'stop_reason': 'pause_turn', 'usage': usage(),
                            'content': [search_block(URL)]}
                return {'stop_reason': 'end_turn', 'usage': usage(),
                        'content': ([] if self.pause else [search_block(URL)])
                        + [answer({'people': rows})]}
        raise AssertionError('모르는 호출')


def burry(url=URL, date='2026-09-28', status='pass'):
    return {'name': 'Michael Burry', 'status': status, 'remark': 'AI 버블 경고를 했습니다.',
            'implication': '하방 헤지를 검토할 시점입니다.',
            'sources': [{'url': url, 'outlet': 'CNBC', 'date': date}], 'note': '', 'note_sources': []}


class TempState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = (RD.STATE, GP.COST, GP.MARK)
        RD.STATE = os.path.join(self.tmp.name, 'guru')
        GP.COST = os.path.join(RD.STATE, 'cost.json')
        GP.MARK = os.path.join(self.tmp.name, 'guru-sent.json')

    def tearDown(self):
        RD.STATE, GP.COST, GP.MARK = self._saved
        self.tmp.cleanup()


class TestResearch(unittest.TestCase):
    def setUp(self):
        self.win = RS.window(NOW, CFG)

    def value_group(self):
        return next(g for g in CFG['groups'] if g['key'] == 'value')

    def test_window_and_days(self):
        self.assertEqual(self.win['session'], '2026-09-28')
        self.assertTrue(RS.runs_on(NOW.date(), CFG))
        self.assertFalse(RS.runs_on(datetime(2026, 9, 28).date(), CFG))      # 월
        self.assertEqual(RS.next_run(datetime(2026, 10, 3).date(), CFG).isoformat(), '2026-10-06')

    def test_source_must_be_in_results(self):
        g = self.value_group()
        urls = RS.result_urls([search_block(URL)])
        ok, drops = RS.validate_people({'people': [burry(url=URL + '?utm_source=x')]}, g,
                                       urls, self.win, CFG)
        b = next(p for p in ok if p['name'] == 'Michael Burry')
        self.assertEqual(b['status'], 'pass')
        bad, drops = RS.validate_people({'people': [burry(url='https://made.up/x')]}, g,
                                        urls, self.win, CFG)
        b = next(p for p in bad if p['name'] == 'Michael Burry')
        self.assertEqual(b['status'], 'none')
        self.assertTrue(any('Michael Burry' in d and '출처' in d for d in drops))
        # 조사 결과에 없는 사람은 '없음' 이 아니라 '확인 못 함'
        self.assertEqual(next(p for p in bad if p['name'] == 'Bill Ackman')['status'], 'unknown')

    def test_old_source_is_not_today(self):
        g = self.value_group()
        urls = RS.result_urls([search_block(URL)])
        rows, _ = RS.validate_people({'people': [burry(date='2026-09-20')]}, g, urls, self.win, CFG)
        self.assertEqual(next(p for p in rows if p['name'] == 'Michael Burry')['status'], 'none')

    def test_name_with_org_suffix_matches(self):
        g = self.value_group()
        urls = RS.result_urls([search_block(URL)])
        rows, drops = RS.validate_people({'people': [dict(burry(), name='Michael Burry (Scion)'),
                                                     {'name': 'bill  ackman', 'status': 'none'}]},
                                         g, urls, self.win, CFG)
        self.assertEqual(next(p for p in rows if p['name'] == 'Michael Burry')['status'], 'pass')
        self.assertEqual(next(p for p in rows if p['name'] == 'Bill Ackman')['status'], 'none')
        self.assertFalse(any('명단 밖' in d for d in drops))

    def test_roster_only(self):
        g = self.value_group()
        _, drops = RS.validate_people({'people': [dict(burry(), name='Warren Buffett')]}, g,
                                      {}, self.win, CFG)
        self.assertTrue(any('명단 밖' in d for d in drops))

    def test_pause_turn_is_continued(self):
        cl = FakeClient(people={'Michael Burry': burry()}, pause=True)
        sp = RS.Spend()
        g = self.value_group()
        blocks = RS.call(cl, CFG, RS.SYSTEM, RS.people_prompt(g, self.win, CFG), 5, sp)
        self.assertEqual(len(cl.calls), 2)
        self.assertEqual(cl.calls[1]['messages'][1]['role'], 'assistant')
        self.assertIn(RS.norm_url(URL), RS.result_urls(blocks))      # 앞 조각의 검색 결과도 남는다
        self.assertEqual(sp.searches, 2)

    def test_urls_from_code_execution_and_citations(self):
        blocks = [
            {'type': 'server_tool_use', 'input': {'query': 'x https://model.said/it'}},
            {'type': 'bash_code_execution_tool_result',
             'content': {'stdout': 'Title A\nhttps://www.reuters.com/a/b/ \n', 'return_code': 0}},
            {'type': 'text', 'text': 'see https://made.up/x',
             'citations': [{'url': 'https://fortune.com/z', 'cited_text': '...'}]},
        ]
        urls = RS.result_urls(blocks)
        self.assertIn(RS.norm_url('https://reuters.com/a/b'), urls)
        self.assertIn(RS.norm_url('https://fortune.com/z'), urls)
        self.assertNotIn(RS.norm_url('https://model.said/it'), urls)     # 모델이 쓴 질의
        self.assertNotIn(RS.norm_url('https://made.up/x'), urls)         # 모델이 쓴 본문

    def test_fallback_tool_when_no_source_matches(self):
        g = self.value_group()
        calls = []

        class Cl:
            messages = None

            def create(self, **kw):
                calls.append(kw['tools'][0]['type'])
                seen = URL if kw['tools'][0]['type'] == 'web_search_20250305' else 'https://other.com/x'
                return {'stop_reason': 'end_turn', 'usage': usage(),
                        'content': [search_block(seen), answer({'people': [burry()]})]}
        cl = Cl()
        cl.messages = cl
        raw, urls, errs = RS._one(cl, CFG, RS.people_prompt(g, self.win, CFG), 5, RS.Spend(),
                                  log=lambda *a: None)
        self.assertEqual(calls, ['web_search_20260209', 'web_search_20250305'])
        self.assertIn(RS.norm_url(URL), urls)
        self.assertTrue(any('재조사' in e for e in errs))
        # 하나라도 맞으면 다시 부르지 않는다
        calls.clear()
        RS._one(cl, dict(CFG, search_tool='web_search_20250305'),
                RS.people_prompt(g, self.win, CFG), 5, RS.Spend(), log=lambda *a: None)
        self.assertEqual(len(calls), 1)

    def test_extract_json(self):
        self.assertEqual(RS.extract_json('a {"x": 1} b {"y": {"z": 2}}'), {'y': {'z': 2}})


class TestSynth(unittest.TestCase):
    def research(self):
        win = RS.window(NOW, CFG)
        return {'window': win, 'market': {'line': 'S&P500 -0.77%(7,683.69)', 'sources': []},
                'groups': [{'key': 'v', 'title': 'x', 'people': [
                    dict(name='Michael Burry', org='Scion', status='pass',
                         remark='5,730억 달러 매트릭스를 인용했습니다.', implication='', sources=[])]}]}

    def test_stray_number_retry_then_drop(self):
        good = dict(macro='금리가 5.24%로 올랐습니다.', sectors=['AI: 설명'], positions='p',
                    contrarian='c', risks=[{'title': 'r', 'body': '7,683.69 수준'}])
        bad = dict(good, macro='금리가 5.24%로 올랐습니다.')
        r = self.research()
        r['market']['line'] += ' 10년물 5.24%'
        cl = FakeClient(synth=[dict(good, positions='99% 확률'), good])
        out, drops, _ = SY.run(cl, CFG, r, log=lambda *a: None)
        self.assertEqual(out['positions'], 'p')                      # 되물어 고쳐졌다
        self.assertFalse(drops)
        cl = FakeClient(synth=[dict(bad, positions='99%'), dict(bad, positions='99%')])
        out, drops, _ = SY.run(cl, CFG, r, log=lambda *a: None)
        self.assertEqual(out['positions'], '')                       # 두 번 다 새면 칸을 뺀다
        self.assertTrue(drops)


class TestRenderAndSend(TempState):
    def build(self, **kw):
        people = {'Michael Burry': burry(), 'Cathie Wood': dict(
            burry(), name='Cathie Wood', remark='"Indeed" 라고 동의했습니다.'),
            'Ray Dalio': {'name': 'Ray Dalio', 'status': 'none', 'note': '9/22 Fortune 인터뷰에서 경고했습니다.',
                          'note_sources': [{'url': URL, 'outlet': 'Fortune', 'date': '2026-09-22'}]}}
        for g in CFG['groups']:
            for p in g['people']:
                people.setdefault(p['name'], {'name': p['name'], 'status': 'none'})
        synth = dict(macro='금리와 유가가 올랐습니다.', sectors=['AI 보안: 설명', '우주: 설명'],
                     positions='포지션', contrarian='컨트래리언',
                     risks=[{'title': '장기금리', 'body': '본문'}])
        cl = FakeClient(people=people, synth=[synth],
                        market={'line': 'S&P500 -0.77%', 'sources': [
                            {'url': URL, 'outlet': 'CNBC', 'date': '2026-09-28'}]}, **kw)
        rc = GP.build(cl=cl, cfg=CFG, now=NOW, log=lambda *a: None)
        self.assertEqual(rc, 0)
        return RD.read('2026-09-29', 'brief.txt'), cl

    def test_format(self):
        text, _ = self.build()
        lines = text.split('\n')
        self.assertEqual(lines[0], '글로벌 투자 구루 브리핑 | 2026.09.29 (화)')
        self.assertEqual(lines[2], '검증 기준: 2026.09.28(월) 미국장 뉴스 사이클 '
                                   '(KST 09.29 06:40 기준 최근 24시간)')
        self.assertEqual(lines[3], '당일 발언 확인(PASS): 2명 / 36명 (Michael Burry, Cathie Wood)')
        self.assertIn('시장 배경 (9/28 미국장): S&P500 -0.77%', text)
        self.assertIn('[오늘의 투자 시사점 종합]\n\n매크로 방향성\n', text)
        self.assertIn('섹터별 힌트\n1) AI 보안: 설명 2) 우주: 설명', text)
        self.assertIn('Ray Dalio (Bridgewater)\n금일 발언 없음 (참고: 9/22 Fortune 인터뷰에서 경고했습니다.)', text)
        self.assertIn('Michael Burry (Scion)\nAI 버블 경고를 했습니다.\n\n→ 하방 헤지를 검토할 시점입니다.', text)
        self.assertIn('[리스크 & 경고 신호]\n\n1. 장기금리\n본문', text)
        self.assertIn('다음 브리핑: 2026.09.30(수) 오전 KST', text)
        self.assertIn('출처: CNBC, Fortune', text)
        self.assertTrue(text.rstrip().endswith(RD.DISCLAIMER))
        self.assertNotIn('※ 확인 못 한 것', text)

    def test_split_under_limit(self):
        text, _ = self.build()
        long = RD.blocks(json.loads(json.dumps(RD.read('2026-09-29', 'research.json'))),
                         RD.read('2026-09-29', 'synth.json')['synth'], CFG)
        parts = RD.split(long, limit=1500)
        self.assertTrue(all(len(p) <= 1500 for p in parts))
        self.assertGreater(len(parts), 1)
        self.assertEqual(RD.text(long), text.rstrip('\n'))

    def test_credit_failure_is_named_on_top(self):
        out = GP.summarize_drops(['growth 조사 실패 — BadRequestError: 400 Your credit balance is too low'])
        self.assertIn('크레딧', out[0])

    def test_debug_saved(self):
        self.build()
        dbg = RD.read('2026-09-29', 'research.json')['debug']
        self.assertEqual(dbg['value']['matched'], dbg['value']['claimed'])
        self.assertIn('market', dbg)

    def test_all_failed_sends_one_line(self):
        class Broke:
            def create(self, **kw):
                raise RuntimeError('Error code: 400 - Your credit balance is too low')
        cl = Broke()
        cl.messages = cl
        GP.build(cl=cl, cfg=CFG, now=NOW, log=lambda *a: None)
        text = RD.read('2026-09-29', 'brief.txt')
        self.assertIn('조사 실패, 오늘 브리핑 없음', text)
        self.assertIn('크레딧', text)
        self.assertNotIn('[헤지펀드 매니저]', text)

    def test_budget_stops_calls(self):
        GP.cost_add('2026-09-01', 31.0)
        cl = FakeClient()
        GP.build(cl=cl, cfg=CFG, now=NOW, log=lambda *a: None)
        self.assertEqual(cl.calls, [])
        self.assertIn('예산', RD.read('2026-09-29', 'brief.txt'))

    def test_not_a_run_day(self):
        cl = FakeClient()
        GP.build(cl=cl, cfg=CFG, now=datetime(2026, 9, 28, 6, 40, tzinfo=RS.KST), log=lambda *a: None)
        self.assertEqual(cl.calls, [])
        self.assertIsNone(RD.read('2026-09-28', 'brief.txt'))

    def test_send_once_and_resume(self):
        self.build()
        os.environ['XDIGEST_CHAT_ID'] = '1'
        try:
            sent = []

            def flaky(text, chat_id=None, parse_mode='x'):
                sent.append(text)
                self.assertIsNone(parse_mode)
                return (len(sent) != 2, 'ok' if len(sent) != 2 else 'HTTP 500')

            # 3통 이상이 되도록 한도를 낮춰 나눈다
            orig = RD.LIMIT
            RD.LIMIT = 1500
            try:
                RD.split.__defaults__ = (1500,)
                self.assertEqual(GP.send('2026-09-29', once=True, sender=flaky, log=lambda *a: None), 1)
                n1 = len(sent)
                self.assertEqual(GP.send('2026-09-29', once=True, sender=flaky, log=lambda *a: None), 0)
                # 두 번째 실행은 실패한 통부터 — 첫 통을 다시 보내지 않는다
                self.assertNotEqual(sent[n1], sent[0])
                self.assertEqual(GP.mark(), '2026-09-29')
                before = len(sent)
                GP.send('2026-09-29', once=True, sender=flaky, log=lambda *a: None)
                self.assertEqual(len(sent), before)                  # 표식이 막는다
            finally:
                RD.LIMIT = orig
                RD.split.__defaults__ = (orig,)
        finally:
            os.environ.pop('XDIGEST_CHAT_ID', None)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""블루스카이 수집기 단위 시험 — 파싱·창·중복·막힘·커버리지·상한·간격.

**네트워크는 스텁이다.** 이 환경은 프록시가 외부를 막고(블루스카이 403),
라이브 호출은 러너에서만 돈다. 그래서 파서와 수집 규약을 픽스처로 못 박아 둔다 —
그러지 않으면 러너 실측을 읽을 때 '파서가 틀린 것' 과 '경로가 막힌 것' 을 가릴
수 없고, 실측을 한 번 더 돌려야 한다(`test_xsource.py` 머리와 같은 이유).
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

from ..ingest import bsky as BS

KST = timezone(timedelta(hours=9))

# 창: 2026-09-21 08:45 ~ 2026-09-22 08:45 KST (기준일 2026-09-22)
ASOF = '2026-09-22'
WIN = dict(start='2026-09-21T08:45:00+09:00', end='2026-09-22T08:45:00+09:00')

CFG = dict(queries=['HBM', 'semiconductor', 'DRAM', 'TSMC'], limit=100,
           sort='latest', window_end_hm='08:45', window_hours=24,
           per_call_sleep_sec=2, timeout_sec=10, retries=1, max_posts=400)


def rec(rkey, handle='someone.bsky.social', text='HBM 단가 하락',
        created='2026-09-22T01:12:41.123Z', langs=('en',), did='did:plc:abc123',
        display='Someone'):
    """searchPosts 응답 한 건. 실제 응답의 필드 이름·꼴을 그대로 쓴다."""
    return {
        'uri': f'at://{did}/app.bsky.feed.post/{rkey}',
        'cid': 'bafy' + rkey,
        'author': {'did': did, 'handle': handle, 'displayName': display},
        'record': {'$type': 'app.bsky.feed.post', 'text': text,
                   'createdAt': created, 'langs': list(langs)},
    }


class Resp:
    """requests 응답의 최소 형태. `http.why` 가 text·json() 을 본다."""

    def __init__(self, status=200, body=None, ctype='application/json'):
        self.status_code = status
        self.headers = {'content-type': ctype}
        self._body = body

    @property
    def text(self):
        if isinstance(self._body, str):
            return self._body
        return json.dumps(self._body or {})

    def json(self):
        if isinstance(self._body, str):
            raise ValueError('not json')
        return self._body or {}


class Stub:
    """호스트·질의별로 답을 정해 주는 세션. 부른 내역을 남긴다."""

    def __init__(self, answers, stamp=None):
        # answers: {(호스트, 질의) 또는 질의 또는 None: Resp}
        self.answers, self.seen, self.stamp = answers, [], stamp

    def get(self, url, params=None, timeout=None):
        host = url.split('/')[2]
        q = (params or {}).get('q')
        self.seen.append((host, q, timeout, self.stamp() if self.stamp else None))
        for key in ((host, q), q, None):
            if key in self.answers:
                return self.answers[key]
        return Resp(200, {'posts': []})


def factory(stub):
    return lambda: stub


class TestParse(unittest.TestCase):
    """파싱은 수집과 분리되어 있다. 픽스처 응답 하나로 못 박는다."""

    def test_uri_and_handle_make_the_web_url(self):
        rows = BS.parse({'posts': [rec('3l4xyz', handle='pol.bsky.social')]}, 'HBM')
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r['id'], 'at://did:plc:abc123/app.bsky.feed.post/3l4xyz')
        self.assertEqual(r['url'],
                         'https://bsky.app/profile/pol.bsky.social/post/3l4xyz')
        self.assertEqual(r['account'], 'pol.bsky.social')
        self.assertEqual(r['display_name'], 'Someone')
        self.assertEqual(r['source'], 'bluesky')
        self.assertEqual(r['query'], 'HBM')

    def test_langs_and_text_come_through_unchanged(self):
        raw = rec('a1', text='HBM  평균 수출단가 $73.39, 전월 대비 -3.6%',
                  langs=('en', 'ko'))
        r = BS.parse([raw], 'HBM')[0]
        # 원문 그대로여야 한다 — 검증(3-3)이 이 문자열과 대조한다.
        self.assertEqual(r['text'], 'HBM  평균 수출단가 $73.39, 전월 대비 -3.6%')
        self.assertEqual(r['langs'], ['en', 'ko'])

    def test_created_at_becomes_kst(self):
        r = BS.parse([rec('a1', created='2026-09-21T16:12:41.123Z')], 'HBM')[0]
        self.assertEqual(r['posted_at'], '2026-09-22T01:12:41+09:00')

    def test_created_at_with_offset_or_long_fraction_is_read(self):
        for raw, want in (
                ('2026-09-22T01:12:41+09:00', '2026-09-22T01:12:41+09:00'),
                ('2026-09-21T16:12:41.1234567Z', '2026-09-22T01:12:41+09:00'),
                ('2026-09-21T16:12:41', '2026-09-22T01:12:41+09:00'),
        ):
            self.assertEqual(BS.parse([rec('a1', created=raw)], 'q')[0]['posted_at'],
                             want, raw)

    def test_langs_missing_is_empty_list_not_none(self):
        raw = rec('a1')
        del raw['record']['langs']
        self.assertEqual(BS.parse([raw], 'q')[0]['langs'], [])

    def test_records_without_uri_text_or_time_are_dropped(self):
        bad = []
        r = rec('a1')
        r['uri'] = ''
        bad.append(r)
        r = rec('a2')
        del r['record']['text']
        bad.append(r)
        r = rec('a3')
        r['record']['createdAt'] = '어제'
        bad.append(r)
        self.assertEqual(BS.parse({'posts': bad}, 'q'), [])

    def test_records_without_a_handle_are_dropped(self):
        """핸들이 없으면 `account` 가 비고 `url` 이 null 인 행이 된다 — 그 행이
        본문에 실리면 `└` 출처 줄이 비어 2장 2번을 어긴다. 조용히 남기지 않는다.
        """
        noauth = rec('noauth')
        del noauth['author']
        empty = rec('empty', handle='')
        self.assertEqual(BS.parse({'posts': [noauth, empty]}, 'q'), [])

    def test_photo_only_post_with_blank_text_is_dropped(self):
        """빈 text 는 검증(3-3)이 대조할 문자열이 없어 다음 단계가 쓸 수 없다."""
        for t in ('', '   ', '\n'):
            self.assertEqual(BS.parse([rec('a1', text=t)], 'q'), [], repr(t))

    def test_empty_and_garbage_do_not_raise(self):
        for js in ({}, {'posts': None}, [], None, {'posts': ['문자열']}):
            self.assertEqual(BS.parse(js, 'q'), [])


class TestWindow(unittest.TestCase):
    def setUp(self):
        # 대기를 갈아 끼우면 되돌린다. 안 되돌리면 `SLEEP` 을 보는 다른 시험이
        # 이 시험보다 뒤에 도는 순서에서만 통과하는 순서 의존 시험이 된다.
        self._sleep = BS.SLEEP
        BS.SLEEP = lambda *_a: None

    def tearDown(self):
        BS.SLEEP = self._sleep

    def test_window_is_24_hours_back_from_0845(self):
        w = BS.window_of('2026-09-22', CFG)
        self.assertEqual(w, WIN)

    def test_boundary_start_is_in_and_end_is_out(self):
        """경계는 시작 포함·끝 배타다(3-1).

        양끝을 다 포함하면 08:45:00 정각 게시물이 D 와 D+1 두 기준일의 창에
        동시에 들어 이틀 연속 본문에 실린다(10장 T5 '중복 0'). 창 밖은 그래도
        버리지 않고 in_window:false 로 남긴다.
        """
        stub = Stub({None: Resp(200, {'posts': [
            rec('before', created='2026-09-20T23:44:59Z'),   # 08:44:59 KST
            rec('at_start', created='2026-09-20T23:45:00Z'),  # 08:45:00 KST
            rec('after_end', created='2026-09-21T23:45:01Z'),  # 다음날 08:45:01
            rec('at_end', created='2026-09-21T23:45:00Z'),
        ]})})
        out = BS.collect(ASOF, WIN, CFG, log=lambda *_a: None,
                         session_factory=factory(stub))
        flag = {p['id'].rsplit('/', 1)[-1]: p['in_window'] for p in out['posts']}
        self.assertEqual(flag, dict(before=False, at_start=True,
                                    at_end=False, after_end=False))
        # 버리지 않았다.
        self.assertEqual(out['sources']['bluesky']['kept'], 4)
        self.assertEqual(out['sources']['bluesky']['in_window'], 1)

    def test_same_post_is_in_only_one_of_two_consecutive_windows(self):
        """08:45:00 정각 한 건이 연속된 두 창에 동시에 들지 않는다."""
        stub = Stub({None: Resp(200, {'posts': [
            rec('edge', created='2026-09-21T23:45:00Z')]})})   # 09-22 08:45:00 KST
        got = []
        for asof in ('2026-09-22', '2026-09-23'):
            out = BS.collect(asof, BS.window_of(asof, CFG), CFG,
                             log=lambda *_a: None, session_factory=factory(stub))
            got.append(out['posts'][0]['in_window'])
        self.assertEqual(got, [False, True])


class TestCollect(unittest.TestCase):
    def setUp(self):
        self._sleep = BS.SLEEP
        BS.SLEEP = lambda *_a: None

    def tearDown(self):
        BS.SLEEP = self._sleep

    def _run(self, stub, cfg=None, **kw):
        return BS.collect(ASOF, WIN, cfg or CFG, log=lambda *_a: None,
                          session_factory=factory(stub), **kw)

    def test_same_post_in_two_queries_is_one_and_keeps_the_first_query(self):
        same = rec('dup', created='2026-09-22T00:00:00+09:00')
        stub = Stub({'HBM': Resp(200, {'posts': [same]}),
                     'semiconductor': Resp(200, {'posts': [same]}),
                     None: Resp(200, {'posts': []})})
        out = self._run(stub)
        self.assertEqual(len(out['posts']), 1)
        self.assertEqual(out['posts'][0]['query'], 'HBM')
        # got 은 응답 원본 합이라 중복을 센다. kept 는 제거 뒤다.
        self.assertEqual(out['sources']['bluesky']['got'], 2)
        self.assertEqual(out['sources']['bluesky']['kept'], 1)

    def test_one_blocked_query_does_not_stop_the_others(self):
        stub = Stub({('api.bsky.app', 'DRAM'): Resp(429, 'rate limited', 'text/plain'),
                     ('public.api.bsky.app', 'DRAM'):
                         Resp(403, '<html>forbidden</html>', 'text/html'),
                     None: Resp(200, {'posts': [
                         rec('x', created='2026-09-22T00:00:00+09:00')]})})
        out = self._run(stub)
        b = out['sources']['bluesky']
        self.assertEqual(b['blocked'], 1)
        # 나머지 셋은 돌았다 — 같은 게시물이라 중복 제거로 1건이지만 질의는 넷 다 눌렀다.
        # 막힌 DRAM 은 쉬고 한 번 더 누른다(block_cooldown — 2026-09-23 미리보기).
        self.assertEqual([q for _h, q, _t, _s in stub.seen if _h == 'api.bsky.app'],
                         ['HBM', 'semiconductor', 'DRAM', 'DRAM', 'TSMC'])
        self.assertEqual(b['kept'], 1)
        self.assertTrue(any('DRAM' in e and '막힘' in e for e in b['errors']), b['errors'])
        # 호스트마다 사유가 남는다 — api 429 와 public 403 은 다른 사실이다.
        joined = ' '.join(b['errors'])
        self.assertIn('api.bsky.app: HTTP 429', joined)
        self.assertIn('public.api.bsky.app: HTTP 403', joined)
        self.assertIn('rate limited', joined)

    def test_blocked_query_is_retried_after_a_cooldown_up_to_the_cap(self):
        """막힘은 한도다 — 쉬고 같은 질의를 한 번 더. 쉬는 횟수에는 상한이 있다."""
        slept = []
        BS.SLEEP, keep = slept.append, BS.SLEEP
        try:
            calls = {'DRAM': 0}

            class Once(Stub):
                def get(self, url, params=None, timeout=None):
                    q = params['q']
                    if q == 'DRAM':
                        calls['DRAM'] += 1
                        if calls['DRAM'] <= 2:               # 첫 시도: 두 호스트 다 막힘
                            return Resp(403, '<html>forbidden</html>', 'text/html')
                    if q == 'TSMC':
                        return Resp(403, '<html>forbidden</html>', 'text/html')
                    return Resp(200, {'posts': []})

            cfg = {**CFG, 'block_cooldown_sec': 7, 'block_cooldowns': 1}
            out = BS.collect(ASOF, WIN, cfg, log=lambda *_a: None,
                             session_factory=factory(Once({})))
        finally:
            BS.SLEEP = keep
        b = out['sources']['bluesky']
        # DRAM 은 쉰 뒤 받았다. TSMC 는 상한(1회)을 다 써서 쉬지 않고 결손으로 남는다.
        self.assertEqual(b['blocked'], 1)
        self.assertEqual(slept.count(7), 1)
        self.assertTrue(any('TSMC' in e for e in b['errors']))
        self.assertFalse(any('DRAM' in e and '막힘' in e for e in b['errors']))

    def test_blocked_query_becomes_a_coverage_gap(self):
        """머리 3행이 coverage.gaps 에서 만들어진다 — 여기 없으면 발송문에서 사라진다."""
        stub = Stub({'TSMC': Resp(503, 'maintenance', 'text/plain'),
                     None: Resp(200, {'posts': []})})
        out = self._run(stub)
        whys = [g['why'] for g in out['coverage']['gaps']]
        self.assertTrue(any('TSMC' in w for w in whys), whys)

    def test_fallback_host_is_tried_and_counted(self):
        stub = Stub({('api.bsky.app', 'HBM'):
                     Resp(403, '<html>blocked</html>', 'text/html'),
                     ('public.api.bsky.app', 'HBM'): Resp(200, {'posts': [
                         rec('viapublic', created='2026-09-22T00:00:00+09:00')]}),
                     None: Resp(200, {'posts': []})})
        out = self._run(stub)
        b = out['sources']['bluesky']
        # 폴백으로 받았으니 막힌 것도 실패한 것도 아니다.
        self.assertEqual(b['blocked'], 0)
        self.assertEqual(b['failed'], 0)
        self.assertEqual(b['kept'], 1)
        # 질의 4개인데 호출은 5회 — 폴백 한 번이 숫자에 남는다.
        self.assertEqual(b['queries'], 4)
        self.assertEqual(b['calls'], 5)
        # 앞 호스트의 403 을 삼키지 않는다. calls 가 하나 많다는 것만으로는
        # '왜' 를 알 수 없다(2장 6번).
        self.assertTrue(any('폴백으로 받음' in e and 'HBM' in e for e in b['errors']),
                        b['errors'])
        self.assertIn('api.bsky.app: HTTP 403', ' '.join(b['errors']))
        # 받았으므로 결손이 아니다 — gaps 에는 구조적 한계 한 줄만 있다.
        self.assertEqual(len(out['coverage']['gaps']), 1)

    def test_all_blocked_leaves_the_file_with_the_reason(self):
        stub = Stub({None: Resp(403, '<html>Forbidden</html>', 'text/html')})
        out = self._run(stub)
        b = out['sources']['bluesky']
        self.assertEqual(out['posts'], [])
        self.assertEqual(b['blocked'], 4)
        self.assertEqual(b['failed'], 4)
        self.assertEqual(len(b['errors']), 4)
        self.assertIsNone(out['coverage']['first'])
        # '없다' 가 아니라 '막혔다' 라고 적혀 있어야 한다.
        joined = ' '.join(g['why'] for g in out['coverage']['gaps'])
        self.assertIn('막힘', joined)
        self.assertIn('403', joined)
        # 파일은 남는다.
        d = tempfile.mkdtemp()
        try:
            BS.XSTATE = os.path.join(d, 'xdigest')
            p = BS.save(ASOF, out)
            self.assertTrue(os.path.exists(p))
            with open(p, encoding='utf-8') as f:
                self.assertEqual(json.load(f)['sources']['bluesky']['blocked'], 4)
        finally:
            BS.XSTATE = os.path.join(BS.STATE, 'xdigest')
            shutil.rmtree(d, ignore_errors=True)

    def test_one_host_blocked_and_the_other_dead_is_not_blocked(self):
        """403 + 전송 오류는 '막힘' 이 아니다 — 계약의 blocked 는 두 호스트가
        다 막힌 질의만 센다(3-1). 뒤 호스트가 연결이 안 된 것을 막혔다고 적으면
        모르는 것을 판정한 것이 된다(2장 1번).
        """
        class Half:
            def get(self, url, params=None, timeout=None):
                if url.split('/')[2] == 'api.bsky.app':
                    return Resp(403, '<html>forbidden</html>', 'text/html')
                raise OSError('proxy reset')
        out = self._run(Half())
        b = out['sources']['bluesky']
        self.assertEqual(b['blocked'], 0)
        # 받은 것은 없으므로 실패 질의로는 센다.
        self.assertEqual(b['failed'], 4)
        joined = ' '.join(b['errors'])
        self.assertIn('실패', joined)
        self.assertNotIn('막힘', joined)
        # 사유는 호스트마다 따로 남는다.
        self.assertIn('api.bsky.app: HTTP 403', joined)
        self.assertIn('public.api.bsky.app: OSError', joined)

    def test_transport_error_is_not_blocked_but_is_recorded(self):
        """연결 실패는 403 과 다르다. 막힌 것이라 단정하지 않고 사유만 남긴다."""
        class Boom:
            def get(self, *_a, **_kw):
                raise OSError('연결 거부')
        out = self._run(Boom())
        b = out['sources']['bluesky']
        self.assertEqual(b['blocked'], 0)
        # 막힌 것으로 세지 않되 받지 못한 질의로는 센다 — 워크플로의 '전부 실패'
        # 판정이 이 값이다.
        self.assertEqual(b['failed'], 4)
        self.assertEqual(len(b['errors']), 4)
        self.assertIn('OSError', b['errors'][0])

    def test_coverage_is_the_window_range_of_in_window_posts(self):
        stub = Stub({'HBM': Resp(200, {'posts': [
            rec('a', created='2026-09-21T09:14:02+09:00'),
            rec('b', created='2026-09-22T08:31:55+09:00'),
            rec('old', created='2026-09-19T12:00:00+09:00'),   # 창 밖
        ]}), None: Resp(200, {'posts': []})})
        out = self._run(stub)
        self.assertEqual(out['coverage']['first'], '2026-09-21T09:14:02+09:00')
        self.assertEqual(out['coverage']['last'], '2026-09-22T08:31:55+09:00')
        # 창 밖은 커버 구간에 넣지 않는다 — 넣으면 구간이 거짓으로 길어진다.
        self.assertEqual(out['sources']['bluesky']['in_window'], 2)
        self.assertEqual(out['coverage']['gaps'][0]['why'],
                         '질의 4개 · 질의당 상위 100건까지 — 그 밖은 알 수 없음')

    def test_posts_are_newest_first(self):
        stub = Stub({'HBM': Resp(200, {'posts': [
            rec('old2', created='2026-09-21T10:00:00+09:00'),
            rec('new2', created='2026-09-22T07:00:00+09:00'),
        ]}), None: Resp(200, {'posts': []})})
        out = self._run(stub)
        self.assertEqual([p['id'].rsplit('/', 1)[-1] for p in out['posts']],
                         ['new2', 'old2'])

    def test_max_posts_keeps_in_window_newest_and_says_what_it_cut(self):
        inw = [rec(f'i{n}', created=f'2026-09-22T0{n}:00:00+09:00') for n in range(1, 6)]
        outw = [rec(f'o{n}', created=f'2026-09-19T0{n}:00:00+09:00') for n in range(1, 4)]
        stub = Stub({'HBM': Resp(200, {'posts': outw + inw}),
                     None: Resp(200, {'posts': []})})
        out = self._run(stub, cfg=dict(CFG, max_posts=4))
        kept = [p['id'].rsplit('/', 1)[-1] for p in out['posts']]
        # 창 안 최신 넷만 남는다. 창 밖이 먼저 버려진다.
        self.assertEqual(kept, ['i5', 'i4', 'i3', 'i2'])
        b = out['sources']['bluesky']
        self.assertEqual(b['kept'], 4)
        self.assertIn('max_posts 4 상한', b['cut'])
        self.assertIn('4건을 버렸다', b['cut'])
        # 조용히 자르지 않는다 — 결손 줄이 읽을 자리에도 적는다.
        self.assertTrue(any('max_posts' in g['why'] for g in out['coverage']['gaps']))

    def test_dropped_records_are_reported_not_swallowed(self):
        bad = rec('bad')
        del bad['record']['text']
        stub = Stub({'HBM': Resp(200, {'posts': [
            bad, rec('good', created='2026-09-22T00:00:00+09:00')]}),
            None: Resp(200, {'posts': []})})
        out = self._run(stub)
        b = out['sources']['bluesky']
        self.assertEqual(b['got'], 2)
        self.assertEqual(b['kept'], 1)
        self.assertTrue(any('파싱에서 뺐다' in e for e in b['errors']), b['errors'])
        # 실패 질의는 없으므로 '전부 실패' 로 읽히면 안 된다.
        self.assertEqual(b['failed'], 0)
        # 파싱 드롭은 결손이 아니다 — 머리 3행에 파서 내부 문구가 나가면 안 된다.
        self.assertEqual([g['why'] for g in out['coverage']['gaps']],
                         ['질의 4개 · 질의당 상위 100건까지 — 그 밖은 알 수 없음'])

    def test_contract_shape_is_exactly_what_the_next_stage_reads(self):
        stub = Stub({None: Resp(200, {'posts': []})})
        out = self._run(stub, now='2026-09-22T08:46:12+09:00')
        self.assertEqual(sorted(out), ['asof', 'collected_at', 'coverage',
                                       'posts', 'sources', 'window'])
        self.assertEqual(out['asof'], ASOF)
        self.assertEqual(out['window'], WIN)
        self.assertEqual(out['collected_at'], '2026-09-22T08:46:12+09:00')
        self.assertEqual(sorted(out['sources']['bluesky']),
                         ['blocked', 'by_topic', 'calls', 'cut', 'errors', 'failed',
                          'got', 'in_window', 'kept', 'queries'])
        self.assertEqual(sorted(out['coverage']), ['first', 'gaps', 'last'])


class TestSections(unittest.TestCase):
    """별도 구획(D-NEXT-Q) — 질의가 구획을 정하고, 게시물은 처음 잡힌 구획으로 간다."""

    SECS = [dict(key='ai_news', title='AI 최신 뉴스', label='AI뉴스', limit=50,
                 queries=['OpenAI', 'LLM']),
            dict(key='quant', title='퀀트·백테스트', queries=['backtest', '퀀트'])]

    def setUp(self):
        self._sleep = BS.SLEEP
        BS.SLEEP = lambda *_a: None

    def tearDown(self):
        BS.SLEEP = self._sleep

    def _cfg(self, **over):
        return {**CFG, 'queries': ['HBM', 'semiconductor'], 'sections': self.SECS, **over}

    def test_plan_puts_the_main_queries_first_with_their_limits(self):
        self.assertEqual(BS.plan(self._cfg()),
                         [('HBM', 'semi', 100), ('semiconductor', 'semi', 100),
                          ('OpenAI', 'ai_news', 50), ('LLM', 'ai_news', 50),
                          ('backtest', 'quant', 100), ('퀀트', 'quant', 100)])

    def test_same_query_in_two_sections_is_called_once(self):
        cfg = self._cfg(sections=[dict(key='x', queries=['HBM', 'LLM'])])
        self.assertEqual([q for q, _k, _l in BS.plan(cfg)], ['HBM', 'semiconductor', 'LLM'])

    def test_bad_section_entries_are_named_not_swallowed(self):
        secs, skipped = BS.sections(dict(sections=[
            dict(key='semi', queries=['a']), dict(title='키 없음', queries=['b']),
            dict(key='ok', queries=[]), 'x', dict(key='ok2', queries=['c'])]))
        self.assertEqual([s['key'] for s in secs], ['ok2'])
        self.assertEqual(len(skipped), 4)
        # 제목·표기가 없으면 키가 대신한다 — 빈 제목의 ■ 를 만들지 않는다.
        self.assertEqual((secs[0]['title'], secs[0]['label']), ('ok2', 'ok2'))

    def test_posts_carry_the_topic_of_the_first_query(self):
        same = rec('dup', text='OpenAI HBM order', created='2026-09-22T00:00:00+09:00')
        stub = Stub({'HBM': Resp(200, {'posts': [same]}),
                     'OpenAI': Resp(200, {'posts': [same, rec(
                         'ai1', text='OpenAI ships a model',
                         created='2026-09-22T01:00:00+09:00')]}),
                     '퀀트': Resp(200, {'posts': [rec(
                         'q1', text='퀀트 전략 백테스트', created='2026-09-22T02:00:00+09:00')]}),
                     None: Resp(200, {'posts': []})})
        logs = []
        out = BS.collect(ASOF, WIN, self._cfg(), log=logs.append,
                         session_factory=factory(stub))
        topic = {p['id'].rsplit('/', 1)[-1]: p['topic'] for p in out['posts']}
        # 반도체 질의가 먼저 잡은 글은 AI 질의에 또 걸려도 기본 구획이다.
        self.assertEqual(topic, {'dup': 'semi', 'ai1': 'ai_news', 'q1': 'quant'})
        b = out['sources']['bluesky']
        self.assertEqual(b['by_topic'], {'semi': 1, 'ai_news': 1, 'quant': 1})
        self.assertEqual(b['queries'], 6)
        # 구획 limit 이 요청에 그대로 실린다 — 호출 수는 질의 수와 같다.
        self.assertEqual(b['calls'], 6)
        self.assertEqual(out['coverage']['gaps'][0]['why'],
                         '질의 6개 · 질의당 상위 50·100건까지 — 그 밖은 알 수 없음')
        self.assertTrue(any('[ai_news]' in l for l in logs), logs)

    def test_section_limit_reaches_the_request(self):
        seen = []

        class Rec(Stub):
            def get(self, url, params=None, timeout=None):
                seen.append((params['q'], params['limit']))
                return Resp(200, {'posts': []})

        BS.collect(ASOF, WIN, self._cfg(), log=lambda *_a: None,
                   session_factory=factory(Rec({})))
        self.assertEqual(dict(seen)['OpenAI'], 50)
        self.assertEqual(dict(seen)['HBM'], 100)

    def test_section_sort_top_sends_the_window_and_lang(self):
        """`sort: top` 구획은 창을 since·until 로 준다 — 안 주면 몇 달 전 인기글이 섞인다."""
        seen = {}

        class Rec(Stub):
            def get(self, url, params=None, timeout=None):
                seen[params['q']] = dict(params)
                return Resp(200, {'posts': []})

        secs = [dict(key='ai_news', queries=['OpenAI'], sort='top'),
                dict(key='quant', queries=['quant'], lang='en')]
        BS.collect(ASOF, WIN, self._cfg(sections=secs), log=lambda *_a: None,
                   session_factory=factory(Rec({})))
        ai, qt, semi = seen['OpenAI'], seen['quant'], seen['HBM']
        self.assertEqual(ai['sort'], 'top')
        self.assertEqual((ai['since'], ai['until']),
                         (BS._utc_z(WIN['start']), BS._utc_z(WIN['end'])))
        self.assertTrue(ai['since'].endswith('Z'))
        self.assertNotIn('lang', ai)
        self.assertEqual((qt['sort'], qt['lang']), ('latest', 'en'))
        self.assertNotIn('since', qt)
        # 기본 구획은 그대로다 — 최신순, 창·언어 없음.
        self.assertEqual(semi['sort'], 'latest')
        self.assertFalse({'since', 'until', 'lang'} & set(semi))

    def test_real_config_has_the_two_sections(self):
        cfg = BS.load_cfg()
        secs, skipped = BS.sections(cfg)
        self.assertEqual(skipped, [])
        self.assertEqual([(s['key'], s['title'], s['label']) for s in secs],
                         [('ai_news', 'AI 최신 뉴스', 'AI뉴스'),
                          ('quant', '퀀트·백테스트', '퀀트')])
        for s in secs:
            self.assertTrue(s['queries'] and s['keywords'] and s['scope'], s['key'])
            self.assertLessEqual(s['limit'] or cfg['limit'], 100)
        # 우리말 질의(퀀트)는 2026-09-23 실측 24시간 0건이라 뺐다 — 설정 주석에
        # 실측표가 있다. 대신 AI 구획은 인기순이어야 한다: 최신순 100건이 한두
        # 시간치라(같은 날 실측 100/100) 24시간 다이제스트가 되지 못한다.
        self.assertEqual(secs[0]['sort'], 'top')
        self.assertIsNone(secs[1]['sort'])
        # 키워드는 소문자여야 한다 — 비교가 소문자 본문과 한다.
        for s in secs:
            self.assertEqual(s['keywords'], [k.lower() for k in s['keywords']])


class TestPacing(unittest.TestCase):
    """질의 사이 2초가 **실제로** 들어가는지 — 시계를 주입해 본다."""

    def setUp(self):
        self._sleep = BS.SLEEP

    def tearDown(self):
        BS.SLEEP = self._sleep

    def test_two_seconds_between_queries(self):
        box = dict(t=datetime(2026, 9, 22, 8, 46, 0, tzinfo=KST))
        slept = []

        def clock():
            return box['t']

        def sleep(sec):
            slept.append(sec)
            box['t'] = box['t'] + timedelta(seconds=sec)

        BS.SLEEP = sleep
        stub = Stub({None: Resp(200, {'posts': []})}, stamp=clock)
        out = BS.collect(ASOF, WIN, CFG, log=lambda *_a: None,
                         session_factory=factory(stub), now=clock)
        # 질의 4개 → 사이가 3개. 질의 앞뒤로 쓸데없이 자지 않는다.
        self.assertEqual(slept, [2, 2, 2])
        stamps = [s for _h, _q, _t, s in stub.seen]
        gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
        self.assertEqual(gaps, [2.0, 2.0, 2.0])
        # collected_at 은 마지막 호출 뒤의 시각이다.
        self.assertEqual(out['collected_at'], '2026-09-22T08:46:06+09:00')

    def test_sleep_setting_is_read_from_config(self):
        slept = []
        BS.SLEEP = lambda sec: slept.append(sec)
        stub = Stub({None: Resp(200, {'posts': []})})
        BS.collect(ASOF, WIN, dict(CFG, per_call_sleep_sec=5, queries=['HBM', 'DRAM']),
                   log=lambda *_a: None, session_factory=factory(stub))
        self.assertEqual(slept, [5])

    def test_timeout_setting_reaches_the_request(self):
        BS.SLEEP = lambda *_a: None
        stub = Stub({None: Resp(200, {'posts': []})})
        BS.collect(ASOF, WIN, dict(CFG, timeout_sec=7, queries=['HBM']),
                   log=lambda *_a: None, session_factory=factory(stub))
        self.assertEqual([t for _h, _q, t, _s in stub.seen], [7])


class TestConfigFile(unittest.TestCase):
    """설정 파일이 실제로 있고, 코드 기본값과 어긋나지 않는지."""

    def test_config_has_the_keys_collect_reads(self):
        cfg = BS.load_cfg()
        for k in ('queries', 'limit', 'sort', 'window_end_hm', 'window_hours',
                  'per_call_sleep_sec', 'timeout_sec', 'retries', 'max_posts',
                  'probe_query', 'topics_label'):
            self.assertIn(k, cfg)
        self.assertEqual(cfg['queries'], list(BS.QUERIES))
        # limit 상한은 searchPosts 규정값 100 이다.
        self.assertLessEqual(cfg['limit'], 100)
        # 질의 × limit 보다 낮은 상한은 정상인 날에도 잘라 낸다 — 별도 구획의
        # 질의·limit 까지 더해서 본다(D-NEXT-Q).
        self.assertGreaterEqual(cfg['max_posts'],
                                sum(l for _q, _k, l in BS.plan(cfg)))


class TestProbe(unittest.TestCase):
    """`--check` 의 판정은 200 JSON 을 받았는가 하나다 — 물량은 판정이 아니다."""

    def setUp(self):
        self._session, self._sleep = BS.session, BS.SLEEP
        BS.SLEEP = lambda *_a: None

    def tearDown(self):
        BS.session, BS.SLEEP = self._session, self._sleep

    def _probe(self, stub, cfg=None):
        BS.session = lambda: stub
        return BS.probe(cfg or dict(CFG, probe_query='semiconductor'))[0]

    def test_no_recent_post_is_a_note_not_a_fail(self):
        """조용한 시간대의 0건을 빨강으로 올리면 막힌 날과 같은 신호가 된다."""
        old = (datetime.now(KST) - timedelta(hours=30)).isoformat()
        stub = Stub({None: Resp(200, {'posts': [rec('a1', created=old)]})})
        name, ok, note = self._probe(stub)
        self.assertIn('semiconductor', name)
        self.assertTrue(ok, note)
        self.assertIn('24시간 안 0건', note)

    def test_blocked_is_a_fail_with_the_reason(self):
        stub = Stub({None: Resp(403, '<html>forbidden</html>', 'text/html')})
        _name, ok, note = self._probe(stub)
        self.assertFalse(ok)
        self.assertIn('막힘', note)
        self.assertIn('403', note)

    def test_probe_query_is_read_from_config(self):
        """물량 최하위인 queries[0](HBM)을 누르지 않는다 — 설정으로 고른다."""
        stub = Stub({None: Resp(200, {'posts': []})})
        self._probe(stub)
        self.assertEqual([q for _h, q, _t, _s in stub.seen], ['semiconductor'])
        stub2 = Stub({None: Resp(200, {'posts': []})})
        self._probe(stub2, cfg=dict(CFG))       # probe_query 가 없으면 첫 질의
        self.assertEqual([q for _h, q, _t, _s in stub2.seen], ['HBM'])


class TestNoAuthPath(unittest.TestCase):
    """로그인은 **선택**이다 — 키가 없으면 로그인 요청 자체가 없어야 한다.

    예전 규칙은 '인증 경로가 아예 없다' 였다(XDIGEST.md 3-1). 2026-09-23 러너에서
    공개 호스트가 첫 호출부터 `403 · HTML(앞단 차단)` 이었고 — 우리 호출량과 무관하게
    GitHub 러너 IP 대역이 막힌 것이다 — 무료 계정의 앱 비밀번호 로그인만이 남은
    길이라 규칙을 '키가 있을 때만' 으로 바꿨다(DECISIONS D-NEXT-Q). 쿠키·그래프QL 은
    여전히 없다.
    """

    def setUp(self):
        BS._auth_reset()

    def tearDown(self):
        BS._auth_reset()

    def test_no_cookie_or_graphql_path(self):
        import ast
        with open(BS.__file__, encoding='utf-8') as f:
            tree = ast.parse(f.read())
        lits = [n.value.lower() for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertEqual([x for x in lits if 'graphql' in x], [])
        kwargs = [k.arg for n in ast.walk(tree) if isinstance(n, ast.Call)
                  for k in n.keywords if k.arg]
        self.assertNotIn('cookies', kwargs)

    def test_without_keys_no_login_request_is_made(self):
        posted = []

        class NoPost(Stub):
            def post(self, *a, **k):
                posted.append(a)
                raise AssertionError('키가 없는데 로그인을 불렀다')

        with mock.patch.object(BS.creds, 'get', return_value=None):
            js, ok, _b, _w = BS.call(BS.SEARCH_METHOD, {'q': 'HBM'},
                                     s=NoPost({None: Resp(200, {'posts': []})}), retries=0)
        self.assertTrue(ok)
        self.assertEqual(posted, [])

    def test_with_keys_login_goes_first_and_secret_is_not_logged(self):
        seen = {}

        class Authed(Stub):
            def post(self, url, json=None, timeout=None):
                seen['login'] = url
                return Resp(200, {'accessJwt': 'tok-123', 'didDoc': {'service': [
                    {'id': '#atproto_pds', 'serviceEndpoint': 'https://pds.example.net'}]}})

            def get(self, url, params=None, timeout=None, headers=None):
                seen.setdefault('get', []).append((url, (headers or {}).get('Authorization')))
                return Resp(200, {'posts': []})

        keys = {'BSKY_HANDLE': 'me.bsky.social', 'BSKY_APP_PASSWORD': 'abcd-efgh'}
        with mock.patch.object(BS.creds, 'get', side_effect=keys.get):
            js, ok, _b, why_ = BS.call(BS.SEARCH_METHOD, {'q': 'HBM'}, s=Authed({}), retries=0)
        self.assertTrue(ok)
        self.assertIn('createSession', seen['login'])
        url, auth = seen['get'][0]
        self.assertTrue(url.startswith('https://pds.example.net/xrpc/'))
        self.assertEqual(auth, 'Bearer tok-123')
        self.assertNotIn('abcd-efgh', str(why_))

    def test_failed_login_falls_back_to_public_hosts_with_a_reason(self):
        class BadLogin(Stub):
            def post(self, url, json=None, timeout=None):
                return Resp(401, {'error': 'AuthenticationRequired'})

        keys = {'BSKY_HANDLE': 'me', 'BSKY_APP_PASSWORD': 'secret-pw'}
        with mock.patch.object(BS.creds, 'get', side_effect=keys.get):
            js, ok, _b, why_ = BS.call(BS.SEARCH_METHOD, {'q': 'HBM'},
                                       s=BadLogin({None: Resp(200, {'posts': []})}), retries=0)
        self.assertTrue(ok)
        self.assertIn('로그인 실패 — HTTP 401', why_ or '')
        self.assertNotIn('secret-pw', why_ or '')

    def test_author_feed_is_not_called(self):
        """계정별 수집은 없다 — 예시 12계정이 블루스카이에 없다(XDIGEST.md 2장)."""
        with open(BS.__file__, encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('app.bsky.feed.getAuthorFeed', src.replace('`', ''))


if __name__ == '__main__':
    unittest.main()

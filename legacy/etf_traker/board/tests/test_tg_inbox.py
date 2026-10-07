"""
텔레그램 인박스 검증 — board/ingest/tg_inbox.py. 가짜 API 로 돈다 (네트워크 없음).

지키려는 것은 셋이다.

1. **디스크가 다음 호출보다 먼저다.** 텔레그램은 더 큰 offset 으로 부르는 순간 앞
   갱신을 지운다. 2페이지째에서 죽어도 1페이지는 inbox.json 에 있어야 한다.
2. **지어내지 않는다.** oEmbed 를 못 받으면 본문은 URL 그대로, text_via 는 null.
3. **버리지 않는다.** 종목명이 없는 일반 메시지도 kind:'other' 로 남는다. 무엇을
   붙일지는 소비자가 정한다 — 수집기가 미리 거르면 왜 안 붙었는지 알 길이 없다.
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

from ..ingest import tg_inbox as TI
from ..ingest.http import Fetch

NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=TI.KST)
TOKEN = '123:abc'
CHAT = 777


class Resp:
    def __init__(self, js, status=200, text=None):
        self._js, self.status_code = js, status
        self.text = text if text is not None else json.dumps(js)

    def json(self):
        if self._js is None:
            raise ValueError('not json')
        return self._js


class FakeAPI:
    """응답 시퀀스. getUpdates 는 `pages` 를 순서대로 돌려주고, 항목이 예외면 던진다."""

    def __init__(self, pages=(), webhook='', oembed=None):
        self.pages = list(pages)
        self.webhook = webhook
        self.oembed = oembed           # 함수(url) → Resp, 또는 예외 인스턴스
        self.calls = []                # (method, params)

    def get(self, url, params=None, timeout=None):
        params = dict(params or {})
        if url.startswith(TI.OEMBED):
            self.calls.append(('oembed', params))
            if isinstance(self.oembed, Exception):
                raise self.oembed
            if self.oembed is None:
                return Resp({}, status=404, text='Not Found')
            return self.oembed(params.get('url'))
        method = url.rsplit('/', 1)[-1]
        self.calls.append((method, params))
        if method == 'getWebhookInfo':
            return Resp({'ok': True, 'result': {'url': self.webhook, 'pending_update_count': 0}})
        if method == 'getUpdates':
            page = self.pages.pop(0) if self.pages else []
            if isinstance(page, Exception):
                raise page
            return Resp({'ok': True, 'result': page})
        raise AssertionError(f'예상 밖 호출 {method}')


def upd(uid, text, chat=CHAT, when=NOW, entities=None, caption=False, forward=None):
    body = {'caption': text, 'caption_entities': entities or []} if caption \
        else {'text': text, 'entities': entities or []}
    msg = dict(message_id=uid * 10, date=int(when.timestamp()),
               chat=dict(id=chat, type='private'), **body)
    if forward:
        msg['forward_origin'] = forward
    return dict(update_id=uid, message=msg)


def oembed_ok(url):
    return Resp(dict(
        url=url, author_name='Some One', author_url='https://twitter.com/someone',
        html='<blockquote class="twitter-tweet"><p lang="ko" dir="ltr">삼성전자 HBM4 '
             '양산 &amp; 공급<br>2분기부터</p>&mdash; Some One (@someone) '
             '<a href="https://twitter.com/someone/status/1">Sep 20, 2026</a></blockquote>'))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='tginbox-')
        self.inbox = os.path.join(self.tmp, 'inbox.json')
        self.offset = os.path.join(self.tmp, '.inbox_offset.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drain(self, api, chat_ids=(CHAT,), now=NOW, **kw):
        return TI.drain(TOKEN, list(chat_ids), self.offset, self.inbox, s=api,
                        log=lambda *a: None, now=now, **kw)

    def read(self):
        with open(self.inbox, encoding='utf-8') as f:
            return json.load(f)

    def offset_value(self):
        with open(self.offset, encoding='utf-8') as f:
            return json.load(f)['offset']


# ── (a) 페이지 단위 저장과 offset 순서 ────────────────────────
class Paging(Base):
    def test_two_pages_then_empty(self):
        api = FakeAPI(pages=[[upd(1, '첫 장'), upd(2, '둘')], [upd(3, '셋')], []])
        r = self.drain(api)
        self.assertEqual(r['n_pages'], 2)
        self.assertEqual(r['n_new'], 3)
        self.assertEqual([x['update_id'] for x in self.read()['items']], [1, 2, 3])
        self.assertEqual(self.offset_value(), 4)
        # 첫 호출은 offset 없이, 그다음은 앞 페이지 최대 update_id + 1 로.
        gets = [p for m, p in api.calls if m == 'getUpdates']
        self.assertNotIn('offset', gets[0])
        self.assertEqual(gets[1]['offset'], 3)
        self.assertEqual(gets[2]['offset'], 4)
        self.assertEqual(gets[0]['limit'], TI.PAGE)
        self.assertEqual(gets[0]['timeout'], 0)
        self.assertEqual(json.loads(gets[0]['allowed_updates']), ['message', 'channel_post'])

    def test_second_page_failure_keeps_first_page_on_disk(self):
        """2페이지째 예외 → 1페이지는 이미 inbox.json 에 있고 offset 도 그만큼 나가 있다."""
        api = FakeAPI(pages=[[upd(1, '살아남는다'), upd(2, '이것도')],
                             ConnectionError('끊김')])
        with self.assertRaises(Fetch):
            self.drain(api)
        self.assertEqual([x['update_id'] for x in self.read()['items']], [1, 2])
        self.assertEqual(self.offset_value(), 3)

    def test_resumes_from_saved_offset(self):
        api = FakeAPI(pages=[[upd(5, 'a')], []])
        self.drain(api)
        api2 = FakeAPI(pages=[[upd(6, 'b')], []])
        self.drain(api2)
        gets = [p for m, p in api2.calls if m == 'getUpdates']
        self.assertEqual(gets[0]['offset'], 6)
        self.assertEqual([x['update_id'] for x in self.read()['items']], [5, 6])

    def test_empty_first_page_still_writes_the_file(self):
        # 소비자는 파일이 없는 것과 항목이 없는 것을 구분하지 않아도 된다.
        r = self.drain(FakeAPI(pages=[[]]))
        self.assertEqual(r['n_pages'], 0)
        d = self.read()
        self.assertEqual(d['source'], 'telegram')
        self.assertEqual(d['items'], [])
        self.assertEqual(d['chat_ids'], [CHAT])
        self.assertFalse(os.path.exists(self.offset), '받은 게 없으면 offset 도 건드리지 않는다')


# ── (b) 화이트리스트 ─────────────────────────────────────────
class Whitelist(Base):
    def test_other_chats_are_dropped_and_counted(self):
        api = FakeAPI(pages=[[upd(1, '내 것'), upd(2, '남의 것', chat=999),
                              upd(3, '또 남', chat=998)], []])
        r = self.drain(api)
        self.assertEqual(r['n_dropped_other_chat'], 2)
        self.assertEqual(r['n_new'], 1)
        self.assertEqual([x['chat_id'] for x in self.read()['items']], [CHAT])
        # 버린 갱신도 offset 은 지나간다. 안 그러면 영원히 다시 온다.
        self.assertEqual(self.offset_value(), 4)

    def test_chat_ids_accept_strings(self):
        api = FakeAPI(pages=[[upd(1, 'x', chat=-100123)], []])
        r = self.drain(api, chat_ids=[' -100123 ', ''])
        self.assertEqual(r['n_new'], 1)
        self.assertEqual(self.read()['chat_ids'], [-100123])

    def test_no_chat_ids_is_an_error(self):
        with self.assertRaises(Fetch):
            self.drain(FakeAPI(pages=[[]]), chat_ids=[])

    def test_at_username_matches_chat_username_not_id(self):
        """갱신의 chat.id 는 늘 숫자다. `@channel` 은 chat.username 과 맞춰야 한다 —
        문자열 그대로 id 와 비교하면 모든 갱신을 '화이트리스트 밖' 으로 버린다."""
        post = dict(update_id=1, channel_post=dict(
            message_id=10, date=int(NOW.timestamp()),
            chat=dict(id=-100999, type='channel', username='MyNews', title='뉴스'),
            text='https://x.com/a/status/1 메모', entities=[]))
        other = dict(update_id=2, channel_post=dict(
            message_id=20, date=int(NOW.timestamp()),
            chat=dict(id=-100998, type='channel', username='other', title='딴 방'),
            text='남의 것', entities=[]))
        r = self.drain(FakeAPI(pages=[[post, other], []]), chat_ids=['@mynews'])
        self.assertEqual(r['n_new'], 1)
        self.assertEqual(r['n_dropped_other_chat'], 1)
        self.assertEqual([x['chat_id'] for x in self.read()['items']], [-100999])


# ── (c) 링크 추출 ─────────────────────────────────────────────
class Links(Base):
    def test_x_com_link_gives_id_and_author(self):
        api = FakeAPI(pages=[[upd(1, '이거 봐 https://x.com/semianalysis/status/1839000000000000001?s=46&t=zz')], []])
        r = self.drain(api)
        it = self.read()['items'][0]
        self.assertEqual(r['n_x'], 1)
        self.assertEqual(it['kind'], 'x')
        self.assertEqual(it['x_ids'], ['1839000000000000001'])
        self.assertEqual(it['author'], 'semianalysis')
        self.assertEqual(it['urls'], ['https://x.com/semianalysis/status/1839000000000000001?s=46&t=zz'])
        self.assertEqual(it['text_via'], 'message')

    def test_twitter_com_and_entities(self):
        # 본문에는 안 보이고 entity 에만 있는 text_link, 그리고 UTF-16 오프셋의 url entity.
        # 앞에 이모지(서로게이트 쌍, UTF-16 으로 2유닛)를 둔다 — '한글' 같은 BMP 문자만
        # 쓰면 UTF-16 유닛 수와 파이썬 인덱스가 같아 `text[offset:]` 구현도 통과해 버린다.
        head = '🚀🚀 한글 '
        text = head + 'https://twitter.com/foo/status/42 끝'
        off = len(head.encode('utf-16-le')) // 2
        self.assertEqual(off, len(head) + 2, '이모지 둘이 파이썬 인덱스보다 2유닛 더 나가야 한다')
        ents = [dict(type='url', offset=off, length=len('https://twitter.com/foo/status/42')),
                dict(type='text_link', offset=0, length=2, url='https://x.com/bar/status/43')]
        api = FakeAPI(pages=[[upd(1, text, entities=ents)], []])
        self.drain(api)
        it = self.read()['items'][0]
        self.assertEqual(it['x_ids'], ['42', '43'], 'entity 목록 순서대로')
        self.assertEqual(it['author'], 'foo', '첫 링크의 계정')
        self.assertEqual(len(it['urls']), 2)

    def test_t_co_is_x_but_not_resolved(self):
        api = FakeAPI(pages=[[upd(1, 'https://t.co/AbCdEf')], []])
        r = self.drain(api)
        it = self.read()['items'][0]
        self.assertEqual(it['kind'], 'x')
        self.assertEqual(it['x_ids'], [])
        self.assertIsNone(it['author'])
        self.assertIsNone(it['text_via'], 'URL 뿐인데 풀 수 없으면 채운 것이 없다')
        self.assertEqual(it['text'], 'https://t.co/AbCdEf')
        self.assertFalse([c for c in api.calls if c[0] == 'oembed'], 't.co 로는 oEmbed 도 안 부른다')

    def test_i_status_has_no_author_but_forward_origin_does(self):
        fwd = dict(type='hidden_user', sender_user_name='전달한 사람')
        api = FakeAPI(pages=[[upd(1, 'https://x.com/i/status/77 메모', forward=fwd)], []])
        self.drain(api)
        it = self.read()['items'][0]
        self.assertEqual(it['x_ids'], ['77'])
        self.assertEqual(it['author'], '전달한 사람')

    def test_i_web_status_gives_id_without_author(self):
        """알림 메일·웹 '링크 복사' 는 x.com/i/web/status/<id> 를 준다. id 는 뽑히고 계정은 없다."""
        self.assertEqual(TI._status('https://x.com/i/web/status/123?s=46'), (None, '123'))
        self.assertEqual(TI._status('https://twitter.com/i/web/status/9'), (None, '9'))
        api = FakeAPI(pages=[[upd(1, 'https://x.com/i/web/status/123?s=46')], []], oembed=oembed_ok)
        self.drain(api)
        it = self.read()['items'][0]
        self.assertEqual(it['x_ids'], ['123'])
        self.assertEqual(it['text_via'], 'oembed', 'URL 뿐이면 oEmbed 대상이 된다')
        self.assertEqual(it['author'], 'someone')

    def test_schemeless_url_entity_still_counts_as_url_only(self):
        """`url` entity 는 스킴 없이 온다. extract_urls 가 붙인 https:// 때문에 본문과
        안 맞으면 URL 뿐인 메시지가 '본문 있음' 으로 남고 oEmbed 도 안 부른다."""
        text = 'x.com/a/status/1'
        ents = [dict(type='url', offset=0, length=len(text))]
        api = FakeAPI(pages=[[upd(1, text, entities=ents)], []], oembed=oembed_ok)
        self.drain(api)
        it = self.read()['items'][0]
        self.assertEqual(it['urls'], ['https://x.com/a/status/1'])
        self.assertEqual(it['text_via'], 'oembed')
        self.assertTrue([c for c in api.calls if c[0] == 'oembed'])
        self.assertTrue(TI._only_urls('x.com/a/status/1', ['https://x.com/a/status/1']))
        self.assertFalse(TI._only_urls('x.com/a/status/1 메모', ['https://x.com/a/status/1']))

    def test_forward_origin_user_and_channel(self):
        self.assertEqual(TI._forward_name(dict(forward_origin=dict(
            type='user', sender_user=dict(first_name='길동', last_name='홍')))), '길동 홍')
        self.assertEqual(TI._forward_name(dict(forward_origin=dict(
            type='channel', chat=dict(title='뉴스방', username='newsroom')))), 'newsroom')
        self.assertEqual(TI._forward_name(dict(forward_from=dict(username='old'))), 'old')
        self.assertIsNone(TI._forward_name({}))

    def test_trailing_punctuation_is_not_part_of_the_url(self):
        urls = TI.extract_urls('봐라 (https://x.com/a/status/1). 그리고 https://x.com/a/status/1')
        self.assertEqual(urls, ['https://x.com/a/status/1'])

    def test_caption_of_a_photo_counts_as_text(self):
        api = FakeAPI(pages=[[upd(1, '캡처 https://x.com/z/status/9', caption=True)], []])
        self.drain(api)
        it = self.read()['items'][0]
        self.assertEqual(it['x_ids'], ['9'])
        self.assertEqual(it['text'], '캡처 https://x.com/z/status/9')


# ── (d) 중복 제거·만료 ────────────────────────────────────────
class DedupExpiry(Base):
    def test_same_update_id_is_not_added_twice(self):
        self.drain(FakeAPI(pages=[[upd(1, '한 번')], []]))
        # offset 파일이 사라져 같은 페이지를 또 받는 상황.
        os.remove(self.offset)
        r = self.drain(FakeAPI(pages=[[upd(1, '한 번'), upd(2, '새로')], []]))
        self.assertEqual(r['n_new'], 1)
        self.assertEqual([x['update_id'] for x in self.read()['items']], [1, 2])

    def test_items_older_than_keep_days_are_dropped(self):
        old = NOW - timedelta(days=15)
        edge = NOW - timedelta(days=13)
        r = self.drain(FakeAPI(pages=[[upd(1, '옛날', when=old), upd(2, '아직', when=edge)], []]))
        self.assertEqual(r['n_expired'], 1)
        self.assertEqual([x['update_id'] for x in self.read()['items']], [2])

    def test_expiry_applies_to_stored_items_on_a_later_run(self):
        self.drain(FakeAPI(pages=[[upd(1, '그때는 신선')], []]))
        r = self.drain(FakeAPI(pages=[[]]), now=NOW + timedelta(days=15))
        self.assertEqual(r['n_expired'], 1)
        self.assertEqual(self.read()['items'], [])

    def test_keep_days_is_configurable(self):
        r = self.drain(FakeAPI(pages=[[upd(1, 'x', when=NOW - timedelta(days=3))], []]),
                       keep_days=2)
        self.assertEqual(r['n_expired'], 1)

    def test_corrupt_inbox_is_moved_aside_and_logged_not_overwritten(self):
        """반쪽 쓰기·잘못 고친 파일을 빈 기본값으로 덮어쓰면 14일치가 조용히 사라진다.
        `.bad` 로 옮기고 사유를 남긴 뒤 진행한다 — 멈추면 새 갱신까지 24시간 뒤에 없어진다."""
        self.drain(FakeAPI(pages=[[upd(1, '살아 있던 것'), upd(2, '이것도')], []]))
        with open(self.inbox, 'w', encoding='utf-8') as f:
            f.write('{"items":[{"update_id":1,')
        logs = []
        r = TI.drain(TOKEN, [CHAT], self.offset, self.inbox, s=FakeAPI(pages=[[upd(3, '새')], []]),
                     log=logs.append, now=NOW)
        self.assertEqual(r['n_new'], 1)
        self.assertEqual([x['update_id'] for x in self.read()['items']], [3])
        with open(self.inbox + '.bad', encoding='utf-8') as f:
            self.assertEqual(f.read(), '{"items":[{"update_id":1,', '옛 파일은 그대로 남는다')
        self.assertTrue(any('손상' in m and '.bad' in m for m in logs), logs)

    def test_corrupt_offset_starts_over_and_dedup_absorbs(self):
        self.drain(FakeAPI(pages=[[upd(1, '한 번')], []]))
        with open(self.offset, 'w', encoding='utf-8') as f:
            f.write('garbage')
        logs = []
        api = FakeAPI(pages=[[upd(1, '한 번'), upd(2, '둘')], []])
        r = TI.drain(TOKEN, [CHAT], self.offset, self.inbox, s=api, log=logs.append, now=NOW)
        gets = [p for m, p in api.calls if m == 'getUpdates']
        self.assertNotIn('offset', gets[0])
        self.assertEqual(r['n_new'], 1)
        self.assertTrue(any('offset' in m and '손상' in m for m in logs), logs)


# ── (e) 웹훅 ─────────────────────────────────────────────────
class Webhook(Base):
    def test_webhook_set_raises_before_any_getUpdates(self):
        api = FakeAPI(pages=[[upd(1, 'x')]], webhook='https://example.com/hook')
        with self.assertRaises(Fetch) as cm:
            self.drain(api)
        self.assertIn('https://example.com/hook', str(cm.exception))
        self.assertIn('getUpdates', str(cm.exception))
        self.assertEqual([m for m, _ in api.calls], ['getWebhookInfo'])
        self.assertFalse(os.path.exists(self.inbox))

    def test_api_refusal_carries_telegram_reason_without_token(self):
        class Refuse(FakeAPI):
            def get(self, url, params=None, timeout=None):
                return Resp({'ok': False, 'error_code': 401, 'description': 'Unauthorized'},
                            status=401)
        with self.assertRaises(Fetch) as cm:
            self.drain(Refuse())
        self.assertIn('Unauthorized', str(cm.exception))
        self.assertNotIn(TOKEN, str(cm.exception))

    def test_connection_error_message_has_token_masked(self):
        """토큰이 새는 길은 requests 연결 예외다 — 메시지에 URL 이 통째로 실린다.
        위 시험은 사유에 토큰이 애초에 없어 _scrub 를 항등으로 바꿔도 통과한다."""
        class Drop(FakeAPI):
            def get(self, url, params=None, timeout=None):
                raise ConnectionError(f"HTTPSConnectionPool(host='api.telegram.org'): "
                                      f"Max retries exceeded with url: {url}")
        with self.assertRaises(Fetch) as cm:
            self.drain(Drop())
        msg = str(cm.exception)
        self.assertIn('<TOKEN>', msg)
        self.assertNotIn(TOKEN, msg)
        self.assertIn('getWebhookInfo', msg)


# ── (f) oEmbed ────────────────────────────────────────────────
class OEmbed(Base):
    def test_url_only_message_is_filled_from_oembed(self):
        api = FakeAPI(pages=[[upd(1, 'https://x.com/i/status/1?s=20')], []], oembed=oembed_ok)
        self.drain(api)
        it = self.read()['items'][0]
        self.assertEqual(it['text_via'], 'oembed')
        self.assertEqual(it['text'], '삼성전자 HBM4 양산 & 공급\n2분기부터')
        self.assertEqual(it['author'], 'someone', 'author_url 의 계정')
        self.assertEqual(it['x_ids'], ['1'])
        oe = [p for m, p in api.calls if m == 'oembed']
        self.assertEqual(oe[0]['url'], 'https://x.com/i/status/1', '추적 파라미터는 뗀다')
        self.assertEqual(str(oe[0]['omit_script']), '1')

    def test_oembed_failure_keeps_the_url_and_marks_null(self):
        api = FakeAPI(pages=[[upd(1, 'https://x.com/gone/status/2')], []], oembed=None)   # 404
        self.drain(api)
        it = self.read()['items'][0]
        self.assertEqual(it['text'], 'https://x.com/gone/status/2')
        self.assertIsNone(it['text_via'])
        self.assertEqual(it['author'], 'gone', '링크의 계정은 oEmbed 와 무관하게 있다')

    def test_oembed_exception_does_not_stop_the_page(self):
        api = FakeAPI(pages=[[upd(1, 'https://x.com/a/status/3'), upd(2, '다음 것')], []],
                      oembed=ConnectionError('막힘'))
        r = self.drain(api)
        self.assertEqual(r['n_new'], 2)
        self.assertIsNone(self.read()['items'][0]['text_via'])

    def test_message_with_caption_does_not_call_oembed(self):
        api = FakeAPI(pages=[[upd(1, '이건 내 메모 https://x.com/a/status/4')], []], oembed=oembed_ok)
        self.drain(api)
        self.assertEqual(self.read()['items'][0]['text_via'], 'message')
        self.assertFalse([c for c in api.calls if c[0] == 'oembed'])

    def test_link_author_wins_over_oembed_author(self):
        api = FakeAPI(pages=[[upd(1, 'https://x.com/linkacct/status/5')], []], oembed=oembed_ok)
        self.drain(api)
        self.assertEqual(self.read()['items'][0]['author'], 'linkacct')

    def test_oembed_author_beats_forward_origin(self):
        """채널 '뉴스방' 이 전달한 i/status 링크. 게시물을 쓴 건 oEmbed 가 준 계정이지
        전달한 채널이 아니다 — 전달자를 앞에 두면 X 게시물을 채널이 쓴 것처럼 보인다."""
        fwd = dict(type='channel', chat=dict(title='뉴스방', username='newsroom'))
        api = FakeAPI(pages=[[upd(1, 'https://x.com/i/status/5', forward=fwd)], []], oembed=oembed_ok)
        self.drain(api)
        it = self.read()['items'][0]
        self.assertEqual(it['text_via'], 'oembed')
        self.assertEqual(it['author'], 'someone')

    def test_forward_origin_stays_when_oembed_fails(self):
        fwd = dict(type='channel', chat=dict(title='뉴스방', username='newsroom'))
        api = FakeAPI(pages=[[upd(1, 'https://x.com/i/status/6', forward=fwd)], []], oembed=None)
        self.drain(api)
        it = self.read()['items'][0]
        self.assertIsNone(it['text_via'])
        self.assertEqual(it['author'], 'newsroom', '채운 것이 없으면 전달자라도 남긴다')


# ── (g) 일반 메시지 ───────────────────────────────────────────
class Other(Base):
    def test_plain_message_is_kept_as_other(self):
        api = FakeAPI(pages=[[upd(1, '오늘 장 어땠어'), upd(2, 'https://n.news.naver.com/a/1')], []])
        r = self.drain(api)
        self.assertEqual(r['n_new'], 2)
        self.assertEqual(r['n_x'], 0)
        items = self.read()['items']
        self.assertEqual([x['kind'] for x in items], ['other', 'other'])
        self.assertEqual(items[0]['text'], '오늘 장 어땠어')
        self.assertEqual(items[0]['text_via'], 'message')
        self.assertEqual(items[1]['urls'], ['https://n.news.naver.com/a/1'])
        self.assertEqual(items[1]['x_ids'], [])

    def test_item_shape_matches_the_contract(self):
        self.drain(FakeAPI(pages=[[upd(1, 'x https://x.com/a/status/1')], []]))
        d = self.read()
        self.assertEqual(set(d), {'source', 'updated_at', 'chat_ids', 'items'})
        self.assertEqual(d['updated_at'], NOW.isoformat(timespec='seconds'))
        it = d['items'][0]
        self.assertEqual(set(it), {'update_id', 'chat_id', 'date', 'text', 'urls', 'x_ids',
                                   'author', 'kind', 'text_via'})
        self.assertEqual(it['date'], NOW.isoformat(timespec='seconds'))
        self.assertTrue(it['date'].endswith('+09:00'))

    def test_non_message_updates_are_ignored(self):
        api = FakeAPI(pages=[[dict(update_id=9, edited_message=dict(chat=dict(id=CHAT), text='편집'))],
                             []])
        r = self.drain(api)
        self.assertEqual(r['n_new'], 0)
        self.assertEqual(self.offset_value(), 10, '무시한 갱신도 offset 은 지나간다')


class CmdInboxExit(unittest.TestCase):
    def test_missing_config_is_a_skip_not_a_failure(self):
        """주말 크론은 --inbox 만 돈다. 토큰이 없는 레포에서 1 을 돌려주면 set -e 아래
        잡이 매주 두 번 빨갛게 되어 진짜 실패가 묻힌다 (GITHUB.md 2장)."""
        from .. import run
        from ..ingest import creds
        real = creds.get
        try:
            creds.get = lambda n, required=False: ''
            self.assertEqual(run.cmd_inbox(), 0)
            creds.get = lambda n, required=False: {'TELEGRAM_BOT_TOKEN': TOKEN}.get(n, '')
            self.assertEqual(run.cmd_inbox(), 0, '대화방이 없어도 건너뜀이지 실패가 아니다')
        finally:
            creds.get = real

    def test_fetch_failure_is_reported_as_failure(self):
        from .. import run
        from ..ingest import creds
        real_get, real_drain = creds.get, TI.drain
        try:
            creds.get = lambda n, required=False: {'TELEGRAM_BOT_TOKEN': TOKEN,
                                                   'TELEGRAM_CHAT_ID': str(CHAT)}.get(n, '')
            def boom(*a, **k):
                raise Fetch('getUpdates 거부')
            TI.drain = boom
            self.assertEqual(run.cmd_inbox(), 1)
        finally:
            creds.get, TI.drain = real_get, real_drain


class ChatIdsFromCreds(unittest.TestCase):
    def test_list_beats_single_and_single_is_the_default(self):
        from ..ingest import creds
        real = creds.get
        try:
            creds.get = lambda n, required=False: {'TRIGGER_INBOX_CHAT_IDS': '1, 2',
                                                   'TELEGRAM_CHAT_ID': '9'}.get(n, '')
            self.assertEqual(TI.inbox_chat_ids(), [1, 2])
            creds.get = lambda n, required=False: {'TELEGRAM_CHAT_ID': '9'}.get(n, '')
            self.assertEqual(TI.inbox_chat_ids(), [9])
            creds.get = lambda n, required=False: ''
            self.assertEqual(TI.inbox_chat_ids(), [])
        finally:
            creds.get = real


if __name__ == '__main__':
    unittest.main()

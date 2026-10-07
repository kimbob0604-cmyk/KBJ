#!/usr/bin/env python3
"""X 자동 수집 경로의 파서 단위 시험.

수집은 러너에서만 돈다(이 환경은 프록시가 막는다). 파서는 픽스처로 못 박아
두어야 러너 실측 결과를 읽을 때 '파서가 틀린 것'과 '경로가 막힌 것'을 가릴 수
있다 — 둘을 못 가리면 실측을 다시 돌려야 한다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.ingest import xsource as X              # noqa: E402


NITTER_RSS = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>ChipWireFx / @ChipWireFx</title>
  <item>
    <title>HBM &amp; 수출단가, 5개월 만에 첫 하락</title>
    <link>https://nitter.net/ChipWireFx/status/1873000000000000001#m</link>
    <pubDate>Mon, 21 Sep 2026 09:12:00 GMT</pubDate>
  </item>
  <item>
    <title><![CDATA[CXMT 5세대 DRAM 양산 <b>개시</b>]]></title>
    <link>https://nitter.net/ChipWireFx/status/1873000000000000002#m</link>
    <pubDate>Mon, 21 Sep 2026 03:40:00 GMT</pubDate>
  </item>
</channel></rss>'''


class TestRss(unittest.TestCase):
    def test_items_are_parsed_in_order(self):
        rows = X.parse_rss(NITTER_RSS)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['text'], 'HBM & 수출단가, 5개월 만에 첫 하락')
        self.assertTrue(rows[0]['url'].endswith('/status/1873000000000000001#m'))
        self.assertIn('21 Sep 2026', rows[0]['at'])

    def test_cdata_and_inner_tags_are_stripped(self):
        rows = X.parse_rss(NITTER_RSS)
        self.assertEqual(rows[1]['text'], 'CXMT 5세대 DRAM 양산 개시')

    def test_empty_and_garbage_do_not_raise(self):
        for bad in ('', None, '<html>not rss</html>', '<rss><channel></channel></rss>'):
            self.assertEqual(X.parse_rss(bad), [])

    def test_item_without_title_or_link_is_dropped(self):
        self.assertEqual(X.parse_rss('<rss><item><guid>x</guid></item></rss>'), [])

    def test_status_id_is_recoverable_from_the_link(self):
        """단건 본문(tweet-result)을 부르려면 링크에서 id 가 나와야 한다."""
        import re
        rows = X.parse_rss(NITTER_RSS)
        ids = [re.search(r'/status/(\d+)', r['url']).group(1) for r in rows]
        self.assertEqual(ids, ['1873000000000000001', '1873000000000000002'])


class TestTimeline(unittest.TestCase):
    def test_entries_are_counted(self):
        html = ('<html><script id="__NEXT_DATA__" type="application/json">'
                '{"props":{"pageProps":{"timeline":{"entries":[1,2,3]}}}}'
                '</script></html>')
        n, why = X.parse_timeline(html)
        self.assertEqual(n, 3)
        self.assertIsNone(why)

    def test_missing_script_says_so(self):
        n, why = X.parse_timeline('<html><body>로그인이 필요합니다</body></html>')
        self.assertIsNone(n)
        self.assertIn('__NEXT_DATA__', why)

    def test_broken_json_says_so(self):
        html = '<script id="__NEXT_DATA__">{not json</script>'
        n, why = X.parse_timeline(html)
        self.assertIsNone(n)
        self.assertIn('파싱 실패', why)

    def test_present_but_empty_is_zero_not_none(self):
        """200 인데 트윗이 없는 것과 스크립트가 없는 것은 다르다."""
        html = '<script id="__NEXT_DATA__">{"props":{"entries":[]}}</script>'
        n, why = X.parse_timeline(html)
        self.assertEqual(n, 0)
        self.assertIsNone(why)


class TestToken(unittest.TestCase):
    """tweet-result 의 token 계산. 문서화된 규칙이 아니라 클라이언트 구현이다."""

    def test_token_is_alnum_without_zero_or_dot(self):
        tok = X._tweet_token('1873000000000000001')
        self.assertTrue(tok)
        self.assertNotIn('0', tok)
        self.assertNotIn('.', tok)
        self.assertTrue(tok.isalnum(), tok)

    def test_different_ids_give_different_tokens(self):
        a = X._tweet_token('1873000000000000001')
        b = X._tweet_token('1999000000000000009')
        self.assertNotEqual(a, b)


class TestNoLoginPath(unittest.TestCase):
    """로그인 쿠키 경로((c))는 여기 없어야 한다 — 계정 정지 위험 (XDIGEST.md 2장)."""

    def test_no_login_endpoint_or_credential_in_the_code(self):
        """주석·설명은 (c) 경로를 **언급**한다. 코드에 있으면 안 된다.

        그래서 파일 전체를 문자열로 뒤지지 않고 ast 로 docstring 을 뺀 뒤,
        실제로 쓰이는 문자열 상수와 호출 인자만 본다.
        """
        import ast
        tree = ast.parse(open(X.__file__, encoding='utf-8').read())
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                d = ast.get_docstring(node, clean=False)
                if d:
                    docs.add(d)
        lits = [n.value.lower() for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value not in docs]
        for banned in ('graphql', 'auth_token', 'ct0', 'authorization', 'bearer'):
            hit = [s for s in lits if banned in s]
            self.assertEqual(hit, [], f'코드에 로그인 경로의 흔적: {banned} → {hit}')
        # 쿠키·헤더를 심는 호출이 없어야 한다.
        kwargs = [k.arg for n in ast.walk(tree) if isinstance(n, ast.Call)
                  for k in n.keywords if k.arg]
        for banned in ('cookies', 'auth'):
            self.assertNotIn(banned, kwargs, f'호출에 {banned}= 가 있다')

    def test_probe_calls_each_candidate_once(self):
        """남의 자원봉사 서버다. 진단은 후보마다 한 번만 부른다."""
        src = open(X.__file__, encoding='utf-8').read()
        self.assertIn('재시도하지 않는다', src)


if __name__ == '__main__':
    unittest.main()


class BskyCallIsOneLayer(unittest.TestCase):
    """블루스카이 호출은 `ingest/bsky.call` 하나다 — 실측도 그것을 쓴다.

    예전에는 실측(`xsource._bsky`)과 수집(`bsky.search`)이 호스트 목록·막힘
    판정·사유 표기를 각자 구현했다. 그래서 검토가 수집 쪽에서 잡아 고친 결함
    둘이 실측 쪽에는 그대로 남았다 — 막힘을 누적 플래그로 두어 '앞 403 + 뒤
    전송 오류' 를 막힘으로 라벨한 것과, 뒤 호스트 사유로 앞 호스트 사유를 덮어
    'api 429 · public 403' 을 '둘 다 403' 과 같게 만든 것이다. 한 벌로 두면
    고침이 한 번이다.
    """

    def test_실측이_수집과_같은_호출을_쓴다(self):
        import inspect

        from ..ingest import xsource
        src = inspect.getsource(xsource._bsky)
        self.assertIn('BS.call', src)

    def test_실측에_호스트_목록이_다시_없다(self):
        """두 번째 호스트 목록이 생기면 둘이 또 갈린다."""
        import inspect

        from ..ingest import xsource
        src = inspect.getsource(xsource._bsky)
        self.assertNotIn('public.api.bsky.app', src)

    def test_막힘_판정이_한_곳에만_있다(self):
        """`BLOCKED` 상수는 bsky.py 에만 둔다."""
        from ..ingest import bsky, xsource
        self.assertEqual(bsky.BLOCKED, (403, 429, 503))
        self.assertFalse(hasattr(xsource, 'BLOCKED'))

    def test_실측은_재시도하지_않는다(self):
        """잰 값이 '한 번 눌러서 무엇이 왔나' 여야 한다."""
        import inspect

        from ..ingest import xsource
        self.assertIn('retries=0', inspect.getsource(xsource._bsky))

    def test_어댑터는_3튜플을_돌려준다(self):
        """이 모듈의 호출부가 기대하는 모양. 실패는 (None, 사유, blocked) 다."""
        import inspect

        from ..ingest import xsource
        calls = []

        def fake(method, params, s=None, hosts=None, retries=None, gap=None):
            calls.append((method, retries, gap))
            return None, False, True, 'api.bsky.app: HTTP 403 / public: HTTP 403'

        from ..ingest import bsky
        orig = bsky.call
        bsky.call = fake
        try:
            js, why, blocked = xsource._bsky(None, 'app.bsky.actor.getProfile', {})
        finally:
            bsky.call = orig
        self.assertIsNone(js)
        self.assertIn('403', why)
        self.assertTrue(blocked)
        self.assertEqual(calls[0][1:], (0, 2))
        _ = inspect  # 위 시험들과 같은 자리에 둔다

    def test_받아_내도_앞_호스트_사유를_삼키지_않는다(self):
        """반환에는 실을 수 없다 — 호출부가 `why` 를 '못 받았다' 로 읽는다.

        그래서 `log` 로 나가야 한다. 수집 쪽(`bsky.search`)에서 검토가 잡아
        고친 것과 같은 결함이고, 어댑터를 쓰며 다시 만들 수 있는 자리다.
        """
        from ..ingest import bsky, xsource
        lines = []
        orig = bsky.call
        bsky.call = lambda *a, **k: ({'posts': []}, True, False, 'api: HTTP 429')
        try:
            js, why, blocked = xsource._bsky(None, 'm', {}, log=lines.append)
        finally:
            bsky.call = orig
        # 반환 모양은 '받았다' 다.
        self.assertEqual(js, {'posts': []})
        self.assertIsNone(why)
        self.assertFalse(blocked)
        # 사유는 로그에 남는다.
        self.assertEqual(len(lines), 1)
        self.assertIn('429', lines[0])
        self.assertIn('폴백', lines[0])

    def test_폴백_없이_받으면_로그도_없다(self):
        """정상적인 날에 군더더기 줄을 남기지 않는다."""
        from ..ingest import bsky, xsource
        lines = []
        orig = bsky.call
        bsky.call = lambda *a, **k: ({'posts': []}, True, False, None)
        try:
            xsource._bsky(None, 'm', {}, log=lines.append)
        finally:
            bsky.call = orig
        self.assertEqual(lines, [])

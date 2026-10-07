"""non-200 의 사유는 상태코드가 아니라 본문에 있다 (CLAUDE.md 2장 6번).

2026-09-01 공공데이터포털이 403 을 줬는데 로그에는 "HTTP 403" 뿐이라
무엇을 해야 하는지 알 수 없었다. 그 회귀를 막는다.
"""
import unittest

from ..ingest import http
from ..ingest.http import Fetch


class _R:
    def __init__(self, status=200, text='', js=None):
        self.status_code = status
        self.text = text
        self._js = js

    def json(self):
        if self._js is None:
            raise ValueError('not json')
        return self._js


class _S:
    def __init__(self, *replies):
        self._it = iter(replies)

    def get(self, url, params=None, timeout=None):
        return next(self._it)


DATAGO_403 = (
    '<OpenAPI_ServiceResponse><cmmMsgHeader>'
    '<errMsg>SERVICE ERROR</errMsg>'
    '<returnAuthMsg>SERVICE_ACCESS_DENIED_ERROR</returnAuthMsg>'
    '<returnReasonCode>20</returnReasonCode>'
    '</cmmMsgHeader></OpenAPI_ServiceResponse>')


class WhyTest(unittest.TestCase):

    def test_xml_error_tags(self):
        w = http.why(_R(403, DATAGO_403))
        self.assertIn('SERVICE_ACCESS_DENIED_ERROR', w)
        self.assertIn('returnReasonCode=20', w)

    def test_json_message(self):
        self.assertIn('막힘', http.why(_R(403, '{}', {'message': '막힘'})))

    def test_falls_back_to_body_snippet(self):
        w = http.why(_R(500, '  이유를   모를   본문 '))
        self.assertEqual(w, '이유를 모를 본문')

    def test_snippet_is_bounded(self):
        self.assertLessEqual(len(http.why(_R(500, 'x' * 5000))), http.BODY_SNIP)

    def test_empty_body_gives_empty_string(self):
        # 사유가 없는 것과 못 읽은 것은 다르다. 호출자가 구분해 적을 수 있어야
        # 한다 — 빈 문자열을 돌려주지 않으면 "HTTP 403 · " 처럼 구분자만 남는다.
        self.assertEqual(http.why(_R(403, '')), '')
        self.assertEqual(http.why(_R(403, '   \n  ')), '')

    def test_value_whitespace_and_cdata(self):
        self.assertEqual(http.why(_R(403, '<errMsg>\n  막힘\n</errMsg>')),
                         'errMsg=막힘')
        self.assertEqual(http.why(_R(403, '<errMsg><![CDATA[막힘]]></errMsg>')),
                         'errMsg=막힘')


class NoBacktrackTest(unittest.TestCase):
    """사유를 뽑다가 멈추면 안 된다. 첫 판이 두 번 멈췄다.

    `(.*?)` 는 닫히지 않은 여는 태그가 많은 본문에서 O(n²) 였고, 그걸 고치며
    넣은 앞뒤 `\s*` 는 값 자리와 같은 공백 구간을 나눠 갖느라 다시 멈췄다.
    수집기가 부르는 자리라 여기서 멈추면 그날 보드가 통째로 안 나온다.
    """

    LIMIT = 2.0

    def _under_limit(self, body):
        import time
        t = time.time()
        http.why(_R(403, body))
        dt = time.time() - t
        self.assertLess(dt, self.LIMIT, f'{len(body):,}자에 {dt:.1f}초')

    def test_many_unclosed_tags(self):
        self._under_limit('<message>' * 20000 + 'x' * 100000)

    def test_whitespace_run(self):
        self._under_limit('<message>' + ' ' * 300000 + '<' * 100)

    def test_interleaved_tags(self):
        self._under_limit('<errMsg><message>' * 10000 + 'y' * 200000)

    def test_large_html(self):
        self._under_limit('<html>' + '<div>가나다</div>' * 40000 + '</html>')


class GetTest(unittest.TestCase):

    def test_non_200_carries_reason(self):
        s = _S(*[_R(403, DATAGO_403)] * 3)
        with self.assertRaises(Fetch) as cm:
            http.get(s, 'https://x/y', params={'serviceKey': 'SEKRIT'},
                     retries=3, backoff=0)
        msg = str(cm.exception)
        self.assertIn('HTTP 403', msg)
        self.assertIn('SERVICE_ACCESS_DENIED_ERROR', msg)
        self.assertNotIn('SEKRIT', msg)          # 인증키는 여전히 안 샌다

    def test_200_but_not_json_raises_with_reason_and_does_not_retry(self):
        # 두 번째 응답을 주지 않는다 — 재시도하면 StopIteration 으로 터진다.
        s = _S(_R(200, DATAGO_403))
        with self.assertRaises(Fetch) as cm:
            http.get(s, 'https://x/y', retries=3, backoff=0)
        self.assertIn('SERVICE_ACCESS_DENIED_ERROR', str(cm.exception))

    def test_200_json_still_returns(self):
        self.assertEqual(
            http.get(_S(_R(200, '{"a":1}', {'a': 1})), 'https://x/y'), {'a': 1})

    def test_text_mode_untouched(self):
        self.assertEqual(
            http.get(_S(_R(200, 'hi')), 'https://x/y', want='text'), 'hi')


if __name__ == '__main__':
    unittest.main()


class DatagoKeyFormTest(unittest.TestCase):
    """인증키가 Encoding 형태로 들어오면 requests 가 한 번 더 인코딩한다.

    값을 로그에 남길 수 없으므로 어느 형태인지 물어볼 수 없다. 눌러 보고
    통한 것을 기록한다.
    """

    def setUp(self):
        import os
        from ..ingest import datago
        self.D = datago
        self.old = os.environ.get('DATAGO_KEY')
        self.real_get = datago.get
        datago.KEY_USED.clear()

    def tearDown(self):
        import os
        self.D.get = self.real_get
        self.D.KEY_USED.clear()
        if self.old is None:
            os.environ.pop('DATAGO_KEY', None)
        else:
            os.environ['DATAGO_KEY'] = self.old

    def _set(self, k):
        import os
        os.environ['DATAGO_KEY'] = k

    def test_encoded_key_is_decoded_first(self):
        self._set('ab%2Bcd%3D%3D')
        self.assertEqual([n for n, _ in self.D.key_forms()], ['디코딩', '그대로'])
        self.assertEqual(self.D.key_forms()[0][1], 'ab+cd==')

    def test_plain_key_has_one_form(self):
        self._set('ab+cd==')
        self.assertEqual([n for n, _ in self.D.key_forms()], ['그대로'])

    def test_call_falls_through_to_second_form(self):
        self._set('ab%2Bcd')
        seen = []

        def fake(s, url, params=None, **kw):
            seen.append(params['serviceKey'])
            if params['serviceKey'] == 'ab+cd':
                raise Fetch('HTTP 403 · 본문 없음')
            return {'ok': 1}

        self.D.get = fake
        self.assertEqual(self.D.call(None, 'https://x', {}), {'ok': 1})
        self.assertEqual(seen, ['ab+cd', 'ab%2Bcd'])
        self.assertEqual(self.D.KEY_USED['form'], '그대로')

    def test_winning_form_is_reused(self):
        self._set('ab%2Bcd')
        seen = []

        def fake(s, url, params=None, **kw):
            seen.append(params['serviceKey'])
            return {'ok': 1}

        self.D.get = fake
        self.D.call(None, 'https://x', {})
        self.D.call(None, 'https://x', {})
        self.assertEqual(seen, ['ab+cd', 'ab+cd'])   # 두 번째는 후보를 안 훑는다

    def test_all_forms_failing_names_them(self):
        self._set('ab%2Bcd')

        def fake(s, url, params=None, **kw):
            raise Fetch('HTTP 403 · 본문 없음')

        self.D.get = fake
        with self.assertRaises(Fetch) as cm:
            self.D.call(None, 'https://x', {})
        msg = str(cm.exception)
        self.assertIn('디코딩', msg)
        self.assertIn('그대로', msg)
        self.assertIn('403', msg)
        self.assertNotIn('ab+cd', msg)               # 키 값은 안 샌다

"""
KRX 호출부 시험 — 망을 타지 않고 **실패를 어떻게 말하는지**를 본다.

첫 실호출이 JSONDecodeError 하나만 남기고 죽었다. 서버가 무엇을 말했는지가
사라지면 화면코드가 틀린 건지, 로그인 화면이 온 건지, 리다이렉트를 따라가다
POST 가 GET 으로 바뀐 건지 구분할 수 없다. 그 구분을 여기서 못 박는다.
"""
import unittest

from board.ingest.http import Fetch

from .. import krx as K


class FakeResp:

    def __init__(self, status=200, body='', ct='application/json', location=None):
        self.status_code = status
        self.text = body
        self.headers = {'Content-Type': ct}
        if location:
            self.headers['Location'] = location

    def json(self):
        import json
        return json.loads(self.text)


class FakeSession:
    """post 할 때마다 미리 넣어 둔 응답을 순서대로 돌려준다."""

    def __init__(self, *resps):
        self.resps, self.calls, self.gets = list(resps), [], []
        self.headers = {}

    def post(self, url, data=None, timeout=None, allow_redirects=None):
        self.calls.append((url, dict(data or {}), allow_redirects))
        return self.resps.pop(0) if self.resps else FakeResp(500)

    def get(self, url, params=None, timeout=None):
        self.gets.append(url)
        return FakeResp(body='{}', ct='text/html')


class 실패를_말한다(unittest.TestCase):

    def test_JSON이_아니면_본문과_ContentType을_싣는다(self):
        s = FakeSession(FakeResp(body='<html>로그인이 필요합니다</html>',
                                 ct='text/html;charset=UTF-8'))
        with self.assertRaises(Fetch) as e:
            K._post(s, {'bld': 'x'})
        msg = str(e.exception)
        self.assertIn('text/html', msg)
        self.assertIn('로그인이 필요합니다', msg)

    def test_리다이렉트는_따라가지_않고_어디로_가려했는지_적는다(self):
        """POST 가 GET 으로 바뀌면 파라미터가 통째로 사라진 요청이 된다."""
        s = FakeSession(FakeResp(status=301, location='https://data.krx.co.kr/'))
        with self.assertRaises(Fetch) as e:
            K._post(s, {'bld': 'x'})
        self.assertIn('301', str(e.exception))
        self.assertIn('data.krx.co.kr', str(e.exception))
        self.assertFalse(s.calls[0][2], 'allow_redirects 가 켜져 있다')

    def test_오류코드는_본문의_사유와_함께_올린다(self):
        s = FakeSession(FakeResp(status=403, body='{"message":"권한이 없습니다"}'))
        with self.assertRaises(Fetch) as e:
            K._post(s, {'bld': 'x'})
        self.assertIn('403', str(e.exception))
        self.assertIn('권한이 없습니다', str(e.exception))

    def test_주소는_https다(self):
        """http 로 부르면 301 을 타고 POST 가 GET 으로 바뀐다."""
        for url in (K.BASE, K.LOGIN, K.REFERER):
            self.assertTrue(url.startswith('https://'), url)


class 세션(unittest.TestCase):

    def test_찌르기_전에_화면을_한_번_연다(self):
        """쿠키 없이 POST 하면 KRX 는 HTTP 400 에 'LOGOUT' 만 준다."""
        fake = FakeSession()
        orig = K.session
        K.session = lambda referer=None: fake
        try:
            K._s()
        finally:
            K.session = orig
        self.assertIn(K.REFERER, fake.gets)


class 표준코드(unittest.TestCase):

    def setUp(self):
        K._ISU.clear()
        K.FINDER_NOTE.clear()

    def test_단축코드를_표준코드로_바꾼다(self):
        s = FakeSession(FakeResp(body='{"block1":[{"short_code":"005930",'
                                      '"full_code":"KR7005930003"}]}'))
        self.assertEqual(K.find_isu('005930', s=s), 'KR7005930003')

    def test_한_번_찾으면_다시_부르지_않는다(self):
        s = FakeSession(FakeResp(body='{"block1":[{"short_code":"005930",'
                                      '"full_code":"KR7005930003"}]}'))
        K.find_isu('005930', s=s)
        K.find_isu('005930', s=s)
        self.assertEqual(len(s.calls), 1)

    def test_못_찾으면_단축코드로_물러서되_사유를_남긴다(self):
        s = FakeSession(FakeResp(body='{"block1":[]}'),
                        FakeResp(body='{"block1":[]}'))
        self.assertEqual(K._isu('005930', s=s), '005930')
        self.assertIn('005930', K.FINDER_NOTE)


if __name__ == '__main__':
    unittest.main()


class 실요청캡처(unittest.TestCase):
    """브라우저로 잡은 요청을 읽는 부분. 브라우저는 띄우지 않는다."""

    def setUp(self):
        from .. import capture
        self.C = capture

    def test_폼_본문을_이름과_값으로_푼다(self):
        got = self.C.parse_form('bld=dbms%2FMDC%2FSTAT%2Fstandard%2FMDCSTAT02303'
                                '&isuCd=KR7005930003&strtDd=20260901')
        self.assertEqual(got['bld'], 'dbms/MDC/STAT/standard/MDCSTAT02303')
        self.assertEqual(got['isuCd'], 'KR7005930003')

    def test_값이_빈_파라미터도_남긴다(self):
        """화면이 무엇을 보내는지가 중요하지 값이 찼는지가 아니다."""
        self.assertIn('share', self.C.parse_form('bld=x&share='))

    def test_쿠키는_적지_않는다(self):
        """잡은 것을 그대로 로그에 찍는다. 쿠키가 섞이면 세션이 샌다."""
        self.assertNotIn('cookie', [h.lower() for h in self.C.SAFE_HEADERS])
        self.assertNotIn('set-cookie', [h.lower() for h in self.C.SAFE_HEADERS])

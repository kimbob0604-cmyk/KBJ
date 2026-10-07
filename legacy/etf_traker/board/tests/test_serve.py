"""로컬 서버 — 링크 하나로 열리는지, 기본값이 조용히 외부에 열리지 않는지."""
import json
import os
import shutil
import tempfile
import unittest
import urllib.request

from ..web import serve


class ServeTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, 'd'))
        os.makedirs(os.path.join(self.root, 'x'))
        self._write('index.html', '<h1>보드</h1>')
        self._write('d/2026-08-27.html', '<h1>어제</h1>')
        self._write('d/index.json',
                    json.dumps({'latest': '2026-08-27', 'dates': ['2026-08-27']}))
        self.httpd = None

    def tearDown(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, rel, body):
        with open(os.path.join(self.root, rel), 'w', encoding='utf-8') as f:
            f.write(body)

    def _start(self, **kw):
        import threading
        self.httpd, url = serve.serve(self.root, port=0 or serve.DEFAULT_PORT,
                                      open_browser=False, log=lambda *a: None,
                                      forever=False, **kw)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return url

    def _get(self, url):
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode('utf-8'), dict(r.headers)

    def test_루트가_최신_보드다(self):
        url = self._start()
        code, body, _ = self._get(url)
        self.assertEqual(code, 200)
        self.assertIn('보드', body)

    def test_보관본과_목록도_열린다(self):
        """file:// 로 열면 fetch 가 막혀 날짜 선택기가 죽는다. 이래서 서버가 필요하다."""
        url = self._start()
        self.assertEqual(self._get(url + 'd/2026-08-27.html')[0], 200)
        code, body, _ = self._get(url + 'd/index.json')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['latest'], '2026-08-27')

    def test_캐시를_끈다(self):
        """daily 를 다시 돌렸는데 어제 화면이 보이면 안 된다."""
        url = self._start()
        _, _, headers = self._get(url)
        self.assertIn('no-store', headers.get('Cache-Control', ''))

    def test_기본은_이_컴퓨터에서만_연다(self):
        """기본값이 조용히 0.0.0.0 이면 안 된다."""
        url = self._start()
        self.assertTrue(url.startswith('http://127.0.0.1:'), url)

    def test_포트가_물려_있으면_다음_포트로_간다(self):
        url1 = self._start()
        first = int(url1.rstrip('/').rsplit(':', 1)[1])
        httpd2, url2 = serve.serve(self.root, open_browser=False,
                                   log=lambda *a: None, forever=False)
        try:
            self.assertNotEqual(url1, url2)
            self.assertGreater(int(url2.rstrip('/').rsplit(':', 1)[1]), first)
        finally:
            httpd2.server_close()

    def test_사이트가_없으면_뭘_돌리라고_알려준다(self):
        empty = tempfile.mkdtemp()
        try:
            with self.assertRaises(FileNotFoundError) as e:
                serve.serve(empty, open_browser=False, log=lambda *a: None,
                            forever=False)
            self.assertIn('--render', str(e.exception))
        finally:
            shutil.rmtree(empty, ignore_errors=True)

        with self.assertRaises(FileNotFoundError) as e:
            serve.serve(os.path.join(self.root, '없는폴더'),
                        open_browser=False, log=lambda *a: None, forever=False)
        self.assertIn('--daily', str(e.exception))

#!/usr/bin/env python3
"""사이트 조립 — 링크 하나로 최신이 보이고, 예전 주소가 안 깨지는지."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.web import site as S               # noqa: E402


class TestPublish(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # publish 는 render.py 가 만든 완성 문서를 받는다. 그 문서에는 <title> 이
    # 있고, 아티팩트 조각도 거기서 나온다. 껍데기만 있는 가짜를 넣으면 실제로
    # 통과하지 못할 입력을 통과시키게 되므로 최소 형태를 갖춰 준다.
    PAGE = '<!doctype html><html><head><title>보드 x</title></head><body>x</body></html>'

    def _pub(self, asof, html=PAGE, **kw):
        return S.publish(self.root, asof, html, log=lambda *a: None, **kw)

    def test_latest_is_at_the_site_root(self):
        """주소가 항상 같아야 링크를 공유할 수 있다."""
        self._pub('2026-08-27', '<!doctype html><html><head><title>보드 A</title></head><body>A</body></html>')
        with open(os.path.join(self.root, 'index.html'), encoding='utf-8') as f:
            self.assertEqual(f.read(), '<!doctype html><html><head><title>보드 A</title></head><body>A</body></html>')
        self._pub('2026-08-28', '<!doctype html><html><head><title>보드 B</title></head><body>B</body></html>')
        with open(os.path.join(self.root, 'index.html'), encoding='utf-8') as f:
            self.assertEqual(f.read(), '<!doctype html><html><head><title>보드 B</title></head><body>B</body></html>')

    def test_each_day_is_archived(self):
        self._pub('2026-08-27', '<!doctype html><html><head><title>보드 A</title></head><body>A</body></html>')
        self._pub('2026-08-28', '<!doctype html><html><head><title>보드 B</title></head><body>B</body></html>')
        with open(os.path.join(self.root, 'd', '2026-08-27.html'), encoding='utf-8') as f:
            self.assertEqual(f.read(), '<!doctype html><html><head><title>보드 A</title></head><body>A</body></html>')

    def test_index_json_lists_newest_first(self):
        for d in ('2026-08-25', '2026-08-27', '2026-08-26'):
            self._pub(d)
        with open(os.path.join(self.root, 'd', 'index.json'), encoding='utf-8') as f:
            j = json.load(f)
        self.assertEqual(j['dates'], ['2026-08-27', '2026-08-26', '2026-08-25'])
        self.assertEqual(j['latest'], '2026-08-27')

    def test_old_archives_are_pruned(self):
        for i in range(1, 8):
            self._pub(f'2026-08-{i:02d}', keep_days=3)
        left = sorted(f for f in os.listdir(os.path.join(self.root, 'd'))
                      if f.endswith('.html'))
        self.assertEqual(left, ['2026-08-05.html', '2026-08-06.html', '2026-08-07.html'])

    def test_nojekyll_exists(self):
        """Jekyll 이 돌면 밑줄로 시작하는 파일을 무시하고 빌드가 끼어든다."""
        self._pub('2026-08-27')
        self.assertTrue(os.path.exists(os.path.join(self.root, '.nojekyll')))

    def test_old_url_redirects(self):
        """이미 공유한 docs/board/ 주소가 404 가 되면 안 된다."""
        self._pub('2026-08-27')
        with open(os.path.join(self.root, 'board', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        self.assertIn('url=../', html)
        self.assertIn('<a href="../">', html)

    def test_xlsx_is_copied_next_to_the_page(self):
        src = os.path.join(self.root, 'tmp.xlsx')
        with open(src, 'wb') as f:
            f.write(b'PK\x03\x04')
        self._pub('2026-08-27', xlsx=src)
        self.assertTrue(os.path.exists(os.path.join(self.root, 'x', 'tmp.xlsx')))

    def test_publishing_twice_same_day_is_idempotent(self):
        self._pub('2026-08-27', '<!doctype html><html><head><title>보드 1</title></head><body>1</body></html>')
        self._pub('2026-08-27', '<!doctype html><html><head><title>보드 2</title></head><body>2</body></html>')
        with open(os.path.join(self.root, 'd', '2026-08-27.html'), encoding='utf-8') as f:
            self.assertEqual(f.read(), '<!doctype html><html><head><title>보드 2</title></head><body>2</body></html>')
        with open(os.path.join(self.root, 'd', 'index.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['dates'], ['2026-08-27'])

    def test_missing_xlsx_is_not_an_error(self):
        self._pub('2026-08-27', xlsx=os.path.join(self.root, 'nope.xlsx'))
        self.assertTrue(os.path.exists(os.path.join(self.root, 'index.html')))



    # ── flowlab 부가 리포트 링크 ────────────────────────────
    SLOT_PAGE = ('<!doctype html><html><head><title>보드 x</title></head>'
                 '<body><!--reports--></body></html>')

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), encoding='utf-8') as f:
            return f.read()

    def test_report_links_only_when_files_exist(self):
        """없는 리포트에 링크를 달면 404 다. 있는 것만 단다."""
        self._pub('2026-08-27', self.SLOT_PAGE)
        idx = self._read('index.html')
        self.assertNotIn('flows-history.html', idx)
        self.assertIn('<!--reports--><!--/reports-->', idx)   # 자리는 비워 두되 표식은 남긴다
        for fn in ('flows-history.html', 'eventstudy.html', 'flows-20260827.html'):
            open(os.path.join(self.root, fn), 'w').close()
        self._pub('2026-08-27', self.SLOT_PAGE)
        idx = self._read('index.html')
        self.assertIn('href="flows-20260827.html">수급<', idx)
        self.assertIn('href="flows-history.html">수급 누적<', idx)
        self.assertIn('href="eventstudy.html">이벤트 스터디<', idx)

    def test_archived_day_links_reports_one_level_up(self):
        """보관본은 d/ 아래라 상대경로가 한 단계 다르다."""
        open(os.path.join(self.root, 'flows-history.html'), 'w').close()
        self._pub('2026-08-27', self.SLOT_PAGE)
        day = self._read('d', '2026-08-27.html')
        self.assertIn('href="../flows-history.html"', day)
        self.assertNotIn('href="flows-history.html"', day)

    def test_relink_fills_links_after_publish(self):
        """엔진이 먼저 올리고 flowlab 이 나중에 리포트를 만들어도 링크가 붙는다."""
        page = self.SLOT_PAGE.replace('<body>', '<body data-asof="2026-08-27">')
        self._pub('2026-08-27', page)
        self.assertNotIn('flows-history.html', self._read('index.html'))
        open(os.path.join(self.root, 'flows-history.html'), 'w').close()
        n = S.relink(self.root, '2026-08-27', log=lambda *a: None)
        self.assertEqual(n, 2)                           # index + 보관본
        self.assertIn('href="flows-history.html">수급 누적<', self._read('index.html'))
        self.assertIn('href="../flows-history.html"', self._read('d', '2026-08-27.html'))
        # 다시 돌려도 바뀌는 것이 없고, 링크가 겹겹이 쌓이지 않는다
        self.assertEqual(S.relink(self.root, '2026-08-27', log=lambda *a: None), 0)
        open(os.path.join(self.root, 'eventstudy.html'), 'w').close()
        S.relink(self.root, '2026-08-27', log=lambda *a: None)
        idx = self._read('index.html')
        self.assertEqual(idx.count('<!--reports-->'), 1)
        self.assertEqual(idx.count('flows-history.html'), 1)
        self.assertEqual(idx.count('eventstudy.html'), 1)

    def test_relink_skips_root_of_another_day(self):
        """루트가 다른 날짜 보드면 그 날 링크를 루트에 달지 않는다."""
        page = self.SLOT_PAGE.replace('<body>', '<body data-asof="2026-08-28">')
        self._pub('2026-08-28', page)
        open(os.path.join(self.root, 'flows-20260827.html'), 'w').close()
        S.relink(self.root, '2026-08-27', log=lambda *a: None)
        self.assertNotIn('flows-20260827.html', self._read('index.html'))

    def test_page_without_slot_is_untouched(self):
        """예전 렌더 결과(자리 없음)도 그대로 올라간다."""
        open(os.path.join(self.root, 'flows-history.html'), 'w').close()
        self._pub('2026-08-27')
        self.assertEqual(self._read('index.html'), self.PAGE)


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Google News RSS — board/ingest/gnews.py. 제목-매체 분리 · GMT→KST · XML 픽스처."""
import unittest
from unittest import mock

from ..ingest import gnews as G
from ..ingest.http import Fetch

XML = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>"케이씨 when:1d" - Google 뉴스</title>
<item>
  <title>케이씨, 반도체 장비 수주 - 매일경제</title>
  <link>https://news.google.com/rss/articles/CBMi...?oc=5</link>
  <pubDate>Mon, 21 Sep 2026 06:12:00 GMT</pubDate>
  <source url="https://www.mk.co.kr">매일경제</source>
</item>
<item>
  <title>케이씨텍 신제품 - 어제 신문</title>
  <link>https://news.google.com/rss/articles/xyz</link>
  <pubDate>Sun, 20 Sep 2026 14:59:00 GMT</pubDate>
</item>
<item>
  <title>매체 표기 없는 제목</title>
  <link>https://news.google.com/rss/articles/abc</link>
  <pubDate>이상한 날짜</pubDate>
</item>
</channel></rss>'''


class Parse(unittest.TestCase):
    def test_items_are_parsed_with_outlet_from_source(self):
        got = G.parse(XML)
        self.assertEqual(len(got), 3)
        a = got[0]
        self.assertEqual(a['title'], '케이씨, 반도체 장비 수주')
        self.assertEqual(a['outlet'], '매일경제')
        self.assertEqual(a['url'], 'https://news.google.com/rss/articles/CBMi...?oc=5')
        self.assertEqual(a['source'], 'google_news')

    def test_gmt_becomes_kst_date(self):
        got = G.parse(XML)
        self.assertEqual(got[0]['date'], '2026-09-21')
        self.assertEqual(got[0]['published_at'], '2026-09-21T15:12+09:00')
        # 20일 23:59 KST — GMT 로는 14:59. 날짜가 넘어가지 않아야 한다.
        self.assertEqual(got[1]['date'], '2026-09-20')

    def test_title_without_source_element_splits_on_last_dash(self):
        got = G.parse(XML)
        self.assertEqual(got[1]['title'], '케이씨텍 신제품')
        self.assertEqual(got[1]['outlet'], '어제 신문')

    def test_unparseable_date_is_none_not_today(self):
        got = G.parse(XML)
        self.assertEqual(got[2]['title'], '매체 표기 없는 제목')
        self.assertIsNone(got[2]['outlet'])
        self.assertIsNone(got[2]['date'])

    def test_non_xml_raises_fetch(self):
        with self.assertRaises(Fetch) as e:
            G.parse('Sorry, you have been blocked (429)')
        self.assertIn('XML 이 아니다', str(e.exception))

    def test_consent_html_raises_fetch(self):
        with self.assertRaises(Fetch) as e:
            G.parse('<html><body>Before you continue to Google</body></html>')
        self.assertIn('consent', str(e.exception))

    def test_broken_xml_raises_fetch(self):
        with self.assertRaises(Fetch):
            G.parse('<rss><channel><item><title>x</title></channel>')

    def test_empty_body(self):
        with self.assertRaises(Fetch):
            G.parse('')


class Split(unittest.TestCase):
    def test_source_name_is_stripped_exactly(self):
        self.assertEqual(G.split_title('A - B - 매일경제', '매일경제'), ('A - B', '매일경제'))

    def test_without_source_uses_tail(self):
        self.assertEqual(G.split_title('제목 - 매체'), ('제목', '매체'))
        self.assertEqual(G.split_title('제목만'), ('제목만', None))


class ToKst(unittest.TestCase):
    def test_offsets(self):
        self.assertEqual(G.to_kst('Mon, 21 Sep 2026 06:12:00 GMT').isoformat(), '2026-09-21T15:12:00+09:00')
        self.assertEqual(G.to_kst('Mon, 21 Sep 2026 06:12:00 +0000').isoformat(), '2026-09-21T15:12:00+09:00')
        self.assertEqual(G.to_kst('Mon, 21 Sep 2026 15:12:00 +0900').isoformat(), '2026-09-21T15:12:00+09:00')
        self.assertIsNone(G.to_kst('없음'))
        self.assertIsNone(G.to_kst(None))


class Search(unittest.TestCase):
    def test_url_and_params(self):
        p = G.params('케이씨')
        self.assertEqual(p['q'], '케이씨 when:1d')
        self.assertEqual((p['hl'], p['gl'], p['ceid']), ('ko', 'KR', 'KR:ko'))

    def test_search_passes_timeout_and_retries_and_parses(self):
        seen = {}

        def fake_get(s, url, params=None, want=None, timeout=None, retries=None, **kw):
            seen.update(url=url, params=params, want=want, timeout=timeout, retries=retries)
            return XML
        with mock.patch.object(G, 'get', side_effect=fake_get):
            got = G.search('케이씨', s=object(), timeout=8, retries=2)
        self.assertEqual(seen['url'], G.URL)
        self.assertEqual(seen['params']['q'], '케이씨 when:1d')
        self.assertEqual((seen['want'], seen['timeout'], seen['retries']), ('text', 8, 2))
        self.assertEqual(len(got), 3)

    def test_probe_reports_failure_as_row(self):
        with mock.patch.object(G, 'get', side_effect=Fetch('x 실패: HTTP 429 · 한도')):
            rows = G.probe()
        self.assertEqual(rows[0][1], False)
        self.assertIn('429', rows[0][2])


if __name__ == '__main__':
    unittest.main()

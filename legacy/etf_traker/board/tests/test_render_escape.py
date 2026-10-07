"""외부 소스에서 온 이름이 화면 마크업을 깨지 못하게 한다.

종목명·섹터명은 네이버·KRX 가 주는 값이라 내용을 통제하지 못한다. 서버 렌더는
모든 값을 escape 하는데, 히트맵으로 가는 두 경로만 빠져 있었다.

  1. JSON 을 `<script type=application/json>` 안에 그대로 넣었다. 문자열 안의
     `</script` 가 HTML 파서에게 스크립트 끝으로 읽히고 그 뒤는 전부 마크업이
     된다.
  2. board.js 가 c.name·g.group 을 이어 붙여 innerHTML 로 넣었다.
"""
import json
import os
import re
import unittest

from ..web import render as R


class JsonInHtmlTest(unittest.TestCase):

    def test_closing_script_tag_cannot_escape(self):
        s = R._json_in_html({'name': '</script><img src=x onerror=1>'})
        self.assertNotIn('</script', s.lower())

    def test_value_survives_a_round_trip(self):
        for v in ('</script>', '<b>', '삼성전자', 'S&T모티브', 'a<b>c'):
            self.assertEqual(json.loads(R._json_in_html({'v': v}))['v'], v)

    def test_korean_is_not_escaped_away(self):
        # ensure_ascii=False 를 유지한다. \\uXXXX 로 부풀면 문서가 커진다.
        self.assertIn('삼성전자', R._json_in_html({'v': '삼성전자'}))

    def test_render_uses_the_helper(self):
        import inspect
        src = inspect.getsource(R)
        self.assertIn('_json_in_html(hm)', src)
        self.assertNotIn('json.dumps(hm, ensure_ascii=False)}</script>', src)


class BoardJsEscapeTest(unittest.TestCase):
    """board.js 는 파이썬 시험이 실행하지 못한다. 소스로 확인한다."""

    def setUp(self):
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'web', 'board.js')
        self.js = open(p, encoding='utf-8').read()

    def test_has_an_escaper(self):
        self.assertRegex(self.js, r'function esc\s*\(')
        for ch in ('&amp;', '&lt;', '&gt;', '&quot;'):
            self.assertIn(ch, self.js)

    def test_names_go_through_it(self):
        self.assertIn('esc(c.name)', self.js)
        self.assertIn('esc(g.group)', self.js)
        self.assertIn('esc(tip)', self.js)

    def test_no_raw_name_interpolation_left(self):
        for bad in ("+ c.name +", "+ g.group +"):
            self.assertNotIn(bad, self.js, f'이스케이프 없이 이어 붙인 곳이 있다: {bad}')

    def test_escaper_handles_ampersand_first(self):
        """& 를 먼저 바꾸지 않으면 이미 바꾼 &lt; 가 다시 망가진다."""
        body = re.search(r'function esc\s*\([\s\S]*?\n  \}', self.js).group(0)
        order = [m for m in re.findall(r"replace\(/(&|<|>|\"|')/g", body)]
        self.assertEqual(order[0], '&', f'치환 순서가 잘못됐다: {order}')


if __name__ == '__main__':
    unittest.main()

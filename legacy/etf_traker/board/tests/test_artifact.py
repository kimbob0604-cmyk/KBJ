"""
아티팩트 조각 변환 검증 — board/web/artifact.py.

여기서 잡으려는 것은 "조용히 깨지는" 두 가지다.
  1. 껍데기 태그가 남으면 아티팩트 안에서 문서가 문서를 감싸 본문이 새어 나온다.
  2. CSP 에 막히는 스타일시트는 에러 없이 무시된다. 글꼴만 조용히 바뀐다.
둘 다 화면을 봐야 알 수 있으므로 기계로 막는다.
"""
import unittest

from board.web import artifact as A

PAGE = (
    '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>국장 신고가 보드 2026-08-28</title>'
    '<link rel="icon" href="data:image/svg+xml,%3Csvg%3E">'
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/x/pretendard.css">'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono">'
    '<noscript><link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/x/pretendard.css">'
    '</noscript>'
    '<style>body{font-family:Pretendard,-apple-system,sans-serif}</style>'
    '</head><body><header>'
    '<label class="daypick">날짜<select id="daysel"><option>2026-08-28</option>'
    '</select></label>'
    '<a class="dl" href="x/rankings-2026-08-28.xlsx" download>랭킹 엑셀</a>'
    '</header><main>코스맥스 +7.07%</main><script>var a=1;</script></body></html>')


class Convert(unittest.TestCase):
    def setUp(self):
        self.title, self.frag = A.convert(PAGE)

    def test_title_is_stable_across_days(self):
        # 갤러리·탭에서 사람이 찾는 이름이다. 날짜가 붙으면 매일 다른 페이지로
        # 보인다. 기준일은 화면 머리말에 이미 있다.
        self.assertEqual(self.title, '국장 신고가 보드')
        # 아티팩트는 파일 앞 8KB 안에서만 제목을 찾는다. 맨 앞이어야 한다.
        self.assertTrue(self.frag.startswith('<title>국장 신고가 보드</title>'))
        self.assertEqual(self.frag.count('<title>'), 1)

    def test_shell_tags_gone(self):
        low = self.frag.lower()
        for tag in ('<!doctype', '<html', '</html>', '<head>', '</head>',
                    '<body', '</body>', '<meta'):
            self.assertNotIn(tag, low, f'{tag} 가 남아 있다')

    def test_blocked_stylesheets_gone(self):
        self.assertNotIn('jsdelivr', self.frag)
        self.assertNotIn('<noscript>', self.frag)
        self.assertNotIn('rel="icon"', self.frag)
        # 통과하는 것만 남는다
        self.assertIn('fonts.googleapis.com/css2?family=IBM+Plex+Mono', self.frag)

    def test_korean_font_substituted(self):
        # Pretendard 는 못 받는다. 대체 글꼴을 실제로 불러와야 한다.
        self.assertNotIn('font-family:Pretendard', self.frag)
        self.assertIn(f"font-family:'{A.KR_FONT}'", self.frag)
        self.assertIn('family=IBM+Plex+Sans+KR', self.frag)

    def test_site_only_controls_gone(self):
        # 아티팩트에는 d/index.json 도 x/*.xlsx 도 없다. 눌리는 척하면 안 된다.
        self.assertNotIn('daypick">', self.frag)
        self.assertNotIn('id="daysel"', self.frag)
        self.assertNotIn('rankings-2026-08-28.xlsx', self.frag)

    def test_content_and_script_kept(self):
        self.assertIn('코스맥스 +7.07%', self.frag)
        self.assertIn('<script>var a=1;</script>', self.frag)
        self.assertIn('<style>', self.frag)

    def test_rejects_foreign_document(self):
        with self.assertRaises(ValueError):
            A.convert('<html><body>제목이 없다</body></html>')


if __name__ == '__main__':
    unittest.main()


class CloseMark(unittest.TestCase):
    """고가 기준 판정 옆에 종가 기준 결과를 붙이는 표시 — render._close_mark.

    기본 판정은 고가 기준인데 증권사 스크리너 상당수는 종가 기준이다. 그 차이를
    화면에서 지우면 '왜 이게 신고가냐'는 질문에 답할 수가 없다.
    """

    def setUp(self):
        from board.web import render
        # 라벨의 한글 이름은 newhigh.json 이 매 렌더마다 채운다. 시험에서는 직접 넣는다.
        self._saved = dict(render.LABEL_KO)
        render.LABEL_KO.update(hist='역사적', w52='52주', d120='120일')
        self.render = render
        self.f = render._close_mark

    def tearDown(self):
        self.render.LABEL_KO.clear()
        self.render.LABEL_KO.update(self._saved)

    def test_same_label_shows_nothing(self):
        # 둘 다 역사적이면 덧붙일 말이 없다. 표를 시끄럽게 하지 않는다.
        self.assertEqual(self.f(dict(high_basis=dict(label='hist'),
                                     close_basis=dict(label='hist'))), '')

    def test_lower_close_label_is_named(self):
        out = self.f(dict(high_basis=dict(label='hist'), close_basis=dict(label='w52')))
        self.assertIn('종가 52주', out)
        self.assertIn('tag-mut', out)

    def test_no_close_label_says_so(self):
        out = self.f(dict(high_basis=dict(label='w52'), close_basis=dict(label=None)))
        self.assertIn('종가 미달', out)

    def test_missing_close_basis_is_treated_as_no_close_label(self):
        # close_basis 가 아예 없는 옛 state 를 읽어도 조용히 빈칸이 되면 안 된다.
        self.assertIn('종가 미달', self.f(dict(high_basis=dict(label='d120'))))

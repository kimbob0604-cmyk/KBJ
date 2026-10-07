#!/usr/bin/env python3
"""업종 목록 파서 단위 시험.

2026-09-21 러너 `--check` 가 '업종 목록 파싱 실패 — 페이지 구조 변경 의심' 으로
끝났다. 옛 정규식이 주소 모양을 너무 좁게 봤다 — 번호 바로 뒤에 닫는 따옴표가
와야 하고, 앵커 안이 태그 없는 글자여야 했다. 주소에 파라미터가 하나만 더
붙어도 목록이 통째로 비는데, 실패 사유는 여섯 글자뿐이라 러너를 한 번 더
돌려야 원인을 볼 수 있었다.

여기서 못 박는 것은 둘이다 — 흔한 주소 모양 변화에 살아남는가, 그래도 못 뽑으면
**무엇을 봤는지** 사유에 남는가.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.ingest import naver as N              # noqa: E402


OLD = ('<table><tr><td>'
       '<a href="/sise/sise_group_detail.naver?type=upjong&no=261">반도체와반도체장비</a>'
       '</td></tr><tr><td>'
       '<a href="/sise/sise_group_detail.naver?type=upjong&no=013">건설</a>'
       '</td></tr></table>')


class TestShapes(unittest.TestCase):
    """주소·앵커 모양이 달라져도 뽑힌다."""

    def test_old_shape(self):
        self.assertEqual(N._sector_rows(OLD),
                         [dict(no=261, name='반도체와반도체장비'), dict(no=13, name='건설')])

    def test_extra_query_param(self):
        """`no=261` 뒤에 파라미터가 더 붙는다 — 옛 정규식이 여기서 죽었다."""
        html = ('<a href="/sise/sise_group_detail.naver?type=upjong&no=261&page=1">'
                '반도체와반도체장비</a>')
        self.assertEqual(N._sector_rows(html), [dict(no=261, name='반도체와반도체장비')])

    def test_single_quotes_and_attribute_order(self):
        html = ("<a class='tltle' title='반도체' "
                "href='/sise/sise_group_detail.naver?type=upjong&no=261'>반도체와반도체장비</a>")
        self.assertEqual(N._sector_rows(html), [dict(no=261, name='반도체와반도체장비')])

    def test_nested_tags_in_anchor_text(self):
        html = ('<a href="/sise/sise_group_detail.naver?type=upjong&no=261">'
                '<span class="n">반도체와반도체장비</span></a>')
        self.assertEqual(N._sector_rows(html), [dict(no=261, name='반도체와반도체장비')])

    def test_legacy_nhn_extension(self):
        html = '<a href="/sise/sise_group_detail.nhn?type=upjong&no=013">건설</a>'
        self.assertEqual(N._sector_rows(html), [dict(no=13, name='건설')])

    def test_entities_are_unescaped(self):
        html = ('<a href="/sise/sise_group_detail.naver?type=upjong&amp;no=272">'
                '전기&amp;전자</a>')
        self.assertEqual(N._sector_rows(html), [dict(no=272, name='전기&전자')])

    def test_order_is_kept_and_duplicates_dropped(self):
        """같은 업종이 표와 목록 두 곳에 나온다. 순서를 지키고 한 번만 센다."""
        html = OLD + '<a href="/sise/sise_group_detail.naver?type=upjong&no=261">반도체</a>'
        rows = N._sector_rows(html)
        self.assertEqual([r['no'] for r in rows], [261, 13])


class TestNotASector(unittest.TestCase):
    """업종이 아닌 링크는 세지 않는다."""

    def test_theme_and_group_links_are_ignored(self):
        html = ('<a href="/sise/sise_group_detail.naver?type=theme&no=100">2차전지</a>'
                '<a href="/sise/sise_group_detail.naver?type=group&no=5">삼성</a>')
        self.assertEqual(N._sector_rows(html), [])

    def test_anchor_without_number_is_ignored(self):
        html = '<a href="/sise/sise_group_detail.naver?type=upjong">업종별 시세</a>'
        self.assertEqual(N._sector_rows(html), [])

    def test_empty_name_is_ignored(self):
        """이미지 링크는 이름이 없다. 빈 이름을 업종으로 만들지 않는다."""
        html = ('<a href="/sise/sise_group_detail.naver?type=upjong&no=261">'
                '<img src="x.gif"></a>')
        self.assertEqual(N._sector_rows(html), [])


class TestWhy(unittest.TestCase):
    """못 뽑았을 때 무엇을 봤는지 남는다 (2장 6번)."""

    def test_reason_counts_the_marks(self):
        why = N._sector_why('<html>' + '<a href="/foo">x</a>' * 3 + '</html>')
        self.assertIn('sise_group_detail 0회', why)
        self.assertIn('<a  3회', why)
        self.assertIn('후보 주소 없음', why)

    def test_reason_shows_candidate_hrefs(self):
        why = N._sector_why('<a href="/sise/sise_group.naver?type=upjong">업종</a>')
        self.assertIn('/sise/sise_group.naver?type=upjong', why)

    def test_fetch_raises_with_the_reason(self):
        from board.ingest.http import Fetch
        real = N.get
        N.get = lambda *a, **k: '<html>아무것도 없다</html>'
        try:
            with self.assertRaises(Fetch) as cm:
                N.fetch_sector_index(s=object())
        finally:
            N.get = real
        self.assertIn('sise_group_detail 0회', str(cm.exception))


if __name__ == '__main__':
    unittest.main()

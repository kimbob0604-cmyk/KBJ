"""
섹터 뉴스 수집 검증 — board/ingest/news.py.

여기서 지키려는 것은 두 가지다.

1. **못 받은 것과 없는 것을 구분한다.** 검색이 실패했는데 빈 목록을 내면 화면이
   "오늘 재료가 없었다"는 사실 주장을 하게 된다 (CLAUDE.md 2장 6번).
2. **오늘 움직인 테마에만 붙인다.** 안 움직인 테마의 기사는 그날의 트리거가
   아니라 그냥 기사다.
"""
import unittest
from unittest import mock

from board.ingest import news as N

CFG = dict(news=dict(max_themes=3, stocks_per_theme=2, max_articles=2,
                     per_query=10, min_abs_chg_pct=2.0))


def theme(name, chg, nh=0, codes=(), leader=None):
    return dict(name=name, theme=name, chg_pct=chg, n_newhigh=nh,
                codes=list(codes), leader=leader)


class Pick(unittest.TestCase):
    def test_quiet_themes_are_dropped(self):
        ts = [theme('조용함', 0.4), theme('움직임', 7.1)]
        self.assertEqual([t['name'] for t in N.pick_themes(ts, CFG)], ['움직임'])

    def test_newhigh_beats_the_move_threshold(self):
        # 등락률이 문턱 아래여도 신고가가 있으면 그날 뭔가 있었던 것이다.
        ts = [theme('신고가만', 0.2, nh=3)]
        self.assertEqual([t['name'] for t in N.pick_themes(ts, CFG)], ['신고가만'])

    def test_newhigh_outranks_a_bigger_move(self):
        ts = [theme('많이올랐다', 9.0), theme('신고가둘', 3.0, nh=2)]
        self.assertEqual([t['name'] for t in N.pick_themes(ts, CFG)][0], '신고가둘')

    def test_a_big_drop_counts_as_movement(self):
        # 급락도 재료가 있다. 부호가 아니라 폭으로 본다.
        self.assertEqual(len(N.pick_themes([theme('급락', -8.0)], CFG)), 1)

    def test_limit_is_honoured(self):
        ts = [theme(f't{i}', 10 - i) for i in range(9)]
        self.assertEqual(len(N.pick_themes(ts, CFG)), 3)


class StockNames(unittest.TestCase):
    STOCKS = [dict(code='1', name='작은데많이올랐다', turnover=3.0),
              dict(code='2', name='거래대금1등', turnover=900.0),
              dict(code='3', name='거래대금2등', turnover=400.0),
              dict(code='9', name='다른테마', turnover=9999.0)]

    def test_picked_by_turnover_not_by_move(self):
        # 등락률로 고르면 시총 30억짜리 상한가가 뽑힌다. 그 이름으로 검색해 봐야
        # 그날 시장이 반응한 재료는 안 나온다.
        t = theme('테마', 5.0, codes=('1', '2', '3'))
        self.assertEqual(N.theme_stock_names(t, self.STOCKS, 2),
                         ['거래대금1등', '거래대금2등'])

    def test_leader_comes_first_and_is_not_duplicated(self):
        t = theme('테마', 5.0, codes=('1', '2', '3'),
                  leader=dict(code='2', name='거래대금1등'))
        got = N.theme_stock_names(t, self.STOCKS, 3)
        self.assertEqual(got[0], '거래대금1등')
        self.assertEqual(len(got), len(set(got)))

    def test_other_themes_are_not_borrowed(self):
        t = theme('테마', 5.0, codes=('1',))
        self.assertNotIn('다른테마', N.theme_stock_names(t, self.STOCKS, 3))


class Collect(unittest.TestCase):
    SEC = dict(themes=[theme('원전', 6.0, nh=2, codes=('1',),
                             leader=dict(code='1', name='한전기술'))])
    UNI = dict(stocks=[dict(code='1', name='한전기술', turnover=500.0)])

    def test_missing_credentials_is_stated_not_swallowed(self):
        with mock.patch.object(N.creds, 'has', return_value=False):
            out = N.collect('2026-08-28', self.SEC, self.UNI, CFG, log=lambda *a: None)
        self.assertEqual(out['themes'], [])
        self.assertTrue(any('없음' in m for m in out['missing']))

    def test_a_failed_search_names_the_theme(self):
        with mock.patch.object(N.creds, 'has', return_value=True), \
             mock.patch.object(N, '_s', return_value=None), \
             mock.patch.object(N, 'for_theme', side_effect=N.Fetch('429 과호출')):
            out = N.collect('2026-08-28', self.SEC, self.UNI, CFG, log=lambda *a: None)
        self.assertEqual(out['themes'], [])
        self.assertTrue(any('원전' in m and '429' in m for m in out['missing']))

    def test_search_ok_but_no_articles_is_said_out_loud(self):
        # 이게 '못 받음' 과 섞이면 안 된다. 검색은 됐고 그날 기사가 없었던 것이다.
        with mock.patch.object(N.creds, 'has', return_value=True), \
             mock.patch.object(N, '_s', return_value=None), \
             mock.patch.object(N, 'for_theme', return_value=[]):
            out = N.collect('2026-08-28', self.SEC, self.UNI, CFG, log=lambda *a: None)
        self.assertEqual(len(out['themes']), 1)
        self.assertTrue(any('하나도 없었습니다' in m for m in out['missing']))

    def test_stock_query_articles_come_first(self):
        """테마명은 일반 명사라 스쳐 지나간 기사가 다 걸린다.

        실데이터에서 화장품 카드 첫 줄이 "제주, 7개월 연속 수출 증가율 전국 1위"
        였다. 종목명 질의가 적어도 그 테마의 실제 회사를 가리킨다.
        """
        arts = [dict(title='원전 업계 전반', url='u2', query='원전'),
                dict(title='한전기술, 수주 공시', url='u1', query='한전기술')]
        with mock.patch.object(N.creds, 'has', return_value=True), \
             mock.patch.object(N, '_s', return_value=None), \
             mock.patch.object(N, 'for_theme', return_value=arts):
            out = N.collect('2026-08-28', self.SEC, self.UNI, CFG, log=lambda *a: None)
        self.assertEqual([a['title'] for a in out['themes'][0]['articles']],
                         ['한전기술, 수주 공시', '원전 업계 전반'])
        self.assertEqual(out['missing'], [])

    def test_articles_are_capped_but_the_true_count_is_kept(self):
        arts = [dict(title=f'a{i}', url=f'u{i}', query='원전') for i in range(9)]
        with mock.patch.object(N.creds, 'has', return_value=True), \
             mock.patch.object(N, '_s', return_value=None), \
             mock.patch.object(N, 'for_theme', return_value=arts):
            out = N.collect('2026-08-28', self.SEC, self.UNI, CFG, log=lambda *a: None)
        t = out['themes'][0]
        self.assertEqual(len(t['articles']), 2)
        self.assertEqual(t['n_found'], 9)   # 자른 것이지 없던 게 아니다


class Parsing(unittest.TestCase):
    def test_pubdate_does_not_depend_on_locale(self):
        # strptime('%b') 는 LC_TIME 에 의존해서 러너 로케일이 바뀌면 전량 실패하고
        # 기사가 조용히 다 버려진다.
        self.assertEqual(N._pubdate('Tue, 26 Aug 2026 18:03:00 +0900'), '2026-08-26')
        self.assertEqual(N._pubdate('Wed, 01 Jan 2025 09:00:00 +0900'), '2025-01-01')
        self.assertIsNone(N._pubdate('알 수 없는 형식'))
        self.assertIsNone(N._pubdate(None))

    def test_tags_and_entities_are_stripped(self):
        self.assertEqual(N._clean('<b>원전</b> &amp; 방산'), '원전 & 방산')


if __name__ == '__main__':
    unittest.main()


class Rank(unittest.TestCase):
    """기사 정렬·선별 — board/ingest/news.rank."""

    def test_stock_name_missing_from_the_title_is_dropped(self):
        # 본문에 스친 기사다. 그날의 트리거라면 제목에 이름이 있다.
        arts = [dict(title='뷰티 시장 동향', query='한국콜마'),
                dict(title='한국콜마, 3분기 영업익 40% 증가', query='한국콜마')]
        got = [a['title'] for a in N.rank(arts, '화장품')]
        self.assertEqual(got, ['한국콜마, 3분기 영업익 40% 증가'])

    def test_theme_query_articles_are_kept_even_without_the_word(self):
        # 테마명은 검색어일 뿐 기사 제목에 그대로 나오지 않아도 된다.
        # (다만 순서는 뒤다 — 잡음이 섞이는 쪽이라서.)
        arts = [dict(title='정부, 원자력 예산 확대', query='원전')]
        self.assertEqual(len(N.rank(arts, '원전')), 1)

    def test_order_is_stock_then_theme(self):
        arts = [dict(title='원전 일반', query='원전'),
                dict(title='한전기술 수주', query='한전기술'),
                dict(title='원전 일반 2', query='원전')]
        got = [a['query'] for a in N.rank(arts, '원전')]
        self.assertEqual(got[0], '한전기술')

    def test_empty_input(self):
        self.assertEqual(N.rank([], '원전'), [])

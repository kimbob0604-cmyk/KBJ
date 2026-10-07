"""
종목별 트리거 수집 — board/ingest/triggers.py (D-085).

여기서 지키려는 것.
  1. 채택 규칙 — 어절 경계·접두 이름 제외·일반명사 조건·자동 생성 기사 제외
  2. 우선주는 보통주로 묻고 표시한다. 보통주가 없으면 건너뛰되 그 사실을 남긴다
  3. 당일 것만, 정규화 제목으로 중복 제거, 종목당 상한, 소스 우선순위
  4. 소스를 접는 조건(접속 불가·401/403/429·DART 키/한도 status·Google consent 페이지)과
     접지 않는 조건(종목 하나의 실패) — 접지 않은 실패도 소스별 N/M 종목으로 결손에 남는다
  5. 시간 예산 — 데드라인을 넘긴 종목은 '시간 예산 초과' 로 남고 결손에 적힌다
  6. 인박스 매칭과 파일 없음
  7. items 가 빈 종목은 결손이 아니다. 검색 범위는 sources 에 남는다
"""
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

from ..ingest import triggers as T
from ..ingest.http import Fetch

ASOF = '2026-09-21'
NOW = datetime(2026, 9, 21, 17, 5, tzinfo=T.KST)

CFG = dict(triggers=dict(
    kind='w52', per_stock_items=3,
    naver_queries=['{name} {code}', '{name} 신고가'], naver_display=20,
    gnews_queries=['{name}'],
    budget_sec=240, per_source_sec=dict(naver_news=90, google_news=60, dart=60, telegram_x=20),
    # 인박스 기능 시험은 켜 놓고 돈다. 배포 기본값은 꺼짐이고 그것은
    # InboxDefaultOff 가 따로 못 박는다 — 기능과 정책을 같은 시험에 섞지 않는다.
    timeout_sec=8, retries=1, inbox_hours=24, inbox_enabled=True,
    title_chars_tg=60,
    name_particles=['가', '는', '은', '이', '도', '의', '를', '을', '와', '과', '로', '에'],
    generic_names=['케이씨', '대상'],
    stock_words=['주가', '신고가', '수주', '공시'],
    autogen_title_patterns=[r'주가,?\s*\d{1,2}월\s*\d{1,2}일', r'장중\s*[\d,]+원'],
    domain_blacklist=['topstarnews.net'],
    finance_outlets={'mk.co.kr': '매일경제'},
    outlets={'yna.co.kr': '연합뉴스'}))


def row(code, name, close=None, high=None, **kw):
    return dict(code=code, name=name, close_basis=dict(label=close),
                high_basis=dict(label=high), **kw)


NEWHIGH = dict(as_of=ASOF, achieved=[
    row('029460', '케이씨', close='w52', high='w52'),
    row('086670', '비엠티', close='hist', high='hist'),
    row('010955', 'S-Oil우', close='w52', high='w52'),
    row('000500', '가온전선', close='hist', high='d60'),
    row('999999', '60일뿐', close='d60', high='d60'),
])
NAVER_ALL = ['케이씨 029460', '케이씨 신고가', '비엠티 086670', '비엠티 신고가',
             'S-Oil 010950', 'S-Oil 신고가', '가온전선 000500', '가온전선 신고가']
UNIVERSE = dict(stocks=[
    dict(code='029460', name='케이씨'), dict(code='029461', name='케이씨텍'),
    dict(code='002380', name='케이씨씨'), dict(code='086670', name='비엠티'),
    dict(code='010950', name='S-Oil'), dict(code='010955', name='S-Oil우'),
    dict(code='000500', name='가온전선'), dict(code='001680', name='대상'),
])
S = T.settings(CFG)


def art(title, outlet='news.example.com', date=ASOF, summary='', url=None, **kw):
    return dict(title=title, outlet=outlet, date=date, summary=summary,
                url=url or f'https://{outlet}/{abs(hash(title)) % 10**6}', **kw)


class Boundary(unittest.TestCase):
    P = S['name_particles']

    def test_name_followed_by_punctuation_or_space_matches(self):
        self.assertTrue(T.name_in('케이씨', '케이씨, 52주 신고가 경신', self.P))
        self.assertTrue(T.name_in('케이씨', '[특징주] 케이씨 강세', self.P))
        self.assertTrue(T.name_in('케이씨', '반도체 장비주 케이씨', self.P))

    def test_longer_name_is_not_a_match(self):
        # 케이씨 ≠ 케이씨텍 · 케이씨씨. 이름 다음 글자가 한글이면 다른 이름이다.
        self.assertFalse(T.name_in('케이씨', '케이씨텍, 반도체 장비 수주', self.P))
        self.assertFalse(T.name_in('케이씨', '케이씨씨 건자재 호조', self.P))
        self.assertFalse(T.name_in('케이씨', '케이씨2 출시', self.P))
        self.assertFalse(T.name_in('케이씨', 'KC케이씨A', self.P))

    def test_particle_is_allowed_but_only_as_a_particle(self):
        self.assertTrue(T.name_in('케이씨', '케이씨가 신고가를 썼다', self.P))
        self.assertTrue(T.name_in('케이씨', '케이씨는 상승', self.P))
        # 조사 뒤가 또 어절 글자면 다른 이름이다 — 케이씨에스
        self.assertFalse(T.name_in('케이씨', '케이씨에스 실적 발표', self.P))
        self.assertFalse(T.name_in('케이씨', '케이씨이엔씨 상승', self.P))

    def test_preceding_word_char_breaks_the_match(self):
        self.assertFalse(T.name_in('케이씨', '삼성케이씨 발표', self.P))

    def test_case_insensitive_for_latin_names(self):
        self.assertTrue(T.name_in('S-Oil', 'S-OIL, 정제마진 개선', self.P))

    def test_empty_inputs(self):
        self.assertFalse(T.name_in('케이씨', '', self.P))
        self.assertFalse(T.name_in('', '케이씨', self.P))


class Adopt(unittest.TestCase):
    LONGER = T.longer_names('케이씨', ['케이씨', '케이씨텍', '케이씨씨', '비엠티'])

    def test_longer_universe_name_in_title_excludes_the_article(self):
        self.assertEqual(self.LONGER, ['케이씨텍', '케이씨씨'])
        by, why = T.adopt('케이씨', art('케이씨텍·케이씨 동반 강세', summary='주가'),
                          S, True, self.LONGER)
        self.assertIsNone(by)
        self.assertEqual(why, 'longer_name_in_title')

    def test_generic_name_needs_stock_word_or_finance_outlet(self):
        by, why = T.adopt('케이씨', art('케이씨 창립 기념식'), S, True, self.LONGER)
        self.assertIsNone(by)
        self.assertEqual(why, 'generic_without_stock_context')
        by, _ = T.adopt('케이씨', art('케이씨 창립 기념식', summary='주가는 강세'),
                        S, True, self.LONGER)
        self.assertEqual(by, 'title+stockword')
        by, _ = T.adopt('케이씨', art('케이씨 창립 기념식', outlet='mk.co.kr'),
                        S, True, self.LONGER)
        self.assertEqual(by, 'title+finance_outlet')

    def test_short_names_are_generic_even_if_not_listed(self):
        self.assertTrue(T.is_generic('비엠티', S))     # 3자
        self.assertTrue(T.is_generic('대상', S))
        self.assertFalse(T.is_generic('가온전선', S))

    def test_ordinary_name_needs_only_the_title(self):
        by, _ = T.adopt('가온전선', art('가온전선, 초고압 케이블 공급'), S, False, [])
        self.assertEqual(by, 'title')

    def test_pref_alias_matches_an_article_about_the_pref_itself(self):
        # 보통주 이름으로는 다음 글자 '우' 가 어절 글자라 안 걸린다. 자기 이름부터 본다.
        by, why = T.adopt('S-Oil', art('S-Oil우, 52주 신고가 경신'), S, False, [])
        self.assertEqual((by, why), (None, 'name_not_in_title'))
        by, _ = T.adopt('S-Oil', art('S-Oil우, 52주 신고가 경신'), S, False, [], alias='S-Oil우')
        self.assertEqual(by, 'pref_self')
        by, _ = T.adopt('S-Oil', art('S-Oil, 정제마진 반등'), S, False, [], alias='S-Oil우')
        self.assertEqual(by, 'title')

    def test_name_missing_from_title_is_not_adopted(self):
        by, why = T.adopt('가온전선', art('전선주 일제히 강세', summary='가온전선 포함'),
                          S, False, [])
        self.assertIsNone(by)
        self.assertEqual(why, 'name_not_in_title')

    def test_autogen_quote_articles_are_dropped(self):
        # '{이름} 주가' 질의가 맨 위로 끌어오는 자동 생성 시세 기사.
        by, why = T.adopt('가온전선', art('가온전선 주가, 9월 21일 장중 108,300원 4.44% 상승',
                                     summary='주가'), S, False, [])
        self.assertIsNone(by)
        self.assertEqual(why, 'autogen_title')
        by, why = T.adopt('가온전선', art('가온전선 강세', outlet='topstarnews.net',
                                     summary='주가'), S, False, [])
        self.assertEqual(why, 'autogen_domain')

    def test_publisher_is_display_name_or_domain(self):
        self.assertEqual(T.publisher_of('mk.co.kr', S), '매일경제')
        self.assertEqual(T.publisher_of('yna.co.kr', S), '연합뉴스')
        self.assertEqual(T.publisher_of('unknown.example', S), 'unknown.example')
        self.assertIsNone(T.publisher_of('', S))


class Collect(unittest.TestCase):
    """소스를 전부 가짜로 바꾸고 수집 흐름을 본다."""

    def setUp(self):
        self.naver = {}        # 질의 → 기사 목록 (또는 예외)
        self.gnews = {}
        self.dart = {}
        self.calls = dict(naver=[], gnews=[], dart=[])
        self.tick = 0.0

        def naver_search(q, display=None, s=None, timeout=None, retries=None):
            self.calls['naver'].append(q)
            r = self.naver.get(q, [])
            if isinstance(r, Exception):
                raise r
            return list(r)

        def gnews_search(q, s=None, timeout=None, retries=None):
            self.calls['gnews'].append(q)
            r = self.gnews.get(q, [])
            if isinstance(r, Exception):
                raise r
            return list(r)

        def dart_for(code, asof, s=None, timeout=None, retries=None):
            self.calls['dart'].append(code)
            r = self.dart.get(code, [])
            if isinstance(r, Exception):
                raise r
            return list(r)

        self.patches = [
            mock.patch.object(T.creds, 'has', return_value=True),
            mock.patch.object(T.news, '_s', return_value=object()),
            mock.patch.object(T.news, 'search', side_effect=naver_search),
            mock.patch.object(T.gnews, 'search', side_effect=gnews_search),
            mock.patch.object(T.gnews, 'session', return_value=None),
            mock.patch.object(T.dart, 'corp_codes', return_value={'029460': '1'}),
            mock.patch.object(T.dart, 'session', return_value=None),
            mock.patch.object(T.dart, 'disclosures_for', side_effect=dart_for),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def run_collect(self, inbox=None, cfg=None, clock=None, newhigh=None, universe=None):
        return T.collect(newhigh or NEWHIGH, universe or UNIVERSE, ASOF, cfg or CFG,
                         log=lambda *a: None, inbox=inbox or dict(items=[]), now=NOW,
                         clock=clock)

    def test_targets_are_52w_and_above_only(self):
        out = self.run_collect()
        self.assertEqual(out['n_target'], 4)
        self.assertNotIn('999999', out['by_code'])
        self.assertEqual(out['kind'], 'w52')
        self.assertEqual(out['as_of'], ASOF)
        self.assertTrue(out['collected_at'].startswith('2026-09-21T17:05'))

    def test_queries_use_name_and_code_not_price(self):
        self.run_collect()
        self.assertIn('케이씨 029460', self.calls['naver'])
        self.assertIn('케이씨 신고가', self.calls['naver'])
        self.assertFalse(any('주가' in q for q in self.calls['naver']))

    def test_pref_share_is_queried_as_common(self):
        self.naver['S-Oil 010950'] = [art('S-Oil, 정제마진 반등')]
        out = self.run_collect()
        e = out['by_code']['010955']
        self.assertEqual(e['name'], 'S-Oil우')
        self.assertEqual(e['query_as'], dict(name='S-Oil', code='010950'))
        self.assertEqual(e['items'][0]['matched_by'], 'pref_common')
        self.assertIn('S-Oil 010950', self.calls['naver'])
        self.assertNotIn('S-Oil우 010955', self.calls['naver'])

    def test_pref_share_without_common_is_skipped_and_said(self):
        uni = dict(stocks=[s for s in UNIVERSE['stocks'] if s['name'] != 'S-Oil'])
        out = self.run_collect(universe=uni)
        e = out['by_code']['010955']
        self.assertEqual(e['items'], [])
        self.assertIn('우선주', e['skipped'])
        self.assertEqual(out['sources']['naver_news']['skipped'], 1)
        self.assertNotIn('S-Oil우 010955', self.calls['naver'])

    def test_second_and_third_pref_series_find_their_common(self):
        # '현대차2우B' 의 보통주는 '현대차2' 가 아니라 '현대차' 다 (engine/kinds 와 같은 접미).
        nh = dict(as_of=ASOF, achieved=[row('005387', '현대차2우B', close='w52', high='w52'),
                                        row('001529', '동양3우B', close='w52', high='w52')])
        uni = dict(stocks=[dict(code='005380', name='현대차'), dict(code='001520', name='동양')])
        # '현대차' 는 3자라 일반명사 규칙을 탄다 — 금융지 매체로 채택 조건을 채운다
        self.naver['현대차 005380'] = [art('현대차, 미국 관세 협상 타결', outlet='mk.co.kr')]
        out = self.run_collect(newhigh=nh, universe=uni)
        e = out['by_code']['005387']
        self.assertNotIn('skipped', e)
        self.assertEqual(e['query_as'], dict(name='현대차', code='005380'))
        self.assertEqual(e['items'][0]['matched_by'], 'pref_common')
        self.assertEqual(out['by_code']['001529']['query_as'], dict(name='동양', code='001520'))

    def test_name_ending_in_woo_with_common_code_is_not_a_pref(self):
        # 코드 끝자리가 0 이면 이름이 '우' 로 끝나도 보통주다 — 제 이름으로 묻는다.
        nh = dict(as_of=ASOF, achieved=[row('123450', '가나다우', close='w52', high='w52')])
        uni = dict(stocks=[dict(code='123450', name='가나다우'), dict(code='123400', name='가나다')])
        out = self.run_collect(newhigh=nh, universe=uni)
        e = out['by_code']['123450']
        self.assertNotIn('query_as', e)
        self.assertIn('가나다우 123450', self.calls['naver'])

    def test_article_about_the_pref_itself_is_adopted_as_pref_self(self):
        # 우선주가 신고가를 낸 날 정작 그 종목 기사만 빠지면 안 된다. 자기 이름은 접두
        # 제외 목록('더 긴 이름')에서도 빠진다.
        self.naver['S-Oil 010950'] = [art('S-Oil우, 사상 최고가 경신'), art('S-Oil, 정제마진 반등')]
        out = self.run_collect()
        items = out['by_code']['010955']['items']
        self.assertEqual([(i['title'], i['matched_by']) for i in items],
                         [('S-Oil우, 사상 최고가 경신', 'pref_self'),
                          ('S-Oil, 정제마진 반등', 'pref_common')])

    def test_only_same_day_articles_are_kept(self):
        self.naver['가온전선 000500'] = [art('가온전선 어제 기사', date='2026-09-18'),
                                     art('가온전선 오늘 기사')]
        self.gnews['가온전선'] = [art('가온전선 구글 어제', date='2026-09-20')]
        out = self.run_collect()
        titles = [i['title'] for i in out['by_code']['000500']['items']]
        self.assertEqual(titles, ['가온전선 오늘 기사'])

    def test_duplicates_are_removed_by_normalised_title_and_dart_comes_first(self):
        self.dart['029460'] = [dict(title='단일판매ㆍ공급계약체결', link='https://dart/1',
                                    date=ASOF, publisher='DART', kind='contract')]
        self.naver['케이씨 029460'] = [art('케이씨, 반도체 장비 수주 공시', outlet='mk.co.kr')]
        self.naver['케이씨 신고가'] = [art('케이씨, 반도체 장비 수주 공시', outlet='yna.co.kr')]
        self.gnews['케이씨'] = [art('케이씨 반도체 장비 수주 공시!', outlet='연합뉴스')]
        out = self.run_collect()
        items = out['by_code']['029460']['items']
        self.assertEqual([i['source'] for i in items], ['dart', 'naver_news'])
        self.assertEqual(items[0]['kind'], 'contract')
        self.assertEqual(items[0]['publisher'], 'DART')
        self.assertEqual(items[0]['matched_by'], 'corp_code')
        self.assertEqual(items[1]['publisher'], '매일경제')
        self.assertEqual(items[1]['query'], '케이씨 029460')

    def test_per_stock_cap_stops_further_sources(self):
        self.dart['029460'] = [dict(title=f'공시 {i}', link=f'https://dart/{i}', date=ASOF,
                                    publisher='DART', kind='other') for i in range(5)]
        out = self.run_collect()
        self.assertEqual(len(out['by_code']['029460']['items']), 3)
        self.assertNotIn('케이씨 029460', self.calls['naver'], '상한을 채우면 뒤 소스는 안 부른다')

    def test_dart_items_from_other_days_are_dropped(self):
        self.dart['029460'] = [dict(title='지난주 공시', link='https://dart/1',
                                    date='2026-09-18', publisher='DART', kind='other')]
        out = self.run_collect()
        self.assertEqual(out['by_code']['029460']['items'], [])

    def test_empty_items_are_a_fact_not_a_gap(self):
        out = self.run_collect()
        for e in out['by_code'].values():
            self.assertEqual(e['items'], [])
        self.assertFalse(any('케이씨' in m for m in out['missing']))
        self.assertIn('상위 20건', out['sources']['naver_news']['scope'])
        self.assertIn(ASOF, out['sources']['dart']['scope'])

    # ── 소스 컷 ──
    def test_429_cuts_the_source_for_the_remaining_stocks(self):
        self.naver['케이씨 029460'] = Fetch('https://openapi.naver.com 실패: HTTP 429 · 한도')
        out = self.run_collect()
        self.assertIn('HTTP 429', out['sources']['naver_news']['cut'])
        self.assertEqual(self.calls['naver'], ['케이씨 029460'], '접은 뒤에는 부르지 않는다')
        self.assertTrue(any('네이버 종목 뉴스 — HTTP 429' in m for m in out['missing']))
        # 다른 소스는 계속 돈다
        self.assertEqual(len(self.calls['gnews']), 4)

    def test_connect_failure_cuts_the_source(self):
        self.gnews['케이씨'] = Fetch('https://news.google.com/rss/search 실패: ConnectTimeout: '
                                  "HTTPSConnectionPool(host='news.google.com') timed out")
        out = self.run_collect()
        self.assertIn('접속 불가', out['sources']['google_news']['cut'])
        self.assertEqual(self.calls['gnews'], ['케이씨'])

    def test_a_single_stock_failure_does_not_cut_the_source(self):
        self.naver['케이씨 029460'] = Fetch('https://openapi.naver.com 실패: HTTP 500 · 본문 없음')
        out = self.run_collect()
        self.assertIsNone(out['sources']['naver_news']['cut'])
        self.assertEqual(out['sources']['naver_news']['failed'], 1)
        self.assertIn('HTTP 500', out['by_code']['029460']['errors']['naver_news'])
        self.assertGreater(len(self.calls['naver']), 1, '다음 종목은 계속 부른다')
        # 접지 않은 실패도 결손이다 — 카운트에만 두면 소비자는 모른다 (2장 6번)
        line = next(m for m in out['missing'] if m.startswith('네이버 종목 뉴스 — '))
        self.assertIn('1/4종목 조회 실패', line)
        self.assertIn('첫 사유: ', line)
        self.assertIn('HTTP 500', line)
        self.assertIn('HTTP 500', out['sources']['naver_news']['error'])

    def test_every_stock_failing_is_a_missing_line_not_silence(self):
        # 전 종목이 5xx 로 실패한 날. 예전에는 missing 이 비어 '그날 기사 없음' 으로 읽혔다.
        for q in NAVER_ALL:
            self.naver[q] = Fetch('https://openapi.naver.com 실패: HTTP 502 · Bad Gateway')
        out = self.run_collect()
        src = out['sources']['naver_news']
        self.assertEqual((src['ok'], src['failed'], src['cut']), (0, 4, None))
        self.assertTrue(any('네이버 종목 뉴스 — 4/4종목 조회 실패' in m for m in out['missing']),
                        out['missing'])
        self.assertFalse(any('Google News' in m for m in out['missing']), '성공한 소스는 결손이 아니다')

    def test_dart_key_or_quota_status_cuts_the_source(self):
        # status=020(일 한도)·010(키 무효)은 종목이 아니라 키의 문제다. 첫 종목에서 접고
        # 결손에 적는다 — 예전에는 종목마다 실패로만 쌓여 '그날 공시 없음' 처럼 보였다.
        for st, ko in (('020', '요청 제한 초과'), ('010', '등록되지 않은 인증키')):
            self.setUp()
            self.dart['029460'] = Fetch(f'DART status={st} {ko}')
            out = self.run_collect()
            self.assertIn(f'DART status={st}', out['sources']['dart']['cut'])
            self.assertEqual(self.calls['dart'], ['029460'], '접은 뒤에는 부르지 않는다')
            self.assertTrue(any(m.startswith(f'DART 종목 공시 — DART status={st}')
                                for m in out['missing']), out['missing'])
            self.assertFalse(any('접수분까지' in m for m in out['missing']),
                             'DART 를 부르지 못한 날에 접수 시각 한계를 적지 않는다')
            self.tearDown()
        self.setUp()

    def test_dart_no_result_status_is_that_stocks_business(self):
        self.dart['029460'] = Fetch('DART status=013 조회된 데이터가 없음')
        out = self.run_collect()
        self.assertIsNone(out['sources']['dart']['cut'])
        self.assertEqual(out['sources']['dart']['failed'], 1)

    def test_gnews_consent_page_cuts_the_source(self):
        # 200 + HTML(consent). gnews.parse 가 Fetch 로 올리고, 질의를 바꿔도 같은 페이지다.
        self.gnews['케이씨'] = Fetch('RSS 가 아니다 — consent 페이지 또는 차단 응답으로 본다')
        out = self.run_collect()
        self.assertIn('consent', out['sources']['google_news']['cut'])
        self.assertEqual(self.calls['gnews'], ['케이씨'])
        self.assertTrue(any(m.startswith('Google News RSS — ') for m in out['missing']), out['missing'])

    def test_missing_credentials_cut_before_any_call(self):
        with mock.patch.object(T.creds, 'has', return_value=False):
            out = self.run_collect()
        self.assertIn('없음', out['sources']['naver_news']['cut'])
        self.assertIn('없음', out['sources']['dart']['cut'])
        self.assertEqual(self.calls['naver'], [])
        self.assertEqual(self.calls['dart'], [])
        self.assertEqual(len(self.calls['gnews']), 4, '키가 필요 없는 소스는 돈다')

    def test_cut_reason_classification(self):
        self.assertIn('접속 불가', T.cut_reason(Fetch('x 실패: ConnectTimeout: timed out')))
        self.assertIn('HTTP 403', T.cut_reason(Fetch('x 실패: HTTP 403 · 거부')))
        self.assertIn('HTTP 401', T.cut_reason(Fetch('x 실패: HTTP 401 · 키')))
        self.assertIsNone(T.cut_reason(Fetch('x 실패: HTTP 500 · 본문 없음')))
        self.assertIsNone(T.cut_reason(Fetch('DART status=013')))
        self.assertIsNone(T.cut_reason(Fetch('DART status=900 정의되지 않은 오류')))
        for st in ('010', '011', '012', '020', '021', '800', '901'):
            self.assertIn(f'DART status={st}', T.cut_reason(Fetch(f'DART status={st} x')) or '', st)
        self.assertIn('요청 제한', T.cut_reason(Fetch('DART status=020 요청 제한 초과')))
        for msg in ('XML 이 아니다 — <!doctype html>', 'XML 파싱 실패 — mismatched tag',
                    'RSS 가 아니다 — consent 페이지 또는 차단 응답으로 본다'):
            self.assertIn('consent', T.cut_reason(Fetch(msg)) or '', msg)

    # ── 시간 예산 ──
    def _slow(self, sec):
        """소스 호출마다 sec 초가 흐르는 가짜 시계."""
        state = dict(t=0.0)

        def clock():
            return state['t']

        def slow_naver(q, **kw):
            state['t'] += sec
            self.calls['naver'].append(q)
            return []

        def slow_gnews(q, **kw):
            state['t'] += sec
            self.calls['gnews'].append(q)
            return []

        def slow_dart(code, asof, **kw):
            state['t'] += sec
            self.calls['dart'].append(code)
            return []
        return clock, slow_naver, slow_gnews, slow_dart

    def test_deadline_skips_remaining_stocks_and_says_so(self):
        clock, n, g, d = self._slow(100.0)
        with mock.patch.object(T.news, 'search', side_effect=n), \
             mock.patch.object(T.gnews, 'search', side_effect=g), \
             mock.patch.object(T.dart, 'disclosures_for', side_effect=d):
            out = self.run_collect(clock=clock)
        # 첫 종목: dart 100 → naver 100+100 → 300초. 둘째 종목부터 예산 초과.
        first = out['by_code']['029460']
        self.assertNotIn('skipped', first)
        self.assertIn('시간 예산 초과', first.get('note', ''))
        for code in ('086670', '010955', '000500'):
            self.assertEqual(out['by_code'][code]['skipped'], '시간 예산 초과')
        self.assertEqual(out['sources']['naver_news']['skipped'], 3)
        self.assertTrue(any('시간 예산 240초 초과로 3종목' in m for m in out['missing']), out['missing'])

    def test_per_source_cap_cuts_only_that_source(self):
        cfg = dict(triggers=dict(CFG['triggers'], budget_sec=100000,
                                 per_source_sec=dict(naver_news=150, google_news=100000,
                                                     dart=100000, telegram_x=100000)))
        clock, n, g, d = self._slow(100.0)
        with mock.patch.object(T.news, 'search', side_effect=n), \
             mock.patch.object(T.gnews, 'search', side_effect=g), \
             mock.patch.object(T.dart, 'disclosures_for', side_effect=d):
            out = self.run_collect(cfg=cfg, clock=clock)
        # 첫 종목 네이버 질의 2개 = 200초 > 150 → 둘째 종목부터 네이버만 접힌다
        self.assertIn('소스별 시간 상한', out['sources']['naver_news']['cut'])
        self.assertEqual(self.calls['naver'], ['케이씨 029460', '케이씨 신고가'])
        self.assertIsNone(out['sources']['google_news']['cut'])
        self.assertEqual(len(self.calls['gnews']), 4)

    # ── 인박스 ──
    def _inbox(self, *items):
        return dict(source='telegram', updated_at=NOW.isoformat(), items=list(items))

    def _fwd(self, text, hours_ago=1, urls=(), author=None, update_id=1):
        return dict(update_id=update_id, chat_id=1,
                    date=(NOW - timedelta(hours=hours_ago)).isoformat(timespec='seconds'),
                    text=text, urls=list(urls), x_ids=[], author=author, kind='x',
                    text_via='message')

    def test_inbox_matches_by_code_or_bounded_name(self):
        inbox = self._inbox(
            self._fwd('케이씨 관련 공시 떴다 029460', urls=['https://x.com/a/status/1'], author='acct'),
            self._fwd('비엠티 신고가 갱신 https://x.com/b/status/2 대박'),
            self._fwd('케이씨텍은 다른 종목', urls=['https://x.com/c/status/3']),
            self._fwd('가온전선 3일 전 글', hours_ago=30),
        )
        out = self.run_collect(inbox=inbox)
        kc = out['by_code']['029460']['items']
        self.assertEqual(len(kc), 1)
        self.assertEqual(kc[0]['source'], 'telegram_x')
        self.assertEqual(kc[0]['matched_by'], 'code')
        self.assertEqual(kc[0]['publisher'], '@acct')
        self.assertEqual(kc[0]['link'], 'https://x.com/a/status/1')
        bm = out['by_code']['086670']['items']
        self.assertEqual(bm[0]['matched_by'], 'name')
        self.assertEqual(bm[0]['publisher'], 'X(포워딩)')
        self.assertEqual(bm[0]['link'], 'https://x.com/b/status/2', '본문 속 링크도 집는다')
        self.assertEqual(bm[0]['title'], '비엠티 신고가 갱신 대박', '제목에서 URL 은 지운다')
        self.assertEqual(out['by_code']['000500']['items'], [], '24시간 밖은 안 본다')
        self.assertIsNone(out['sources']['telegram_x']['cut'])

    def test_inbox_title_is_first_120_chars(self):
        inbox = self._inbox(self._fwd('가온전선 ' + 'x' * 300))
        out = self.run_collect(inbox=inbox)
        self.assertEqual(len(out['by_code']['000500']['items'][0]['title']), 120)

    def test_generic_name_in_inbox_needs_code_or_stock_word(self):
        inbox = self._inbox(self._fwd('케이씨 라고 적힌 아무 글'),
                            self._fwd('케이씨 신고가!', update_id=2))
        out = self.run_collect(inbox=inbox)
        self.assertEqual([i['title'] for i in out['by_code']['029460']['items']], ['케이씨 신고가!'])

    def test_missing_inbox_file_is_a_gap_line_not_a_crash(self):
        with mock.patch.object(T, 'INBOX_PATH', os.path.join(os.sep, 'nonexistent', 'inbox.json')):
            out = T.collect(NEWHIGH, UNIVERSE, ASOF, CFG, log=lambda *a: None, now=NOW)
        self.assertEqual(out['sources']['telegram_x']['cut'], T.INBOX_ABSENT_LINE)
        self.assertIn(T.INBOX_ABSENT_LINE, out['missing'])
        self.assertEqual(out['sources']['telegram_x']['ok'], 0)

    def test_disclosure_cutoff_time_is_stated_when_dart_ran(self):
        out = self.run_collect()
        self.assertTrue(any(m.startswith('공시는 17:05 접수분까지') for m in out['missing']),
                        out['missing'])

    def test_output_carries_the_inbox_window_and_source_error_slot(self):
        out = self.run_collect()
        self.assertEqual(out['inbox_hours'], 24)
        for k in T.ORDER:
            self.assertIsNone(out['sources'][k]['error'])

    def test_no_targets_returns_empty_without_calls(self):
        out = self.run_collect(newhigh=dict(as_of=ASOF, achieved=[]))
        self.assertEqual(out['n_target'], 0)
        self.assertEqual(out['by_code'], {})
        self.assertEqual(self.calls['naver'], [])


class StageFailure(unittest.TestCase):
    def test_failed_output_marks_every_source_and_one_missing_line(self):
        out = T.failed(ASOF, CFG, RuntimeError('newhigh.json 이 없다'), now=NOW)
        self.assertEqual(out['by_code'], {})
        for k in T.ORDER:
            self.assertIn('단계 실패', out['sources'][k]['cut'])
            self.assertEqual(out['sources'][k]['failed'], 1)
        self.assertEqual(len(out['missing']), 1)
        self.assertTrue(out['missing'][0].startswith('재료: 수집되지 않음(단계 실패'))
        self.assertIn('newhigh.json', out['missing'][0])

    def test_load_without_file_returns_the_absent_standin(self):
        with mock.patch('board.engine.build.read', return_value=None):
            got = T.load(ASOF)
        self.assertTrue(got['absent'])
        self.assertEqual(got['missing'], [T.ABSENT_LINE])
        self.assertEqual(got['by_code'], {})

    def test_load_with_file_returns_it(self):
        with mock.patch('board.engine.build.read', return_value=dict(source='triggers', by_code={'a': 1})):
            got = T.load(ASOF)
        self.assertNotIn('absent', got)
        self.assertEqual(got['by_code'], {'a': 1})


class Settings(unittest.TestCase):
    def test_defaults_fill_missing_keys(self):
        s = T.settings({})
        self.assertEqual(s['per_stock_items'], 3)
        self.assertEqual(s['per_source_sec']['naver_news'], 90)
        self.assertEqual(s['_autogen'], [])

    def test_repo_settings_have_the_block(self):
        from ..engine.config import load
        s = T.settings(load())
        self.assertEqual(s['budget_sec'], 240)
        self.assertEqual(s['per_stock_items'], 3)
        self.assertEqual(s['naver_queries'], ['{name} {code}', '{name} 신고가'])
        self.assertIn('케이씨', s['generic_names'])
        self.assertIn('topstarnews.net', s['domain_blacklist'])
        self.assertEqual(s['finance_outlets']['mk.co.kr'], '매일경제')
        self.assertEqual(s['title_chars_tg'], 60)


if __name__ == '__main__':
    unittest.main()


class ProbeInboxAbsent(unittest.TestCase):
    """인박스 파일이 없는 것은 고장이 아니다 — 아직 아무것도 공유되지 않았을 뿐이다."""

    def test_missing_inbox_is_not_a_fail(self):
        nowhere = os.path.join(os.sep, 'nowhere', 'inbox.json')
        # 라이브 소스는 전부 실패시키고 인박스 줄만 본다. cfg 는 settings 가 기본값으로 채운다.
        with mock.patch.object(T, 'INBOX_PATH', nowhere), \
             mock.patch.object(T.creds, 'has', return_value=False), \
             mock.patch.object(T.gnews, 'search', side_effect=RuntimeError('no net')):
            rows = T.probe(cfg={'triggers': {}})
        row = next(r for r in rows if r[0] == 'X 인박스')
        self.assertTrue(row[1], row)
        self.assertIn('아직 공유된 게시물 없음', row[2])


class MeasuredAutogen(unittest.TestCase):
    """2026-09-22 러너 실측 제목으로 고정한다 (D-085 실측표).

    첫 실행이 신고가 8종목에 재료 15건을 붙였는데 그중 여섯이 '올랐다' 를 다시
    쓴 자동 생성 기사였다. 등락률·거래량·순매수는 보드가 이미 같은 줄에 적는다 —
    그 자리에 같은 수치가 한 번 더 앉으면 읽는 쪽은 트리거가 있다고 읽는다.
    """

    # 실제로 붙었던 제목. 하나라도 통과하면 리포트에 그대로 나간다.
    REJECT = [
        '[장중수급포착] 샘씨엔에스, 외국인/기관 동시 순매수… 주가 +6.12%',
        '[장중수급포착] 마이크로컨텍솔, 기관 5일 연속 순매수행진... 주가 +7.48%',
        '[장중수급포착] 케이씨, 외국인/기관 동시 순매수… 주가 +7.26%',
        '가온전선 주가 8,500원 하락 후 마감',
        '대한전선·가온전선·세방전지 동반 약세…전기·전선 관련주 장중 흐름은',
        '케이씨 주가, 9월 21일 애프터마켓 44,400원 7.51% 상승',
    ]
    # 이유가 붙은 제목은 남는다. 수치가 들었다고 다 자동 생성은 아니다.
    KEEP = [
        '특징주, 피에스케이홀딩스-HBM(고대역폭메모리) 테마 상승세에 6.19% ↑',
        '디지털대성, 자사주 62.8만주 소각…"100억 추가 매입도 시작"',
        '[공시]미리펀드, 지어소프트 지분 13.80%로 확대',
        '케이씨, 반도체 장비 수주로 52주 신고가',
        "S-OIL, 사옥 글판 새 단장…'멈추지 마라' 메시지 담아",
    ]

    def setUp(self):
        from board.engine.config import load
        self.S = T.settings(load())          # 실제 settings.yaml 로 본다

    def _hit(self, title):
        return any(p.search(title) for p in self.S['_autogen'])

    def test_measured_autogen_titles_are_rejected(self):
        for t in self.REJECT:
            self.assertTrue(self._hit(t), f'놓쳤다: {t}')

    def test_titles_with_a_reason_survive(self):
        for t in self.KEEP:
            self.assertFalse(self._hit(t), f'잘못 뺐다: {t}')


class DropTally(unittest.TestCase):
    """버린 기사는 사유별로 센다 — 조용히 버리면 '그날 기사가 없었다' 와 같아진다."""

    def setUp(self):
        Collect.setUp(self)

    def tearDown(self):
        Collect.tearDown(self)

    run_collect = Collect.run_collect

    def test_other_day_articles_are_counted_not_silent(self):
        self.naver['케이씨 029460'] = [art('케이씨, 반도체 수주', date='2026-09-22')]
        out = self.run_collect()
        self.assertEqual(out['sources']['naver_news']['dropped'].get('not_asof'), 1)

    def test_source_that_kept_nothing_says_why(self):
        """받아는 놓고 전부 걸러졌으면 결손에 사유가 남는다."""
        for q in ('케이씨 029460', '케이씨 신고가'):
            self.naver[q] = [art('케이씨, 반도체 수주', date='2026-09-22')]
        out = self.run_collect()
        line = next((m for m in out['missing'] if '네이버' in m), None)
        self.assertIsNotNone(line, out['missing'])
        self.assertIn('다른 날 기사', line)

    def test_adopted_source_says_nothing(self):
        """한 건이라도 남았으면 그 소스 줄은 안 나간다."""
        self.naver['케이씨 029460'] = [art('케이씨, 반도체 장비 수주로 신고가')]
        out = self.run_collect()
        self.assertFalse([m for m in out['missing'] if '전부 걸러져' in m], out['missing'])

    def test_autogen_drop_is_named(self):
        self.naver['케이씨 029460'] = [art('케이씨 주가, 9월 21일 애프터마켓 44,400원 7.51% 상승')]
        out = self.run_collect()
        self.assertEqual(out['sources']['naver_news']['dropped'].get('autogen_title'), 1)


class NaverWindow(unittest.TestCase):
    """기준일이 오늘이 아니면 네이버를 건너뛴다 (최신순·기간 필터 없음).

    창을 100건으로 넓혀도 안 됐다 — 2026-09-22 09:28 KST 실측에서 11종목 × 2질의로
    2,124건을 받아 한 건도 기준일 기사가 아니었다. 큰 응답 스물두 번에 0건이다.
    """

    def setUp(self):
        Collect.setUp(self)

    def tearDown(self):
        Collect.tearDown(self)

    def _display_used(self, now):
        seen = []
        real = T.news.search

        def spy(q, display=None, s=None, timeout=None, retries=None):
            seen.append(display)
            return real(q, display=display, s=s, timeout=timeout, retries=retries)

        with mock.patch.object(T.news, 'search', side_effect=spy):
            T.collect(NEWHIGH, UNIVERSE, ASOF, CFG, log=lambda *a: None,
                      inbox=dict(items=[]), now=now)
        return set(seen)

    def test_same_day_calls_naver(self):
        self.assertEqual(self._display_used(NOW), {20})

    def test_past_asof_does_not_call_naver(self):
        later = datetime(2026, 9, 22, 9, 19, tzinfo=T.KST)
        self.assertEqual(self._display_used(later), set())

    def test_past_asof_says_why(self):
        later = datetime(2026, 9, 22, 9, 19, tzinfo=T.KST)
        out = T.collect(NEWHIGH, UNIVERSE, ASOF, CFG, log=lambda *a: None,
                        inbox=dict(items=[]), now=later)
        cut = out['sources']['naver_news']['cut']
        self.assertIn('오늘이 아니다', cut or '')
        self.assertTrue([m for m in out['missing'] if '네이버' in m], out['missing'])


class InboxDefaultOff(unittest.TestCase):
    """배포 기본값은 인박스 꺼짐이다 (2026-09-22, XDIGEST.md 8장 11번).

    사용자가 손 공유를 하지 않기로 했다. 켜 두면 '인박스 없음' 이 배너에 **매일**
    붙는데, 고칠 수도 없고 고칠 생각도 없는 줄이 매일 붙으면 배너 전체를 안 읽게
    된다. 기능은 그대로 두고 설정으로만 끈다.
    """

    def test_shipped_default_is_off(self):
        from board.engine.config import load
        self.assertFalse(T.settings(load())['inbox_enabled'])

    def test_default_in_code_is_off(self):
        """설정 파일에 키가 없어도 꺼져 있어야 한다."""
        self.assertFalse(T.settings({'triggers': {}})['inbox_enabled'])

    def test_off_means_no_banner_line_and_no_file_read(self):
        read = []
        real = T.load_inbox

        def spy(*a, **k):
            read.append(1)
            return real(*a, **k)

        cfg = dict(triggers=dict(CFG['triggers'], inbox_enabled=False))
        with mock.patch.object(T, 'load_inbox', side_effect=spy):
            Collect.setUp(self)
            try:
                out = T.collect(NEWHIGH, UNIVERSE, ASOF, cfg, log=lambda *a: None,
                                inbox=dict(items=[]), now=NOW)
            finally:
                Collect.tearDown(self)
        self.assertEqual(read, [], '꺼 놨는데 인박스를 읽었다')
        self.assertIsNone(out['sources']['telegram_x']['cut'])
        self.assertEqual(out['sources']['telegram_x']['skipped'], 1)
        self.assertIn('꺼 둠', out['sources']['telegram_x']['scope'])
        self.assertFalse([m for m in out['missing'] if '인박스' in m], out['missing'])

    def test_on_still_reports_the_absent_inbox(self):
        """켜면 예전처럼 사유가 배너에 붙는다 — 기능을 지운 것이 아니다."""
        Collect.setUp(self)
        try:
            out = T.collect(NEWHIGH, UNIVERSE, ASOF, CFG, log=lambda *a: None,
                            inbox=None, now=NOW)
        finally:
            Collect.tearDown(self)
        self.assertEqual(out['sources']['telegram_x']['cut'], T.INBOX_ABSENT_LINE)
        self.assertIn(T.INBOX_ABSENT_LINE, out['missing'])

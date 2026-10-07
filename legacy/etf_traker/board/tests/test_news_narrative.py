"""섹터 뉴스 탭에 레퍼런스 양식의 Claude 서술을 얹는다 (D-062).

수집기(D-044)는 기사를 붙였는데 facts.py 가 news=None 으로 자리만 두어 서술
모델에 한 건도 안 들어갔다. 트리거를 쓸 재료가 없으니 "처음 준 예시대로"
나올 수가 없었다.
"""
import unittest

from ..engine import facts as F
from ..web import render as R


class ThemeNewsPackTest(unittest.TestCase):

    def test_articles_are_folded_without_url(self):
        out = F._theme_news(dict(articles=[
            dict(title='웨스팅하우스 지분 공동인수설', outlet='연합뉴스',
                 date='2026-08-26', summary='…', url='https://x/y', query='원전'),
        ]))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['outlet'], '연합뉴스')   # 단독 보도 표기의 재료
        self.assertNotIn('url', out[0])                  # 주소는 토큰 낭비

    def test_empty_articles_stay_none(self):
        # '기사 없음' 이 빈 리스트로 들어가면 프롬프트의 "news 가 있으면" 분기가
        # 헛돈다. 없으면 None 이어야 트리거를 쓰지 않는다.
        self.assertIsNone(F._theme_news(dict(articles=[])))
        self.assertIsNone(F._theme_news(None))

    def test_build_attaches_news_to_pack(self):
        import inspect
        src = inspect.getsource(F.build)
        self.assertIn("news=_theme_news(news_by_id.get(t['theme']))", src)
        self.assertNotIn('news=None,          # 뉴스 소스 미확보', src)


class NarrativeHtmlTest(unittest.TestCase):

    def test_two_levels_and_bold(self):
        h = R._narr_html('- 첫 줄.\n» 하위 **강조** 줄')
        self.assertIn('class="n1"', h)
        self.assertIn('class="n2"', h)
        self.assertIn('<b>강조</b>', h)

    def test_markup_is_escaped(self):
        h = R._narr_html('- <img src=x onerror=1>')
        self.assertNotIn('<img', h)

    def test_empty_returns_empty(self):
        self.assertEqual(R._narr_html(''), '')
        self.assertEqual(R._narr_html(None), '')

    def test_news_card_carries_the_narrative(self):
        news = dict(themes=[dict(name='원전', theme='nuclear', chg_pct=3.2,
                                 n_newhigh=2, queries=['원전'], n_found=1,
                                 articles=[dict(title='t', url='https://x',
                                                outlet='o', summary='s')])])
        html = R._news(news, '2026-09-01', {'원전': '- 트리거 한 줄.'})
        self.assertIn('트리거 한 줄', html)
        self.assertLess(html.index('트리거 한 줄'), html.index('https://x'),
                        '서술이 먼저, 기사가 아래다')

    def test_news_card_without_narrative_still_renders(self):
        news = dict(themes=[dict(name='원전', theme='nuclear', chg_pct=3.2,
                                 n_newhigh=0, queries=['원전'], n_found=0,
                                 articles=[])])
        html = R._news(news, '2026-09-01', None)
        self.assertIn('원전', html)


class TriggerPromptTest(unittest.TestCase):
    """트리거를 모르면 트리거 문장을 쓰지 않는다 (D-085). 프롬프트가 그렇게 시킨다."""

    def test_style_no_longer_asks_for_the_unconfirmed_sentence(self):
        from ..writer import prompts as P
        self.assertNotIn('확인되지 않음', P.STYLE)
        self.assertIn('트리거 문장을 아예 쓰지 마라', P.STYLE)

    def test_theme_prompt_explains_stock_triggers(self):
        from ..writer import prompts as P
        self.assertIn('`trigger`', P.THEME)
        self.assertIn('공시:', P.THEME)
        self.assertIn('X 포워딩:', P.THEME)
        self.assertIn('다른 종목명은 옮기지 마라', P.THEME)


class TriggerFactsTest(unittest.TestCase):
    """triggers.json → 종목별 trigger. url 은 빼고 name 키는 없다."""

    TJ = dict(by_code={'029460': dict(code='029460', name='케이씨', items=[
        dict(title='케이씨, 반도체 장비 수주', link='https://mk/1', publisher='매일경제',
             published_at='2026-09-21T09:10+09:00', source='naver_news', matched_by='title',
             query='케이씨 029460'),
        dict(title='단일판매ㆍ공급계약체결', link='https://dart/1', publisher='DART',
             published_at='2026-09-21', source='dart', kind='contract', matched_by='corp_code'),
    ]), '086670': dict(code='086670', name='비엠티', items=[])})

    def test_folded_items_have_no_url_and_no_name(self):
        out = F._trigger_facts(self.TJ)
        self.assertEqual(list(out), ['029460'], '재료 없는 종목은 키 자체가 없다')
        a, b = out['029460']
        self.assertEqual(a, dict(title='케이씨, 반도체 장비 수주', publisher='매일경제',
                                 date='2026-09-21', source='naver_news'))
        self.assertEqual(b['kind'], 'contract')
        self.assertEqual(b['publisher'], 'DART')
        for it in (a, b):
            self.assertNotIn('link', it)
            self.assertNotIn('name', it)

    def test_stock_carries_its_trigger(self):
        trig = F._trigger_facts(self.TJ)
        x = dict(name='케이씨', code='029460', chg_pct=5.43)
        d = F._stock(x, {}, {}, {}, trig)
        self.assertEqual(len(d['trigger']), 2)
        y = F._stock(dict(name='비엠티', code='086670', chg_pct=1.0), {}, {}, {}, trig)
        self.assertNotIn('trigger', y)

    def test_absent_file_folds_to_nothing(self):
        from ..ingest import triggers as TR
        self.assertEqual(F._trigger_facts(TR.absent('2026-09-21')), {})
        self.assertEqual(F._trigger_facts(None), {})


if __name__ == '__main__':
    unittest.main()

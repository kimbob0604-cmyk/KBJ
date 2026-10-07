#!/usr/bin/env python3
"""후처리 검증 단위 시험 — CLAUDE.md 2장 4번이 걸린 곳이라 여기가 제일 중요하다."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.writer import verify as V          # noqa: E402

PACK = {
    'themes': [{
        'name': '원전',
        'chg': '+8.42%',
        'stocks': [
            {'name': '한전기술', 'chg': '+13.63%', 'turnover': '3,240억',
             'vol_mult': '8.2배', 'cum_2d': '+36.00%'},
            {'name': '한전산업', 'chg': '+12.74%', 'vol_mult': '46.6배'},
        ],
        'detected': [{'type': 'material_giveback', 'stocks': ['금화피에스시'],
                      'fact': '장중 고가 대비 종가 괴리 11.2%p · 종가 등락률 +1.45%'}],
    }],
    'market': {'indices': {'코스피': {'close': '6,808.21', 'chg': '+0.97%'}}},
}
UNIVERSE = ['한전기술', '한전산업', '금화피에스시', '두산에너빌리티',
            '성광벤드', '비에이치아이', '삼성전자', '한미글로벌',
            '카카오', '카카오페이']


class TestNames(unittest.TestCase):
    def test_clean_draft_passes(self):
        d = '- 한전기술(+13.63%) 거래대금 3,240억. 한전산업(+12.74%) 거래량 46.6배.'
        ok, v = V.check(d, PACK, UNIVERSE)
        self.assertTrue(ok, V.report(v))

    def test_hallucinated_stock_is_caught(self):
        # 두산에너빌리티는 실재 종목이지만 이 사실 팩에는 없다
        d = '- 한전기술(+13.63%)과 두산에너빌리티가 함께 올랐음.'
        ok, v = V.check(d, PACK, UNIVERSE)
        self.assertFalse(ok)
        self.assertTrue(any(x['kind'] == 'name' and x['value'] == '두산에너빌리티'
                            for x in v))

    def test_stock_in_detected_block_is_allowed(self):
        d = '» 금화피에스시는 장중 고가 대비 종가 괴리 11.2%p 로 재료 반납.'
        ok, v = V.check(d, PACK, UNIVERSE)
        self.assertTrue(ok, V.report(v))

    def test_short_names_are_not_matched(self):
        # 두 글자 이하는 일반 단어와 충돌해 검사하지 않는다
        ok, v = V.check('- 대상 종목 없음.', PACK, ['대상'])
        self.assertTrue(ok)


class TestNumbers(unittest.TestCase):
    def test_rounded_number_is_caught(self):
        d = '- 한전기술(약 14%) 상승.'
        ok, v = V.check(d, PACK, UNIVERSE)
        self.assertFalse(ok)
        self.assertTrue(any(x['kind'] == 'number' for x in v))

    def test_invented_number_is_caught(self):
        d = '- 한전기술(+13.63%) 거래대금 9,999억.'
        ok, v = V.check(d, PACK, UNIVERSE)
        self.assertFalse(ok)
        self.assertTrue(any(x['value'].strip() == '9,999억' for x in v))

    def test_index_value_allowed(self):
        d = '- 코스피 6,808.21(+0.97%)로 마감.'
        ok, v = V.check(d, PACK, UNIVERSE)
        self.assertTrue(ok, V.report(v))

    def test_percent_point_unit(self):
        d = '» 고가 대비 종가 괴리 11.2%p.'
        ok, v = V.check(d, PACK, UNIVERSE)
        self.assertTrue(ok, V.report(v))

    def test_whitespace_variant_allowed(self):
        d = '- 한전기술(+13.63 %) 상승.'
        ok, v = V.check(d, PACK, UNIVERSE)
        self.assertTrue(ok, V.report(v))

    def test_cumulative_return_allowed(self):
        d = '- 한전기술(+13.63%, 이틀간 +36.00%).'
        ok, v = V.check(d, PACK, UNIVERSE)
        self.assertTrue(ok, V.report(v))


class TestBypasses(unittest.TestCase):
    """감사에서 나온 우회 세 가지. 전부 통과하고 있었다."""

    def test_value_attributed_to_wrong_stock(self):
        # 팩에 있는 수치이긴 한데 다른 종목의 것이다
        ok, v = V.check('- 한전산업(+13.63%) 상승.', PACK, UNIVERSE)
        self.assertFalse(ok)
        self.assertTrue(any(x['kind'] == 'pair' for x in v), v)

    def test_invented_stock_name(self):
        # 유니버스에도 없는 이름이라 예전 방식으로는 절대 안 걸렸다
        ok, v = V.check('- 삼성반도체소재(+13.63%) 급등.', PACK, UNIVERSE)
        self.assertFalse(ok)
        self.assertTrue(any(x['value'] == '삼성반도체소재' for x in v), v)

    def test_bare_number_is_checked(self):
        # 단위 없는 지수 레벨. 예전에는 허용 집합을 만드는 데만 쓰였다
        ok, v = V.check('- 코스피 9,999.99 로 마감.', PACK, UNIVERSE)
        self.assertFalse(ok)
        self.assertTrue(any(x['kind'] == 'number' for x in v), v)

    def test_correct_index_level_passes(self):
        ok, v = V.check('- 코스피 6,808.21(+0.97%)로 마감.', PACK, UNIVERSE)
        self.assertTrue(ok, V.report(v))

    def test_substring_name_is_not_a_false_positive(self):
        """팩에 '카카오페이' 가 있고 유니버스에 '카카오' 가 있을 때."""
        pack = dict(PACK)
        pack['themes'] = [dict(PACK['themes'][0])]
        pack['themes'][0]['stocks'] = [{'name': '카카오페이', 'chg': '+3.50%'}]
        ok, v = V.check('- 카카오페이(+3.50%) 상승함.', pack, UNIVERSE)
        self.assertTrue(ok, V.report(v))

    def test_pair_with_correct_value_passes(self):
        ok, v = V.check('- 한전기술(+13.63%) 거래대금 3,240억.', PACK, UNIVERSE)
        self.assertTrue(ok, V.report(v))


class TestCollect(unittest.TestCase):
    def test_allowed_numbers_includes_nested(self):
        n = V.allowed_numbers(PACK)
        for x in ('+13.63%', '3,240억', '46.6배', '11.2%p', '+0.97%', '6,808.21'):
            self.assertIn(V.norm(x), n, x)

    def test_allowed_names_includes_detected_stocks(self):
        n = V.allowed_names(PACK)
        self.assertIn(V.norm('금화피에스시'), n)
        self.assertIn(V.norm('한전기술'), n)
        self.assertNotIn(V.norm('두산에너빌리티'), n)


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestJargon(unittest.TestCase):
    """사실 팩의 키가 본문에 새는 것을 잡는다 (2026-08-28 초안 실측)."""

    def _v(self, draft):
        ok, v = V.check(draft, {}, set(), strict_numbers=False)
        return [x for x in v if x['kind'] == 'jargon']

    def test_필드명이_그대로_나오면_잡는다(self):
        v = self._v('stages_new_today 기준 브랜드 단계로 자금 유입')
        self.assertEqual([x['value'] for x in v], ['stages_new_today'])

    def test_우리말로_풀면_통과(self):
        self.assertEqual(self._v('브랜드 단계가 오늘 새로 반응'), [])

    def test_코드블록_안은_예외(self):
        self.assertEqual(self._v('키는 `vol_mult` 다'), [])

    def test_업계_약어는_안_잡는다(self):
        self.assertEqual(self._v('ODM·CDMO·SMR 반응'), [])

    def test_여러_개면_한_번씩만(self):
        v = self._v('vol_mult 와 vol_mult 그리고 near_gap')
        self.assertEqual(sorted(x['value'] for x in v), ['near_gap', 'vol_mult'])


class TestProseFalsePositives(unittest.TestCase):
    """산문과 어휘를 조작으로 잡으면 멀쩡한 섹션이 통째로 빠진다.

    2026-09-01 실측 — 장 흐름 섹션이 '마감(+0.97%)' 의 '마감'(2글자 산문)과
    '52주 신고가' 의 '52주'(라벨 어휘) 때문에 3회 재시도 끝에 매번 빠졌다.
    """

    def _pack(self):
        return dict(counts={'역사적': 3, '52주': 13, '60일': 81},
                    market=dict(name='코스피', chg='+0.97%', close='6,808.21'))

    def test_two_char_prose_before_paren_is_not_a_name(self):
        ok, v = V.check('- 코스피 6,808.21로 마감(+0.97%)', self._pack(), [])
        self.assertTrue(ok, v)

    def test_two_char_listed_name_is_still_checked(self):
        # '기아' 는 상장명이다. 산문 예외가 상장 두 글자 이름까지 덮으면 안 된다.
        ok, v = V.check('- 기아(+55.00%) 급등', self._pack(), ['기아'])
        self.assertFalse(ok)
        self.assertEqual(v[0]['kind'], 'name')

    def test_label_vocab_is_not_an_invented_number(self):
        ok, v = V.check('- 52주 신고가 13종목', self._pack(), [])
        self.assertTrue(ok, v)

    def test_other_durations_stay_flagged(self):
        # '3주 연속' 은 라벨이 아니라 주장이다. 팩에 없으면 잡아야 한다.
        ok, v = V.check('- 3주 연속 상승', self._pack(), [])
        self.assertFalse(ok)
        self.assertEqual(v[0]['kind'], 'number')


class TestFieldLeakBoundary(unittest.TestCase):
    """`\\w` 는 유니코드라 조사가 붙은 키를 못 잡았다 (2026-09-21 초안 실측)."""

    def _v(self, draft):
        ok, v = V.check(draft, {}, set(), strict_numbers=False)
        return [x['value'] for x in v if x['kind'] == 'jargon']

    def test_조사가_붙은_키도_잡는다(self):
        self.assertEqual(self._v('stages_new_today가 비어있어 추가로 확산된 단계는 없음'),
                         ['stages_new_today'])
        self.assertEqual(self._v('vol_mult는 2.1배'), ['vol_mult'])

    def test_영문_경계는_그대로다(self):
        self.assertEqual(self._v('Xstages_new_today 처럼 영문에 붙은 것은 키가 아니다'), [])
        self.assertEqual(self._v('키는 `vol_mult` 다'), [])


class TestPhrases(unittest.TestCase):
    """트리거를 모른다고 적는 문장 — 2026-09-21 초안(docs/api/latest.json) 실측 6문장."""

    DRAFT = [
        '디스플레이(+5.04%) 거래대금 2,283억, 트리거는 확인되지 않음.',
        '개별 뉴스로는 이수페타시스의 AI 데이터센터용 고다층 PCB 수요 확대·2분기 실적 '
        '기사가 확인되나 테마 전체 상승 트리거로 단정하긴 어려움.',
        '태양광 테마 전체 -3.18%, 거래대금 1,283억, 신고가 종목 없음. 트리거는 확인되지 않음.',
        '수소 테마 전 종목 하락, 트리거는 확인되지 않음. 거래대금 478억, 신고가 종목 0개.',
        '반도체 전공정 테마 +3.44%, 거래대금 11.52조. 트리거는 확인되지 않음.',
        '팹리스·설계 테마 +3.62%, 거래대금 762억, 트리거는 확인되지 않음. 신고가 종목 없음.',
    ]

    def _phrase(self, draft):
        ok, v = V.check(draft, {}, [], strict_numbers=False)
        return [x for x in v if x['kind'] == 'phrase']

    def test_every_draft_sentence_is_caught(self):
        for s in self.DRAFT:
            self.assertTrue(self._phrase('- ' + s), s)

    def test_other_wordings_are_caught_too(self):
        for s in ('트리거 미확인.', '재료 부재.', '뉴스 없음.', '원인은 알 수 없음.',
                  '호재가 없어 등락률만 적음.', '재료는 확인 안 됨.', '촉매 불명.'):
            self.assertTrue(self._phrase('- ' + s), s)

    def test_real_trigger_sentences_pass(self):
        for s in ('트리거는 DART 공시 — 단일판매공급계약.',
                  '재료 반납 — 장중 고가 대비 종가 괴리 3.9%p.',
                  '연합뉴스 보도가 트리거.',
                  '뉴스는 매일경제 — 반도체 장비 수주.',
                  '거래대금 762억, 신고가 종목 없음.',
                  # STYLE 규칙 5 — 단독 보도·미확인 루머면 그 사실을 함께 적는다. 이 문장은
                  # 트리거를 모른다는 말이 아니라 트리거가 무엇인지 적은 것이다.
                  '케이씨(+12.00%)의 트리거는 미확인 루머 — X 포워딩: 반도체 장비 대형 수주설.',
                  '한전기술(+13.63%)은 연합뉴스 단독 보도, 재료는 확인되지 않은 지분 인수설.',
                  '재료는 미확인 루머 — X 포워딩의 지분 인수설.',
                  '재료는 미확인 루머임.',
                  '트리거는 미확인 보도(연합뉴스 단독).'):
            self.assertEqual(self._phrase('- ' + s), [], s)

    def test_predicate_forms_are_still_caught_next_to_the_exception(self):
        # 술어형·'상태' 류는 여전히 모른다는 말이다. 예외는 루머·설·보도가 이어질 때뿐.
        for s in ('트리거는 확인되지 않은 상태로 마감.', '재료 미확인 상태.',
                  '트리거는 확인되지 않음, 거래량 5배임.', '뉴스 없음, 미확인 보도.',
                  '재료는 미확인.'):
            self.assertTrue(self._phrase('- ' + s), s)

    def test_violation_is_the_sentence(self):
        v = self._phrase('- 수소 테마 전 종목 하락, 트리거는 확인되지 않음. 거래대금 478억.')
        self.assertEqual(len(v), 1)
        self.assertTrue(v[0]['value'].startswith('수소 테마 전 종목 하락'))

    def test_strip_removes_only_the_offending_sentence(self):
        txt, n = V.strip_phrases('- 수소 테마 전 종목 하락, 트리거는 확인되지 않음. '
                                 '거래대금 478억, 신고가 종목 0개.\n» 상아프론테크 거래량 2.1배.')
        self.assertEqual(n, 1)
        self.assertEqual(txt, '- 거래대금 478억, 신고가 종목 0개.\n» 상아프론테크 거래량 2.1배.')

    def test_strip_drops_the_bullet_when_nothing_is_left(self):
        txt, n = V.strip_phrases('- 첫 줄 사실.\n- 디스플레이(+5.04%) 거래대금, 트리거는 확인되지 않음.\n- 셋째.')
        self.assertEqual(n, 1)
        self.assertEqual(txt, '- 첫 줄 사실.\n- 셋째.')

    def test_strip_leaves_clean_text_alone(self):
        src = '- 코스피 6,938.34로 시작해 7,007.72로 마감(+1.65%). 2일 연속 상승.'
        self.assertEqual(V.strip_phrases(src), (src, 0))


class TestMentionedNames(unittest.TestCase):
    """기사·재료 제목 속 다른 종목명은 검사 3 에서만 관용한다."""

    PACK = {'themes': [{'name': '반도체', 'stocks': [
        {'name': '케이씨', 'chg': '+5.43%',
         'trigger': [{'title': '케이씨텍·케이씨, 반도체 장비 수주 5.98% 상승',
                      'publisher': '매일경제', 'date': '2026-09-21', 'source': 'naver_news'}]},
    ]}]}
    UNI = ['케이씨', '케이씨텍', '삼성전자']

    def test_name_in_trigger_title_is_tolerated_in_prose(self):
        ok, v = V.check('- 케이씨(+5.43%)는 매일경제 보도 — 케이씨텍과 함께 장비 수주.', self.PACK, self.UNI)
        self.assertTrue(ok, V.report(v))

    def test_name_outside_any_title_is_still_caught(self):
        ok, v = V.check('- 케이씨(+5.43%)와 삼성전자 동반 상승.', self.PACK, self.UNI)
        self.assertFalse(ok)
        self.assertEqual([x['value'] for x in v if x['kind'] == 'name'], ['삼성전자'])

    def test_pairing_a_value_to_the_mentioned_name_is_still_caught(self):
        # 관용은 산문에서만이다. 이름(값) 짝에는 팩의 종목만 허용된다.
        ok, v = V.check('- 케이씨텍(+5.43%) 상승.', self.PACK, self.UNI)
        self.assertFalse(ok)
        self.assertTrue(any(x['kind'] == 'name' and x['value'] == '케이씨텍' for x in v), v)

    def test_trigger_numbers_are_not_registered_as_the_stocks_values(self):
        # 중첩 trigger 의 수치는 그 종목의 값이 아니다 — _walk_pairs 가 name 키 없는
        # dict 를 지나치므로 '5.98%' 는 케이씨의 짝이 아니다.
        by = V.pairs(self.PACK)
        self.assertIn(V.norm('+5.43%'), by[V.norm('케이씨')])
        self.assertNotIn(V.norm('5.98%'), by[V.norm('케이씨')])
        ok, v = V.check('- 케이씨(+5.98%) 상승.', self.PACK, self.UNI)
        self.assertFalse(ok)
        self.assertTrue(any(x['kind'] == 'pair' for x in v), v)

    def test_mentioned_set_is_not_in_allowed_names(self):
        self.assertNotIn(V.norm('케이씨텍'), V.allowed_names(self.PACK))
        self.assertIn(V.norm('케이씨텍'), V.mentioned_names(self.PACK, self.UNI))

    # ── 제목 속 이름은 자리로 본다 ──
    KEPCO = {'themes': [{'name': '원전', 'stocks': [
        {'name': '한국전력기술', 'chg': '+13.63%',
         'trigger': [{'title': '한국전력기술, 웨스팅하우스 지분 300억 공동인수 추진',
                      'publisher': '연합뉴스', 'date': '2026-09-21', 'source': 'naver_news'}]},
    ]}]}
    KEPCO_UNI = ['한국전력기술', '한국전력', '웨스팅', '삼성전자']

    def test_shorter_name_inside_a_longer_universe_name_is_not_tolerated(self):
        # 제목의 '한국전력' 은 '한국전력기술' 의 일부다. 부분 문자열로 관용하면 팩에 없는
        # 한국전력을 초안에 끌어와도 검사 3 을 통과했다.
        self.assertEqual(V.mentioned_names(self.KEPCO, self.KEPCO_UNI),
                         {V.norm('한국전력기술'), V.norm('웨스팅')})
        ok, v = V.check('- 한국전력기술(+13.63%) 연합뉴스 보도 — 웨스팅하우스 지분 공동인수. '
                        '한국전력도 동반 강세를 보임.', self.KEPCO, self.KEPCO_UNI)
        self.assertFalse(ok)
        self.assertEqual([x['value'] for x in v if x['kind'] == 'name'], ['한국전력'])

    def test_part_of_a_non_universe_word_in_the_title_is_still_tolerated(self):
        # '웨스팅하우스' 는 유니버스 이름이 아니다 — 초안이 그 낱말을 옮겨 적는 것은
        # 종목 '웨스팅' 을 지어낸 것이 아니다.
        ok, v = V.check('- 한국전력기술(+13.63%) 연합뉴스 보도 — 웨스팅하우스 지분 공동인수.',
                        self.KEPCO, self.KEPCO_UNI)
        self.assertTrue(ok, V.report(v))

    def test_trigger_title_numbers_are_not_allowed_numbers(self):
        # 프롬프트가 "trigger 항목의 수치도 옮기지 마라" 고 한다 — 검사 2 도 같은 말을 한다.
        pack = {'themes': [{'name': '반도체', 'stocks': [
            {'name': '케이씨', 'chg': '+5.43%',
             'trigger': [{'title': '케이씨 2분기 영업이익 120억, 전년比 35% 증가',
                          'publisher': '매일경제', 'source': 'naver_news'}]}]}]}
        self.assertNotIn(V.norm('120억'), V.allowed_numbers(pack))
        self.assertIn(V.norm('+5.43%'), V.allowed_numbers(pack))
        ok, v = V.check('- 케이씨(+5.43%) 영업이익 120억, 35% 증가.', pack, self.UNI)
        self.assertFalse(ok)
        self.assertEqual(sorted(x['value'].strip() for x in v if x['kind'] == 'number'),
                         ['120억', '35%'])
        ok, v = V.check('- 케이씨(+5.43%) 매일경제 보도 — 2분기 영업이익 증가.', pack, self.UNI)
        self.assertTrue(ok, V.report(v))

#!/usr/bin/env python3
"""
다이제스트 검증 단위 시험 — XDIGEST.md 3-3.

대조 대상이 **게시물 원문**이라는 것이 이 파일의 요점이다. 위반 표본이 각각 걸리고,
정상 표본은 통과해야 한다. 특히 영어 본문의 `Samsung` 을 `삼성전자` 로 옮긴 줄은
**위반이 아니다** — 1장 문체가 요구하는 일이고, 별칭 사전이 그것을 증명한다.
"""
import unittest

from ..xdigest import verify as V

POSTS = {
    'at://did:plc:a/app.bsky.feed.post/1': dict(
        account='pequity.bsky.social',
        text='KITA data: August HBM average export price $73.39, -3.6% MoM. '
             'March was $40.68 and July $76.14.'),
    'at://did:plc:b/app.bsky.feed.post/2': dict(
        account='polaris.bsky.social',
        text='BofA now sees 2030 DRAM+NAND TAM at $2T, up from $1.8T. '
             'Top picks $MU $TER.'),
    'at://did:plc:c/app.bsky.feed.post/3': dict(
        account='photon.bsky.social',
        text='Samsung Electronics plans HBM4 glass carrier outsourcing, '
             'capex +30% next year.'),
    'at://did:plc:d/app.bsky.feed.post/4': dict(
        account='micron.bsky.social',
        text='Micron posted record revenue of $46.7B, capex $80.4B. '
             'KOSPI index 6,808.21. Plans to ship 3 million HBM wafers '
             'in five months.'),
    'at://did:plc:e/app.bsky.feed.post/5': dict(
        account='pharma.bsky.social',
        text='Hanmi Pharmaceutical announced clinical results. '
             'Samsung Electro-Mechanics announced MLCC capacity expansion.'),
}
SOURCES = {k: v['text'] for k, v in POSTS.items()}
ACCOUNTS = {v['account'] for v in POSTS.values()}
P1, P2, P3, P4, P5 = list(POSTS)


def _alias():
    return V.load_names()


class Numbers(unittest.TestCase):
    def test_computed_percent_is_caught(self):
        # 게시물에 없는 계산값. $40.68 → $76.14 를 모델이 더해 만든 수치다.
        v = V.check('3~7월 약 +87% 상승함', [P1], SOURCES, ACCOUNTS, _alias())
        self.assertTrue([x for x in v if x['kind'] == 'number'], V.report(v))

    def test_converted_unit_is_caught(self):
        # 게시물은 `$2T` 라고 썼다. `2조 달러` 는 환산이다 (1장 문체).
        v = V.check('2030년 TAM 2조 달러 전망', [P2], SOURCES, ACCOUNTS, _alias())
        self.assertTrue([x for x in v if x['kind'] == 'number'], V.report(v))

    def test_literal_number_passes(self):
        v = V.check('8월 HBM 평균 수출단가 $73.39, 전월 대비 -3.6%',
                    [P1], SOURCES, ACCOUNTS, _alias())
        self.assertEqual(v, [], V.report(v))

    def test_unit_swap_after_a_dollar_sign_is_caught(self):
        # `$46.7B` → `$46.7M`. 1000배 틀린 매출이다. 예전 판은 `$…` 갈래가 단위를
        # 먹어 `$46.7` 만 토큰으로 잡고 통과시켰다.
        v = V.check('Micron 매출 $46.7M 기록함', [P4], SOURCES, ACCOUNTS, _alias())
        self.assertTrue([x for x in v if x['kind'] == 'number'], V.report(v))

    def test_dropped_unit_is_caught(self):
        # `$80.4B` → `$80.4`. 10억 배 차이다.
        v = V.check('캐펙스 $80.4 로 제시', [P4], SOURCES, ACCOUNTS, _alias())
        self.assertTrue([x for x in v if x['kind'] == 'number'], V.report(v))

    def test_truncated_number_is_caught(self):
        # 부분 문자열 대조가 통과시켰던 절단값 둘.
        for line in ('단가 $73.3 로 하락', '지수 6,808.2 로 마감'):
            v = V.check(line, [P1, P4], SOURCES, ACCOUNTS, _alias())
            self.assertTrue([x for x in v if x['kind'] == 'number'],
                            f'{line} — {V.report(v)}')

    def test_number_with_the_same_unit_passes(self):
        v = V.check('Micron 매출 $46.7B · 캐펙스 $80.4B · 지수 6,808.21',
                    [P4], SOURCES, ACCOUNTS, _alias())
        self.assertEqual(v, [], V.report(v))

    def test_scale_conversion_to_korean_is_caught(self):
        # `3 million` → `300만`. 환산이라 1장 문체가 금지한다.
        v = V.check('HBM 웨이퍼 300만 장 출하 계획임', [P4], SOURCES, ACCOUNTS,
                    _alias())
        self.assertTrue([x for x in v if x['kind'] == 'number'], V.report(v))

    def test_english_scale_word_matches_the_letter_form(self):
        # 원문이 `3 million` 이면 `3M` 은 표기만 다르다 — 자릿수가 같으므로 통과.
        v = V.check('HBM 웨이퍼 3M 장 출하 계획임', [P4], SOURCES, ACCOUNTS, _alias())
        self.assertEqual(v, [], V.report(v))

    def test_spelled_out_source_number_passes(self):
        # 원문 `in five months` → `5개월`. 수사를 숫자로 적는 것은 환산이 아니다.
        v = V.check('5개월 안에 출하 계획임', [P4], SOURCES, ACCOUNTS, _alias())
        self.assertEqual(v, [], V.report(v))

    def test_changed_period_is_caught(self):
        v = V.check('18개월 안에 출하 계획임', [P4], SOURCES, ACCOUNTS, _alias())
        self.assertTrue([x for x in v if x['kind'] == 'number'], V.report(v))

    def test_year_must_be_in_the_post(self):
        ok = V.check('2030년 TAM 전망', [P2], SOURCES, ACCOUNTS, _alias())
        self.assertEqual(ok, [], V.report(ok))
        bad = V.check('2031년 TAM 전망', [P2], SOURCES, ACCOUNTS, _alias())
        self.assertTrue([x for x in bad if x['kind'] == 'number'], V.report(bad))


class Mentions(unittest.TestCase):
    def test_unknown_account_is_caught(self):
        v = V.check('@nobody.bsky.social 이 같은 수치를 인용함',
                    [P1], SOURCES, ACCOUNTS, _alias())
        self.assertTrue([x for x in v if x['kind'] == 'account'], V.report(v))

    def test_unknown_ticker_is_caught(self):
        v = V.check('수혜주로 $AVGO 제시', [P2], SOURCES, ACCOUNTS, _alias())
        self.assertTrue([x for x in v if x['kind'] == 'ticker'], V.report(v))

    def test_ticker_in_the_post_passes(self):
        v = V.check('수혜주로 $MU $TER 제시', [P2], SOURCES, ACCOUNTS, _alias())
        self.assertEqual(v, [], V.report(v))


class Names(unittest.TestCase):
    def test_english_name_mapped_to_korean_passes(self):
        # 이 시험이 4번 검사의 존재 이유다. `Samsung Electronics` → `삼성전자`.
        v = V.check('삼성전자, HBM4 글라스 캐리어 외주 확대. 캐펙스 +30%',
                    [P3], SOURCES, ACCOUNTS, _alias())
        self.assertEqual(v, [], V.report(v))

    def test_alias_mapping_failure_is_caught(self):
        # 그 게시물은 SK하이닉스를 말하지 않았다.
        v = V.check('SK하이닉스도 같은 계획을 밝힘', [P3], SOURCES, ACCOUNTS, _alias())
        self.assertTrue([x for x in v if x['kind'] == 'name'], V.report(v))

    def test_other_company_with_the_same_prefix_is_caught(self):
        # `Hanmi Pharmaceutical` 기사가 `한미반도체` 로 바뀌면 렌더가 `(042700)` 을
        # 붙인다. 홀로 서는 짧은 별칭(`Hanmi`)을 사전에서 뺀 이유다.
        v = V.check('한미반도체 신약 임상 결과 발표함', [P5], SOURCES, ACCOUNTS,
                    _alias())
        self.assertTrue([x for x in v if x['kind'] == 'name'], V.report(v))

    def test_group_affiliate_is_not_the_flagship(self):
        v = V.check('삼성전자가 MLCC 증설 발표함', [P5], SOURCES, ACCOUNTS, _alias())
        self.assertTrue([x for x in v if x['kind'] == 'name'], V.report(v))

    def test_model_written_stock_code_is_caught(self):
        v = V.check('삼성전자(005930) 캐펙스 +30%', [P3], SOURCES, ACCOUNTS, _alias())
        self.assertTrue([x for x in v if x['kind'] == 'code'], V.report(v))

    def test_dictionary_has_codes_and_self_alias(self):
        a = _alias()
        self.assertEqual(V.code_of(a, '삼성전자'), '005930')
        self.assertIn('삼성전자', a['삼성전자']['aliases'])
        for head, info in a.items():
            self.assertRegex(info['code'], r'^\d{6}$', head)

    def test_no_standalone_group_prefix_alias(self):
        # 다른 상장사 이름의 앞머리가 되는 짧은 별칭은 사전에 없어야 한다 —
        # 있으면 남의 기사에 틀린 6자리 코드가 붙는다 (yaml 머리 주석).
        banned = {'samsung', 'hanmi', 'kakao', 'psk', 'nepes', 'dongjin',
                  'jusung', 'leeno', 'tesna', 'daeduck'}
        for head, info in _alias().items():
            for al in info['aliases']:
                self.assertNotIn(V.low(al), banned, f'{head} — {al}')


class Ids(unittest.TestCase):
    def test_missing_post_id_is_caught(self):
        v = V.check('8월 HBM 평균 수출단가 $73.39',
                    ['at://did:plc:z/app.bsky.feed.post/9'], SOURCES, ACCOUNTS,
                    _alias())
        self.assertTrue([x for x in v if x['kind'] == 'ids'], V.report(v))

    def test_id_outside_the_unit_is_caught(self):
        v = V.check('8월 HBM 평균 수출단가 $73.39', [P1], SOURCES, ACCOUNTS,
                    _alias(), allowed={P2})
        self.assertTrue([x for x in v if x['kind'] == 'ids'], V.report(v))

    def test_line_without_ids_is_caught(self):
        v = V.check('근거 없는 줄', [], SOURCES, ACCOUNTS, _alias())
        self.assertEqual([x['kind'] for x in v], ['ids'])


class Jargon(unittest.TestCase):
    def test_snake_case_leak_is_caught(self):
        v = V.check('posted_at 기준 -3.6% 하락', [P1], SOURCES, ACCOUNTS, _alias())
        self.assertTrue([x for x in v if x['kind'] == 'jargon'], V.report(v))


class Units(unittest.TestCase):
    """3-3 8번 — 통과한 줄로 다시 판정한다."""

    def test_one_account_left_demotes_the_subtopic(self):
        facts = [dict(text='a', post_ids=[P1])]
        ok, why = V.unit_ok(facts, POSTS)
        self.assertFalse(ok)
        self.assertIn('1개', why)

    def test_two_accounts_keep_it(self):
        facts = [dict(text='a', post_ids=[P1]), dict(text='b', post_ids=[P2])]
        ok, why = V.unit_ok(facts, POSTS)
        self.assertTrue(ok, why)

    def test_no_surviving_fact_demotes_the_subtopic(self):
        ok, why = V.unit_ok([], POSTS)
        self.assertFalse(ok)
        self.assertIn('사실 줄', why)


class Views(unittest.TestCase):
    """④ 해석 줄 — 대조 대상이 ②③ 의 사실 줄이다."""

    FACTS = {'f001': '8월 HBM 평균 수출단가 $73.39, 전월 대비 -3.6%',
             'f002': '2030년 DRAM+NAND TAM $2T 전망. 수혜주로 $MU $TER 제시'}

    def test_view_from_the_fact_lines_passes(self):
        v = V.check('단가 -3.6% 는 첫 균열이지만 TAM $2T 전망은 유지 — $MU 주시',
                    ['f001', 'f002'], self.FACTS, ACCOUNTS, _alias())
        self.assertEqual(v, [], V.report(v))

    def test_new_number_in_a_view_is_caught(self):
        v = V.check('단가 하락폭이 -12.5% 까지 벌어질 전망',
                    ['f001'], self.FACTS, ACCOUNTS, _alias())
        self.assertTrue([x for x in v if x['kind'] == 'number'], V.report(v))


if __name__ == '__main__':
    unittest.main()

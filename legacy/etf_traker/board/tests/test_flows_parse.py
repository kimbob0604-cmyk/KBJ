"""
네이버 투자자별 매매동향 파서 — board/ingest/flows.py.

실데이터에서 "표 파싱 실패 — 페이지 구조 변경 의심" 으로 계속 비어 있던 자리다.
이 환경은 네이버로 못 나가므로, 여기서는 표기 형태만 못박고 실제 확인은
러너의 `--check` 로그에서 한다.
"""
import unittest

from board.ingest import flows as F
from board.ingest.http import Fetch

# 시장 전체 표. 연도가 **두 자리**다. 4자리만 받으면 한 행도 안 걸린다.
MARKET = '''<div>투자자별 매매동향 (단위: 억원)</div><table class="type_1">
<tr><th>날짜</th><th>개인</th><th>외국인</th><th>기관계</th><th>기타법인</th></tr>
<tr><td>26.08.28</td><td>-20,145</td><td>-1,162</td><td>7,604</td><td>10,203</td></tr>
<tr><td>26.08.27</td><td>3,001</td><td>-5,020</td><td>1,110</td><td>909</td></tr>
</table>'''

# 종목별 표는 네 자리다. 둘 다 통과해야 한다.
STOCK = '''<table><tr><th>날짜</th><th>종가</th></tr>
<tr><td>2026.08.28</td><td>71,000</td><td>500</td><td>0.71</td>
<td>12,345,678</td><td>-100,000</td><td>250,000</td></tr></table>'''


class StockTrendApi(unittest.TestCase):
    """종목별 수급 — 2026-09 에 HTML 표가 사라지고 JSON API 로 바뀌었다.

    옛 주소(finance.naver.com/item/frgn.naver)는 302 로 Next.js 화면으로 넘어가고
    응답에 tr/td 가 하나도 없다(러너 실측 118,366자 · tr 0개). 여기 픽스처는
    **러너에서 실제로 받은 응답**이다(003490, 2026-09-16).
    """

    # 실제 응답 그대로. 지어낸 값이 아니다.
    TREND = [
        {"itemCode": "003490", "bizdate": "20260916",
         "foreignerPureBuyQuant": "+7,600", "foreignerHoldRatio": "25.81%",
         "organPureBuyQuant": "+63,482", "individualPureBuyQuant": "-74,126",
         "closePrice": "29,250", "compareToPreviousClosePrice": "-100",
         "accumulatedTradingVolume": "1,322,448"},
        {"itemCode": "003490", "bizdate": "20260911",
         "foreignerPureBuyQuant": "+597,024", "foreignerHoldRatio": "25.72%",
         "organPureBuyQuant": "-415,612", "individualPureBuyQuant": "-153,284",
         "closePrice": "28,950", "compareToPreviousClosePrice": "-600",
         "accumulatedTradingVolume": "2,165,418"},
    ]

    def _fetch(self, payload):
        """get() 만 갈아 끼우고 fetch_stock 본체를 그대로 태운다."""
        real = F.get
        F.get = lambda s, url, params=None, **kw: payload
        try:
            return F.fetch_stock('003490', s=object())
        finally:
            F.get = real

    def test_json_is_read(self):
        r = self._fetch(self.TREND)
        self.assertEqual(r['unit'], '주')
        self.assertEqual(sorted(r['by_date']), ['2026-09-11', '2026-09-16'])
        day = r['by_date']['2026-09-16']
        self.assertAlmostEqual(day['close'], 29250.0)
        self.assertAlmostEqual(day['volume'], 1322448.0)
        self.assertAlmostEqual(day['기관'], 63482.0)
        self.assertAlmostEqual(day['외국인'], 7600.0)

    def test_sign_is_kept(self):
        # 순매수와 순매도를 가르는 값이다. 부호가 날아가면 정반대가 된다.
        r = self._fetch(self.TREND)
        self.assertAlmostEqual(r['by_date']['2026-09-11']['기관'], -415612.0)
        self.assertAlmostEqual(r['by_date']['2026-09-11']['개인'], -153284.0)

    def test_absent_person_is_not_invented(self):
        """2026-09-21 실측 형태 — individualPureBuyQuant 가 없다. 0 으로 만들지 않는다."""
        rows = [{k: v for k, v in x.items() if k != 'individualPureBuyQuant'}
                for x in self.TREND]
        r = self._fetch(rows)
        day = r['by_date']['2026-09-16']
        self.assertNotIn('개인', day)
        self.assertAlmostEqual(day['기관'], 63482.0)
        self.assertAlmostEqual(day['외국인'], 7600.0)

    def test_no_investor_keys_in_any_row_is_a_failure(self):
        """키가 바뀐 날. 날짜와 종가만 든 행을 돌려주면 받는 쪽이 '성공' 으로
        세고 값은 하나도 없다 — 사유에 받은 키를 적어 올린다."""
        rows = [{k: v for k, v in x.items() if not k.endswith('PureBuyQuant')}
                for x in self.TREND]
        with self.assertRaises(Fetch) as e:
            self._fetch(rows)
        self.assertIn('투자자 구분이 한 행에도 없다', str(e.exception))
        self.assertIn('closePrice', str(e.exception))

    def test_one_row_with_values_is_enough(self):
        # 당일 행만 아직 비어 있는 날. 전일 행에 값이 있으면 실패가 아니다.
        rows = [{k: v for k, v in self.TREND[0].items()
                 if not k.endswith('PureBuyQuant')}, self.TREND[1]]
        r = self._fetch(rows)
        self.assertNotIn('기관', r['by_date']['2026-09-16'])
        self.assertAlmostEqual(r['by_date']['2026-09-11']['기관'], -415612.0)

    def test_days_goes_out_as_page_size(self):
        seen = {}
        real = F.get

        def fake(s, url, params=None, **kw):
            seen.update(params or {})
            return self.TREND
        F.get = fake
        try:
            F.fetch_stock('003490', s=object(), days=5)
        finally:
            F.get = real
        self.assertEqual(seen.get('pageSize'), 5)

    def test_dead_html_endpoint_is_not_used(self):
        # 이 버그의 본체는 파서가 아니라 없어진 주소였다. 되돌아가면 또 빈다.
        self.assertNotIn('item/frgn.naver', F.STOCK_URL)
        self.assertIn('{code}', F.STOCK_URL)

    def test_empty_or_wrong_shape_is_not_silent(self):
        # 조용히 빈 dict 를 돌려주면 139종목이 또 말없이 빈다.
        for bad in ([], None, {}, 'nope'):
            with self.assertRaises(Fetch):
                self._fetch(bad)

    def test_rows_without_a_date_are_dropped(self):
        # 날짜 없는 행을 0 으로 채우지 않는다.
        with self.assertRaises(Fetch):
            self._fetch([{"closePrice": "1,000"}])


class Amounts(unittest.TestCase):
    def test_quantity_times_close_is_an_estimate(self):
        self.assertAlmostEqual(F.to_amount(250000, 71000), 177.5)
        self.assertIsNone(F.to_amount(None, 71000))
        self.assertIsNone(F.to_amount(250000, 0))


if __name__ == '__main__':
    unittest.main()


# 네이버 실제 구조: 머리글이 두 줄이고 '기관' 이 하위 구분 위에 colspan 으로 걸린다.
# 2026-08-31 실행의 진단이 알려 준 머리글: 날짜·개인·외국인·기관계·기관·기타법인
REAL = '''<div>투자자별 매매동향 (단위: 억원)</div><table class="type_1">
<tr><th rowspan="2">날짜</th><th rowspan="2">개인</th><th rowspan="2">외국인</th>
    <th rowspan="2">기관계</th><th colspan="3">기관</th><th rowspan="2">기타법인</th></tr>
<tr><th>금융투자</th><th>보험</th><th>투신</th></tr>
<tr><td>26.08.28</td><td>-20,145</td><td>1,162</td><td>7,604</td>
    <td>100</td><td>200</td><td>300</td><td>10,203</td></tr>
</table>'''


class SeedNearMatch(unittest.TestCase):
    """못 찾은 시드에 후보를 붙인다 — 대부분은 사명 변경이다."""

    def test_renamed_company_is_proposed(self):
        from board.engine import themes as TH
        uni = {'1': dict(name='HD한국조선해양'), '2': dict(name='삼성전자')}
        ty = dict(axes={}, themes=[dict(id='t', name='T', axis='a',
                                        seeds={'설계': ['한국조선해양']})])
        _, _, un = TH.build(ty, uni, dict(themes=dict(seed_confidence=0.9)))
        self.assertEqual(un[0]['near'], ['HD한국조선해양'])

    def test_declared_unlisted_gets_no_candidates(self):
        # 안 붙는 게 정상인데 "혹시 이건가" 를 띄우면 노이즈다.
        from board.engine import themes as TH
        uni = {'1': dict(name='세메스투자')}
        ty = dict(axes={}, themes=[dict(id='t', name='T', axis='a',
                                        seeds={'장비': ['세메스']})],
                  unlisted={'세메스': '비상장'})
        _, _, un = TH.build(ty, uni, dict(themes=dict(seed_confidence=0.9)))
        self.assertEqual(un[0]['near'], [])
        self.assertEqual(un[0]['known'], '비상장')

    def test_nothing_similar_gives_an_empty_list(self):
        from board.engine import themes as TH
        uni = {'1': dict(name='삼성전자')}
        ty = dict(axes={}, themes=[dict(id='t', name='T', axis='a',
                                        seeds={'설계': ['존재하지않는회사명']})])
        _, _, un = TH.build(ty, uni, dict(themes=dict(seed_confidence=0.9)))
        self.assertEqual(un[0]['near'], [])

    def test_coincidental_overlap_is_not_proposed(self):
        """실측(2026-08-31)에서 나온 가짜 후보들.

        비율만 보면 셋 다 0.667 이고, 진짜 개명인 현대미포조선 → HD현대미포 도
        0.667 이다. 문턱으로는 못 가른다. 개명은 옛 이름이 거의 통째로 남는다는
        성질을 쓴다.
        """
        from board.engine import themes as TH
        uni = {str(i): dict(name=n) for i, n in enumerate(
            ['에이럭스', '노머스', '로스웰', 'HD현대미포'])}
        ty = dict(axes={}, themes=[dict(id='t', name='T', axis='a', seeds={
            's': ['삼화에이스', '리노스', '뉴로스', '현대미포조선']})])
        _, _, un = TH.build(ty, uni, dict(themes=dict(seed_confidence=0.9)))
        got = {x['seed']: x['near'] for x in un}
        self.assertEqual(got['삼화에이스'], [])
        self.assertEqual(got['리노스'], [])
        self.assertEqual(got['뉴로스'], [])
        self.assertEqual(got['현대미포조선'], ['HD현대미포'])   # 진짜 개명은 남는다

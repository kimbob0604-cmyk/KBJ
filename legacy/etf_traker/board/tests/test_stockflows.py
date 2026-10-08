"""
52주 이상 신고가 종목의 **종목별** 수급 — 누가 샀는지.

여기서 잡으려는 것은 조용히 틀리는 것들이다.
  1. 대상이 기본 기준(`label`)으로 갈린다 → 설정을 바꾸는 날 목록이 바뀐다
  2. 못 받은 종목이 조용히 빠진다 → 신고가를 안 냈다고 읽힌다
  3. 받은 값이 0 으로 채워진다 → '안 샀다' 는 없는 사실이 된다
  4. 전일 값을 당일이라고 적는다 → 다른 날의 사실이 같은 날로 읽힌다
  5. KIS 가 접속 자체를 안 받는 시간대(D-083)에 종목마다 25초씩 기다린다
     → 11종목에 9분을 버리고 전부 빈다. 첫 접속 실패에서 멈추고 네이버로 간다
  6. 네이버 폴백은 순매매량(주)이고 개인은 없는 날이 있다 → 개인을 0 으로 만들거나
     금액을 사실처럼 적으면 없는 사실이 된다. 출처와 '추정' 이 보여야 한다
  7. 투자자 구분이 없는 행(키가 바뀐 날)을 '성공' 으로 센다 → 배너는 성공,
     텔레그램은 사유 없는 '못 받음'. 값 없는 항목은 실패이고 사유가 있어야 한다
  8. 기준일 뒤 날짜를 골라 놓고 '이전 영업일 값' 이라 적는다 → 지난 기준일 재실행
"""
import math
import unittest
from datetime import datetime

from ..ingest import stockflows as SF
from ..ingest.http import Fetch
from ..report import telegram as TG

# 러너 실측을 본뜬 KIS 접속 실패 메시지(run 109, 2026-09-21 22:59 KST). http.get
# 이 이렇게 감싸 올린다. 실제 문자열은 500자에 가깝다 — 그걸 그대로 싣지 않는지 본다.
CONNECT_TIMEOUT = Fetch(
    'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/'
    'inquire-investor 실패: ConnectTimeout: HTTPSConnectionPool(host='
    "'openapi.koreainvestment.com', port=9443): Max retries exceeded with url: "
    '/uapi/domestic-stock/v1/quotations/inquire-investor?FID_COND_MRKT_DIV_CODE=J&'
    'FID_INPUT_ISCD=005930 (Caused by ConnectTimeoutError(<urllib3.connection.'
    "HTTPSConnection object at 0x7f3c2a1b2d90>, 'Connection to "
    "openapi.koreainvestment.com timed out. (connect timeout=25)'))")

# 네이버 trend 응답. 2026-09-21 실측 형태 — 기관·외국인만 있고 개인은 없다.
# 문자열에 쉼표와 부호가 든다.
TREND = [
    {"bizdate": "20260917", "closePrice": "29,250",
     "compareToPreviousClosePrice": "-100", "accumulatedTradingVolume": "1,234,567",
     "organPureBuyQuant": "+63,482", "foreignerPureBuyQuant": "-12,000",
     "foreignerHoldRatio": "25.81%"},
    {"bizdate": "20260916", "closePrice": "29,350",
     "compareToPreviousClosePrice": "+400", "accumulatedTradingVolume": "987,654",
     "organPureBuyQuant": "-415,612", "foreignerPureBuyQuant": "+597,024",
     "foreignerHoldRatio": "25.72%"},
]
KST_2302 = datetime(2026, 9, 21, 23, 2, tzinfo=SF.KST)
KST_1720 = datetime(2026, 9, 21, 17, 20, tzinfo=SF.KST)


def row(code, name, close=None, high=None, **kw):
    return dict(code=code, name=name,
                close_basis=dict(label=close), high_basis=dict(label=high), **kw)


NEWHIGH = dict(
    as_of='2026-09-17',
    labels=dict(hist='역사적', w52='52주', d120='120일'),
    achieved=[
        row('001', '종가52', close='w52', high='w52', chg_pct=3.0, vol_mult=4.0),
        row('002', '고가만52', close=None, high='w52', chg_pct=-0.4, vol_mult=0.5),
        row('003', '종가역사적', close='hist', high='hist', chg_pct=1.2, vol_mult=0.9),
        # 종가로는 120일, 고가로는 52주. 52주 목록에 들어야 한다.
        row('004', '섞인것', close='d120', high='w52', chg_pct=3.6, vol_mult=10.1),
        row('005', '120일뿐', close='d120', high='d120', chg_pct=1.0, vol_mult=2.0),
    ])


class Targets(unittest.TestCase):

    def test_picks_52w_and_above_on_either_basis(self):
        got = [x['name'] for x in SF.targets(NEWHIGH)]
        self.assertEqual(got, ['종가52', '고가만52', '종가역사적', '섞인것'])

    def test_60d_only_is_left_out(self):
        self.assertNotIn('120일뿐', [x['name'] for x in SF.targets(NEWHIGH)])

    def test_reads_the_basis_by_name_not_the_default_label(self):
        """`label` 은 설정이 가리키는 기본 기준이다. 그걸 보면 대상이 흔들린다."""
        # 기본 기준이 무엇이든 close_basis/high_basis 만 보므로 결과가 같아야 한다.
        nh = dict(NEWHIGH, basis='high')
        for x in nh['achieved']:
            x['label'] = 'd120'          # 기본 라벨을 일부러 낮춰 놓는다
        self.assertEqual(len(SF.targets(nh)), 4)

    def test_same_code_is_counted_once(self):
        nh = dict(NEWHIGH, achieved=list(NEWHIGH['achieved']) + [
            row('001', '종가52', close='w52', high='w52')])
        self.assertEqual(len(SF.targets(nh)), 4)


class Collect(unittest.TestCase):

    def setUp(self):
        self.real_has = SF.creds.has
        self.real_session = SF.kis.session
        self.real_flows = SF.kis.stock_flows
        self.real_naver = SF.naver_flows
        SF.creds.has = lambda *a: True
        SF.kis.session = lambda: None
        self.kis_calls = []
        self.naver_calls = []
        # 기본은 네이버도 안 된다. 라이브 호출이 새지 않게 항상 갈아 끼운다.
        self._naver_fail()

    def tearDown(self):
        SF.creds.has = self.real_has
        SF.kis.session = self.real_session
        SF.kis.stock_flows = self.real_flows
        SF.naver_flows = self.real_naver

    def _fake(self, table, errors=None):
        """KIS 표. 없는 종목은 종목 단위 오류, errors 에 든 종목은 그 예외."""
        def f(code, s=None, retries=None):
            self.kis_calls.append(code)
            # 빠른 실패의 절반은 시도 횟수다. collect 가 이 값을 안 넘기면 접속
            # 불가 시각에 첫 종목에서 25초 × 기본값을 다시 기다린다.
            self.assertEqual(retries, SF.KIS_RETRIES)
            if errors and code in errors:
                raise errors[code]
            if code not in table:
                raise Fetch(f'stock_investor rt_cd=1 없는 종목 {code}')
            return dict(code=code, source='kis', by_date=table[code])
        SF.kis.stock_flows = f

    def _kis_down(self, exc=CONNECT_TIMEOUT):
        def f(code, s=None, retries=None):
            self.kis_calls.append(code)
            self.assertEqual(retries, SF.KIS_RETRIES)
            raise exc
        SF.kis.stock_flows = f

    def _naver_ok(self, payload=TREND):
        """네이버 get() 만 갈아 끼우고 fetch_stock 파서는 그대로 태운다."""
        real_get = SF.NV.get

        def f(code):
            self.naver_calls.append(code)
            SF.NV.get = lambda s, url, params=None, **kw: payload
            try:
                return SF.NV.fetch_stock(code, days=SF.NAVER_DAYS)
            finally:
                SF.NV.get = real_get
        SF.naver_flows = f

    def _naver_fail(self):
        def f(code):
            self.naver_calls.append(code)
            raise Fetch(f'https://m.stock.naver.com/api/stock/{code}/trend 실패: '
                        'HTTP 500 · 본문 없음')
        SF.naver_flows = f

    def collect(self, **kw):
        kw.setdefault('log', lambda *a: None)
        kw.setdefault('now', KST_1720)
        return SF.collect(NEWHIGH, '2026-09-17', **kw)

    # ── KIS 정상 경로 ──
    def test_collects_the_target_day(self):
        self._fake({'001': {'2026-09-17': {'기관': 12.0, '외국인': -3.0,
                                           '개인': -9.0, '_unit': '억원'}}})
        out = self.collect()
        self.assertEqual(out['by_code']['001']['기관'], 12.0)
        self.assertEqual(out['by_code']['001']['as_of'], '2026-09-17')
        self.assertEqual(out['by_code']['001']['source'], 'kis')
        self.assertIsNone(out['kis_down'])

    def test_failures_are_named_not_swallowed(self):
        self._fake({'001': {'2026-09-17': {'기관': 1.0, '_unit': '억원'}}})
        out = self.collect()
        self.assertEqual(len(out['by_code']), 1)
        self.assertEqual(sorted(out['failed']), ['002', '003', '004'],
                         '못 받은 종목이 조용히 빠졌다')
        self.assertEqual(len(out['missing']), 3)
        self.assertTrue(any('고가만52' in m for m in out['missing']))
        # 사유는 두 소스를 다 말하고, 스택이 아니다.
        why = out['failed']['002']
        self.assertIn('KIS', why)
        self.assertIn('네이버 폴백도 실패', why)
        self.assertLess(len(why), 300)

    def test_absent_investor_is_not_zero(self):
        self._fake({'001': {'2026-09-17': {'기관': 5.0, '_unit': '억원'}}})
        out = self.collect()
        e = out['by_code']['001']
        self.assertNotIn('외국인', e, '못 받은 구분이 0 으로 들어갔다')
        self.assertNotIn('개인', e)

    def test_older_day_is_kept_but_dated(self):
        """당일치가 없으면 직전 값을 쓰되 그 날짜를 남긴다."""
        self._fake({'001': {'2026-09-16': {'기관': 7.0, '_unit': '억원'}}})
        out = self.collect()
        self.assertEqual(out['by_code']['001']['as_of'], '2026-09-16')
        self.assertTrue(any('이전 영업일' in m and '2026-09-16' in m
                            for m in out['missing']))

    def test_no_credentials_says_so(self):
        SF.creds.has = lambda *a: False
        out = self.collect()
        self.assertEqual(out['by_code'], {})
        self.assertTrue(any('앱키' in m for m in out['missing']))
        self.assertEqual(self.kis_calls, [], '앱키가 없는데 KIS 를 불렀다')

    def test_no_credentials_still_gets_naver(self):
        """앱키가 없어도 네이버는 인증이 없다. 사유는 그대로 남긴다."""
        SF.creds.has = lambda *a: False
        self._naver_ok()
        out = self.collect()
        self.assertEqual(len(out['by_code']), 4)
        self.assertTrue(all(v['source'] == 'naver' for v in out['by_code'].values()))
        self.assertTrue(any('앱키' in m and '네이버 폴백 4종목 성공' in m
                            for m in out['missing']))

    def test_no_targets_is_not_an_error(self):
        out = SF.collect(dict(achieved=[]), '2026-09-17', log=lambda *a: None)
        self.assertEqual(out['n_target'], 0)
        self.assertEqual(out['missing'], [])
        self.assertEqual(out['failed'], {})

    # ── (a) 빠른 실패 ──
    def test_connect_failure_calls_kis_once_then_falls_back(self):
        """run 109 의 증상. 종목마다 25초씩 기다리면 11종목에 9분이다."""
        self._kis_down()
        self._naver_ok()
        out = self.collect(now=KST_2302)
        self.assertEqual(len(self.kis_calls), 1, 'KIS 접속 불가인데 다음 종목을 또 불렀다')
        self.assertEqual(len(self.naver_calls), 4)
        self.assertEqual(len(out['by_code']), 4)
        self.assertTrue(all(v['source'] == 'naver' for v in out['by_code'].values()))
        self.assertTrue(out['kis_down'].startswith('KIS 접속 불가(연결 시간 초과'))
        self.assertIn('23:02 KST', out['kis_down'])
        self.assertIn('점검 시간대로 추정', out['kis_down'])
        self.assertEqual(out['failed'], {})

    def test_connect_failure_is_one_line_not_a_stack(self):
        self._kis_down()
        self._naver_ok()
        out = self.collect(now=KST_2302)
        line = out['missing'][0]
        self.assertTrue(line.startswith('KIS 접속 불가('), line)
        self.assertIn('4종목 → 네이버 폴백 4종목 성공', line)
        for m in out['missing']:
            self.assertLess(len(m), 220, m)
            self.assertNotIn('HTTPSConnectionPool', m)
            self.assertNotIn('urllib3', m)

    def test_maintenance_hint_only_near_23h(self):
        """17:20 에 안 붙은 것은 이유를 모른다. 점검이라고 짐작해 적지 않는다."""
        self._kis_down()
        self._naver_ok()
        out = self.collect(now=KST_1720)
        self.assertIn('17:20 KST', out['kis_down'])
        self.assertNotIn('점검', out['kis_down'])

    def test_down_reason_names_the_failure_kind(self):
        self.assertIn('연결 시간 초과', SF.down_reason(CONNECT_TIMEOUT, KST_1720))
        self.assertIn('연결 거부', SF.down_reason(
            Fetch('x 실패: ConnectionError: [Errno 111] Connection refused'), KST_1720))
        self.assertIn('연결 실패', SF.down_reason(
            Fetch('x 실패: ConnectionError: something else'), KST_1720))

    # ── (f) 종목 단위 KIS 실패는 그 종목만 폴백 ──
    def test_stock_level_kis_error_falls_back_only_that_stock(self):
        day = {'2026-09-17': {'기관': 1.0, '외국인': 2.0, '_unit': '억원'}}
        self._fake({'001': day, '003': day, '004': day})      # 002 는 rt_cd 오류
        self._naver_ok()
        out = self.collect()
        self.assertEqual(self.kis_calls, ['001', '002', '003', '004'],
                         'rt_cd 오류는 종목 단위다. 나머지는 KIS 로 계속 가야 한다')
        self.assertEqual(self.naver_calls, ['002'])
        self.assertIsNone(out['kis_down'])
        self.assertEqual(out['by_code']['002']['source'], 'naver')
        self.assertIn('rt_cd=1', out['by_code']['002']['kis_error'])
        for c in ('001', '003', '004'):
            self.assertEqual(out['by_code'][c]['source'], 'kis')
        self.assertEqual(out['unit'], 'mixed')
        self.assertTrue(any(m.startswith('KIS 종목 단위 실패 1종목') and '고가만52' in m
                            for m in out['missing']))

    # ── (b) 네이버 파서 ──
    def test_naver_keeps_sign_and_commas_and_makes_no_person(self):
        self._kis_down()
        self._naver_ok()
        out = self.collect(now=KST_2302)
        e = out['by_code']['001']
        self.assertEqual(e['unit'], '주')
        self.assertEqual(e['as_of'], '2026-09-17')
        self.assertAlmostEqual(e['기관'], 63482.0)      # "+63,482"
        self.assertAlmostEqual(e['외국인'], -12000.0)   # "-12,000"
        self.assertNotIn('개인', e, '응답에 없는 개인을 만들었다')
        self.assertNotIn('개인', e.get('amt_est') or {})
        # 배너도 실제로 받은 구분만 말한다.
        note = next(m for m in out['missing'] if m.startswith('네이버 폴백'))
        self.assertIn('기관·외국인 순매매량(주)', note)
        self.assertIn('개인은 응답에 없습니다', note)

    def test_naver_person_when_present_is_kept_and_banner_agrees(self):
        """2026-09-16 실측 형태 — individualPureBuyQuant 가 있다. 배너가 '개인은
        없습니다' 라고 단정하면 같은 보드의 텔레그램 줄('개인 -74,126주')과 모순된다."""
        self._kis_down()
        self._naver_ok([dict(TREND[0], individualPureBuyQuant='-74,126')])
        out = self.collect(now=KST_2302)
        e = out['by_code']['001']
        self.assertAlmostEqual(e['개인'], -74126.0)
        self.assertIn('개인', e['amt_est'])
        note = next(m for m in out['missing'] if m.startswith('네이버 폴백'))
        self.assertIn('기관·외국인·개인 순매매량(주)', note)
        self.assertNotIn('개인은 응답에 없습니다', note)
        self.assertNotIn('개인은 없습니다', note)

    def test_naver_older_day_is_dated(self):
        self._kis_down()
        self._naver_ok([TREND[1]])                       # 09-16 만 있다
        out = self.collect(now=KST_2302)
        self.assertEqual(out['by_code']['001']['as_of'], '2026-09-16')
        self.assertAlmostEqual(out['by_code']['001']['기관'], -415612.0)
        self.assertTrue(any('이전 영업일' in m and '2026-09-16' in m
                            for m in out['missing']))

    # ── (c) 추정 금액 ──
    def test_estimated_amount_is_flagged(self):
        self._kis_down()
        self._naver_ok()
        out = self.collect(now=KST_2302)
        e = out['by_code']['001']
        est = e['amt_est']
        self.assertIs(est['is_estimate'], True)
        self.assertEqual(est['unit'], '억원')
        # 63,482주 × 29,250원 = 18.568485억. 만원 자리(4자리)까지 둔다 — 표기
        # 자릿수는 표기하는 쪽이 정한다.
        self.assertAlmostEqual(est['기관'], 18.5685)
        self.assertAlmostEqual(est['외국인'], -3.51)
        # 수량 칸은 추정이 아니다. 섞이지 않게 키가 따로다.
        self.assertAlmostEqual(e['기관'], 63482.0)
        self.assertTrue(any('추정' in m and '네이버 폴백 4종목' in m
                            for m in out['missing']))

    # ── (e) 둘 다 실패 ──
    def test_both_sources_down_leaves_reason_not_zero(self):
        self._kis_down()
        self._naver_fail()
        out = self.collect(now=KST_2302)
        self.assertEqual(out['by_code'], {}, '못 받은 값이 채워졌다')
        self.assertEqual(sorted(out['failed']), ['001', '002', '003', '004'])
        why = out['failed']['002']
        self.assertIn('KIS 접속 불가', why)
        self.assertIn('네이버 폴백도 실패', why)
        self.assertNotIn('HTTPSConnectionPool', why)
        line = out['missing'][0]
        self.assertIn('네이버 폴백 0종목 성공', line)
        self.assertIn('4종목 실패(', line)
        self.assertIn('고가만52(002)', line)
        # 배너만 보는 쪽도 네이버가 **왜** 실패했는지 알아야 한다. 같은 사유는 한 번.
        self.assertIn('네이버 사유: HTTP 500 · 본문 없음', line)
        self.assertEqual(line.count('HTTP 500'), 1)
        self.assertEqual(len(self.kis_calls), 1)
        self.assertIsNone(out['source'], '받은 값이 없는데 출처가 있다')

    # ── (g) 투자자 구분이 없는 행은 값이 아니다 ──
    NO_WHO = [{"bizdate": "20260917", "closePrice": "29,250",
               "accumulatedTradingVolume": "1"}]

    def test_naver_rows_without_investor_keys_are_a_failure_with_reason(self):
        """네이버가 organPureBuyQuant 키를 바꾼 날. 파서는 close 만 든 행을 돌려주고
        예전에는 그게 '네이버 폴백 4종목 성공' 이 됐다 — 값은 하나도 없이."""
        self._kis_down()
        self._naver_ok(self.NO_WHO)
        out = self.collect(now=KST_2302)
        self.assertEqual(out['by_code'], {}, '값 없는 항목이 성공으로 들어갔다')
        self.assertEqual(out['n_naver'], 0)
        self.assertEqual(sorted(out['failed']), ['001', '002', '003', '004'])
        why = out['failed']['001']
        self.assertIn('네이버 폴백도 실패', why)
        self.assertIn('투자자 구분이 한 행에도 없다', why)
        self.assertIn('closePrice', why, '받은 키를 적어야 무엇이 바뀌었는지 안다')
        self.assertIn('네이버 폴백 0종목 성공', out['missing'][0])
        self.assertIn('네이버 사유:', out['missing'][0])

    def test_naver_target_row_without_values_falls_to_the_dated_older_row(self):
        """당일 행은 왔는데 구분이 아직 안 채워졌고 전일 행에는 있다 — 전일 값을
        날짜와 함께 쓴다. 빈 당일 행을 고르면 값 없는 '성공' 이 된다."""
        self._kis_down()
        self._naver_ok(self.NO_WHO + [TREND[1]])
        out = self.collect(now=KST_2302)
        e = out['by_code']['001']
        self.assertEqual(e['as_of'], '2026-09-16')
        self.assertAlmostEqual(e['기관'], -415612.0)
        self.assertTrue(any('이전 영업일' in m for m in out['missing']))

    def test_kis_rows_without_investor_keys_fall_back_to_naver(self):
        """KIS 가 구분 없이 close 만 주는 날. 빈 항목을 KIS 성공으로 세지 않고
        종목 단위 실패로 두어 네이버로 간다 — 사유에 받은 키가 남는다."""
        self._fake({'001': {'2026-09-17': {'close': 29250.0}}})
        self._naver_ok()
        out = self.collect()
        e = out['by_code']['001']
        self.assertEqual(e['source'], 'naver')
        self.assertIn('투자자 구분이 없다', e['kis_error'])
        self.assertIn('close', e['kis_error'])
        # 002~004 는 rt_cd 오류로 함께 구제된다. 001 의 사유가 그 줄에 있어야 한다.
        self.assertTrue(any(m.startswith('KIS 종목 단위 실패 4종목')
                            and '종가52(001) 투자자 구분이 없다' in m
                            for m in out['missing']))

    # ── (h) 기준일 뒤 날짜는 고르지 않는다 ──
    def test_latest_never_picks_a_day_after_asof(self):
        by = {'2026-09-16': {'기관': 1.0}, '2026-09-17': {'기관': 2.0},
              '2026-09-25': {'기관': 3.0}}
        self.assertEqual(SF._latest(by, '2026-09-17'), ({'기관': 2.0}, '2026-09-17'))
        self.assertEqual(SF._latest(by, '2026-09-18'), ({'기관': 2.0}, '2026-09-17'))
        self.assertEqual(SF._latest(by, '2026-09-10'), (None, None))
        self.assertIn('기준일 이전 행이 없다', SF._no_value_reason(by, '2026-09-10'))

    def test_latest_skips_rows_without_values(self):
        by = {'2026-09-17': {'close': 1.0}, '2026-09-16': {'기관': 2.0, 'close': 1.0}}
        self.assertEqual(SF._latest(by, '2026-09-17')[1], '2026-09-16')
        self.assertIsNone(SF._latest({'2026-09-17': {'close': 1.0}}, '2026-09-17')[0])
        self.assertIn("['close']", SF._no_value_reason({'2026-09-17': {'close': 1.0}},
                                                        '2026-09-17'))

    def test_rerun_for_a_past_day_does_not_take_a_later_value(self):
        """지난 기준일을 다시 만드는데 응답 창이 그 뒤 날짜뿐이다. 8일 뒤 값을
        '이전 영업일 값' 이라 적지 않고 사유와 함께 실패한다."""
        self._kis_down()
        self._naver_ok([dict(TREND[0], bizdate='20260925'),
                        dict(TREND[1], bizdate='20260924')])
        out = SF.collect(NEWHIGH, '2026-09-17', log=lambda *a: None, now=KST_2302)
        self.assertEqual(out['by_code'], {})
        self.assertIn('기준일 이전 행이 없다', out['failed']['001'])
        self.assertIn('2026-09-25', out['failed']['001'])
        self.assertFalse(any('이전 영업일' in m for m in out['missing']))

    def test_naver_window_is_wide_enough_for_a_rerun(self):
        # 5행이면 기준일 이하 필터가 재실행 창을 통째로 비운다.
        self.assertGreaterEqual(SF.NAVER_DAYS, 10)

    # ── (i) 1억 미만 추정치 ──
    def test_small_estimate_keeps_its_value_and_sign(self):
        """150주 × 29,250원 = 438.75만원. 억원 1자리에서 자르면 0.0 / -0.0 이 되어
        값이 사라지고, 매도가 '+0만' 으로 나갔다."""
        self._kis_down()
        self._naver_ok([dict(TREND[0], organPureBuyQuant='+150',
                             foreignerPureBuyQuant='-150')])
        out = self.collect(now=KST_2302)
        est = out['by_code']['001']['amt_est']
        self.assertAlmostEqual(est['기관'], 0.0439)
        self.assertAlmostEqual(est['외국인'], -0.0439)
        bit = TG._flow_bit(out['by_code']['001'])
        self.assertIn('기관 +150주(추정 +439만)', bit)
        self.assertIn('외국인 -150주(추정 -439만)', bit)
        self.assertNotIn('+0만', bit)

    def test_negative_zero_is_stored_as_zero(self):
        # -1주 × 100원 = -1e-6억 → 4자리 반올림이 -0.0 을 만든다. 부호 없는 0 으로.
        self._kis_down()
        self._naver_ok([dict(TREND[0], closePrice='100', organPureBuyQuant='-1')])
        out = self.collect(now=KST_2302)
        v = out['by_code']['001']['amt_est']['기관']
        self.assertEqual(v, 0.0)
        self.assertGreater(math.copysign(1, v), 0, '-0.0 이 저장됐다')

    # ── (j) 접속 불가가 몇 종목 뒤에 왔을 때의 배너 ──
    def test_banner_counts_kis_successes_before_the_outage(self):
        """001 KIS 성공, 002 rt_cd 오류 → 네이버 구제, 003 접속 불가, 004 네이버.
        '4종목 → 3종목 성공' 이라 적으면 KIS 로 받은 종목이 실패한 것처럼 읽힌다."""
        day = {'2026-09-17': {'기관': 1.0, '외국인': 2.0, '_unit': '억원'}}
        self._fake({'001': day, '004': day}, errors={'003': CONNECT_TIMEOUT})
        self._naver_ok()
        out = self.collect()
        self.assertEqual(self.kis_calls, ['001', '002', '003'])
        self.assertEqual(self.naver_calls, ['002', '003', '004'])
        self.assertEqual(out['by_code']['001']['source'], 'kis')
        self.assertEqual(out['source'], 'mixed')
        line = out['missing'][0]
        self.assertTrue(line.startswith('KIS 접속 불가('), line)
        self.assertIn('KIS 로 1종목 받은 뒤 2종목 → 네이버 폴백 2종목 성공', line)
        # 접속 불가 앞의 종목 단위 실패 줄은 kis_down 과 무관하게 남는다.
        self.assertTrue(any(m.startswith('KIS 종목 단위 실패 1종목') and 'rt_cd=1' in m
                            for m in out['missing']))
        self.assertTrue(any(m.startswith('네이버 폴백 3종목') for m in out['missing']))

    # ── (k) 파일 머리의 출처는 실제로 쓴 소스 ──
    def test_top_level_source_is_what_was_actually_used(self):
        self._kis_down()
        self._naver_ok()
        self.assertEqual(self.collect(now=KST_2302)['source'], 'naver')
        self.kis_calls.clear()
        day = {'2026-09-17': {'기관': 1.0, '_unit': '억원'}}
        self._fake({'001': day, '002': day, '003': day, '004': day})
        self.assertEqual(self.collect()['source'], 'kis')
        self.assertIsNone(SF.collect(dict(achieved=[]), '2026-09-17',
                                     log=lambda *a: None)['source'])

    # ── (l) 접속 표식 ──
    def test_read_timeout_is_fast_failed_but_named_as_response_timeout(self):
        """붙었는데 답이 없는 것도 종목마다 25초를 기다릴 이유가 없다. 다만
        '연결 시간 초과' 라고 적으면 접속 문제처럼 읽힌다."""
        e = Fetch('https://openapi.koreainvestment.com:9443/x 실패: ReadTimeout: '
                  "HTTPSConnectionPool(host='openapi.koreainvestment.com', port=9443): "
                  'Read timed out. (read timeout=25)')
        self.assertTrue(SF.kis.connect_failed(e))
        self.assertIn('응답 시간 초과', SF.down_reason(e, KST_1720))
        self.assertNotIn('연결 시간 초과', SF.down_reason(e, KST_1720))
        self._kis_down(e)
        self._naver_ok()
        out = self.collect()
        self.assertEqual(len(self.kis_calls), 1)
        self.assertTrue(out['kis_down'].startswith('KIS 접속 불가(응답 시간 초과'))

    def test_http_error_with_timeout_in_the_body_is_not_an_outage(self):
        """504 Gateway Timeout 은 TCP 가 붙은 뒤다. 한 종목의 504 로 전 종목이 KIS 를
        건너뛰면 안 된다 — 그 종목만 네이버로."""
        e = Fetch('https://openapi.koreainvestment.com:9443/x 실패: HTTP 504 · Gateway Timeout')
        self.assertFalse(SF.kis.connect_failed(e))
        day = {'2026-09-17': {'기관': 1.0, '_unit': '억원'}}
        self._fake({'002': day, '003': day, '004': day}, errors={'001': e})
        self._naver_ok()
        out = self.collect()
        self.assertEqual(self.kis_calls, ['001', '002', '003', '004'])
        self.assertIsNone(out['kis_down'])
        self.assertEqual(out['by_code']['001']['source'], 'naver')
        self.assertIn('HTTP 504', out['by_code']['001']['kis_error'])

    def test_kis_tries_twice(self):
        """D-083: 시도 2회(재시도 1회). 1회면 정상 시간대의 일시적 실패 한 번에
        나머지 전 종목이 추정치 폴백으로 간다."""
        self.assertEqual(SF.KIS_RETRIES, 2)


class Probe(unittest.TestCase):
    """--check 의 폴백 프로브. 행이 와도 구분이 없으면 FAIL 이다."""

    def setUp(self):
        self.real = SF.naver_flows

    def tearDown(self):
        SF.naver_flows = self.real

    def test_rows_without_investor_keys_fail(self):
        SF.naver_flows = lambda code: dict(by_date={
            '2026-09-17': {'close': 1.0, 'volume': 1.0}})
        (_, ok, note), = SF.probe()
        self.assertFalse(ok)
        self.assertIn('투자자 구분이 한 행에도 없다', note)
        self.assertIn('close', note)

    def test_valued_rows_pass_and_name_the_person_gap(self):
        SF.naver_flows = lambda code: dict(by_date={
            '2026-09-17': {'close': 1.0, '기관': 5.0, '외국인': -1.0}})
        (_, ok, note), = SF.probe()
        self.assertTrue(ok)
        self.assertIn('기관 +5주', note)
        self.assertIn('개인 없음', note)


class Eok(unittest.TestCase):
    """표기 함수 — 0 에 부호를 붙이지 않는다. -0.0 은 0 이다."""

    def test_zero_has_no_sign(self):
        self.assertEqual(TG._eok(-0.0, '억원'), '0만')
        self.assertEqual(TG._eok(0.0, '억원'), '0만')
        self.assertEqual(TG._eok(-0.0, '주'), '0주')

    def test_small_values_keep_sign_and_value(self):
        self.assertEqual(TG._eok(0.0439, '억원'), '+439만')
        self.assertEqual(TG._eok(-0.0439, '억원'), '-439만')

    def test_render_rule_matches(self):
        from ..web import render as R
        self.assertEqual(R.net_amt(-0.0), '0만')
        self.assertEqual(R.net_amt(-0.0439), '-439만')


class Message(unittest.TestCase):
    """텔레그램 본문 — 52주 이상 신고가 + 종목별 수급."""

    RANK = dict(as_of='2026-09-17', sector_boards=[], stock_boards=[], cross_codes=[])
    FLOWS = dict(as_of='2026-09-17', unit='억원', by_code={
        '001': dict(code='001', name='종가52', as_of='2026-09-17', unit='억원',
                    기관=12.0, 외국인=-3.0, 개인=-9.0),
        '004': dict(code='004', name='섞인것', as_of='2026-09-16', unit='억원',
                    기관=2.0)})

    def msg(self, **kw):
        return TG.rankings_message(self.RANK, newhigh=NEWHIGH, **kw)

    def test_lists_52w_and_above_only(self):
        t = self.msg(stockflows=self.FLOWS)
        for name in ('종가52', '고가만52', '종가역사적', '섞인것'):
            self.assertIn(name, t)
        self.assertNotIn('120일뿐', t)

    def test_flow_line_follows_the_stock(self):
        t = self.msg(stockflows=self.FLOWS)
        self.assertIn('기관 +12.0억', t)
        self.assertIn('외국인 -3.0억', t)

    def test_under_one_eok_keeps_its_value(self):
        """1억 미만을 '억' 으로 반올림하면 `-0억` 이 되어 부호만 남는다."""
        sf = dict(self.FLOWS, by_code=dict(self.FLOWS['by_code'],
                  **{'001': dict(code='001', name='종가52', as_of='2026-09-17',
                                 unit='억원', 기관=-0.04, 외국인=0.62)}))
        t = self.msg(stockflows=sf)
        self.assertIn('기관 -400만', t)
        self.assertIn('외국인 +6,200만', t)
        self.assertNotIn('-0억', t)

    def test_label_matches_why_it_qualified(self):
        """종가 120일·고가 52주인 종목을 '120일' 로 적으면 목록이 틀려 보인다."""
        t = self.msg(stockflows=self.FLOWS)
        line = [x for x in t.split('\n') if '섞인것' in x][0]
        self.assertIn('52주(고가)', line)
        self.assertNotIn('120일', line)

    def test_high_only_stock_is_marked(self):
        line = [x for x in self.msg().split('\n') if '고가만52' in x][0]
        self.assertIn('(고가)', line)

    def test_missing_flow_is_said_not_blank(self):
        t = self.msg(stockflows=self.FLOWS)
        self.assertIn('수급 못 받음', t)

    def test_stale_flow_carries_its_date(self):
        t = self.msg(stockflows=self.FLOWS)
        self.assertIn('2026-09-16 자', t)

    def test_without_flows_no_false_zero(self):
        t = self.msg()
        self.assertNotIn('기관 +0억', t)
        self.assertNotIn('수급 못 받음', t, '수급을 아예 안 받은 날은 그 줄이 없다')

    def test_empty_day_says_so(self):
        t = TG.rankings_message(self.RANK, newhigh=dict(NEWHIGH, achieved=[]))
        self.assertIn('신고가를 낸 종목이 없음', t)

    def test_ranking_still_goes_out_without_newhigh(self):
        self.assertIn('2026-09-17 랭킹', TG.rankings_message(self.RANK))

    def test_empty_stock_boards_do_not_crash(self):
        # 이 가지가 리스트를 돌려주어 메시지가 통째로 죽던 자리다.
        self.assertIn('교차 시그널', TG.rankings_message(self.RANK))


class NaverMessage(unittest.TestCase):
    """(d) 텔레그램 줄 — 출처·추정·사유가 보여야 한다."""

    RANK = dict(as_of='2026-09-17', sector_boards=[], stock_boards=[], cross_codes=[])
    FLOWS = dict(
        as_of='2026-09-17', unit='mixed',
        kis_down='KIS 접속 불가(연결 시간 초과, 23:02 KST) — 23:00 KST 전후 점검 시간대로 추정',
        by_code={
            '001': dict(code='001', name='종가52', as_of='2026-09-17', unit='주',
                        source='naver', 기관=63482.0, 외국인=-12000.0, close=29250.0,
                        amt_est=dict(기관=18.6, 외국인=-3.5, unit='억원',
                                     is_estimate=True, basis='순매매량 × 종가')),
            '003': dict(code='003', name='종가역사적', as_of='2026-09-17', unit='억원',
                        source='kis', 기관=12.0, 외국인=-3.0, 개인=-9.0)},
        failed={'002': 'KIS 접속 불가(연결 시간 초과, 23:02 KST) — 23:00 KST 전후 '
                       '점검 시간대로 추정 · 네이버 폴백도 실패: HTTP 500 · 본문 없음'})

    def msg(self, flows=None):
        return TG.rankings_message(self.RANK, newhigh=NEWHIGH,
                                   stockflows=flows or self.FLOWS)

    def line(self, name, flows=None):
        lines = self.msg(flows).split('\n')
        i = next(i for i, x in enumerate(lines) if name in x)
        return lines[i + 1]

    def test_naver_line_shows_quantity_source_and_estimate(self):
        ln = self.line('종가52')
        self.assertIn('기관 +63,482주', ln)
        self.assertIn('외국인 -12,000주', ln)
        self.assertIn('(네이버)', ln)
        self.assertIn('추정 +18.6억', ln)
        self.assertIn('추정 -3.5억', ln)
        self.assertNotIn('개인', ln, '네이버에 없는 개인이 줄에 생겼다')

    def test_kis_line_has_no_source_tag_and_no_estimate(self):
        ln = self.line('종가역사적')
        self.assertIn('기관 +12.0억', ln)
        self.assertNotIn('네이버', ln)
        self.assertNotIn('추정', ln)

    def test_failed_stock_carries_its_reason(self):
        ln = self.line('고가만52')
        self.assertIn('수급 못 받음 — KIS 접속 불가', ln)
        self.assertIn('네이버 폴백도 실패', ln)
        self.assertNotIn('+0', ln)

    def test_long_reason_is_cut_with_a_mark(self):
        """사유가 길면 자르되, 잘렸다는 것이 보여야 한다. 종목마다 붙는 줄이다."""
        fl = dict(self.FLOWS, failed={'002': 'KIS 접속 불가 · ' + 'x' * 300})
        ln = self.line('고가만52', fl)
        self.assertLessEqual(len(ln), TG.REASON_MAX + 20)
        self.assertTrue(ln.endswith('…'), ln)

    def test_failed_without_reason_still_says_missing(self):
        fl = dict(self.FLOWS, failed={})
        self.assertEqual(self.line('고가만52', fl).strip(), '↳ 수급 못 받음')

    def test_head_explains_naver_marker(self):
        t = self.msg()
        self.assertIn('(네이버) 표시는 순매매량(주)', t)
        self.assertIn('억원은 종가 환산 추정', t)

    def test_estimate_without_flag_is_not_shown_as_estimate(self):
        """is_estimate 가 없는 amt_est 는 규약 위반이다. 추정으로 적지 않고 버린다."""
        fl = dict(self.FLOWS, by_code={'001': dict(
            self.FLOWS['by_code']['001'], amt_est=dict(기관=18.6, unit='억원'))})
        ln = self.line('종가52', fl)
        self.assertIn('기관 +63,482주', ln)
        self.assertNotIn('18.6', ln)


if __name__ == '__main__':
    unittest.main()

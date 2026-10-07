"""KIS 필수 파라미터 자동 보충 — 문서 대신 응답이 알려주는 이름을 읽는다."""
import unittest

from ..ingest import kis


class CallFillTest(unittest.TestCase):

    def setUp(self):
        self.real_get = kis.get
        self.real_session = kis.session
        self.real_headers = kis._headers
        kis.session = lambda *a, **k: _S()
        kis._headers = lambda tr_id: {}
        kis.FILLED.clear()
        self.seen = []

    def tearDown(self):
        kis.get = self.real_get
        kis.session = self.real_session
        kis._headers = self.real_headers
        kis.FILLED.clear()

    def _serve(self, replies):
        """replies: 순서대로 돌려줄 응답. params 스냅샷을 self.seen 에 쌓는다."""
        it = iter(replies)

        def fake(s, url, params=None, retries=None, **kw):
            self.seen.append(dict(params or {}))
            return next(it)
        kis.get = fake

    def test_빠진_필드를_읽어_채운다(self):
        self._serve([
            {'rt_cd': '2', 'msg1': 'ERROR INPUT FIELD NOT FOUND [FID_INPUT_ISCD_2]'},
            {'rt_cd': '0', 'output1': [{'stck_bsop_date': '20260828'}]},
        ])
        rows = kis.call('market_investor', '0001')
        self.assertEqual(len(rows), 1)
        self.assertNotIn('FID_INPUT_ISCD_2', self.seen[0])
        self.assertEqual(self.seen[1]['FID_INPUT_ISCD_2'], '')
        self.assertEqual(kis.FILLED['market_investor'], ['FID_INPUT_ISCD_2'])

    def test_여러_개도_차례로_채운다(self):
        self._serve([
            {'rt_cd': '2', 'msg1': 'ERROR INPUT FIELD NOT FOUND [FID_A]'},
            {'rt_cd': '2', 'msg1': 'ERROR INPUT FIELD NOT FOUND [FID_B]'},
            {'rt_cd': '0', 'output1': [{'x': 1}]},
        ])
        kis.call('market_investor', '0001')
        self.assertEqual(kis.FILLED['market_investor'], ['FID_A', 'FID_B'])

    def test_같은_필드를_또_요구하면_포기한다(self):
        """이미 채운 걸 또 달라고 하면 빈 값이 답이 아니라는 뜻이다. 무한루프 금지."""
        self._serve([
            {'rt_cd': '2', 'msg1': 'ERROR INPUT FIELD NOT FOUND [FID_A]'},
            {'rt_cd': '2', 'msg1': 'ERROR INPUT FIELD NOT FOUND [FID_A]'},
        ])
        with self.assertRaises(kis.Fetch) as e:
            kis.call('market_investor', '0001')
        self.assertIn('FID_A', str(e.exception))

    def test_다른_오류는_그대로_올린다(self):
        self._serve([{'rt_cd': '1', 'msg1': '초당 거래건수를 초과하였습니다'}])
        with self.assertRaises(kis.Fetch) as e:
            kis.call('market_investor', '0001')
        self.assertIn('초당 거래건수', str(e.exception))

    def test_상한을_넘으면_멈춘다(self):
        self._serve([{'rt_cd': '2', 'msg1': f'ERROR INPUT FIELD NOT FOUND [F{i}]'}
                     for i in range(kis.MAX_FILL + 1)])
        with self.assertRaises(kis.Fetch) as e:
            kis.call('market_investor', '0001')
        self.assertIn(str(kis.MAX_FILL), str(e.exception))

    def test_성공하면_아무것도_기록하지_않는다(self):
        self._serve([{'rt_cd': '0', 'output1': [{'x': 1}]}])
        kis.call('market_investor', '0001')
        self.assertEqual(kis.FILLED, {})


class StockFlowsRetriesTest(unittest.TestCase):
    """빠른 실패(D-083)의 절반 — `retries` 가 stock_flows → call → http.get 까지 내려간다.

    어느 한 단계가 이 값을 삼키면 접속 불가 시각에 첫 종목에서 25초 × 기본값을
    도로 기다린다. stockflows 쪽 스텁은 stock_flows 에서 멈추므로 여기서 본다.
    """

    def setUp(self):
        self.real_get = kis.get
        self.real_session = kis.session
        self.real_headers = kis._headers
        kis.session = lambda *a, **k: _S()
        kis._headers = lambda tr_id: {}
        self.seen = []

        def fake(s, url, params=None, retries=None, **kw):
            self.seen.append(retries)
            return {'rt_cd': '0', 'output': [
                {'stck_bsop_date': '20260917', 'stck_clpr': '29250',
                 'orgn_ntby_qty': '100', 'frgn_ntby_qty': '-50'}]}
        kis.get = fake

    def tearDown(self):
        kis.get = self.real_get
        kis.session = self.real_session
        kis._headers = self.real_headers

    def test_retries_reaches_http_get(self):
        kis.stock_flows('005930', retries=1)
        self.assertEqual(self.seen, [1])

    def test_default_is_two_tries(self):
        kis.stock_flows('005930')
        self.assertEqual(self.seen, [2])


class _S:
    headers = {}

    def update(self, *a):
        pass


class MarketFlowsZeroTest(unittest.TestCase):
    """전부 0 인 수급은 사실이 아니라 실패한 질의다.

    필수 파라미터를 빈 값으로 자동 보충하면 호출은 200 으로 성공하면서 0 만
    돌아온다. 그대로 내보내면 화면에 '기관계 +0억원' 이 사실처럼 찍힌다.
    """

    def setUp(self):
        self.real_call = kis.call
        kis.FILLED.clear()

    def tearDown(self):
        kis.call = self.real_call
        kis.FILLED.clear()

    def _rows(self, p, f, o, date='20260828'):
        kis.call = lambda *a, **k: [dict(
            stck_bsop_date=date, prsn_ntby_tr_pbmn=p,
            frgn_ntby_tr_pbmn=f, orgn_ntby_tr_pbmn=o)]

    def test_전부_0이면_실패로_올린다(self):
        self._rows('0', '0', '0')
        with self.assertRaises(kis.Fetch) as e:
            kis.market_flows('0001')
        self.assertIn('전부 0', str(e.exception))

    def test_보충한_파라미터를_사유에_적는다(self):
        self._rows('0', '0', '0')
        kis.FILLED['market_investor'] = ['FID_INPUT_ISCD_2']
        with self.assertRaises(kis.Fetch) as e:
            kis.market_flows('0001')
        self.assertIn('FID_INPUT_ISCD_2', str(e.exception))

    def test_하나라도_값이_있으면_통과(self):
        self._rows('0', '-523000', '0')
        r = kis.market_flows('0001')
        self.assertEqual(r['by_date']['2026-08-28']['외국인'], -5230.0)

    def test_단위는_백만원에서_억원으로(self):
        self._rows('100000', '-523000', '423000')
        v = kis.market_flows('0001')['by_date']['2026-08-28']
        self.assertEqual(v['개인'], 1000.0)
        self.assertEqual(v['기관계'], 4230.0)

    def test_최신일만_본다(self):
        """과거 어느 날이 0 이어도(휴장 등) 최신일에 값이 있으면 쓴다."""
        kis.call = lambda *a, **k: [
            dict(stck_bsop_date='20260827', prsn_ntby_tr_pbmn='0',
                 frgn_ntby_tr_pbmn='0', orgn_ntby_tr_pbmn='0'),
            dict(stck_bsop_date='20260828', prsn_ntby_tr_pbmn='100000',
                 frgn_ntby_tr_pbmn='-523000', orgn_ntby_tr_pbmn='423000')]
        r = kis.market_flows('0001')
        self.assertIn('2026-08-28', r['by_date'])


class ParamCandidateTest(unittest.TestCase):
    """파라미터 후보 — 날짜를 채운 조합이 먼저 와야 한다."""

    def test_후보에_날짜가_모두_들어간다(self):
        for label, p in kis.market_param_candidates('0001'):
            self.assertTrue(p['FID_INPUT_DATE_1'], label)
            self.assertTrue(p['FID_INPUT_DATE_2'], label)

    def test_날짜는_8자리_숫자다(self):
        _, p = kis.market_param_candidates('0001')[0]
        for k in ('FID_INPUT_DATE_1', 'FID_INPUT_DATE_2'):
            self.assertRegex(p[k], r'^\d{8}$')

    def test_시장코드가_후보에_들어간다(self):
        """FID_INPUT_ISCD 가 무엇을 가리키는지가 마지막 열린 축이다 (D-081).

        시장코드 말고 '0000'(전체)·'2001'(코스피200)도 함께 눌러 본다. 그래서
        '모든 후보가 시장코드' 는 더 이상 참이 아니다 — 시장코드가 **후보에
        들어 있는지**를 본다.
        """
        iscd = {p['FID_INPUT_ISCD'] for _, p in kis.market_param_candidates('1001')}
        self.assertIn('1001', iscd)
        self.assertGreater(len(iscd), 1, 'ISCD 축을 한 값으로만 보면 안 된다')

    def test_후보가_서로_다르다(self):
        seen = [tuple(sorted(p.items())) for _, p in kis.market_param_candidates()]
        self.assertEqual(len(seen), len(set(seen)), '같은 조합을 두 번 부르지 않는다')

    def test_빈_ISCD_조합도_남긴다(self):
        """지금 쓰는 조합(빈 값)도 후보에 있어야 비교가 된다."""
        empties = [l for l, p in kis.market_param_candidates()
                   if p['FID_INPUT_ISCD_2'] == '']
        self.assertTrue(empties)


class MarketParamProbeTest(unittest.TestCase):
    """시장 수급 파라미터는 지어내지 않고 격자로 실측한다 (CLAUDE.md 9장 2번).

    빈 값으로 부르면 호출은 200 으로 성공하면서 0 만 돌아온다(D-037). 그 0 이
    화면에 '기관계 +0억원' 으로 찍히면 안 된다.
    """

    def test_시장구분은_U_로_확정됐다(self):
        """2026-09-17 실측에서 'J' 는 rt_cd=2 INVALID 였다. 다시 부르지 않는다."""
        from ..ingest import kis
        divs = {p['FID_COND_MRKT_DIV_CODE']
                for _, p in kis.market_param_candidates('0001')}
        self.assertEqual(divs, {'U'})

    def test_날짜_축은_닫혔다(self):
        """당일~당일과 30일~당일이 같은 300행을 준다 — 이 TR 은 날짜를 무시한다.

        흔들 이유가 없는 축을 계속 흔들면 격자만 커지고 호출 한도를 먹는다.
        """
        from ..ingest import kis
        cands = kis.market_param_candidates('0001')
        self.assertEqual(len({p['FID_INPUT_DATE_1'] for _, p in cands}), 1)

    def test_아직_모르는_ISCD_축을_흔든다(self):
        from ..ingest import kis
        cands = kis.market_param_candidates('0001')
        self.assertGreaterEqual(len({p['FID_INPUT_ISCD'] for _, p in cands}), 2)
        self.assertGreaterEqual(len({p['FID_INPUT_ISCD_1'] for _, p in cands}), 2)
        self.assertGreaterEqual(len({p['FID_INPUT_ISCD_2'] for _, p in cands}), 2)

    def test_합격_판정이_주체별_칼럼까지_본다(self):
        """집계 세 키만 보면 증권·보험·투신만 채워져 온 응답을 놓친다."""
        import inspect

        from ..ingest import kis
        src = inspect.getsource(kis.probe_market_params)
        self.assertIn("'ntby' in k", src)

    def test_응답_원문을_적는다(self):
        """rt_cd=0 인데 값이 0 이면 답은 파라미터가 아니라 응답 안에 있다."""
        from ..ingest import kis
        rows = [dict(stck_bsop_date='20260917', prsn_ntby_tr_pbmn='0',
                     frgn_ntby_tr_pbmn='0', some_other_key='123456')]
        got = dict((n, note) for n, ok, note in kis.describe_rows(rows))
        self.assertIn('some_other_key', got['원문 키'])
        self.assertIn('some_other_key=123456', got['값이 0 이 아닌 키 (전체)'])

    def test_순매수_키를_따로_뽑는다(self):
        """응답에 지수 시세가 잔뜩 섞여 있어 전체 목록에서는 밀려난다."""
        from ..ingest import kis
        rows = [dict(bstp_nmix_prpr='6715.41', prsn_ntby_tr_pbmn='0',
                     bank_ntby_tr_pbmn='-12345')]
        got = dict((n, note) for n, ok, note in kis.describe_rows(rows))
        self.assertIn('bank_ntby_tr_pbmn=-12345', got['순매수 키 · 값 있음'])
        self.assertIn('prsn_ntby_tr_pbmn', got['순매수 키 · 전부 0'])
        self.assertNotIn('bstp_nmix_prpr', got['순매수 키 · 값 있음'])

    def test_전부_0_이면_그렇게_적는다(self):
        """'값이 있는 키가 없다' 도 사실이다. 조용히 빈 줄을 내지 않는다."""
        from ..ingest import kis
        rows = [dict(a='0', b='0')]
        names = {n: ok for n, ok, _ in kis.describe_rows(rows)}
        self.assertFalse(names['값이 0 이 아닌 키 (전체)'])

    def test_원문_줄은_자르지_않는다(self):
        import inspect

        from .. import run
        self.assertIn("label.startswith('[원문]')",
                      inspect.getsource(run.cmd_kis_probe))

    def test_응답이_알려준_두_필드가_모든_후보에_있다(self):
        from ..ingest import kis
        for label, p in kis.market_param_candidates('0001'):
            self.assertIn('FID_INPUT_ISCD_2', p, label)
            self.assertIn('FID_INPUT_DATE_2', p, label)

    def test_호출_상한이_있다(self):
        """KIS 는 유량 제한이 있다. 격자가 늘어도 한 번에 다 부르지 않는다."""
        from ..ingest import kis
        self.assertLessEqual(len(kis.market_param_candidates('0001')[:kis.MAX_PROBE_CALLS]),
                             kis.MAX_PROBE_CALLS)

    def test_결과_블록_후보를_전부_열어_본다(self):
        """어느 out 키로 오는지도 모른다. output 만 보면 빈 결과로 오판한다."""
        from ..ingest import kis
        self.assertIn('output', kis.OUT_KEYS)
        self.assertIn('output1', kis.OUT_KEYS)
        self.assertIn('output2', kis.OUT_KEYS)

    def test_합격_판정에_상쇄_검사가_있다(self):
        """셋이 0 이 아닌 것만으로는 부족하다 — 엉뚱한 블록도 숫자는 채워져 있다."""
        import inspect

        from ..ingest import kis
        src = inspect.getsource(kis.probe_market_params)
        self.assertIn('balanced', src)

    def test_진단_모드가_배선돼_있다(self):
        import inspect

        from .. import run
        # 플래그 정의는 build_parser, 배선은 main 이다 — 둘을 갈라 둔 뒤로
        # 한쪽만 보면 '플래그는 있는데 아무 일도 안 하는' 모드를 못 잡는다.
        self.assertIn('--kis-probe', inspect.getsource(run.build_parser))
        self.assertIn('cmd_kis_probe', inspect.getsource(run.main))
        self.assertIn('probe_market_params', inspect.getsource(run.cmd_kis_probe))

    def test_격자_전체를_보고_멈추지_않는다(self):
        """첫 합격에서 멈추면 그게 우연인지 알 수 없다."""
        import inspect

        from .. import run
        self.assertIn('stop_on_hit=False', inspect.getsource(run.cmd_kis_probe))

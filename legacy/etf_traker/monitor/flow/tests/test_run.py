"""
러너·차트·전송 배선 시험.

외부 호출은 전부 막고 **모양과 연결**만 본다. 숫자의 옳고 그름은
test_analyze.py 가 골든으로 본다.
"""
import json
import os
import shutil
import tempfile
import unittest

from .. import analyze as A
from .. import chart as C
from .. import narrative as N
from .. import run as R
from .. import telegram as T

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'golden.json')


def report():
    with open(FIX, encoding='utf-8') as f:
        g = json.load(f)
    return A.analyze(g['code'], g['name'], g['amt'], g['qty'], g['totals'],
                     g['closes'], g['dates'], g['baseDate'])


def basis_row(code, name, kind='w52', hi=True, cl=True, vol_mult=9.0):
    """보드가 내보내는 한 줄. 두 기준을 이름으로 담는다.

    `hits`/`label` 은 보드의 기본 기준이고 지금 그것은 **종가**다
    (board/config/settings.yaml `default_basis: close`).
    """
    on = {kind: True}
    return {'code': code, 'name': name, 'vol_mult': vol_mult,
            'mktcap': 32000.0, 'turnover': 1840.0,
            'label': kind if cl else None,
            'hits': on if cl else {},
            'close_basis': {'label': kind if cl else None, 'hits': on if cl else {}},
            'high_basis': {'label': kind if hi else None, 'hits': on if hi else {}}}


def w52(code, name, close_only=False, vol_mult=9.0):
    """52주 신고가가 (종가 기준으로) 선 종목.

    `vol_mult` 는 보드가 재 둔 당일 거래량 ÷ 20일 평균이다. 기본값은 문턱을
    넉넉히 넘겨 둔다 — 라벨을 보는 시험이 거래량 때문에 갈리지 않게.
    """
    return basis_row(code, name, hi=not close_only, cl=True, vol_mult=vol_mult)


def candles(n=20):
    """네이버 일봉 모양의 한 구간. 오름/내림이 섞이게 만든다."""
    out, px = [], 10000.0
    for i in range(n):
        o = px
        c = px * (1.03 if i % 3 else 0.97)
        out.append(dict(asof=f'2026-08-{i + 1:02d}', open=o, close=c,
                        high=max(o, c) * 1.01, low=min(o, c) * 0.99,
                        volume=100000 + i * 1000))
        px = c
    return out


def codes(nh, *a, **kw):
    """고른 종목코드만."""
    return [c for c, _, _ in R.pick_targets(nh, *a, **kw)[0]]


class 대상고르기(unittest.TestCase):

    def test_문턱을_걸면_많이_는_순서로_집는다(self):
        # 문턱을 걸 때만 거래량으로 다시 세운다. 기본(문턱 0)은 보드 순서다.
        nh = {'achieved': [w52('A', '가', vol_mult=2.5),
                           w52('B', '나', vol_mult=8.0),
                           w52('C', '다', vol_mult=4.0)]}
        self.assertEqual(codes(nh, 2, vol_min=2.0), ['B', 'C'])

    def test_기본은_보드_순서를_그대로_쓴다(self):
        nh = {'achieved': [w52('A', '가', vol_mult=2.5),
                           w52('B', '나', vol_mult=8.0),
                           w52('C', '다', vol_mult=4.0)]}
        self.assertEqual(codes(nh, 2), ['A', 'B'])

    def test_같은_배수면_보드_순서를_지킨다(self):
        nh = {'achieved': [w52('A', '가', vol_mult=3.0),
                           w52('B', '나', vol_mult=3.0)]}
        self.assertEqual(codes(nh, 2), ['A', 'B'])

    def test_중복_종목은_한_번만(self):
        nh = {'achieved': [w52('A', '가'), w52('A', '가')]}
        self.assertEqual(len(codes(nh, 5)), 1)

    def test_비었으면_빈_목록(self):
        self.assertEqual(R.pick_targets(None)[0], [])
        self.assertEqual(R.pick_targets({})[0], [])

    def test_코드가_없는_행은_건너뛴다(self):
        nh = {'achieved': [dict(w52('', '이름만'), code=None), w52('B', '나')]}
        got = R.pick_targets(nh, 5)[0]
        self.assertEqual([(c, n) for c, n, _ in got], [('B', '나')])

    def test_어느_기준으로_선_신고가인지_적는다(self):
        self.assertEqual(R.basis_of(basis_row('A', '가', hi=True, cl=False), 'w52'), '고가')
        self.assertEqual(R.basis_of(basis_row('B', '나', hi=False, cl=True), 'w52'), '종가')
        self.assertEqual(R.basis_of(basis_row('C', '다'), 'w52'), '고가·종가')
        self.assertIsNone(R.basis_of(basis_row('D', '라', kind='d60'), 'w52'))

    def test_고가로만_선_종목은_대상이_아니다(self):
        """장중에 잠깐 뚫고 종가는 밀린 날이다. 받아 보면 '일간 마이너스인데
        신고가' 가 되어 매번 멈춰서 따져 보게 된다."""
        nh = {'achieved': [basis_row('H', '고가만', hi=True, cl=False),
                           basis_row('C', '종가도', hi=True, cl=True)]}
        self.assertEqual(codes(nh, 9), ['C'])

    def test_기준이_meta_에_실린다(self):
        (_, _, meta), = R.pick_targets({'achieved': [w52('A', '가')]}, 1)[0]
        self.assertEqual(meta['basis'], '고가·종가')

    def test_시총과_거래대금을_들고_온다(self):
        """보드가 이미 잰 값이다. 여기서 다시 계산하면 화면과 갈린다."""
        (_, _, meta), = R.pick_targets({'achieved': [w52('A', '가')]}, 1)[0]
        self.assertEqual(meta['mktcap'], 32000.0)
        self.assertEqual(meta['turnover'], 1840.0)
        self.assertFalse(meta['turnover_est'])

    def test_추정_거래대금은_그렇게_표시된다(self):
        row = dict(w52('A', '가'), turnover_is_estimate=True)
        (_, _, meta), = R.pick_targets({'achieved': [row]}, 1)[0]
        self.assertTrue(meta['turnover_est'])

    def test_기본_상한은_한_자릿수_신고가를_다_담는다(self):
        # 52주 이상은 보통 하루 한 자릿수다. 이 수에 먼저 닿는 일이 드물어야 한다.
        self.assertGreaterEqual(R.TOP_N, 10)


class 거래량문턱(unittest.TestCase):
    """신고가만으로 고르면 '조용히 신고가를 낸' 종목이 섞인다."""

    def setUp(self):
        self.nh = {'achieved': [
            w52('A', '급증', vol_mult=6.1),
            w52('B', '보통', vol_mult=1.2),
            w52('C', '딱걸침', vol_mult=2.0),
            w52('D', '모름', vol_mult=None),
        ]}

    def test_평소와_같으면_뺀다(self):
        self.assertNotIn('B', codes(self.nh, 9, vol_min=2.0))

    def test_문턱과_같으면_넣는다(self):
        self.assertIn('C', codes(self.nh, 9, vol_min=2.0))

    def test_거래량을_모르면_뺀다(self):
        """0 배로 치면 없는 사실을 지어내는 것이다."""
        self.assertNotIn('D', codes(self.nh, 9, vol_min=2.0))

    def test_왜_빠졌는지_센다(self):
        _, st = R.pick_targets(self.nh, 9, vol_min=2.0)
        self.assertEqual(st['achieved'], 4)
        self.assertEqual(st['below'], 1)
        self.assertEqual(st['no_vol'], 1)
        self.assertEqual(st['passed'], 2)

    def test_문턱을_끄면_보드_순서_그대로(self):
        """0 이면 거래량을 보지 않는다 — 모르는 종목도 그대로 둔다."""
        self.assertEqual(codes(self.nh, 9, vol_min=0), ['A', 'B', 'C', 'D'])

    def test_문턱을_높이면_줄어든다(self):
        self.assertEqual(codes(self.nh, 9, vol_min=5.0), ['A'])

    def test_기본은_문턱이_없다(self):
        """신고가를 낸 종목의 수급은 빠짐없이 본다 — 거래량으로 거르지 않는다.

        2.0 이 기본이던 때 2026-09-17 에 6종목 중 2종목만 남았고, 나머지 넷은
        신고가를 냈는데도 수급이 한 줄도 안 갔다.
        """
        self.assertEqual(R.VOL_MULT_MIN, 0.0)
        self.assertEqual(codes(self.nh, 9), ['A', 'B', 'C', 'D'])


class 어느_신고가인가(unittest.TestCase):
    """기본은 52주다. 보드 알약과 같은 규약으로 고른다."""

    def setUp(self):
        hist = basis_row('H', '역사적', vol_mult=9.0)
        hist['hits'] = hist['close_basis']['hits'] = {'hist': True, 'w52': True}
        self.nh = {'achieved': [
            hist,
            w52('W', '오십이주', vol_mult=8.0),
            basis_row('D', '육십일', kind='d60', vol_mult=7.0),
        ]}

    def test_기본은_52주다(self):
        self.assertEqual(codes(self.nh, 9), ['H', 'W'])

    def test_역사적_신고가도_52주에_든다(self):
        """역사적으로 최고면 52주로도 최고다. 빼면 보드의 '52주 N' 과 갈린다."""
        self.assertIn('H', codes(self.nh, 9, kind='w52'))

    def test_60일만_선_종목은_빠진다(self):
        self.assertNotIn('D', codes(self.nh, 9, kind='w52'))

    def test_종가_기준으로만_뚫어도_대상이다(self):
        """보드가 두 기준을 다 담는다. 고가만 보면 종가로 뚫은 종목이 사라진다."""
        nh = {'achieved': [w52('C', '종가만', close_only=True)]}
        self.assertEqual(codes(nh, 9), ['C'])

    def test_all_이면_거르지_않는다(self):
        self.assertEqual(len(codes(self.nh, 9, kind='all')), 3)

    def test_60일도_고를_수_있다(self):
        self.assertEqual(codes(self.nh, 9, kind='d60'), ['D'])


class 조회구간(unittest.TestCase):

    def test_거래일수보다_넉넉히_잡는다(self):
        import datetime as dt
        s, e = R.trading_window(20, end=dt.date(2026, 9, 14))
        self.assertEqual(e, '2026-09-14')
        span = (dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days
        self.assertGreater(span, 20, '주말·공휴일 여유가 없다')


class 차트(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rep = report()
        cls.tmp = tempfile.mkdtemp()
        cls.made = C.render_all(cls.rep, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_네_장이_만들어진다(self):
        self.assertEqual(len(self.made), 4)
        for p in self.made:
            self.assertTrue(os.path.exists(p))
            self.assertGreater(os.path.getsize(p), 5000, os.path.basename(p))

    def test_한글_폰트를_실제로_확인하고_쓴다(self):
        """글리프 확인 없이 쓰면 라벨이 조용히 두부(□)가 된다."""
        path = C.korean_font()
        self.assertTrue(C._covers_hangul(path), path)

    def test_주체마다_색이_고정이다(self):
        """계열 수가 달라져도 색이 옮겨 다니면 두 차트를 나란히 못 읽는다."""
        self.assertEqual(len(set(C.COLOR.values())), len(C.COLOR))
        for who in ('개인', '외국인', '기관합계'):
            self.assertIn(who, C.COLOR)

    def test_일봉이_있으면_캔들을_그린다(self):
        """기준일=100 선은 기준일이 매일 밀려 어제 그림과 겹쳐 읽을 수 없었다."""
        rep = dict(self.rep, ohlcv=candles())
        tmp = tempfile.mkdtemp()
        try:
            p = C.price_chart(rep, os.path.join(tmp, 'p.png'))
            self.assertTrue(p and os.path.getsize(p) > 5000)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_일봉을_못_받으면_종가_선으로_물러선다(self):
        rep = dict(self.rep, ohlcv=[])
        tmp = tempfile.mkdtemp()
        try:
            p = C.price_chart(rep, os.path.join(tmp, 'p.png'))
            self.assertTrue(p and os.path.getsize(p) > 5000)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_가격이_아예_없으면_그리지_않는다(self):
        """빈 축을 보내면 '가격이 없었다' 로 읽힌다."""
        rep = dict(self.rep, ohlcv=[], closes={})
        tmp = tempfile.mkdtemp()
        try:
            self.assertIsNone(C.price_chart(rep, os.path.join(tmp, 'p.png')))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_오르면_빨강_내리면_파랑(self):
        """국내 관행이다. 뒤집으면 매번 범례를 찾아야 한다."""
        self.assertNotEqual(C.UP, C.DOWN)

    def test_움직임_없는_기관구분은_안_그린다(self):
        rep = dict(self.rep)
        rep['cum'] = {k: [(d, 0.0) for d, _ in v] for k, v in self.rep['cum'].items()}
        tmp = tempfile.mkdtemp()
        try:
            self.assertIsNone(C.inst_chart(rep, os.path.join(tmp, 'x.png')))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class 본문(unittest.TestCase):

    def setUp(self):
        self.rep = report()
        self.text = T.compose(self.rep, N.lines(self.rep), N.numbers(self.rep))

    def test_제목과_기준이_들어간다(self):
        # KBJ P1: 골든이 합성 종목(가상전자 999990)으로 바뀌었다.
        self.assertIn('가상전자', self.text)
        self.assertIn('999990', self.text)
        self.assertIn('20거래일', self.text)
        self.assertIn('종가=100', self.text)

    def test_하단_문구는_기본으로_붙이지_않는다(self):
        """사용자가 빼 달라고 했다(본인 대화방). 사실은 데이터와 로그에 남는다."""
        self.assertFalse(T.FOOTER)
        for l in A.LIMITS:
            self.assertNotIn(l, self.text)

    def test_붙이기로_하면_그대로_나온다(self):
        """다른 사람에게 보낼 때를 위해 스위치는 살려 둔다."""
        T.FOOTER = True
        try:
            text = T.compose(self.rep, N.lines(self.rep), N.numbers(self.rep))
        finally:
            T.FOOTER = False
        for l in A.LIMITS:
            self.assertIn(l, text)

    def test_표는_코드블록에_넣는다(self):
        self.assertEqual(self.text.count('```'), 2)

    def test_고른_이유를_적는다(self):
        rep = dict(self.rep, pick=dict(kind='w52', label='52주', vol_mult=4.2))
        text = T.compose(rep, N.lines(rep), N.numbers(rep))
        self.assertIn('52주 신고가', text)
        self.assertIn('거래량 20일 평균의 4.2배', text)

    def test_시총과_시총_대비_거래대금을_적는다(self):
        """같은 1,000억도 시총 3천억이면 손이 통째로 바뀐 것이다."""
        rep = dict(self.rep, pick=dict(label='52주', vol_mult=4.2,
                                       mktcap=32000.0, turnover=1840.0))
        text = T.compose(rep, N.lines(rep), N.numbers(rep))
        self.assertIn('시총 3.20조', text)
        self.assertIn('거래대금 1,840억', text)
        self.assertIn('시총의 5.8%', text)

    def test_추정_거래대금은_추정이라고_적는다(self):
        rep = dict(self.rep, pick=dict(label='52주', mktcap=1820.0,
                                       turnover=610.0, turnover_est=True))
        self.assertIn('(추정)', T.compose(rep, N.lines(rep), N.numbers(rep)))

    def test_시총을_모르면_비율을_적지_않는다(self):
        rep = dict(self.rep, pick=dict(label='52주', turnover=610.0))
        text = T.compose(rep, N.lines(rep), N.numbers(rep))
        self.assertIn('거래대금 610억', text)
        self.assertNotIn('시총의', text)

    def test_기준일이_다르면_말한다(self):
        """신고가는 보드가 어제 판정하고 수급은 오늘치가 없을 수 있다."""
        rep = dict(self.rep, pick=dict(label='52주', asof='2026-09-15'),
                   dates=['20260914'])
        self.assertIn('기준일이 다르다',
                      T.compose(rep, N.lines(rep), N.numbers(rep)))

    def test_같은_날이면_그_줄이_없다(self):
        rep = dict(self.rep, pick=dict(label='52주', asof='2026-09-14'),
                   dates=['20260914'])
        self.assertNotIn('기준일이 다르다',
                         T.compose(rep, N.lines(rep), N.numbers(rep)))

    def test_조_단위는_조로_적는다(self):
        self.assertEqual(T.won(32000.0), '3.20조')
        self.assertEqual(T.won(1820.0), '1,820억')
        self.assertIsNone(T.won(None))

    def test_이유가_없으면_그_줄도_없다(self):
        """--codes 로 종목을 직접 준 경우다. 없는 근거를 지어내지 않는다."""
        self.assertNotIn('거래량 20일 평균', self.text)


class 대상이_없는_날(unittest.TestCase):
    """문턱을 걸었는데 넘은 종목이 없는 날. 사고가 아니다 — 그래도 말은 한다.

    기본값에는 문턱이 없으므로 이 상황은 손으로 `--vol-mult` 를 줄 때만 난다.
    """

    def setUp(self):
        self.nh = {'asof': '20260915',
                   'achieved': [w52('A', '조용', vol_mult=1.1)]}
        self.sent = []
        self._nh, self._send = R.latest_newhigh, T.send_text
        R.latest_newhigh = lambda *a, **k: self.nh
        T.send_text = lambda text, **k: (self.sent.append(text), (True, 'ok'))[1]

    def tearDown(self):
        R.latest_newhigh, T.send_text = self._nh, self._send

    def test_아무것도_안_보내고_끝내지_않는다(self):
        n = R.run(log=lambda *a: None, vol_min=2.0)
        self.assertEqual(n, 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('대상 없음', self.sent[0])

    def test_dry_run_이면_보내지_않는다(self):
        R.run(dry=True, log=lambda *a: None, vol_min=2.0)
        self.assertEqual(self.sent, [])

    def test_보드_기준일을_읽는다(self):
        """키 이름은 as_of 다. asof 로 읽어 여태 '보드 ?' 로 찍혔다."""
        self.nh = {'as_of': '2026-09-15', 'achieved': [w52('A', '가')]}
        seen = {}
        real = R.build_one
        R.build_one = lambda code, name, **k: (
            {'code': code, 'name': name, 'dates': ['20260914'],
             'main': [], 'verified': True}, 'kis')
        keep = (R.C.render_all, R.T.compose, R.N.lines, R.N.numbers)
        R.C.render_all = lambda rep, out: []
        R.N.lines = R.N.numbers = lambda rep: ''
        R.T.compose = lambda rep, *a: seen.update(rep.get('pick') or {}) or ''
        try:
            R.run(dry=True, log=lambda *a: None)
        finally:
            R.build_one = real
            R.C.render_all, R.T.compose, R.N.lines, R.N.numbers = keep
        self.assertEqual(seen.get('asof'), '2026-09-15')

    def test_달성_자체가_0이면_죽는다(self):
        """목록을 못 꺼낸 것과 오늘 없는 것은 다르다."""
        self.nh = {'achieved': []}
        with self.assertRaises(SystemExit):
            R.run(log=lambda *a: None)


class 전송(unittest.TestCase):

    def test_파일이_없으면_보냈다고_하지_않는다(self):
        ok, why = T.send_photos(['/없는/경로.png'], token='t', chat_id='c')
        self.assertFalse(ok)
        self.assertIn('없다', why)

    def test_보낼_그림이_없으면_실패다(self):
        ok, why = T.send_photos([], token='t', chat_id='c')
        self.assertFalse(ok)

    def test_토큰이_없으면_실패다(self):
        ok, why = T.send_photos(['x'], token='', chat_id='')
        self.assertFalse(ok)
        self.assertIn('TELEGRAM_BOT_TOKEN', why)


class 검산차단(unittest.TestCase):

    def test_검산이_깨지면_리포트를_쓰지_않는다(self):
        """틀린 숫자를 사실처럼 보내느니 그 종목을 건너뛴다."""
        with open(FIX, encoding='utf-8') as f:
            g = json.load(f)
        totals = json.loads(json.dumps(g['totals']))
        totals['기관합계']['net_amt'] += 1_000_000     # 일부러 어긋낸다
        rep = A.analyze(g['code'], g['name'], g['amt'], g['qty'], totals,
                        g['closes'], g['dates'], g['baseDate'])
        self.assertFalse(rep['ok'])
        bad = [c for c in rep['checks'] if not c[2]]
        self.assertTrue(bad)


if __name__ == '__main__':
    unittest.main()

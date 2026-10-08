"""'종가' 는 KRX 정규장 종가다 (D-080).

네이버가 16:07 에 주는 당일 값은 종가가 아니다. NXT 애프터마켓(15:30~20:00)·
시간외 단일가가 끝날 때까지 움직이는 잠정치다. 2026-09-01 state 대조에서
유니버스 2,686종목 중 2,369종목(88.2%)이 달랐고 달성 표 안에서만 14건이
종가 기준 라벨을 바꿀 크기였다.

여기서 막으려는 것은 네 가지다.
  1. 확정치를 받고도 당일 봉을 안 덮는 것 — 수집이 조용히 옛 규칙으로 돈다
  2. 아직 안 나온 어제 확정치로 오늘 스냅을 덮는 것 — 보드가 조용히 어제가 된다
  3. 못 받은 날에 '확정' 이라고 적는 것 — 잠정치를 사실로 단정한다 (CLAUDE.md 2장 1번)
  4. 스칼라를 굴린 **뒤에** 덮는 것 — 사상 최고가가 잠정치로 굳는다
"""
import unittest

from ..engine import build as B
from ..engine import db as DB
from ..ingest import krx
from ..ingest import pipeline as P


def _day(session='2026-09-01', **rows):
    return dict(session=session, by_code=rows)


def _k(close, open=None, high=None, low=None, volume=None,
       turnover=None, mktcap=None, chg_pct=None):
    return dict(code='X', name='X', open=open, high=high, low=low, close=close,
                volume=volume, turnover=turnover, mktcap=mktcap, chg_pct=chg_pct,
                asof='2026-09-01', source=krx.SOURCE)


class SourceConstantTest(unittest.TestCase):
    """engine 은 ingest 를 import 하지 않는다. 그래서 문자열이 두 군데 있다."""

    def test_engine_과_ingest_가_같은_문자열을_쓴다(self):
        self.assertEqual(B.KRX_SOURCE, krx.SOURCE)


class BarOverrideTest(unittest.TestCase):

    def setUp(self):
        self.stat = dict(px_fixed=0, px_added=0)
        self.rows = [
            dict(asof='2026-08-31', open=90, high=95, low=89, close=94, volume=10),
            dict(asof='2026-09-01', open=95, high=105, low=94, close=103, volume=20),
        ]

    def test_당일_종가를_확정치로_덮는다(self):
        day = _day(X=_k(close=100, high=105, low=94, open=95, volume=21))
        out = P._apply_krx_bar('X', self.rows, day, self.stat)
        self.assertEqual(out[-1]['close'], 100)
        self.assertEqual(out[-1]['source'], krx.SOURCE)
        self.assertEqual(self.stat['px_fixed'], 1)

    def test_전일_봉은_건드리지_않는다(self):
        day = _day(X=_k(close=100))
        out = P._apply_krx_bar('X', self.rows, day, self.stat)
        self.assertEqual(out[0]['close'], 94)
        self.assertEqual(out[0].get('source'), None)

    def test_소스가_못_준_칸은_원래_값을_남긴다(self):
        """None 으로 덮으면 일봉에 구멍이 뚫린다."""
        day = _day(X=_k(close=100))          # 시·고·저·거래량 없음
        out = P._apply_krx_bar('X', self.rows, day, self.stat)
        self.assertEqual(out[-1]['high'], 105)
        self.assertEqual(out[-1]['volume'], 20)

    def test_차이가_반올림_수준이면_세지_않는다(self):
        day = _day(X=_k(close=103))
        P._apply_krx_bar('X', self.rows, day, self.stat)
        self.assertEqual(self.stat['px_fixed'], 0)

    def test_네이버에_당일_봉이_없으면_채운다(self):
        rows = self.rows[:1]
        day = _day(X=_k(close=100))
        out = P._apply_krx_bar('X', rows, day, self.stat)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[-1]['asof'], '2026-09-01')
        self.assertEqual(out[-1]['high'], 100)   # 못 받은 칸은 종가로 채운다
        self.assertEqual(self.stat['px_added'], 1)

    def test_확정치에_없는_종목은_그대로_둔다(self):
        out = P._apply_krx_bar('Y', self.rows, _day(X=_k(close=100)), self.stat)
        self.assertEqual(out[-1]['close'], 103)

    def test_스칼라를_굴리기_전에_덮는다(self):
        """뒤에 덮으면 사상 최고가가 잠정 종가로 굳은 채 이튿날에도 안 고쳐진다."""
        import inspect
        src = inspect.getsource(P.sync_px)
        self.assertLess(src.index('_apply_krx_bar'), src.index('roll_alltime'))


class SnapshotOverrideTest(unittest.TestCase):

    def test_시총_거래대금까지_확정치로_덮는다(self):
        uni = {'X': dict(code='X', name='엑스', close=103, chg_pct=9.5, volume=20,
                         turnover=1.0, mktcap=500, turnover_is_estimate=True,
                         source='naver')}
        day = _day(X=_k(close=100, volume=21, turnover=2.5, mktcap=510, chg_pct=6.4))
        n, changed, big = P._apply_krx_snapshot(uni, day)
        x = uni['X']
        self.assertEqual((n, changed), (1, 1))
        self.assertEqual(x['close'], 100)
        self.assertEqual(x['chg_pct'], 6.4)
        self.assertEqual(x['turnover'], 2.5)
        self.assertFalse(x['turnover_is_estimate'])
        self.assertEqual(x['source'], krx.SOURCE)
        self.assertEqual(big, [])            # 2.9% — 3% 예시에는 안 든다

    def test_3퍼센트를_넘으면_예시로_남긴다(self):
        uni = {'X': dict(code='X', name='엑스', close=100, source='naver')}
        n, changed, big = P._apply_krx_snapshot(uni, _day(X=_k(close=95)))
        self.assertEqual(changed, 1)
        self.assertEqual(len(big), 1)
        self.assertIn('엑스', big[0])

    def test_확정치에_없는_종목은_네이버_값으로_남는다(self):
        uni = {'Y': dict(code='Y', name='와이', close=100, source='naver')}
        n, changed, _ = P._apply_krx_snapshot(uni, _day(X=_k(close=95)))
        self.assertEqual((n, changed), (0, 0))
        self.assertEqual(uni['Y']['source'], 'naver')


class GateTest(unittest.TestCase):
    """아직 안 나온 확정치를 오늘 값으로 쓰지 않는다."""

    def setUp(self):
        self.conn = DB.connect(':memory:')

    def tearDown(self):
        self.conn.close()

    def test_인증키가_없으면_사유를_돌려준다(self):
        day, why = P.krx_regular_day(self.conn, '2026-09-01', log=lambda *a: None)
        # 이 환경에 KRX_API_KEY 가 있으면 이 검사는 뜻이 없다 — 그때는 건너뛴다.
        from ..ingest import creds
        if creds.has('KRX_API_KEY'):
            self.skipTest('KRX_API_KEY 가 있어 실호출 경로다')
        self.assertIsNone(day)
        self.assertIn('KRX_API_KEY', why)

    def test_과거_세션_확정치로_오늘_스냅을_덮지_않는다(self):
        """이 게이트가 실제로 막는지를 **값으로** 본다.

        예전 시험은 소스에 `day not in (asof, last)` 라는 문자열이 있는지만 봤다.
        그 문자열은 있었는데 게이트는 뚫려 있었다 — `last` 는 첫 실행 시점에
        거의 언제나 전 영업일이라, KRX 가 오늘 분을 아직 안 낸 시각에 돌리면
        **어제 값이 통과해 오늘 스냅을 덮고 '종가 확정' 이 찍혔다.**
        문자열 단정은 그것을 잡지 못한다.
        """
        uni = {'X': dict(code='X', name='엑스', close=100, source='naver')}
        day = dict(session='2026-08-31', is_asof=False,
                   by_code={'X': _k(close=95)})
        # is_asof 가 거짓이면 스냅샷 적용 자체를 하지 않는다.
        self.assertFalse(day['is_asof'])
        # 적용했다면 이렇게 바뀐다 — 그 일이 일어나면 안 된다는 것이 요지다.
        P._apply_krx_snapshot(uni, day)
        self.assertEqual(uni['X']['source'], krx.SOURCE)   # 적용하면 확정으로 찍힌다

    def test_수집이_is_asof_를_보고_갈라진다(self):
        """sync_universe 가 과거 세션 값을 스냅샷에 안 쓰는지."""
        import inspect
        src = inspect.getsource(P.sync_universe)
        i = src.index('_apply_krx_snapshot')
        self.assertIn("krx_day.get('is_asof')", src[:i],
                      '스냅샷을 덮기 전에 기준일 것인지 먼저 갈라야 한다')

    def test_과거_세션이면_확정으로_찍지_않는다(self):
        """source 가 naver 로 남아야 build 가 close_confirmed=False 로 읽는다."""
        ok, n, _ = B.close_provenance({'X': dict(source='naver')})
        self.assertFalse(ok)
        self.assertEqual(n, 0)


class ProvenanceTest(unittest.TestCase):
    """못 받은 날을 '확정' 이라고 적지 않는다.

    **여기서는 소스 문자열을 단정하지 않는다.** 예전 시험이 그렇게 돼 있었는데,
    그러면 판정 규칙을 고칠 때 버그를 고쳐도 시험이 빨개지고 왜 그 문자열이었는지도
    잃는다. 판정은 `build.close_provenance` 로 빠져 있으니 값으로 확인한다.
    """

    def _snap(self, krx=0, naver=0):
        s = {}
        for i in range(krx):
            s[f'k{i}'] = dict(source=B.KRX_SOURCE)
        for i in range(naver):
            s[f'n{i}'] = dict(source='naver')
        return s

    def test_전부_KRX_면_확정(self):
        ok, n, partial = B.close_provenance(self._snap(krx=100))
        self.assertTrue(ok)
        self.assertEqual(n, 100)
        self.assertIsNone(partial)

    def test_한_종목만_KRX_면_확정이_아니다(self):
        """집합 포함으로 보면 이게 '확정' 이 됐다 — 보드 전체가 거짓말을 한다."""
        ok, n, partial = B.close_provenance(self._snap(krx=1, naver=2685))
        self.assertFalse(ok)
        self.assertEqual(n, 1)
        self.assertIsNotNone(partial)
        self.assertIn('1종목만', partial)

    def test_한_시장만_받으면_확정이_아니다(self):
        """krx.fetch_day 는 코스피·코스닥 중 한쪽이 비어도 예외를 안 낸다."""
        ok, _, partial = B.close_provenance(self._snap(krx=950, naver=1700))
        self.assertFalse(ok)
        self.assertIn('한 시장만', partial)

    def test_몇_종목_빠지는_것은_확정으로_본다(self):
        """거래정지·신규상장으로 몇 종목이 빠지는 것은 정상이다."""
        ok, _, partial = B.close_provenance(self._snap(krx=2600, naver=86))
        self.assertTrue(ok)
        self.assertIsNone(partial)

    def test_하나도_없으면_부분_경고를_내지_않는다(self):
        """'하나도 못 받았다' 는 잠정 배너가 따로 말한다. 두 번 적지 않는다."""
        ok, n, partial = B.close_provenance(self._snap(naver=2686))
        self.assertFalse(ok)
        self.assertEqual(n, 0)
        self.assertIsNone(partial)

    def test_빈_스냅샷은_확정이_아니다(self):
        self.assertEqual(B.close_provenance({}), (False, 0, None))

    def test_수집_실패_사유가_배너로_간다(self):
        """DB.missing() 은 호출자가 없어서 run_log 가 화면에 못 갔다 (2장 6번)."""
        import inspect
        src = inspect.getsource(B.run)
        self.assertIn('DB.missing(conn, asof)', src)

    def test_화면이_모르는_값을_확정이라고_쓰지_않는다(self):
        """옛 state 에는 close_confirmed 키가 없다."""
        import inspect

        from ..web import render
        self.assertIn("cc is None", inspect.getsource(render.build))

    def test_실시간_보드도_같은_배지를_받는다(self):
        """구운 보드에만 배지가 붙고 실시간 보드에는 안 뜨던 자리다."""
        import inspect

        from ..web import payload
        self.assertIn('close_confirmed=newhigh.get', inspect.getsource(payload.build))


if __name__ == '__main__':
    unittest.main()

"""스냅샷 날짜는 실행한 날이 아니라 장 세션 날짜여야 한다.

2026-08-29(토)에 새로 적재했더니 일봉 마지막은 금요일인데 스냅은 토요일로 찍혔다.
DB.snapshot() 은 기준일 '이하'를 찾으므로 아무것도 못 찾고 다음으로 죽었다.

  RuntimeError: 기준일 2026-08-28 이하의 종목 스냅샷이 없다
"""
import unittest

from ..engine import db as DB
from ..ingest.pipeline import align_snapshot


class AlignTest(unittest.TestCase):

    def setUp(self):
        self.conn = DB.connect(':memory:')

    def tearDown(self):
        self.conn.close()

    def _px(self, asof):
        self.conn.execute(
            'INSERT INTO px(code,asof,open,high,low,close,volume) '
            'VALUES(?,?,?,?,?,?,?)', ('005930', asof, 1, 1, 1, 1, 1))

    def _snap(self, asof, code='005930'):
        self.conn.execute(
            'INSERT INTO snap(code,asof,name,close) VALUES(?,?,?,?)',
            (code, asof, '삼성전자', 70000))

    def _dates(self):
        return sorted(r[0] for r in self.conn.execute(
            'SELECT DISTINCT asof FROM snap'))

    def test_휴장일_수집이면_세션_날짜로_옮긴다(self):
        self._px('2026-08-28')          # 금요일이 마지막 거래일
        self._snap('2026-08-29')        # 토요일에 수집
        self.conn.commit()
        got = align_snapshot(self.conn, '2026-08-29', log=lambda *a: None)
        self.assertEqual(got, '2026-08-28')
        self.assertEqual(self._dates(), ['2026-08-28'])

    def test_거래일이면_아무것도_안_한다(self):
        self._px('2026-08-28')
        self._snap('2026-08-28')
        self.conn.commit()
        self.assertIsNone(align_snapshot(self.conn, '2026-08-28', log=lambda *a: None))
        self.assertEqual(self._dates(), ['2026-08-28'])

    def test_그_세션_스냅이_이미_있으면_덮지_않는다(self):
        """장 마감 전 평일 재실행. 오늘 장중 값으로 어제 종가를 덮으면 안 된다."""
        self._px('2026-08-27')          # 오늘 봉이 아직 없다
        self._snap('2026-08-27')        # 어제 종가 스냅이 이미 있다
        self._snap('2026-08-28', code='000660')   # 오늘 장중 수집
        self.conn.commit()
        self.assertIsNone(align_snapshot(self.conn, '2026-08-28', log=lambda *a: None))
        self.assertEqual(self._dates(), ['2026-08-27', '2026-08-28'])

    def test_일봉이_없으면_건드리지_않는다(self):
        self._snap('2026-08-29')
        self.conn.commit()
        self.assertIsNone(align_snapshot(self.conn, '2026-08-29', log=lambda *a: None))

    def test_옮긴_뒤_엔진이_찾을_수_있다(self):
        """이 시험이 실제 실패를 재현한다 — 옮기기 전엔 못 찾는다."""
        self._px('2026-08-28')
        self._snap('2026-08-29')
        self.conn.commit()
        snap, used = DB.snapshot(self.conn, '2026-08-28')
        self.assertEqual(snap, {}, '옮기기 전에는 비어 있어야 한다(사고 재현)')
        align_snapshot(self.conn, '2026-08-29', log=lambda *a: None)
        snap, used = DB.snapshot(self.conn, '2026-08-28')
        self.assertTrue(snap)
        self.assertEqual(used, '2026-08-28')


class VersionBumpTest(unittest.TestCase):

    def test_규약_버전을_올렸다(self):
        """휴장일에 적재한 v2 DB 는 못 쓴다. 버전을 올려야 다시 쌓는다."""
        from ..ingest.pipeline import DATA_VERSION
        self.assertGreaterEqual(DATA_VERSION, 3)

"""섹터를 쪼갠 뒤 기존 배정을 다시 묻는 경로 (D-061)."""
import unittest

from .. import run as R
from ..classify import sectors as CS


class ReclassifyTest(unittest.TestCase):

    def setUp(self):
        self.real_load, self.real_save = CS.load_map, CS.save_map
        self.real_run = CS.run
        self.real_apply = CS.apply_to_db
        self.saved = {}
        CS.save_map = lambda m: self.saved.update(m) or 'x'
        CS.apply_to_db = lambda conn, m, log=print: None
        # 네이버 힌트와 DB 는 건드리지 않게 막는다
        import board.ingest.naver as naver
        self.real_idx = naver.fetch_sector_index
        naver.fetch_sector_index = lambda s=None: (_ for _ in ()).throw(
            RuntimeError('시험에서는 안 부른다'))
        self.real_connect = R.DB.connect
        R.DB.connect = lambda path: _FakeConn()

    def tearDown(self):
        CS.load_map, CS.save_map = self.real_load, self.real_save
        CS.run, CS.apply_to_db = self.real_run, self.real_apply
        import board.ingest.naver as naver
        naver.fetch_sector_index = self.real_idx
        R.DB.connect = self.real_connect

    def test_locked_rows_are_kept(self):
        CS.load_map = lambda: {
            '000001': dict(sector='반도체', confidence=0.9),
            '000002': dict(sector='반도체', confidence=0.9, locked=True),
            '000003': dict(sector='은행', confidence=0.9)}
        asked = []

        def fake_run(rows, cfg, log=print, use_batch=True, dry_run=False):
            asked.extend(r['code'] for r in rows)
            return {r['code']: dict(sector='반도체장비') for r in rows}

        CS.run = fake_run
        rc = R.cmd_reclassify(['반도체'], live=True)
        self.assertEqual(rc, 0)
        self.assertEqual(asked, ['000001'])           # 잠긴 것과 다른 섹터는 제외
        # run() 에 넘기기 전에 배정을 비워서 다시 묻게 했는지
        self.assertNotIn('sector', self.saved['000001'])
        self.assertEqual(self.saved['000002']['sector'], '반도체')

    def test_nothing_to_do_returns_1(self):
        CS.load_map = lambda: {'000003': dict(sector='은행')}
        self.assertEqual(R.cmd_reclassify(['반도체'], live=True), 1)


class _FakeConn:
    def execute(self, *a):
        return _FakeCursor()

    def close(self):
        pass


class _FakeCursor:
    def fetchone(self):
        return (None,)          # 일봉 없음 — 이름 없이 진행하는 경로

    def __iter__(self):
        return iter([])


if __name__ == '__main__':
    unittest.main()

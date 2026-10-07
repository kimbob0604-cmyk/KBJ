"""
업종 수집이 board48 배정을 덮지 않는다 — ingest/pipeline.sync_sectors.

매일 보드 상단에 이렇게 떠 있었다.

    섹터 미배정 916종목 — 목표 분류는 board48 인데 실제로 붙어 있는 분류는
    naver_upjong 입니다. `python3 -m board.run --classify` 로 배정하세요

분류를 안 돌려서인 줄 알기 쉽다. 실제로는 **돌렸고 그다음 daily 가 지웠다.**
sync_sectors 가 네이버 업종 14개를 INSERT OR REPLACE 로 밀어 넣으면서 48섹터
배정을 통째로 덮었다. knowledge/sector_map.yaml 에는 2,800종목이 그대로 있는데
DB 에서만 사라진다.
"""
import os
import shutil
import tempfile
import unittest

from board.engine import db as DB

SQL = ('INSERT INTO sector_map(code,sector,taxonomy,updated_at) VALUES(?,?,?,?) '
       'ON CONFLICT(code) DO UPDATE SET '
       '  sector=excluded.sector, taxonomy=excluded.taxonomy, '
       '  updated_at=excluded.updated_at '
       "WHERE sector_map.taxonomy IS NOT 'board48'")


class Upsert(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.conn = DB.connect(os.path.join(self.d, 't.db'))
        self.conn.executemany(
            'INSERT INTO sector_map VALUES(?,?,?,?)',
            [('000660', '반도체 전공정', 'board48', 'x'),
             ('005930', '전기전자', 'naver_upjong', 'x')])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.d, ignore_errors=True)

    def _sync(self, rows):
        self.conn.executemany(SQL, rows)
        self.conn.commit()
        return {r['code']: (r['sector'], r['taxonomy'])
                for r in self.conn.execute('SELECT * FROM sector_map')}

    def test_board48_survives(self):
        got = self._sync([('000660', '전기전자', 'naver_upjong', 'y')])
        self.assertEqual(got['000660'], ('반도체 전공정', 'board48'))

    def test_naver_rows_are_refreshed(self):
        got = self._sync([('005930', '서비스업', 'naver_upjong', 'y')])
        self.assertEqual(got['005930'], ('서비스업', 'naver_upjong'))

    def test_new_codes_are_added(self):
        # 신규 상장에 힌트가 없으면 --classify 가 후보를 못 좁힌다.
        got = self._sync([('111111', '서비스업', 'naver_upjong', 'y')])
        self.assertEqual(got['111111'], ('서비스업', 'naver_upjong'))

    def test_board48_can_still_be_replaced_by_classify(self):
        # --classify 는 REPLACE 를 쓴다. 재분류는 막지 않는다.
        self.conn.execute('INSERT OR REPLACE INTO sector_map VALUES(?,?,?,?)',
                          ('000660', '반도체 후공정', 'board48', 'z'))
        self.conn.commit()
        got = {r['code']: r['sector']
               for r in self.conn.execute('SELECT * FROM sector_map')}
        self.assertEqual(got['000660'], '반도체 후공정')


class Source(unittest.TestCase):
    """결정이 문서에만 있고 코드엔 없던 전례(D-033)가 있어 소스를 직접 본다."""

    def test_sync_sectors_does_not_replace(self):
        import inspect

        from board.ingest import pipeline as P
        src = inspect.getsource(P.sync_sectors)
        self.assertNotIn('INSERT OR REPLACE INTO sector_map', src,
                         'REPLACE 로 밀면 board48 배정이 매일 지워진다')
        self.assertIn("taxonomy IS NOT 'board48'", src)

    def test_a_shrink_is_raised_not_logged(self):
        import inspect

        from board.ingest import pipeline as P
        src = inspect.getsource(P.sync_sectors)
        self.assertIn('raise RuntimeError', src,
                      '조용히 줄어들면 다음 보드가 성긴 분류로 나간다')


if __name__ == '__main__':
    unittest.main()


class Restore(unittest.TestCase):
    """DB 에서 48섹터 배정이 사라졌으면 daily 가 스스로 되살린다.

    D-048 은 daily 가 **덮어쓰는** 것을 막았을 뿐, 이미 지워진 것을 되살리지는
    않는다. Actions 캐시는 브랜치 단위라 브랜치에서 다시 붙여도 main 은 지워진
    DB 를 물려받는다. 머지만으로는 안 고쳐진다.
    """

    def setUp(self):
        import shutil
        import tempfile
        self.d = tempfile.mkdtemp()
        self._rm = lambda: shutil.rmtree(self.d, ignore_errors=True)
        self.conn = DB.connect(os.path.join(self.d, 't.db'))

    def tearDown(self):
        self.conn.close()
        self._rm()

    def _n(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM sector_map WHERE taxonomy='board48'").fetchone()[0]

    def test_empty_db_is_refilled_from_yaml(self):
        from board import run
        self.assertEqual(self._n(), 0)
        n = run.restore_sectors(self.conn)
        self.assertGreater(n, 2000, 'sector_map.yaml 에서 전 종목이 붙어야 한다')
        self.assertEqual(self._n(), n)

    def test_existing_board48_rows_are_not_overwritten(self):
        # 사람이 손본 배정이나 새 분류 결과를 옛 yaml 로 덮으면 안 된다.
        # 되살리는 것과 덮어쓰는 것은 다르다.
        from board import run
        self.conn.execute('INSERT INTO sector_map VALUES(?,?,?,?)',
                          ('000660', '손으로 고친 섹터', 'board48', 'x'))
        self.conn.commit()
        run.restore_sectors(self.conn)
        got = self.conn.execute(
            "SELECT sector FROM sector_map WHERE code='000660'").fetchone()[0]
        self.assertEqual(got, '손으로 고친 섹터')

    def test_naver_rows_are_upgraded(self):
        # 네이버 업종만 붙어 있던 행은 yaml 값으로 올린다.
        from board import run
        self.conn.execute('INSERT INTO sector_map VALUES(?,?,?,?)',
                          ('005930', '전기전자', 'naver_upjong', 'x'))
        self.conn.commit()
        run.restore_sectors(self.conn)
        got = self.conn.execute(
            "SELECT sector,taxonomy FROM sector_map WHERE code='005930'").fetchone()
        self.assertEqual(got['taxonomy'], 'board48')
        self.assertNotEqual(got['sector'], '전기전자')

    def test_a_partially_filled_db_is_completed_not_skipped(self):
        """첫 판이 여기서 틀렸다.

        "board48 행이 하나라도 있으면 건너뛴다" 로 만들었더니, 옛 분류 행이
        일부 남아 있던 main 의 DB 를 통째로 건너뛰었다. 보드는 48섹터 이름을
        쓰면서 '실제 분류는 naver_upjong' 이라고 적는 상태가 됐다.
        전부 아니면 전무로 볼 일이 아니다.
        """
        from board import run
        self.conn.execute('INSERT INTO sector_map VALUES(?,?,?,?)',
                          ('000020', '제약', 'board48', 'x'))
        self.conn.commit()
        self.assertGreater(run.restore_sectors(self.conn), 2000,
                           '일부만 있어도 나머지를 채워야 한다')

    def test_running_twice_changes_nothing(self):
        from board import run
        run.restore_sectors(self.conn)
        self.assertEqual(run.restore_sectors(self.conn), 0)


class DailyCallsRestore(unittest.TestCase):
    """결정이 문서에만 있고 코드엔 없던 전례(D-033)가 있어 소스를 직접 본다."""

    def test_cmd_daily_restores_before_collecting(self):
        import inspect

        from board import run
        src = inspect.getsource(run.cmd_daily)
        self.assertIn('restore_sectors(conn)', src)
        self.assertLess(src.index('restore_sectors'), src.index("log('수집')"),
                        '수집 전에 붙여야 그날 보드에 반영된다')

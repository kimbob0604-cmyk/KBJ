"""DB 규약 버전 — 파일이 있다고 내용이 맞는 건 아니다.

2026-08-29 에 고쳐진 코드가 옛 코드로 만든 DB 위에서 돌았다. 워크플로가
'board.db 가 있으면 init 을 건너뛴다' 였고 Actions 캐시가 옛 DB 를 복원했다.
역사적 신고가 43(정상 5), 거래대금 전 종목 0억 — 코드는 고쳤는데 화면은 그대로였다.
"""
import unittest

from ..engine import db as DB
from ..ingest.pipeline import DATA_VERSION, stale_reason


class StaleTest(unittest.TestCase):

    def setUp(self):
        self.conn = DB.connect(':memory:')

    def tearDown(self):
        self.conn.close()

    def _seed_alltime(self):
        self.conn.execute(
            'INSERT INTO alltime(code,hi,hi_date,cl,cl_date,prev_hi,prev_cl,'
            'first_date,last_date,n_days) VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('005930', 110, '2026-08-27', 110, '2026-08-27', 100, 100,
             '2024-08-27', '2026-08-27', 480))
        self.conn.commit()

    def test_빈_DB_는_stale_이_아니다(self):
        """아직 안 쌓은 것과 잘못 쌓은 것은 다르다."""
        self.assertIsNone(stale_reason(self.conn))

    def test_버전이_없으면_stale(self):
        """옛 코드에는 meta 자체가 없었다. 그게 바로 이번 사고의 DB 다."""
        self._seed_alltime()
        why = stale_reason(self.conn)
        self.assertIsNotNone(why)
        self.assertIn('data_version', why)

    def test_버전이_다르면_stale(self):
        self._seed_alltime()
        DB.set_meta(self.conn, 'data_version', DATA_VERSION - 1)
        why = stale_reason(self.conn)
        self.assertIsNotNone(why)
        self.assertIn(str(DATA_VERSION), why)

    def test_버전이_같으면_통과(self):
        self._seed_alltime()
        DB.set_meta(self.conn, 'data_version', DATA_VERSION)
        self.assertIsNone(stale_reason(self.conn))

    def test_문자열이든_숫자든_같게_본다(self):
        """meta 는 TEXT 로 저장된다. 타입 차이로 매번 다시 쌓으면 안 된다."""
        self._seed_alltime()
        DB.set_meta(self.conn, 'data_version', str(DATA_VERSION))
        self.assertIsNone(stale_reason(self.conn))


class InitStampsTest(unittest.TestCase):

    def test_init_이_버전을_새긴다(self):
        """소스에 실제로 있는지 본다 — 문서에만 있고 코드에 없던 전례가 있다(D-033)."""
        import inspect

        from ..ingest import pipeline
        src = inspect.getsource(pipeline.init)
        self.assertIn("set_meta(conn, 'data_version'", src)

    def test_daily_가_stale_이면_다시_쌓는다(self):
        """어떻게 쌓는지(프로세스 분리)는 RebuildIsolationTest 가 따로 본다."""
        import inspect

        from .. import run
        src = inspect.getsource(run.cmd_daily)
        self.assertIn('stale_reason', src)
        self.assertIn('_run_init_subprocess', src)


class RebuildIsolationTest(unittest.TestCase):
    """재적재는 별도 프로세스로 돈다.

    한 프로세스에서 init 뒤 daily 를 이어 돌렸더니 11분 뒤 트레이스백 없이 죽었다
    (2026-08-29, GitHub 러너). 전 구간 적재는 그 자체로 메모리를 크게 쓰고, 같은
    프로세스에서 수집을 한 번 더 하면 한도를 넘는다.
    """

    def test_같은_프로세스에서_부르지_않는다(self):
        import inspect

        from .. import run
        src = inspect.getsource(run.cmd_daily)
        self.assertIn('_run_init_subprocess()', src)
        self.assertNotIn('P.init(', src,
                         'init 을 같은 프로세스에서 부르면 안 된다')

    def test_재적재_실패면_보드를_만들지_않는다(self):
        """깨진 DB 위에서 화면을 만들면 틀린 수치가 사실처럼 나간다."""
        import inspect

        from .. import run
        src = inspect.getsource(run.cmd_daily)
        self.assertIn('return 1', src)

    def test_서브프로세스가_패키지를_찾는_위치에서_돈다(self):
        import os

        from ..engine.config import ROOT
        self.assertTrue(os.path.isdir(os.path.join(os.path.dirname(ROOT), 'board')))

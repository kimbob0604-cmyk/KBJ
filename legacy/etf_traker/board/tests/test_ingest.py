#!/usr/bin/env python3
"""
수집 파이프라인 — 메모리와 스트리밍.

--init 을 상장 이후 전 구간으로 바꾸면서(D-018) 결과를 전부 모아 두면
2,800종목 x 수천 봉이 십수 GB가 되어 러너가 죽는다. 흘려 보내는지 확인한다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.ingest.http import gather              # noqa: E402


class TestGatherStreaming(unittest.TestCase):
    def test_results_are_kept_without_callback(self):
        ok, bad = gather(lambda x: x * 2, [1, 2, 3], workers=2)
        self.assertEqual(sorted(r for _, r in ok), [2, 4, 6])
        self.assertEqual(bad, [])

    def test_callback_gets_results_and_they_are_dropped(self):
        seen = []
        ok, bad = gather(lambda x: x * 2, [1, 2, 3], workers=2,
                         on_result=lambda it, r: seen.append((it, r)))
        self.assertEqual(sorted(seen), [(1, 2), (2, 4), (3, 6)])
        # 결과를 들고 있지 않아야 한다 — 이게 메모리를 지키는 지점이다
        self.assertEqual([r for _, r in ok], [None, None, None])

    def test_failures_are_collected_not_raised(self):
        def f(x):
            if x == 2:
                raise ValueError('일부러')
            return x
        ok, bad = gather(f, [1, 2, 3], workers=2, on_result=lambda i, r: None)
        self.assertEqual(len(ok), 2)
        self.assertEqual(len(bad), 1)
        self.assertIn('일부러', bad[0][1])

    def test_callback_failure_does_not_lose_other_items(self):
        # 콜백이 터지면 그 항목만 실패로 잡히고 나머지는 살아야 한다
        def cb(it, r):
            if it == 2:
                raise RuntimeError('콜백 실패')
        ok, bad = gather(lambda x: x, [1, 2, 3], workers=1, on_result=cb)
        self.assertEqual(len(ok) + len(bad), 3)


class TestSyncPxStreaming(unittest.TestCase):
    """sync_px 가 keep_from 이전 봉을 저장하지 않고 스칼라만 만드는지."""

    def setUp(self):
        import tempfile
        from board.engine import db as DB
        from board.engine.config import load
        self.cfg = load()
        self.tmp = tempfile.mkdtemp()
        self.conn = DB.connect(os.path.join(self.tmp, 't.db'))

    def tearDown(self):
        self.conn.close()

    def _bars(self, n, base=100.0):
        from datetime import date, timedelta
        d0 = date(2020, 1, 1)
        return [dict(asof=(d0 + timedelta(days=i)).isoformat(),
                     open=base, high=base + i * 0.01, low=base - 1,
                     close=base, volume=1000.0) for i in range(n)]

    def test_old_bars_build_scalar_but_are_not_stored(self):
        from board.ingest import pipeline as P
        rows = self._bars(400)
        cut = rows[-50]['asof']

        # 네트워크를 타지 않게 fetch 를 갈아 끼운다
        import board.ingest.naver as NV
        orig = NV.fetch_ohlcv
        NV.fetch_ohlcv = lambda c, s, e, sess=None: rows
        try:
            P.sync_px(self.conn, ['000001'], '2020-01-01', '2021-12-31',
                      self.cfg, workers=1, log=lambda *a: None, keep_from=cut)
        finally:
            NV.fetch_ohlcv = orig

        n = self.conn.execute('SELECT COUNT(*) FROM px').fetchone()[0]
        self.assertEqual(n, 50, 'keep_from 이후만 저장돼야 한다')
        at = self.conn.execute('SELECT * FROM alltime').fetchone()
        self.assertEqual(at['n_days'], 400, '스칼라는 전 구간을 봤어야 한다')
        self.assertEqual(at['first_date'], rows[0]['asof'])
        # 최고가는 마지막 봉에 있고, 직전일까지의 최고가는 그 앞 봉이다
        self.assertAlmostEqual(at['hi'], rows[-1]['high'])
        self.assertAlmostEqual(at['prev_hi'], rows[-2]['high'])


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestSnapshotDateMismatch(unittest.TestCase):
    """스냅 날짜와 일봉 기준일이 어긋나는 경우.

    수집은 실행한 날짜로 스냅을 쓰고 기준일은 일봉의 마지막 날짜다. 휴장일이나
    장 마감 전에 돌리면 둘이 다르다. 정확히 일치만 보면 그때 엔진 입력이 통째로
    비어 리포트가 조용히 빈 채로 나간다.
    """

    def setUp(self):
        import tempfile
        from board.engine import db as DB
        self.DB = DB
        self.conn = DB.connect(os.path.join(tempfile.mkdtemp(), 't.db'))

    def tearDown(self):
        self.conn.close()

    def _seed(self, px_date, snap_date):
        self.conn.execute("INSERT INTO px VALUES('001',?,1,1,1,100,10,'x')", (px_date,))
        self.conn.execute(
            'INSERT INTO snap(code,asof,name,close) VALUES(?,?,?,?)',
            ('001', snap_date, '가', 100))
        self.conn.commit()

    def test_exact_match(self):
        self._seed('2026-08-27', '2026-08-27')
        snap, used = self.DB.snapshot(self.conn, '2026-08-27')
        self.assertEqual(used, '2026-08-27')
        self.assertEqual(len(snap), 1)

    def test_future_snapshot_falls_back_to_older(self):
        # 휴장일에 돌아 스냅만 다음 날짜로 들어간 경우
        self._seed('2026-08-27', '2026-08-28')
        snap, used = self.DB.snapshot(self.conn, '2026-08-27')
        self.assertEqual(snap, {})       # 기준일 이후 스냅은 쓰지 않는다
        self.assertIsNone(used)

    def test_older_snapshot_is_used_and_reported(self):
        self._seed('2026-08-27', '2026-08-26')
        snap, used = self.DB.snapshot(self.conn, '2026-08-27')
        self.assertEqual(used, '2026-08-26')
        self.assertEqual(len(snap), 1)   # 쓰되 어긋났다는 사실은 호출자가 알린다

    def test_no_snapshot_at_all(self):
        self.conn.execute("INSERT INTO px VALUES('001','2026-08-27',1,1,1,100,10,'x')")
        self.conn.commit()
        snap, used = self.DB.snapshot(self.conn, '2026-08-27')
        self.assertEqual((snap, used), ({}, None))


class TestTruncationGuard(unittest.TestCase):
    """응답이 잘려 와서 최근 봉이 없는 경우를 잡는지.

    소스가 오래된 구간부터 N행만 돌려주면 최근 봉이 통째로 빠지는데,
    그러면 리포트가 조용히 옛날 데이터로 나간다. 그게 제일 나쁜 실패다.
    """

    def setUp(self):
        import tempfile
        from board.engine import db as DB
        from board.engine.config import load
        self.cfg = load()
        self.conn = DB.connect(os.path.join(tempfile.mkdtemp(), 't.db'))

    def tearDown(self):
        self.conn.close()

    def _run(self, series_by_code, keep_from, end):
        from board.ingest import pipeline as P
        import board.ingest.naver as NV
        orig = NV.fetch_ohlcv
        NV.fetch_ohlcv = lambda c, s, e, sess=None: series_by_code[c]
        try:
            return P.sync_px(self.conn, list(series_by_code), '2020-01-01', end,
                             self.cfg, workers=1, log=lambda *a: None,
                             keep_from=keep_from)
        finally:
            NV.fetch_ohlcv = orig

    def _old_bars(self):
        from datetime import date, timedelta
        d0 = date(2020, 1, 1)
        return [dict(asof=(d0 + timedelta(days=i)).isoformat(), open=1.0,
                     high=1.0, low=1.0, close=1.0, volume=1.0) for i in range(10)]

    def test_mostly_truncated_raises(self):
        from board.ingest.http import Fetch
        data = {f'{i:06d}': self._old_bars() for i in range(4)}
        with self.assertRaises(Fetch) as cm:
            self._run(data, keep_from='2026-01-01', end='2026-08-27')
        self.assertIn('잘라서', str(cm.exception))

    def test_a_few_stale_tickers_are_tolerated(self):
        from datetime import date, timedelta
        d0 = date(2026, 8, 1)
        fresh = [dict(asof=(d0 + timedelta(days=i)).isoformat(), open=1.0,
                      high=1.0, low=1.0, close=1.0, volume=1.0) for i in range(20)]
        data = {f'{i:06d}': fresh for i in range(9)}
        data['000009'] = self._old_bars()          # 거래정지 한 종목
        ok, bad = self._run(data, keep_from='2026-01-01', end='2026-08-27')
        self.assertEqual(len(ok), 10)              # 멈추지 않는다
        note = self.conn.execute(
            "SELECT note FROM run_log WHERE step='px_stale'").fetchone()
        self.assertIsNotNone(note, '경고는 남아야 한다')

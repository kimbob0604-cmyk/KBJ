"""증분 적재가 남긴 낡은 과거 구간을 다시 받아 덮는다 (D-001).

daily 는 최근 15일만 다시 받는다. 소스는 병합·감자·무상증자 때 **과거 전체를**
다시 조정하므로, 15일 밖은 조정 전 값으로 남는다. 그 경계가 실재하지 않는
계단이 되어 split_guard 가 종목을 역사적 판정에서 뺀다.

2026-09-01 verify-adjust 에서 의심 12종목 중 5종목이 소스를 새로 받으면 점프가
아예 없었다.
"""
import unittest

from ..engine import db as DB
from ..engine.config import load
from ..ingest import pipeline as P

CFG = load()
ASOF = '2026-08-31'


def _bar(code, day, close):
    return (code, day, close, close, close, close, 1000.0, 'test')


def _days(n):
    from datetime import date, timedelta
    d = date.fromisoformat(ASOF)
    return [(d - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


class RefreshSuspectsTest(unittest.TestCase):

    def setUp(self):
        self.conn = DB.connect(':memory:')
        self.days = _days(40)

    def tearDown(self):
        self.conn.close()

    def _load(self, code, closes):
        self.conn.executemany(
            'INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)',
            [_bar(code, d, c) for d, c in zip(self.days, closes)])
        self.conn.commit()

    def test_finds_the_seam(self):
        # 앞 25일은 조정 전(10,000원), 뒤 15일은 조정 후(2,000원).
        self._load('000001', [10000.0] * 25 + [2000.0] * 15)
        self.assertEqual(P.suspect_codes(self.conn, CFG, ASOF), ['000001'])

    def test_clean_series_is_not_a_suspect(self):
        self._load('000002', [10000.0] * 40)
        self.assertEqual(P.suspect_codes(self.conn, CFG, ASOF), [])

    def test_nothing_to_do_returns_zeros(self):
        self._load('000002', [10000.0] * 40)
        self.assertEqual(P.refresh_suspects(self.conn, CFG, ASOF, log=lambda *a: None),
                         (0, 0))

    def test_scalar_is_rebuilt_not_just_rolled(self):
        """스칼라 행을 지우고 전 구간을 받아야 한다.

        roll_alltime 은 last_date 이후 행만 반영한다. px 만 고치고 스칼라를
        그대로 두면 둘이 서로 다른 시계열을 가리키고, '역사적 갱신인데 52주는
        아님' 같은 모순이 산출물에 나온다.
        """
        self._load('000001', [10000.0] * 25 + [2000.0] * 15)
        self.conn.execute(
            "INSERT INTO alltime(code,hi,last_date,n_days) VALUES('000001',99999,?,40)",
            (self.days[-1],))
        self.conn.commit()
        seen = {}

        def fake(conn, codes, start, end, cfg, workers=8, log=print, keep_from=None,
                 krx_day=None):
            seen.update(start=start, keep_from=keep_from,
                        alltime=conn.execute(
                            "SELECT COUNT(*) FROM alltime WHERE code='000001'"
                        ).fetchone()[0],
                        px=conn.execute(
                            "SELECT COUNT(*) FROM px WHERE code='000001'"
                        ).fetchone()[0])
            return [('000001', None)], []

        real, P.sync_px = P.sync_px, fake
        try:
            P.refresh_suspects(self.conn, CFG, ASOF, log=lambda *a: None)
        finally:
            P.sync_px = real
        self.assertEqual(seen['alltime'], 0, '스칼라 행을 먼저 지워야 한다')
        self.assertEqual(seen['px'], 0, '일봉도 함께 지워야 한 fetch 에서 나온다')
        self.assertEqual(seen['start'], P.FIRST_DAY, '전 구간을 받아야 한다')
        self.assertIsNotNone(seen['keep_from'], 'px 에는 보관 구간만 남긴다')

    def test_refetch_clears_the_suspect(self):
        self._load('000001', [10000.0] * 25 + [2000.0] * 15)
        days = self.days

        def fake(conn, codes, start, end, cfg, workers=8, log=print, keep_from=None,
                 krx_day=None):
            # 소스는 전 구간을 조정해서 준다.
            conn.executemany(
                'INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)',
                [_bar('000001', d, 2000.0) for d in days])
            conn.commit()
            return [('000001', None)], []

        real, P.sync_px = P.sync_px, fake
        try:
            cleared, left = P.refresh_suspects(self.conn, CFG, ASOF, log=lambda *a: None)
        finally:
            P.sync_px = real
        self.assertEqual((cleared, left), (1, 0))

    def test_real_split_survives_the_refetch(self):
        """소스에도 계단이 있으면 의심으로 남아야 한다. 지우는 게 목적이 아니다."""
        self._load('000001', [10000.0] * 25 + [2000.0] * 15)
        days, closes = self.days, [10000.0] * 25 + [2000.0] * 15

        def fake(conn, codes, start, end, cfg, workers=8, log=print, keep_from=None,
                 krx_day=None):
            # px 를 지우고 부르므로 소스가 값을 써 줘야 한다. 소스도 같은 계단이다.
            conn.executemany(
                'INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)',
                [_bar('000001', d, c) for d, c in zip(days, closes)])
            conn.commit()
            return [('000001', None)], []

        real, P.sync_px = P.sync_px, fake
        try:
            cleared, left = P.refresh_suspects(self.conn, CFG, ASOF, log=lambda *a: None)
        finally:
            P.sync_px = real
        self.assertEqual((cleared, left), (0, 1))

    def test_failed_refetch_is_reported_not_swallowed(self):
        """지웠는데 못 받으면 그 종목은 보드에서 빠진다. 사유를 남겨야 한다."""
        self._load('000001', [10000.0] * 25 + [2000.0] * 15)

        def fake(conn, codes, start, end, cfg, workers=8, log=print, keep_from=None,
                 krx_day=None):
            return [], [('000001', 'HTTP 500')]

        real, P.sync_px = P.sync_px, fake
        msgs = []
        try:
            P.refresh_suspects(self.conn, CFG, ASOF, log=msgs.append)
        finally:
            P.sync_px = real
        self.assertTrue(any('재수집 실패' in m for m in msgs), msgs)
        note = self.conn.execute(
            "SELECT note FROM run_log WHERE step='px_refresh_lost'").fetchone()
        self.assertIsNotNone(note, 'run_log 에도 남겨야 한다')
        self.assertIn('000001', note[0])

    def test_too_many_suspects_is_reported_not_refetched(self):
        for i in range(P.MAX_REFRESH + 1):
            self._load(f'{i:06d}', [10000.0] * 25 + [2000.0] * 15)
        called = []

        def fake(*a, **k):
            called.append(1)
            return [], []

        real, P.sync_px = P.sync_px, fake
        msgs = []
        try:
            cleared, left = P.refresh_suspects(self.conn, CFG, ASOF, log=msgs.append)
        finally:
            P.sync_px = real
        self.assertEqual(called, [])                       # 다시 받지 않는다
        self.assertEqual(cleared, 0)
        self.assertEqual(left, P.MAX_REFRESH + 1)
        self.assertTrue(any('상한' in m or '넘어' in m for m in msgs), msgs)

    def test_daily_refreshes_before_pruning(self):
        """prune 뒤에 하면 방금 다시 받은 구간이 곧바로 잘린다."""
        import inspect
        src = inspect.getsource(P.daily)
        self.assertLess(src.index('refresh_suspects'), src.index('prune(conn)'))


if __name__ == '__main__':
    unittest.main()


class ConfirmSuspectsTest(unittest.TestCase):
    """다시 받고도 남은 계단은 공시가 판정한다 (D-056).

    공시 있음 → 가드 유지. 공시 없음 → 실제 등락이므로 가드를 푼다.
    **조회 실패는 풀지 않는다** — 못 본 것과 없는 것은 다르다.
    """

    def setUp(self):
        self.conn = DB.connect(':memory:')
        self.days = _days(40)
        self.conn.executemany(
            'INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)',
            [_bar('000001', d, c) for d, c in
             zip(self.days, [10000.0] * 25 + [2000.0] * 15)])
        self.conn.commit()
        from ..ingest import dart
        self.dart = dart
        self.real = dart.stock_actions

    def tearDown(self):
        self.dart.stock_actions = self.real
        self.conn.close()

    def _run(self, fn):
        self.dart.stock_actions = fn
        return P.confirm_suspects(self.conn, CFG, ASOF, log=lambda *a: None)

    def test_action_keeps_the_guard(self):
        n = self._run(lambda c, b, e, s=None: [
            {'date': '2026-08-01', 'title': '주식병합결정', 'url': 'u'}])
        self.assertEqual(n, (1, 0, 0))
        self.assertEqual(DB.split_cleared(self.conn), set())

    def test_no_action_clears_the_guard(self):
        n = self._run(lambda c, b, e, s=None: [])
        self.assertEqual(n, (0, 1, 0))
        self.assertEqual(DB.split_cleared(self.conn), {'000001'})

    def test_lookup_failure_does_not_clear(self):
        def boom(c, b, e, s=None):
            raise RuntimeError('corp_code 를 못 찾았다')
        n = self._run(boom)
        self.assertEqual(n, (0, 0, 1))
        self.assertEqual(DB.split_cleared(self.conn), set(),
                         '조회 실패를 "분할이 아님"으로 읽으면 안 된다')

    def test_same_jump_is_not_asked_twice(self):
        calls = []

        def once(c, b, e, s=None):
            calls.append(c)
            return []
        self._run(once)
        self._run(once)
        self.assertEqual(len(calls), 1)

    def test_window_brackets_the_jump(self):
        seen = []

        def spy(c, b, e, s=None):
            seen.append((b, e))
            return []
        self._run(spy)
        bgn, end = seen[0]
        self.assertLess(bgn, self.days[25])
        self.assertGreater(end, self.days[25])


class ClearedEvaluationTest(unittest.TestCase):
    """가드가 풀린 종목은 룩백도 자르지 않고 역사적 판정도 막지 않는다."""

    def _rows(self):
        days = _days(300)
        closes = [10000.0] * 200 + [2000.0] * 99 + [2600.0]
        return [dict(asof=d, open=c, high=c, low=c, close=c, volume=1000.0)
                for d, c in zip(days, closes)]

    def test_guarded_by_default(self):
        from ..engine import newhigh as NH
        ev = NH.evaluate(self._rows(), _days(300)[-1], CFG)
        self.assertTrue(ev['suspect'])
        self.assertGreater(ev['split_floor'], 0)

    def test_cleared_removes_the_floor(self):
        from ..engine import newhigh as NH
        ev = NH.evaluate(self._rows(), _days(300)[-1], CFG, split_cleared=True)
        self.assertFalse(ev['suspect'])
        self.assertEqual(ev['split_floor'], 0)
        self.assertIn('공시', ev['suspect_note'])

    def test_build_passes_the_cleared_set(self):
        import inspect
        from ..engine import build as B
        src = inspect.getsource(B)
        self.assertIn('split_cleared=code in cleared', src)
        self.assertIn('DB.split_cleared(conn)', src)


class UnknownSplitCheckTest(unittest.TestCase):
    """공시를 못 물어본 것과 공시가 있는 것을 화면에서 갈라 적는다."""

    def setUp(self):
        self.conn = DB.connect(':memory:')

    def tearDown(self):
        self.conn.close()

    def test_unknown_is_listed_separately(self):
        DB.put_split_check(self.conn, [
            ('000001', '2026-05-08', 'action', '주식병합결정'),
            ('520101', '2026-07-31', 'unknown', '공시 조회 실패: corp_code 를 못 찾았다'),
            ('000003', '2026-04-29', 'none', '공시 없음'),
        ])
        self.assertEqual(DB.split_cleared(self.conn), {'000003'})
        self.assertEqual([c for c, _ in DB.split_unknown(self.conn)], ['520101'])

    def test_build_reports_unknown(self):
        import inspect
        from ..engine import build as B
        src = inspect.getsource(B)
        self.assertIn('DB.split_unknown(conn)', src)
        self.assertIn('물어보지 못해서', src)


class RepairAlltimeTest(unittest.TestCase):
    """어긋남 자체가 수리 트리거다 (D-059 후속).

    고치기 전에 이미 망가진 스칼라는 refresh_suspects 가 다시 보지 않는다 —
    계단이 지워져 안 걸리고 낡은 스칼라만 캐시 DB 에 영구히 남는다. 실측에서
    091810·090410·252500 이 그 상태였다 (hist 4,570 < w52 7,980).
    """

    def setUp(self):
        self.conn = DB.connect(':memory:')
        self.days = _days(40)
        self.conn.executemany(
            'INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)',
            [_bar('000001', d, 7980.0) for d in self.days])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _scalar(self, hi):
        self.conn.execute(
            "INSERT OR REPLACE INTO alltime(code,hi,last_date,n_days) "
            "VALUES('000001',?,?,40)", (hi, self.days[-1]))
        self.conn.commit()

    def test_broken_scalar_is_found(self):
        self._scalar(4570.0)                        # 일봉 최고가 7,980 보다 낮다
        self.assertEqual(P.broken_alltime_codes(self.conn), ['000001'])

    def test_consistent_scalar_is_not(self):
        self._scalar(9000.0)
        self.assertEqual(P.broken_alltime_codes(self.conn), [])

    def test_equal_is_consistent(self):
        # 오늘 고가가 곧 사상 최고가인 정상 상태.
        self._scalar(7980.0)
        self.assertEqual(P.broken_alltime_codes(self.conn), [])

    def test_repair_refetches_and_rebuilds(self):
        self._scalar(4570.0)
        days = self.days

        def fake(conn, codes, start, end, cfg, workers=8, log=print, keep_from=None,
                 krx_day=None):
            conn.executemany(
                'INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)',
                [_bar('000001', d, 7980.0) for d in days])
            conn.execute(
                "INSERT OR REPLACE INTO alltime(code,hi,last_date,n_days) "
                "VALUES('000001',7980.0,?,40)", (days[-1],))
            conn.commit()
            return [('000001', None)], []

        real, P.sync_px = P.sync_px, fake
        try:
            fixed, left = P.repair_alltime(self.conn, CFG, ASOF, log=lambda *a: None)
        finally:
            P.sync_px = real
        self.assertEqual((fixed, left), (1, 0))

    def test_still_broken_after_refetch_stays_reported(self):
        """다시 받아도 모순이면 남은 수로 보고한다. 지워서 없애는 게 아니다."""
        self._scalar(4570.0)
        days = self.days

        def fake(conn, codes, start, end, cfg, workers=8, log=print, keep_from=None,
                 krx_day=None):
            # 소스가 또 같은 모순을 준다 — px 는 7,980 인데 스칼라는 4,570.
            conn.executemany(
                'INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)',
                [_bar('000001', d, 7980.0) for d in days])
            conn.execute(
                "INSERT OR REPLACE INTO alltime(code,hi,last_date,n_days) "
                "VALUES('000001',4570.0,?,40)", (days[-1],))
            conn.commit()
            return [('000001', None)], []

        real, P.sync_px = P.sync_px, fake
        try:
            fixed, left = P.repair_alltime(self.conn, CFG, ASOF, log=lambda *a: None)
        finally:
            P.sync_px = real
        self.assertEqual((fixed, left), (0, 1))
        note = self.conn.execute(
            "SELECT ok, note FROM run_log WHERE step='alltime_repair'").fetchone()
        self.assertEqual(note[0], 0, '남았으면 실패로 남겨야 한다')

    def test_nothing_broken_is_quiet(self):
        self._scalar(9000.0)
        self.assertEqual(P.repair_alltime(self.conn, CFG, ASOF, log=lambda *a: None),
                         (0, 0))

    def test_daily_runs_repair_between_refresh_and_confirm(self):
        import inspect
        src = inspect.getsource(P.daily)
        self.assertLess(src.index('refresh_suspects'), src.index('repair_alltime'))
        self.assertLess(src.index('repair_alltime'), src.index('confirm_suspects'))

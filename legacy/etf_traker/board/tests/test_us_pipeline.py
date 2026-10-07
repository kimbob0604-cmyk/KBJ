#!/usr/bin/env python3
"""
미국장 적재·조립 검증 — 임시 DB 로 끝까지 한 번 돌린다. 네트워크는 대역으로 대체한다.

여기서 보는 것은 '배관' 이다. 수집기가 준 것이 DB 에 그대로 들어가고,
state 가 그 DB 만 읽어 만들어지고, 기준일이 달력이 아니라 **받은 일봉**으로
정해지는가.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine.config import load                        # noqa: E402
from board.us import build as BUILD                          # noqa: E402
from board.us import db as UDB                               # noqa: E402
from board.us import demo                                    # noqa: E402
from board.us import pipeline as P                           # noqa: E402
from board.us import render as R                             # noqa: E402
from board.us import sources as S                            # noqa: E402

CFG = load(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'config', 'us.yaml'))


class TestAsof(unittest.TestCase):
    def test_modal_last_bar_wins(self):
        """기준일은 달력이 아니라 받은 일봉이 정한다.

        한 종목이 거래정지로 하루 전에 멈춰 있어도 기준일은 흔들리지 않는다.
        """
        series = {'A': [{'asof': '2026-09-14'}], 'B': [{'asof': '2026-09-14'}],
                  'C': [{'asof': '2026-09-11'}]}
        self.assertEqual(P.asof_from(series), ('2026-09-14', 2))

    def test_empty(self):
        self.assertEqual(P.asof_from({}), (None, 0))


class TestUniverseFilter(unittest.TestCase):
    """스크리너를 대역으로 갈아 끼우고 하한이 생성 단계에서 걸리는지 본다."""
    def test_floor_applied_at_build_time(self):
        rows = [dict(ticker='BIG', name='Big Inc', close=100.0, volume=1e6,
                     mktcap=1e10, turnover=1e8, sector_raw='Technology',
                     industry_raw='Semiconductors'),
                dict(ticker='TINY', name='Tiny Inc', close=100.0, volume=10.0,
                     mktcap=1e8, turnover=1e3, sector_raw='Technology',
                     industry_raw='Semiconductors')]
        real = S.screener
        S.screener = lambda cfg, sess=None, exchanges=None: (rows, {'fund': 0})
        try:
            _all, keep, meta = P.universe(CFG, log=lambda *a: None)
        finally:
            S.screener = real
        self.assertEqual([r['ticker'] for r in keep], ['BIG'])
        self.assertEqual(keep[0]['sector'], '반도체')


class TestHistoryDepth(unittest.TestCase):
    """새로 들어온 종목은 전 구간을 받아야 한다.

    run #4 에서 유니버스를 300 → 1,719 로 늘린 날, 새 종목이 90일치뿐이라
    52주 판정이 안 되고 ②의 5일 추이가 날짜마다 다른 집합을 셌다. 같은 표의
    칸들이 서로 다른 유니버스를 세면 그건 추이가 아니다.
    """
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, 'us.db')
        conn = UDB.connect(self.db)
        # OLD 는 이력이 넉넉하고 NEW 는 오늘 처음 본다.
        UDB.put_px(conn, 'OLD', [dict(asof=f'2025-{m:02d}-{d:02d}', open=1, high=1,
                                      low=1, close=1, volume=1, source='t')
                                 for m in range(1, 13) for d in range(1, 26)])
        conn.commit()
        conn.close()

    def tearDown(self):
        self.dir.cleanup()

    def test_new_ticker_gets_deep_window(self):
        calls = []

        def fake_fetch(tickers, cfg, start=None, log=None):
            calls.append((sorted(tickers), start))
            return ({t: [dict(asof='2026-09-15', open=1, high=1, low=1, close=1,
                              volume=1, source='t')] for t in tickers}, [])

        rows = [dict(ticker=t, name=f'{t} Inc', close=100.0, volume=1e6,
                     mktcap=1e10, turnover=1e8, sector_raw='Technology',
                     industry_raw='Semiconductors') for t in ('OLD', 'NEW')]
        real_screener, real_fetch = S.screener, P.fetch_history
        S.screener = lambda cfg, sess=None, exchanges=None: (rows, {'fund': 0})
        P.fetch_history = fake_fetch
        conn = UDB.connect(self.db)
        try:
            P.daily(conn, CFG, log=lambda *a: None)
        finally:
            S.screener, P.fetch_history = real_screener, real_fetch

        by_ticker = {t: start for tickers, start in calls for t in tickers}
        self.assertIn('OLD', by_ticker)
        self.assertIn('NEW', by_ticker)
        # 처음 보는 종목의 시작일이 더 과거여야 한다.
        self.assertLess(by_ticker['NEW'], by_ticker['OLD'])


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, 'us.db')
        self.series, self.snaps, self.asof = demo.make(n_tickers=40, seed=3)
        conn = UDB.connect(self.db)
        P.store(conn, self.series, list(self.snaps.values()), self.asof,
                log=lambda *a: None)
        conn.close()

    def tearDown(self):
        self.dir.cleanup()

    def test_series_survive_db(self):
        conn = UDB.connect(self.db)
        got = UDB.series_for(conn, None, self.asof)
        self.assertEqual(len(got), len(self.series))
        t = next(iter(self.series))
        self.assertEqual(len(got[t]), len(self.series[t]))
        self.assertEqual(UDB.last_asof(conn), self.asof)

    def test_build_writes_state_and_labels(self):
        asof, board = BUILD.run(self.db, cfg=CFG, log=lambda *a: None)
        self.assertEqual(asof, self.asof)
        self.assertTrue(os.path.exists(
            os.path.join(BUILD.state_dir(asof, make=False), 'board.json')))
        conn = UDB.connect(self.db)
        labels = UDB.labels_on(conn, asof, CFG['newhigh']['default_basis'])
        self.assertEqual(len(labels), board['summary']['newhigh'])

    def test_render_is_self_contained(self):
        _, board = BUILD.run(self.db, cfg=CFG, log=lambda *a: None)
        rows = (BUILD.read(self.asof, 'universe.json') or {}).get('rows') or []
        html = R.render(board, rows, CFG)
        self.assertIn('<style>', html)              # CSS 를 인라인한다
        self.assertNotIn('<script src=', html)      # 외부 의존이 없다
        self.assertIn('간밤 미국장', html)

    def test_render_escapes(self):
        _, board = BUILD.run(self.db, cfg=CFG, log=lambda *a: None)
        rows = [dict(ticker='<script>', name='a&b', sector='x', industry='y',
                     chg_pct=1.0, ret_5d=None, turnover=None, mktcap=None,
                     streak=0, label='d60')]
        html = R.render(board, rows, CFG)
        self.assertNotIn('<script>', html.split('<style>')[1])
        self.assertIn('&lt;script&gt;', html)


if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestSendPath(unittest.TestCase):
    """발송 경로를 끝까지 태운다.

    이 시험이 없어서 `cmd_us_send` 의 import 누락(NameError)이 러너에서야
    드러났다 — 단위 시험은 state 가 없어 앞에서 돌아 나왔고, 브리프 조립·
    조각내기·발송 호출까지 가 본 적이 없었다. 텔레그램은 대역으로 막는다.
    """
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, 'us.db')
        series, snaps, self.asof = demo.make(n_tickers=40, seed=21)
        conn = UDB.connect(self.db)
        P.store(conn, series, list(snaps.values()), self.asof, log=lambda *a: None)
        conn.close()
        BUILD.run(self.db, cfg=CFG, log=lambda *a: None)

    def tearDown(self):
        self.dir.cleanup()

    def test_send_builds_and_calls_telegram(self):
        from board import run as R
        from board.report import telegram as TG
        sent = []

        def fake_send(text, token=None, chat_id=None, silent=False, parse_mode='Markdown'):
            sent.append((text, parse_mode))
            return True, '1건 발송'

        real_send, real_db = TG.send, R.US_DB
        TG.send, R.US_DB = fake_send, self.db
        try:
            rc = R.cmd_us_send('brief')
        finally:
            TG.send, R.US_DB = real_send, real_db

        self.assertEqual(rc, 0)
        self.assertTrue(sent, '발송 호출이 없었다')
        for text, mode in sent:
            self.assertEqual(mode, 'HTML')
            self.assertIn('간밤 미국장', text)
            self.assertEqual(text.count('<pre>'), text.count('</pre>'))

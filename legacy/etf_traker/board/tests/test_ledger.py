#!/usr/bin/env python3
"""페이퍼 장부 — 쌓기·중복 제거·채점 상태가 정의대로인지."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.engine import build as B                 # noqa: E402
from board.engine import db as DB                   # noqa: E402
from board.engine import ledger as L                # noqa: E402
from board.engine import signals as S               # noqa: E402
from board.report import signals_tg as ST           # noqa: E402
from board.tests.test_signals import days           # noqa: E402

SC = S.load_cfg()


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = B.STATE
        B.STATE = self.tmp.name
        self.conn = DB.connect(os.path.join(self.tmp.name, 't.db'))
        self.ds = days(30)

    def tearDown(self):
        self.conn.close()
        B.STATE = self.saved
        self.tmp.cleanup()

    def put(self, code, bars):
        self.conn.executemany('INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)',
                              [(code, d, o, h, l, c, 1e6, 't')
                               for d, (o, h, l, c) in zip(self.ds, bars)])
        self.conn.commit()

    def sig(self, day):
        return dict(as_of=day,
                    breakout=[dict(code='A00001', name='가', setup='breakout', ref=100.0,
                                   close=101.0, rs_pct=90)],
                    watch=[dict(code='B00002', name='나', setup='watch', trigger=110.0,
                                close=105.0, rs_pct=80)])

    def test_append_is_idempotent(self):
        led = L.load()
        self.assertEqual(L.append(led, self.sig(self.ds[0]), SC), 2)
        self.assertEqual(L.append(led, self.sig(self.ds[0]), SC), 0)
        self.assertEqual(len(led['entries']), 2)
        self.assertTrue(all(e['rules'] == L.rules_version(SC) for e in led['entries']))

    def test_pending_until_next_bar(self):
        self.put('A00001', [(101, 102, 100, 101)])
        led = L.load()
        L.append(led, self.sig(self.ds[0]), SC)
        L.score(led, self.conn, SC, self.ds[0])
        a = next(e for e in led['entries'] if e['code'] == 'A00001')
        self.assertEqual(a['status'], 'pending')

    def test_stop_closes_and_no_fill_voids(self):
        self.put('A00001', [(101, 102, 100, 101), (101, 101, 90, 91)] + [(91, 92, 90, 91)] * 3)
        self.put('B00002', [(105, 106, 104, 105), (105, 107, 104, 106)] + [(106, 107, 105, 106)] * 3)
        led = L.load()
        L.append(led, self.sig(self.ds[0]), SC)
        L.score(led, self.conn, SC, self.ds[4])
        st = {e['code']: e for e in led['entries']}
        self.assertEqual(st['A00001']['status'], 'closed')
        self.assertEqual(st['A00001']['result']['reason'], 'stop')
        self.assertEqual(st['B00002']['status'], 'void')          # 돌파가 110 에 안 닿았다
        L.save(led)
        again = L.load()
        sm = L.summary(again)
        self.assertEqual(sm['breakout']['closed'], 1)
        self.assertEqual(sm['watch']['void'], 1)
        text = ST.message(dict(as_of=self.ds[4], breakout=[], watch=[], paper=sm,
                               regime={}, disclaimer='x'), SC)
        self.assertIn('페이퍼 누적', text)
        self.assertIn('돌파: 신호 1건', text)

    def test_running_trade_stays_open_with_mark(self):
        self.put('A00001', [(101, 102, 100, 101)] + [(102, 103, 101, 102)] * 3)
        led = L.load()
        L.append(led, self.sig(self.ds[0]), SC)
        L.score(led, self.conn, SC, self.ds[3])
        a = next(e for e in led['entries'] if e['code'] == 'A00001')
        self.assertEqual(a['status'], 'open')
        self.assertEqual(a['result']['reason'], 'open_end')


if __name__ == '__main__':
    unittest.main()

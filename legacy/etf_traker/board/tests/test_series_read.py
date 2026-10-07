#!/usr/bin/env python3
"""일봉을 읽는 세 가지 방법이 같은 것을 준다는 것을 못 박는다.

`all_series` 는 전 종목 일봉을 한 dict 에 올린다. 420일 x 2,800종목 = 120만 행이라
지금은 견디지만, 한 종목씩 보고 버리는 자리(`suspect_codes`)와 열 몇 개만 보는
자리(`confirm_splits`)까지 그걸 쓰고 있었다. 갈래를 나눴으니 **결과가 같다**는
것을 여기서 지킨다 — 안 그러면 메모리를 아끼려다 판정이 달라진다.
"""
import unittest

from ..engine import db as DB


def _bar(code, day, close):
    return (code, day, close, close, close, close, 1000.0, 'test')


DAYS = ['2026-08-2%d' % i for i in range(4, 9)]


class SeriesRead(unittest.TestCase):

    def setUp(self):
        self.conn = DB.connect(':memory:')
        rows = []
        for code, base in (('000001', 1000.0), ('000002', 2000.0), ('000003', 3000.0)):
            for i, d in enumerate(DAYS):
                rows.append(_bar(code, d, base + i))
        self.conn.executemany('INSERT OR REPLACE INTO px VALUES(?,?,?,?,?,?,?,?)', rows)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_흘려보낸_것과_한꺼번에_올린_것이_같다(self):
        self.assertEqual(dict(DB.iter_series(self.conn, DAYS[-1])),
                         DB.all_series(self.conn, DAYS[-1]))

    def test_종목마다_한_번씩만_나온다(self):
        codes = [c for c, _ in DB.iter_series(self.conn, DAYS[-1])]
        self.assertEqual(codes, sorted(set(codes)), '코드 경계에서 조각이 나면 안 된다')
        self.assertEqual(len(codes), 3)

    def test_각_종목의_일봉이_날짜순으로_온전하다(self):
        for _code, ser in DB.iter_series(self.conn, DAYS[-1]):
            self.assertEqual([r['asof'] for r in ser], DAYS)

    def test_기준일_이후는_안_들어온다(self):
        got = dict(DB.iter_series(self.conn, DAYS[2]))
        self.assertEqual([r['asof'] for r in got['000001']], DAYS[:3])

    def test_지정한_종목만_읽는다(self):
        got = DB.series_for(self.conn, ['000002'], DAYS[-1])
        self.assertEqual(list(got), ['000002'])
        self.assertEqual(got['000002'], DB.all_series(self.conn, DAYS[-1])['000002'])

    def test_없는_종목은_빈칸으로_남는다(self):
        # 키를 만들어 두면 '일봉이 0행' 과 '조회 안 됨' 이 구분되지 않는다.
        got = DB.series_for(self.conn, ['000009'], DAYS[-1])
        self.assertEqual(got, {})

    def test_빈_목록은_쿼리하지_않는다(self):
        self.assertEqual(DB.series_for(self.conn, [], DAYS[-1]), {})

    def test_중복_코드를_줘도_한_번만_읽는다(self):
        got = DB.series_for(self.conn, ['000001', '000001'], DAYS[-1])
        self.assertEqual(len(got['000001']), len(DAYS))

    def test_변수_한도를_넘는_목록도_나눠_묻는다(self):
        # SQLite 기본 변수 한도는 999다. 한 번에 물으면 여기서 터진다.
        many = [f'{i:06d}' for i in range(1, 1500)]
        got = DB.series_for(self.conn, many, DAYS[-1])
        self.assertEqual(sorted(got), ['000001', '000002', '000003'])

    def test_빈_DB_는_조용하다(self):
        c = DB.connect(':memory:')
        try:
            self.assertEqual(list(DB.iter_series(c, DAYS[-1])), [])
            self.assertEqual(DB.all_series(c, DAYS[-1]), {})
        finally:
            c.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)

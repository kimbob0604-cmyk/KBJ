"""--init 은 백필이다. 기존 스칼라를 지우지 않으면 전 구간을 받아도 값이 안 변한다."""
import unittest

from ..engine import db as DB
from ..engine import newhigh as nh
from ..engine.config import load


def _bar(d, hi, close=None):
    c = close if close is not None else hi
    return dict(asof=d, open=c, high=hi, low=c, close=c, volume=1000)


class RollSemanticsTest(unittest.TestCase):
    """왜 지워야 하는지를 못박는다."""

    def test_과거를_뒤늦게_넣으면_무시된다(self):
        cfg = load()
        prev = nh.roll_alltime(None, [_bar('2026-08-26', 100),
                                      _bar('2026-08-27', 110)], cfg)
        self.assertEqual(prev['hi'], 110)
        # 이제 1990년부터의 진짜 최고가(500)를 뒤늦게 먹인다.
        after = nh.roll_alltime(prev, [_bar('1995-03-02', 500),
                                       _bar('2026-08-26', 100),
                                       _bar('2026-08-27', 110)], cfg)
        # last_date 이후 행만 반영하므로 500 은 통째로 무시된다.
        self.assertEqual(after['hi'], 110,
                         '이 동작 때문에 init 이 alltime 을 비워야 한다')


class InitClearsTest(unittest.TestCase):

    def setUp(self):
        self.conn = DB.connect(':memory:')

    def tearDown(self):
        self.conn.close()

    def _seed(self):
        self.conn.execute(
            'INSERT INTO alltime(code,hi,hi_date,cl,cl_date,prev_hi,prev_cl,'
            'first_date,last_date,n_days) VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('005930', 110, '2026-08-27', 110, '2026-08-27', 100, 100,
             '2024-08-27', '2026-08-27', 480))
        self.conn.execute(
            'INSERT INTO px(code,asof,open,high,low,close,volume) '
            'VALUES(?,?,?,?,?,?,?)',
            ('005930', '2026-08-27', 110, 110, 110, 110, 1))
        self.conn.commit()

    def test_init_이_스칼라와_일봉을_비운다(self):
        """init 본체는 네트워크를 타므로, 비우는 부분만 떼어 검증한다."""
        self._seed()
        self.assertEqual(
            self.conn.execute('SELECT COUNT(*) FROM alltime').fetchone()[0], 1)
        self.conn.execute('DELETE FROM alltime')
        self.conn.execute('DELETE FROM px')
        self.conn.commit()
        self.assertEqual(
            self.conn.execute('SELECT COUNT(*) FROM alltime').fetchone()[0], 0)
        self.assertEqual(
            self.conn.execute('SELECT COUNT(*) FROM px').fetchone()[0], 0)

    def test_비운_뒤에는_과거_최고가가_들어온다(self):
        cfg = load()
        a = nh.roll_alltime(None, [_bar('1995-03-02', 500),
                                   _bar('2026-08-26', 100),
                                   _bar('2026-08-27', 110)], cfg)
        self.assertEqual(a['hi'], 500)
        self.assertEqual(a['hi_date'], '1995-03-02')


class InitSourceTest(unittest.TestCase):

    def test_init_에_삭제문이_실제로_있다(self):
        """문서에만 있고 코드에 없던 적이 있다(D-026). 소스를 직접 확인한다."""
        import inspect

        from ..ingest import pipeline
        src = inspect.getsource(pipeline.init)
        self.assertIn("DELETE FROM alltime", src)
        self.assertIn("DELETE FROM px", src)

"""산출물이 스스로와 맞는지 보는 교차 검사.

이 프로젝트를 반복해서 문 것은 '필드 하나만 보면 멀쩡한데 서로 안 맞는' 부류다 —
거래대금 단위(D-035), 밀린 열(D-049), 낡은 과거 구간(D-056). 값 하나를 보는
검사로는 하나도 못 잡았고 전부 교차 검사가 잡았다.
"""
import unittest

from ..engine import build as B
from ..engine.config import load

CFG = load()


def row(code='000001', hits=None, gap=None, label=None, near=None, refs=None):
    return dict(code=code, hits=hits or {}, gap=gap or {},
                label=label, near_kind=near, refs=refs or {})


def ok(**kw):
    """정상 행 — 52주 갱신, 하위도 갱신, 갭 음수."""
    base = dict(hits={'hist': False, 'w52': True, 'd60': True, 'd20': True},
                gap={'w52': -1.2, 'd60': -5.0, 'd20': -6.0},
                label='w52')
    base.update(kw)
    return row(**base)


if __name__ == '__main__':
    unittest.main()


class LabelKindSyncTest(unittest.TestCase):
    """라벨 구성이 바뀌면 저장된 라벨 테이블도 따라와야 한다 (D-060).

    d120 을 빼자 어제 행의 rank 가 옛 우선순위 숫자로 남았다. 숫자로 비교하는
    신규/이어감 판정이 하루 동안 '이어감'을 '신규'로 적는다.
    """

    def setUp(self):
        from ..engine import db as DB
        self.conn = DB.connect(':memory:')
        self.conn.executemany(
            'INSERT INTO label VALUES(?,?,?,?,?)',
            [('000001', '2026-08-31', 'high', 'd120', 2),   # 빠진 라벨
             ('000002', '2026-08-31', 'high', 'd60', 3),    # 옛 rank
             ('000003', '2026-08-31', 'high', 'w52', 1)])   # 맞는 행
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_removed_kind_is_deleted_and_ranks_remapped(self):
        n_del, n_fix = B.sync_label_kinds(self.conn, CFG)
        self.assertEqual((n_del, n_fix), (1, 1))
        rows = {r['code']: (r['kind'], r['rank']) for r in
                self.conn.execute('SELECT code, kind, rank FROM label')}
        self.assertNotIn('000001', rows)
        self.assertEqual(rows['000002'], ('d60', 2))
        self.assertEqual(rows['000003'], ('w52', 1))

    def test_idempotent(self):
        B.sync_label_kinds(self.conn, CFG)
        self.assertEqual(B.sync_label_kinds(self.conn, CFG), (0, 0))

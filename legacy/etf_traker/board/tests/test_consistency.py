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


class ConsistencyTest(unittest.TestCase):

    def _why(self, rows):
        return ' / '.join(B.consistency_notes(rows, CFG))

    def test_clean_rows_pass(self):
        self.assertEqual(B.consistency_notes([ok(), ok(code='000002')], CFG), [])

    def test_no_rows_pass(self):
        self.assertEqual(B.consistency_notes([], CFG), [])

    def test_upper_label_without_lower(self):
        r = ok(hits={'hist': False, 'w52': True, 'd60': False, 'd20': True})
        self.assertIn('하위가 미갱신', self._why([r]))

    def test_uncomputed_lower_kind_is_not_a_violation(self):
        """px 가 420일치뿐이라 w52 를 계산 못 했는데 hist 는 스칼라로 판정된다.

        실측에서 090410 이 이걸로 잘못 찍혔다. 엔진이 아니라 검사가 틀렸다.
        """
        r = row(hits={'hist': True, 'w52': False, 'd60': True, 'd20': True},
                gap={'hist': -0.5, 'w52': None, 'd60': -5.0, 'd20': -6.0},
                label='hist')
        self.assertEqual(B.consistency_notes([r], CFG), [])

    def test_computed_lower_kind_still_flags(self):
        """계산은 됐는데 갱신이 없으면 여전히 모순이다. 구멍을 만들지 않는다."""
        r = row(hits={'hist': True, 'w52': False, 'd60': True, 'd20': True},
                gap={'hist': -0.5, 'w52': +2.0, 'd60': -5.0, 'd20': -6.0},
                label='hist')
        self.assertIn('하위가 미갱신', self._why([r]))

    def test_label_is_not_the_top(self):
        r = ok(label='d60')
        self.assertIn('최상위가 아님', self._why([r]))

    def test_label_without_any_hit(self):
        r = row(hits={'w52': False}, label='w52')
        self.assertIn('갱신이 없는데', self._why([r]))

    def test_positive_gap_on_a_hit(self):
        r = ok(gap={'w52': +2.5, 'd60': -5.0, 'd20': -6.0})
        self.assertIn('갭이 양수', self._why([r]))

    def test_proximity_outside_the_threshold(self):
        mx = CFG['proximity']['max_gap_pct']
        r = row(hits={}, gap={'w52': mx + 1.0}, near='w52')
        self.assertIn('임계 밖', self._why([r]))

    def test_proximity_inside_is_fine(self):
        mx = CFG['proximity']['max_gap_pct']
        r = row(hits={}, gap={'w52': mx - 0.1}, near='w52')
        self.assertEqual(B.consistency_notes([r], CFG), [])

    def test_proximity_without_a_gap_is_flagged(self):
        # 근접이라고 적어 놓고 갭이 없으면 무엇으로 판정했는지 알 수 없다.
        r = row(hits={}, gap={}, near='w52')
        self.assertIn('임계 밖', self._why([r]))

    def test_examples_are_bounded(self):
        rows = [ok(code=f'{i:06d}', label='d60') for i in range(50)]
        note = self._why(rows)
        self.assertIn('50종목', note)
        self.assertEqual(note.count(' / '), B.CONSIST_EX - 1)

    def test_message_names_the_kind_and_value(self):
        """'그렇다' 만 적으면 다음 사람이 할 수 있는 게 없다.

        첫 판은 코드만 적어서, 실측 위반을 보고도 어느 종류가 왜 어긋났는지
        몰라 두 번 헛다리를 짚었다.
        """
        r = ok(hits={'hist': False, 'w52': True, 'd60': False, 'd20': True},
               gap={'w52': -1.2, 'd60': +0.8, 'd20': -6.0})
        note = self._why([r])
        self.assertIn('d60', note)
        self.assertIn('+0.80%', note)
        self.assertIn('w52 인데', note)

    def test_proximity_message_says_the_threshold(self):
        mx = CFG['proximity']['max_gap_pct']
        note = self._why([row(hits={}, gap={'w52': mx + 3}, near='w52')])
        self.assertIn(f'임계 {mx}', note)
        self.assertIn('w52', note)

    def test_build_calls_it(self):
        import inspect
        src = inspect.getsource(B)
        self.assertIn('consistency_notes(rows, cfg)', src)


if __name__ == '__main__':
    unittest.main()


class HistRefContainsWindowsTest(unittest.TestCase):
    """역사적 최고가는 어떤 창의 최고가보다도 작을 수 없다.

    상장 이후 전체가 직전 252일을 포함하기 때문이다. 작다면 alltime 스칼라와
    px 일봉이 서로 다른 시계열을 가리키는 것이고, 그러면 라벨 판정이 통째로
    어긋난다. 실측에서 090410 이 hist 기준 42% 낮았다.
    """

    def _why(self, r):
        return ' / '.join(B.consistency_notes([r], CFG))

    def test_hist_below_a_window_is_flagged(self):
        r = row(refs={'hist': 1000.0, 'w52': 1730.0, 'd60': 1200.0})
        note = self._why(r)
        self.assertIn('역사적 최고가가 창 최고가보다 낮음', note)
        self.assertIn('1,730', note)
        self.assertIn('w52', note)

    def test_hist_above_all_windows_is_fine(self):
        self.assertEqual(
            B.consistency_notes([row(refs={'hist': 2000.0, 'w52': 1730.0})], CFG), [])

    def test_equal_is_fine(self):
        # 오늘 새로 쓴 최고가가 곧 창 최고가인 것은 정상이다.
        self.assertEqual(
            B.consistency_notes([row(refs={'hist': 1730.0, 'w52': 1730.0})], CFG), [])

    def test_missing_hist_is_not_flagged(self):
        # 이력이 짧아 hist 를 계산 못 한 것은 모순이 아니다.
        self.assertEqual(
            B.consistency_notes([row(refs={'hist': None, 'w52': 1730.0})], CFG), [])

    def test_missing_window_is_not_flagged(self):
        self.assertEqual(
            B.consistency_notes([row(refs={'hist': 1000.0, 'w52': None})], CFG), [])


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

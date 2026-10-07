"""점프가 분할 때문인지 실제 등락인지는 공시가 답한다 (D-001).

split_guard 는 하루 ±31% 를 잡을 뿐 이유를 모른다. 12종목이 그 상태로
역사적 판정에서 빠져 있었다.
"""
import unittest

from .. import run as R
from ..ingest import dart


class _Fake:
    ACTION_WINDOW = dart.ACTION_WINDOW

    def __init__(self, acts=None, boom=None):
        self._acts, self._boom = acts or [], boom
        self.asked = []

    def stock_actions(self, code, bgn, end, s=None):
        self.asked.append((code, bgn, end))
        if self._boom:
            raise self._boom
        return self._acts


class VerdictTest(unittest.TestCase):

    def test_no_action_means_real_move(self):
        v = R._action_verdict(_Fake(), '005930', '2026-03-04')
        self.assertIn('공시 없음', v)
        self.assertIn('과하다', v)

    def test_action_is_named(self):
        f = _Fake([{'date': '2026-02-20', 'title': '주식분할결정', 'url': 'u'}])
        v = R._action_verdict(f, '005930', '2026-03-04')
        self.assertIn('공시 1건', v)
        self.assertIn('주식분할결정', v)
        self.assertNotIn('공시 없음', v)

    def test_window_brackets_the_jump(self):
        f = _Fake()
        R._action_verdict(f, '005930', '2026-03-04')
        _, bgn, end = f.asked[0]
        self.assertLess(bgn, '2026-03-04')
        self.assertGreater(end, '2026-03-04')

    def test_failure_is_not_reported_as_absence(self):
        # 못 본 것을 '없다' 로 적으면 그 다음 판단이 통째로 틀린다.
        v = R._action_verdict(_Fake(boom=RuntimeError('HTTP 500')), '005930',
                              '2026-03-04')
        self.assertIn('실패', v)
        self.assertNotIn('공시 없음', v)

    def test_unreadable_date_is_reported(self):
        v = R._action_verdict(_Fake(), '005930', '')
        self.assertIn('못 함', v)


class FilterTest(unittest.TestCase):
    """제목 필터가 주식 수를 바꾸는 공시만 남기는지."""

    def test_keeps_corp_actions(self):
        for t in ('주식분할결정', '주식병합결정', '감자결정', '무상증자결정',
                  '[기재정정]주식분할결정', '액면분할'):
            self.assertTrue(any(w in t for w in dart.CORP_ACTION), t)

    def test_drops_routine_filings(self):
        for t in ('분기보고서', '단일판매·공급계약체결', '최대주주변경',
                  '현금·현물배당결정', '유상증자결정'):
            self.assertFalse(any(w in t for w in dart.CORP_ACTION), t)


if __name__ == '__main__':
    unittest.main()

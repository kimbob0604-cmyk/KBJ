"""
고가/종가 기준 전환 — engine/build.achieved_rows + web/render 의 구분 칸.

증권사 스크리너 상당수가 종가 기준이라 두 목록이 갈린다. 사용자가 "금요일 기준
GS 랑 혜인만 있는데" 라고 한 것이 이 차이였다(D-043).

여기서 막으려는 실수: 고가 기준 목록을 만들어 놓고 화면에서 종가 라벨로 다시
거르는 것. 그러면 **종가로만 신고가를 낸 종목이 조용히 사라진다.**
"""
import unittest

from board.engine import build as B
from board.web import render as R

SHOW = {'hist', 'w52', 'd60'}
RANK = {'hist': 0, 'w52': 1, 'd60': 2, 'd20': 3}


def row(name, hi=None, cl=None, turnover=0.0, label=...):
    """보드가 내보내는 한 줄. 두 기준을 **이름으로** 담는다.

    label/hits 는 기본 기준(default_basis)이라 설정에 따라 가리키는 기준이
    바뀐다. 화면은 high_basis/close_basis 를 읽는다.
    """
    return dict(name=name, turnover=turnover,
                label=(cl if label is ... else label),   # 기본 기준 = 종가
                high_basis=dict(label=hi, hits={hi: True} if hi else {}),
                close_basis=dict(label=cl, hits={cl: True} if cl else {}))


class AchievedRows(unittest.TestCase):
    def test_high_only_rows_are_kept_when_default_is_close(self):
        """기본이 종가여도 고가로만 뚫은 종목은 남아야 한다.

        빠지면 화면의 '고가 기준' 토글이 빈 표가 된다 — 토글이 거짓말을 한다.
        """
        rows = [row('고가만', hi='w52'), row('종가만', cl='w52')]
        got = [r['name'] for r in B.achieved_rows(rows, SHOW, RANK)]
        self.assertIn('고가만', got)

    def test_close_only_rows_are_kept(self):
        # 이게 이 파일의 존재 이유다. 고가로 거르면 이 종목이 사라진다.
        rows = [row('고가만', hi='w52'), row('종가만', cl='w52')]
        got = [r['name'] for r in B.achieved_rows(rows, SHOW, RANK)]
        self.assertIn('종가만', got)
        self.assertEqual(len(got), 2)

    def test_rows_with_neither_label_are_dropped(self):
        rows = [row('아무것도아님'), row('있음', hi='d60')]
        self.assertEqual([r['name'] for r in B.achieved_rows(rows, SHOW, RANK)],
                         ['있음'])

    def test_below_threshold_labels_are_dropped(self):
        # min_display_kind 아래(20일)는 표에 넣지 않는다. 하루 수백 종목이다.
        rows = [row('이십일', hi='d20', cl='d20')]
        self.assertEqual(B.achieved_rows(rows, SHOW, RANK), [])

    def test_sorted_by_grade_then_turnover(self):
        rows = [row('작은역사적', hi='hist', turnover=10),
                row('큰52주', hi='w52', turnover=9999),
                row('큰역사적', hi='hist', turnover=500)]
        self.assertEqual([r['name'] for r in B.achieved_rows(rows, SHOW, RANK)],
                         ['큰역사적', '작은역사적', '큰52주'])

    def test_close_only_row_sorts_by_its_close_grade(self):
        rows = [row('고가60일', hi='d60', turnover=1),
                row('종가역사적', cl='hist', turnover=1)]
        self.assertEqual([r['name'] for r in B.achieved_rows(rows, SHOW, RANK)][0],
                         '종가역사적')


class BasisCell(unittest.TestCase):
    def setUp(self):
        self._saved = dict(R.LABEL_KO)
        R.LABEL_KO.update(hist='역사적', w52='52주', d60='60일')

    def tearDown(self):
        R.LABEL_KO.clear()
        R.LABEL_KO.update(self._saved)

    def test_both_bases_are_rendered_and_one_is_hidden(self):
        # 자바스크립트가 라벨을 만들어 내면 화면과 데이터가 갈라진다.
        # 서버가 둘 다 그려 두고 토글은 보이기만 바꾼다.
        h = R._basis_cell(row('x', hi='hist', cl='w52'))
        self.assertIn('data-b="hi"', h)
        self.assertIn('data-b="cl" hidden', h)
        self.assertIn('역사적', h)
        self.assertIn('52주', h)

    def test_missing_other_basis_says_so(self):
        h = R._basis_cell(row('x', hi='w52', cl=None))
        self.assertIn('종가 미달', h)

    def test_close_only_row_shows_a_dash_for_high(self):
        h = R._basis_cell(row('x', hi=None, cl='w52'))
        self.assertIn('고가 미달', h)     # 종가 쪽 칸에서 본 고가 결과
        self.assertIn('–', h)             # 고가 쪽 칸의 라벨 자리

    def test_pills_show_the_default_basis_count_first(self):
        # 표는 종가 기준으로 그려져 있는데 알약만 고가 수를 들고 있으면 어긋난다.
        h = R._pills({'w52': 7}, 0, ['w52'], {'w52': 3}, dflt='cl')
        self.assertIn('data-c-hi="7"', h)
        self.assertIn('data-c-cl="3"', h)
        self.assertIn('<span class="c">3</span>', h)

    def test_pills_follow_the_high_default_too(self):
        h = R._pills({'w52': 7}, 0, ['w52'], {'w52': 3}, dflt='hi')
        self.assertIn('<span class="c">7</span>', h)

    def test_row_carries_both_labels_as_data(self):
        tr = R._row_achieved(dict(row('x', hi='hist', cl='w52'),
                                  code='000001', stage='', status=None,
                                  chg_pct=1.0, vol_mult=1.0, mktcap=1.0))
        self.assertIn('data-hi="hist"', tr)
        self.assertIn('data-cl="w52"', tr)


if __name__ == '__main__':
    unittest.main()

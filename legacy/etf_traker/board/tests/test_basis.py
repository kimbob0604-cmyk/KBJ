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

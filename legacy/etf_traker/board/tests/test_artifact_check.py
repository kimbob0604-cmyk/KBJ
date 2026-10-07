#!/usr/bin/env python3
"""게시 전 점검 — 17:09 루틴 3번 검사와 같은 판정인지."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from board.tools import artifact_check as AC       # noqa: E402


def board(**kw):
    d = dict(as_of='2026-09-28', universe_n=2678,
             market=dict(flows=[dict(who=w, value=1.0) for w in ('기관계', '외국인', '개인')]),
             notices=dict(miss=[], scope=['섹터 미배정 75종목 — …',
                                          '투자자별 수급에서 기타법인는 빠져 있습니다 — (D-082)']))
    d.update(kw)
    return d


def ok(d, **kw):
    return {n: o for n, o, _ in AC.check(d, **kw)}


class TestArtifactCheck(unittest.TestCase):
    def test_current_board_passes(self):
        self.assertTrue(all(ok(board(), asof='2026-09-28').values()))

    def test_each_rule_fails(self):
        self.assertFalse(ok(board(universe_n=1999))['종목 수'])
        self.assertFalse(ok(board(notices=dict(miss=[], scope=['섹터 미배정 912종목'])))['섹터 미배정'])
        # 기타법인 안내 없이 셋이면 실패, 둘이면 실패
        self.assertFalse(ok(board(notices=dict(miss=[], scope=['섹터 미배정 1종목'])))['투자자 수급'])
        self.assertFalse(ok(board(market=dict(flows=[dict(who='개인', value=1)])))['투자자 수급'])
        self.assertFalse(ok(board(notices=dict(miss=['시세를 덜 받았습니다'],
                                               scope=['섹터 미배정 1종목'])))["'덜 받았습니다'·'줄었습니다'"])
        self.assertFalse(ok(board(), asof='2026-09-29')['기준일'])


if __name__ == '__main__':
    unittest.main()

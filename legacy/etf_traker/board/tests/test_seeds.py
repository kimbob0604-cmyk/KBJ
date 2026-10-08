"""
themes.yaml 시드 해결 — board/engine/themes.py.

매일 "시드 35건이 상장 마스터에 없습니다" 만 뜨고 있었다. 그중 다섯은 애초에
비상장이라 영원히 안 붙는다. 합쳐 세면 진짜 오타·사명변경이 그 숫자에 묻힌다.
"""
import unittest

import yaml

from board.engine import themes as TH
from board.engine.config import ROOT

import os

CFG = dict(themes=dict(seed_confidence=0.9))
UNIVERSE = {'000001': dict(name='한전기술'), '000002': dict(name='두산에너빌리티')}


def ty(seeds, unlisted=None):
    d = dict(axes={'project_chain': dict(stages=['설계', '주기기'])},
             themes=[dict(id='nuclear', name='원전', axis='project_chain',
                          seeds=seeds)])
    if unlisted:
        d['unlisted'] = unlisted
    return d


if __name__ == '__main__':
    unittest.main()


class BannerWording(unittest.TestCase):
    """화면이 "사명 변경" 이라고 **단정하지 않는다**.

    이름이 비슷한 것과 같은 회사인 것은 다르다. 실측에서
    SK머티리얼즈 → 하나머티리얼즈처럼 업종 접미사만 겹친 것이 섞여 나왔다.
    기계는 찾을 자리를 좁혀 줄 뿐이다 (CLAUDE.md 2장 1번).
    """

    def _banner(self, unresolved):
        from board.web import render as R
        # newhigh.json 의 최소 형태. 실제 파일은 engine 이 늘 이 필드들을 채운다.
        nh = dict(labels={}, counts={}, achieved=[], proximity=[],
                  as_of='2026-08-31', basis='high',
                  thresholds=dict(
                      proximity=dict(max_gap_pct=5.0, min_mktcap_eok=200.0,
                                     narrow_days=5),
                      lookback=dict(d60=60, w52=252)))
        return R.build(nh, {}, {}, {}, dict(theme_seeds_unresolved=unresolved),
                       meta={})

    def test_does_not_assert_a_rename(self):
        doc = self._banner([dict(seed='SK머티리얼즈', known=None,
                                 near=['하나머티리얼즈'])])
        self.assertIn('확인이 필요합니다', doc)
        self.assertNotIn('사명 변경으로 보입니다', doc)

    def test_duplicate_seed_names_are_shown_once(self):
        # 같은 이름이 두 테마에 있으면 목록에 두 번 뜨던 것.
        doc = self._banner([dict(seed='한화세미텍', known=None, near=[]),
                            dict(seed='한화세미텍', known=None, near=[])])
        self.assertEqual(doc.count('한화세미텍'), 1)

    def test_the_two_buckets_are_separate(self):
        doc = self._banner([dict(seed='한국조선해양', known=None,
                                 near=['HD한국조선해양']),
                            dict(seed='뉴로스', known=None, near=[])])
        self.assertIn('한국조선해양→HD한국조선해양', doc)
        self.assertIn('비슷한 이름조차 없습니다', doc)
        self.assertIn('상장폐지·비상장이면', doc)

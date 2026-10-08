"""KBJ P3 다리 시험 — board 신고가 엔진은 kbj 정본을 다시 내보낼 뿐이다(docs/p3_design.md §1.3·§8.1).

계산은 `kbj.engines.board` 한 곳이고(D-P3-10), legacy `engine/{newhigh,aggregate,rankings,kinds,themes,
build}.py` 는 같은 함수 객체를 내보내는 shim 이다. legacy `build.run` 은 sqlite 를 읽어 kbj
`compute_day` 를 부르고 state 파일을 쓰기만 한다. 임계값은 레포 루트 config/board.yaml 한 곳이다.
"""
import unittest

import kbj.engines.board.aggregate as k_agg
import kbj.engines.board.build as k_build
import kbj.engines.board.kinds as k_kinds
import kbj.engines.board.newhigh as k_nh
import kbj.engines.board.rankings as k_rk
import kbj.engines.board.themes as k_th
from kbj.engines.board.config import BoardConfig

from ..engine import aggregate, build, config, kinds, newhigh, rankings, themes


class KbjEngineShimTest(unittest.TestCase):
    def test_legacy_engine_is_the_kbj_engine(self):
        self.assertIs(newhigh.evaluate, k_nh.evaluate)
        self.assertIs(newhigh.roll_alltime, k_nh.roll_alltime)
        self.assertIs(newhigh.hist_ref_for, k_nh.hist_ref_for)
        self.assertIs(aggregate.detect, k_agg.detect)
        self.assertIs(aggregate.heatmap, k_agg.heatmap)
        self.assertIs(kinds.of, k_kinds.of)
        self.assertIs(themes.build, k_th.build)
        self.assertIs(rankings.sector_board, k_rk.sector_board)
        self.assertIs(build.compute_day, k_build.compute_day)
        self.assertIs(build.consistency_notes, k_build.consistency_notes)
        self.assertIs(build.close_provenance, k_build.close_provenance)
        # 계산 본문이 legacy 에 남아 있지 않다 — run 은 kbj compute_day 를 부른다
        import inspect
        src = inspect.getsource(build.run)
        self.assertIn('compute_day(inp, cfg)', src)
        self.assertNotIn('nh.evaluate(', inspect.getsource(build))
        # 엔진 임계값은 config/board.yaml 하나 — legacy load() 가 그 값을 그대로 돌려준다
        eng = BoardConfig.load()
        cfg = config.load()
        for sec in ('newhigh', 'proximity', 'display', 'integrity', 'detect', 'rankings'):
            self.assertEqual(cfg[sec], eng[sec], sec)
        self.assertIn('news', cfg)   # 엔진 밖 절은 settings.yaml 에 그대로


if __name__ == '__main__':
    unittest.main()

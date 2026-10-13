"""KBJ P5 다리 시험 — board 미국장(us/)은 kbj 정본을 다시 내보낼 뿐이다(docs/p5_design.md §1.6·§1.9).

계산은 `kbj.engines.us`(engine·board·brief·sectors), 수집은 `kbj.data.private.{nasdaq,stooq,yahoo}`
한 곳이고, legacy `us/{engine,sectors,brief}.py` 는 같은 함수 객체를 내보내는 shim, `us/sources.py`
는 예전 호출 모양을 kbj 클라이언트로 잇는 얇은 함수, `us/build.py` 는 sqlite·state 파일 입출력만
남았다. 설정은 레포 루트 config/us.yaml, 섹터 사전은 config/knowledge/us/ 한 곳이다(git mv).
조합 탐색(`us/btsearch.py`)은 국장 백테스트 엔진과 함께 P8 에 옮긴다(legacy 에 남음).
"""
import inspect
import os
import unittest

import kbj.data.private.nasdaq.client as k_nq
import kbj.data.private.stooq.client as k_sq
import kbj.data.private.yahoo.client as k_yh
import kbj.engines.us.board as k_board
import kbj.engines.us.brief as k_brief
import kbj.engines.us.engine as k_engine
import kbj.engines.us.sectors as k_sec
from kbj.engines.us.config import load_us, us_config_path, us_knowledge_path

from .. import run as R
from ..us import brief, build, engine, sectors, sources


class KbjUsShimTest(unittest.TestCase):
    def test_legacy_us_is_the_kbj_us(self):
        self.assertIs(engine.hits_on, k_engine.hits_on)
        self.assertIs(engine.evaluate_all, k_engine.evaluate_all)
        self.assertIs(engine.summary, k_engine.summary)
        self.assertIs(sectors.classify, k_sec.classify)
        self.assertIs(brief.render, k_brief.render)
        self.assertIs(brief.telegram_chunks, k_brief.telegram_chunks)
        self.assertIs(build.trend, k_board.trend)
        self.assertIs(sources.parse_screener, k_nq.parse_screener)
        self.assertIs(sources._num, k_nq.num)
        self.assertIs(sources.parse_nasdaq_history, k_nq.parse_nasdaq_history)
        self.assertIs(sources.parse_stooq, k_sq.parse_stooq)
        self.assertIs(sources.parse_yahoo, k_yh.parse_yahoo)
        # 계산 본문이 legacy 에 남아 있지 않다 — build 는 kbj board.build 를 부른다
        self.assertIn('_kbj_build(', inspect.getsource(build.build))
        self.assertNotIn('E.hits_on(', inspect.getsource(build))
        self.assertNotIn('csv.DictReader', inspect.getsource(sources))

    def test_config_and_knowledge_moved(self):
        self.assertEqual(os.path.realpath(R.US_CFG), os.path.realpath(us_config_path()))
        self.assertEqual(R.us_cfg()['newhigh'], load_us()['newhigh'])
        self.assertEqual(R.us_cfg()['newhigh']['lookback']['w52'], 252)   # ADR 0001 Q10
        self.assertTrue(us_knowledge_path('us_sectors.yaml').exists())
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertFalse(os.path.exists(os.path.join(here, 'config', 'us.yaml')))
        self.assertFalse(os.path.exists(os.path.join(here, 'us', 'knowledge')))


if __name__ == '__main__':
    unittest.main()

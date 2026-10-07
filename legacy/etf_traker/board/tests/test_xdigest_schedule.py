"""X 다이제스트의 시각 셋이 서로 맞는가.

2026-09-23 발송을 09:00 → 08:00 으로 당겼다. 시각이 세 군데 있다.

  config/xdigest.yaml  window_end_hm  창의 끝 = 수집 dispatch 시각
                       send_at        1행 제목 시각 = 지연 표시 임계
  xdigest.yml          schedule       GitHub 크론 뒤받침 (UTC)

하나만 옮기면 조용히 틀린다. 창의 끝이 발송보다 늦으면 분석할 시간이 없고,
크론이 창의 끝보다 이르면 **아직 안 끝난 창**을 모아 보낸다. 외부 스케줄러
(cron-job.org)는 레포 밖이라 여기서 못 본다 — GITHUB.md 5-2 표가 그 자리다.
"""
import os
import re
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _hm(s):
    h, m = map(int, str(s).split(':'))
    return h * 60 + m


class ScheduleIsConsistent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'board', 'config', 'xdigest.yaml'), encoding='utf-8') as f:
            cls.cfg = yaml.safe_load(f)
        with open(os.path.join(ROOT, '.github', 'workflows', 'xdigest.yml'), encoding='utf-8') as f:
            cls.wf = yaml.safe_load(f)

    def test_창의_끝이_발송보다_앞이다(self):
        end, send = _hm(self.cfg['window_end_hm']), _hm(self.cfg['send_at'])
        self.assertLess(end, send, '창이 발송 뒤에 끝나면 분석·렌더할 시간이 없다')
        # 분석·검증·렌더에 5~7분이 든다(XDIGEST.md 4장). 여유를 10분은 둔다.
        self.assertGreaterEqual(send - end, 10)

    def test_크론_뒤받침은_창의_끝_뒤에_돈다(self):
        """앞에 돌면 아직 끝나지 않은 창을 모아 보낸다."""
        on = self.wf.get('on') or self.wf.get(True)          # PyYAML 은 'on' 을 True 로 읽는다
        crons = [c['cron'] for c in on['schedule']]
        self.assertTrue(crons)
        end = _hm(self.cfg['window_end_hm'])
        for c in crons:
            m, h = map(int, c.split()[:2])
            kst = ((h + 9) % 24) * 60 + m
            self.assertGreater(kst, end, f"크론 '{c}' 이 KST {kst // 60:02d}:{kst % 60:02d} 로 창의 끝보다 이르다")

    def test_코드_기본값이_설정과_같다(self):
        """설정 파일을 못 읽는 날도 같은 시각으로 돈다."""
        from ..ingest import bsky
        from ..xdigest import render
        self.assertEqual(bsky.WINDOW_END_HM, self.cfg['window_end_hm'])
        self.assertEqual(render.SEND_AT, self.cfg['send_at'])

    def test_등록표가_같은_시각을_말한다(self):
        """사람이 cron-job.org 에 옮겨 적는 표다 — 설정과 어긋나면 그대로 틀린다."""
        with open(os.path.join(ROOT, 'board', 'docs', 'GITHUB.md'), encoding='utf-8') as f:
            doc = f.read()
        row = next(l for l in doc.split('\n') if '`xdigest-daily`' in l and '|' in l)
        self.assertIn(f"**매일 {self.cfg['window_end_hm']} KST**", row)


if __name__ == '__main__':
    unittest.main()

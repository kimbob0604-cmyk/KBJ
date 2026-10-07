"""X 다이제스트 미리보기(--xdigest-preview)가 **아무것도 남기지 않는가.**

2026-09-23 구획(D-NEXT-Q)을 붙인 날, 그날 다이제스트를 이미 보내 수집이 표식을
보고 멈췄다 — 새 구획을 실물로 볼 길이 다음 아침뿐이었다. 미리보기는 표식을
무시하고 돌되, 그날 발송본의 근거 파일(state/xdigest)과 표식을 건드리면 안 된다.
"""
import copy
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from .. import run
from ..ingest import bsky as BS
from ..xdigest import render as XR, send as XS

HERE = os.path.dirname(os.path.abspath(__file__))
FX = os.path.join(HERE, 'fixtures')


def _posts():
    with open(os.path.join(FX, 'xdigest_render_posts.json'), encoding='utf-8') as f:
        p = json.load(f)
    b = p.setdefault('sources', {}).setdefault('bluesky', {})
    for k in ('queries', 'calls', 'got', 'in_window', 'kept', 'failed', 'blocked'):
        b.setdefault(k, 1)
    b.setdefault('errors', [])
    p.setdefault('coverage', {}).setdefault('first', None)
    p['coverage'].setdefault('last', None)
    return p


class Preview(unittest.TestCase):
    def setUp(self):
        # [KBJ P1] 원본은 레포에 커밋된 board/state/xdigest(실제 게시물·발송본)를 '실제
        # state' 로 봤다. 그 디렉터리는 개인 데이터라 옮기지 않았다(MIGRATION.md). 같은
        # 모양의 합성 state(fixtures/xdigest_state — synthetic_fixtures.py 가 만든다)를
        # 임시 디렉터리에 깔아 그 자리에 둔다. 아래 시험은 그대로다.
        self._orig = (BS.XSTATE, XR.STATE)
        self._tmp = tempfile.mkdtemp(prefix='xdigest-state-')
        state = os.path.join(self._tmp, 'xdigest')
        shutil.copytree(os.path.join(FX, 'xdigest_state'), state)
        BS.XSTATE = XR.STATE = state
        self.real = (BS.XSTATE, XR.STATE)

    def tearDown(self):
        BS.XSTATE, XR.STATE = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, send=False, sender=None):
        posts = _posts()
        with mock.patch.object(BS, 'collect', return_value=copy.deepcopy(posts)), \
             mock.patch.object(XS, 'mark', return_value=XR.today()), \
             mock.patch('board.xdigest.analyze.run',
                        return_value=({}, {'error': '시험 — 분석 생략'}, {})), \
             mock.patch('board.report.telegram.send',
                        side_effect=sender or (lambda *a, **k: (True, 'ok'))), \
             mock.patch.object(XS, 'chat', return_value=('1', 'TELEGRAM_CHAT_ID')), \
             mock.patch.object(run, 'log'):
            return run.cmd_xdigest_preview(send=send)

    def test_표식이_있어도_돌고_실제_state_는_그대로다(self):
        real_state = self.real[0]
        before = sorted(os.listdir(real_state)) if os.path.isdir(real_state) else []
        self.assertEqual(self._run(), 0)
        self.assertNotEqual(BS.XSTATE, real_state, '임시 디렉터리로 옮기지 않았다')
        self.assertTrue(os.path.exists(BS.state_path(XR.today(), 'posts.json')))
        self.assertTrue((XR.read(XR.today(), 'digest.txt') or '').strip())
        after = sorted(os.listdir(real_state)) if os.path.isdir(real_state) else []
        self.assertEqual(before, after)

    def test_보낼_때_첫_통에_미리보기가_붙고_표식은_안_쓴다(self):
        sent = []
        mp = XS.mark_path()
        before = os.path.getmtime(mp) if os.path.exists(mp) else None
        rc = self._run(send=True, sender=lambda text, **k: (sent.append(text) or (True, 'ok')))
        self.assertEqual(rc, 0)
        after = os.path.getmtime(mp) if os.path.exists(mp) else None
        self.assertEqual(before, after, '미리보기가 표식을 썼다')
        self.assertTrue(sent)
        self.assertTrue(sent[0].startswith('🔎 미리보기'))
        self.assertFalse(any(t.startswith('🔎') for t in sent[1:]))


if __name__ == '__main__':
    unittest.main()

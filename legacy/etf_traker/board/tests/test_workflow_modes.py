"""워크플로가 부르는 플래그가 CLI 에 실제로 있는지 본다.

`.github/workflows/xdigest.yml` 의 `check` 모드가 `--xdigest-check` 를 불렀는데
그 플래그가 없었다. `|| true` 와 `exit 0` 이 붙어 있어 **0초에 초록으로** 끝났고,
러너 로그에 argparse usage 만 찍혔다. 발송 전에 수집원을 확인하려고 둔 모드가
아무것도 확인하지 않은 채 통과한 것이다(CLAUDE.md 2장 6번).

`--help` 는 사람이 읽을 때만 결손을 드러낸다. 워크플로가 부르는 이름은 기계가
대조해야 한다 — YAML 에서 `board.run --플래그` 를 전부 긁어 파서에 있는지 본다.
"""
import os
import re
import unittest

from .. import run

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WF = os.path.join(ROOT, '.github', 'workflows')

# `python3 -m board.run --xdigest-collect` · `board.run --send xdigest` 꼴을 잡는다.
CALL = re.compile(r'board\.run\s+((?:--[a-z0-9-]+\s*)+)')


def flags_in(path):
    with open(path, encoding='utf-8') as f:
        body = f.read()
    out = set()
    for m in CALL.finditer(body):
        out.update(re.findall(r'--[a-z0-9-]+', m.group(1)))
    return out


class WorkflowFlagsExist(unittest.TestCase):
    def setUp(self):
        self.known = {o for a in run.build_parser()._actions
                      for o in a.option_strings}

    def test_다이제스트_워크플로의_플래그가_전부_있다(self):
        used = flags_in(os.path.join(WF, 'xdigest.yml'))
        self.assertTrue(used, 'xdigest.yml 에서 board.run 호출을 못 찾았다')
        self.assertEqual(sorted(used - self.known), [])

    def test_보드_워크플로의_플래그가_전부_있다(self):
        used = flags_in(os.path.join(WF, 'board.yml'))
        self.assertTrue(used, 'board.yml 에서 board.run 호출을 못 찾았다')
        self.assertEqual(sorted(used - self.known), [])

    def test_모든_워크플로를_훑는다(self):
        """새 워크플로가 생겨도 자동으로 든다 — 파일을 손으로 적지 않는다."""
        bad = {}
        for name in sorted(os.listdir(WF)):
            if not name.endswith(('.yml', '.yaml')):
                continue
            miss = sorted(flags_in(os.path.join(WF, name)) - self.known)
            if miss:
                bad[name] = miss
        self.assertEqual(bad, {})


class CheckModeDoesNotSwallow(unittest.TestCase):
    """점검이 실패를 삼키면 '점검했다' 가 거짓이 된다."""

    def test_점검_모드가_종료코드를_버리지_않는다(self):
        with open(os.path.join(WF, 'xdigest.yml'), encoding='utf-8') as f:
            body = f.read()
        i = body.index('check)')
        branch = body[i:body.index(';;', i)]
        self.assertIn('rc=$?', branch)
        self.assertIn('exit $rc', branch)
        self.assertNotIn('|| true', branch)


if __name__ == '__main__':
    unittest.main()

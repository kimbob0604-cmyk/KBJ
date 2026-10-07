"""CLI 는 이 저장소를 부리는 유일한 문이다 — `--help` 가 그 문의 목록이다.

`.github/workflows/board.yml` 의 mode 들이 전부 `board/run.py` 의 플래그로
내려오고, 처음 보는 사람(그리고 이 코드를 고치러 오는 에이전트)은 `--help` 로
무엇이 있는지 알아낸다. 설명이 빈 플래그는 목록에 이름만 있고 뜻이 없어 그
경로가 사실상 안 보인다 — 실제로 아홉 개가 그랬다(`--check --init --daily
--engine --render --test --demo --verify-adjust --no-render`).

여기서 보는 것은 **문구의 품질이 아니라 있는지 없는지**다. 품질은 사람이 본다.
"""
import unittest

from .. import run


class HelpIsComplete(unittest.TestCase):
    def setUp(self):
        self.actions = [a for a in run.build_parser()._actions
                        if a.dest != 'help']

    def test_모든_플래그에_설명이_있다(self):
        bare = [a.option_strings[0] for a in self.actions
                if not (a.help or '').strip()]
        self.assertEqual(bare, [], f'설명 없는 플래그: {bare}')

    def test_플래그가_하나도_안_빠졌다(self):
        """파서를 못 만들었는데 시험이 통과하는 일을 막는다."""
        self.assertGreater(len(self.actions), 30)

    def test_설명은_한글_한_줄이다(self):
        """줄바꿈이 든 설명은 argparse 가 다시 접어 목록을 흩뜨린다."""
        bad = [a.option_strings[0] for a in self.actions if '\n' in (a.help or '')]
        self.assertEqual(bad, [], f'줄바꿈이 든 설명: {bad}')


class ParserIsSeparable(unittest.TestCase):
    """`build_parser` 가 `main` 에서 떨어져 있어야 위 시험이 성립한다."""

    def test_파서를_돌리지_않고_만들_수_있다(self):
        ap = run.build_parser()
        a = ap.parse_args(['--check'])
        self.assertTrue(a.check)

    def test_모드는_서로_배타다(self):
        with self.assertRaises(SystemExit):
            run.build_parser().parse_args(['--check', '--daily'])

    def test_모드를_안_주면_거절한다(self):
        with self.assertRaises(SystemExit):
            run.build_parser().parse_args([])


if __name__ == '__main__':
    unittest.main()

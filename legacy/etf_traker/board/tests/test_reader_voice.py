"""리포트는 리포트의 말만 한다 — 우리 검증·산출 절차의 말은 섞이지 않는다.

2026-09-22 발송된 보드 메시지에 이런 줄들이 실렸다:

    빠진 데이터 6건 — … · 산출물 정합성 위반 — 상위 라벨인데 하위가 미갱신
    1종목: 00050… · 산출물 정합성 위반 — 역사적 최고가가 창 최고가보다 낮음 5종목: 0…
    - 수정주가 미반영 의심 7종목 — 역사적 신고가 판정 제외
    - 5종목은 계단이 있었지만 공시가 없어 실제 등락으로 보고 판정에 포함
    - 장 흐름 섹션 생성 실패 — 검증 실패 1건
    기준일 2026-09-22 · 종가 기준 · 수치는 board 엔진이 계산한 값이고 서술은 …

전부 **우리가 산출·검증하며 쓰는 말**이다. 읽는 사람은 '산출물 정합성 위반' 이
무엇인지, '판정' 에 포함되고 제외되는 것이 무엇인지 알 도리가 없고, 종목코드
`00050…` 나 `board 엔진` 은 고치는 사람에게 할 말이다.

**정보를 지우는 것이 아니다**(CLAUDE.md 2장 6번). 갈라 담는다:

    결손(notes)      — 무슨 데이터가 없나. 배너·리포트에 실린다.
    진단(diagnostics) — 엔진이 자기 산출을 의심하나. universe.json·run_log·로그에
                        남고 `--check` 가 읽는다. 배너에는 안 실린다.

여기서 보는 것은 **금지어가 독자 경로에 흐르지 않는가**다. 문구의 품질은 사람이 본다.
"""
import inspect
import unittest

from ..engine import build as B
from ..engine import facts as F
from ..engine import rankings as R
from ..writer import compose as CP
from .. import run as RUN

# 독자 경로에 나오면 안 되는 말. 전부 2026-09-22 실발송에서 실제로 나온 것이다.
BANNED = (
    '산출물 정합성 위반',     # 엔진 자기검사
    '검증 실패',              # 우리 검증 절차
    '생성 실패',              # 우리 산출 절차
    '금지 문구',              # 우리 검증 절차
    'board 엔진',             # 구현
    '판정 제외',              # 내부 용어
    '판정에 포함',            # 내부 용어
    '확인하세요',             # 고치는 사람에게 하는 지시
    '확인하라',
)


class DiagnosticsDoNotReachTheReader(unittest.TestCase):
    """엔진 자기검사는 진단이다. 결손과 한 목록에 담지 않는다."""

    def test_정합성_문구_자체는_그대로_둔다(self):
        """진단은 고치는 사람이 읽는다 — 거기서는 구체적일수록 좋다."""
        src = inspect.getsource(B.consistency_notes)
        self.assertIn('산출물 정합성 위반', src)


class ReaderStringsAreInTheReportsVoice(unittest.TestCase):
    """결손으로 남기는 줄에는 내부 용어를 쓰지 않는다."""

    def _reader_strings(self, fn):
        """그 함수가 `missing`/`missing_notes` 에 넣는 문자열 리터럴."""
        src = inspect.getsource(fn)
        out = []
        for line in src.split('\n'):
            t = line.strip()
            if t.startswith('#'):
                continue
            if 'missing.append' in t or 'missing_notes.append' in t:
                out.append(t)
            elif out and not t.startswith(('def ', 'if ', 'for ')) and (
                    out[-1].count('(') > out[-1].count(')')):
                out[-1] += ' ' + t
        return out

    def _assert_clean(self, lines, where):
        for line in lines:
            for bad in BANNED:
                self.assertNotIn(bad, line, f'{where} 의 독자 문구에 "{bad}": {line}')

    def test_facts_의_결손_문구(self):
        self._assert_clean(self._reader_strings(F), 'facts')

    def test_compose_의_결손_문구(self):
        self._assert_clean(self._reader_strings(CP._assemble), 'compose._assemble')

    def test_꼬리말에_구현_이야기가_없다(self):
        src = inspect.getsource(CP._assemble)
        tail = src[src.index("L.append('---')"):]
        for bad in ('board 엔진', '옮긴 것이다'):
            # 주석에는 왜 뺐는지 적혀 있으므로 append 되는 줄만 본다.
            appended = [l for l in tail.split('\n')
                        if 'L.append' in l and not l.strip().startswith('#')]
            self.assertNotIn(bad, ' '.join(appended))


class DiagnosticsAreVisibleSomewhere(unittest.TestCase):
    """배너에서 뺀 것이 **숨긴 것**이 되지 않으려면 볼 곳이 있어야 한다.

    처음 갈라 담을 때 이 단계를 안 만들었다. 그동안 진단은 `universe.json` 을
    직접 열지 않으면 아무도 못 보는 상태였고, 그 사실을 모른 채 '--check 가
    읽는다' 고 적어 뒀다. 그 주장이 코드와 어긋나지 않게 여기서 고정한다.
    """

    def test_check_가_진단을_찍는다(self):
        self.assertIn('_check_diagnostics()', inspect.getsource(RUN.cmd_check))

    def test_진단_단계가_universe_를_읽는다(self):
        src = inspect.getsource(RUN._check_diagnostics)
        self.assertIn("'universe.json'", src)
        self.assertIn("get('diagnostics')", src)

    def test_있으면_전부_찍는다(self):
        lines = self._run(dict(as_of='2026-09-22', diagnostics=['가', '나', '다']))
        for x in ('가', '나', '다'):
            self.assertTrue([l for l in lines if x in l], f'{x} 가 안 찍혔다')

    def test_없으면_모순_없음이라고_말한다(self):
        """조용하면 '안 봤다' 와 '봤는데 없다' 가 같아진다."""
        lines = self._run(dict(as_of='2026-09-22', diagnostics=[]))
        self.assertTrue([l for l in lines if '모순 없음' in l])

    def test_산출이_없으면_실패라고_하지_않는다(self):
        lines = self._run(None, asof=None)
        self.assertTrue([l for l in lines if '아직 산출이 없다' in l])

    def test_진단은_종료코드를_바꾸지_않는다(self):
        """소스가 살아 있는지와 엔진이 스스로 모순인지는 다른 질문이다."""
        src = inspect.getsource(RUN._check_diagnostics)
        self.assertNotIn('return 1', src)
        self.assertNotIn('fails', src)

    def _run(self, uni, asof='2026-09-22'):
        lines = []
        orig_read, orig_asof, orig_log = B.read, RUN._asof, RUN.log
        B.read = lambda a, f: uni
        RUN._asof = lambda: asof
        RUN.log = lines.append
        try:
            RUN._check_diagnostics()
        finally:
            B.read, RUN._asof, RUN.log = orig_read, orig_asof, orig_log
        return lines


class NothingIsSwallowed(unittest.TestCase):
    """2장 6번 — 갈라 담되 버리지 않는다."""

    def test_섹션_사유는_meta_로_남는다(self):
        self.assertIn('skipped=skipped', inspect.getsource(CP))

    def test_검수_메모도_meta_로_남는다(self):
        self.assertIn('notes=notes', inspect.getsource(CP))


if __name__ == '__main__':
    unittest.main()

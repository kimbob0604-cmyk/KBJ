"""보드 계산 코드의 모양을 지키는 원본 시험 — 계산이 kbj 로 옮겨 와서 함께 옮겼다.

KBJ P3 묶음 E1 — ET `board/tests/` 의 소스 검사 시험 8개를 승격했다(docs/p3_design.md §1.3·§1.11).
legacy `engine/build.py` 는 이제 sqlite 를 읽어 `kbj.engines.board.build.compute_day` 를 부르는 shim
이라, 계산 본문의 모양을 보는 이 시험들은 legacy 에서는 볼 것이 없다. 바꾼 것(뜻은 같다):

- 보는 대상: legacy `B.run`·`B`·`R` → kbj `compute_day`·`kbj.engines.board.build`·`rankings`
- 문자열 단언의 따옴표: kbj 코드는 ruff format(큰따옴표)이라 `'…'` → `"…"`
- `test_진단은_run_log_에도_남는다`: kbj 엔진은 DB 를 모른다 — 실패 단계를 `steps` 로 돌려주고 읽는
  쪽(legacy `build.run` shim 은 `DB.log_step`, kbj 서비스는 실행 detail)이 기록한다. 그래서
  `DB.log_step` → `steps.append` 를 보고, legacy shim 이 그것을 run_log 에 쓰는지를 함께 본다.
- `test_build_passes_the_cleared_set`: `DB.split_cleared(conn)` 은 읽는 쪽에 있다 — legacy shim 과
  kbj 서비스(`repos.board.split_cleared()`) 둘 다를 본다.
"""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from kbj.engines.board import build as B
from kbj.engines.board import rankings as R
from kbj.services.engine import board as SVC

LEGACY_BUILD = (
    Path(__file__).resolve().parents[4] / "legacy" / "etf_traker" / "board" / "engine" / "build.py"
)


def _legacy_build_source() -> str:
    """legacy shim 의 소스를 글자로만 읽는다(import 하지 않는다 — kbj 는 legacy 를 모른다)."""
    return LEGACY_BUILD.read_text(encoding="utf-8")


class ProvenanceTest(unittest.TestCase):
    """ET test_close_source.ProvenanceTest 에서."""

    def test_잠정이면_배너에_사유를_적는다(self) -> None:
        src = inspect.getsource(B.compute_day)
        i = src.index("close_provenance")
        self.assertIn("missing_notes.append(note)", src[i : i + 1600])


class DiagnosticsDoNotReachTheReader(unittest.TestCase):
    """ET test_reader_voice.DiagnosticsDoNotReachTheReader 에서(엔진 부분 4개)."""

    def test_정합성_검사는_진단으로_간다(self) -> None:
        src = inspect.getsource(B)
        i = src.index("for note in consistency_notes(rows, cfg):")
        block = src[i : i + 400]
        self.assertIn("diag_notes.append", block)
        self.assertNotIn("missing_notes.append", block)

    def test_단위_의심도_진단으로_간다(self) -> None:
        src = inspect.getsource(B)
        i = src.index("unit_note = unit_sanity(rows)")
        block = src[i : i + 300]
        self.assertIn("diag_notes.append", block)
        self.assertNotIn("missing_notes.append", block)

    def test_진단이_페이로드에_따로_실린다(self) -> None:
        """빼는 것이지 지우는 것이 아니다 — 고치는 사람은 볼 수 있어야 한다."""
        self.assertIn("diagnostics=diag_notes", inspect.getsource(B))

    def test_배너는_진단을_읽지_않는다(self) -> None:
        src = inspect.getsource(R)
        self.assertIn('uni.get("notes")', src)
        self.assertNotIn('uni.get("diagnostics")', src)


class NothingIsSwallowed(unittest.TestCase):
    """ET test_reader_voice.NothingIsSwallowed 에서."""

    def test_진단은_run_log_에도_남는다(self) -> None:
        src = inspect.getsource(B)
        i = src.index("for note in consistency_notes(rows, cfg):")
        self.assertIn("steps.append", src[i : i + 400])
        # 읽는 쪽이 그 단계를 run_log 에 쓴다(legacy shim)
        self.assertIn("DB.log_step(conn, asof, step, False, note)", _legacy_build_source())


class TurnoverToggle(unittest.TestCase):
    """ET test_mktcap_floor.TurnoverToggle 에서."""

    def test_설정과_파일이_이어져_있다(self) -> None:
        # build 가 이 값을 newhigh.json 에 실어야 화면까지 닿는다.
        src = inspect.getsource(B.compute_day)
        self.assertIn('min_turnover_eok=float(d.get("min_turnover_eok") or 0)', src)


class ClearedEvaluationTest(unittest.TestCase):
    """ET test_stale_px.ClearedEvaluationTest 에서."""

    def test_build_passes_the_cleared_set(self) -> None:
        src = inspect.getsource(B)
        self.assertIn("split_cleared=code in cleared", src)
        self.assertIn("DB.split_cleared(conn)", _legacy_build_source())
        self.assertIn("repos.board.split_cleared()", inspect.getsource(SVC))

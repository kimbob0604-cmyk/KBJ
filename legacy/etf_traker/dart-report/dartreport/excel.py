"""엑셀 리포트 빌더 — KBJ P4: KBJ 정본을 다시 내보낸다(두 벌 금지).

정본은 `kbj.reports.dart_excel.excel`(셀·서식·수식·차트 한 줄도 바꾸지 않고 옮겼다 — docs/p4_design.md
D-P4-6 ④·D-P4-7). 바뀐 것: `build_workbook(..., generated_at=None)` — 요약 '생성 시각'을 주입받는다
(안 주면 지금 시각, KST 로 표시). 이식 전과 같은지는 tests/golden/dart_report(정규화 비교 — 차트 XML
포함)가 본다.
"""

from kbj.reports.dart_excel.excel import (
    BLACK,
    BLUE,
    BOX,
    FONT,
    GREEN,
    HDR_FILL,
    NUM,
    NUM1,
    OP_FILL,
    PCT,
    PCT1,
    THIN,
    TOT_FILL,
    WARN_FILL,
    build_workbook,
)

__all__ = [
    "BLACK",
    "BLUE",
    "BOX",
    "FONT",
    "GREEN",
    "HDR_FILL",
    "NUM",
    "NUM1",
    "OP_FILL",
    "PCT",
    "PCT1",
    "THIN",
    "TOT_FILL",
    "WARN_FILL",
    "build_workbook",
]

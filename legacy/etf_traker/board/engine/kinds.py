"""KBJ P3 묶음 E1 — 정본은 `kbj.engines.board.kinds`. 여기는 다시 내보내기만 한다.

docs/p3_design.md §1.3·D-P3-10: 골든(tests/golden/board — 이 모듈의 shim 전 산출)을 먼저 캡처한 뒤
계산을 kbj 로 옮겼다. 같은 계산을 두 벌 두지 않는다(CLAUDE.md 3장). legacy 의 다른 모듈·시험은 예전
이름 그대로 이 모듈을 import 한다.
"""
from kbj.engines.board.kinds import *  # noqa: F401,F403
from kbj.engines.board.kinds import __all__  # noqa: F401

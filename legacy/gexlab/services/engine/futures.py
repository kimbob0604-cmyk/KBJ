"""KBJ P7(묶음 ENG): 정본은 `kbj.engines.gex.futures`(docs/p7_design.md §1.3) — 이름을 다시 내보낸다.

옮긴 원본 시험은 KBJ `tests/unit/engines/gex/` 에서 돈다(같은 단언, import 경로만).
"""

from kbj.engines.gex.futures import *  # noqa: F403
from kbj.services.engine.gex import quote_of  # noqa: E402, F401 — 원문 검증은 서비스로 옮겼다

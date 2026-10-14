"""KBJ P7(묶음 ENG): 정본은 `kbj.services.engine.gex`(docs/p7_design.md §1.3) — 이름을 다시 내보낸다.

GX compose 의 `python -m services.engine` 은 KBJ 서비스를 띄운다(`__main__`). 옮긴 원본 시험은 KBJ
`tests/unit/engines/gex/` 에서 돈다(같은 단언, import 경로만).
"""

from kbj.services.engine.gex import *  # noqa: F403
from kbj.services.engine.gex import SERVICE, main  # noqa: F401 — 명시(진입점·compose 시험)

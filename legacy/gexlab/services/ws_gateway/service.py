"""KBJ P7 ING: 정본은 `kbj.services.ws_gateway.service`(이 파일에서 `git mv` 로 승격, docs/p7_design.md §1.4).

legacy GX 가 이름을 그대로 쓰도록 공개 이름 전부를 다시 내보낸다(두 벌 금지 — CLAUDE.md §3).
옮긴 시험은 kbj `tests/unit/{ws_gateway,recorder,kis}/`·`tests/golden/gex/`(MIGRATION.md 'P7').
"""

from kbj.services.ws_gateway.service import *  # noqa: F403

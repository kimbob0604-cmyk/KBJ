"""KBJ P5 묶음 US — 정본은 `kbj.engines.us.sectors`. 여기는 다시 내보내기만 한다.

docs/p5_design.md §1.6: 규칙 사전은 레포 루트 `config/knowledge/us/`(us_sectors·us_overrides —
git mv)로 옮겼다. 같은 분류를 두 벌 두지 않는다.
"""
from kbj.engines.us.sectors import (  # noqa: F401
    UNMAPPED,
    apply,
    by_source,
    classify,
    overrides,
    rules,
    unmapped_industries,
)

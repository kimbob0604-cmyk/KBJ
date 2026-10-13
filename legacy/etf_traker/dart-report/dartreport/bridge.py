"""영업이익 → 당기순이익 워터폴 브릿지 — KBJ P4: KBJ 정본을 다시 내보낸다(두 벌 금지).

정본은 `kbj.engines.financials.bridge`(함수 본문 그대로 — docs/p4_design.md D-P4-6 ④). 잔차 플러그
(기타 영업외손익)는 그대로다. legacy 동작(부호 키워드 '손' 이 '…평가이익' 에도 걸림 등)도 그대로 —
고칠 때는 새 골든 세트 + ADR(MIGRATION.md 'legacy 버그 목록'). `run.py` 진단이 쓰는
`_bucketize_bridge` 는 kbj 공개 별칭을 옛 이름으로 다시 붙인다(같은 함수 객체).
"""

from kbj.engines.financials.bridge import (
    BRIDGE_ORDER,
    build_bridge,
    fetch_bridge_details,
    waterfall_layout,
)
from kbj.engines.financials.bridge import bucketize_bridge as _bucketize_bridge
from kbj.engines.financials.statements import EOK, Period

from .client import DartClient

__all__ = [
    "BRIDGE_ORDER",
    "EOK",
    "DartClient",
    "Period",
    "_bucketize_bridge",
    "build_bridge",
    "fetch_bridge_details",
    "waterfall_layout",
]

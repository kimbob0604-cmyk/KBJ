"""poller 산출 레코드 — KBJ P7(FND-B): 정본은 `kbj.core.gex.records`(docs/p7_design.md §1.1).

legacy GX 는 이름을 그대로 쓰도록 다시 내보낸다(두 벌 금지 — CLAUDE.md §3). 필드·검증은 그대로다.
"""

from kbj.core.gex.records import (
    CallPut,
    ChainRecord,
    ChainSource,
    ExpiryRecord,
    FuturesRecord,
    HealthEvent,
    HealthLevel,
    InvestorRecord,
    QuarantineRecord,
    Quality,
    SessionName,
)

__all__ = [
    "CallPut",
    "ChainRecord",
    "ChainSource",
    "ExpiryRecord",
    "FuturesRecord",
    "HealthEvent",
    "HealthLevel",
    "InvestorRecord",
    "QuarantineRecord",
    "Quality",
    "SessionName",
]

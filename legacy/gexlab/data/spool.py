"""DB 쓰기 실패 묶음의 로컬 디스크 큐 — KBJ P2: 정본은 `kbj.store.spool`
(이 파일에서 승격, 설계 §1.8).

코드는 GX 그대로 승격했다(두 벌 금지 — CLAUDE.md §3). legacy GX `PostgresSink`(data/store.py)는 같은
이름을 여기서 받는다. 승격한 시험: `tests/unit/test_spool.py` 38개 중 DiskSpool·인코딩 18개 → kbj
`tests/unit/store/test_spool.py`(나머지 20개는 GX 레코드·PostgresSink 를 써 P7 까지 여기 남는다).
"""

from kbj.store.spool import (
    CORRUPT,
    DEAD,
    DEFAULT_MAX_BYTES,
    DEFAULT_SEGMENT_BYTES,
    VERSION,
    DiskSpool,
    DropNotice,
    RejectedRows,
    ReplayResult,
    SpooledBatch,
    SpoolError,
    SpoolStatus,
    TransientWrite,
    decode_batch,
    decode_value,
    encode_batch,
    encode_value,
)

__all__ = [
    "CORRUPT",
    "DEAD",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_SEGMENT_BYTES",
    "VERSION",
    "DiskSpool",
    "DropNotice",
    "RejectedRows",
    "ReplayResult",
    "SpoolError",
    "SpoolStatus",
    "SpooledBatch",
    "TransientWrite",
    "decode_batch",
    "decode_value",
    "encode_batch",
    "encode_value",
]

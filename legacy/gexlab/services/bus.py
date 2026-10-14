"""서비스 사이 Redis 계약 — KBJ P7(FND-B): 정본은 `kbj.services.runtime.bus`(docs/p7_design.md §1.1).

legacy GX 는 이름을 그대로 쓰도록 다시 내보낸다(두 벌 금지 — CLAUDE.md §3). 채널·키 이름과 메시지
모양은 그대로다(하트비트 모델·`session:*`·`health:heartbeat:`·`krx:calls:` 는 KBJ 정본과 같은 것).
"""

from kbj.services.runtime.bus import *  # noqa: F403
from kbj.services.runtime.bus import __all__ as __all__  # noqa: PLC0414

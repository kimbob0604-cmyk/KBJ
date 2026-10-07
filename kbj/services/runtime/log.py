"""구조화 로그 — JSON 한 줄(GEXLAB `services/runtime.py:setup_logging`·`log_event` 승격).

필드: service·event·trade_date·session + 호출자가 준 필드. 토큰·접속 문자열·키는 싣지 않는다 —
호출자가 넣지 않고, 넣더라도 문자열 필드는 kbj.core.masking.mask_text 를 거친다(절대 규칙 5).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date

from kbj.core.masking import mask_text

Tag = tuple[date | None, str | None]  # (거래일, 세션) — 캘린더 태거(kbj.core.calendar)가 만든다


def setup_logging(level: int = logging.INFO) -> None:
    """서비스 진입점에서 한 번. 메시지는 이미 JSON 이라 서식은 메시지만."""
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stderr)
    for noisy in ("httpx", "httpcore", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _clean(value: object) -> object:
    return mask_text(value) if isinstance(value, str) else value


def log_event(
    logger: logging.Logger,
    level: int,
    service: str,
    event: str,
    tag: Tag | None = None,
    **fields: object,
) -> None:
    """구조화 JSON 한 줄(서비스명·거래일·세션 포함)."""
    td, ss = tag if tag is not None else (None, None)
    rec: dict[str, object] = {
        "service": service,
        "event": event,
        "trade_date": td.isoformat() if td is not None else None,
        "session": ss,
        **{k: _clean(v) for k, v in fields.items()},
    }
    logger.log(level, json.dumps(rec, ensure_ascii=False, default=str))

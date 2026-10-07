"""KIS 토큰 오류 — GEXLAB `data/kis/auth_client.py`(`TokenError`:57, `TokenUnavailable`:61,
`TokenIssueError`:65, `TokenIssueThrottled`:69) 승격.

자격(`credentials`)·읽기(`token`)·발급(`kbj/services/auth/issuer.py`) 세 곳이 같은 오류를 쓰므로
따로 둔다(어댑터 → 서비스 import 는 막혀 있다 — 발급 오류도 여기서 정의하고 발급자가 다시 내보낸다).
메시지에는 토큰·앱키가 들어가지 않는다(절대 규칙 5).
"""

from __future__ import annotations

from datetime import datetime

from kbj.core.time import KST


class TokenError(RuntimeError):
    """토큰 관련 오류. 메시지에 토큰·앱키가 들어가지 않는다."""


class TokenUnavailable(TokenError):
    """쓸 수 있는 토큰이 캐시에 없고 발급자도 없다(읽기 전용 제공자), 또는 KIS 자격이 없다."""


class TokenIssueError(TokenError):
    """발급 요청이 실패했다(발급은 `kbj.services.auth` 만 한다)."""


class TokenIssueThrottled(TokenIssueError):
    """발급 간격(61초) 안이라 발급하지 않았다 — 또는 KIS 가 `EGW00133`(1분 1회)으로 돌려보냈다."""

    def __init__(self, retry_at: datetime | None) -> None:
        when = retry_at.astimezone(KST).strftime("%H:%M:%S KST") if retry_at else "잠시 뒤"
        super().__init__(f"토큰 발급은 61초에 한 번 — {when} 이후 다시 시도")
        self.retry_at = retry_at

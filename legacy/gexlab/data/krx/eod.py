"""KRX Open API 일별매매정보 — KBJ P2: 정본은 `kbj.data.private.krx.client`(설계 §1.5·§3.8).

KRX 주소는 KBJ 어댑터에만 있다(check_canonical `krx_api`). legacy GX 는 이름을 그대로 쓰고,
`KrxClient.from_settings` 만 GX 설정(`config.settings.Settings` — `krx_api_key`)을 받는다. 일 호출
상한은 GX 쪽이 따로 센다(`services/scheduler/krx.py:KrxCallBudget` — 같은 Redis 키
`krx:calls:<날짜>`).
`fetch_daily`(probe 진단용)는 같은 클라이언트로 부른다 — 직접 HTTP 호출은 없다.

승격한 시험: `tests/unit/test_krx_client.py` → kbj `tests/unit/data/private/`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from kbj.data.private.krx.client import (
    ERROR_MAX,
    FUT_DAILY,
    KRX_CONNECT_S,
    KRX_MAX_BYTES,
    KRX_READ_S,
    KRX_TOTAL_S,
    OPT_DAILY,
    KrxDailyResponse,
)
from kbj.data.private.krx.client import KrxClient as _KbjKrxClient
from kbj.data.private.krx.client import KrxError as _KbjKrxError

from config.settings import Settings

__all__ = [
    "ERROR_MAX",
    "FUT_DAILY",
    "KRX_CONNECT_S",
    "KRX_MAX_BYTES",
    "KRX_READ_S",
    "KRX_TOTAL_S",
    "OPT_DAILY",
    "KrxClient",
    "KrxDailyResponse",
    "KrxError",
    "fetch_daily",
]


class KrxError(_KbjKrxError):
    """KBJ `KrxError` 그대로 — 문구만 GX 모양(출처 접두어 `KRX:` 없이 가린 사유)으로 보인다."""

    def __str__(self) -> str:
        return self.reason


class KrxClient(_KbjKrxClient):
    """KBJ KrxClient — `from_settings` 는 GX 설정을 받고(리미터·예산 없이, GX 와 같은 동작),
    실패는 GX 모양 문구의 `KrxError` 로 올린다(HTTP 상태·엔드포인트·재시도 여부는 그대로)."""

    def daily(self, endpoint: str, bas_dd: date) -> list[dict[str, Any]]:
        try:
            return super().daily(endpoint, bas_dd)
        except KrxError:
            raise
        except _KbjKrxError as e:
            raise KrxError(
                e.reason, status=e.status, endpoint=e.endpoint, retryable=e.retryable
            ) from None

    @classmethod
    def from_settings(cls, settings: Settings, **kw: Any) -> KrxClient:  # type: ignore[override]
        if settings.krx_api_key is None:
            raise KrxError("KRX_API_KEY 가 없다")
        return cls(settings.krx_api_key, **kw)


def fetch_daily(
    settings: Settings, endpoint: str, bas_dd: str, timeout: float = 30.0
) -> tuple[int, Any]:
    """probe 용 — (HTTP 상태, 본문). KBJ 클라이언트로 부르고 실패는 상태·가린 문구로 돌려준다."""
    client = KrxClient.from_settings(settings, read_s=timeout)
    day = date(int(bas_dd[:4]), int(bas_dd[4:6]), int(bas_dd[6:8]))
    try:
        rows = client.daily(endpoint, day)
    except KrxError as e:
        return (e.status or 0), {"_text": str(e)[:500]}
    return 200, {"OutBlock_1": rows}

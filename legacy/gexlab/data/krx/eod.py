"""KRX Open API 파생상품 일별매매정보 클라이언트 (PLAN §3 #15·#16, docs/phase1_design.md §2·§8).

- 인증키는 `AUTH_KEY` 헤더로 보낸다. SecretStr 에서 부를 때만 꺼내고, 로그·오류 문구·repr 에
  싣지 않는다 — 오류 문구는 첫 줄만, 키를 가리고 자른다. 원래 예외는 매달지 않는다(`from None`)
- 응답 `{"OutBlock_1": [...]}` 은 pydantic 으로 검증한다(행 하나하나는 data/krx/models.py 몫).
  `OutBlock_1` 이 없으면 오류다 — 빈 날(휴장·아직 갱신 전)은 `{"OutBlock_1": []}` (HTTP 200,
  2026-09-28 probe 09-24·09-25)이라 인증 오류 같은 다른 본문을 빈 날로 읽지 않는다
- 접속 5초·읽기 30초, 전체는 조각 사이에서 `total_s`(120초)로 끊는다. 옵션 하루 약 16,700행(수 MB)
  이고 응답 시간은 미실측이라 보수적으로 잡았다 **[확인 필요]**
- KRX 는 다음 영업일 08:00 갱신·10,000회/일(#16). 호출 수 상한은 부르는 쪽(scheduler)이 센다
- `fetch_daily` 는 Phase 0 probe 용(상태 코드·본문을 그대로 돌려준다)
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from config.settings import KRX_BASE, Settings

# 파생상품 일별매매정보 (krx-openapi 카탈로그)
FUT_DAILY = "/drv/fut_bydd_trd"
OPT_DAILY = "/drv/opt_bydd_trd"

KRX_CONNECT_S = 5.0  # 확인 필요: KRX 응답 시간·크기 미실측 — 보수적으로
KRX_READ_S = 30.0
KRX_TOTAL_S = 120.0
KRX_MAX_BYTES = 64 << 20
ERROR_MAX = 200


def fetch_daily(
    settings: Settings, endpoint: str, bas_dd: str, timeout: float = 30.0
) -> tuple[int, Any]:
    if settings.krx_api_key is None:
        raise RuntimeError("KRX_API_KEY 가 없다")
    headers = {"AUTH_KEY": settings.krx_api_key.get_secret_value()}
    r = httpx.get(KRX_BASE + endpoint, params={"basDd": bas_dd}, headers=headers, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"_text": r.text[:500]}


class KrxError(RuntimeError):
    """KRX 호출 실패. 문구엔 인증키가 없다(가렸다). status = HTTP 상태(응답을 받았을 때)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class KrxDailyResponse(BaseModel):
    """일별매매정보 응답 봉투. 행은 dict 그대로 — 모델 검증은 행 단위로 따로(한 행이 전체를 막지
    않게, data/krx/models.py `parse_rows`)."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    rows: list[dict[str, Any]] = Field(validation_alias="OutBlock_1")


class KrxClient:
    """KRX Open API 일별매매정보. 호출마다 연결을 새로 연다(하루 몇 번뿐). 시험은 transport."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = KRX_BASE,
        connect_s: float = KRX_CONNECT_S,
        read_s: float = KRX_READ_S,
        total_s: float = KRX_TOTAL_S,
        max_bytes: int = KRX_MAX_BYTES,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise ValueError("KRX 인증키가 비었다")
        if min(connect_s, read_s, total_s) <= 0 or max_bytes <= 0:
            raise ValueError("시간 제한·크기 상한은 0 보다 커야 한다")
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = httpx.Timeout(read_s, connect=connect_s)
        self._total = total_s
        self._max = max_bytes
        self._transport = transport
        self._clock = clock

    @classmethod
    def from_settings(cls, settings: Settings, **kw: Any) -> KrxClient:
        if settings.krx_api_key is None:
            raise KrxError("KRX_API_KEY 가 없다")
        return cls(settings.krx_api_key, **kw)

    def __repr__(self) -> str:  # 인증키를 보이지 않는다
        return f"KrxClient(base_url={self._base!r})"

    def daily(self, endpoint: str, bas_dd: date) -> list[dict[str, Any]]:
        """`bas_dd` 하루의 행(dict). 빈 날은 []. 실패는 KrxError(키를 가린 문구)."""
        what = f"{endpoint} {bas_dd:%Y%m%d}"
        try:
            status, body = self._get(endpoint, bas_dd)
        except KrxError:
            raise
        except httpx.HTTPError as e:
            raise KrxError(self._redact(f"{what}: {type(e).__name__}: {e}")) from None
        if status != 200:
            text = body.decode("utf-8", "replace")
            raise KrxError(self._redact(f"{what}: HTTP {status} {text}"), status=status)
        try:
            doc = json.loads(body)
        except ValueError:
            raise KrxError(f"{what}: 응답이 JSON 이 아니다", status=status) from None
        if not isinstance(doc, dict):
            raise KrxError(f"{what}: 응답이 객체가 아니다", status=status)
        try:
            resp = KrxDailyResponse.model_validate(doc)
        except ValidationError as e:
            keys = ",".join(sorted(str(k) for k in doc)[:5])
            msg = f"{what}: 응답 형식 오류 {e.error_count()}건 (키 {keys})"
            raise KrxError(self._redact(msg), status=status) from None
        return resp.rows

    def _get(self, endpoint: str, bas_dd: date) -> tuple[int, bytes]:
        """상태 코드와 본문 바이트. 접속·읽기 한 번마다 시간 제한, 전체는 total_s 로 끊는다."""
        deadline = self._clock() + self._total
        buf = bytearray()
        headers = {"AUTH_KEY": self._key.get_secret_value()}
        with (
            httpx.Client(timeout=self._timeout, transport=self._transport) as client,
            client.stream(
                "GET", self._base + endpoint, params={"basDd": f"{bas_dd:%Y%m%d}"}, headers=headers
            ) as r,
        ):
            status = r.status_code
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > self._max:
                    raise KrxError(f"{endpoint}: 응답이 {self._max}B 보다 크다", status=status)
                if self._clock() > deadline:
                    raise KrxError(f"{endpoint}: 응답 {self._total:g}초 초과", status=status)
            return status, bytes(buf)

    def _redact(self, text: str) -> str:
        """첫 줄만, 인증키를 가리고 ERROR_MAX 자로."""
        lines = text.strip().splitlines()
        msg = lines[0] if lines else ""
        key = self._key.get_secret_value()
        if key:
            msg = msg.replace(key, "***")
        return msg if len(msg) <= ERROR_MAX else msg[: ERROR_MAX - 1] + "…"

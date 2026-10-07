"""KIS REST 클라이언트 — 읽기 전용 토큰 + 앱키당 레이트리미터(설계 §1.2·§3.5·§4.2).

GEXLAB `data/kis/rest.py`(`KisResponse`:69, `KisClient`:98, `minute_chart_params`, `redact`) +
`services/scheduler/minute.py:reader_kis_client`:323 승격.

- **토큰 제공자는 필수**다(K7: GX `KisClient._provider` 의 기본 발급 경로를 없앴다). 서비스는
  `KisRestClient.for_service(settings, redis)` — auth 가 Redis 에 둔 토큰을 읽기만 하는 `reader` 와
  앱키당 Redis 리미터(`rl:kis:<해시>` — legacy GX poller·ws-gateway 와 같은 버킷)를 끼운다.
- 앱키·시크릿은 요청마다 헤더에 싣는다(KIS 가 모든 요청에 요구 — ADR 0004 안 A). 이 클라이언트에는
  발급 요청이 없다.
- `rate_limiter` 를 주면 호출마다 허가를 받고, 한도초과(`EGW00201`)면 감속을 알리고 다음 허가에서
  1회 재시도한다. 없으면 호출자가 간격을 둔다(실측 probe).
- KIS 가 토큰을 거절하면(`EGW00121`·`EGW00123`) 제공자에 알리고(`invalidate` — 읽기 전용은 거절
  신고만) 1회 다시 보낸다. 새 토큰이 아직 없으면 그 재시도는 `TokenUnavailable` 로 끝난다(발급하지
  않는다).
- 응답은 가공하지 않고 status·본문·소요시간·`tr_cont` 를 그대로 돌려준다. 값 표준화(source·as_of·
  quality)는 수집 작업이 한다.
- 토큰·앱키는 어떤 경로로도 출력하지 않는다(`redact`, repr 에 값 없음).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

import httpx
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.masking import redact as _redact_values
from kbj.data.http import make_client
from kbj.data.private.kis.credentials import KisCredentials
from kbj.data.private.kis.token import TokenProvider, reader, utcnow
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RateLimitTimeout, RedisRateLimiter

__all__ = [
    "MINUTE_PATH",
    "MINUTE_TR",
    "RATE_LIMIT_CODE",
    "TOKEN_REJECTED_CODES",
    "KisResponse",
    "KisRestClient",
    "minute_chart_params",
    "redact",
]

RATE_LIMIT_CODE: Final = "EGW00201"  # 초당 거래건수를 초과하였습니다
# 유효하지 않은 token / 기간이 만료된 token — 실측 전(확인 필요), KIS 공식 샘플 기준
TOKEN_REJECTED_CODES: Final = frozenset({"EGW00121", "EGW00123"})
CONNECT_S: Final = 5.0

# 선물옵션 분봉 — legacy GX `scripts/probe_common.py` P_MINUTE·TR_MINUTE 와 같다(같은지는 시험이
# 본다)
MINUTE_PATH: Final = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
MINUTE_TR: Final = "FHKIF03020200"
_HHMMSS = re.compile(r"\d{6}")


def minute_chart_params(market: str, code: str, day: date, hour: str) -> dict[str, str]:
    """분봉 한 번의 조회 파라미터 — (day, hour) 부터 과거로 최대 102봉(GX #17).

    GX `scripts/probe_minute_history.fetch_resp` 가 실측(2026-09-28)에 쓴 값 그대로다: 1분봉(`60`),
    과거 데이터 포함, 허봉 제외. market 은 주간 `F`·야간 `CM`. hour 는 HHMMSS 6자리 — 어떤 표기를
    넘길지(야간 이어 가기 등)는 부르는 쪽이 정한다.
    """
    if not _HHMMSS.fullmatch(hour):
        raise ValueError(f"시각은 HHMMSS 6자리: {hour!r}")
    return {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code,
        "FID_HOUR_CLS_CODE": "60",
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N",
        "FID_INPUT_DATE_1": day.strftime("%Y%m%d"),
        "FID_INPUT_HOUR_1": hour,
    }


@dataclass
class KisResponse:
    status: int
    body: dict[str, Any]
    elapsed_ms: float
    tr_cont: str = ""

    @property
    def rt_cd(self) -> str:
        return str(self.body.get("rt_cd", ""))

    @property
    def msg_cd(self) -> str:
        return str(self.body.get("msg_cd", ""))

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.rt_cd == "0"

    @property
    def rate_limited(self) -> bool:
        return self.msg_cd == RATE_LIMIT_CODE

    @property
    def token_rejected(self) -> bool:
        return self.msg_cd in TOKEN_REJECTED_CODES


class KisRestClient:
    """KIS REST GET. 토큰은 `token_provider`(읽기 전용 `reader`)에서, 허가는 `rate_limiter` 에서."""

    def __init__(
        self,
        creds: KisCredentials,
        token_provider: TokenProvider,
        rate_limiter: RateLimiter | None,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if token_provider is None:  # pyright: ignore[reportUnnecessaryComparison]
            raise TypeError("token_provider 가 필요하다 — 토큰 발급은 auth 서비스만 한다(ADR 0004)")
        self.creds = creds
        self.token_provider = token_provider
        self.rate_limiter = rate_limiter
        self.timeout = timeout
        self._transport = transport
        self._token: str | None = None  # 마지막으로 쓴 토큰 (redact 용)
        self._http: httpx.Client | None = None

    @classmethod
    def for_service(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        clock: Clock | None = None,
        now: Callable[[], datetime] = utcnow,
        transport: httpx.BaseTransport | None = None,
    ) -> KisRestClient:
        """서비스용 — 앱키당 Redis 리미터(config/limits.yaml `kis`) + auth 토큰 읽기만.

        토큰이 없으면 그 조회가 `TokenUnavailable` 로 실패하고 다음 실행에 다시 읽는다. 자격이
        없으면 `TokenUnavailable`. clock 은 리미터 시계(기본 호스트 시계 — 모든 프로세스가 같은
        호스트 시계), now 는 토큰 만료 판정 시계, transport 는 시험용 가짜 KIS.
        """
        creds = KisCredentials.from_settings(settings)
        limiter = RedisRateLimiter.scoped(
            redis, "kis", creds.app_key.get_secret_value(), clock=clock
        )
        provider = reader(redis, creds, now=now, by=settings.service or "unknown")
        return cls(creds, provider, limiter, transport=transport)

    def __repr__(self) -> str:
        return f"KisRestClient(env={self.creds.env!r}, limiter={self.rate_limiter is not None})"

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = make_client(
                self.creds.base_url,
                connect_s=min(CONNECT_S, self.timeout),
                read_s=self.timeout,
                transport=self._transport,
            )
        return self._http

    def token(self) -> str:
        """접근토큰 — 캐시(Redis)에서 읽기만 한다. 없으면 `TokenUnavailable`."""
        self._token = self.token_provider.get()
        return self._token

    def get(
        self,
        path: str,
        tr_id: str,
        params: dict[str, str],
        tr_cont: str = "",
        *,
        priority: Priority = Priority.P2,
        timeout: float | None = None,
    ) -> KisResponse:
        """GET 한 건. 리미터가 있으면 priority·timeout 으로 허가를 받는다(`RateLimitTimeout`)."""
        resp = self._send(path, tr_id, params, tr_cont, priority, timeout)
        if resp.token_rejected:
            self.token_provider.invalidate()
            resp = self._send(path, tr_id, params, tr_cont, priority, timeout)
        if resp.rate_limited and self.rate_limiter is not None:
            self.rate_limiter.on_rate_limited()
            try:
                resp = self._send(path, tr_id, params, tr_cont, priority, timeout)
            except RateLimitTimeout:
                pass  # 재시도 허가를 못 받으면 처음 응답(한도초과)을 돌려준다
        return resp

    def _send(
        self,
        path: str,
        tr_id: str,
        params: dict[str, str],
        tr_cont: str,
        priority: Priority,
        timeout: float | None,
    ) -> KisResponse:
        headers = {
            "authorization": f"Bearer {self.token()}",
            "appkey": self.creds.app_key.get_secret_value(),
            "appsecret": self.creds.app_secret.get_secret_value(),
            "tr_id": tr_id,
            "custtype": "P",
            "content-type": "application/json; charset=utf-8",
        }
        if tr_cont:
            headers["tr_cont"] = tr_cont
        if self.rate_limiter is not None:
            self.rate_limiter.acquire(priority, tr_id, timeout)
        t0 = time.perf_counter()
        r = self._client().get(path, params=params, headers=headers)
        elapsed = (time.perf_counter() - t0) * 1000
        try:
            body: dict[str, Any] = r.json()
        except ValueError:
            # 가린 뒤 자른다(앞 2000자만 보고 가린다 — 500자 선에 걸친 비밀값 조각이 남지 않게)
            body = {"_text": _redact_values(r.text[:2000], self.secrets())[:500]}
        return KisResponse(r.status_code, body, elapsed, r.headers.get("tr_cont", ""))

    def secrets(self) -> list[str]:
        """가릴 값 — 앱키·시크릿·마지막으로 쓴 토큰."""
        vals = self.creds.secrets()
        if self._token:
            vals.append(self._token)
        return vals

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None


def redact(text: str, client: KisRestClient) -> str:
    """client 의 앱키·시크릿·토큰을 `***` 로(긴 값부터)."""
    return _redact_values(text, client.secrets())

"""KIS REST 클라이언트.

- 토큰은 `TokenProvider` 에서 받는다. 기본값은 캐시 우선 제공자(REDIS_URL 이 있으면 Redis, 없거나
  연결이 안 되면 `state/kis.token.json`) — probe 가 실행마다 새로 발급하지 않는다(`auth_client`)
- `rate_limiter` 를 주면 호출마다 허가를 받고, 한도초과(`EGW00201`)면 감속을 알리고 다음 허가에서
  1회 재시도한다(설계 §3). 없으면 호출자가 간격을 둔다(probe 는 `Ctx.call`)
- KIS 가 토큰을 거절하면(`EGW00121`·`EGW00123`) 제공자에 알리고 새 토큰으로 1회 재시도한다
- 토큰·앱키는 어떤 경로로도 출력하지 않는다 (`redact`)
- 응답은 가공하지 않고 status·헤더·본문·소요시간을 그대로 돌려준다
- 요청 도우미: 선물옵션 분봉(`minute_chart_params`) — probe 가 실측에 쓴 파라미터 그대로(#17)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from config.settings import Settings
from data.kis.auth_client import TOKEN_PATH, TokenProvider, default_token_provider
from data.kis.ratelimit import Priority, RateLimiter, RateLimitTimeout

__all__ = [
    "MINUTE_PATH",
    "MINUTE_TR",
    "RATE_LIMIT_CODE",
    "TOKEN_PATH",
    "KisClient",
    "KisResponse",
    "minute_chart_params",
    "redact",
]

RATE_LIMIT_CODE = "EGW00201"  # 초당 거래건수를 초과하였습니다
# 유효하지 않은 token / 기간이 만료된 token — 실측 전(확인 필요), KIS 공식 샘플 기준
TOKEN_REJECTED_CODES = frozenset({"EGW00121", "EGW00123"})

# 선물옵션 분봉 — scripts/probe_common.py P_MINUTE·TR_MINUTE 와 같다(그쪽이 이 모듈을 가져다 써서
# 여기서 가져오면 순환이다 — 같은지는 시험이 본다)
MINUTE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
MINUTE_TR = "FHKIF03020200"
_HHMMSS = re.compile(r"\d{6}")


def minute_chart_params(market: str, code: str, day: date, hour: str) -> dict[str, str]:
    """분봉 한 번의 조회 파라미터 — (day, hour) 부터 과거로 최대 102봉(#17).

    `scripts/probe_minute_history.fetch_resp` 가 실측(2026-09-28)에 쓴 값 그대로다: 1분봉(`60`),
    과거 데이터 포함, 허봉 제외. market 은 주간 `F`·야간 `CM`. hour 는 HHMMSS 6자리 — 어떤 표기를
    넘길지(야간 이어 가기 등)는 부르는 쪽이 정한다(services/scheduler/minute.py).
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


@dataclass
class KisClient:
    settings: Settings
    timeout: float = 20.0
    token_provider: TokenProvider | None = None
    rate_limiter: RateLimiter | None = None
    transport: httpx.BaseTransport | None = field(default=None, repr=False)  # 테스트용
    _token: str | None = field(default=None, repr=False)  # 마지막으로 쓴 토큰 (redact 용)
    _http: httpx.Client | None = field(default=None, repr=False)

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                base_url=self.settings.kis_base, timeout=self.timeout, transport=self.transport
            )
        return self._http

    def _creds(self) -> tuple[str, str]:
        key, sec = self.settings.kis_app_key, self.settings.kis_app_secret
        if key is None or sec is None:
            raise RuntimeError("KIS_APP_KEY / KIS_APP_SECRET 가 없다")
        return key.get_secret_value(), sec.get_secret_value()

    def _provider(self) -> TokenProvider:
        if self.token_provider is None:
            self.token_provider = default_token_provider(self.settings, self._client())
        return self.token_provider

    def token(self) -> str:
        """접근토큰. 캐시에 살아 있으면 그것을, 없을 때만 발급한다 (발급 61초 1회)."""
        self._token = self._provider().get()
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
            self._provider().invalidate()
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
        key, sec = self._creds()
        headers = {
            "authorization": f"Bearer {self.token()}",
            "appkey": key,
            "appsecret": sec,
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
            body = {"_text": r.text[:500]}
        return KisResponse(r.status_code, body, elapsed, r.headers.get("tr_cont", ""))

    def secrets(self) -> list[str]:
        vals = [
            v.get_secret_value()
            for v in (self.settings.kis_app_key, self.settings.kis_app_secret)
            if v
        ]
        if self._token:
            vals.append(self._token)
        return vals

    def close(self) -> None:
        if self._http is not None:
            self._http.close()


def redact(text: str, client: KisClient) -> str:
    for s in client.secrets():
        if s:
            text = text.replace(s, "***")
    return text

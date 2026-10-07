"""KRX OpenAPI 일별 정보 클라이언트 — 로그인 등급(DATA_TIERS §1, 원천 데이터 재배포 금지).

승격 원본: GEXLAB `data/krx/eod.py`(`FUT_DAILY`:28, `OPT_DAILY`:29, `KrxError`:51,
`KrxDailyResponse`:59, `KrxClient`:68 — `from_settings`:96, `daily`:104, `_get`:130, `_redact`:150)
+ stock-dashboard `krx_api.py` 엔드포인트 목록(:9~19). 설계 `docs/p2_design.md` §1.5·§4.2.

- 인증키는 `AUTH_KEY` 헤더로 보낸다. `SecretStr` 에서 부를 때만 꺼내고 로그·오류 문구·repr 에
  싣지 않는다 — 오류 문구는 첫 줄만, 키를 가리고 자른다. 원래 예외는 매달지 않는다(`from None`).
- 응답 `{"OutBlock_1": [...]}` 의 봉투만 여기서 검증한다(행은 `models`·`stocks` 가 행 단위로).
  `OutBlock_1` 이 없으면 오류다 — 빈 날(휴장·아직 갱신 전)은 `{"OutBlock_1": []}`(HTTP 200, GX
  probe 2026-09-28)이라 인증 오류 같은 다른 본문을 빈 날로 읽지 않는다.
- 접속 5초·읽기 30초, 전체는 조각 사이에서 `total_s`(120초)로, 크기는 64MiB 로 끊는다
  (`kbj.data.http.get_capped`). 옵션 하루 약 16,700행(수 MB) — 응답 시간 미실측 **[확인 필요]**.
- **한도**(config/limits.yaml `krx`): 초당 리미터 `rl:krx:<키 해시>`(2/s [추정]) + 일 예산
  `krx:calls:<YYYYMMDD>`(8,000/일 [확인 필요 — 메인 결정 D5], GX 와 같은 키라 전환 기간 legacy GX
  와 함께 센다). 부르기 **전에** 리미터 허가와 예산 한 몫을 받는다 — 실패한 호출도 센다. 백필은
  그 안에서 따로 `budget:krx:backfill:<날짜>`(5,000)를 함께 센다(`from_settings(backfill=True)`).
  KRX 가 일 한도 초과를 어떤 응답으로 알리는지는 [실측 필요 — probe_results §7 #12].
- **401** 은 두 갈래다(SD `krx_api.py` 머리말 :20~23): 본문 `Unauthorized API Call` = 그
  엔드포인트를 구독하지 않았다 → `KrxNotSubscribed`, 그날(KST)은 그 엔드포인트만 부르지 않는다
  (예산을 쓰지 않는다). 그 밖의 401 = 키 문제 → `KrxKeyError`(critical). 부르는 쪽이 health 를 낸다
  (이 모듈은 kbj.services 를 모른다 — 계약 ⑥).
- 429 → 리미터 감속(`on_rate_limited`) + `KrxThrottled`, 5xx → `KrxError(retryable=True)`.
  다시 부르기는 작업 등록부의 재시도(`krx.daily` 10분마다 10:00 까지)가 한다 — 여기서 돌지 않는다.
- 엔드포인트 이름·필드는 KRX OpenAPI 카탈로그 기준이다. 파생 2종은 GX 실측(#15), 주식·지수·ETP 는
  [실측 필요](키 받은 뒤 `probe_results.md` §7 #14).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from typing import Any, Final, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.time import kst_date, utcnow
from kbj.data.budget import DailyBudget, krx_budget
from kbj.data.http import FetchError, get_capped, make_client
from kbj.data.limits import Limits, load_limits
from kbj.data.ratelimit import Clock, LocalRateLimiter, Priority, RateLimiter, RedisRateLimiter

SOURCE: Final = "KRX"
LIMITS_SOURCE: Final = "krx"
# KRX OpenAPI 게이트웨이. 이 주소는 이 파일에만 둔다(scripts/check_canonical.py 그룹 `krx_api` —
# 설계 §9.1). legacy 는 논리 URL 브리지 `krx:` 로만 부른다(설계 §3.7)
_KRX_BASE: Final = "https://data-dbg.krx.co.kr/svc/apis"

# ── 엔드포인트 (KRX OpenAPI 카탈로그 — 엔드포인트마다 따로 구독해야 한다) ─────────────────────
STK_DAILY: Final = "/sto/stk_bydd_trd"  # 유가증권 일별매매정보
KSQ_DAILY: Final = "/sto/ksq_bydd_trd"  # 코스닥 일별매매정보
KNX_DAILY: Final = "/sto/knx_bydd_trd"  # 코넥스 일별매매정보
STK_BASE_INFO: Final = "/sto/stk_isu_base_info"  # 유가증권 종목기본정보
KSQ_BASE_INFO: Final = "/sto/ksq_isu_base_info"  # 코스닥 종목기본정보
IDX_KOSPI_DAILY: Final = "/idx/kospi_dd_trd"  # KOSPI 시리즈 일별시세(업종지수 포함)
IDX_KOSDAQ_DAILY: Final = "/idx/kosdaq_dd_trd"  # KOSDAQ 시리즈 일별시세
IDX_KRX_DAILY: Final = "/idx/krx_dd_trd"  # KRX 시리즈 일별시세
ETF_DAILY: Final = "/etp/etf_bydd_trd"  # ETF 일별매매정보(NAV·상장좌수·순자산)
ETN_DAILY: Final = "/etp/etn_bydd_trd"  # ETN 일별매매정보
FUT_DAILY: Final = "/drv/fut_bydd_trd"  # 선물 일별매매정보(GX)
OPT_DAILY: Final = "/drv/opt_bydd_trd"  # 옵션 일별매매정보(GX)

StockMarket = Literal["kospi", "kosdaq", "konex"]
BaseInfoMarket = Literal["kospi", "kosdaq"]
IndexSeries = Literal["kospi", "kosdaq", "krx"]

STOCK_DAILY: Final[Mapping[StockMarket, str]] = {
    "kospi": STK_DAILY,
    "kosdaq": KSQ_DAILY,
    "konex": KNX_DAILY,
}
STOCK_BASE_INFO: Final[Mapping[BaseInfoMarket, str]] = {
    "kospi": STK_BASE_INFO,
    "kosdaq": KSQ_BASE_INFO,
}
INDEX_DAILY: Final[Mapping[IndexSeries, str]] = {
    "kospi": IDX_KOSPI_DAILY,
    "kosdaq": IDX_KOSDAQ_DAILY,
    "krx": IDX_KRX_DAILY,
}
ENDPOINTS: Final[tuple[str, ...]] = (
    STK_DAILY,
    KSQ_DAILY,
    KNX_DAILY,
    STK_BASE_INFO,
    KSQ_BASE_INFO,
    IDX_KOSPI_DAILY,
    IDX_KOSDAQ_DAILY,
    IDX_KRX_DAILY,
    ETF_DAILY,
    ETN_DAILY,
    FUT_DAILY,
    OPT_DAILY,
)

KRX_CONNECT_S: Final = 5.0  # 확인 필요: KRX 응답 시간·크기 미실측 — 보수적으로(GX)
KRX_READ_S: Final = 30.0
KRX_TOTAL_S: Final = 120.0
KRX_MAX_BYTES: Final = 64 << 20
ERROR_MAX: Final = 200
NOT_SUBSCRIBED_MARK: Final = "Unauthorized API Call"


def dataset_of(endpoint: str) -> str:
    """엔드포인트 → 카탈로그 데이터셋 이름(`/sto/stk_bydd_trd` → `sto/stk_bydd_trd`)."""
    return endpoint.lstrip("/")


class KrxError(FetchError):
    """KRX 호출 실패. 문구엔 인증키가 없다(가렸다). `status` = HTTP 상태(응답을 받았을 때).

    `endpoint` 는 부른 엔드포인트(`/drv/opt_bydd_trd`), `critical` 은 운영 알림 대상(키 문제),
    `retryable` 은 다음 재시도에서 나을 수 있는 실패(5xx·429·전송 오류).
    """

    critical: bool = False

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        endpoint: str = "",
        retryable: bool = False,
    ) -> None:
        self.endpoint = endpoint
        self.retryable = retryable
        super().__init__(SOURCE, "", status, message)


class KrxKeyError(KrxError):
    """HTTP 401(구독 문제가 아닌 것) — 키가 없거나 틀렸다. 운영 critical."""

    critical = True


class KrxNotSubscribed(KrxError):
    """HTTP 401 `Unauthorized API Call` — 이 엔드포인트를 구독하지 않았다(키는 맞다).

    그날(KST) 이 엔드포인트는 다시 부르지 않는다. 구독은 openapi.krx.co.kr 마이페이지에서.
    """


class KrxThrottled(KrxError):
    """HTTP 429 — 리미터를 감속했다. 다음 주기에 다시."""


class KrxFormatError(KrxError):
    """응답 모양이 기대와 다르다(JSON 아님·객체 아님·`OutBlock_1` 없음)."""


class KrxDailyResponse(BaseModel):
    """일별 정보 응답 봉투. 행은 dict 그대로 — 모델 검증은 행 단위로 따로(한 행이 전체를 막지
    않게, `kbj.data.private.krx.models.parse_rows`)."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    rows: list[dict[str, Any]] = Field(validation_alias="OutBlock_1")


class KrxClient:
    """KRX OpenAPI 일별 정보. 호출마다 연결을 새로 연다(하루 몇십 번뿐). 시험은 `transport`.

    `limiter`·`budget` 을 안 주면 한도 없이 부른다 — 운영에서는 `from_settings` 로 만든다.
    """

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = _KRX_BASE,
        connect_s: float = KRX_CONNECT_S,
        read_s: float = KRX_READ_S,
        total_s: float = KRX_TOTAL_S,
        max_bytes: int = KRX_MAX_BYTES,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        limiter: RateLimiter | None = None,
        budget: DailyBudget | None = None,
        extra_budgets: Sequence[DailyBudget] = (),
        priority: Priority = Priority.P3,
        acquire_timeout: float | None = 120.0,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise ValueError("KRX 인증키가 비었다")
        if min(connect_s, read_s, total_s) <= 0 or max_bytes <= 0:
            raise ValueError("시간 제한·크기 상한은 0 보다 커야 한다")
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._connect_s = connect_s
        self._read_s = read_s
        self._total = total_s
        self._max = max_bytes
        self._transport = transport
        self._clock = clock
        self._limiter = limiter
        # 가져가는 순서: 좁은 예산(백필 등 `extra_budgets`) → 공용 일 예산(`budget`) — `daily` 참고
        self._budgets = tuple(b for b in (*extra_budgets, budget) if b is not None)
        self._priority = priority
        self._acquire_timeout = acquire_timeout
        self._now = now
        self._off: dict[str, date] = {}  # 구독 안 된 엔드포인트 → 그날(KST)
        self.calls = 0  # 실제로 보낸 HTTP 요청 수(시험·운영 화면용)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis | None = None,
        *,
        limits: Limits | None = None,
        rate_clock: Clock | None = None,
        backfill: bool = False,
        **kw: Any,
    ) -> KrxClient:
        """`KBJ_KRX_API_KEY` + config/limits.yaml `krx` 의 리미터·일 예산.

        `redis` 를 주면 프로세스가 달라도 같은 버킷(`rl:krx:<키 해시>`)·같은 카운터
        (`krx:calls:<날짜>`)를 쓴다. 없으면 이 프로세스 안에서만 센다(시험·스크립트).
        `backfill=True` 면 백필 상한(`backfill_cap`)을 함께 센다.
        """
        if settings.krx_api_key is None:
            raise KrxKeyError("KBJ_KRX_API_KEY 가 없다")
        src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
        cfg = src.rate_config()
        limiter: RateLimiter
        if redis is not None:
            key = settings.krx_api_key.get_secret_value()
            limiter = RedisRateLimiter.scoped(redis, LIMITS_SOURCE, key, cfg, rate_clock)
        else:
            limiter = LocalRateLimiter(cfg, rate_clock)
        if src.daily_cap is None:
            raise ValueError("limits.yaml krx.daily_cap 이 없다")
        budget = krx_budget(redis, src.daily_cap)
        extra: list[DailyBudget] = []
        if backfill:
            if src.backfill_cap is None:
                raise ValueError("limits.yaml krx.backfill_cap 이 없다")
            extra.append(DailyBudget(redis, LIMITS_SOURCE, src.backfill_cap, scope="backfill"))
        return cls(settings.krx_api_key, limiter=limiter, budget=budget, extra_budgets=extra, **kw)

    def __repr__(self) -> str:  # 인증키를 보이지 않는다
        return f"KrxClient(base_url={self._base!r})"

    # ── 이름 붙은 엔드포인트 ─────────────────────────────────────────────────────────────

    def stock_daily(self, market: StockMarket, bas_dd: date) -> list[dict[str, Any]]:
        """유가·코스닥·코넥스 전 종목 하루(시·고·저·종·거래량·거래대금·시가총액·상장주식수)."""
        return self.daily(STOCK_DAILY[market], bas_dd)

    def stock_base_info(self, market: BaseInfoMarket, bas_dd: date) -> list[dict[str, Any]]:
        """종목기본정보(표준·단축코드·상장일·주식종류·액면가·상장주식수)."""
        return self.daily(STOCK_BASE_INFO[market], bas_dd)

    def index_daily(self, series: IndexSeries, bas_dd: date) -> list[dict[str, Any]]:
        """KOSPI·KOSDAQ·KRX 시리즈 지수 하루(업종지수 포함)."""
        return self.daily(INDEX_DAILY[series], bas_dd)

    def etf_daily(self, bas_dd: date) -> list[dict[str, Any]]:
        """ETF 하루 — NAV·상장좌수·순자산총액·거래대금(docs/metrics.md §4)."""
        return self.daily(ETF_DAILY, bas_dd)

    def etn_daily(self, bas_dd: date) -> list[dict[str, Any]]:
        """ETN 하루 — 지표가치·거래대금."""
        return self.daily(ETN_DAILY, bas_dd)

    def futures_daily(self, bas_dd: date) -> list[dict[str, Any]]:
        """파생 선물 하루(GX)."""
        return self.daily(FUT_DAILY, bas_dd)

    def options_daily(self, bas_dd: date) -> list[dict[str, Any]]:
        """파생 옵션 하루(GX) — 약 16,700행."""
        return self.daily(OPT_DAILY, bas_dd)

    # ── 한 번 부르기 ────────────────────────────────────────────────────────────────────

    def daily(self, endpoint: str, bas_dd: date) -> list[dict[str, Any]]:
        """`bas_dd` 하루의 행(dict). 빈 날은 []. 실패는 `KrxError` 갈래(키를 가린 문구).

        예외: `BudgetExhausted`(그날 상한 — 부르지 않았다), `RateLimitTimeout`(허가 대기 초과).
        """
        if isinstance(bas_dd, datetime) or not isinstance(bas_dd, date):
            raise TypeError("bas_dd 는 date 여야 한다(시각 아님)")
        if not endpoint.startswith("/") or "://" in endpoint or "?" in endpoint:
            raise ValueError(f"endpoint 는 '/' 로 시작하는 경로만: {endpoint!r}")
        what = f"{endpoint} {bas_dd:%Y%m%d}"
        today = kst_date(self._now())
        if self._off.get(endpoint) == today:
            raise KrxNotSubscribed(
                f"{what}: 오늘({today}) 구독 안 됨으로 껐다 — 부르지 않았다", endpoint=endpoint
            )
        if self._limiter is not None:
            self._limiter.acquire(self._priority, endpoint, self._acquire_timeout)
        at = self._now()
        # 좁은 예산(백필)부터 가져간다 — 백필 상한에 걸린 시도가 공용 `krx:calls` 를 헛되이 올려
        # `krx.daily` 몫을 깎지 않게. 상한이면 BudgetExhausted — 부르지 않는다
        for b in self._budgets:
            b.take(at)
        try:
            status, body = self._get(endpoint, bas_dd)
        except KrxError:
            raise
        except httpx.HTTPError as e:
            raise KrxError(
                self._redact(f"{what}: {type(e).__name__}: {e}"),
                endpoint=endpoint,
                retryable=True,
            ) from None
        if status != 200:
            self._raise_status(what, endpoint, status, body, today)
        try:
            doc = json.loads(body)
        except ValueError:
            raise KrxFormatError(
                f"{what}: 응답이 JSON 이 아니다", status=status, endpoint=endpoint
            ) from None
        if not isinstance(doc, dict):
            raise KrxFormatError(f"{what}: 응답이 객체가 아니다", status=status, endpoint=endpoint)
        try:
            resp = KrxDailyResponse.model_validate(doc)
        except ValidationError as e:
            keys = ",".join(sorted(str(k) for k in doc)[:5])
            msg = f"{what}: 응답 형식 오류 {e.error_count()}건 (키 {keys})"
            raise KrxFormatError(self._redact(msg), status=status, endpoint=endpoint) from None
        return resp.rows

    def _raise_status(
        self, what: str, endpoint: str, status: int, body: bytes, today: date
    ) -> None:
        text = body.decode("utf-8", "replace")
        msg = self._redact(f"{what}: HTTP {status} {text}")
        if status == 401:
            if NOT_SUBSCRIBED_MARK in text:
                self._off[endpoint] = today
                raise KrxNotSubscribed(msg, status=status, endpoint=endpoint)
            raise KrxKeyError(msg, status=status, endpoint=endpoint)
        if status == 429:
            if self._limiter is not None:
                self._limiter.on_rate_limited()
            raise KrxThrottled(msg, status=status, endpoint=endpoint, retryable=True)
        raise KrxError(msg, status=status, endpoint=endpoint, retryable=status >= 500)

    def _get(self, endpoint: str, bas_dd: date) -> tuple[int, bytes]:
        """상태 코드와 본문 바이트. 접속·읽기 한 번마다 시간 제한, 전체는 total_s 로 끊는다."""
        headers = {"AUTH_KEY": self._key.get_secret_value()}
        self.calls += 1
        with make_client(
            self._base,
            connect_s=self._connect_s,
            read_s=self._read_s,
            transport=self._transport,
        ) as client:
            try:
                return get_capped(
                    client,
                    endpoint,
                    params={"basDd": f"{bas_dd:%Y%m%d}"},
                    headers=headers,
                    max_bytes=self._max,
                    total_s=self._total,
                    clock=self._clock,
                    source=SOURCE,
                    dataset=endpoint,
                )
            except FetchError as e:  # 크기·전체 시간 초과
                raise KrxError(
                    f"{endpoint}: {e.reason}", status=e.status, endpoint=endpoint
                ) from None

    def _redact(self, text: str) -> str:
        """첫 줄만, 인증키를 가리고 ERROR_MAX 자로."""
        lines = text.strip().splitlines()
        msg = lines[0] if lines else ""
        key = self._key.get_secret_value()
        if key:
            msg = msg.replace(key, "***")
        return msg if len(msg) <= ERROR_MAX else msg[: ERROR_MAX - 1] + "…"

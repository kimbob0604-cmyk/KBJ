"""FRED(세인트루이스 연은) API — 공개 등급은 **미국 정부 작성 시리즈만**(DATA_TIERS §1, D-P5-6).

다시 만든 원본 기능: bok `fetch-fred.js`(74계열 — 코드 없음, docs/inventory.md 기능 기준 — U3)·SD
`api_global_macro` 채권·금리 절(장단기 금리차는 엔진 `kbj.engines.macro.derive`).

- 두 층(ECOS 와 같은 모양 — `kbj.data.public.ecos.client`).
    - `FredTransport` — 전송·오류·리미터. **등급을 보지 않는다**. 저작권 시리즈(S&P·ICE BofA·CBOE
      등)는 `kbj.data.private.fred_restricted` 가 이 전송층을 재사용한다(로그인 → 공개 import 는
      계약 ①이 허용, 거꾸로는 금지).
    - `FredClient` — 공개 쪽 입구. 만들 때 받은 **정부 시리즈 목록**(`config/macro.yaml` 의
      `FRED:gov_series` 항목 — `from_settings` 가 읽는다) 밖의 시리즈는 `TierError`(부르지 않는다).
- 경로
  `/fred/series/observations`(`series_id`·`observation_start`·`observation_end`·`file_type=json`)
  와 `/fred/releases/dates`(경제 캘린더 공개 행 — D-P5-8). 키는 쿼리 `api_key`(`KBJ_FRED_KEY`) —
  오류 문구는 키(원문·URL 인코딩)를 지운 뒤에만 만든다(절대 규칙 5).
- 값 `"."` = 결측(FRED 표기) → None(행은 남기고 quality invalid — 0 으로 채우지 않는다). 그 밖에
  숫자가
  아니면 형식이 바뀐 것으로 보고 실패.
- 오류: 429 → 리미터 감속 + `FredThrottled`. 400 본문 `error_message` 에 'api_key' → `FredKeyError`
  (critical), 'does not exist' → `FredNotFound`. 그 밖 → `FredError`. 오류 본문 모양
  `{"error_code": 400, "error_message": "…"}` [확인 필요 #45].
- 한도: 공표 분당 120 [확인 필요] — 2/s 상한(limits.yaml `fred`, 공개·로그인이 같은 키라 버킷 하나).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from urllib.parse import quote, quote_plus

import httpx
from pydantic import SecretStr
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.quality import Quality
from kbj.data.http import FetchError, get_capped, make_client, scrub
from kbj.data.limits import Limits, load_limits
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RedisRateLimiter

__all__ = [
    "FredClient",
    "FredError",
    "FredKeyError",
    "FredNotFound",
    "FredObservation",
    "FredReleaseDate",
    "FredThrottled",
    "FredTransport",
    "TierError",
    "parse_observations",
    "parse_release_dates",
    "parse_value",
]

SOURCE: Final = "FRED"
LIMITS_SOURCE: Final = "fred"
_BASE: Final = "https://api.stlouisfed.org"
_OBS: Final = "/fred/series/observations"
_RELEASE_DATES: Final = "/fred/releases/dates"
TR_OBS: Final = "fred:observations"
TR_RELEASES: Final = "fred:releases_dates"
MAX_BYTES: Final = 16 * 1024 * 1024
TOTAL_S: Final = 60.0
RELEASE_LIMIT: Final = 1000  # releases/dates 한 쪽 최대 [확인 필요 #45]
MISSING: Final = "."


class TierError(PermissionError):
    """공개 쪽에서 정부 시리즈가 아닌(저작권·판정 전) FRED 시리즈를 부르려 했다."""


class FredError(FetchError):
    """FRED 호출 실패(사유 포함 — 키는 가렸다)."""

    critical: bool = False

    def __init__(self, dataset: str, status: int | None, reason: str) -> None:
        super().__init__(SOURCE, dataset, status, reason)


class FredKeyError(FredError):
    """키 없음·무효 — 운영 critical."""

    critical = True


class FredThrottled(FredError):
    """429 — 감속했다. 다음 실행(재시도)이 다시."""


class FredNotFound(FredError):
    """없는 시리즈."""


@dataclass(frozen=True)
class FredObservation:
    """관측값 한 줄. value None = FRED 결측 '.'(quality invalid)."""

    series_id: str
    date: date
    value: Decimal | None
    raw: str

    @property
    def quality(self) -> Quality:
        return Quality.OK if self.value is not None else Quality.INVALID


@dataclass(frozen=True)
class FredReleaseDate:
    release_id: int
    name: str
    date: date


def parse_value(raw: object) -> Decimal | None:
    """'.' → None, 숫자 문자열 → Decimal. 그 밖은 ValueError(형식 바뀜)."""
    s = "" if raw is None else str(raw).strip()
    if s == MISSING:
        return None
    try:
        v = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"FRED 값이 숫자가 아니다: {s[:20]!r}") from None
    if not v.is_finite():
        raise ValueError(f"FRED 값이 유한수가 아니다: {s[:20]!r}")
    return v


def parse_observations(series_id: str, payload: Any) -> list[FredObservation]:
    """`observations` 배열 → 날짜 오름차순. 날짜·값 형식이 틀리면 ValueError."""
    obs = (payload or {}).get("observations") if isinstance(payload, Mapping) else None
    if not isinstance(obs, list):
        raise ValueError("observations 배열이 없다")
    out: list[FredObservation] = []
    for o in obs:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(o, Mapping):
            raise ValueError("observations 항목이 객체가 아니다")
        raw = str(o.get("value", ""))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        d = date.fromisoformat(str(o.get("date", "")))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        out.append(FredObservation(series_id, d, parse_value(raw), raw))
    out.sort(key=lambda x: x.date)
    return out


def parse_release_dates(payload: Any) -> list[FredReleaseDate]:
    rows = (payload or {}).get("release_dates") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("release_dates 배열이 없다")
    out: list[FredReleaseDate] = []
    for r in rows:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(r, Mapping):
            raise ValueError("release_dates 항목이 객체가 아니다")
        name = str(r.get("release_name", "")).strip()  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        if not name:
            raise ValueError("release_name 이 비었다")
        out.append(
            FredReleaseDate(
                int(r.get("release_id")),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportArgumentType]
                name,
                date.fromisoformat(str(r.get("date", ""))),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            )
        )
    out.sort(key=lambda x: (x.date, x.release_id))
    return out


def _error_message(body: bytes) -> str:
    try:
        js = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return body[:300].decode("utf-8", "replace")
    if isinstance(js, Mapping):
        msg = js.get("error_message")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if msg:
            return str(msg)[:300]  # pyright: ignore[reportUnknownArgumentType]
    return body[:300].decode("utf-8", "replace")


class FredTransport:
    """FRED 전송·오류·리미터. 등급을 보지 않는다 — 공개 쪽은 `FredClient` 를 쓴다."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        limiter: RateLimiter,
        transport: httpx.BaseTransport | None = None,
        base_url: str = _BASE,
        priority: Priority = Priority.P3,
        acquire_timeout_s: float | None = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        key = api_key.get_secret_value().strip()
        if not key or any(c.isspace() for c in key):
            raise FredKeyError("", None, "FRED 키가 비었거나 형식이 틀렸다")
        self._key = key
        self._secrets = [key, quote(key, safe=""), quote_plus(key)]
        self._limiter = limiter
        self._priority = priority
        self._acquire_timeout = acquire_timeout_s
        self._clock = clock
        self._http = make_client(base_url, transport=transport)
        self.calls = 0

    def __repr__(self) -> str:
        return "FredTransport(key=***)"

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: Mapping[str, str], *, tr_id: str, dataset: str) -> Any:
        q = {**params, "api_key": self._key, "file_type": "json"}
        self._limiter.acquire(self._priority, tr_id, self._acquire_timeout)
        self.calls += 1
        try:
            status, body = get_capped(
                self._http, path, params=q, max_bytes=MAX_BYTES, total_s=TOTAL_S,
                clock=self._clock, source=SOURCE, dataset=dataset,
            )  # fmt: skip
        except httpx.HTTPError as e:
            raise FredError(
                dataset, None, scrub(f"전송 실패: {type(e).__name__}", q, self._secrets)
            ) from None
        except FetchError as e:
            raise FredError(dataset, e.status, scrub(e.reason, q, self._secrets)) from None
        if status == 200:
            try:
                return json.loads(body)
            except ValueError:
                raise FredError(dataset, status, "200 이지만 JSON 이 아니다") from None
        msg = scrub(_error_message(body), q, self._secrets)
        if status == 429:
            self._limiter.on_rate_limited()
            raise FredThrottled(dataset, status, f"429 한도 초과 — 감속 {msg}".strip())
        low = msg.lower()
        if status in (400, 401, 403) and "api_key" in low:
            raise FredKeyError(dataset, status, f"키 거절: {msg}")
        if status in (400, 404) and "does not exist" in low:
            raise FredNotFound(dataset, status, msg)
        raise FredError(dataset, status, f"HTTP {status} {msg}")

    def observations(
        self, series_id: str, start: date | None = None, end: date | None = None
    ) -> list[FredObservation]:
        if not series_id or not series_id.replace("_", "").isalnum():
            raise ValueError(f"FRED 시리즈 id 형식: {series_id!r}")
        params: dict[str, str] = {"series_id": series_id}
        if start is not None:
            params["observation_start"] = start.isoformat()
        if end is not None:
            params["observation_end"] = end.isoformat()
        dataset = f"observations:{series_id}"
        payload = self._get(_OBS, params, tr_id=TR_OBS, dataset=dataset)
        try:
            return parse_observations(series_id, payload)
        except ValueError as e:
            raise FredError(dataset, 200, f"응답 형식: {e}") from None

    def release_dates(self, start: date, end: date) -> list[FredReleaseDate]:
        if end < start:
            raise ValueError("end 가 start 보다 앞이다")
        params = {
            "realtime_start": start.isoformat(),
            "realtime_end": end.isoformat(),
            "include_release_dates_with_no_data": "true",
            "limit": str(RELEASE_LIMIT),
            "sort_order": "asc",
        }
        payload = self._get(_RELEASE_DATES, params, tr_id=TR_RELEASES, dataset="releases/dates")
        try:
            return parse_release_dates(payload)
        except (ValueError, TypeError) as e:
            raise FredError("releases/dates", 200, f"응답 형식: {e}") from None


def limiter_for(
    settings: Settings, redis: Redis, *, limits: Limits | None = None, clock: Clock | None = None
) -> tuple[SecretStr, RedisRateLimiter]:
    """`KBJ_FRED_KEY` 와 Redis 리미터 `rl:fred:<키 해시>`(공개·로그인 같은 버킷)."""
    if settings.fred_key is None:
        raise FredKeyError("", None, "KBJ_FRED_KEY 가 없다")
    src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
    limiter = RedisRateLimiter.scoped(
        redis, LIMITS_SOURCE, settings.fred_key.get_secret_value(), src.rate_config(), clock
    )
    return settings.fred_key, limiter


class FredClient:
    """공개 등급 FRED — 정부 시리즈(`gov_series`)만 받는다. 발표일(releases/dates)은 공개."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        limiter: RateLimiter,
        gov_series: Collection[str],
        transport: httpx.BaseTransport | None = None,
        **kw: Any,
    ) -> None:
        if not gov_series:
            raise ValueError("정부 시리즈 목록이 비었다(config/macro.yaml FRED:gov_series)")
        self.gov_series = frozenset(gov_series)
        self.transport = FredTransport(api_key, limiter=limiter, transport=transport, **kw)

    def __repr__(self) -> str:
        return f"FredClient(key=***, gov_series={len(self.gov_series)})"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        gov_series: Collection[str],
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> FredClient:
        key, limiter = limiter_for(settings, redis, limits=limits, clock=clock)
        return cls(key, limiter=limiter, gov_series=gov_series, **kw)

    def close(self) -> None:
        self.transport.close()

    def require_public(self, series_id: str) -> None:
        if series_id not in self.gov_series:
            raise TierError(
                f"FRED {series_id}: 정부 시리즈 목록 밖 — 공개 쪽은 받지 않는다. 저작권 시리즈는 "
                "kbj.data.private.fred_restricted 로(D-P5-6)"
            )

    def observations(
        self, series_id: str, start: date | None = None, end: date | None = None
    ) -> list[FredObservation]:
        self.require_public(series_id)
        return self.transport.observations(series_id, start, end)

    def release_dates(self, start: date, end: date) -> list[FredReleaseDate]:
        return self.transport.release_dates(start, end)

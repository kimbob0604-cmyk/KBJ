"""ForexFactory 주간 경제 일정(예상치 포함) — **로그인 등급**(비공식 피드·약관 미확인, DATA_TIERS
§1).

다시 만든 원본 기능: bok `fetch-consensus.js`(코드 없음 — docs/inventory.md 기능 기준, U3).
사용자 결정(2026-10-13 Q3): 예상치를 쓴다 — 로그인 화면·아침 브리핑만. 원자료·예상치는 공개
빌드·공개
내보내기·레포 픽스처에 넣지 않는다(시험은 합성 — tests/fakes/ff_server.py).

- 주소 `/ff_calendar_thisweek.json`(이번 주 — 비공식 JSON 피드) [확인 필요 #49]. 항목 칸
  `title`·`country`(통화 코드)·`date`(ISO, 시간대 포함)·`impact`(High·Medium·Low·Holiday)·
  `forecast`·`previous`(문자열 — '0.3%'·'150K'·'' ) [확인 필요 #49]. 갱신 주기 [확인 필요 #49].
- **받아서 정규화만**: `FfEvent`(KST 날짜·시각, 국가 2자, 중요도 1~3, 예상치 Decimal|None + 단위
  접미사). 문자열 예상치는 접미사(%·K·M·B·T)를 떼어 숫자로 — 숫자가 아니면 None(0 금지, 사유
  `forecast_raw` 보존). Holiday·모르는 통화는 버린다(`COUNTRIES` 밖).
- 리미터 `ff`(0.2/s). 429 → 감속 + `FfThrottled`. 키 없음.
- 공개 캘린더 행에 덧붙이는 일(같은 나라·날·종류)은 엔진 `kbj.engines.macro.calendar` 가 한다 —
  이 모듈은 종류(kind) 추정만 `KINDS`(제목 앞부분 → kind [확인 필요 #49])로 한다.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import httpx
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.time import KST
from kbj.data.http import FetchError, get_capped, make_client
from kbj.data.limits import Limits, load_limits
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RedisRateLimiter
from kbj.data.spec import DatasetSpec, Tier

__all__ = [
    "COUNTRIES",
    "DATASETS",
    "KINDS",
    "FfClient",
    "FfError",
    "FfEvent",
    "FfThrottled",
    "parse_events",
    "parse_forecast",
]

SOURCE: Final = "FF"
ROW_SOURCE: Final = "ff"  # prv_macro.calendar.source
LIMITS_SOURCE: Final = "ff"
_BASE: Final = "https://nfs.faireconomy.media"
_PATH: Final = "/ff_calendar_thisweek.json"
TR_ID: Final = "ff:calendar"
MAX_BYTES: Final = 2 * 1024 * 1024
TOTAL_S: Final = 30.0
# 통화 → 국가 2자(받는 나라만 — 나머지는 버린다) [제안]
COUNTRIES: Final[Mapping[str, str]] = {
    "USD": "US",
    "KRW": "KR",
    "CNY": "CN",
    "JPY": "JP",
    "EUR": "EU",
}
IMPACT: Final[Mapping[str, int]] = {"high": 3, "medium": 2, "low": 1}
# 제목 앞부분(소문자) → 종류(공개 캘린더 행과 맞추는 열쇠 — kbj.engines.macro.calendar) [확인 필요
# #49]
KINDS: Final[tuple[tuple[str, str], ...]] = (
    ("core cpi", "core_cpi"),
    ("cpi", "cpi"),
    ("core pce", "core_pce"),
    ("ppi", "ppi"),
    ("non-farm employment change", "payrolls"),
    ("unemployment rate", "unemployment"),
    ("federal funds rate", "fomc"),
    ("fomc", "fomc"),
    ("advance gdp", "gdp"),
    ("prelim gdp", "gdp"),
    ("final gdp", "gdp"),
    ("gdp", "gdp"),
    ("retail sales", "retail_sales"),
    ("ism manufacturing pmi", "ism_mfg"),
    ("ism services pmi", "ism_svc"),
    ("bok rate", "bok_rate"),
    ("interest rate decision", "rate_decision"),
)
_SUFFIX: Final[Mapping[str, str]] = {"%": "%", "K": "K", "M": "M", "B": "B", "T": "T"}
_SLUG = re.compile(r"[^a-z0-9]+")


class FfError(FetchError):
    def __init__(self, dataset: str, status: int | None, reason: str) -> None:
        super().__init__(SOURCE, dataset, status, reason)


class FfThrottled(FfError):
    """429 — 감속했다."""


@dataclass(frozen=True)
class FfEvent:
    country: str  # 2자(US·KR …)
    kind: str  # KINDS 로 정한 종류, 모르면 'ff_<제목 슬러그>'
    code: str  # event_id 의 끝 조각(제목 슬러그)
    date: date  # KST 날짜
    time_kst: dtime | None  # 종일(All Day·Tentative)이면 None
    title: str
    importance: int
    forecast: Decimal | None
    unit: str | None  # 예상치 접미사(%·K·M·B·T) — 없으면 None
    forecast_raw: str
    previous_raw: str


def parse_forecast(raw: object) -> tuple[Decimal | None, str | None]:
    """'0.3%' → (0.3, '%'), '150K' → (150, 'K'), '' → (None, None). 숫자가 아니면 (None, None)."""
    s = "" if raw is None else str(raw).strip().replace(",", "")
    if not s:
        return None, None
    unit = _SUFFIX.get(s[-1])
    body = s[:-1] if unit else s
    try:
        v = Decimal(body)
    except InvalidOperation:
        return None, None
    return (v, unit) if v.is_finite() else (None, None)


def _kind(title: str) -> tuple[str, str]:
    low = title.lower().strip()
    slug = _SLUG.sub("_", low).strip("_")[:48] or "event"
    for prefix, kind in KINDS:
        if low.startswith(prefix):
            return kind, slug
    return f"ff_{slug}"[:40].rstrip("_"), slug


def parse_events(payload: Any) -> list[FfEvent]:
    """피드 배열 → KST 날짜·시각 오름차순. 칸이 틀리면 ValueError(형식 바뀜)."""
    if not isinstance(payload, list):
        raise ValueError("피드가 배열이 아니다")
    out: list[FfEvent] = []
    for r in payload:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(r, Mapping):
            raise ValueError("피드 항목이 객체가 아니다")
        cur = str(r.get("country", "")).upper()  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        imp = IMPACT.get(str(r.get("impact", "")).lower())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        country = COUNTRIES.get(cur)
        if country is None or imp is None:
            continue  # 받지 않는 나라·Holiday
        title = str(r.get("title", "")).strip()  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        if not title:
            raise ValueError("title 이 비었다")
        raw_dt = str(r.get("date", ""))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        dt = datetime.fromisoformat(raw_dt)
        if dt.tzinfo is None:
            raise ValueError("date 에 시간대가 없다")
        local = dt.astimezone(KST)
        all_day = dt.timetz().replace(tzinfo=None) == dtime(
            0, 0
        )  # 피드는 종일을 00:00 으로 [확인 필요]
        kind, code = _kind(title)
        fc_raw = str(r.get("forecast", "") or "")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        value, unit = parse_forecast(fc_raw)
        out.append(
            FfEvent(
                country=country, kind=kind, code=code, date=local.date(),
                time_kst=None if all_day else local.time().replace(second=0, microsecond=0),
                title=title, importance=imp, forecast=value, unit=unit, forecast_raw=fc_raw,
                previous_raw=str(r.get("previous", "") or ""),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            )
        )  # fmt: skip
    out.sort(key=lambda e: (e.date, e.time_kst or dtime(0, 0), e.country, e.code))
    return out


class FfClient:
    """`FfClient(limiter).this_week() -> list[FfEvent]`. 시험은 `transport=` 에 가짜 서버."""

    def __init__(
        self,
        limiter: RateLimiter,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = _BASE,
        priority: Priority = Priority.P3,
        acquire_timeout_s: float | None = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limiter = limiter
        self._priority = priority
        self._acquire_timeout = acquire_timeout_s
        self._clock = clock
        self._http = make_client(base_url, transport=transport, headers={"User-Agent": "kbj"})

    def __repr__(self) -> str:
        return "FfClient()"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> FfClient:
        src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
        limiter = RedisRateLimiter.scoped(
            redis, LIMITS_SOURCE, "calendar", src.rate_config(), clock
        )
        return cls(limiter, **kw)

    def close(self) -> None:
        self._http.close()

    def this_week(self) -> list[FfEvent]:
        dataset = "calendar:thisweek"
        self._limiter.acquire(self._priority, TR_ID, self._acquire_timeout)
        try:
            status, body = get_capped(
                self._http, _PATH, max_bytes=MAX_BYTES, total_s=TOTAL_S, clock=self._clock,
                source=SOURCE, dataset=dataset,
            )  # fmt: skip
        except httpx.HTTPError as e:
            raise FfError(dataset, None, f"전송 실패: {type(e).__name__}") from None
        if status == 429:
            self._limiter.on_rate_limited()
            raise FfThrottled(dataset, status, "429 한도 초과 — 감속")
        if status != 200:
            raise FfError(dataset, status, f"HTTP {status} {body[:200].decode('utf-8', 'replace')}")
        try:
            return parse_events(json.loads(body))
        except ValueError as e:
            raise FfError(dataset, status, f"응답 형식: {e}") from None


# 카탈로그 PLANNED 의 FF:calendar 를 이리로 옮기는 일은 FND 몫(kbj/data/catalog.py — MAC 요청)
DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec(
        id=f"{SOURCE}:calendar",
        source=SOURCE,
        dataset="calendar",
        tier=Tier.PRIVATE,
        limiter=LIMITS_SOURCE,
        budget=None,
        published="수시(이번 주 피드 — 갱신 주기 [확인 필요 #49])",
        as_of_kind="run_date",
        store="prv_macro.calendar",
        notes=(
            "ForexFactory 주간 일정·예상치(비공식 피드 — 로그인, 사용자 결정 2026-10-13 Q3: 쓴다). "
            "칸 이름·갱신 주기 [확인 필요 #49]. "
            "작업 macro.morning_prv(config/macro.yaml ff.enabled)"
        ),
    ),
)

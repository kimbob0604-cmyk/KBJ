"""Stooq CSV 일봉 클라이언트 — 로그인 등급(약관 미확인). Nasdaq 히스토리 실패 종목의 폴백만.

승격 원본: ETF-Traker `board/us/sources.py` — `stooq_symbol`:161·`parse_stooq`:225·`stooq_daily`:243
(ET D-077). 파서는 본문 그대로, 원본 시험 `test_us_sources.py:TestStooq`·
`test_symbol_forms` 의 Stooq 단언은 `tests/unit/data/private/test_us_sources.py` 로 옮겼다(import
경로만). 통신만 `httpx` + 리미터(`limits.yaml stooq`).

- HTTP 200 에 본문이 비거나 'No data'·한도 문구면 예외(ET 실측 run #1 — 데이터센터 IP 차단·한도).
  빈 목록으로 돌려주면 '상장 첫날'처럼 읽힌다.
- 수정주가다(ET 머리말) [확인 필요 #47].
"""

from __future__ import annotations

import csv
import io
import re
import time
from collections.abc import Callable
from datetime import date
from typing import Any, Final

import httpx
from redis import Redis

from kbj.config.settings import Settings
from kbj.data.http import FetchError, get_capped, make_client
from kbj.data.limits import Limits, load_limits
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RedisRateLimiter

__all__ = [
    "LIMITS_SOURCE",
    "ROW_SOURCE",
    "SOURCE",
    "StooqClient",
    "StooqError",
    "parse_stooq",
    "stooq_symbol",
]

SOURCE: Final = "STOOQ"
LIMITS_SOURCE: Final = "stooq"
ROW_SOURCE: Final = "stooq"
_BASE: Final = "https://stooq.com"
_CSV: Final = "/q/d/l/"
MAX_BYTES: Final = 4 * 1024 * 1024
TOTAL_S: Final = 30.0


class StooqError(FetchError):
    """Stooq 수집 실패(사유 포함)."""

    def __init__(self, dataset: str, status: int | None, reason: str) -> None:
        super().__init__(SOURCE, dataset, status, reason)


def stooq_symbol(ticker: str) -> str:
    """stooq 심볼. 마침표가 하이픈이고 `.us` 가 붙는다 (BRK.B → brk-b.us)."""
    return ticker.lower().replace(".", "-") + ".us"


def parse_stooq(text: str | None) -> list[dict[str, Any]]:
    """stooq CSV → 오름차순 일봉. 헤더가 없거나 'N/D' 면 빈 리스트."""
    rows: list[dict[str, Any]] = []
    for rec in csv.DictReader(io.StringIO(text or "")):
        d = (rec.get("Date") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            continue

        def f(k: str, rec: dict[str, Any] = rec) -> float | None:
            try:
                return float(rec[k])
            except (TypeError, ValueError, KeyError):
                return None

        rows.append(
            dict(
                asof=d, open=f("Open"), high=f("High"), low=f("Low"), close=f("Close"),
                volume=f("Volume"), source=ROW_SOURCE,
            )
        )  # fmt: skip
    rows.sort(key=lambda r: r["asof"])
    return rows


class StooqClient:
    """`StooqClient(limiter).daily('AAPL', start)`. 시험은 `transport=` 에 가짜 서버."""

    def __init__(
        self,
        limiter: RateLimiter,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = _BASE,
        priority: Priority = Priority.P3,
        acquire_timeout_s: float | None = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limiter = limiter
        self._priority = priority
        self._acquire_timeout = acquire_timeout_s
        self._clock = clock
        self._http = make_client(base_url, transport=transport, headers={"User-Agent": "kbj"})

    def __repr__(self) -> str:
        return "StooqClient()"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> StooqClient:
        """Redis 리미터 `rl:stooq:<'csv' 해시>`(limits.yaml `stooq`)."""
        src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
        limiter = RedisRateLimiter.scoped(redis, LIMITS_SOURCE, "csv", src.rate_config(), clock)
        return cls(limiter, **kw)

    def close(self) -> None:
        self._http.close()

    def daily(self, ticker: str, start: date | str | None = None) -> list[dict[str, Any]]:
        """한 종목의 일봉(ET `stooq_daily` 본문 그대로)."""
        params = {"s": stooq_symbol(ticker), "i": "d"}
        if start:
            s = start.isoformat() if isinstance(start, date) else start
            params["d1"] = s.replace("-", "")
        dataset = f"daily:{ticker}"
        self._limiter.acquire(self._priority, "stooq:csv", self._acquire_timeout)
        try:
            status, body = get_capped(
                self._http, _CSV, params=params, max_bytes=MAX_BYTES, total_s=TOTAL_S,
                clock=self._clock, source=SOURCE, dataset=dataset,
            )  # fmt: skip
        except httpx.HTTPError as e:
            raise StooqError(dataset, None, f"전송 실패: {type(e).__name__}") from None
        if status != 200:
            raise StooqError(dataset, status, f"stooq {ticker} HTTP {status}")
        txt = body.decode("utf-8", "replace")
        if txt.strip().lower().startswith("no data") or "Exceeded the daily hits limit" in txt:
            if "Exceeded" in txt:
                self._limiter.on_rate_limited()  # 일 한도 — 감속(다시 부르기는 작업 재시도)
            raise StooqError(dataset, status, f"stooq {ticker} 데이터 없음 — {txt.strip()[:80]}")
        rows = parse_stooq(txt)
        if not rows:
            # 러너에서 실제로 이랬다 — HTTP 200 에 본문이 비어 있다(run #1).
            # 빈 리스트로 넘기면 '상장 첫날' 처럼 보이므로 사유를 올린다.
            raise StooqError(
                dataset,
                status,
                f"stooq {ticker} 0행 (HTTP 200, 본문 {len(txt)}바이트) "
                "— 데이터센터 IP 차단이나 한도로 보인다",
            )
        return rows

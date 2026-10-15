"""stock-dashboard 테마 마스터 받기 — `data/master/<파일>` raw 파일(ADR 0039).

공개 쪽에 둔다(ECOS 방식 — 로그인 어댑터 `kbj.data.private.stock_dashboard` 가 이 클라이언트를
그대로 쓴다, 로그인 → 공개 import 는 계약 ① 이 허용). 등급은 데이터셋이 정한다: 공개
`theme_classes`(대테마 분류 — themes.json 의 사용자 작성 칸만)·로그인 `theme_master`(네이버 유래
파일 전부).

- 주소: `config/theme.yaml master.base_url` + `/<파일 이름>`(기본은 GitHub raw main 브랜치 — 공개
  레포라 키 없음). 넘겨주기(redirect)는 따라가지 않는다(`make_client`).
- 받아서 바이트만 돌려준다 — 해석·검증은 순수 엔진(`kbj.engines.theme.master`)이 한다.
- 파일 하나 크기 상한(`max_bytes`)·전체 시간 상한. 넘으면 실패(자르지 않는다). 200 이 아니면 실패
  (상태·본문 앞 일부 — 키가 없는 공개 주소라 가릴 것이 없지만 공용 마스킹을 거친다).
- 리미터 `stock_dashboard`(1/s — 주 1회 파일 7개). 429 → 감속 + `ThemeMasterThrottled`.
- 시험은 `transport=httpx.MockTransport(...)`(가짜 서버) — 이 세션 컨테이너는 원천 주소가 막혀 있을
  수 있다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any, Final

import httpx
from redis import Redis

from kbj.config.settings import Settings
from kbj.data.http import FetchError, get_capped, make_client
from kbj.data.limits import Limits, load_limits
from kbj.data.public.stock_dashboard.datasets import LIMITER, SOURCE
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RedisRateLimiter

__all__ = ["ThemeMasterClient", "ThemeMasterError", "ThemeMasterThrottled"]

TR_ID: Final = "stock_dashboard:theme_master"
TOTAL_S: Final = 60.0
DEFAULT_MAX_BYTES: Final = 8 * 1024 * 1024


class ThemeMasterError(FetchError):
    def __init__(self, dataset: str, status: int | None, reason: str) -> None:
        super().__init__(SOURCE, dataset, status, reason)


class ThemeMasterThrottled(ThemeMasterError):
    """429 — 감속했다."""


class ThemeMasterClient:
    """`ThemeMasterClient(limiter, base_url=…).fetch("meta.json") -> bytes`."""

    def __init__(
        self,
        limiter: RateLimiter,
        *,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        priority: Priority = Priority.P4,
        acquire_timeout_s: float | None = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("base_url 은 https:// 로 시작해야 한다")
        self._limiter = limiter
        self._priority = priority
        self._acquire_timeout = acquire_timeout_s
        self._clock = clock
        self._max_bytes = max_bytes
        self._http = make_client(base_url, transport=transport)

    def __repr__(self) -> str:
        return "ThemeMasterClient()"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        base_url: str,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> ThemeMasterClient:
        src = (limits or load_limits(settings=settings)).source(LIMITER)
        limiter = RedisRateLimiter.scoped(redis, LIMITER, "theme_master", src.rate_config(), clock)
        return cls(limiter, base_url=base_url, **kw)

    def close(self) -> None:
        self._http.close()

    def fetch(self, name: str) -> bytes:
        """파일 하나(이름은 영문 소문자·숫자·밑줄 + 확장자 json·csv)."""
        if not name or "/" in name or not name.endswith((".json", ".csv")):
            raise ValueError(f"파일 이름 형식: {name[:40]!r}")
        self._limiter.acquire(self._priority, TR_ID, self._acquire_timeout)
        try:
            status, body = get_capped(
                self._http, f"/{name}", max_bytes=self._max_bytes, total_s=TOTAL_S,
                clock=self._clock, source=SOURCE, dataset=name,
            )  # fmt: skip
        except httpx.HTTPError as e:
            raise ThemeMasterError(name, None, f"전송 실패: {type(e).__name__}") from None
        if status == 429:
            self._limiter.on_rate_limited()
            raise ThemeMasterThrottled(name, status, "429 한도 초과 — 감속")
        if status != 200:
            raise ThemeMasterError(
                name, status, f"HTTP {status} {body[:120].decode('utf-8', 'replace')}"
            )
        return body

    def fetch_all(self, names: Iterable[str]) -> dict[str, bytes]:
        """여러 파일(하나라도 실패하면 그 오류를 올린다 — 일부만 적재하지 않는다)."""
        return {n: self.fetch(n) for n in names}

"""응답 캐시 — 키 = (경로, 정렬된 쿼리, 데이터 버전)(docs/p3_design.md §5.3).

- 데이터 버전 = 그 영역 작업들의 마지막 완료 시각(`ops.data_claim.done_at`·
  `ops.job_run.finished_at`)
  — Redis `api:data_version:<영역>` 에 15초 둔다(같은 버전이면 다시 계산하지 않는다).
  작업이 새 데이터를 쓰면 버전이 바뀌어 캐시가 저절로 낡는다.
- 응답 본문 캐시는 **이 프로세스 메모리**에 둔다(로그인 데이터를 Redis 에 복사해 두지 않는다 —
  사용자 1명·프로세스 1개라 충분하다). 크기 상한을 넘으면 오래된 것부터 버린다.
- 시각은 주입한 `now()` 로 잰다(만료 판정). 버전 원천이 없으면(시험·메모리 저장소) 캐시하지 않는다.
- 버전 원천의 오류는 삼키지 않는다 — 그대로 올라가 그 요청이 503 이 된다(app 의 처리기).
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Protocol

from redis import Redis

from kbj.store.redis_keys import api_data_version_key

__all__ = [
    "DOMAIN_JOBS",
    "CachedBody",
    "DataVersionSource",
    "DataVersions",
    "PgDataVersionSource",
    "ResponseCache",
    "etag_of",
]

VERSION_TTL_S: Final = 15
NO_VERSION: Final = "none"

# 영역 → 그 영역 데이터를 쓰는 작업(config/jobs.yaml 이름). 보드 작업은 수집 키가 없어
# data_claim 이 아니라 job_run 완료 시각으로 바뀐다.
DOMAIN_JOBS: Final[Mapping[str, tuple[str, ...]]] = {
    "market": (
        "krx.daily",
        "market.backfill",
        "market.close_collect",
        "market.intraday",
        "flows.intraday",
        "board.daily",
        "board.confirm",
    ),
    "board": ("board.daily", "board.confirm", "market.close_collect", "krx.daily"),
    "flows": ("krx.daily", "market.backfill", "market.close_collect", "flows.intraday",
              "board.daily", "board.confirm"),
    "etf": ("krx.daily", "market.backfill", "market.close_collect", "flows.intraday",
            "etf.collect"),
}  # fmt: skip


class DataVersionSource(Protocol):
    def __call__(self, domain: str, /) -> str: ...


_VERSION_SQL: Final = """
SELECT greatest(
    (SELECT max(done_at) FROM ops.data_claim WHERE status = 'done' AND job = ANY(%(jobs)s)),
    (SELECT max(finished_at) FROM ops.job_run WHERE status = 'ok' AND job = ANY(%(jobs)s))
)
"""


class PgDataVersionSource:
    """운영 버전 원천 — ops 표에서 영역 작업들의 마지막 완료 시각(읽기만)."""

    def __init__(self, conn_factory: Callable[[], Any]) -> None:
        self._connect = conn_factory

    def __call__(self, domain: str) -> str:
        jobs = list(DOMAIN_JOBS[domain])
        with self._connect() as conn:
            row = conn.execute(_VERSION_SQL, {"jobs": jobs}).fetchone()
        ts = None if row is None else row[0]
        return NO_VERSION if ts is None else ts.isoformat()


class DataVersions:
    """영역 데이터 버전 — Redis 에 15초 캐시."""

    def __init__(self, redis: Redis, source: DataVersionSource, ttl_s: int = VERSION_TTL_S) -> None:
        self._r = redis
        self._source = source
        self._ttl = ttl_s

    def get(self, domain: str) -> str:
        if domain not in DOMAIN_JOBS:
            raise ValueError(f"모르는 영역: {domain!r}")
        key = api_data_version_key(domain)
        raw = self._r.get(key)
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        if isinstance(raw, str):
            return raw
        v = self._source(domain)
        self._r.set(key, v, ex=self._ttl)
        return v


@dataclass(frozen=True)
class CachedBody:
    body: bytes
    etag: str
    expires_at: datetime


def etag_of(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def cache_key(path: str, query: Sequence[tuple[str, str]], version: str) -> str:
    q = "&".join(f"{k}={v}" for k, v in sorted(query))
    return f"{path}?{q}#{version}"


class ResponseCache:
    """프로세스 메모리 응답 캐시(크기 상한, 주입 시계로 만료).

    동기 라우트는 스레드 풀에서 동시에 돈다(위젯 여러 개를 한꺼번에 부른다) — 잠금 하나로
    지킨다 — 만료 삭제·LRU 이동·축출이 겹쳐 KeyError 가 나지 않게.
    """

    def __init__(self, *, now: Callable[[], datetime], max_entries: int = 256) -> None:
        if max_entries < 1:
            raise ValueError("max_entries 는 1 이상")
        self._now = now
        self._max = max_entries
        self._items: OrderedDict[str, CachedBody] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> CachedBody | None:
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return None
            if self._now() >= hit.expires_at:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return hit

    def put(self, key: str, body: bytes, ttl_s: int) -> CachedBody:
        item = CachedBody(body, etag_of(body), self._now() + timedelta(seconds=ttl_s))
        with self._lock:
            self._items[key] = item
            self._items.move_to_end(key)
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return item

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

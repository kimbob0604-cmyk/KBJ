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
    "DOMAIN_TS",
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
        "trade.customs_tenday",  # P6 — 상단 띠 '수출 속보' 칩(docs/p6_design.md §5)
        # 2026-10-27 실시간 점검(docs/realtime_audit.md) — 요약 보조 타일·띠 칩이 읽는 표의 작업:
        # 금융위 일봉(원장)·VKOSPI 타일·원/달러 타일과 신용스프레드 칩(매크로)·신용잔고 칩(금투협)·
        # 한국 대 글로벌 칩(미국 지수 일봉). 빠지면 그 작업이 쓴 값이 TTL(5분)까지 옛 본문에 묶였다
        "market.fsc_daily", "krx.vkospi", "market_stats.kofia", "us.eod",
        "macro.morning", "macro.morning_prv", "macro.monthly", "macro.monthly_prv",
        "macro.evening", "macro.evening_prv",
    ),
    "board": ("board.daily", "board.confirm", "market.close_collect", "krx.daily"),
    "flows": ("krx.daily", "market.backfill", "market.close_collect", "flows.intraday",
              "board.daily", "board.confirm", "market.fsc_daily"),
    "etf": ("krx.daily", "market.backfill", "market.close_collect", "flows.intraday",
            "etf.collect"),
    # P4(docs/p4_design.md §5.3 — 묶음 A): 종목 상세는 시세·보드 + 공시·재무·컨센서스·밸류 작업,
    # 공시 패널(페이지 5)은 공시·파생·알림 작업(+ 회사 시총 가산의 시세).
    # 관심종목(`/api/watchlist`)은 CLI 가 DB 에 직접 쓰므로 버전 캐시를 쓰지 않는다(영역 없음)
    "stock": ("krx.daily", "market.backfill", "market.close_collect", "market.intraday",
              "board.daily", "board.confirm", "filings.corp_code", "filings.company_profile",
              "filings.dart_feed", "filings.derive", "fin.quarterly", "fin.backfill",
              "reports.dart_excel", "consensus.snapshot", "fin.valuation_band", "earnings.alerts",
              "earnings.backfill", "market.fsc_daily"),
    "filings": ("filings.corp_code", "filings.company_profile", "filings.dart_feed",
                "filings.derive", "fin.quarterly", "earnings.alerts", "earnings.backfill",
                "krx.daily", "market.close_collect"),
    # P5(docs/p5_design.md §5 — 웨이브 1 FND). 장중 1분·미국 보드는 자기 작업만 보는 영역으로 나눴다
    # (1분마다 바뀌는 수집이 P3 수급 캐시를 매분 비우지 않게)
    "macro": ("macro.morning", "macro.morning_prv", "macro.monthly", "macro.monthly_prv",
              "macro.evening", "macro.evening_prv", "market_stats.kofia"),
    "program": ("program.intraday", "program.stock_scan", "program.daily", "program.stock_sweep",
                "program.backfill"),
    # 1분 투자자 차트는 지수 장중(market.intraday)도 겹쳐 그린다
    "flows_minute": ("flows.market_minute", "market.close_collect", "market.intraday"),
    "board_us": ("us.universe", "us.eod"),
    # P5 2차 묶음 IDX — 지수 정기변경 예측(구성종목·컷오프는 CLI 가 직접 쓴다 — 다음 예측
    # 실행 때 바뀐다)
    "index_review": ("index.calendar", "index.predict"),
    # P6(docs/p6_design.md §5 — 웨이브 1 FND) — 수출입 속보·탐색·매핑(관세청 수집·파생·매핑·지수)
    "trade": ("trade.customs_tenday", "trade.customs_monthly", "trade.derive", "trade.map_load",
              "trade.stock_index"),
    # P7(docs/p7_design.md §5 — 웨이브 1 FND-B). GEX 는 상시 서비스(gex.engine)가 쓰므로 작업 완료
    # 기록이 없다 — 버전은 마지막 엔진 사이클 시각(DOMAIN_TS 의 `gex:latest` 뜻 —
    # prv_gex.levels max(ts))
    # 과 알림 작업 중 늦은 것. 진단은 작업 셋(공개·로그인·VKOSPI).
    # `/api/gex/flow` 는 1분 투자자(prv_gex.investor_flow — flows.market_minute, 마감+10분까지)도
    # 읽는다
    "gex": ("gex.alert", "flows.market_minute"),
    "diag": ("diag.public", "diag.daily", "krx.vkospi"),
    # P8(docs/p8_design.md §5 — 웨이브 1 FND). 신호·성과는 규칙 작업이 쓴다.
    # 규칙·일지·매매·포트폴리오는
    # 쓰기 모듈이 DB 에 바로 쓰므로 영역 없음(캐시 없이 — kbj.services.api.writes.fresh_json)
    "rules": ("rules.intraday", "rules.close", "rules.outcome", "rules.backtest_nightly"),
    "backtest": ("rules.outcome", "rules.backtest_nightly"),
    # 페이지 9(ADR 0039) — 대테마 분류·편입(주 1회) + 강도의 시세(보드 종목 하루)
    "themes": ("theme.master_sync", "theme.classes_sync", "board.daily", "board.confirm"),
    # 페이지 11 스크리너 — 전 종목 일봉·스냅(마감 수집·KRX 일별)
    "screen": ("krx.daily", "market.backfill", "market.close_collect", "market.fsc_daily"),
}  # fmt: skip
# 작업 기록 말고 표의 최신 시각으로도 버전을 정하는 영역(상시 서비스가 쓰는 표 — 상수 SQL)
DOMAIN_TS: Final[Mapping[str, str]] = {
    "gex": "SELECT max(ts) FROM prv_gex.levels",
}


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
            extra = DOMAIN_TS.get(domain)
            if extra is not None:
                more = conn.execute(extra).fetchone()
                t2 = None if more is None else more[0]
                if t2 is not None and (ts is None or t2 > ts):
                    ts = t2
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

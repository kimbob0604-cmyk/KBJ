"""P3 수집 처리기 공용 — 자원 해석·KIS 한 번 부르기·데이터 키별 결과 모으기(docs/p3_design.md §1.2).

`krx_daily`·`market_close`·`market_intraday` 가 같이 쓴다(이 묶음 C 안에서만 — 두 벌 금지).

자원(`JobContext.resources`) — 시험·시뮬레이션은 가짜를 넣고, 운영은 설정·Redis 로 만든다:

- `repos` → `kbj.store.repos.pg_repos(resources["connect"])`
- `kis` → `KisRestClient.for_service(settings, resources["redis"])` — auth 토큰 읽기만·앱키 리미터
- `krx` → `KrxClient.from_settings(settings, resources["redis"], backfill=…)` — 리미터·일 예산
- `markets` → `kbj.config.markets.load_markets(settings=…)`
- `kr` → `TradingCalendar.default()`
- `notify` → (없으면 알림을 보내지 않고 detail 에 적는다)

데이터 키 결과(`Outcome`): 키마다 따로 받는다 — 한 키의 실패가 다른 키를 멈추지 않는다(절대 규칙 4).
받은 키만 `collected` 로 돌려주고, 못 받은 키는 사유를 detail 에 남긴다(실행기가 그 키만 다시
잡는다). 공표 전(`NotReady`)만 남았으면 `not_ready`, 하나라도 오류가 있으면 `failed`.

거래소 구분(D-P3-9): `config/markets.yaml` `kis.venues`(기본 KRX) 밖 거래소의 키는 **부르지
않는다**. 등록부가 그 키를 잡아 넘기면 0행으로 끝냈다고 돌려주고 detail `venues_off` 에
적는다(등록부 collects 에 venues 를 맞추는 것이 정석 — 묶음 S 요청).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final, Protocol

from kbj.config.markets import MarketsConfig, load_markets
from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.data.private.kis.parse import PATHS, require_ok
from kbj.data.private.kis.rest import KisResponse, KisRestClient
from kbj.data.private.krx.client import KrxClient
from kbj.data.ratelimit import Priority
from kbj.data.spec import DataKey
from kbj.services.runtime.log import log_event
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import EtfRepo, FlowsRepo, MarketRepo, pg_repos

__all__ = [
    "KisGetter",
    "NotReady",
    "Outcome",
    "PartialFailure",
    "RepoSet",
    "calendar",
    "count_by",
    "day_of",
    "kis_client",
    "kis_get",
    "krx_client",
    "markets",
    "reason",
    "repos",
    "slot_ts",
    "split_venues",
]

SERVICE: Final = "scheduler"
log = logging.getLogger("kbj.services.collectors")
REASON_MAX: Final = 200
DETAIL_ERRORS_MAX: Final = 8


class NotReady(Exception):
    """아직 공표 전(빈 응답·당일 행 없음) — 재시도 규칙대로 다시."""


class PartialFailure(RuntimeError):
    """일부 대상만 받았다 — 받은 것은 썼고, 이 키는 미완(재시도가 나머지를 받는다)."""


class RepoSet(Protocol):
    """저장소 묶음 — `kbj.store.repos.Repos`(Pg)·`MemoryRepos`(시험) 둘 다 맞는 모양."""

    @property
    def market(self) -> MarketRepo: ...

    @property
    def flows(self) -> FlowsRepo: ...

    @property
    def etf(self) -> EtfRepo: ...


class KisGetter(Protocol):
    """`KisRestClient.get` 모양(시험은 가짜를 넣는다)."""

    def get(
        self,
        path: str,
        tr_id: str,
        params: dict[str, str],
        tr_cont: str = "",
        *,
        priority: Priority = Priority.P2,
        timeout: float | None = None,
    ) -> KisResponse: ...


def reason(e: BaseException) -> str:
    """예외 → 한 줄 사유(형 이름 + 첫 줄, 길이 제한). 키·토큰은 어댑터가 이미 가렸다."""
    msg = " ".join(str(e).split())
    text = f"{type(e).__name__}: {msg}" if msg else type(e).__name__
    return text if len(text) <= REASON_MAX else text[: REASON_MAX - 1] + "…"


# ── 자원 ────────────────────────────────────────────────────────────────────────────────


def repos(ctx: JobContext) -> RepoSet:
    got = ctx.resources.get("repos")
    if got is not None:
        return got
    return pg_repos(ctx.resource("connect"))


def markets(ctx: JobContext) -> MarketsConfig:
    got = ctx.resources.get("markets")
    if got is not None:
        return got
    return load_markets(settings=ctx.settings)


def calendar(ctx: JobContext) -> TradingCalendar:
    got = ctx.resources.get("kr")
    return got if got is not None else TradingCalendar.default()


@contextmanager
def kis_client(ctx: JobContext) -> Iterator[KisGetter]:
    """주입된 `kis` 가 있으면 그것(닫지 않는다), 없으면 서비스용 클라이언트를 열고 닫는다."""
    got = ctx.resources.get("kis")
    if got is not None:
        yield got
        return
    if ctx.settings is None:
        raise RuntimeError("설정이 주입되지 않았다(KBJ_KIS_*)")
    client = KisRestClient.for_service(ctx.settings, ctx.resource("redis"))
    try:
        yield client
    finally:
        client.close()


def krx_client(ctx: JobContext, *, backfill: bool = False) -> KrxClient:
    got = ctx.resources.get("krx_backfill" if backfill else "krx") or ctx.resources.get("krx")
    if got is not None:
        return got
    if ctx.settings is None:
        raise RuntimeError("설정이 주입되지 않았다(KBJ_KRX_API_KEY)")
    pri = Priority.P4 if backfill else Priority.P3
    return KrxClient.from_settings(
        ctx.settings, ctx.resource("redis"), backfill=backfill, priority=pri
    )


def kis_get(
    client: KisGetter,
    name: str,
    params: dict[str, str],
    *,
    what: str,
    priority: Priority,
    timeout: float | None,
) -> dict[str, Any]:
    """`PATHS[name]` TR 한 번. HTTP·rt_cd 가 정상이 아니면 `KisRejected`."""
    spec = PATHS[name]
    resp = client.get(spec.path, spec.tr_id, params, priority=priority, timeout=timeout)
    require_ok(what, resp.status, resp.body)
    return resp.body


# ── 키·시각 ──────────────────────────────────────────────────────────────────────────────


def split_venues(
    keys: Sequence[DataKey], venues: Sequence[str]
) -> tuple[list[DataKey], list[DataKey]]:
    """(받을 키, 설정에서 끈 거래소의 키). 거래소를 나누지 않는 키('')는 받는다."""
    on = set(venues)
    active = [k for k in keys if not k.venue or k.venue in on]
    off = [k for k in keys if k.venue and k.venue not in on]
    return active, off


def slot_ts(as_of: str) -> datetime:
    """slot10m as_of(`YYYY-MM-DDTHH:MM`, KST) → 슬롯 시작 시각(aware)."""
    ts = datetime.strptime(as_of, "%Y-%m-%dT%H:%M").replace(tzinfo=KST)
    if ts.minute % 10:
        raise ValueError(f"10분 슬롯 시작이 아니다: {as_of!r}")
    return ts


def day_of(as_of: str) -> date:
    return date.fromisoformat(as_of[:10])


# ── 결과 모으기 ──────────────────────────────────────────────────────────────────────────


@dataclass
class Outcome:
    """데이터 키마다 받은 결과를 모아 `JobResult` 하나로."""

    job: str
    keys: tuple[DataKey, ...]
    collected: list[DataKey] = field(default_factory=list[DataKey])
    errors: dict[str, str] = field(default_factory=dict[str, str])
    not_ready: dict[str, str] = field(default_factory=dict[str, str])
    rows: int = 0
    detail: dict[str, Any] = field(default_factory=dict[str, Any])

    def run(self, key: DataKey, fn: Callable[[], int]) -> None:
        """키 하나를 받는다. 예외는 그 키의 실패로 남기고(사유 기록) 다음 키로 간다."""
        try:
            n = fn()
        except NotReady as e:
            self.not_ready[key.label()] = reason(e)
            return
        except Exception as e:  # 격리 — 사유를 남기고 이 키만 미완(삼키지 않는다)
            why = reason(e)
            self.errors[key.label()] = why
            log_event(log, logging.WARNING, SERVICE, "collect_key_failed", job=self.job,
                      key=key.label(), error=why)  # fmt: skip
            return
        self.collected.append(key)
        self.rows += n

    def skip_off(self, off: Sequence[DataKey]) -> None:
        """설정에서 끈 거래소의 키 — 부르지 않고 0행으로 끝냈다고 적는다."""
        if off:
            self.collected.extend(off)
            self.detail["venues_off"] = sorted({k.venue for k in off})

    def result(self) -> JobResult:
        detail: dict[str, Any] = {**self.detail, "rows": self.rows}
        if self.errors:
            items = sorted(self.errors.items())[:DETAIL_ERRORS_MAX]
            detail["errors"] = dict(items)
        if self.not_ready:
            detail["not_ready"] = dict(sorted(self.not_ready.items())[:DETAIL_ERRORS_MAX])
        done = tuple(self.collected)
        if len(done) == len(self.keys) and not self.errors and not self.not_ready:
            return JobResult("ok", collected=done, rows=self.rows, detail=detail)
        if self.errors:
            first = next(iter(sorted(self.errors.items())))
            detail["reason"] = f"키 {len(self.errors)}개 실패 — {first[0]}: {first[1]}"
            return JobResult("failed", collected=done, rows=self.rows, detail=detail)
        first = next(iter(sorted(self.not_ready.items())))
        detail["reason"] = f"아직 공표 전 {len(self.not_ready)}개 — {first[0]}: {first[1]}"
        return JobResult("not_ready", collected=done, rows=self.rows, detail=detail)


def count_by(values: Mapping[str, int]) -> dict[str, int]:
    """detail 용 — 0 인 항목은 뺀다."""
    return {k: v for k, v in values.items() if v}

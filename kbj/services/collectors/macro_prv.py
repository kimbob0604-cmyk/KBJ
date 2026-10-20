"""로그인 매크로 수집 —
`macro.morning_prv`(06:20)·`macro.monthly_prv`(09:15)·`macro.evening_prv`(17:05)
→ `prv_macro`.

근거: docs/p5_design.md §1.2·§3.2·§4.1, D-P5-6·7. 원본 기능: SD `_MACRO_TICKERS` 12종(Yahoo — 단위
그대로)·FRED 저작권 시리즈·ECOS 타기관 작성 표(802Y001·731Y001·901Y056·901Y009)·bok
`fetch-consensus.js`(ForexFactory 예상치 — 사용자 결정 2026-10-13 Q3: 쓴다, 로그인 화면·아침
브리핑만).

- 로그인 작업이라 `pub_*` 에 쓰지 않는다(ADR 0002). 공개 수집 도우미(`kbj.services.collectors.macro`
  —
  `run_keys`·`write_series`·`ready_series` …)를 가져다 쓴다(로그인 → 공개 방향만, 반대는 금지).
- Yahoo 일봉 종가 → 시리즈 값. 정규장이 열린 동안의 마지막 봉은 `estimated`(종가 아님 — 어댑터
  규칙).
- ForexFactory: `config/macro.yaml ff.enabled` 가 false 면 부르지 않고 키 `skipped`. 켜져 있으면
  이번 주
  일정을 `prv_macro.calendar`(source `ff`, 예상치 칸)에 쓴다. 같은 (나라·종류·날짜) 공개 행에
  덧붙이는
  일은 API 리더가 엔진 `merge` 로 한다.
- 자원: `macro_config`·`repos`(없으면 `connect` 로 `pg_repos`), `yahoo`·`fred_restricted`·`ff`·
  `ecos_restricted`(없으면 `from_settings`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Final

from kbj.config.macro import MacroConfig
from kbj.core.p5_rows import CalendarEvent, MacroSeriesRow, event_id
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.data.private.ecos_restricted import EcosRestrictedClient
from kbj.data.private.forexfactory import ROW_SOURCE as FF_SOURCE
from kbj.data.private.forexfactory import FfClient
from kbj.data.private.fred_restricted import FredRestrictedClient
from kbj.data.private.yahoo.client import YahooClient, YahooError
from kbj.data.spec import DataKey
from kbj.services.collectors.macro import (
    LOOKBACK_DAYS,
    NotConfigured,
    as_of_period,
    client,
    config,
    ecos_rows,
    fred_daily_want,
    period_of,
    ready_series,
    require_periods,
    run_keys,
    write_series,
)
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import Repos, pg_repos

__all__ = ["evening", "monthly", "morning"]

YAHOO_RANGE: Final = "3mo"  # 일별 45일 창을 덮는 가장 짧은 범위
FF_UNITS: Final = {"%": "%", "K": "천", "M": "백만", "B": "십억", "T": "조"}


def _repos(ctx: JobContext) -> Repos:
    return ctx.resources.get("repos") or pg_repos(ctx.resource("connect"))


def _settings(ctx: JobContext) -> Any:
    if ctx.settings is None:
        raise RuntimeError("설정이 주입되지 않았다(출처 키)")
    return ctx.settings


def _day(key: DataKey) -> date:
    return date.fromisoformat(key.as_of[:10])


def _yahoo(ctx: JobContext, cfg: MacroConfig, repos: Repos, key: DataKey) -> int:
    series = ready_series(cfg, "YAHOO:macro", tier="private")
    y = client(
        ctx, "yahoo", lambda: YahooClient.from_settings(_settings(ctx), ctx.resource("redis"))
    )
    day = _day(key)
    since = day - timedelta(days=LOOKBACK_DAYS["D"])
    got, failed = y.charts([s.item or "" for s in series], YAHOO_RANGE)
    rows: list[MacroSeriesRow] = []
    for s in series:
        for b in got.get(s.item or "", []):
            if since < b.date <= day:
                v = None if b.close is None else Decimal(repr(b.close))
                q = b.quality if v is not None else Quality.INVALID
                rows.append(
                    MacroSeriesRow(s.id, period_of(s.freq, b.date), v, s.unit, "YAHOO", ctx.now, q)
                )
    n = write_series(repos.macro_prv, series, rows, job=ctx.job, now=ctx.now)
    if failed:
        sym, why = next(iter(failed.items()))
        raise YahooError(f"chart:{sym}", None, f"심볼 {len(failed)}개 실패({sym} …) — {why}")
    require_periods(series, rows, day.isoformat())
    return n


def _fred_restricted(ctx: JobContext, cfg: MacroConfig, repos: Repos, key: DataKey) -> int:
    series = ready_series(cfg, "FRED:restricted_series", tier="private")
    ids = [s.item for s in series if s.item]
    fr = client(
        ctx,
        "fred_restricted",
        lambda: FredRestrictedClient.from_settings(
            _settings(ctx), ctx.resource("redis"), restricted=ids
        ),
    )
    day = _day(key)
    rows: list[MacroSeriesRow] = []
    for s in series:
        for o in fr.observations(s.item or "", day - timedelta(days=LOOKBACK_DAYS[s.freq]), day):
            q = o.quality
            rows.append(
                MacroSeriesRow(s.id, period_of(s.freq, o.date), o.value, s.unit, "FRED", ctx.now, q)
            )
    n = write_series(repos.macro_prv, series, rows, job=ctx.job, now=ctx.now)
    daily = [s for s in series if s.freq == "D"]
    require_periods(daily, rows, fred_daily_want(ctx, day), count_blank=True)  # FRED 하루 늦게
    return n


def _ff(ctx: JobContext, cfg: MacroConfig, repos: Repos, key: DataKey) -> int:
    if not cfg.ff.enabled:
        raise NotConfigured("ForexFactory 꺼짐(config/macro.yaml ff.enabled: false)")
    ff = client(ctx, "ff", lambda: FfClient.from_settings(_settings(ctx), ctx.resource("redis")))
    events = [
        CalendarEvent(
            event_id(e.country, e.kind, e.date, e.code),
            e.kind,
            e.date,
            e.time_kst,
            e.title,
            e.country,
            e.importance,
            FF_SOURCE,
            Quality.OK,
            forecast=e.forecast,
            unit=FF_UNITS.get(e.unit or "") if e.unit else None,
        )
        for e in ff.this_week()
    ]
    if not events:
        return 0
    return repos.macro_prv.upsert_calendar(events, loaded_by=ctx.job, received_at=ctx.now)


def _ecos(ctx: JobContext, cfg: MacroConfig, repos: Repos, key: DataKey) -> int:
    series = ready_series(cfg, key.dataset_id, tier="private")
    ecos = client(
        ctx,
        "ecos_restricted",
        lambda: EcosRestrictedClient.from_settings(_settings(ctx), ctx.resource("redis")),
    )
    rows = ecos_rows(ecos.search, series, ctx.now.astimezone(KST).date(), ctx.now)
    n = write_series(repos.macro_prv, series, rows, job=ctx.job, now=ctx.now)
    require_periods(series, rows, as_of_period(key.as_of))
    return n


def _handlers(ctx: JobContext) -> dict[str, Callable[[DataKey], int]]:
    cfg = config(ctx)
    repos = _repos(ctx)
    out: dict[str, Callable[[DataKey], int]] = {
        "YAHOO:macro": lambda k: _yahoo(ctx, cfg, repos, k),
        "FRED:restricted_series": lambda k: _fred_restricted(ctx, cfg, repos, k),
        "FF:calendar": lambda k: _ff(ctx, cfg, repos, k),
    }
    for k in ctx.keys:
        if k.source == "ECOS":
            out[k.dataset_id] = lambda key: _ecos(ctx, cfg, repos, key)
    return out


def morning(ctx: JobContext) -> JobResult:
    return run_keys(ctx, _handlers(ctx))


def monthly(ctx: JobContext) -> JobResult:
    return run_keys(ctx, _handlers(ctx))


def evening(ctx: JobContext) -> JobResult:
    return run_keys(ctx, _handlers(ctx))

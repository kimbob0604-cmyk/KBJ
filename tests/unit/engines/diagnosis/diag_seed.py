"""진단 시험 씨앗 — p7_world(합성, 고정 시드)의 진단 입력을 메모리 저장소 행으로 옮긴다.

공개: ECOS 국고 3년·회사채 AA-(= 국고 + 신용스프레드), 금투협 신용공여(코스피 60%·코스닥 40%로
나눔)·
예탁금, 실적 공시 예고(합성 회사 코드). 로그인: GEX 엔진 산출·투자자별(world.fill_gex).
값은 전부 합성(실측 아님). 공표 시각은 world 의 published_at 그대로 — 시점 입력 시험이 이것을 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

from kbj.core.fin_rows import ScheduleRow
from kbj.core.p5_rows import MacroSeriesRow, MarketStatRow
from kbj.core.quality import Quality
from kbj.services.engine.diag_pub import AA3Y, KTB3Y
from kbj.services.public_export.diag_json import (
    Q_DIAG_EVENTS,
    Q_DIAG_GAUGE,
    Q_DIAG_HISTORY,
    Q_DIAG_LAST,
)
from kbj.services.public_export.pub_read import Query, Row
from kbj.store.repos.diag import MemoryDiagPublicRepo
from tests.fixtures.synthetic.p7_world import P7World, build_world

__all__ = ["CORP", "DiagReader", "seed_public", "world"]

CORP: Final = "99{:06d}"  # 합성 회사 코드(8자리)


def world() -> P7World:
    return build_world()


def _macro(sid: str, d: date, v: Decimal | None, at: datetime) -> MacroSeriesRow:
    return MacroSeriesRow(
        series_id=sid, period=d.isoformat(), value=v, unit="%", source="ECOS", as_of=at,
        quality=Quality.OK, released_at=at,
    )  # fmt: skip


def _stat(stat: str, item: str, d: date, v: Decimal, at: datetime) -> MarketStatRow:
    return MarketStatRow(
        stat=stat, date=d, item=item, value=v, unit="원", source="DATAGO", as_of=at,  # type: ignore[arg-type]
        quality=Quality.OK,
    )  # fmt: skip


def seed_public(repos: Any, w: P7World, *, received_at: datetime) -> None:
    """repos = MemoryRepos 또는 MemoryPublicRepos(macro·market_stats·filings)."""
    ktb = {p.day: p for p in w.series["ktb3y"]}
    macro: list[MacroSeriesRow] = []
    for p in w.series["credit_spread"]:
        k = ktb[p.day]
        macro.append(_macro(KTB3Y, p.day, k.value, k.published_at))
        aa = None if p.value is None or k.value is None else k.value + p.value
        macro.append(_macro(AA3Y, p.day, aa, p.published_at))
    repos.macro.upsert_series(macro, loaded_by="seed", received_at=received_at)
    stats: list[MarketStatRow] = []
    for p in w.series["credit_balance"]:
        if p.value is None:
            continue
        kospi = (p.value * Decimal("0.6")).quantize(Decimal(1))
        stats.append(_stat("credit_balance", "loan_kospi", p.day, kospi, p.published_at))
        stats.append(_stat("credit_balance", "loan_kosdaq", p.day, p.value - kospi, p.published_at))
    for p in w.series["deposits"]:
        if p.value is not None:
            stats.append(
                _stat("market_capital", "investor_deposit", p.day, p.value, p.published_at)
            )
    repos.market_stats.upsert(stats, loaded_by="seed", received_at=received_at)
    sched: list[ScheduleRow] = []
    i = 0
    for d, n in w.earnings_counts.items():
        for _ in range(n):
            i += 1
            sched.append(ScheduleRow(
                corp_code=CORP.format(i), period_end=date(2026, 9, 30), kind="pre_announce",
                planned_date=d, fiscal_year=2026, fiscal_q=3,
            ))  # fmt: skip
    codes = {r.corp_code for r in sched}
    repos.filings.replace_schedule(codes, sched, loaded_by="seed", now=received_at)


class DiagReader:
    """pub_diag Query 이름에 같은 뜻으로 답하는 메모리 읽기(시험 전용)."""

    def __init__(self, repo: MemoryDiagPublicRepo) -> None:
        self.repo = repo

    def __call__(self, q: Query, p: Mapping[str, object]) -> list[Row]:
        r = self.repo
        if q is Q_DIAG_LAST:
            upto = p["upto"]
            assert isinstance(upto, date)
            return [{"trade_date": r.last_indicator_date(upto)}]
        start, end = p["start"], p["end"]
        assert isinstance(start, date) and isinstance(end, date)
        if q is Q_DIAG_HISTORY:
            rows = sorted(
                (x for x in r.indicator.where(lambda x: start <= x.trade_date <= end)),
                key=lambda x: (x.key, x.trade_date),
            )
            return [
                {"trade_date": x.trade_date, "key": x.key, "value": x.value, "unit": x.unit,
                 "state": x.state, "pct_rank": x.pct_rank, "flags": list(x.flags),
                 "quality": x.quality.value, "reasons": list(x.reasons), "as_of": x.as_of}
                for x in rows
            ]  # fmt: skip
        if q is Q_DIAG_GAUGE:
            return [
                {"trade_date": g.trade_date, "label": g.label, "score": g.score,
                 "parts": dict(g.parts), "quality": g.quality.value, "reasons": list(g.reasons),
                 "as_of": g.as_of}
                for g in r.gauge_history(start, end)
            ]  # fmt: skip
        if q is Q_DIAG_EVENTS:
            return [
                {"date": e.date, "kind": e.kind, "title": e.title, "detail": dict(e.detail),
                 "quality": e.quality.value, "as_of": e.as_of}
                for e in r.events_between(start, end)
            ]  # fmt: skip
        raise AssertionError(f"모르는 Query: {q.name}")

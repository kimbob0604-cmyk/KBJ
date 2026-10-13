"""매크로·경제 캘린더 reader(docs/p5_design.md §5 — MAC 웨이브 2 본문).

`pub_macro`·`prv_macro` 저장소 → 엔진(`kbj.engines.macro` — 파생·캘린더 합치기) → 모델. 로그인 API
라
공개·로그인 시리즈·예상치를 함께 낸다(공개판은 정적 JSON — 공개 내보내기 `macro_json`).

- 탭·시리즈 목록은 `config/macro.yaml` 한 곳(처음 한 번 읽어 둔다 — `_config`). 항목 코드가 실측
  전인
  시리즈는 `ready=False`·`na_reason='확인 전(#43)'` — 값을 지어내지 않는다.
- 수집된 행이 하나도 없으면 `NoData`(작업 이름·예정 시각 — 화면 '아직 없음'). 수집 작업은 실측
  전이라
  꺼져 있다(#43~#46·#48).
- 파생(metrics §16)은 엔진이 입력 행으로 계산한다(공개 파생에 로그인 입력이면 엔진이 거부).
- 캘린더: 공개 행에 발표 결과(같은 시리즈의 공표 값 — 읽을 때 다시 붙인다)와 예상치(로그인 — FF)를
  덧붙인다(엔진 `merge`).
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Final

from kbj.config.macro import DerivedCfg, MacroConfig, SeriesCfg, load_macro
from kbj.core.p5_rows import MacroSeriesRow
from kbj.core.quality import Quality
from kbj.engines.macro.calendar import attach_actuals, merge
from kbj.engines.macro.derive import DerivedSeries, derive, latest_change
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.macro import (
    CalendarRow,
    EconCalendar,
    MacroPoint,
    MacroSeries,
    MacroSeriesInfo,
    MacroSeriesSet,
    MacroTab,
    MacroTabs,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, label, worst

__all__ = ["NO_DATA", "macro_calendar", "macro_series", "macro_series_one", "macro_tabs"]

NO_DATA: Final = (
    "아직 없음 — macro.morning 06:20 · macro.monthly 09:10 · macro.evening 17:00 (확인 전 #43~#46)"
)
NO_CALENDAR: Final = "아직 없음 — macro.morning 06:20(공식 일정·FRED 발표일, 확인 전 #45)"
UNKNOWN_TAB: Final = "아직 없음 — 모르는 탭(macro.morning 06:20 · config/macro.yaml tabs)"
UNKNOWN_SERIES: Final = "아직 없음 — 모르는 시리즈(macro.morning 06:20 · config/macro.yaml series)"
# 기본 기간(§7 공개 내보내기와 같은 창): 일별 2년, 그 밖 10년
DEFAULT_SPAN_DAYS: Final = {"D": 2 * 366, "W": 2 * 366, "M": 10 * 366, "Q": 10 * 366, "A": 10 * 366}
_JOB_OF: Final = {
    "public": "macro.morning·monthly·evening",
    "private": "macro.morning_prv·monthly_prv",
}


@functools.cache
def _config() -> MacroConfig:
    """`config/macro.yaml`(한 번만 읽는다 — 바뀌면 API 재시작)."""
    return load_macro()


def _source_label(s: SeriesCfg) -> str:
    return s.dataset if s.source == "ECOS" else s.source


def _info(s: SeriesCfg) -> MacroSeriesInfo:
    return MacroSeriesInfo(
        series_id=s.id,
        title=s.title,
        unit=s.unit,
        freq=s.freq,
        tier=s.tier,
        source=_source_label(s),
        dataset=s.dataset,
        ready=s.ready,
        probe=s.probe,
    )


def _derived_info(d: DerivedCfg, cfg: MacroConfig) -> MacroSeriesInfo:
    by = {s.id: s for s in cfg.series}
    ins = [by[i] for i in d.inputs]
    freq = ins[0].freq if d.kind == "spread" else "M"
    return MacroSeriesInfo(
        series_id=d.id,
        title=d.title,
        unit=d.unit,
        freq=freq,
        tier=d.tier,
        source="+".join(dict.fromkeys(_source_label(s) for s in ins)),
        dataset="DERIVED",
        ready=all(s.ready for s in ins),
        probe=next((s.probe for s in ins if s.probe), None),
    )


def _has_data(ctx: ReadContext) -> bool:
    return bool(ctx.repos.macro.meta() or ctx.repos.macro_prv.meta())


def macro_tabs(ctx: ReadContext) -> Envelope[MacroTabs]:
    if not _has_data(ctx):
        raise NoData(NO_DATA)
    cfg = _config()
    tabs = [
        MacroTab(
            id=t.id,
            title=t.title,
            public=t.public,
            series=[_info(s) for s in cfg.series if s.tab == t.id]
            + [_derived_info(d, cfg) for d in cfg.derived if d.tab == t.id],
        )
        for t in cfg.tabs
    ]
    metas = [*ctx.repos.macro.meta(), *ctx.repos.macro_prv.meta()]
    return envelope(
        MacroTabs(tabs=tabs),
        source=label(m.source for m in metas),
        as_of=ctx.now(),
        quality=Quality.OK,
        notes=["시장 탭은 로그인 등급(저작권·작성기관 제한 시리즈)"],
        generated_at=ctx.now(),
    )


def _start_period(freq: str, start: date | None, today: date) -> str:
    d = start or (today - timedelta(days=DEFAULT_SPAN_DAYS[freq]))
    if freq in ("D", "W"):
        return d.isoformat()
    if freq == "M":
        return f"{d:%Y-%m}"
    if freq == "Q":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    return f"{d.year}"


def _rows(
    ctx: ReadContext, series: Sequence[SeriesCfg], start: date | None, end: date | None = None
) -> dict[str, list[MacroSeriesRow]]:
    today = ctx.today()
    out: dict[str, list[MacroSeriesRow]] = {}
    for s in series:
        repo = ctx.repos.macro if s.tier == "public" else ctx.repos.macro_prv
        a = _start_period(s.freq, start, today)
        b = None if end is None else _start_period(s.freq, end, today)
        out[s.id] = repo.series([s.id], a, b).get(s.id, [])
    return out


def _series_model(s: SeriesCfg, rows: Sequence[MacroSeriesRow]) -> MacroSeries:
    pts = [
        MacroPoint(
            period=r.period,
            value=None if r.value is None else float(r.value),
            released_at=r.released_at,
        )
        for r in rows
    ]
    lc = latest_change([(r.period, r.value) for r in rows])
    last = next((r for r in reversed(rows) if lc is not None and r.period == lc.period), None)
    if not s.ready and not rows:
        na: str | None = f"확인 전({s.probe})"
    elif not rows:
        na = f"아직 없음 — {_JOB_OF[s.tier]}"
    else:
        na = None
    return MacroSeries(
        series_id=s.id,
        title=s.title,
        unit=s.unit,
        freq=s.freq,
        tier=s.tier,
        source=_source_label(s),
        as_of=max((r.as_of for r in rows), default=None),
        quality=last.quality if last is not None else (Quality.INVALID if rows else None),
        latest=None
        if last is None
        else MacroPoint(
            period=last.period, value=lc.value if lc else None, released_at=last.released_at
        ),
        change=lc.change if lc else None,
        change_pct=lc.change_pct if lc else None,
        points=pts,
        na_reason=na,
    )


def _derived_model(
    d: DerivedCfg, cfg: MacroConfig, ds: DerivedSeries, as_of: datetime | None
) -> MacroSeries:
    info = _derived_info(d, cfg)
    pts = [MacroPoint(period=p.period, value=p.value, released_at=None) for p in ds.points]
    lc = latest_change([(p.period, p.value) for p in ds.points])
    lastp = next((p for p in reversed(ds.points) if lc is not None and p.period == lc.period), None)
    na = ds.na_reason or (None if lc is not None else "입력 없음")
    if not info.ready and not ds.points:
        na = f"확인 전({info.probe})"
    return MacroSeries(
        series_id=d.id,
        title=d.title,
        unit=d.unit,
        freq=info.freq,
        tier=d.tier,
        source=info.source,
        as_of=as_of if pts else None,
        quality=lastp.quality if lastp else None,
        latest=None
        if lc is None
        else MacroPoint(period=lc.period, value=lc.value, released_at=None),
        change=lc.change if lc else None,
        change_pct=lc.change_pct if lc else None,
        points=pts,
        derived=True,
        na_reason=na,
    )


def _derived(
    ctx: ReadContext, d: DerivedCfg, cfg: MacroConfig, start: date | None, end: date | None
) -> MacroSeries:
    by = {s.id: s for s in cfg.series}
    ins = [by[i] for i in d.inputs]
    # 비율(yoy·real_rate)은 기준 기간이 앞에 있어야 하니 13개월 더 읽는다
    span = timedelta(days=DEFAULT_SPAN_DAYS["M"])
    base = start or (ctx.today() - span if d.kind != "spread" else None)
    pad = base - timedelta(days=400) if base is not None and d.kind != "spread" else start
    rows = _rows(ctx, ins, pad, end)
    ds = derive(d, rows, {s.id: s.tier for s in cfg.series})
    if base is not None:
        cut = _start_period(ins[0].freq if d.kind == "spread" else "M", base, ctx.today())
        ds = DerivedSeries(
            ds.series_id,
            ds.tier,
            ds.unit,
            ds.sources,
            tuple(p for p in ds.points if p.period >= cut),
            ds.na_reason,
        )
    as_of = max((r.as_of for rs in rows.values() for r in rs), default=None)
    return _derived_model(d, cfg, ds, as_of)


def _envelope_of[T](ctx: ReadContext, data: T, series: Sequence[MacroSeries]) -> Envelope[T]:
    stamps = [s.as_of for s in series if s.as_of is not None]
    if not stamps:
        raise NoData(NO_DATA)
    got = [s for s in series if s.as_of is not None]
    return envelope(
        data,
        source=label(s.source for s in got),
        as_of=max(stamps),
        quality=worst(s.quality for s in got),
        notes=[f"{s.title}: {s.na_reason}" for s in series if s.na_reason][:12],
        generated_at=ctx.now(),
    )


def macro_series(ctx: ReadContext, *, tab: str, start: date | None) -> Envelope[MacroSeriesSet]:
    cfg = _config()
    if tab not in cfg.tab_ids():
        raise NoData(UNKNOWN_TAB)
    series = [s for s in cfg.series if s.tab == tab]
    rows = _rows(ctx, series, start)
    out = [_series_model(s, rows[s.id]) for s in series]
    out += [_derived(ctx, d, cfg, start, None) for d in cfg.derived if d.tab == tab]
    return _envelope_of(ctx, MacroSeriesSet(tab=tab, series=out), out)


def macro_series_one(
    ctx: ReadContext, *, series_id: str, start: date | None, end: date | None
) -> Envelope[MacroSeries]:
    cfg = _config()
    s = next((x for x in cfg.series if x.id == series_id), None)
    if s is not None:
        m = _series_model(s, _rows(ctx, [s], start, end)[s.id])
    else:
        d = next((x for x in cfg.derived if x.id == series_id), None)
        if d is None:
            raise NoData(UNKNOWN_SERIES)
        m = _derived(ctx, d, cfg, start, end)
    return _envelope_of(ctx, m, [m])


def macro_calendar(
    ctx: ReadContext, *, start: date | None, end: date | None
) -> Envelope[EconCalendar]:
    cfg = _config()
    today = ctx.today()
    a = start or today - timedelta(days=cfg.calendar.window_days.past)
    b = end or today + timedelta(days=cfg.calendar.window_days.future)
    pub = ctx.repos.macro.calendar(a, b)
    prv = ctx.repos.macro_prv.calendar(a, b)
    if not pub and not prv:
        raise NoData(NO_CALENDAR)
    ids = sorted({e.actual_series for e in pub if e.actual_series})
    pub = attach_actuals(pub, ctx.repos.macro.series(ids) if ids else {}, ctx.now())
    rows = [
        CalendarRow(
            event_id=e.event_id,
            date=e.date,
            time_kst=None if e.time_kst is None else e.time_kst.strftime("%H:%M"),
            kind=e.kind,
            title=e.title,
            country=e.country,
            importance=e.importance,
            source=e.source if fsrc is None or fsrc == e.source else f"{e.source}+{fsrc}",
            quality=e.quality,
            actual=None if e.actual is None else float(e.actual),
            actual_series=e.actual_series,
            unit=e.unit,
            forecast=None if e.forecast is None else float(e.forecast),
        )
        for e, fsrc in merge(pub, prv, include_private=True)
    ]
    sources = sorted({e.source for e in [*pub, *prv]})
    return envelope(
        EconCalendar(start=a, end=b, rows=rows),
        source=label(s.upper() for s in sources),
        as_of=ctx.now(),
        quality=worst(r.quality for r in rows) or Quality.OK,
        notes=["예상치(ForexFactory)는 로그인 등급"] if prv else [],
        generated_at=ctx.now(),
    )

"""진단 리더 — `/api/diag/*`(docs/p7_design.md §5, metrics §D0~§D9 — 로그인). P7 묶음 DIAG 본문.

`ctx.repos.diag`(prv_diag — 시장 진단·공포탐욕·판정)·`ctx.repos.diag_pub`(pub_diag — 공공지표·
게이지·이벤트)를 읽어 `models.diag` 로(DB 만 — 계약 ⑩). 계산은 하지 않는다 — 상태 점수(§D0)·판정
근거 셋(§D8)만 저장된 구간에서 정본 함수로 다시 읽는다(`kbj.engines.diagnosis`).

- 날짜: `date` 를 주면 그날 이하, 아니면 오늘 이하에서 가장 최근. 공개 행(08:40)과 로그인
  행(16:00)의
  날짜가 다를 수 있다 — 각자 최근 날짜를 싣고 다르면 notes 에 적는다. 둘 다 없으면 NoData.
- 지표 카드 순서는 `config/diag.yaml indicators` 순서(공개 다섯 → 로그인 여섯), 없는 키는 싣지
  않는다.
- 공포탐욕 대체 표시(`substitutes`)는 행 사유 중 '대체:' 로 시작하는 것.
- 이벤트 D-n: 오늘 기준 달력일(지난 이벤트는 None). 이벤트 주의 = 판정 꼬리표가 하나라도 있으면.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from functools import cache
from typing import Final, Literal, cast

from kbj.config.diag import DiagConfig, load_diag
from kbj.core.p7_rows import DiagIndicatorRow, EventMarker, FearGreedRow, GaugeRow, VerdictRow
from kbj.core.quality import Quality
from kbj.engines.diagnosis.bands import state_score, worst
from kbj.engines.diagnosis.verdict import top_reasons
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.diag import (
    DiagEvent,
    DiagEvents,
    DiagFearGreed,
    DiagGauge,
    DiagHistory,
    DiagHistoryPoint,
    DiagIndicator,
    DiagSummary,
    DiagVerdict,
    EventKindT,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope

__all__ = [
    "FEAR_GREED_KEY",
    "NO_DIAG",
    "NO_DIAG_EVENTS",
    "diag_events",
    "diag_history",
    "diag_summary",
]

NO_DIAG: Final = "아직 없음 — diag.public 08:40 · diag.daily 16:00"
NO_DIAG_EVENTS: Final = "아직 없음 — diag.public 08:40"
FEAR_GREED_KEY: Final = "fear_greed"
SUBSTITUTE_PREFIX: Final = "대체:"
Score = Literal[-1, 0, 1]
Found = DiagIndicatorRow | GaugeRow | VerdictRow | FearGreedRow


@cache
def _cfg() -> DiagConfig:
    return load_diag()


def _f(v: object) -> float | None:
    return None if v is None else float(cast("float", v))


def _indicator(r: DiagIndicatorRow, cfg: DiagConfig) -> DiagIndicator | None:
    spec = cfg.indicators.get(r.key)
    if spec is None:
        return None
    score = cast("Score | None", state_score(r.state, spec.risk_on))
    return DiagIndicator(
        key=r.key, tier=spec.tier, value=_f(r.value), unit=r.unit, state=r.state,
        pct_rank=_f(r.pct_rank), score=score, flags=list(r.flags), quality=r.quality,
        reasons=list(r.reasons),
    )  # fmt: skip


def _fear(r: FearGreedRow) -> DiagFearGreed:
    return DiagFearGreed(
        score=_f(r.score), band=r.band, components={k: _f(v) for k, v in r.components.items()},
        substitutes=[x for x in r.reasons if x.startswith(SUBSTITUTE_PREFIX)],
        quality=r.quality, reasons=list(r.reasons),
    )  # fmt: skip


def _verdict(r: VerdictRow, cfg: DiagConfig) -> DiagVerdict:
    return DiagVerdict(
        label=r.label, score=r.score, parts=dict(r.parts),
        top=top_reasons(r.parts, cfg.verdict.top_reasons or 3),
        event_flags=[cast("EventKindT", f) for f in r.event_flags],
        quality=r.quality, reasons=list(r.reasons),
    )  # fmt: skip


def _gauge(r: GaugeRow) -> DiagGauge:
    return DiagGauge(
        label=r.label,
        score=r.score,
        parts=dict(r.parts),
        quality=r.quality,
        reasons=list(r.reasons),
    )


def _event(e: EventMarker, today: date) -> DiagEvent:
    left = (e.date - today).days
    return DiagEvent(
        date=e.date, kind=e.kind, title=e.title, days_left=left if left >= 0 else None,
        quality=e.quality,
    )  # fmt: skip


def _sources(srcs: Iterable[str]) -> str:
    return "+".join(dict.fromkeys(s for s in srcs if s))


def _ordered(rows: Sequence[DiagIndicatorRow], cfg: DiagConfig) -> list[DiagIndicatorRow]:
    order = {k: i for i, k in enumerate(cfg.indicators)}
    return sorted((r for r in rows if r.key in order), key=lambda r: order[r.key])


def diag_summary(ctx: ReadContext, *, day: date | None) -> Envelope[DiagSummary]:
    cfg = _cfg()
    today = ctx.today()
    upto = day or today
    pub_d = ctx.repos.diag_pub.last_indicator_date(upto)
    prv_d = ctx.repos.diag.last_verdict_date(upto)
    if pub_d is None and prv_d is None:
        raise NoData(NO_DIAG)
    notes: list[str] = []
    pub_rows = ctx.repos.diag_pub.indicators_on(pub_d) if pub_d else []
    gauge = ctx.repos.diag_pub.gauge_on(pub_d) if pub_d else None
    v_row = ctx.repos.diag.verdict_on(prv_d) if prv_d else None
    prv_rows = ctx.repos.diag.indicators_on(prv_d) if prv_d else []
    fg = ctx.repos.diag.fear_greed_history(prv_d, prv_d) if prv_d else []
    if pub_d is None:
        notes.append("공공지표 진단 없음 — diag.public 08:40")
    if prv_d is None:
        notes.append("시장 진단·판정 없음 — diag.daily 16:00")
    if pub_d and prv_d and pub_d != prv_d:
        notes.append(f"공공지표 {pub_d.isoformat()} · 시장 진단 {prv_d.isoformat()} 기준")
    trade_date = max(d for d in (pub_d, prv_d) if d is not None)
    rows = _ordered([*pub_rows, *prv_rows], cfg)
    cards = [c for r in rows if (c := _indicator(r, cfg)) is not None]
    horizon = today + timedelta(days=cfg.events.horizon_days)
    events = [_event(e, today) for e in ctx.repos.diag_pub.events_between(today, horizon)]
    data = DiagSummary(
        trade_date=trade_date,
        verdict=_verdict(v_row, cfg) if v_row is not None else None,
        fear_greed=_fear(fg[-1]) if fg else None,
        gauge=_gauge(gauge) if gauge is not None else None,
        indicators=cards,
        events=events,
        caution=bool(v_row.event_flags) if v_row is not None else False,
    )
    found: list[Found] = [*rows]
    found += [x for x in (gauge, v_row, fg[-1] if fg else None) if x is not None]
    # 봉투 품질은 값이 있는 것 중 가장 나쁜 것 — 값 없는 카드는 자기 품질·사유로 보인다
    valued = [x for x in found if not (isinstance(x, DiagIndicatorRow) and x.value is None)]
    missing = [r.key for r in rows if r.value is None]
    if missing:
        notes.append(f"값 없음: {', '.join(missing)}")
    return envelope(
        data,
        source=_sources(x.source for x in found),
        as_of=max((x.as_of for x in found), default=ctx.now()),
        quality=worst(x.quality for x in valued) or Quality.INVALID,
        notes=notes,
        generated_at=ctx.now(),
    )


def _history_points(
    ctx: ReadContext, cfg: DiagConfig, key: str, start: date, end: date, days: int
) -> tuple[str, str, list[float], list[DiagHistoryPoint], list[Found]]:
    if key == FEAR_GREED_KEY:
        fg = ctx.repos.diag.fear_greed_history(start, end)[-days:]
        pts = [
            DiagHistoryPoint(trade_date=r.trade_date, value=_f(r.score), state=None,
                             pct_rank=None, quality=r.quality)
            for r in fg
        ]  # fmt: skip
        return "login", "점", [float(b) for b in cfg.fear_greed.bands], pts, list(fg)
    spec = cfg.indicators.get(key)
    if spec is None:
        raise NoData(f"모르는 진단 지표: {key}")
    repo = ctx.repos.diag_pub if spec.tier == "public" else ctx.repos.diag
    rows = repo.indicator_history(key, start, end)[-days:]
    pts = [
        DiagHistoryPoint(trade_date=r.trade_date, value=_f(r.value), state=r.state,
                         pct_rank=_f(r.pct_rank), quality=r.quality)
        for r in rows
    ]  # fmt: skip
    return spec.tier, spec.unit, [float(b) for b in cfg.bands], pts, list(rows)


def diag_history(ctx: ReadContext, *, key: str, days: int) -> Envelope[DiagHistory]:
    cfg = _cfg()
    today = ctx.today()
    start = today - timedelta(days=days * 2 + 10)  # 거래일 → 달력일 여유
    tier, unit, bands, points, found = _history_points(ctx, cfg, key, start, today, days)
    if not points:
        raise NoData(NO_DIAG)
    data = DiagHistory(
        key=key, tier=cast("Literal['public', 'login']", tier), unit=unit, bands=bands,
        points=points,
    )  # fmt: skip
    as_of: datetime = max(x.as_of for x in found)
    return envelope(
        data, source=_sources(x.source for x in found), as_of=as_of,
        quality=worst(x.quality for x in found), notes=[], generated_at=ctx.now(),
    )  # fmt: skip


def diag_events(ctx: ReadContext, *, start: date | None, end: date | None) -> Envelope[DiagEvents]:
    cfg = _cfg()
    today = ctx.today()
    s = start or today
    e = end or s + timedelta(days=cfg.events.horizon_days)
    if s > e:
        raise NoData(f"기간이 거꾸로다: {s} > {e}")
    rows = ctx.repos.diag_pub.events_between(s, e)
    if not rows and ctx.repos.diag_pub.last_indicator_date(today) is None:
        raise NoData(NO_DIAG_EVENTS)
    data = DiagEvents(start=s, end=e, events=[_event(x, today) for x in rows])
    return envelope(
        data,
        source=_sources(x.source for x in rows) or "KBJ:diag.public",
        as_of=max((x.as_of for x in rows), default=ctx.now()),
        quality=worst(x.quality for x in rows) or Quality.OK,
        notes=[] if rows else ["그 기간 이벤트 없음"],
        generated_at=ctx.now(),
    )

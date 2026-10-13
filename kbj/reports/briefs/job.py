"""작업 처리기 `brief.morning`(08:10, 캐치업 09:00)·`brief.closing`(16:00, 캐치업 20:30 — ADR 0018).

- 하루 1회(U2): `notify(kind='brief.<종류>', as_of=<실행일>)` 한 통 — 정책 `dedup: daily` 라 같은 날
  두 번째 접수는 notifier 가 `duplicate` 로 돌려주고 작업은 성공으로 끝난다(재시도하지 않는다 —
  등록부
  retry max 0). 캐치업은 실행기가 아직 안 보낸 날에만 부른다(`catch_up_until`).
- 알림을 넣지 못하면(대기열 거절) 실패로 끝낸다(에러를 삼키지 않는다).
- 자원: `repos`(없으면 `connect` 로 `pg_repos`), `notify`(필수), `kr`·`us`(거래 캘린더). 시각은
  `ctx.now`·`ctx.as_of` 만 쓴다(시계 주입). 설정은 `config/briefs.yaml`·`markets.yaml`·
  `calendar_events.yaml`·`fin.yaml`(설정 파일 — 외부 호출 아님).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Final

from kbj.config.briefs import BriefsConfig, load_briefs
from kbj.config.fin import load_fin
from kbj.config.markets import load_calendar_events, load_markets
from kbj.config.settings import Settings
from kbj.core.calendar import TradingCalendar, us_calendar
from kbj.core.time import to_kst
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import pg_repos

from . import closing_brief as C
from . import morning_brief as M
from .render import Brief, Builder, Kind, assemble, render
from .sections import BriefCtx

__all__ = ["BUILDERS", "build", "closing", "make_ctx", "morning", "send"]

BUILDERS: Final[Mapping[str, Mapping[str, Builder]]] = {
    "morning": {
        "us": M.us_overnight,
        "rates_fx_commodities": M.rates_table,
        "kr_macro": M.kr_macro,
        "calendar": M.calendar_section,
    },
    "closing": {
        "market": C.market_section,
        "sectors": C.sectors_section,
        "newhigh": C.newhigh_section,
        "flows": C.flows_trend,
        "filings": C.filings_section,
    },
}


def make_ctx(
    repos: Any,
    now: datetime,
    *,
    day: date | None = None,
    cfg: BriefsConfig | None = None,
    kr: TradingCalendar | None = None,
    us: TradingCalendar | None = None,
    settings: Settings | None = None,
) -> BriefCtx:
    """섹션 입력 묶음. 설정은 파일에서 읽는다(시험은 cfg 등을 직접 준다)."""
    markets = load_markets(settings=settings)
    events = load_calendar_events(settings=settings)
    fin = load_fin(settings=settings)
    return BriefCtx(
        repos=repos,
        now=now,
        day=day or to_kst(now).date(),
        cfg=cfg or load_briefs(settings=settings),
        kr=kr or TradingCalendar.default(),
        us=us if us is not None else us_calendar(),
        indices=dict(markets.market.intraday_indices),
        bok_meetings=tuple((e.date, e.title) for e in events.events if e.kind == "bok_mpc"),
        importance_high=fin.filings.importance_levels.high,
    )


def build(kind: Kind, bc: BriefCtx) -> tuple[Brief, str]:
    brief = assemble(kind, bc, BUILDERS[kind])
    return brief, render(brief)


def send(kind: Kind, bc: BriefCtx, *, notify: Any, source: str) -> dict[str, Any]:
    """조립·렌더해서 대기열에 한 통. 반환 = 작업 기록 detail(값 없이 수·이름만)."""
    if notify is None:
        raise RuntimeError(f"자원 notify 가 주입되지 않았다 — brief.{kind} 를 넣지 못했다")
    brief, text = build(kind, bc)
    ticket = notify(text, kind=f"brief.{kind}", source=source, as_of=bc.day)
    if not getattr(ticket, "ok", False):
        raise RuntimeError(f"알림을 넣지 못했다: {getattr(ticket, 'reason', '사유 없음')}")
    return {
        "day": bc.day.isoformat(),
        "sections": [s.key for s in brief.sections if s.status == "ok"],
        "not_ready": [s.key for s in brief.sections if s.status == "not_ready"],
        "dropped": list(brief.dropped),
        "chars": len(text),
        "duplicate": bool(getattr(ticket, "duplicate", False)),
    }


def _run(ctx: JobContext, kind: Kind) -> JobResult:
    repos = ctx.resources.get("repos")
    if repos is None:
        repos = pg_repos(ctx.resource("connect"))
    try:
        day = date.fromisoformat(ctx.as_of[:10])
    except ValueError:
        day = to_kst(ctx.now).date()
    kr = ctx.resources.get("kr")
    us = ctx.resources.get("us")
    bc = make_ctx(
        repos,
        ctx.now,
        day=day,
        kr=kr if isinstance(kr, TradingCalendar) else None,
        us=us if isinstance(us, TradingCalendar) else None,
        settings=ctx.settings,
    )
    detail = send(kind, bc, notify=ctx.resources.get("notify"), source=ctx.job or f"brief.{kind}")
    return JobResult("ok", collected=tuple(ctx.keys), rows=1, detail=detail)


def morning(ctx: JobContext) -> JobResult:
    """`brief.morning`(08:10, 캐치업 09:00) 처리기."""
    return _run(ctx, "morning")


def closing(ctx: JobContext) -> JobResult:
    """`brief.closing`(16:00 거래일, 캐치업 20:30) 처리기."""
    return _run(ctx, "closing")

"""`gex.alert` 처리기 — 장중 GEX 레벨 알림(docs/p7_design.md §1.6·D-P7-16·D-P7-17, §10 Q5).

등록부 `config/jobs.yaml` `gex.alert`(정규장 09:00~15:30 매분, 쿨다운 600초 — notify.yaml
`alert.gex_level`). 흐름:

1. 지금(KST)이 `config/gex.yaml alert.window` 밖이거나 휴장이면 `skipped` — 15:30 뒤·야간 변화는
   보내지 않는다(16:00 마감 요약·다음 날 아침 브리핑에만).
2. `GexRepo.levels_latest(alert.scope)`(DB 만 — 엔진이 저장한 산출) → visible·invalid/stale 뺀
   스냅샷(`kbj.reports.gex.lines.snapshot`).
3. 막힘(산출 없음·품질 불량·사이클이 `STALE_AFTER` 보다 오래됨)이면 보내지 않고 상태도
   바꾸지 않는다.
4. Redis `gex:alert:last`(직전 상태 JSON — 표 아님, 등록부 `writes: []`)와 비교해 알림을 만들고
   notify(`alert.gex_level`, 대상 = 조건별)로 넣는다. 넣기에 실패하면 예외로 올린다(삼키지 않는다 —
   상태를 저장하지 않으니 다음 분에 다시 센다, 같은 대상 중복은 쿨다운이 막는다).
5. 새 상태를 저장한다.

시계는 `ctx.now`(주입). 엔진 서비스(`gex.engine`)는 보내지 않는다 — 이 작업 하나만 보낸다.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any, Final

from kbj.config.gex import GexConfig, load_gex
from kbj.core.calendar import TradingCalendar
from kbj.core.time import to_kst
from kbj.reports.gex.alert import KIND, AlertState, blocked_reason, evaluate
from kbj.reports.gex.lines import ALERT_EXCLUDE, snapshot
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import pg_repos

__all__ = ["KIND", "STALE_AFTER", "STATE_KEY", "in_window", "run"]

log = logging.getLogger(__name__)

STATE_KEY: Final = "gex:alert:last"
STATE_TTL_S: Final = 3 * 86_400  # 세션이 바뀌면 어차피 새로 잡는다 — 오래된 키는 사라지게
# 엔진 사이클 ≈30초(chain.ready) + 따라잡기 10초. API 리더의 '지연' 2분보다 조금 넉넉히 [제안]
STALE_AFTER: Final = timedelta(minutes=3)
SOURCE: Final = "gex.alert"


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_window(now: datetime, cfg: GexConfig, cal: TradingCalendar) -> bool:
    """정규장 창(양 끝 포함)·거래일인가(§10 Q5)."""
    k = to_kst(now)
    if not cal.is_trading_day(k.date()):
        return False
    t = k.time().replace(second=0, microsecond=0)
    return _hhmm(cfg.alert.window.start) <= t <= _hhmm(cfg.alert.window.end)


def _repos(ctx: JobContext) -> Any:
    repos = ctx.resources.get("repos")
    return repos if repos is not None else pg_repos(ctx.resource("connect"))


def _load(redis: Any) -> AlertState | None:
    return AlertState.loads(redis.get(STATE_KEY))


def run(ctx: JobContext) -> JobResult:
    cfg_res = ctx.resources.get("gex_config")
    cfg = cfg_res if isinstance(cfg_res, GexConfig) else load_gex(settings=ctx.settings)
    kr = ctx.resources.get("kr")
    cal = kr if isinstance(kr, TradingCalendar) else TradingCalendar.default()
    now = ctx.now
    if not in_window(now, cfg, cal):
        return JobResult("skipped", detail={"reason": "정규장 창 밖·휴장 — 보내지 않는다(§10 Q5)"})

    rows = _repos(ctx).gex.levels_latest(cfg.alert.scope, upto=now)
    snap = snapshot(rows, exclude=ALERT_EXCLUDE)
    why = blocked_reason(snap, now, STALE_AFTER)
    if why is not None or snap is None:
        return JobResult("ok", rows=0, detail={"sent": 0, "blocked": why})

    redis = ctx.resource("redis")
    prev = _load(redis)
    alerts, state = evaluate(prev, snap, cfg.alert.conditions)
    if alerts:
        notify = ctx.resources.get("notify")
        if notify is None:
            raise RuntimeError("자원 notify 가 주입되지 않았다 — GEX 알림을 넣지 못했다")
        for a in alerts:
            ticket = notify(
                a.text,
                kind=KIND,
                source=ctx.job or SOURCE,
                as_of=snap.trade_date,
                subject=a.subject,
            )
            if not getattr(ticket, "ok", False):
                raise RuntimeError(
                    f"알림을 넣지 못했다({a.subject}): {getattr(ticket, 'reason', '사유 없음')}"
                )
    redis.set(STATE_KEY, state.dumps(), ex=STATE_TTL_S)
    return JobResult(
        "ok",
        collected=tuple(ctx.keys),
        rows=len(alerts),
        detail={
            "sent": len(alerts),
            "subjects": [a.subject for a in alerts],
            "cycle": snap.ts.isoformat(),
            "baseline": prev is None
            or (prev.trade_date, prev.session) != (snap.trade_date, snap.session),
        },
    )

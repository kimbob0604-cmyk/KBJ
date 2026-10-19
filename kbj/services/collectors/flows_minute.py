"""`flows.market_minute` — 장중 시장별 투자자 1분 수집(KIS FHPTJ04030000 → `prv_gex.investor_flow`).

근거: docs/p5_flow_program_prompt.md §1.1·§1.3·§2·§3.4, docs/p5_design.md §0.4·§3.4·§4.7, ADR 0019
(초안 — 이 작업이 `prv_gex.investor_flow` 의 **유일한 쓰기 주인**이다. P7 GX poller 는 이 TR 을
부르지 않고 표를 읽는다), docs/metrics.md §21.

한 실행 = 한 분(as_of `YYYY-MM-DDTHH:MM` KST — 등록부 `as_of: minute`, `equity {start: open, end:
close+10, every_min: 1}`). 데이터 키 `KIS:market_investor_intraday@<분>` 하나.
1. 조합 = `config/markets.yaml flows.minute.pairs` 의 켠 것(세션 day). 코드 표는 KIS 공식 Postman
   샘플(코스피 KSP·0001, 코스닥 KSQ·1001, K2I F001·OC01·OP01, 주식선물 999·S001, 위클리 WKM·WKI —
   근거 URL 은 markets.yaml 주석).
2. 조합마다 TR 한 번(오늘 누적 한 줄) → 12구분 행. 행 `ts` = **슬롯 분**(호출 시각이 아님). 슬롯 +
   45초 안에 받았으면 `ok`, 늦었으면 `estimated`(그 분보다 뒤의 누적 — 사유는 detail `late_s`).
3. 검산 ①(현물만)·②를 분마다 하고, 어긋난 분의 행은 `invalid` 로 쓴다(지우지 않는다 — 수를 센다,
   어긋난 칸별 수는 detail `check_failed`).
4. 같은 분 재실행은 같은 키(UNIQUE ts·시장·업종·투자자)를 덮는다(멱등). 재시도(attempt ≥ 2)는 그
   분에 이미 쓴 조합을 다시 부르지 않는다.
5. 한 조합의 실패는 다른 조합을 멈추지 않는다(받은 것은 쓴다 — 절대 규칙 4). 하나라도 실패하면 키는
   미완(실행기가 슬롯 안에서 한 번 다시 — `retry {max: 1}`).
6. 모든 조합이 '값이 모두 0'(장 시작 전·휴장일)이면 `not_ready` — 0 을 사실로 쓰지 않는다.

**뒤에서 채우기는 없다**(2026-10-19 KIS 공식 레포 확인 — `kbj.data.private.kis.investors_minute`
머리말): FHPTJ04030000 은 HTS [0403] 의 '상단 표'(호출 순간 누적 한 줄, 시각 칸 없음)이고, 분 이력을
주는 시장별 투자자 TR 은 공식 예제에 없다(FHPTJ04040000 은 일별). 그래서 맥이 잠들었던 분은
영영 비고, 화면이 앞뒤 값을 이어(점선) 그린다. 실행기도 1분 작업은 따라잡지 않는다(지난 분을
지금 누적으로 채우면 틀린 값 — `kbj.services.scheduler.runner._startup_catch_up`).

자원: `repos`(없으면 `connect` 로 `pg_repos`), `kis`(없으면 `KisRestClient.for_service` — auth 토큰
읽기만·앱키 리미터), `markets`(없으면 `load_markets`), `kis_priority`(시험용 — 없으면
`config/limits.yaml
kis.priorities.market_minute`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final, Protocol

from kbj.config.markets import MinutePair
from kbj.core.p5_rows import InvestorMinute
from kbj.core.time import KST
from kbj.data.limits import load_limits
from kbj.data.private.kis.investors_minute import (
    TR_MARKET_MINUTE,
    params_market_minute,
    parse_market_minute,
)
from kbj.data.private.kis.parse import KisEmpty, TrSpec, require_ok
from kbj.data.ratelimit import Priority
from kbj.data.spec import DataKey
from kbj.engines.flows.minute import SPOT_DAILY_MARKET, apply_minute_checks, slot_quality
from kbj.services.collectors._p3 import (
    KisGetter,
    NotReady,
    Outcome,
    PartialFailure,
    kis_client,
    markets,
    reason,
)
from kbj.services.runtime.log import log_event
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import FlowsRepo, pg_repos

__all__ = [
    "DEFAULT_PRIORITY",
    "LOADED_BY",
    "TIMEOUT_S",
    "call_tr",
    "kis_priority",
    "minute_ts",
    "on_pairs",
    "run",
]

LOADED_BY: Final = "flows.market_minute"
DEFAULT_PRIORITY: Final = Priority.P2  # 분 수집이 10분 슬롯·스캔보다 먼저(§3.4 R5)
TIMEOUT_S: Final = 20.0  # 그 분 안에 끝나야 한다 — 리미터 허가 대기 상한
SERVICE: Final = "scheduler"
log = logging.getLogger("kbj.services.collectors")


class MinuteRepos(Protocol):
    @property
    def flows(self) -> FlowsRepo: ...


def call_tr(
    kis: KisGetter,
    spec: TrSpec,
    params: dict[str, str],
    *,
    what: str,
    priority: Priority,
    timeout: float | None,
) -> dict[str, Any]:
    """TR 한 번(F2 수집 처리기 공용 — `program` 도 쓴다). HTTP·rt_cd 가 정상이 아니면
    `KisRejected`."""
    resp = kis.get(spec.path, spec.tr_id, params, priority=priority, timeout=timeout)
    require_ok(what, resp.status, resp.body)
    return resp.body


def kis_priority(ctx: JobContext, role: str, default: Priority) -> Priority:
    """`kis_priority` 자원(시험) > `config/limits.yaml kis.priorities.<role>` > default."""
    got = ctx.resources.get("kis_priority")
    if got is not None:
        return Priority(got)
    src = load_limits(settings=ctx.settings).source("kis")
    name = src.priorities.get(role)
    return Priority[name] if name is not None else default


def minute_ts(as_of: str) -> datetime:
    """minute as_of(`YYYY-MM-DDTHH:MM`, KST) → 슬롯 분(aware)."""
    return datetime.strptime(as_of, "%Y-%m-%dT%H:%M").replace(tzinfo=KST)


def on_pairs(pairs: Sequence[MinutePair], session: str = "day") -> list[MinutePair]:
    """켠 조합(코드가 있는 것만 — 코드를 모르는 조합은 설정 검사가 켜지 못하게 막는다)."""
    return [
        p
        for p in pairs
        if p.enabled and p.session == session and p.market is not None and p.sector is not None
    ]


def _repos(ctx: JobContext) -> MinuteRepos:
    got = ctx.resources.get("repos")
    return got if got is not None else pg_repos(ctx.resource("connect"))


def _existing(r: MinuteRepos, ts: datetime, pairs: Sequence[MinutePair]) -> set[str]:
    """그 분에 이미 쓴 조합 id(재시도가 다시 부르지 않게)."""
    keys = {(p.market or "", p.sector or ""): p.id for p in pairs}
    got = r.flows.investor_minutes(ts.date(), list(keys))
    return {keys[(m.market_code, m.sector_code)] for m in got if m.ts == ts}


def _collect(
    ctx: JobContext,
    kis: KisGetter,
    r: MinuteRepos,
    ts: datetime,
    pairs: Sequence[MinutePair],
    detail: dict[str, Any],
) -> int:
    priority = kis_priority(ctx, "market_minute", DEFAULT_PRIORITY)
    quality = slot_quality(ts, ctx.now)
    late_s = int((ctx.now - ts).total_seconds())
    if quality.value != "ok":
        detail["late_s"] = late_s
    done = _existing(r, ts, pairs) if ctx.attempt > 1 else set[str]()
    rows: list[InvestorMinute] = []
    errors: dict[str, str] = {}
    empty: list[str] = []
    invalid_slots = 0
    failed_checks: dict[str, int] = {}
    for p in pairs:
        if p.id in done:
            continue
        market, sector = p.market or "", p.sector or ""
        try:
            body = call_tr(
                kis, TR_MARKET_MINUTE, params_market_minute(market, sector),
                what=f"FHPTJ04030000 {p.id}", priority=priority, timeout=TIMEOUT_S,
            )  # fmt: skip
            got = parse_market_minute(
                body, ts=ts, trade_date=ts.date(), market_code=market, sector_code=sector,
                session="day", quality=quality,
            )  # fmt: skip
        except KisEmpty as e:
            empty.append(p.id)
            detail.setdefault("empty", {})[p.id] = reason(e)
            continue
        except Exception as e:  # 격리 — 이 조합만 미완(사유를 남긴다, 삼키지 않는다)
            errors[p.id] = reason(e)
            log_event(log, logging.WARNING, SERVICE, "minute_pair_failed", job=ctx.job,
                      pair=p.id, error=errors[p.id])  # fmt: skip
            continue
        checked, slots = apply_minute_checks(got, spot=p.id in SPOT_DAILY_MARKET)
        for c in slots:
            if c.ok:
                continue
            invalid_slots += 1
            for what in (["c1"] if c.c1_ok is False else []) + [f"c2:{m}" for m in c.c2_failed]:
                failed_checks[f"{p.id}:{what}"] = failed_checks.get(f"{p.id}:{what}", 0) + 1
        rows += checked
    if rows:
        r.flows.put_investor_minutes(rows, loaded_by=LOADED_BY)
    detail["pairs"] = len(pairs)
    detail["pairs_written"] = len({(m.market_code, m.sector_code) for m in rows})
    if done:
        detail["pairs_already"] = len(done)
    if invalid_slots:
        detail["invalid_slots"] = invalid_slots
        detail["check_failed"] = dict(sorted(failed_checks.items()))
    if errors:
        detail["pair_errors"] = dict(sorted(errors.items()))
        raise PartialFailure(f"조합 {len(errors)}개 실패 — {next(iter(sorted(errors)))}")
    if empty and len(empty) + len(done) == len(pairs) and not rows:
        raise NotReady(f"값이 모두 0 인 조합 {len(empty)}개 — 장 시작 전·휴장일")
    return len(rows)


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.flows_minute:run` — `flows.market_minute`)."""
    if not ctx.keys:
        return JobResult("skipped", detail={"reason": "선점한 키가 없다"})
    ts = minute_ts(ctx.as_of)
    pairs = on_pairs(markets(ctx).flows.minute.pairs)
    out = Outcome(ctx.job, tuple(ctx.keys))
    if not pairs:
        out.detail["pairs"] = 0
        out.detail["reason"] = "켠 조합이 없다(config/markets.yaml flows.minute.pairs)"
        for key in ctx.keys:
            out.run(key, lambda: 0)
        return out.result()
    r = _repos(ctx)
    with kis_client(ctx) as kis:
        for key in sorted(ctx.keys, key=DataKey.label):
            out.run(key, lambda: _collect(ctx, kis, r, ts, pairs, out.detail))
    return out.result()

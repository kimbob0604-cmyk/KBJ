"""`krx.vkospi` — KRX 파생상품지수 일별에서 VKOSPI 종가를 `prv_diag.vol_index` 에 쓴다(P7 —
D-P7-14).

**꺼진 작업**(config/jobs.yaml `enabled: false`) — 엔드포인트·행 이름·칸은 실측 전이다(#P7-1).
켜기는
실측 뒤 사용자. 꺼져 있거나 값이 없으면 국장 공포탐욕의 변동성 성분은 월물 ATM IV 로 대신한다
(`kbj.services.engine.diag` — metrics §D7).

- 날짜: 선점한 데이터 키의 as_of(직전 거래일 — `collects as_of: prev_trading_day`), 키가 없으면
  ctx.as_of.
- KRX 호출은 `KrxClient`(리미터·일일 상한 `budget: krx`)로만 하루 1건.
- 행이 없거나 VKOSPI 행을 못 찾으면 `not_ready`(재시도 창 10:00 까지) — 값을 지어내지 않는다.
  찾았는데 종가가 없으면 invalid 행을 쓰고 `failed` 를 돌려준다(삼키지 않는다).
"""

from __future__ import annotations

from datetime import date
from typing import Final

from kbj.data.private.krx.client import IDX_DRVPROD_DAILY
from kbj.data.private.krx.vol_index import pick_vkospi
from kbj.services.collectors._p3 import krx_client
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.diag_prv import DiagRepo, PgDiagRepo

__all__ = ["JOB", "run"]

JOB: Final = "krx.vkospi"


def _repo(ctx: JobContext) -> DiagRepo:
    got = ctx.resources.get("diag_repo")
    if got is not None:
        return got
    repos = ctx.resources.get("repos")
    if repos is not None:
        return repos.diag
    return PgDiagRepo(ctx.resource("connect"))


def _day(ctx: JobContext) -> date:
    days = {k.as_of for k in ctx.keys} or {ctx.as_of[:10]}
    if len(days) != 1:
        raise ValueError(f"한 실행에 날짜가 여럿이다: {sorted(days)}")
    return date.fromisoformat(next(iter(days)))


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.krx_vkospi:run`)."""
    day = _day(ctx)
    client = krx_client(ctx)
    rows = client.daily(IDX_DRVPROD_DAILY, day)
    pick = pick_vkospi(rows, day, received_at=ctx.now)
    detail: dict[str, object] = {"day": day.isoformat(), "rows": len(rows), "bad": pick.bad_rows}
    if pick.row is None:
        return JobResult("not_ready", detail={**detail, "reason": pick.reason})
    n = _repo(ctx).upsert_vol_index((pick.row,), received_at=ctx.now, loaded_by=JOB)
    if pick.reason is not None:
        return JobResult("failed", rows=n, detail={**detail, "reason": pick.reason})
    return JobResult("ok", collected=ctx.keys, rows=n, detail=detail)

"""`/api/backtest*` — 즉석 백테스트·야간 실행 요약·일치 감시(docs/p8_design.md §4.6·§5·D-P8-17 —
로그인, DB·Redis 만 읽는다, 계약 ⑩). 경로·쿼리·응답 모델은 웨이브 1(FND)이 고정, 본문은 BKT.

- `POST /api/backtest` 는 저장하지 않는 계산(상한 config/rules.yaml adhoc — 넘으면 413/503, 결과는
  Redis `rules:bt:<요청 해시>:<원장 기준일>` 1시간). POST 라 `WriteSession`(CSRF·본문 상한).
- 캐시 영역 `backtest`(cache.py — rules.outcome·rules.backtest_nightly 완료가 버전).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Final

from fastapi import Body, Query, Request, Response

from kbj.config.rules_cfg import load_rules_cfg
from kbj.services.api.deps import PRIVATE, TTL_DEFAULT_S, cached_json, get_state
from kbj.services.api.models.backtest import BacktestIn, BacktestMatch, BacktestResult, BacktestRuns
from kbj.services.api.models.common import Envelope
from kbj.services.api.readers.backtest import adhoc_backtest, backtest_match, backtest_runs
from kbj.services.api.routes._common import data_router
from kbj.services.api.writes import WRITE_RESPONSES, WriteSession
from kbj.services.engine.rules_perf import split_ratio_of

__all__ = ["DOMAIN", "router"]

DOMAIN: Final = "backtest"
RULE_ID: Final = r"^r_[0-9a-z]{12}$"

router = data_router(DOMAIN)


@router.post("", response_model=Envelope[BacktestResult], responses=WRITE_RESPONSES)
def run(request: Request, _: WriteSession, body: Annotated[BacktestIn, Body()]) -> Response:
    st = get_state(request)
    cfg = load_rules_cfg(settings=st.settings)
    env = adhoc_backtest(st.read, body, cfg=cfg, split_ratio=split_ratio_of(), redis=st.redis)
    return Response(
        content=env.model_dump_json(), media_type="application/json",
        headers={"Cache-Control": PRIVATE},
    )  # fmt: skip


@router.get("/runs", response_model=Envelope[BacktestRuns])
def runs(request: Request, rule: Annotated[str | None, Query(pattern=RULE_ID)] = None) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_DEFAULT_S,
        build=lambda: backtest_runs(st.read, rule_id=rule),
    )


@router.get("/match", response_model=Envelope[BacktestMatch])
def match(
    request: Request,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request, domain=DOMAIN, ttl_s=TTL_DEFAULT_S,
        build=lambda: backtest_match(st.read, start=start, end=end),
    )  # fmt: skip

"""`/api/rules/*` — 규칙 목록·판 이력·쓰기·검증·예시·신호 이력·성과 통계(docs/p8_design.md §5 —
로그인,
DB·Redis 만 읽는다, 계약 ⑩). 경로·쿼리·응답 모델은 웨이브 1(FND)이 고정, 리더·쓰기 본문은 EVL
(성과 통계 `/stats` 는 BKT — `readers/rules_stats.py`).

- 쓰기(POST·PUT)는 `WriteSession`(세션 + CSRF + 본문 64 KiB — `kbj.services.api.writes`) 뒤
  쓰기 모듈 `kbj.services.personal.rules`(CLI 와 같은 함수). 규칙 수정은 새 판(판은 바꾸지
  않는다), 삭제는 보관(`archive`) — D-P8-16. 검사 실패는 422 `invalid_rule`(위치·사유만 — 입력값
  없음), 없는 규칙 404 `unknown_rule`, 보관된 규칙 고치기·켜기 409 `rule_state`. 쓰기 응답은
  `no-store`.
- `POST /validate` 는 상태를 바꾸지 않는다 — 검사 실패도 200(ok=false), 통과면 오늘 원장 시험 평가.
- 캐시 영역 `rules`(cache.py — 규칙 작업 넷 완료가 버전). 규칙 목록·판 이력은 쓰기 모듈이 DB 에 바로
  쓰므로 캐시 없이 만든다(`fresh_json`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Final, Literal

from fastapi import Body, Path, Query, Request, Response
from pydantic import BaseModel

from kbj.config.rules_cfg import load_rules_cfg
from kbj.core.quality import Quality
from kbj.engines.rules.dsl import DslError, dsl_hash, limits_of, validate_rule
from kbj.services.api.deps import TTL_DEFAULT_S, ApiError, cached_json, get_state
from kbj.services.api.models.backtest import RuleStats
from kbj.services.api.models.common import Envelope, ErrorBody
from kbj.services.api.models.rules import (
    RuleDetail,
    RuleExamples,
    RuleIn,
    RuleList,
    RuleWriteResult,
    SignalList,
    SignalModeT,
    ValidateIssue,
    ValidateResult,
)
from kbj.services.api.readers._common import envelope, worst
from kbj.services.api.readers.rules import (
    SOURCE,
    rule_detail,
    rule_examples,
    rule_signals,
    rule_trial,
    rules_list,
)
from kbj.services.api.readers.rules_stats import rule_stats
from kbj.services.api.routes._common import data_router
from kbj.services.api.writes import WRITE_RESPONSES, WriteSession, fresh_json
from kbj.services.personal.rules import (
    RuleNotFound,
    RuleStateError,
    WriteResult,
    create_rule,
)
from kbj.services.personal.rules import new_version as write_version
from kbj.services.personal.rules import set_state as write_state

__all__ = ["DOMAIN", "router"]

DOMAIN: Final = "rules"
RULE_ID: Final = r"^r_[0-9a-z]{12}$"
CODE: Final = r"^[0-9A-Z]{6}$"

router = data_router(DOMAIN)
RuleIdP = Annotated[str, Path(pattern=RULE_ID)]
RULE_WRITE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **WRITE_RESPONSES,
    404: {"model": ErrorBody, "description": "없는 규칙(unknown_rule)"},
    409: {"model": ErrorBody, "description": "보관된 규칙 고치기·켜기(rule_state)"},
    422: {"model": ErrorBody, "description": "규칙 검사 실패(invalid_rule — 위치·사유만)"},
}


@router.get("", response_model=Envelope[RuleList])
def rules(request: Request) -> Response:
    st = get_state(request)
    return fresh_json(request, lambda: rules_list(st.read))


@router.get("/examples", response_model=Envelope[RuleExamples])
def examples(request: Request) -> Response:
    st = get_state(request)
    return fresh_json(request, lambda: rule_examples(st.read))


@router.get("/signals", response_model=Envelope[SignalList])
def signals(
    request: Request,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
    rule: Annotated[str | None, Query(pattern=RULE_ID)] = None,
    code: Annotated[str | None, Query(pattern=CODE)] = None,
    mode: SignalModeT | None = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_DEFAULT_S,
        build=lambda: rule_signals(
            st.read, start=start, end=end, rule_id=rule, code=code, mode=mode
        ),
    )


@router.get("/stats", response_model=Envelope[RuleStats])
def stats(
    request: Request,
    rule: Annotated[str | None, Query(pattern=RULE_ID)] = None,
    version: Annotated[int | None, Query(ge=1)] = None,
    horizon: Annotated[int | None, Query(ge=1, le=60)] = None,
) -> Response:
    st = get_state(request)
    return cached_json(
        request,
        domain=DOMAIN,
        ttl_s=TTL_DEFAULT_S,
        build=lambda: rule_stats(st.read, rule_id=rule, version=version, horizon=horizon),
    )


@router.get("/{rule_id}", response_model=Envelope[RuleDetail])
def detail(request: Request, rule_id: RuleIdP) -> Response:
    st = get_state(request)
    return fresh_json(request, lambda: rule_detail(st.read, rule_id))


# ── 쓰기(쓰기 모듈 `kbj.services.personal.rules` 하나 — D-P8-15·16) ──────────────────────────


def _no_store(env: BaseModel, status: int = 200) -> Response:
    return Response(
        content=env.model_dump_json(),
        media_type="application/json",
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _invalid(e: DslError) -> Response:
    """422 — 위치(JSON 포인터)·사유 종류만(입력값 없음)."""
    body = ErrorBody(
        code="invalid_rule",
        message="규칙 검사 실패",
        detail={"issues": [{"pointer": i.pointer, "reason": i.reason} for i in e.issues]},
    )
    return Response(
        content=body.model_dump_json(),
        media_type="application/json",
        status_code=422,
        headers={"Cache-Control": "no-store"},
    )


def _written(request: Request, r: WriteResult, status: int = 200) -> Response:
    st = get_state(request)
    data = RuleWriteResult(
        rule_id=r.rule_id,
        version=r.version,
        dsl_hash=r.dsl_hash,
        enabled=r.enabled,
        archived=r.archived,
    )
    env = envelope(
        data,
        source=SOURCE,
        as_of=st.read.now(),
        quality=Quality.OK,
        notes=[],
        generated_at=st.read.now(),
    )
    return _no_store(env, status)


def _guard_errors(fn: Callable[[], Response]) -> Response:
    try:
        return fn()
    except DslError as e:
        return _invalid(e)
    except RuleNotFound:
        raise ApiError(404, "unknown_rule", "없는 규칙") from None
    except RuleStateError as e:
        raise ApiError(409, "rule_state", str(e)) from None


@router.post(
    "", response_model=Envelope[RuleWriteResult], status_code=201, responses=RULE_WRITE_RESPONSES
)
def create(request: Request, _: WriteSession, body: Annotated[RuleIn, Body()]) -> Response:
    st = get_state(request)

    def run() -> Response:
        r = create_rule(
            st.read.repos,
            body.dsl,
            enabled=body.enabled,
            source="api",
            now=st.read.now(),
            loaded_by="api",
            cfg=load_rules_cfg(),
        )
        return _written(request, r, 201)

    return _guard_errors(run)


@router.post("/validate", response_model=Envelope[ValidateResult], responses=WRITE_RESPONSES)
def validate(request: Request, _: WriteSession, body: Annotated[RuleIn, Body()]) -> Response:
    """검증 + 오늘 원장으로 시험 평가(저장하지 않는다). 검사 실패도 200(ok=false + 위치·사유)."""
    st = get_state(request)
    now = st.read.now()
    try:
        rule = validate_rule(body.dsl, limits_of(load_rules_cfg()))
    except DslError as e:
        issues = [ValidateIssue(pointer=i.pointer, reason=i.reason) for i in e.issues]
        res = ValidateResult(ok=False, issues=issues, dsl_hash=None, normalized=None, trial=[])
        return _no_store(
            envelope(res, source=SOURCE, as_of=now, quality=Quality.OK, notes=[], generated_at=now)
        )
    trial, notes, as_of = rule_trial(st.read, rule)
    res = ValidateResult(
        ok=True,
        issues=[],
        dsl_hash=dsl_hash(rule),
        normalized=rule.model_dump(mode="json", exclude_none=True),
        trial=trial,
    )
    q = worst(t.quality for t in trial) if trial else Quality.OK
    return _no_store(
        envelope(
            res,
            source=SOURCE if not trial else "KBJ:rules+원장",
            as_of=as_of or now,
            quality=q,
            notes=notes,
            generated_at=now,
        )
    )


@router.put("/{rule_id}", response_model=Envelope[RuleWriteResult], responses=RULE_WRITE_RESPONSES)
def new_version(
    request: Request, _: WriteSession, rule_id: RuleIdP, body: Annotated[RuleIn, Body()]
) -> Response:
    st = get_state(request)

    def run() -> Response:
        r = write_version(
            st.read.repos,
            rule_id,
            body.dsl,
            enabled=body.enabled,
            now=st.read.now(),
            cfg=load_rules_cfg(),
        )
        return _written(request, r)

    return _guard_errors(run)


@router.post(
    "/{rule_id}/{action}", response_model=Envelope[RuleWriteResult], responses=RULE_WRITE_RESPONSES
)
def set_state(
    request: Request,
    _: WriteSession,
    rule_id: RuleIdP,
    action: Literal["enable", "disable", "archive"],
) -> Response:
    st = get_state(request)
    return _guard_errors(
        lambda: _written(request, write_state(st.read.repos, rule_id, action, now=st.read.now()))
    )

"""`/api/journal/*` — 분석 일지·매매 기록·추천 성과(docs/p8_design.md §4.7·§5 — 로그인, DB 만
읽는다, 계약 ⑩). 경로·쿼리·응답 모델은 웨이브 1(FND)이 고정, 리더·쓰기 본문은 JNL.

쓰기(POST·PATCH·DELETE)는 `WriteSession`(세션 + CSRF + 본문 64 KiB) 뒤 쓰기 모듈
`kbj.services.personal.journal` 하나를 부른다(D-P8-15). 없는 id 404, 행 검사 실패·음수 보유 422
(사유 코드만 — 입력값 없음). 개인 기록은 쓰기 모듈이 DB 에 바로 쓰므로 읽기는 캐시 없이 만든다
(`fresh_json`), 쓰기 응답은 `no-store`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Final

from fastapi import Body, Path, Query, Request, Response
from pydantic import BaseModel

from kbj.core.quality import Quality
from kbj.services.api.deps import ApiError, get_state
from kbj.services.api.models.common import Envelope, ErrorBody
from kbj.services.api.models.journal import (
    AnalysisIn,
    AnalysisList,
    AnalysisMarkdown,
    AnalysisOut,
    AnalysisPrefill,
    Deleted,
    PickList,
    TradeIn,
    TradeList,
    TradeOut,
)
from kbj.services.api.readers._common import envelope
from kbj.services.api.readers.journal import (
    SOURCE,
    analysis,
    analysis_env,
    analysis_list,
    analysis_markdown,
    analysis_prefill,
    picks,
    trade_env,
    trades,
)
from kbj.services.api.routes._common import data_router
from kbj.services.api.writes import WRITE_RESPONSES, WriteSession, fresh_json
from kbj.services.personal.journal import (
    AnalysisNotFound,
    NegativeHolding,
    TradeNotFound,
    add_trade,
    create_analysis,
    delete_analysis,
    delete_trade,
    update_analysis,
)

__all__ = ["DOMAIN", "JOURNAL_WRITE_RESPONSES", "router"]

DOMAIN: Final = "journal"
CODE: Final = r"^[0-9A-Z]{6}$"

router = data_router(DOMAIN)
IdP = Annotated[int, Path(ge=1)]
CodeQ = Annotated[str | None, Query(pattern=CODE)]

JOURNAL_WRITE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **WRITE_RESPONSES,
    404: {"model": ErrorBody, "description": "없는 일지·매매"},
    422: {"model": ErrorBody, "description": "행 검사 실패·보유 수량 음수(사유 코드만)"},
}


def _no_store(env: BaseModel, status: int = 200) -> Response:
    return Response(
        content=env.model_dump_json(),
        media_type="application/json",
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _guard(fn: Callable[[], Response]) -> Response:
    """쓰기 모듈의 거절 → HTTP. 문구에 입력값(금액·수량)을 싣지 않는다."""
    try:
        return fn()
    except AnalysisNotFound:
        raise ApiError(404, "unknown_analysis", "없는 일지") from None
    except TradeNotFound:
        raise ApiError(404, "unknown_trade", "없는 매매") from None
    except NegativeHolding:
        raise ApiError(422, "negative_holding", "보유 수량보다 많이 판다") from None
    except ValueError:
        raise ApiError(422, "invalid_row", "칸 형식이 틀렸다") from None


def _deleted(request: Request, row_id: int) -> Response:
    st = get_state(request)
    now = st.read.now()
    env = envelope(Deleted(id=row_id), source=SOURCE, as_of=now, quality=Quality.OK, notes=[],
                   generated_at=now)  # fmt: skip
    return _no_store(env)


@router.get("/analysis", response_model=Envelope[AnalysisList])
def analysis_index(request: Request, code: CodeQ = None) -> Response:
    st = get_state(request)
    return fresh_json(request, lambda: analysis_list(st.read, code=code))


@router.post("/analysis", response_model=Envelope[AnalysisOut], status_code=201,
             responses=JOURNAL_WRITE_RESPONSES)  # fmt: skip
def analysis_create(
    request: Request, _: WriteSession, body: Annotated[AnalysisIn, Body()]
) -> Response:
    st = get_state(request)

    def run() -> Response:
        row = create_analysis(st.read.repos, body.model_dump(), source="api", now=st.read.now())
        return _no_store(analysis_env(st.read, row), 201)

    return _guard(run)


@router.get("/analysis/prefill/{code}", response_model=Envelope[AnalysisPrefill])
def analysis_prefill_route(request: Request, code: Annotated[str, Path(pattern=CODE)]) -> Response:
    st = get_state(request)
    return fresh_json(request, lambda: analysis_prefill(st.read, code))


@router.get("/analysis/{analysis_id}", response_model=Envelope[AnalysisOut])
def analysis_one(request: Request, analysis_id: IdP) -> Response:
    st = get_state(request)
    return fresh_json(request, lambda: analysis(st.read, analysis_id))


@router.patch("/analysis/{analysis_id}", response_model=Envelope[AnalysisOut],
              responses=JOURNAL_WRITE_RESPONSES)  # fmt: skip
def analysis_update(
    request: Request, _: WriteSession, analysis_id: IdP, body: Annotated[AnalysisIn, Body()]
) -> Response:
    st = get_state(request)

    def run() -> Response:
        row = update_analysis(st.read.repos, analysis_id, body.model_dump(), now=st.read.now())
        return _no_store(analysis_env(st.read, row))

    return _guard(run)


@router.delete("/analysis/{analysis_id}", response_model=Envelope[Deleted],
               responses=JOURNAL_WRITE_RESPONSES)  # fmt: skip
def analysis_delete(request: Request, _: WriteSession, analysis_id: IdP) -> Response:
    st = get_state(request)
    return _guard(lambda: _deleted(request, delete_analysis(st.read.repos, analysis_id)))


@router.get("/analysis/{analysis_id}/markdown", response_model=Envelope[AnalysisMarkdown])
def analysis_md(request: Request, analysis_id: IdP) -> Response:
    st = get_state(request)
    return fresh_json(request, lambda: analysis_markdown(st.read, analysis_id))


@router.get("/trades", response_model=Envelope[TradeList])
def trade_index(request: Request, code: CodeQ = None) -> Response:
    st = get_state(request)
    return fresh_json(request, lambda: trades(st.read, code=code))


@router.post("/trades", response_model=Envelope[TradeOut], status_code=201,
             responses=JOURNAL_WRITE_RESPONSES)  # fmt: skip
def trade_create(request: Request, _: WriteSession, body: Annotated[TradeIn, Body()]) -> Response:
    st = get_state(request)

    def run() -> Response:
        row = add_trade(st.read.repos, body.model_dump(), source="api", now=st.read.now())
        return _no_store(trade_env(st.read, row), 201)

    return _guard(run)


@router.delete("/trades/{trade_id}", response_model=Envelope[Deleted],
               responses=JOURNAL_WRITE_RESPONSES)  # fmt: skip
def trade_delete(request: Request, _: WriteSession, trade_id: IdP) -> Response:
    st = get_state(request)
    return _guard(lambda: _deleted(request, delete_trade(st.read.repos, trade_id)))


@router.get("/picks", response_model=Envelope[PickList])
def pick_index(request: Request) -> Response:
    st = get_state(request)
    return fresh_json(request, lambda: picks(st.read))

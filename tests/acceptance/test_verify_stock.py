"""실데이터 완료 검증 CLI(`kbj.services.engine.verify_stock`)를 처리기를 거친 합성 세계에 돌린다.

docs/p4_design.md §0.3('같은 기준 — 실데이터')·§8.8 #37. 키를 받은 뒤 운영 DB 로 도는 것과 같은
코드가 완료 시험 세계(`p4_pipeline.py`)에서 실패 칸 0 을 내고, 출력에 값·종목코드가 없음을 본다.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable

import pytest
from fastapi import FastAPI

from kbj.services.api.routes import stock as stock_routes
from kbj.services.engine import verify_stock as vs
from tests.acceptance.p4_pipeline import P4Pipeline
from tests.unit.api.api_world_p4 import P4ApiWorld, expect


def _build(
    api: P4ApiWorld, pipeline: P4Pipeline
) -> Callable[[], tuple[FastAPI, Callable[..., list[str]]]]:
    def build() -> tuple[FastAPI, Callable[..., list[str]]]:
        def watch(name: str) -> list[str]:
            return [i.code for i in pipeline.repos.journal.watchlist(name)]

        return api.app(), watch

    return build


def test_routes_are_the_page8_routes_of_the_acceptance_matrix() -> None:
    assert [r.name for r in vs.ROUTES] == list(expect()["routes"])


def test_watchlist_has_no_failed_cell(api: P4ApiWorld, pipeline: P4Pipeline) -> None:
    out, err = io.StringIO(), io.StringIO()
    rc = vs.main(["--json"], out=out, err=err, build=_build(api, pipeline))
    assert rc == 0, out.getvalue() + err.getvalue()
    summary = json.loads(out.getvalue())
    watch = [i.code for i in pipeline.repos.journal.watchlist("main")]
    assert summary["stocks"] == len(watch) == 19  # #16 은 관심 밖
    assert summary["cells"] == 19 * len(vs.ROUTES)
    assert summary["failures"] == 0 and summary["problems"] == {}
    assert set(summary["status"]) <= {"200", "404"}
    # 값·종목코드가 출력에 없다(관심종목은 개인 데이터) — 기본 모드
    text = out.getvalue()
    assert not [c for c in watch if c in text]


def test_text_output_and_show_codes(api: P4ApiWorld, pipeline: P4Pipeline) -> None:
    out = io.StringIO()
    rc = vs.main(["--show-codes"], out=out, err=io.StringIO(), build=_build(api, pipeline))
    assert rc == 0
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("종목 상세 검증 — 관심종목 19개 × 라우트 14개")
    assert lines[-1] == "실패 0칸 (없음)"  # 실패가 없으면 코드도 없다


def test_failures_are_counted_and_exit_1(api: P4ApiWorld, pipeline: P4Pipeline) -> None:
    """라우트 하나가 깨지면(5xx) 그 칸만 실패로 세고 종료 코드 1 — 다른 칸은 계속 부른다."""
    app = api.app()

    def boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("검사용 고장")

    mp = pytest.MonkeyPatch()
    mp.setattr(stock_routes, "stock_header", boom)
    try:
        report = vs.verify_app(app, ["994010", "994020"])
    finally:
        mp.undo()
    bad = report.failures
    assert bad and {c.route for c in bad} == {"header"} and {c.problem for c in bad} == {"http_500"}
    assert len(report.cells) == 2 * len(vs.ROUTES)
    summary = report.summary(show_codes=True)
    assert summary["failed_cells"] == [("header", "994010"), ("header", "994020")]


def test_empty_watchlist_and_errors_have_distinct_exit_codes(api: P4ApiWorld) -> None:
    err = io.StringIO()
    assert vs.main([], out=io.StringIO(), err=err, build=lambda: (api.app(), lambda _n: [])) == 1
    assert "비었다" in err.getvalue()

    def broken() -> tuple[FastAPI, Callable[..., list[str]]]:
        raise ConnectionError("postgresql://user:secret@db/kbj")

    err = io.StringIO()
    assert vs.main([], out=io.StringIO(), err=err, build=broken) == 2
    assert "secret" not in err.getvalue() and "ConnectionError" in err.getvalue()


def test_classify_rejects_nan_and_unknown_404() -> None:
    route = vs.ROUTES[0]
    assert vs.classify(route, 200, {}, '{"x": NaN}').problem == "nan_or_bad_json"
    assert vs.classify(route, 404, {}, '{"code": "nope"}').problem == "404_nope"
    assert vs.classify(route, 404, {}, '{"code": "no_data"}').outcome == "no_data"
    assert vs.classify(route, 503, {}, "").problem == "http_503"
    xlsx = next(r for r in vs.ROUTES if r.model is None)
    assert vs.classify(xlsx, 200, {"content-type": "text/html"}, "").problem == "not_xlsx"

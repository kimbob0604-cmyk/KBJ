"""`web/test/fixtures/api/*.json` — 합성 응답 픽스처가 API 응답 모델과 같은 모양이고 최신인가."""

from __future__ import annotations

import json
import math
from typing import Any, cast

import pytest
from pydantic import BaseModel

from kbj.services.api.models.auth import CsrfResponse, MeResponse
from kbj.services.api.models.common import Envelope, ErrorBody, NoDataBody
from tests.unit.api.make_web_fixtures import AUTH, FIXTURES, OUT, render
from tests.unit.api.test_routes_common import DATA_ROUTES


def _base(path: str) -> str:
    b = path.split("?")[0]
    return "/api/flows/stock" if b.startswith("/api/flows/stock/") else b


_MODEL_BY_PATH: dict[str, type[BaseModel]] = {_base(p): m for p, m, _ in DATA_ROUTES}
_AUTH_MODELS: dict[str, type[BaseModel]] = {
    "auth_me.json": MeResponse,
    "auth_login.json": CsrfResponse,
    "no_data.json": NoDataBody,
    "unauthorized.json": ErrorBody,
}


def _model(path: str) -> type[BaseModel]:
    return _MODEL_BY_PATH[_base(path)]


def _load(name: str) -> Any:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(("name", "path", "live"), FIXTURES)
def test_fixture_is_valid_envelope(name: str, path: str, live: bool) -> None:
    body = _load(name)
    Envelope[_model(path)].model_validate(body)
    assert body["source"] and body["as_of"] and body["quality"]
    if live and "summary" in name:
        assert body["quality"] == "estimated"


@pytest.mark.parametrize("name", sorted(AUTH))
def test_auth_fixtures(name: str) -> None:
    _AUTH_MODELS[name].model_validate(_load(name))


def test_every_data_route_has_a_fixture() -> None:
    have = {_model(p) for _, p, _ in FIXTURES}
    assert have == set(_MODEL_BY_PATH.values())


def test_fixtures_are_fresh() -> None:
    """합성 세계로 다시 만든 글자와 같다.

    다르면 `uv run python -m tests.unit.api.make_web_fixtures` 로 다시 만든다.
    """
    for name, text in render().items():
        assert (OUT / name).read_text(encoding="utf-8") == text, name


# ── P4(묶음 A — docs/p4_design.md §1.8): 합성 20종목 세계의 하위 폴더 픽스처 ──────────────────


def _p4_model(rel: str) -> type[BaseModel] | None:
    """하위 폴더 픽스처 → 응답 모델(404 본문이면 None)."""
    from kbj.services.api.models.filings import (
        EarningsList,
        EarningsSchedule,
        FilingsFeed,
        InsiderRecent,
        OverhangRecent,
    )
    from kbj.services.api.models.flows import StockFlowDetail
    from kbj.services.api.models.stock import StockSearch, Watchlist
    from tests.unit.api.test_routes_stock import STOCK_MODELS

    fixed: dict[str, type[BaseModel]] = {
        "stock/search.json": StockSearch,
        "filings/feed.json": FilingsFeed,
        "filings/earnings.json": EarningsList,
        "filings/overhang.json": OverhangRecent,
        "filings/insiders.json": InsiderRecent,
        "filings/schedule.json": EarningsSchedule,
        "watchlist/main.json": Watchlist,
    }
    if rel in fixed:
        return fixed[rel]
    section = rel.rsplit("/", 1)[-1].removesuffix(".json")
    return StockFlowDetail if section == "flows" else STOCK_MODELS[section]


def test_p4_fixtures_are_valid() -> None:
    from tests.unit.api.make_web_fixtures import P4_OTHER, P4_SECTIONS, p4_codes, p4_files_on_disk

    index = _load("stock/_index.json")
    assert sorted(index) == p4_codes()
    names = {n for n, _t in P4_SECTIONS} | {"flows"}
    want = {f"stock/{c}/{n}.json" for c in p4_codes() for n in names}
    want |= {rel for rel, _p in P4_OTHER} | {"stock/_index.json"}
    assert p4_files_on_disk() == want
    for rel in sorted(want - {"stock/_index.json"}):
        body = _load(rel)
        text = (OUT / rel).read_text(encoding="utf-8")
        assert "NaN" not in text and "Infinity" not in text, rel
        if rel.startswith("stock/") and rel.count("/") == 2:
            code, section = rel.split("/")[1], rel.split("/")[2].removesuffix(".json")
            status = index[code][section]
        else:
            status = 200
        if status == 404:
            assert body["code"] == "no_data" and body["message"].startswith("아직 없음"), rel
            continue
        model = _p4_model(rel)
        assert model is not None
        Envelope[model].model_validate(body)


def _same(a: Any, b: Any, where: str) -> None:
    """JSON 값 비교 — 실수는 허용오차(rel·abs 1e-9), 그 밖은 정확히(CLAUDE §4 — 플랫폼 간 float
    끝자리 차이로 거짓 실패하지 않게. 배수·백분위·비율이 실수다)."""
    if isinstance(a, float) or isinstance(b, float):
        assert isinstance(a, int | float) and isinstance(b, int | float), where
        assert not isinstance(a, bool) and not isinstance(b, bool), where
        assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9), (where, a, b)
    elif isinstance(a, dict) and isinstance(b, dict):
        da, db = cast(dict[str, Any], a), cast(dict[str, Any], b)
        assert list(da) == list(db), where  # 키와 순서까지(파일 글자가 바뀌지 않게)
        for k in da:
            _same(da[k], db[k], f"{where}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        la, lb = cast(list[Any], a), cast(list[Any], b)
        assert len(la) == len(lb), where
        for i, (x, y) in enumerate(zip(la, lb, strict=True)):
            _same(x, y, f"{where}[{i}]")
    else:
        assert type(a) is type(b) and a == b, (where, a, b)


def test_p4_fixtures_are_fresh() -> None:
    """합성 세계로 다시 만든 응답과 같다(실수는 허용오차 — `_same`).

    다르면 `uv run python -m tests.unit.api.make_web_fixtures` 로 다시 만든다.
    """
    from tests.unit.api.make_web_fixtures import render_p4

    for rel, text in render_p4().items():
        _same(_load(rel), json.loads(text), rel)

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
from tests.unit.api.web_fixtures_p5 import DOMAINS as P5_DOMAINS
from tests.unit.api.web_fixtures_p6 import DOMAINS as P6_DOMAINS


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


# ── P5(docs/p5_design.md §1.1 — 도메인별 모듈, 도메인 폴더만) ──────────────────────────


@pytest.mark.parametrize("domain", P5_DOMAINS)
def test_p5_fixtures_are_fresh(domain: str) -> None:
    """`web/test/fixtures/api/<도메인>/**` 가 그 도메인 모듈의 출력과 같다(파일 집합·바이트). 다르면
    `uv run python -m tests.unit.api.make_web_fixtures --only <도메인>` 로 다시 만든다."""
    from tests.unit.api.make_web_fixtures import p5_files_on_disk, render_p5

    want = render_p5(domain)
    assert set(want) == p5_files_on_disk(domain), f"{domain}: 픽스처 파일 목록이 낡았다"
    for rel, text in want.items():
        assert rel.startswith(f"{domain}/")
        assert (OUT / rel).read_text(encoding="utf-8") == text, f"{rel} 이 낡았다"
        json.loads(text)


def test_p5_domains_and_modules() -> None:
    import importlib

    from tests.unit.api.make_web_fixtures import render_p5

    assert P5_DOMAINS == ("macro", "etf", "us", "program", "index_review")
    for d in P5_DOMAINS:
        mod = importlib.import_module(f"tests.unit.api.web_fixtures_p5.{d}")
        assert mod.DOMAIN == d
    with pytest.raises(ValueError, match="모르는 P5 도메인"):
        render_p5("nope")
    # P5 도메인 폴더는 P3 평평한 파일·P4 하위 폴더와 겹치지 않는다
    assert not set(P5_DOMAINS) & {"stock", "filings", "watchlist"}


def test_p5_render_refuses_paths_outside_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.api import make_web_fixtures as mk
    from tests.unit.api.web_fixtures_p5 import macro

    for bad in ("../x.json", "/abs.json", "a/../../b.json", "x.txt"):
        monkeypatch.setattr(macro, "render", lambda bad=bad: {bad: "{}"})
        with pytest.raises(ValueError, match="경로 형식"):
            mk.render_p5("macro")


# ── P6(docs/p6_design.md §1.1 — 도메인별 모듈, trade_<도메인> 폴더만) ──────────────────────


@pytest.mark.parametrize("domain", P6_DOMAINS)
def test_p6_fixtures_are_fresh(domain: str) -> None:
    """`web/test/fixtures/api/trade_<도메인>/**` 가 그 도메인 모듈의 출력과 같다. 다르면
    `uv run python -m tests.unit.api.make_web_fixtures --only <도메인>` 로 다시 만든다."""
    from tests.unit.api.make_web_fixtures import p6_files_on_disk, render_p6

    want = render_p6(domain)
    assert set(want) == p6_files_on_disk(domain), f"{domain}: 픽스처 파일 목록이 낡았다"
    for rel, text in want.items():
        assert rel.startswith(f"trade_{domain}/")
        assert (OUT / rel).read_text(encoding="utf-8") == text, f"{rel} 이 낡았다"
        json.loads(text)


def test_p6_domains_and_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    from tests.unit.api import make_web_fixtures as mk
    from tests.unit.api.web_fixtures_p6 import flash

    assert P6_DOMAINS == ("flash", "explore", "map")
    for d in P6_DOMAINS:
        assert importlib.import_module(f"tests.unit.api.web_fixtures_p6.{d}").DOMAIN == d
    with pytest.raises(ValueError, match="모르는 P6 도메인"):
        mk.render_p6("nope")
    # P6 폴더(trade_*)는 P3·P4·P5 폴더와 겹치지 않는다
    assert not {f"trade_{d}" for d in P6_DOMAINS} & {*P5_DOMAINS, "stock", "filings", "watchlist"}
    for bad in ("../x.json", "/abs.json", "x.txt"):
        monkeypatch.setattr(flash, "render", lambda bad=bad: {bad: "{}"})
        with pytest.raises(ValueError, match="경로 형식"):
            mk.render_p6("flash")

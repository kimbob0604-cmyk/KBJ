"""공개 사이트 내보내기(site) — 파일 이름 규칙·개인 데이터 빼기·묶음(live·full). 합성 세계만."""

from __future__ import annotations

import io
import json
import tarfile
from collections import Counter
from pathlib import Path

import pytest

from kbj.services.api.site import (
    SKIP,
    SiteConfig,
    SiteExpand,
    SiteFile,
    SiteRequest,
    base_requests,
    build_site,
    codes_in,
    expand_requests,
    harvest,
    load_site_config,
    scrub,
    site_file,
    write_tar,
)
from tests.unit.api.api_world import World

# web/test/snapshot.test.ts 와 같은 표 — 두 쪽 이름 규칙이 어긋나면 화면이 파일을 못 찾는다
NAME_CASES: list[tuple[str, dict[str, object] | None, str]] = [
    ("market/summary", None, "market/summary.json"),
    ("etf/flows", {"period": 5, "mode": "out"}, "etf/flows@mode-out.period-5.json"),
    ("x", {"b": True, "a": "", "c": None}, "x@b-true.json"),
    ("diag/history", {"key": "credit/spread", "from": "2026-11-09"},
     "diag/history@from-2026-11-09.key-credit_spread.json"),
    ("stock/005930/valuation", {"metric": "per"}, "stock/005930/valuation@metric-per.json"),
]  # fmt: skip


@pytest.mark.parametrize(("key", "params", "want"), NAME_CASES)
def test_site_file_names(key: str, params: dict[str, object] | None, want: str) -> None:
    assert site_file(key, params) == want


def test_request_url_and_file_agree() -> None:
    r = SiteRequest("etf/flows", (("mode", "out"), ("period", "5")))
    assert r.url == "/api/etf/flows?mode=out&period=5"
    assert r.file == "etf/flows@mode-out.period-5.json"


def test_scrub_clears_watch_flags_everywhere() -> None:
    body = {"data": {"items": [{"code": "005930", "is_watch": True}], "is_watch": True}}
    assert scrub(body) == {
        "data": {"items": [{"code": "005930", "is_watch": False}], "is_watch": False}
    }


def test_codes_in_counts_code_fields_only() -> None:
    seen: Counter[str] = Counter()
    codes_in(
        {"rows": [{"code": "005930"}, {"code": "005930"}, {"code": "0088M0"}], "name": "005930"},
        seen,
    )
    assert seen == Counter({"005930": 2, "0088M0": 1})


def test_config_rejects_bad_codes(tmp_path: Path) -> None:
    p = tmp_path / "site.yaml"
    p.write_text("always: ['5930']\n", encoding="utf-8")
    with pytest.raises(ValueError, match="always"):
        load_site_config(p)
    p.write_text(
        "max_stocks: 3\nlive: [market/summary]\n"
        "variants: [{key: etf/flows, params: {mode: out}}]\n",
        encoding="utf-8",
    )
    c = load_site_config(p)
    assert c.max_stocks == 3
    assert c.variants == (SiteRequest("etf/flows", (("mode", "out"),)),)


def test_expand_from_values_and_other_response() -> None:
    body = {"data": {"indicators": [{"key": "credit_spread"}, {"key": "deposits"}, {"x": 1}]}}
    assert harvest(body, "indicators", "key") == ["credit_spread", "deposits"]
    files = {"diag/summary.json": SiteFile("diag/summary.json", json.dumps(body).encode())}
    e = SiteExpand(
        "diag/history", "key", values=("fear_greed",), source="diag/summary",
        list="indicators", field="key", fixed=(("days", "250"),),
    )  # fmt: skip
    got = [r.file for r in expand_requests(e, files)]
    assert got == [
        "diag/history@days-250.key-fear_greed.json",
        "diag/history@days-250.key-credit_spread.json",
        "diag/history@days-250.key-deposits.json",
    ]


def test_config_expand_needs_values_or_source(tmp_path: Path) -> None:
    p = tmp_path / "site.yaml"
    p.write_text("expand: [{key: gex/metrics, param: name}]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="values"):
        load_site_config(p)


def test_repo_config_loads() -> None:
    c = load_site_config(Path(__file__).resolve().parents[3] / "config" / "site.yaml")
    assert c.live and all("{" not in k for k in c.live)
    assert {e.key for e in c.expand} >= {"diag/history", "macro/series", "flows/screen"}


def test_base_requests_skip_personal_and_auth(world: World) -> None:
    keys = [r.key for r in base_requests(world.app())]
    assert "market/summary" in keys
    assert not [k for k in keys if f"/api/{k}".startswith(SKIP)]


def test_full_site_has_pages_but_no_personal_data(world: World) -> None:
    app = world.app()
    code = world.market.universe[0].code
    files, report = build_site(app, "full", SiteConfig(max_stocks=3, always=(code,)), world.clock)
    names = {f.name for f in files}
    assert "market/summary.json" in names
    assert f"stock/{code}/header.json" in names
    assert "stock/search-index.json" in names
    assert not [
        n for n in names if n.startswith(("journal", "portfolio", "watchlist", "rules", "ops"))
    ]
    assert not [n for n in names if n.startswith(("auth", "backtest", "health"))]
    for f in files:
        assert b'"is_watch":true' not in f.body
    index = json.loads(next(f for f in files if f.name == "stock/search-index.json").body)
    assert index["source"] and index["quality"] == "ok"
    assert 1 <= report.stocks <= 3
    assert report.files == len(files) - 2  # _meta.json·_defaults.json 은 세지 않는다
    defaults = json.loads(next(f for f in files if f.name == "_defaults.json").body)
    assert defaults["stock/{code}/valuation"] == {"metric": "per"}
    assert not [k for k in defaults if f"/api/{k}".startswith(SKIP)]
    assert not [s for s in report.failed.values() if s >= 500]


def test_live_set_only_listed_keys(world: World) -> None:
    config = SiteConfig(
        live=("market/summary",), variants=(SiteRequest("etf/flows", (("mode", "out"),)),)
    )
    files, _ = build_site(world.app(), "live", config, world.clock)
    assert [f.name for f in files] == ["market/summary.json"]


def test_tar_is_deterministic(world: World) -> None:
    config = SiteConfig(live=("market/summary", "market/ribbon"))
    files, _ = build_site(world.app(), "live", config, world.clock)
    a, b = io.BytesIO(), io.BytesIO()
    write_tar(files, a)
    write_tar(list(reversed(files)), b)
    assert a.getvalue() == b.getvalue()
    with tarfile.open(fileobj=io.BytesIO(a.getvalue())) as t:
        assert sorted(t.getnames()) == sorted(f.name for f in files)

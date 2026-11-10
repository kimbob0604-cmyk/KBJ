"""공개 사이트 내보내기(site) — 파일 이름 규칙·개인 데이터 빼기·묶음(live·full). 합성 세계만."""

from __future__ import annotations

import io
import json
import tarfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest

from kbj.core.fin_rows import WatchItem
from kbj.core.time import KST
from kbj.services.api.site import (
    GRID_MAX,
    SKIP,
    Harvest,
    SiteConfig,
    SiteFile,
    SiteGrid,
    SiteRequest,
    base_requests,
    build_site,
    check_public,
    codes_in,
    grid_plan,
    grid_requests,
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
    ("filings/feed", {"date": "2026-11-10", "kind": "prelim", "limit": 1000},
     "filings/feed@date-2026-11-10.kind-prelim.limit-1000.json"),
    ("filings/insiders", {"days": 30, "min_ratio_pct": 0.1},
     "filings/insiders@days-30.min_ratio_pct-0_1.json"),
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
        "grid: [{key: etf/flows, params: {mode: [out, null], period: 5}}]\n",
        encoding="utf-8",
    )
    c = load_site_config(p)
    assert c.max_stocks == 3
    assert c.grid == (SiteGrid("etf/flows", (("mode", ("out", None)), ("period", ("5",)))),)


# ── 펼침(grid) ─────────────────────────────────────────────────────────────────────────
TODAY = date(2026, 11, 10)


def _files(**bodies: object) -> dict[str, SiteFile]:
    return {f"{k.replace('__', '/')}.json": _sf(f"{k}.json", v) for k, v in bodies.items()}


def test_grid_all_combinations_drop_null_and_api_defaults() -> None:
    """모든 조합 — null 은 인자를 빼고, API 기본값과 같은 값도 뺀다(화면이 빼고 찾는 이름과
    같게·같은 파일은 한 번)."""
    g = SiteGrid(
        "etf/flows",
        (("mode", ("in", "out")), ("type", (None, "kr_theme")), ("period", ("5", "20"))),
    )
    defaults = {"etf/flows": {"mode": "in", "period": 5, "limit": 100}}
    reqs, skipped = grid_requests(g, {}, today=TODAY, defaults=defaults)
    assert skipped == 0
    assert [r.file for r in reqs] == [
        "etf/flows.json",
        "etf/flows@period-20.json",
        "etf/flows@type-kr_theme.json",
        "etf/flows@period-20.type-kr_theme.json",
        "etf/flows@mode-out.json",
        "etf/flows@mode-out.period-20.json",
        "etf/flows@mode-out.type-kr_theme.json",
        "etf/flows@mode-out.period-20.type-kr_theme.json",
    ]
    assert reqs[3].url == "/api/etf/flows?period=20&type=kr_theme"
    # 값이 기본값과 같아 빠지면 같은 파일 — 한 번만
    dup = SiteGrid("board/us", (("basis", ("close", "close", None)),))
    reqs, _ = grid_requests(dup, {}, today=TODAY, defaults={"board/us": {"basis": "close"}})
    assert [r.file for r in reqs] == ["board/us.json"]
    # bool 기본값은 화면(String(v))처럼 소문자로 비교한다
    lev = SiteGrid("etf/map", (("lev", (None, "false", "true")),))
    reqs, _ = grid_requests(lev, {}, today=TODAY, defaults={"etf/map": {"lev": False}})
    assert [r.file for r in reqs] == ["etf/map.json", "etf/map@lev-true.json"]


def test_grid_today_tokens_are_kst_dates() -> None:
    g = SiteGrid("filings/earnings", (("from", ("@today-6", "@today")), ("to", ("@today",))))
    reqs, _ = grid_requests(g, {}, today=TODAY, defaults={})
    assert [r.file for r in reqs] == [
        "filings/earnings@from-2026-11-04.to-2026-11-10.json",
        "filings/earnings@from-2026-11-10.to-2026-11-10.json",
    ]
    # 달 경계 — 달력일(화면의 '어제'도 달력일 −1)
    feed = SiteGrid("filings/feed", (("date", ("@today", "@today-1")),))
    reqs, _ = grid_requests(feed, {}, today=date(2026, 11, 1), defaults={})
    assert [r.file for r in reqs] == [
        "filings/feed@date-2026-11-01.json",
        "filings/feed@date-2026-10-31.json",
    ]


def test_grid_values_from_other_response_and_path_params() -> None:
    """경로 인자({hs})·쿼리 인자 값을 이미 받은 응답의 목록에서 모은다(limit 개 — 목록
    앞에서부터)."""
    items = {"data": {"rows": [{"hs": "85"}, {"hs": "01"}, {"hs": "../x"}, {"hs": "27"}]}}
    files = _files(trade__items=items)
    hs = Harvest("trade/items", "rows", "hs", limit=3)
    g = SiteGrid("trade/items/{hs}/series", (("hs", (hs,)), ("flow", ("exp", "imp"))))
    defaults = {"trade/items/{hs}/series": {"flow": "exp"}}
    reqs, skipped = grid_requests(g, files, today=TODAY, defaults=defaults)
    assert [r.file for r in reqs] == [
        "trade/items/85/series.json",
        "trade/items/85/series@flow-imp.json",
        "trade/items/01/series.json",
        "trade/items/01/series@flow-imp.json",
    ]
    assert skipped == 1  # '../x' — 경로 한 마디가 아니다(결과 줄에 센다)
    assert reqs[1].url == "/api/trade/items/85/series?flow=imp"
    # 원천 응답이 없으면 그 값은 없다(고정 값과 섞은 인자는 고정 값만)
    mixed = SiteGrid(
        "etf/holdings/changes", (("issuer", (None, Harvest("x/y", "rows", "issuer"))),)
    )
    reqs, _ = grid_requests(mixed, {}, today=TODAY, defaults={})
    assert [r.file for r in reqs] == ["etf/holdings/changes.json"]
    assert harvest(items, "rows", "hs") == ["85", "01", "../x", "27"]


def test_stock_grid_fills_code() -> None:
    g = SiteGrid("stock/{code}/valuation", (("metric", ("per", "pbr")),))
    reqs, _ = grid_requests(
        g, {}, today=TODAY, defaults={"stock/{code}/valuation": {"metric": "per"}}, code="005930"
    )
    assert [r.file for r in reqs] == [
        "stock/005930/valuation.json",
        "stock/005930/valuation@metric-pbr.json",
    ]


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("variants: [{key: etf/flows, params: {mode: out}}]", "grid 로 바뀌었다"),
        ("expand: [{key: gex/metrics, param: name, values: [vex]}]", "grid 로 바뀌었다"),
        ("grid: [{key: filings/feed, params: {date: ['@yesterday']}}]", "값 토큰"),
        ("grid: [{key: 'trade/items/{hs}/series', params: {flow: [exp]}}]", "경로 자리"),
        ("grid: [{key: 'trade/items/{hs}/series', params: {hs: [null]}}]", "경로 인자 값"),
        ("grid: [{key: x, params: {a: [{source: s, list: rows, field: f, limit: 500}]}}]", "limit"),
        ("grid: [{key: x, params: {a: [{list: rows, field: f}]}}]", "source"),
        ("grid: [{key: '../x', params: {}}]", "경로 형식"),
        ("stock_grid: [{key: stock/005930/chart, params: {days: [1240]}}]", "code"),
        ("stock_grid: [{key: 'stock/{code}/chart', params: {code: ['005930']}}]", "code"),
    ],
)
def test_config_grid_errors(tmp_path: Path, text: str, match: str) -> None:
    p = tmp_path / "site.yaml"
    p.write_text(text + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        load_site_config(p)


def test_config_grid_cap(tmp_path: Path) -> None:
    """상한 — 펼침 하나의 조합(모으는 값은 limit 개로 센다)이 GRID_MAX 를 넘으면 설정을 읽을
    때 오류."""
    p = tmp_path / "site.yaml"
    big = list(range(GRID_MAX // 2 + 1))
    p.write_text(f"grid: [{{key: x, params: {{a: {big}, b: [1, 2]}}}}]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="상한"):
        load_site_config(p)
    harvested = "{source: s, list: rows, field: f, limit: 100}"
    p.write_text(
        f"grid: [{{key: x, params: {{a: [null, {harvested}], b: [1, 2]}}}}]\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="상한"):  # (1 + 100) × 2 = 202
        load_site_config(p)


def test_repo_config_loads() -> None:
    c = load_site_config(Path(__file__).resolve().parents[3] / "config" / "site.yaml")
    assert c.live and all("{" not in k for k in c.live)
    keys = {g.key for g in c.grid}
    assert keys >= {
        "diag/history", "macro/series", "flows/screen", "filings/feed", "filings/earnings",
        "etf/flows", "etf/map", "board/newhigh", "trade/items/{hs}/series",
        "trade/countries/{cc}/trend", "index-review", "themes/strength",
    }  # fmt: skip
    # 상한(수천 개로 불지 않게): 펼침마다 GRID_MAX 안, 합은 1,000 안, 종목마다 8개
    assert all(g.bound() <= GRID_MAX for g in c.grid)
    full, per_stock = grid_plan(c)
    assert full <= 1000
    assert per_stock == 8
    assert c.max_stocks * per_stock <= 2000
    # 장중(live) 펼침도 작게 — 10분마다 다시 만든다
    assert sum(g.bound() for g in c.grid if g.key in c.live) <= 100
    # 날짜 버튼(오늘·어제)과 화면이 고정해 부르는 인자
    feed = next(g for g in c.grid if g.key == "filings/feed")
    assert dict(feed.params)["date"] == ("@today", "@today-1")
    assert dict(feed.params)["limit"] == ("1000",)
    screen = next(g for g in c.grid if g.key == "flows/screen")
    assert dict(screen.params)["min_avg_turnover"] == ("30000000000",)


def test_base_requests_skip_personal_and_auth(world: World) -> None:
    keys = [r.key for r in base_requests(world.app())]
    assert "market/summary" in keys
    assert not [k for k in keys if f"/api/{k}".startswith(SKIP)]


GRIDS = (
    SiteGrid("etf/flows", (("mode", ("in", "out")), ("period", ("5", "20")))),
    SiteGrid("filings/feed", (("date", ("@today", "@today-1")), ("limit", ("1000",)))),
)
STOCK_GRIDS = (
    SiteGrid("stock/{code}/valuation", (("metric", ("pbr", "psr")),)),
    SiteGrid("stock/{code}/financials", (("basis", ("a",)), ("n", ("4",)))),
)


def test_full_site_has_pages_but_no_personal_data(world: World) -> None:
    app = world.app()
    code = world.market.universe[0].code
    world.repos.journal.replace_watchlist(
        "main",
        [WatchItem("main", code, None, world.clock(), 0)],
        loaded_by="test",
        now=world.clock(),
    )
    config = SiteConfig(max_stocks=3, always=(code,), grid=GRIDS, stock_grid=STOCK_GRIDS)
    files, report = build_site(app, "full", config, world.clock)
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
    assert 1 <= report.stocks <= 3
    assert report.files == len(files) - 2  # _meta.json·_defaults.json 은 세지 않는다
    defaults = json.loads(next(f for f in files if f.name == "_defaults.json").body)
    assert defaults["stock/{code}/valuation"] == {"metric": "per"}
    assert not [k for k in defaults if f"/api/{k}".startswith(SKIP)]
    assert not [s for s in report.failed.values() if s >= 500]
    # 펼침: 버튼 조합(기본값 파일은 인자 없이 한 번)·오늘·어제(KST)·종목마다 변형
    today = world.clock().astimezone(KST).date()
    assert {"etf/flows@mode-out.json", "etf/flows@period-20.json"} <= names
    assert "etf/flows@mode-in.json" not in names
    assert f"filings/feed@date-{today.isoformat()}.limit-1000.json" in names
    assert f"filings/feed@date-{(today - timedelta(days=1)).isoformat()}.limit-1000.json" in names
    stocks = sorted(n.split("/")[1] for n in names if n.endswith("/header.json"))
    for c in stocks:
        assert f"stock/{c}/valuation@metric-pbr.json" in names
        assert f"stock/{c}/financials@basis-a.n-4.json" in names


def test_full_site_stock_index_is_every_stock_with_detail_flags(world: World) -> None:
    """전 종목 목록 — 상세 수(max_stocks)보다 많은 줄, 상세 파일이 있는 종목만 detail,
    관심종목 표시는 늘 false."""
    app = world.app()
    code = world.market.universe[0].code
    world.repos.journal.replace_watchlist(
        "main",
        [WatchItem("main", code, None, world.clock(), 0)],
        loaded_by="test",
        now=world.clock(),
    )
    files, report = build_site(app, "full", SiteConfig(max_stocks=2, always=(code,)), world.clock)
    names = {f.name for f in files}
    index = json.loads(next(f for f in files if f.name == "stock/search-index.json").body)
    data = index["data"]
    assert index["source"] and index["quality"] == "ok"
    assert data["complete"] is True and data["q"] == ""
    assert data["n_total"] == len(data["items"]) > report.stocks
    assert {i["code"] for i in data["items"]} >= {u.code for u in world.market.universe}
    detail = {i["code"] for i in data["items"] if i["detail"]}
    assert detail == {n.split("/")[1] for n in names if n.endswith("/header.json")}
    assert code in detail and len(detail) == report.stocks
    assert all(i["is_watch"] is False for i in data["items"])
    assert index["notes"] == [
        f"전 종목 {data['n_total']}개 — 상세는 {report.stocks}종목만(화면에 자주 나온 순)"
    ]


def _sf(name: str, body: object) -> SiteFile:
    return SiteFile(name, json.dumps(body).encode())


def test_check_public_blocks_personal_and_secret_like() -> None:
    env = {"source": "KRX", "as_of": "2026-11-10T15:30:00+09:00", "quality": "ok", "data": {}}
    ok = [
        _sf("market/summary.json", {**env, "data": {"session": "regular", "session_open": True}}),
        _sf("_meta.json", {"set": "full"}),
        _sf("stock/search-index.json", {**env, "data": {"items": [{"is_watch": False}]}}),
        _sf("board/us.json", {"code": "no_data", "message": "아직 없음 — KBJ_KRX_API_KEY 없음"}),
    ]
    assert check_public(ok) == []
    bad = [
        _sf("journal/trades.json", env),
        _sf("watchlist.json", env),
        _sf("market/ribbon.json", {**env, "data": {"items": [{"is_watch": True}]}}),
        _sf("market/sectors.json", {**env, "data": {"app_key": "x"}}),
        _sf("flows/intraday.json", {**env, "notes": ["Bearer abc.def"]}),
        _sf("../x.json", env),
    ]
    got = check_public(bad)
    assert len(got) == len(bad)
    assert all("abc.def" not in g for g in got)  # 값은 문구에 싣지 않는다


def test_full_site_passes_public_check(world: World) -> None:
    files, _ = build_site(world.app(), "full", SiteConfig(max_stocks=2), world.clock)
    assert check_public(files) == []


def test_live_set_only_listed_keys(world: World) -> None:
    """live — 목록의 경로와 그 경로의 펼침만(다른 경로의 펼침은 full 때만)."""
    config = SiteConfig(
        live=("market/sectors",),
        grid=(
            SiteGrid("market/sectors", (("period", ("1", "5", "20")),)),
            SiteGrid("etf/flows", (("mode", ("out",)),)),
        ),
    )
    files, _ = build_site(world.app(), "live", config, world.clock)
    assert sorted(f.name for f in files) == [
        "market/sectors.json",
        "market/sectors@period-20.json",
        "market/sectors@period-5.json",
    ]


def test_grid_config_mistakes_are_reported_not_fetched(world: World) -> None:
    """오타·개인 경로·모르는 인자는 부르지 않고 결과의 실패에 남긴다(설정 오류를 삼키지 않는다)."""
    config = SiteConfig(
        live=("market/sectors", "watchlist", "no/such"),
        grid=(
            SiteGrid("market/sectors", (("perod", ("5",)),)),
            SiteGrid("watchlist", (("list", ("main",)),)),
            SiteGrid("no/such", (("a", ("1",)),)),
        ),
    )
    files, report = build_site(world.app(), "live", config, world.clock)
    assert [f.name for f in files] == ["market/sectors.json"]
    assert sorted(report.failed) == [
        "market/sectors (설정 — 모르는 인자 perod)",
        "no/such (설정 — 내보내는 GET 경로 아님)",
        "watchlist (설정 — 내보내는 GET 경로 아님)",
    ]


def test_tar_is_deterministic(world: World) -> None:
    config = SiteConfig(live=("market/summary", "market/ribbon"))
    files, _ = build_site(world.app(), "live", config, world.clock)
    a, b = io.BytesIO(), io.BytesIO()
    write_tar(files, a)
    write_tar(list(reversed(files)), b)
    assert a.getvalue() == b.getvalue()
    with tarfile.open(fileobj=io.BytesIO(a.getvalue())) as t:
        assert sorted(t.getnames()) == sorted(f.name for f in files)

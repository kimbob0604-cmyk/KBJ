"""P6 픽스처 `web/test/fixtures/api/trade_explore/**` — 묶음 EXP 의 응답(/api/trade/items*·
countries*·regions·surges·items104)과 공개 파일(`trade/hs2.json`·`trade/hs/<장>.json`·
`trade/countries.json`·`trade/country/<ISO2>.json`·`trade/region/<hs6>.json`·`trade/surge.json`·
`trade/items104.json` — 고른 몇 개 + 첫 지역 파일).

합성 세계(`tests/fixtures/synthetic/p6_world.py` — 고정 시드, 실데이터 아님)를 메모리 저장소에 싣고
`trade.derive` 를 한 번 돌린 뒤(급등·104품목), 가짜 시계(세계의 CLOCK)에서 앱 응답을 `{상대 경로:
JSON 글자}` 로 낸다(결정적). 공개 파일은 같은 저장소를 SQL 과 같은 뜻으로 읽는 메모리
읽기(`memory_explore_reader`)로 만든다 — `tests/unit/public_export/pub_rows.py` 의 `MemReader` 도 이
함수를 쓴다(같은 뜻의 메모리 읽기는 한 곳).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC
from typing import Any, Final

from kbj.services.engine.trade import JOB, derive
from kbj.services.public_export.pub_read import Query, Reader
from kbj.services.public_export.trade_explore_json import build_trade_explore
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos import memory_repos
from kbj.store.repos.trade import MemoryTradeRepo
from tests.fixtures.synthetic.p6_world import CLOCK, LAST_YM, build_world
from tests.unit.api.api_world import FakeClock
from tests.unit.api.web_fixtures_p6.flash import FlashApiWorld

__all__ = [
    "DOMAIN",
    "FILES",
    "PUBLIC_FILES",
    "explore_api_world",
    "memory_explore_reader",
    "render",
    "run_derive",
]

DOMAIN: Final = "explore"
# (파일, 경로) — 로그인 API
FILES: Final[tuple[tuple[str, str], ...]] = (
    ("items_l2_exp.json", "/api/trade/items?level=2&flow=exp"),
    ("items_l2_imp.json", "/api/trade/items?level=2&flow=imp"),
    ("items_l4_85.json", "/api/trade/items?level=4&parent=85&flow=exp"),
    ("items_l4_89.json", "/api/trade/items?level=4&parent=89&flow=exp"),
    ("items_l6_8501.json", "/api/trade/items?level=6&parent=8501&flow=exp"),
    ("items_l10_850110.json", "/api/trade/items?level=10&parent=850110&flow=exp"),
    ("series_85.json", "/api/trade/items/85/series?flow=exp"),
    ("series_8502.json", "/api/trade/items/8502/series?flow=exp"),
    ("series_8901.json", "/api/trade/items/8901/series?flow=exp"),
    ("item_countries_8502.json", "/api/trade/items/8502/countries?flow=exp"),
    ("countries.json", "/api/trade/countries"),
    ("country_items_US.json", "/api/trade/countries/US/items?flow=exp"),
    ("regions.json", "/api/trade/regions"),
    ("surges.json", "/api/trade/surges"),
    ("items104.json", "/api/trade/items104"),
)
# 공개 파일 중 픽스처로 싣는 것(전부 싣으면 국가·지역 파일이 수십 개 — 화면 시험에 필요한 것만)
PUBLIC_FILES: Final[tuple[str, ...]] = (
    "trade/hs2.json",
    "trade/hs/85.json",
    "trade/hs/89.json",
    "trade/countries.json",
    "trade/country/US.json",
    "trade/surge.json",
    "trade/items104.json",
)


def run_derive(repo: MemoryTradeRepo) -> None:
    """`trade.derive` 를 세계의 마지막 기준 월로 한 번(급등·104품목 행)."""
    res = derive(
        JobContext(
            job=JOB,
            as_of=LAST_YM,
            run_id="fixture",
            attempt=1,
            now=CLOCK.astimezone(UTC),
            resources={"trade_repo": repo},
        )
    )
    if res.status != "ok":
        raise RuntimeError(f"trade.derive: {res.status} {dict(res.detail)}")


def _rows(rows: list[Any], cols: tuple[str, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        d = {c: getattr(r, c) for c in cols}
        d["quality"] = r.quality.value
        out.append(d)
    return out


_ITEM: Final = ("ym", "hs", "exp_usd", "exp_kg", "imp_usd", "imp_kg", "as_of")
_COUNTRY: Final = ("ym", "country", "country_name", "exp_usd", "imp_usd", "bal_usd", "as_of")
_IC: Final = ("ym", "country", "hs", "exp_usd", "exp_kg", "as_of")
_SGG: Final = ("ym", "sido_cd", "sgg_nm", "hs6", "region_id", "exp_usd", "as_of")
_SURGE: Final = ("ym", "level", "key", "flow", "rule", "yoy", "yoy_3m", "usd", "rank", "as_of")
_GROUP: Final = ("ym", "group_id", "exp_usd", "yoy", "unit_price", "mode", "as_of")


def memory_explore_reader(trade: MemoryTradeRepo) -> Reader:
    """`trade_explore_json` 의 Query 이름에 SQL 과 같은 뜻으로 답하는 메모리 읽기."""

    def last(table: str) -> list[dict[str, Any]]:
        return [{"ym": trade.last_ym(table)}]

    def read(q: Query, p: Mapping[str, object]) -> list[dict[str, Any]]:
        n = q.name
        if n == "trade_x_item_last":
            return last("pub_trade.item_monthly")
        if n == "trade_x_chapters":
            chs = {r.hs[:2] for r in trade.item_monthly(ym_from=str(p["ym_from"]))}
            return [{"ch": c} for c in sorted(chs)]
        if n == "trade_x_items":
            rows = trade.item_monthly(prefix=str(p["ch"]), ym_from=str(p["ym_from"]))
            return _rows(rows, _ITEM)
        if n == "trade_x_hs_dim":
            return [{"hs": d.hs, "name_ko": d.name_ko} for d in trade.hs_dim() if d.hs_len <= 6]
        if n == "trade_x_country_last":
            return last("pub_trade.country_monthly")
        if n == "trade_x_countries":
            yms = {str(p["ym"]), str(p["ym_prev"])}
            return _rows([r for r in trade.country_monthly() if r.ym in yms], _COUNTRY)
        if n == "trade_x_item_country_last":
            return last("pub_trade.item_country_monthly")
        if n == "trade_x_item_countries":
            yms = {str(p["ym"]), str(p["ym_prev"])}
            return _rows([r for r in trade.item_country_monthly() if r.ym in yms], _IC)
        if n == "trade_x_sigungu_last":
            return last("pub_trade.sigungu_monthly")
        if n == "trade_x_sigungu":
            yms = {str(p["ym"]), str(p["ym_prev"])}
            return _rows([r for r in trade.sigungu_monthly() if r.ym in yms], _SGG)
        if n == "trade_x_surge_last":
            return last("pub_trade.surge")
        if n == "trade_x_surge":
            return _rows(trade.surge(ym=str(p["ym"])), _SURGE)
        if n == "trade_x_group_last":
            return last("pub_trade.item_group_monthly")
        if n == "trade_x_groups":
            ym = str(p["ym"])
            return _rows(trade.item_group_monthly(ym_from=ym, ym_to=ym), _GROUP)
        raise KeyError(f"모르는 Query: {n}")

    return read


def explore_api_world(*, empty: bool = False) -> FlashApiWorld:
    """합성 세계 + trade.derive 를 실은 앱 세계(empty = 빈 저장소 — 404 시험)."""
    repos = memory_repos()
    w = None if empty else build_world()
    if w is not None:
        w.fill(repos.trade)
        run_derive(repos.trade)
    return FlashApiWorld(w, repos, FakeClock(CLOCK))


def _dump(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def render() -> dict[str, str]:
    """도메인 폴더 기준 상대 경로 → JSON 글자(결정적 — 가짜 시계·고정 시드)."""
    aw = explore_api_world()
    c = aw.authed()
    out: dict[str, str] = {}
    for name, path in FILES:
        r = c.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        out[name] = _dump(r.json())
    empty = explore_api_world(empty=True).authed().get("/api/trade/items104")
    if empty.status_code != 404:
        raise RuntimeError(f"빈 저장소 items104: HTTP {empty.status_code}")
    out["no_data_items104.json"] = _dump(empty.json())
    reader = memory_explore_reader(aw.repos.trade)
    built = {f.name: f for f in build_trade_explore(reader, now=CLOCK).files}
    first_region = sorted(n for n in built if n.startswith("trade/region/"))[:1]
    for name in (*PUBLIC_FILES, *first_region):
        out[f"public/{name}"] = _dump(json.loads(built[name].render(CLOCK)))
    return out

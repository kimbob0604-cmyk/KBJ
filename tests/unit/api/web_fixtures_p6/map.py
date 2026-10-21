"""P6 픽스처 `web/test/fixtures/api/trade_map/**` — 묶음 MAP 의 응답
(/api/trade/stocks·/api/trade/stock/{code})과 공개
파일(`trade/stocks.json`·`trade/stock/<코드>.json` 몇 개).

합성 세계(`tests/fixtures/synthetic/p6_world.py` — 고정 시드, 실데이터 아님)를 메모리 저장소에 싣고
실제 처리기 `trade.stock_index` 를 세계 시계로 돌린 뒤 앱 응답을 `{상대 경로: JSON 글자}` 로 낸다
(결정적).

`map_api_world()` 는 MAP 시험(작업·리더·공개 내보내기)이 같이 쓴다:
- 세계의 품목·국가·시군구·매핑(공개 30 + local 3)을 trade 저장소에,
- 분기 매출(세계의 `revenue` — p4_world 재사용)을 fin 저장소에(합성 고유번호 `99` + 숫자 6자리),
  고유번호 표(filings `corp_codes`)에는 매출이 있는 종목만(없으면 `no_revenue`).
- 세계의 매핑에는 `p6_expect.yaml status` 의 no_revenue(994015)·short_overlap(992059) 종목이 없다
  (FND 세계의 빈칸 — 보고함). 여기서 그 둘의 **공개** 매핑 줄(`EXTRA`)을 덧붙여 네 상태가 다 나오게
  한다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, timedelta
from typing import Any, Final

from kbj.config.trade import load_trade
from kbj.core.fin_rows import QuarterRow
from kbj.core.quality import Quality
from kbj.core.trade_rows import StockMapRow
from kbj.services.engine.trade_map import JOB_INDEX, run_stock_index
from kbj.services.public_export.pub_read import Query, Reader
from kbj.services.public_export.trade_stock_json import build_trade_stock
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import memory_repos
from kbj.store.repos.memory import MemoryRepos
from tests.fixtures.synthetic.p6_world import CLOCK, MAP_VERSION, P6World, build_world
from tests.unit.api.api_world import FakeClock
from tests.unit.api.web_fixtures_p6.flash import FlashApiWorld

__all__ = [
    "DOMAIN",
    "EXTRA",
    "STOCK_FILES",
    "corp_of",
    "fill_map_world",
    "map_api_world",
    "memory_stock_reader",
    "render",
    "run_index",
]

DOMAIN: Final = "map"
# 세계 매핑에 없는 심은 상태 종목 — (코드, HS 접두). 접두는 세계에 있는 장(85)
EXTRA: Final[tuple[tuple[str, str], ...]] = (("994015", "85"), ("992059", "85"))
# 픽스처로 싣는 종목(상태 네 가지 + local 하나 — 화면 시험용). 나머지는 stocks.json 랭킹에만
STOCK_FILES: Final[tuple[str, ...]] = ("992004", "992060", "994015", "992059", "992011")


def _quarter(period: str) -> tuple[date, date]:
    y, q = int(period[:4]), int(period[5])
    start = date(y, 3 * (q - 1) + 1, 1)
    nxt = date(y + (q == 4), (3 * q) % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def corp_of(code: str) -> str:
    """합성 고유번호(8자리 숫자 — 영숫자 코드는 숫자로 바꾼다)."""
    digits = "".join(c if c.isdigit() else str(ord(c) % 10) for c in code)
    return f"99{digits}"


def _extra_rows(w: P6World) -> list[StockMapRow]:
    names = dict(w.stocks)
    return [
        StockMapRow(
            code=code, hs=hs, flow="exp", region_id="", name=names[code], weight=0.3,
            origin="public", reviewed=False, map_version=MAP_VERSION, source="KBJ:trade_map",
            as_of=w.clock, quality=Quality.OK, countries=(),
            evidence=({"kind": "synthetic", "note": "합성 근거(실측 아님)"},),
        )
        for code, hs in EXTRA
    ]  # fmt: skip


def fill_map_world(repos: MemoryRepos, w: P6World) -> None:
    w.fill(repos.trade)
    pub = [r for r in w.stock_map if r.origin == "public"] + _extra_rows(w)
    repos.trade.replace_stock_map(pub, origin="public", loaded_by="p6_world", received_at=w.clock)
    codes = {r.code for r in w.stock_map} | {c for c, _ in EXTRA}
    names = dict(w.stocks)
    quarters: list[QuarterRow] = []
    for code in sorted(codes):
        rev = w.revenue(code)
        if not rev:
            continue
        corp = corp_of(code)
        repos.filings.corp_codes[code] = (corp, names[code])
        for period, value in rev:
            start, end = _quarter(period)
            quarters.append(
                QuarterRow(
                    corp_code=corp, fiscal_year=start.year, fiscal_q=int(period[5]),
                    fs_div="CFS", period_start=start, period_end=end,
                    rcept_dt=end + timedelta(days=45), tier="main", revenue=value,
                )
            )  # fmt: skip
    repos.fin.upsert_quarters(quarters, loaded_by="p6_world", now=w.clock)


def run_index(repos: MemoryRepos) -> JobResult:
    """실제 처리기 `trade.stock_index` 를 세계 시계로 한 번."""
    ctx = JobContext(
        job=JOB_INDEX, as_of=CLOCK.date().isoformat(), run_id="fixture", attempt=1,
        now=CLOCK.astimezone(UTC),
    )  # fmt: skip
    res = run_stock_index(repos.trade, repos.fin, repos.filings, ctx, cfg=load_trade())
    if res.status != "ok":
        raise RuntimeError(f"trade.stock_index: {res.status} {dict(res.detail)}")
    return res


def map_api_world(*, empty: bool = False, index: bool = True) -> FlashApiWorld:
    """합성 세계 + 매출 + trade.stock_index 를 실은 앱 세계(empty = 빈 저장소 — 404 시험)."""
    repos = memory_repos()
    w = None if empty else build_world()
    if w is not None:
        fill_map_world(repos, w)
        if index:
            run_index(repos)
    return FlashApiWorld(w, repos, FakeClock(CLOCK))


_MAP_COLS: Final = (
    "code", "hs", "flow", "region_id", "name", "weight", "origin", "reviewed", "map_version",
    "countries", "evidence", "as_of",
)  # fmt: skip
_EXP_COLS: Final = (
    "code", "period", "export_usd", "yoy", "revenue_yoy", "status", "map_version", "lag_corr",
    "as_of",
)  # fmt: skip


def memory_stock_reader(repos: MemoryRepos) -> Reader:
    """`trade_stock_json` 의 Query 이름에 SQL 과 같은 뜻으로 답하는 메모리 읽기(공개 줄만)."""

    def rows(objs: list[Any], cols: tuple[str, ...]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in objs:
            d = {c: getattr(r, c) for c in cols}
            if "countries" in d:
                d["countries"] = list(d["countries"])
            if "evidence" in d:
                d["evidence"] = [dict(e) for e in d["evidence"]]
            if "lag_corr" in d:
                d["lag_corr"] = dict(d["lag_corr"])
            d["quality"] = r.quality.value
            out.append(d)
        return out

    def read(q: Query, p: Mapping[str, object]) -> list[dict[str, Any]]:
        del p
        if q.name == "trade_s_map":
            return rows(repos.trade.stock_map(origin="public"), _MAP_COLS)
        if q.name == "trade_s_export":
            public = {r.code for r in repos.trade.stock_map(origin="public")}
            got = [r for r in repos.trade.stock_export() if r.code in public]
            return rows(got, _EXP_COLS)
        raise KeyError(f"모르는 Query: {q.name}")

    return read


def _dump(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def render() -> dict[str, str]:
    """도메인 폴더 기준 상대 경로 → JSON 글자(결정적 — 가짜 시계·고정 시드)."""
    aw = map_api_world()
    c = aw.authed()
    out: dict[str, str] = {}
    r = c.get("/api/trade/stocks")
    if r.status_code != 200:
        raise RuntimeError(f"/api/trade/stocks: HTTP {r.status_code}")
    out["stocks.json"] = _dump(r.json())
    for code in STOCK_FILES:
        r = c.get(f"/api/trade/stock/{code}")
        if r.status_code != 200:
            raise RuntimeError(f"/api/trade/stock/{code}: HTTP {r.status_code}")
        out[f"stock_{code}.json"] = _dump(r.json())
    for code in STOCK_FILES[:2]:
        r = c.get(f"/api/trade/stock/{code}/flows")
        if r.status_code != 200:
            raise RuntimeError(f"/api/trade/stock/{code}/flows: HTTP {r.status_code}")
        out[f"flows_{code}.json"] = _dump(r.json())
        # P10 단계 3 — 지수 없는 종목은 404(파일 없음)
        r = c.get(f"/api/trade/stock/{code}/preview")
        if r.status_code not in (200, 404):
            raise RuntimeError(f"/api/trade/stock/{code}/preview: HTTP {r.status_code}")
        if r.status_code == 200:
            out[f"preview_{code}.json"] = _dump(r.json())
    for name, path in (  # P10 단계 4 — 국가 품목 추이·산업(로그인)
        ("trend_US.json", "/api/trade/countries/US/trend"),
        ("sectors.json", "/api/trade/sectors"),
    ):
        r = c.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        out[name] = _dump(r.json())
    nomap = c.get("/api/trade/stock/994010")
    if nomap.status_code != 404:
        raise RuntimeError(f"매핑 없는 종목: HTTP {nomap.status_code}")
    out["no_map_994010.json"] = _dump(nomap.json())
    built = {f.name: f for f in build_trade_stock(memory_stock_reader(aw.repos), now=CLOCK).files}
    for name in ("trade/stocks.json", *(f"trade/stock/{s}.json" for s in STOCK_FILES[:2])):
        out[f"public/{name}"] = _dump(built[name].envelope(CLOCK))
    return out

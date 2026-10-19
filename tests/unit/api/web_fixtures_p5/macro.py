"""P5 픽스처 `web/test/fixtures/api/macro/**` — 매크로·경제 캘린더·상단 띠 P5 칩(MAC).

합성 세계(`tests/fixtures/synthetic/p5_world.py` — 고정 시드·가짜 시계 `CLOCK`)를 P3 API 세계의
메모리
저장소에 올리고(`load_macro`) 로그인 API 응답을 `{상대 경로: JSON 글자}` 로 낸다(결정적). 공개 파일
모양(`macro/*.json`·`credit.json` — 공개 내보내기)은 `public/` 아래에 같은 세계로 만든다(공개 행만).

p5_world 금투협 항목 이름은 수집기 이름표(`kbj.services.collectors.kofia.ITEMS` — `loan_kospi`·
`loan_kosdaq`·`investor_deposit`)와 같다(WRAP 이 p5_world 를 맞췄다).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, date
from typing import Any, Final, Protocol

from kbj.services.public_export.credit_json import build_credit
from kbj.services.public_export.macro_json import build_macro
from kbj.services.public_export.manifest import PublicFile
from kbj.services.public_export.pub_read import Query
from kbj.store.repos import MemoryRepos
from kbj.store.repos.macro import MemoryMacroPublicRepo
from kbj.store.repos.market_stats import MemoryMarketStatsRepo
from tests.fixtures.synthetic.p5_world import CLOCK, P5World, build_world

__all__ = ["DOMAIN", "load_macro", "memory_pub_reader", "render"]

DOMAIN: Final = "macro"
LOADED_BY: Final = "web-fixtures"
# (파일, 경로, 기대 상태) — 고용·가계 탭은 합성 세계에 값이 없어 404 no_data('아직 없음')
ROUTES: Final[tuple[tuple[str, str, int], ...]] = (
    ("tabs.json", "/api/macro/tabs", 200),
    ("series_rates.json", "/api/macro/series?tab=rates", 200),
    ("series_prices.json", "/api/macro/series?tab=prices", 200),
    ("series_us.json", "/api/macro/series?tab=us", 200),
    ("series_market.json", "/api/macro/series?tab=market", 200),
    ("series_labor_nodata.json", "/api/macro/series?tab=labor", 404),
    ("series_one_dgs10.json", "/api/macro/series/FRED:DGS10?from=2026-10-01", 200),
    ("calendar.json", "/api/macro/calendar", 200),
    ("ribbon.json", "/api/market/ribbon", 200),
    # 페이지 1 '신용잔고 · 예탁금 · 펀드 자금' 패널(금투협 — 같은 세계의 market_stats)
    ("market_credit.json", "/api/market/credit", 200),
)
Reader = Callable[[Query, Mapping[str, object]], list[dict[str, Any]]]


def load_macro(repos: MemoryRepos, w: P5World) -> None:
    """p5_world 매크로·캘린더·금투협 통계 → 메모리 저장소(공개는 pub, 로그인은 prv)."""
    now = CLOCK
    repos.macro.upsert_meta(
        [m for m in w.macro_meta if m.tier == "public"], loaded_by=LOADED_BY, now=now
    )
    repos.macro_prv.upsert_meta(
        [m for m in w.macro_meta if m.tier == "private"], loaded_by=LOADED_BY, now=now
    )
    repos.macro.upsert_series(w.macro_rows("public"), loaded_by=LOADED_BY, received_at=now)
    repos.macro_prv.upsert_series(w.macro_rows("private"), loaded_by=LOADED_BY, received_at=now)
    repos.macro.upsert_calendar(
        [e for e in w.calendar if e.is_public], loaded_by=LOADED_BY, received_at=now
    )
    repos.macro_prv.upsert_calendar(
        [e for e in w.calendar if not e.is_public], loaded_by=LOADED_BY, received_at=now
    )
    repos.market_stats.upsert(list(w.market_stats), loaded_by=LOADED_BY, received_at=now)


class _PubLike(Protocol):
    """공개 저장소 묶음(`MemoryRepos`·`MemoryPublicRepos`)의 매크로·금투협 부분."""

    @property
    def macro(self) -> MemoryMacroPublicRepo: ...

    @property
    def market_stats(self) -> MemoryMarketStatsRepo: ...


def memory_pub_reader(repos: _PubLike) -> Reader:
    """공개 내보내기 `Reader` 와 같은 뜻으로 답하는 메모리 읽기(macro_json·credit_json 의 Query
    만)."""

    def rows_of(sid: str, start: str) -> list[dict[str, Any]]:
        return [
            {
                "series_id": r.series_id,
                "period": r.period,
                "value": r.value,
                "quality": r.quality.value,
                "as_of": r.as_of,
                "released_at": r.released_at,
            }
            for r in repos.macro.series([sid], start).get(sid, [])
        ]

    def two(a: str, b: str, start: str, *, yoy: bool) -> list[dict[str, Any]]:
        allb = {r["period"]: r for r in rows_of(b, "0000")}
        out: list[dict[str, Any]] = []
        for x in rows_of(a, start):
            if yoy and len(x["period"]) != 7:
                continue
            key = f"{int(x['period'][:4]) - 1:04d}{x['period'][4:]}" if yoy else x["period"]
            y = allb.get(key)
            if y is None:
                continue
            if yoy:
                ok = x["value"] is not None and bool(y["value"])
                v = (x["value"] / y["value"] - 1) * 100 if ok else None
            else:
                xv, yv = x["value"], y["value"]
                v = xv - yv if xv is not None and yv is not None else None
            out.append(
                {
                    "period": x["period"],
                    "value": v,
                    "qa": x["quality"],
                    "qb": y["quality"],
                    "as_of": max(x["as_of"], y["as_of"]),
                }
            )
        return out

    def read(q: Query, p: Mapping[str, object]) -> list[dict[str, Any]]:
        if q.name == "macro_meta":
            return [
                {
                    "series_id": m.series_id,
                    "tab": m.tab,
                    "title": m.title,
                    "unit": m.unit,
                    "freq": m.freq,
                    "source": m.source,
                    "dataset": m.dataset,
                }
                for m in repos.macro.meta()
            ]
        if q.name == "macro_series":
            return rows_of(str(p["sid"]), str(p["start"]))
        if q.name == "macro_spread":
            return two(str(p["a"]), str(p["b"]), str(p["start"]), yoy=False)
        if q.name == "macro_yoy":
            return two(str(p["a"]), str(p["a"]), str(p["start"]), yoy=True)
        if q.name == "macro_calendar":
            a, b = p["start"], p["end"]
            assert isinstance(a, date) and isinstance(b, date)
            return [
                {
                    "event_id": e.event_id,
                    "kind": e.kind,
                    "event_date": e.date,
                    "time_kst": e.time_kst,
                    "title": e.title,
                    "country": e.country,
                    "importance": e.importance,
                    "actual": e.actual,
                    "actual_series": e.actual_series,
                    "unit": e.unit,
                    "source": e.source,
                    "quality": e.quality.value,
                    "received_at": CLOCK,
                }
                for e in repos.macro.calendar(a, b)
            ]
        if q.name == "credit_series":
            return [
                r
                for sid in (str(p["a"]), str(p["b"]))
                for r in rows_of(sid, str(p["start"]))
                if r["value"] is not None
            ]
        if q.name in ("credit_balance", "credit_capital"):
            stat = "credit_balance" if q.name == "credit_balance" else "market_capital"
            start = p["start"]
            assert isinstance(start, date)
            return [
                {
                    "stat_date": r.date,
                    "item": r.item,
                    "value": r.value,
                    "unit": r.unit,
                    "quality": r.quality.value,
                    "as_of": r.as_of,
                }
                for r in repos.market_stats.series(stat, start=start)
                if r.value is not None
                and (stat == "credit_balance" or r.item == "investor_deposit")
            ]
        raise KeyError(f"모르는 Query: {q.name}")

    return read


def _dump(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def _public(f: PublicFile) -> tuple[str, str]:
    return f"public/{f.name}", _dump(json.loads(f.render(CLOCK)))


def render() -> dict[str, str]:
    """도메인 폴더 기준 상대 경로 → JSON 글자(결정적)."""
    from tests.unit.api.api_world import build_world as build_api_world

    api = build_api_world()
    api.clock.set(CLOCK.astimezone(UTC))
    load_macro(api.repos, build_world())
    client = api.client()
    api.login(client)
    out: dict[str, str] = {}
    for rel, path, status in ROUTES:
        r = client.get(path)
        assert r.status_code == status, (path, r.text[:200])
        out[rel] = _dump(r.json())
    read = memory_pub_reader(api.repos)
    for f in (*build_macro(read, now=CLOCK).files, *build_credit(read, now=CLOCK).files):
        k, v = _public(f)
        out[k] = v
    return out

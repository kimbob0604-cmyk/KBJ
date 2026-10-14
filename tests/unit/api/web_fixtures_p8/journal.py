"""P8 `journal` 프런트 시험 픽스처 — `web/test/fixtures/api/journal/**`(docs/p8_design.md §1.5·§6,
묶음 JNL — 분석 일지·매매·포트폴리오·상관·추천 성과·관심종목 쓰기 응답).

API 시험 세계(`tests.unit.api.api_world.build_world` — 고정 시드·가짜 시계)에 합성 종목 셋
(990001~990003, 260 거래일 일봉 — 시드 고정 난수)을 더하고, 실제 쓰기 라우트(세션 + CSRF)로 일지 2·
매매 4·관심종목 3을 넣은 뒤 읽기 응답을 `{상대 경로: JSON 글자}` 로 낸다(결정적). 추천 이력은 이관
저장소 함수로 2건. 빈 세계 응답(`*_empty.json`)도 같이 낸다.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Final

from fastapi.testclient import TestClient

from kbj.core.quality import Quality
from kbj.core.rows import Bar, UniverseRow
from kbj.core.rules_rows import PickRow
from kbj.services.api.auth import CSRF_HEADER
from tests.unit.api.api_world import BASE, World, build_world

__all__ = ["CODES", "DOMAIN", "FILES", "add_market", "render"]

DOMAIN: Final = "journal"
CODES: Final = ("990001", "990002", "990003")
NAMES: Final = {"990001": "합성전자", "990002": "합성화학", "990003": "합성기계"}
# (파일, 경로) — 쓰기 뒤 읽기
FILES: Final[tuple[tuple[str, str], ...]] = (
    ("analysis.json", "/api/journal/analysis"),
    ("analysis_1.json", "/api/journal/analysis/1"),
    ("analysis_1_markdown.json", "/api/journal/analysis/1/markdown"),
    ("prefill_990002.json", "/api/journal/analysis/prefill/990002"),
    ("trades.json", "/api/journal/trades"),
    ("portfolio.json", "/api/portfolio"),
    ("corr_watchlist_60.json", "/api/portfolio/correlation?set=watchlist&n=60"),
    ("corr_watchlist_120.json", "/api/portfolio/correlation?set=watchlist&n=120"),
    ("corr_holdings_60.json", "/api/portfolio/correlation?set=holdings&n=60"),
    ("picks.json", "/api/journal/picks"),
    ("watchlist.json", "/api/watchlist?list=main"),
)
EMPTY: Final[tuple[tuple[str, str], ...]] = (
    ("analysis_empty.json", "/api/journal/analysis"),
    ("trades_empty.json", "/api/journal/trades"),
    ("portfolio_empty.json", "/api/portfolio"),
    ("picks_empty.json", "/api/journal/picks"),
    ("corr_empty.json", "/api/portfolio/correlation?set=watchlist&n=60"),
)


def _weekdays(end: date, n: int) -> list[date]:
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def add_market(w: World) -> list[date]:
    """합성 종목 셋의 260 거래일 일봉·유니버스를 더한다(종목마다 시드 고정)."""
    end = w.market.last_day
    days = _weekdays(end, 260)
    rows: list[Bar] = []
    for k, code in enumerate(CODES):
        r = random.Random(f"kbj-p8-journal-{code}")  # noqa: S311 — 합성 값
        px = 10_000.0 * (k + 1)
        for d in days:
            px *= 1 + r.gauss(0.0005, 0.02)
            c = float(round(px))
            rows.append(Bar(code, d, c, c, c, c, 1000, int(c * 1000), "KRX", "KRX", Quality.OK))
    w.repos.market.upsert_daily_bars(rows, loaded_by="fixture")
    w.repos.market.upsert_universe(
        [UniverseRow(c, end, NAMES[c], "KOSPI", "common", None, "KRX", Quality.OK) for c in CODES],
        loaded_by="fixture",
    )
    return days


def _login(w: World) -> tuple[TestClient, dict[str, str]]:
    c = w.client()
    csrf = w.login(c)
    return c, {CSRF_HEADER: csrf, "Origin": BASE}


def _ok(r: Any) -> None:
    if r.status_code not in (200, 201):
        raise RuntimeError(f"픽스처 쓰기 실패: HTTP {r.status_code}")


def _seed(w: World, days: list[date]) -> TestClient:
    c, h = _login(w)
    a1: dict[str, Any] = {
        "code": "990001", "analyzed_on": days[-5].isoformat(), "price": 12000.0,
        "ret_52w": 18.4, "fwd_per": 11.2, "band_pos": 35.0, "mktcap": 1_200_000_000_000,
        "opm": 14.5, "eps": {"bear": 900.0, "base": 1100.0, "bull": 1300.0},
        "per": {"bear": 9.0, "base": 11.0, "bull": 13.0},
        "tp": {"bear": 8100.0, "base": 12100.0, "bull": 16900.0},
        "tp_source": "kbj 시나리오 적정가", "conviction": "중", "memo": "합성 메모 — 수요 회복",
        "tags": ["합성", "반도체"], "conclusion": "보유",
        "value_sources": {"price": "kbj", "fwd_per": "kbj", "opm": "consensus", "tp": "kbj"},
    }  # fmt: skip
    a2 = {"code": "990002", "analyzed_on": days[-2].isoformat(), "memo": "수기 메모만",
          "value_sources": {}}  # fmt: skip
    for body in (a1, a2):
        _ok(c.post("/api/journal/analysis", json=body, headers=h))
    trades: list[dict[str, Any]] = [
        {"code": "990001", "side": "buy", "trade_date": days[-60].isoformat(), "price": 10000,
         "qty": 10, "fee": 15, "strategy": "스윙"},
        {"code": "990002", "side": "buy", "trade_date": days[-45].isoformat(), "price": 20000,
         "qty": 5, "fee": 15},
        {"code": "990001", "side": "sell", "trade_date": days[-20].isoformat(), "price": 11000,
         "qty": 4, "fee": 7, "tax": 88, "memo": "일부 익절"},
        {"code": "990003", "side": "buy", "trade_date": days[-10].isoformat(), "price": 30000,
         "qty": 2},
    ]  # fmt: skip
    for body in trades:
        _ok(c.post("/api/journal/trades", json=body, headers=h))
    for code in CODES:
        _ok(c.put(f"/api/watchlist/main/{code}", headers=h))
    w.repos.personal.put_picks_legacy_import(
        [
            PickRow(days[-15], "합성추천", 1, "990001", NAMES["990001"], "kr", 0.9, Decimal(10500)),
            PickRow(days[-3], "합성추천", 2, "990003", NAMES["990003"], "kr", 0.7, None),
        ],
        loaded_by="import:sd",
    )
    return c


def _dump(c: TestClient, files: tuple[tuple[str, str], ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, path in files:
        r = c.get(path)
        if r.status_code not in (200, 404):
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        body = r.json() if r.status_code == 200 else {"status": r.status_code, **r.json()}
        out[name] = json.dumps(body, ensure_ascii=False, indent=2) + "\n"
    return out


def render() -> dict[str, str]:
    empty = build_world()
    add_market(empty)
    c0, _ = _login(empty)
    out = _dump(c0, EMPTY)
    w = build_world()
    days = add_market(w)
    c = _seed(w, days)
    out.update(_dump(c, FILES))
    return out

"""`/api/home` — 첫 화면 카드 묶음(docs/metrics.md §32, ADR 0043).

봉투·카드 칸, 같은 함수의 값을 고른다(두 벌 아님), 카드 격리(한 카드 오류가 다른 카드를
멈추지 않는다 — 오류 본문은 응답·캐시에 남지 않는다), NoData 안내, 개인 데이터 없음, 캐시
영역(묶음이 부르는 경로의 영역을 모두 잇는다), 카드 고르기(장중·마감·부분 실패). 합성 세계만
쓴다.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.trade.flash import FlashChip
from kbj.services.api import site
from kbj.services.api.cache import DOMAIN_JOBS
from kbj.services.api.models.common import FUTURE_AS_OF_NOTE, Envelope
from kbj.services.api.models.flows import MarketMinute, MinuteCard
from kbj.services.api.models.gex import GexBriefing, GexLevel, GexLevels
from kbj.services.api.models.home import Home, HomeCards
from kbj.services.api.models.program import ProgramDaily, ProgramDayBar
from kbj.services.api.readers import home as home_reader
from kbj.services.api.readers._common import NoData, envelope
from kbj.services.api.readers.home import (
    CARD_KEYS,
    CARD_PAGES,
    FUT_NO_CHG_NOTE,
    HOME_DOMAINS,
)
from kbj.store.db import StoreError
from kbj.store.redis_keys import api_data_version_key
from tests.unit.api.api_world import World

CARDS = tuple(HomeCards.model_fields)
QUALITIES = ("ok", "stale", "estimated", "invalid")
# 합성 세계(마감 뒤)에서 값이 있는 카드 — 나머지(GEX·진단·프로그램·공시·테마·수출·선물)는 원천이
# 없다
OK_AFTER_CLOSE = {"indices", "breadth", "spike", "movers", "foreign_inst", "newhigh", "etf"}
# 묶음에 실리면 안 되는 개인 데이터 칸(관심종목·관심종목 가산 중요도·알림 여부·규칙)
PERSONAL_KEYS = {"is_watch", "importance", "login_score", "alerted", "watch_only", "watchlist",
                 "rules", "signals", "rule_name"}  # fmt: skip


def _home(c: TestClient) -> dict[str, Any]:
    r = c.get("/api/home")
    assert r.status_code == 200, r.text
    return r.json()


def _keys(v: Any) -> set[str]:
    if isinstance(v, dict):
        return {str(k) for k in v} | {x for val in v.values() for x in _keys(val)}
    if isinstance(v, list):
        return {x for item in v for x in _keys(item)}
    return set()


def _clear_versions(w: World) -> None:
    for d in DOMAIN_JOBS:
        w.redis.delete(api_data_version_key(d))


# ── 봉투·카드 칸 ─────────────────────────────────────────────────────────────────────────


def test_envelope_and_cards_after_close(world: World, authed: TestClient) -> None:
    body = _home(authed)
    Envelope[Home].model_validate(body)
    d = body["data"]
    assert list(d["cards"]) == list(CARDS)
    assert d["date"] == world.market.last_day.isoformat()
    assert d["n_ok"] + d["n_no_data"] + d["n_error"] == len(CARDS)
    gen = datetime.fromisoformat(body["generated_at"])
    for name, card in d["cards"].items():
        assert card["page"] == CARD_PAGES[name] and card["keys"] == list(CARD_KEYS[name]), name
        if card["status"] == "ok":
            assert card["source"].strip() and card["quality"] in QUALITIES, name
            assert datetime.fromisoformat(card["as_of"]) <= gen, name
            assert card["data"] is not None and card["error"] is None, name
        else:
            assert card["status"] == "no_data" and card["data"] is None, name
            assert card["message"].startswith("아직 없음"), name
    assert {k for k, c in d["cards"].items() if c["status"] == "ok"} == OK_AFTER_CLOSE
    assert body["source"].strip() and body["quality"] in QUALITIES
    assert any("아직 없는 카드 7개" in n for n in body["notes"])


def test_cards_pick_the_same_values_as_their_routes(authed: TestClient) -> None:
    """두 벌 아님 — 카드 값은 같은 경로가 주는 값을 고른 것이다."""
    cards = _home(authed)["data"]["cards"]
    assert cards["movers"]["data"] == authed.get("/api/market/movers").json()["data"]
    summ = authed.get("/api/market/summary").json()["data"]
    tiles = {t["code"]: t for t in summ["indices"]}
    idx = cards["indices"]["data"]
    assert idx["kospi"]["tile"] == tiles["0001"] and idx["kosdaq"]["tile"] == tiles["1001"]
    assert idx["breadth"]["up"] == summ["breadth"]["up"]
    assert idx["breadth"]["down"] == summ["breadth"]["down"]
    assert cards["breadth"]["data"]["above_ma20_pct"] == summ["breadth"]["above_ma20_pct"]
    by_market = authed.get("/api/flows/investors").json()["data"]["by_market"]
    k = idx["kospi"]["investors"]
    assert k["basis"] == "daily" and k["foreign"] == by_market["KOSPI"]["foreign"]
    assert k["individual"] == by_market["KOSPI"]["individual"]
    spike = authed.get("/api/flows/screen?mode=spike&period=1").json()["data"]
    sp = cards["spike"]["data"]
    assert [x["code"] for x in sp["rows"]] == [x["code"] for x in spike["rows"][:10]]
    assert sp["n_hits"] == len(spike["rows"]) and sp["min_turnover"] == spike["min_avg_turnover"]
    fi = cards["foreign_inst"]["data"]
    assert fi["basis"] == "close"
    for who in ("foreign", "inst"):
        rows = authed.get(
            f"/api/flows/screen?mode={who}&period=1&min_avg_turnover=0&limit=5"
        ).json()["data"]["rows"]
        assert [x["code"] for x in fi[who]] == [x["code"] for x in rows if x[who] > 0]
        assert [x["value"] for x in fi[who]] == [x[who] for x in rows if x[who] > 0]
    nh = cards["newhigh"]["data"]
    assert nh["rows"] and all(r["label"] in ("hist", "w52") for r in nh["rows"])
    caps = [r["mktcap"] for r in nh["rows"]]
    assert caps == sorted(caps, reverse=True)
    board = authed.get("/api/board/newhigh").json()["data"]
    assert nh["counts"] == board["counts_close"]
    eok = {r["code"]: r["mktcap"] for r in board["achieved"]}
    assert all(r["mktcap"] == round(eok[r["code"]] * 100_000_000) for r in nh["rows"])  # 억원 → 원
    etf = cards["etf"]["data"]
    flows = authed.get("/api/etf/flows?mode=in&period=5&limit=3").json()["data"]["rows"]
    assert [x["code"] for x in etf["top"]] == [x["code"] for x in flows]
    inflows = [t["net_inflow"] for t in etf["types"]]
    assert inflows == sorted(inflows, reverse=True) and etf["period"] == 5


def test_live_slot_uses_intraday_and_clamps_as_of(world: World, authed: TestClient) -> None:
    world.clock.set(world.slot.replace(minute=5))
    body = _home(authed)
    cards = body["data"]["cards"]
    gen = datetime.fromisoformat(body["generated_at"])
    fi = cards["foreign_inst"]
    assert fi["data"]["basis"] == "intraday" and fi["data"]["date"] == world.next_day.isoformat()
    assert fi["quality"] == "estimated"
    live = authed.get("/api/flows/intraday").json()["data"]
    want = [x["code"] for x in live["top_foreign"]][:5]
    assert [x["code"] for x in fi["data"]["foreign"]] == want
    assert all(x["chg_pct"] is None for x in fi["data"]["foreign"])  # 가집계에는 등락률이 없다
    idx = cards["indices"]
    assert idx["data"]["kospi"]["tile"]["live"] is True
    # 장중 슬롯 끝(10:10)은 아직 오지 않은 시각 — 카드 기준 시각을 응답 시각으로 당기고 적는다
    assert datetime.fromisoformat(idx["as_of"]) == gen and FUTURE_AS_OF_NOTE in idx["notes"]
    for c in cards.values():
        if c["status"] == "ok":
            assert datetime.fromisoformat(c["as_of"]) <= gen
    last = world.market.last_day
    assert any(f"{last.month}/{last.day} 마감 기준" in n for n in cards["spike"]["notes"])
    assert any(f"{last.month}/{last.day} 마감 기준" in n for n in cards["movers"]["notes"])


def test_empty_store_every_card_says_no_data(world: World) -> None:
    from kbj.store.repos import memory_repos

    world.repos = memory_repos()
    c = world.client()
    world.login(c)
    body = _home(c)
    d = body["data"]
    assert (d["n_ok"], d["n_no_data"], d["n_error"]) == (0, len(CARDS), 0)
    assert body["quality"] == "invalid"
    for name, card in d["cards"].items():
        assert card["status"] == "no_data" and card["message"].startswith("아직 없음"), name


# ── 격리 ───────────────────────────────────────────────────────────────────────────────


def test_one_card_error_does_not_stop_the_others(
    world: World,
    authed: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("터짐 appkey=SECRET123")

    def store_down(*_a: Any, **_k: Any) -> Any:
        raise StoreError("저장소 장애", cause="57P01")

    monkeypatch.setattr(home_reader, "etf_types", boom)
    monkeypatch.setattr(home_reader, "board_newhigh", store_down)
    caplog.set_level(logging.ERROR)
    body = _home(authed)
    cards = body["data"]["cards"]
    assert (cards["etf"]["status"], cards["etf"]["error"]) == ("error", "internal")
    assert (cards["newhigh"]["status"], cards["newhigh"]["error"]) == ("error", "unavailable")
    assert cards["etf"]["data"] is None and cards["etf"]["notes"]
    still = OK_AFTER_CLOSE - {"etf", "newhigh"}
    assert {k for k, c in cards.items() if c["status"] == "ok"} == still
    assert body["data"]["n_error"] == 2 and any("오류 카드 2개" in n for n in body["notes"])
    text = json.dumps(body, ensure_ascii=False)
    assert "SECRET123" not in text and "터짐" not in text and "57P01" not in text
    logs = "\n".join(r.getMessage() for r in caplog.records)
    assert "카드 etf" in logs and "카드 newhigh" in logs and "57P01" in logs
    assert "SECRET123" not in logs  # 비밀 형태는 가린다(절대 규칙 5)


def test_shared_read_failure_hits_each_card_separately(
    authed: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """market/summary 는 한 번만 부르고, 실패하면 그것을 쓰는 카드만 error — 선물 카드는 보조 값이라
    그 칸만 빈다(선물은 다른 원천도 없어 오류)."""
    calls: list[int] = []

    def broken(*_a: Any, **_k: Any) -> Any:
        calls.append(1)
        raise StoreError("저장소 장애", cause="08006")

    monkeypatch.setattr(home_reader, "market_summary", broken)
    cards = _home(authed)["data"]["cards"]
    assert len(calls) == 1
    assert cards["indices"]["error"] == "unavailable" and cards["breadth"]["error"] == "unavailable"
    assert cards["futures"]["status"] == "error"  # GEX·1분 없음 + 야간 타일 실패 → 오류
    assert cards["movers"]["status"] == "ok" and cards["spike"]["status"] == "ok"


def test_shared_read_runs_once_across_workers(
    authed: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """카드를 여러 일꾼이 만들어도 공유 읽기(market/summary)는 한 번 — 키마다 잠금."""
    real = home_reader.market_summary
    calls: list[int] = []

    def slow(*a: Any, **k: Any) -> Any:
        calls.append(1)
        time.sleep(0.05)  # 다른 일꾼이 같은 읽기를 기다리게(경쟁을 만든다 — 값 계산과 무관)
        return real(*a, **k)

    monkeypatch.setattr(home_reader, "market_summary", slow)
    cards = _home(authed)["data"]["cards"]
    assert len(calls) == 1
    assert cards["indices"]["status"] == "ok" and cards["breadth"]["status"] == "ok"


def test_error_body_is_not_cached(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.versions = lambda _d: "v1"
    c = world.client()
    world.login(c)
    real = home_reader.etf_types
    calls: list[int] = []
    fail = {"on": True}

    def flaky(*a: Any, **k: Any) -> Any:
        calls.append(1)
        if fail["on"]:
            raise RuntimeError("일시 장애")
        return real(*a, **k)

    monkeypatch.setattr(home_reader, "etf_types", flaky)
    assert _home(c)["data"]["cards"]["etf"]["status"] == "error"
    assert _home(c)["data"]["cards"]["etf"]["status"] == "error"
    assert len(calls) == 2  # 오류 본문은 캐시에 두지 않는다 — 다시 만든다
    fail["on"] = False
    ok = _home(c)
    assert ok["data"]["cards"]["etf"]["status"] == "ok" and len(calls) == 3
    assert _home(c) == ok and len(calls) == 3  # 오류가 없으면 캐시 본문


# ── 개인 데이터 ────────────────────────────────────────────────────────────────────────


def test_no_personal_data_and_watchlist_does_not_change_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """관심종목이 있는 P4 세계(공시·잠정실적 포함) — 개인 칸이 없고, 관심종목을 비워도 값이 같다."""
    from kbj.services.api.readers import stock as stock_reader
    from tests.unit.api.api_world_p4 import shared

    w = shared()
    assert w.repos.journal.watch_codes()  # 관심종목이 있는 세계다
    c = w.client(data_versions=None)
    w.login(c)
    body = _home(c)
    filings = body["data"]["cards"]["filings"]
    assert filings["status"] == "ok" and filings["data"]["top"]
    assert not (_keys(body) & PERSONAL_KEYS)
    assert site.check_public([site.SiteFile("home.json", json.dumps(body).encode())]) == []
    monkeypatch.setattr(stock_reader.Lookup, "watch", property(lambda _self: set()))
    c2 = w.client(data_versions=None)
    w.login(c2)
    assert _home(c2)["data"] == body["data"]


def test_filings_card_picks_by_keyword_score() -> None:
    from tests.unit.api.api_world_p4 import shared

    w = shared()
    c = w.client(data_versions=None)
    w.login(c)
    f = _home(c)["data"]["cards"]["filings"]["data"]
    feed = c.get(f"/api/filings/feed?date={f['date']}&limit=1000").json()["data"]
    assert f["n_total"] == feed["n_total"] and f["by_kind"] == feed["by_kind"]
    want = sorted(
        (r for r in feed["rows"] if not r["is_correction"] and (r["kw_score"] or 0) > 0),
        key=lambda r: (r["kw_score"], r["first_seen_at"], r["rcept_no"]),
        reverse=True,
    )[:8]
    assert [r["rcept_no"] for r in f["top"]] == [r["rcept_no"] for r in want]
    earn = c.get(f"/api/filings/earnings?from={f['date']}&to={f['date']}").json()["data"]["rows"]
    assert f["n_prelim"] == len(earn)
    assert f["n_surprise"] + f["n_shock"] >= len(f["prelims"]) > 0
    prio = [p["priority"] for p in f["prelims"]]
    assert prio == sorted(prio)
    for p in f["prelims"]:
        assert p["side"] in ("surprise", "shock") and p["scope"] != "subsidiary"


# ── 캐시 영역 ──────────────────────────────────────────────────────────────────────────


def test_home_domains_cover_every_card_route(world: World) -> None:
    """카드가 고르는 경로들의 캐시 영역이 묶음 키에 모두 든다 — 빠지면 그 영역 작업이 쓴 새 값이
    TTL 동안 묶음의 옛 본문에 묶인다(test_domain_jobs_cover_table_writers 와 같은 뜻)."""
    asked: list[str] = []

    def versions(d: str) -> str:
        asked.append(d)
        return "v1"

    world.versions = versions
    c = world.client()
    world.login(c)
    for name, keys in CARD_KEYS.items():
        for k in keys:
            asked.clear()
            _clear_versions(world)
            c.get(f"/api/{k}")
            assert asked, (name, k)  # 그 경로가 버전 캐시를 쓴다
            assert set(asked) <= set(HOME_DOMAINS), (name, k, asked)
    asked.clear()
    _clear_versions(world)
    _home(c)
    assert sorted(asked) == sorted(HOME_DOMAINS)


def test_home_cache_follows_every_domain_version(world: World) -> None:
    from kbj.store.repos import memory_repos

    versions = dict.fromkeys(HOME_DOMAINS, "v1")
    world.versions = lambda d: versions.get(d, "none")
    c = world.client()
    world.login(c)
    first = _home(c)
    assert first["data"]["cards"]["etf"]["status"] == "ok"
    app_state = c.app.state.kbj  # pyright: ignore[reportFunctionMemberAccess, reportAttributeAccessIssue]
    object.__setattr__(app_state.read, "repos", memory_repos())
    assert _home(c) == first  # 버전이 같으면 캐시 본문
    versions["etf"] = "v2"  # 영역 하나만 바뀌어도
    world.redis.delete(api_data_version_key("etf"))
    assert _home(c)["data"]["cards"]["etf"]["status"] == "no_data"  # 다시 만든다


# ── 카드 고르기(읽기 함수를 바꿔 끼운 합성 봉투) ─────────────────────────────────────────


def _env(data: Any, *, at: datetime, quality: Quality = Quality.OK, source: str = "KIS") -> Any:
    return envelope(data, source=source, as_of=at, quality=quality, notes=[], generated_at=at)


def _levels(day: date, at: datetime) -> GexLevels:
    def lv(name: str, value: float | None, flag: str = "visible") -> GexLevel:
        return GexLevel(name=name, value=value, flag=flag, quality=Quality.OK)  # pyright: ignore[reportArgumentType]

    return GexLevels(
        scope="all", trade_date=day, session="day", cycle_at=at, session_open=True, price=401.25,
        price_quality=Quality.OK, stale=False, stale_reason=None,
        levels=[lv("flip", 395.0), lv("flip_distance", 1.56), lv("expected_move_calendar", 4.2),
                lv("call_wall", None, "shadow")],
    )  # fmt: skip


def _minute(day: date, at: datetime, tab: str) -> MarketMinute:
    cards = [("prsn", "개인", -300), ("frgn", "외국인", 200), ("orgn", "기관", 100),
             ("etc_corp", "기타법인", None)]  # fmt: skip
    return MarketMinute(
        market=tab, venue="KRX", venue_locked=True, trade_date=day, axis=[], lines=[],
        cards=[MinuteCard(investor=i, label=lb, net_value=None if v is None else v * 10**8,
                          quality=Quality.OK) for i, lb, v in cards],
        collected_at=at, refresh_s=60, provisional=True, status="ok",
    )  # fmt: skip


def test_futures_and_gex_cards_pick_levels_and_minute(
    world: World, authed: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.clock.set(world.slot.replace(minute=5))
    day, at = world.next_day, world.slot
    monkeypatch.setattr(home_reader, "gex_levels", lambda *_a, **_k: _env(_levels(day, at), at=at))
    monkeypatch.setattr(
        home_reader, "market_minute",
        lambda _ctx, *, market, venue: _env(_minute(day, at, market), at=at),
    )  # fmt: skip

    def brief_boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("브리핑 실패")

    monkeypatch.setattr(home_reader, "gex_briefing", brief_boom)
    cards = _home(authed)["data"]["cards"]
    fut = cards["futures"]
    assert fut["status"] == "ok" and FUT_NO_CHG_NOTE in fut["notes"]
    f = fut["data"]
    assert (f["price"], f["session"], f["session_open"]) == (401.25, "day", True)
    inv = f["investors"]
    assert inv["basis"] == "minute" and inv["date"] == day.isoformat()
    assert (inv["individual"], inv["foreign"], inv["institution"], inv["other_corp"]) == (
        -300 * 10**8, 200 * 10**8, 100 * 10**8, None
    )  # fmt: skip
    assert inv["quality"] == "estimated"  # 장중 잠정
    g = cards["gex"]
    assert g["status"] == "ok"  # 국면(브리핑)이 실패해도 레벨 값은 그대로
    assert (g["data"]["flip"], g["data"]["flip_distance"], g["data"]["expected_move"]) == (
        395.0, 1.56, 4.2
    )  # fmt: skip
    assert g["data"]["regime"] is None and any("감마 국면 오류" in n for n in g["notes"])
    # 1분 누적(오늘)이 마감 일별(어제)보다 새 날이라 지수 카드 투자자는 1분 값
    k = cards["indices"]["data"]["kospi"]["investors"]
    assert k["basis"] == "minute" and k["foreign"] == 200 * 10**8


def test_gex_card_regime_from_briefing(
    world: World, authed: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    day, at = world.next_day, world.slot
    world.clock.set(at.replace(minute=5))
    monkeypatch.setattr(home_reader, "gex_levels", lambda *_a, **_k: _env(_levels(day, at), at=at))
    brief = GexBriefing(
        scope="all", cycle_at=at, session_open=True, price=401.25, regime="long",
        regime_label="롱 감마", net_gex=1.0e9, net_vs_avg=1.2, headline="합성 머리 문장",
        expected_move=3.9, levels=[], strikes=[], expiry=None, expiry_date=None,
        days_to_expiry=None, multiplier=250_000, strategies=[],
    )  # fmt: skip
    monkeypatch.setattr(home_reader, "gex_briefing", lambda *_a, **_k: _env(brief, at=at))
    g = _home(authed)["data"]["cards"]["gex"]["data"]
    assert (g["regime"], g["regime_label"], g["headline"]) == ("long", "롱 감마", "합성 머리 문장")
    assert g["expected_move"] == 4.2  # 기대변동폭은 레벨(§G3.8 달력) — 브리핑의 1일 폭이 아니다


def test_program_card_last_valid_day_and_no_intraday_note(
    world: World, authed: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    last = world.market.last_day
    prev = world.cal.prev_trading_day(last)
    at = datetime.combine(last, datetime.min.time(), tzinfo=KST) + timedelta(hours=15, minutes=30)

    def bar(d: date, net: int, q: Quality = Quality.OK) -> ProgramDayBar:
        return ProgramDayBar(date=d, net=net, arbt_net=net // 2, nabt_net=net - net // 2,
                             entm_net=None, onsl_net=None, index_close=None, quality=q)  # fmt: skip

    def daily(_ctx: Any, *, market: str, days: int) -> Any:
        if market == "Q":
            raise NoData("아직 없음 — program.daily 15:50")
        rows = [bar(prev, 10**10), bar(last, -(10**10), Quality.INVALID)]  # 마지막 날 검산 실패
        return _env(ProgramDaily(market="K", venue="KRX", days=2, rows=rows), at=at)

    monkeypatch.setattr(home_reader, "program_daily", daily)
    world.clock.set(world.slot.replace(minute=5))  # 다음 거래일 장중
    card = _home(authed)["data"]["cards"]["program"]
    assert card["status"] == "ok"
    d = card["data"]
    assert d["kospi"]["date"] == prev.isoformat() and d["kospi"]["net"] == 10**10
    assert d["kosdaq"] is None
    assert any("코스닥 프로그램: 아직 없음" in n for n in card["notes"])
    assert any("장중 시장 프로그램 누적은 아직 API 에 없다" in n for n in card["notes"])


def test_export_card(world: World, authed: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = datetime(2026, 9, 11, 9, 0, tzinfo=KST)
    chip = FlashChip(ym="2026-09", seg=1, span_text="01~10", usd=17_000_000_000, yoy=0.123,
                     yoy_reason=None, first_seen_at=seen, source="관세청",
                     quality=Quality.ESTIMATED)  # fmt: skip
    monkeypatch.setattr(home_reader, "export_flash", lambda *_a, **_k: chip)
    card = _home(authed)["data"]["cards"]["export"]
    assert card["status"] == "ok" and card["quality"] == "estimated" and card["source"] == "관세청"
    d = card["data"]
    assert (d["ym"], d["seg"], d["span_label"], d["usd"], d["yoy"]) == (
        "2026-09", 1, "1~10일", 17_000_000_000, 0.123
    )  # fmt: skip
    monkeypatch.setattr(home_reader, "export_flash", lambda *_a, **_k: None)
    world.clock.advance(seconds=1)
    assert _home(authed)["data"]["cards"]["export"]["status"] == "no_data"


def test_home_requires_session(client: TestClient) -> None:
    r = client.get("/api/home")
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"

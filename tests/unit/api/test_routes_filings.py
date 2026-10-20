"""`/api/filings/*`(페이지 5)와 종목 상세의 공시·잠정실적·오버행·내부자 섹션 — 합성 세계에서 손계산·
엔진 규칙과 같다(docs/p4_design.md §4.1~§4.4·§5.3·§6.4).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.services.api.models.common import Envelope
from kbj.services.api.models.filings import (
    EarningsList,
    EarningsSchedule,
    FilingsFeed,
    InsiderRecent,
    OverhangRecent,
)
from kbj.store.repos import memory_repos
from tests.unit.api.api_world_p4 import P4ApiWorld, expect, shared

FILINGS_ROUTES: list[tuple[str, type[Any]]] = [
    ("/api/filings/feed", FilingsFeed),
    ("/api/filings/earnings?from=2026-11-10&to=2026-11-16", EarningsList),
    ("/api/filings/overhang?days=90", OverhangRecent),
    ("/api/filings/insiders?days=30", InsiderRecent),
    ("/api/filings/schedule?days=60", EarningsSchedule),
]


@pytest.fixture(scope="module")
def pw() -> P4ApiWorld:
    return shared()


@pytest.fixture(scope="module")
def c(pw: P4ApiWorld) -> TestClient:
    return pw.authed()


@pytest.mark.parametrize(("path", "model"), FILINGS_ROUTES)
def test_envelopes(c: TestClient, path: str, model: type[Any]) -> None:
    r = c.get(path)
    assert r.status_code == 200, r.text
    body = r.json()
    Envelope[model].model_validate(body)
    assert body["source"] == "DART" and "NaN" not in r.text


def test_overhang_matches_hand_count(c: TestClient) -> None:
    """#14 CB 3·BW 1(리픽싱·일부 청구) — 요약이 손계산(p4_expect hand)과 같다."""
    hand = expect()["hand"]["overhang_994130"]
    data = c.get("/api/stock/994130/overhang").json()["data"]
    s = data["summary"]
    assert s["potential_new_shares"] == hand["potential_new_shares"]
    assert s["exercisable_shares"] == hand["exercisable_shares"]
    assert s["denominator"] == hand["denominator"]
    assert s["ratio_pct"] == pytest.approx(hand["ratio_pct"], abs=1e-4)
    assert s["exercisable_ratio_pct"] == pytest.approx(hand["exercisable_ratio_pct"], abs=1e-4)
    # 로그인 칸 — 전 거래일 종가 기준(추정)
    assert data["close"] is not None and s["listed_shares"] is not None
    active = [i for i in data["instruments"] if i["active"]]
    assert s["amount"] == sum(i["amount"] for i in active if i["amount"] is not None)
    itm = sum(i["potential_shares"] for i in active if i["itm"])
    assert s["itm_shares"] == itm
    assert {i["instrument"] for i in data["instruments"]} >= {"cb", "bw"}
    assert len(data["timeline"]) == len(data["instruments"])


def test_overhang_without_events_is_200(c: TestClient) -> None:
    body = c.get("/api/stock/994150/overhang").json()
    assert body["data"]["instruments"] == [] or body["data"]["summary"] is not None


def test_insider_windows_match_hand_count(c: TestClient) -> None:
    """#15 내부자 대량 매도 + 5% 보고 — 30·90·180일 창이 손계산과 같다(정정은 늦은 것만)."""
    hand = expect()["hand"]["insider_994140"]
    body = c.get("/api/stock/994140/insiders?days=180").json()
    data = body["data"]
    assert data["label"] == "보고 기준 순증감(사유 미구분 포함)"
    got = {w["days"]: w for w in data["windows"]}
    for days, want in hand.items():
        w = got[int(days)]
        for k in ("net_shares", "buy_shares", "sell_shares", "n_reports", "n_reporters"):
            assert w[k] == want[k], (days, k)
        assert w["ratio_pct"] == pytest.approx(want["ratio_pct"], abs=1e-6)
        assert w["amount"] is not None and w["amount"] < 0  # 매도 우위 — 금액(추정)도 음수
    assert data["major_holders"], "5% 대량보유 목록"
    short = c.get("/api/stock/994140/insiders?days=30").json()["data"]["reports"]
    assert len(short) <= len(data["reports"])
    assert all(r["rcept_dt"] >= "2026-10-17" for r in short)
    assert any("장내 순매수가 아니다" in n for n in body["notes"])


def test_disclosures_scores_and_filters(c: TestClient) -> None:
    body = c.get("/api/stock/994140/disclosures?days=365").json()
    rows = body["data"]["rows"]
    assert rows and all(r["corp_code"] == "99400140" for r in rows)
    assert [r["rcept_no"] for r in rows] == sorted((r["rcept_no"] for r in rows), reverse=True)
    for r in rows:
        assert r["is_watch"] is True
        assert r["login_score"] >= r["kw_score"] + 3  # 관심종목 가산
        assert r["link"].endswith(r["rcept_no"])
    kind = rows[0]["kind"]
    only = c.get(f"/api/stock/994140/disclosures?days=365&kind={kind}").json()["data"]["rows"]
    assert only and all(r["kind"] == kind for r in only)
    other = c.get("/api/stock/994150/disclosures?days=365").json()["data"]["rows"]
    assert all(r["is_watch"] is False for r in other)


def test_earnings_section(c: TestClient, pw: P4ApiWorld) -> None:
    """#15 잠정실적(정정 1건) — YoY 분류·알림 기록. 알림은 한 통(같은 subject)."""
    data = c.get("/api/stock/994140/earnings").json()["data"]
    q3 = [p for p in data["prelims"] if p["period"] == "2026Q3"]
    assert len(q3) == 2  # 원 공시 + 정정
    assert {p["signal"] for p in q3} == {"YOY_BIG_UP"}
    assert sum(1 for p in q3 if p["is_correction"]) == 1
    assert sum(1 for p in q3 if p["alerted"]) == 1
    assert data["surprises"] and all(s["signal"] for s in data["surprises"])
    subjects = [s for s in pw.notify.subjects if s]
    assert subjects.count(expect()["alerts"]["alert.earnings"]["subject"]) == 1


def test_feed_filters(c: TestClient) -> None:
    all_ = c.get("/api/filings/feed?limit=200&listed_only=false").json()["data"]
    assert all_["date"] == "2026-11-16"
    assert sum(all_["by_kind"].values()) == all_["n_total"] >= len(all_["rows"]) == 200
    # 기본은 상장사 소식만 — 비상장(E)·발행 서류(투자설명서 등)는 뺀다(2026-10-20)
    listed = c.get("/api/filings/feed?limit=1000").json()["data"]
    assert listed["n_total"] < all_["n_total"]
    assert all(r["corp_cls"] in ("Y", "K", "N") for r in listed["rows"])
    assert not any("투자설명서" in (r["title"] or "") for r in listed["rows"])
    seen = [r["first_seen_at"] for r in all_["rows"]]
    assert seen == sorted(seen, reverse=True)
    watch = c.get("/api/filings/feed?watch_only=true").json()["data"]
    assert watch["rows"] and all(r["is_watch"] for r in watch["rows"])
    kospi = c.get("/api/filings/feed?market=KOSPI").json()["data"]
    assert all(r["corp_cls"] == "Y" for r in kospi["rows"])
    hi = c.get("/api/filings/feed?importance=high").json()["data"]
    assert all(r["importance"] == "high" for r in hi["rows"])
    lim = c.get("/api/filings/feed?limit=3").json()["data"]
    assert len(lim["rows"]) == 3 and lim["n_total"] == listed["n_total"]
    day = c.get("/api/filings/feed?date=2026-11-13").json()["data"]
    assert day["date"] == "2026-11-13" and all(r["rcept_dt"] == "2026-11-13" for r in day["rows"])


def test_feed_no_data_on_empty_db(pw: P4ApiWorld) -> None:
    w = P4ApiWorld(
        w=pw.w, repos=memory_repos(), clock=pw.clock, redis=pw.redis, fin=pw.fin,
        reports=pw.reports, cal=pw.cal, notify=pw.notify, versions=None,
    )  # fmt: skip
    c = w.authed()
    r = c.get("/api/filings/feed")
    assert r.status_code == 404 and r.json()["code"] == "no_data"
    assert c.get("/api/stock/994010/header").json()["code"] == "unknown_code"


@pytest.mark.parametrize(
    "path",
    [
        "/api/filings/earnings?from=2026-10-01&to=2026-11-16",
        "/api/filings/earnings?from=2026-11-16&to=2026-11-10",
        "/api/filings/feed?limit=0",
        "/api/filings/feed?importance=urgent",
        "/api/filings/feed?market=NYSE",
        "/api/filings/feed?date=2026-13-01",
        "/api/filings/overhang?days=91",
        "/api/filings/overhang?instrument=warrant",
        "/api/filings/insiders?min_ratio_pct=-1",
        "/api/filings/schedule?days=61",
    ],
)
def test_bad_query_is_422(c: TestClient, path: str) -> None:
    r = c.get(path)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_request"


def test_earnings_list_default_range(c: TestClient) -> None:
    data = c.get("/api/filings/earnings").json()["data"]
    assert (date.fromisoformat(data["to_date"]) - date.fromisoformat(data["from_date"])).days == 6
    rows = data["rows"]
    assert rows and all(data["from_date"] <= r["rcept_dt"] <= data["to_date"] for r in rows)
    watched = [r for r in rows if r["is_watch"]]
    assert watched and all(r["stock_code"] for r in watched)


def test_overhang_recent_and_insiders_recent(c: TestClient) -> None:
    oh = c.get("/api/filings/overhang?days=90&instrument=cb").json()["data"]
    assert oh["rows"] and all(r["instrument"] == "cb" for r in oh["rows"])
    ins = c.get("/api/filings/insiders?days=30").json()["data"]
    hand = expect()["hand"]["insider_994140"][30]
    row = next(r for r in ins["rows"] if r["stock_code"] == "994140")
    assert row["net_shares"] == hand["net_shares"]
    big = c.get("/api/filings/insiders?days=30&min_ratio_pct=50").json()["data"]
    assert big["rows"] == []


def test_schedule_rows(c: TestClient) -> None:
    data = c.get("/api/stock/994010/earnings").json()["data"]
    kinds = {s["kind"] for s in data["schedule"]}
    assert kinds <= {"pre_announce", "statutory"}
    for s in data["schedule"]:
        if s["kind"] == "statutory":
            assert s["quality"] == "estimated"

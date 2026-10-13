"""간밤 미국 라우트(`/api/board/us` — 로그인, docs/p5_design.md §1.6·§5, metrics §23).

합성 세계(p5_world)를 메모리 저장소에 올리고 `us.eod` 산출을 만든 API(`web_fixtures_p5/us.py`).
리더는 DB(산출)만 읽는다 — 미국 거래일(뉴욕 날짜)·출처·보류 수를 반드시 싣는다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.synthetic.p5_world import US_DAY
from tests.unit.api.web_fixtures_p5.us import UsApiWorld, us_api_world


@pytest.fixture(scope="module")
def uw() -> UsApiWorld:
    return us_api_world()


@pytest.fixture(scope="module")
def c(uw: UsApiWorld) -> TestClient:
    return uw.authed()


def data(c: TestClient, path: str) -> dict[str, Any]:
    r = c.get(path)
    assert r.status_code == 200, r.text
    return r.json()


def test_미국_거래일_출처_보류를_싣는다(c: TestClient, uw: UsApiWorld) -> None:
    body = data(c, "/api/board/us")
    d = body["data"]
    assert d["us_trade_date"] == US_DAY.isoformat() and d["basis"] == "close"
    assert body["source"] == "nasdaq" and body["quality"] == "ok"
    assert any("미국 거래일 2026-11-16(뉴욕)" in n for n in body["notes"])
    assert uw.world is not None
    held = uw.world.expect_us()["held"]
    assert d["pending"] == len(held) == 1
    assert any("보류 1종목" in n for n in body["notes"])  # 숨기지 않는다


def test_고가_기준_52주는_심은_신고가와_같다(c: TestClient, uw: UsApiWorld) -> None:
    assert uw.world is not None
    want = uw.world.expect_us()
    d = data(c, "/api/board/us?basis=high")["data"]
    assert d["basis"] == "high"
    w52 = sorted(r["symbol"] for r in d["newhigh"] if r["label"] == "52주")
    # 유니버스 하한(거래대금 추정 $30M↑ 등)을 넘은 종목만 보드에 남는다 — 심은 것의 부분집합
    assert set(w52) <= set(want["new_highs"]) and w52
    assert not set(want["funds"]) & {r["symbol"] for r in d["newhigh"]}


def test_신고가_행은_52주_먼저_그다음_신고가(c: TestClient) -> None:
    rows = data(c, "/api/board/us")["data"]["newhigh"]
    labels = [r["label"] for r in rows]
    assert labels == sorted(labels, key=lambda x: {"52주": 0, "신고가": 1}[x])
    assert all(r["close"] is not None and r["market_cap"] is not None for r in rows)


def test_한눈에_문장과_섹터는_산출_그대로(c: TestClient) -> None:
    d = data(c, "/api/board/us")["data"]
    first = d["summary_lines"][0]
    n = len(d["newhigh"])
    assert first.startswith(f"신고가 {n}종목")
    assert "–+" not in " ".join(d["summary_lines"])  # 설정의 표시 경계가 붙었다
    assert sum(s["newhigh"] for s in d["sectors"]) == n


def test_기준이_다르면_다른_해석(c: TestClient) -> None:
    close = data(c, "/api/board/us")["data"]
    high = data(c, "/api/board/us?basis=high")["data"]
    assert close["us_trade_date"] == high["us_trade_date"]
    assert [r["label"] for r in close["newhigh"]] != [r["label"] for r in high["newhigh"]]


def test_빈_저장소는_404_no_data() -> None:
    c = us_api_world(empty=True).authed()
    r = c.get("/api/board/us")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "no_data" and "us.eod" in body["message"]


def test_잘못된_기준은_422(c: TestClient) -> None:
    assert c.get("/api/board/us?basis=open").status_code == 422

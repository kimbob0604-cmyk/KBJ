"""`/api/watchlist` — 관심종목(로그인·개인) 목록 + 종목마다 마지막 시세(docs/p4_design.md §5.3,
D-P4-13).

목록은 CLI 가 DB 에 직접 쓰므로 응답 캐시를 쓰지 않는다 — 바꾼 즉시 보인다(ETag 304 는 된다).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from kbj.core.fin_rows import WatchItem
from kbj.core.time import KST
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.stock import Watchlist
from kbj.services.api.readers.watchlist import IMPORT_HINT
from tests.unit.api.api_world_p4 import P4ApiWorld, build_p4_api_world, shared


@pytest.fixture(scope="module")
def pw() -> P4ApiWorld:
    return shared()


def test_watchlist_in_file_order_with_quotes(pw: P4ApiWorld) -> None:
    c = pw.authed()
    r = c.get("/api/watchlist")
    assert r.status_code == 200
    body = r.json()
    Envelope[Watchlist].model_validate(body)
    data = body["data"]
    items = data["items"]
    assert data["list_name"] == "main" and data["n"] == len(items) == 19
    assert [i["code"] for i in items] == [w.code for w in pw.w.watchlist]
    assert [i["sort"] for i in items] == sorted(i["sort"] for i in items)
    assert "994150" not in {i["code"] for i in items}  # #16 은 관심 밖
    first = items[0]
    assert first["close"] is not None and first["date"] == "2026-11-13"
    assert first["source"] == "import:sd" and first["kind"] == "common"
    assert body["quality"] in ("ok", "stale", "estimated")
    assert r.headers["cache-control"] == "private, no-cache"
    again = c.get("/api/watchlist", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304


def test_change_is_visible_without_cache() -> None:
    w = build_p4_api_world(valuation=False)
    c = w.authed()
    assert c.get("/api/watchlist").json()["data"]["n"] == 19
    now = datetime(2026, 11, 16, 22, 0, tzinfo=KST)
    w.repos.journal.replace_watchlist(
        "main", [WatchItem("main", "994150", "모의소프트", now, 0, None, "cli")],
        loaded_by="test", now=now,
    )  # fmt: skip
    data = c.get("/api/watchlist").json()["data"]
    assert [i["code"] for i in data["items"]] == ["994150"]
    # 관심종목이 바뀌면 상세 머리의 관심 표시도 바뀐다(머리 캐시 60초 — 새 앱)
    assert w.authed().get("/api/stock/994150/header").json()["data"]["is_watch"] is True
    w.repos.journal.replace_watchlist("main", [], loaded_by="test", now=now)
    empty = c.get("/api/watchlist").json()
    assert empty["data"]["items"] == [] and IMPORT_HINT in empty["notes"]
    other = c.get("/api/watchlist?list=swing").json()["data"]
    assert other["list_name"] == "swing" and other["n"] == 0


@pytest.mark.parametrize("q", ["list=Main", "list=1abc", "list=" + "a" * 40])
def test_bad_list_name_is_422(pw: P4ApiWorld, q: str) -> None:
    r = pw.authed().get(f"/api/watchlist?{q}")
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"


def test_requires_session(pw: P4ApiWorld) -> None:
    assert pw.client().get("/api/watchlist").status_code == 401

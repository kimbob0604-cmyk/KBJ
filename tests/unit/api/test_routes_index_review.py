"""`/api/index-review`·`/api/index-review/stock/{code}` — 합성 세계(P5 2차 묶음 IDX §3-5·§4).

탭·일정 D-day·후보 표(필요 상승률 정렬)·'추정' 표시·빈 목록 상태·사용자 컷오프
나란히·배지·404·로그인 필요.
"""

from __future__ import annotations

from typing import Any

from kbj.services.api.readers.index_review import NO_CALENDAR
from tests.fixtures.synthetic.index_world import golden_doc
from tests.unit.api.index_review_world import IndexApiWorld, index_api_world


def get(api: IndexApiWorld, path: str, status: int = 200) -> Any:
    r = api.authed().get(path)
    assert r.status_code == status, r.text
    return r.json()


def test_requires_login() -> None:
    r = index_api_world(predict=False).client().get("/api/index-review")
    assert r.status_code == 401


def test_default_tab_is_msci_with_dday_band_and_estimates() -> None:
    body = get(index_api_world(), "/api/index-review")
    d = body["data"]
    assert [t["id"] for t in d["indexes"]] == ["msci_korea", "kospi200", "kosdaq150"]
    assert d["index_id"] == "msci_korea"
    rv = d["review"]
    assert rv["review_id"] == "2026-11" and rv["confirmed"] is False
    assert rv["quality"] == "estimated"
    assert (rv["d_base"], rv["d_announce"], rv["d_trade"], rv["d_effective"]) == (0, 12, 31, 32)
    assert [r["review_id"] for r in d["reviews"]] == ["2026-05", "2026-11"]
    assert body["quality"] == "estimated" and "일정 추정" in " ".join(body["notes"])
    assert body["as_of"].startswith("2026-10-30T15:30")
    # 캘린더(페이지 5) — 효력 전 리뷰만, 발표일 순
    assert [(c["index_id"], c["review_id"]) for c in d["calendar"]] == [
        ("msci_korea", "2026-11"), ("kospi200", "2026-12"), ("kosdaq150", "2026-12"),
    ]  # fmt: skip


def test_tables_match_engine_and_are_sorted_by_required_rise() -> None:
    d = get(index_api_world(), "/api/index-review?index=msci_korea")["data"]
    (run,) = d["runs"]
    gold = golden_doc()["reviews"]["msci_korea:2026-11"]
    assert run["cutoff_kind"] == "model" and run["status"] == "ok"
    assert run["cutoff"]["won"] == gold["cutoff"] and run["cutoff"]["low_won"] <= gold["cutoff"]
    want = {r["code"]: r["outcome"] for r in gold["rows"]}
    for table, verdict in (("adds", "add"), ("deletes", "delete"), ("borderline", "borderline")):
        rows = run[table]
        assert {r["code"] for r in rows} == {c for c, v in want.items() if v == verdict}
        rises = [r["required_rise"] for r in rows if r["required_rise"] is not None]
        assert rises == sorted(rises)
        assert all(r["quality"] == "estimated" for r in rows)
    add = run["adds"][0]
    # 사용자 예시 열 — 모두 있다
    for k in ("mktcap", "ff_mcap", "fif", "gap_mcap", "gap_ff", "condition_met", "required_rise",
              "required_basis", "passive_won", "adv_multiple"):  # fmt: skip
        assert k in add
    assert add["condition_met"] is True and add["passive_won"] > 0
    assert run["n_add"] == len(run["adds"]) and run["aum_won"] == 30 * 10**12


def test_krx_tab_and_review_param() -> None:
    d = get(index_api_world(), "/api/index-review?index=kospi200&review=2026-12")["data"]
    (run,) = d["runs"]
    assert d["index_id"] == "kospi200" and run["cutoff"]["method"].startswith("sector rank")
    assert all(r["gap_ff"] is None for r in run["adds"])  # KRX 형은 유동 조건 없음
    get(index_api_world(), "/api/index-review?index=kospi200&review=2030-01", 404)
    get(index_api_world(), "/api/index-review?index=nope", 404)
    get(index_api_world(), "/api/index-review?index=BAD!", 422)


def test_empty_constituents_state() -> None:
    body = get(index_api_world(members=False), "/api/index-review")
    (run,) = body["data"]["runs"]
    assert run["status"] == "no_constituents" and run["adds"] == [] and run["cutoff"] is None
    assert any("구성종목 목록 없음" in n for n in body["notes"])
    assert body["quality"] != "ok"


def test_no_prediction_yet_and_no_calendar() -> None:
    body = get(index_api_world(predict=False), "/api/index-review")
    assert body["data"]["runs"] == [] and any("예측 없음" in n for n in body["notes"])
    body = get(index_api_world(calendar=False, predict=False), "/api/index-review", 404)
    assert NO_CALENDAR in str(body)


def test_user_cutoff_runs_side_by_side() -> None:
    d = get(index_api_world(user_cutoff=2 * 10**12), "/api/index-review")["data"]
    assert [r["cutoff_kind"] for r in d["runs"]] == ["model", "user"]
    assert d["runs"][1]["cutoff"] == {"kind": "user", "won": 2 * 10**12, "low_won": None,
                                      "high_won": None, "method": "user"}  # fmt: skip


def test_stock_badges() -> None:
    api = index_api_world()
    gold = golden_doc()["reviews"]["msci_korea:2026-11"]
    code = next(r["code"] for r in gold["rows"] if r["outcome"] == "add")
    body = get(api, f"/api/index-review/stock/{code}")
    badges = body["data"]["badges"]
    msci = next(b for b in badges if b["index_id"] == "msci_korea")
    assert msci["verdict"] == "add" and msci["label"].startswith("MSCI Korea 편입 후보 · ")
    keep = next(r["code"] for r in gold["rows"] if r["outcome"] == "keep")
    kb = get(api, f"/api/index-review/stock/{keep}")["data"]["badges"]
    assert all(b["index_id"] != "msci_korea" for b in kb)  # 유지는 배지 없음
    empty = get(api, "/api/index-review/stock/000000")
    assert empty["data"]["badges"] == [] and empty["notes"]
    get(api, "/api/index-review/stock/abc", 422)

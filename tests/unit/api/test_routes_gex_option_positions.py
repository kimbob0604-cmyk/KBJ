"""`/api/gex/option-positions` — 옵션 행사가별 투자자 추정 포지션(metrics §34, ADR 0044 — 로그인).

합성 5거래일 세계(`tests/unit/api/optpos_fixtures.py`)를 메모리 저장소에 싣고 앱으로 본다:
봉투(source·as_of·quality ≤ estimated), 누적 시작일, OI 초과 invalid, 벽·청산 라벨, 합성 델타,
오늘 바뀐 행사가 상위(숫자·라벨만), EWY 보류, 빈 저장소 404, 잘못된 투자자 422, 세션 없으면 401,
프런트 픽스처 최신.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.services.api.models.common import Envelope
from kbj.services.api.models.gex_optpos import OptionPositions
from kbj.services.api.readers.gex_option_positions import NO_DATA
from tests.fixtures.synthetic.opt_pos_world import WORLD_DAYS
from tests.unit.api.optpos_fixtures import OUT, optpos_api_world, render


@pytest.fixture(scope="module")
def c() -> TestClient:
    return optpos_api_world().authed()


def _get(c: TestClient, path: str) -> dict[str, Any]:
    r = c.get(path)
    assert r.status_code == 200, r.text
    return r.json()


def test_envelope_and_series(c: TestClient) -> None:
    body = _get(c, "/api/gex/option-positions")
    env = Envelope[OptionPositions].model_validate(body)
    d = env.data
    assert d.trade_date == WORLD_DAYS[-1] and d.investor == "frgn"
    assert body["quality"] == "estimated" and "키움 방식" in body["source"]
    assert any("추정치" in n for n in body["notes"])
    assert any("OI 검산 실패 1행" in n for n in body["notes"])
    labels = [s.label for s in d.series]
    assert labels == ["M:202611", "WKI:261101"]
    m = d.series[0]
    assert m.accum_from == WORLD_DAYS[0] and m.quality == "estimated" and m.invalid == 1
    assert m.call_wall is not None and m.call_wall.strike == 900.0
    assert m.put_wall is not None and m.put_wall.strike == 850.0
    s950 = next(s for s in m.strikes if (s.strike, s.cp) == (950.0, "C"))
    assert s950.label == "cleared" and s950.prev_holding == -4000 and s950.holding == 0


def test_top_changes_are_numbers_and_codes(c: TestClient) -> None:
    d = _get(c, "/api/gex/option-positions")["data"]
    top = d["top_changes"]
    assert top[0]["change"] == 4000 and top[0]["label"] == "cleared"
    assert set(top[0]) == {"series", "strike", "cp", "change", "holding", "prev_holding", "label"}
    assert d["synthetic"]["futures"] == -3000 * 4 + 670
    assert d["synthetic"]["total"] is not None and d["synthetic"]["quality"] == "estimated"
    assert d["ewy"]["status"] == "unavailable" and "확인 필요" in d["ewy"]["reason"]
    assert d["futures_today"] == 670 and d["futures_price"] == 884.0
    assert d["thresholds"]["min_judge"] == 300 and d["thresholds"]["option_multiplier"] == 250_000


def test_board_oi_fallback_note_when_krx_missing() -> None:
    c = optpos_api_world(krx=False).authed()
    body = _get(c, "/api/gex/option-positions")
    assert any("KRX 확정 OI 전" in n for n in body["notes"])
    m = body["data"]["series"][0]
    assert {s["oi_source"] for s in m["strikes"]} == {"board"}


def test_date_query_and_investor(c: TestClient) -> None:
    d = _get(c, f"/api/gex/option-positions?date={WORLD_DAYS[2].isoformat()}")["data"]
    assert d["trade_date"] == WORLD_DAYS[2].isoformat()
    assert _get(c, "/api/gex/option-positions?investor=prsn")["data"]["investor"] == "prsn"


def test_empty_is_404_bad_investor_422_and_session_required() -> None:
    w = optpos_api_world(empty=True)
    r = w.authed().get("/api/gex/option-positions")
    assert r.status_code == 404 and NO_DATA in r.text
    assert w.authed().get("/api/gex/option-positions?investor=nope").status_code == 422
    assert w.client().get("/api/gex/option-positions").status_code == 401


def test_optpos_fixtures_are_fresh() -> None:
    """`web/test/fixtures/api/gex_optpos/**` 가 지금 코드 출력과 같다. 다르면
    `uv run python -m tests.unit.api.optpos_fixtures` 로 다시 만든다."""
    want = render()
    assert {p.name for p in OUT.glob("*.json")} == set(want)
    for name, text in want.items():
        assert (OUT / name).read_text(encoding="utf-8") == text, f"{name} 이 낡았다"
        json.loads(text)

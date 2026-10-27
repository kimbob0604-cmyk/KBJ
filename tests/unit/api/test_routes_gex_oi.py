"""`/api/gex/futures-oi` — 코스피200 선물 미결제약정 패널(metrics §32, ADR 0042 — 로그인).

합성 OI 세계(`tests/fixtures/synthetic/oi_world.py` — 2026-11-04 18:00 KST)를 메모리 저장소에
싣고 앱으로 본다.
- 확정 행은 KRX 11-03 까지(오래된 것부터 `days` 개), 11-04 는 전광판 잠정(estimated) +
  투자자 15:40 행
- 투자자 대금은 원(investor_flow 백만원 × 10⁶), 15:45 전 마지막 행은 estimated + 사유
- 빈 저장소 404 `no_data`(작업 이름), 범위 밖 days 422, 세션 없으면 401
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.engines.gex.futures_oi import OPTION_DAYS
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.gex_oi import FuturesOi
from kbj.services.api.readers.gex_oi import NO_OI
from tests.fixtures.synthetic.oi_world import LAST_CONFIRMED, TODAY, build_oi_world
from tests.unit.api.web_fixtures_p7.gex import GexApiWorld, oi_api_world


@pytest.fixture(scope="module")
def aw() -> GexApiWorld:
    return oi_api_world()


@pytest.fixture(scope="module")
def c(aw: GexApiWorld) -> TestClient:
    return aw.authed()


def _get(c: TestClient, path: str) -> dict[str, Any]:
    r = c.get(path)
    assert r.status_code == 200, r.text
    return r.json()


def test_envelope_confirmed_days_and_provisional_today(c: TestClient) -> None:
    body = _get(c, "/api/gex/futures-oi?days=20")
    env = Envelope[FuturesOi].model_validate(body)
    d = env.data
    assert len(d.days) == 20 and d.days[-1].trade_date == LAST_CONFIRMED
    assert [x.trade_date for x in d.days] == sorted(x.trade_date for x in d.days)
    assert all(not x.provisional and x.source.startswith("KRX:") for x in d.days)
    p = d.provisional
    assert p is not None and p.trade_date == TODAY and p.provisional
    assert p.quality == "estimated" and p.source.startswith("KIS:")
    assert body["quality"] == "estimated"  # 잠정·15:40 투자자 행이 들어 있다
    assert body["source"].startswith("KRX:") and "KIS" in body["source"]
    assert any("KRX 확정 전" in n for n in body["notes"])
    assert d.thresholds.price_flat_pct == 0.10 and d.thresholds.oi_flat_pct == 0.30
    assert d.basis == "total"


def test_totals_match_the_synthetic_krx_rows(c: TestClient) -> None:
    rows = [r for r in build_oi_world().krx if r["trade_date"] == LAST_CONFIRMED]
    d = _get(c, "/api/gex/futures-oi?days=20")["data"]["days"][-1]
    assert d["total_oi"] == sum(r["acc_opnint_qty"] for r in rows)
    near = min(rows, key=lambda r: r["expiry"])
    assert d["near_expiry"] == near["expiry"] and d["near_oi"] == near["acc_opnint_qty"]
    assert d["label"] in {"new_long", "new_short", "short_cover", "long_liquidation", "flat"}
    assert d["label_text"]


def test_investor_flows_are_won_and_marked_estimated_before_the_15_45_close(
    c: TestClient,
) -> None:
    d = _get(c, "/api/gex/futures-oi?days=20")["data"]
    last = d["days"][-1]
    by = {f["investor"]: f for f in last["flows"]}
    assert set(by) == {"foreign", "institution", "individual"}
    assert by["foreign"]["net_qty"] == 3_451
    assert by["foreign"]["net_value_won"] == 3_451 * 140 * 1_000_000
    assert by["foreign"]["quality"] == "estimated" and "15:40" in by["foreign"]["reason"]
    assert d["foreign_streak"] == 3  # 11-02·11-03·11-04(잠정) 연속 매수
    opts = d["options"]
    assert {(o["series"], o["cp"]) for o in opts} == {
        ("monthly", "C"), ("monthly", "P"), ("weekly_mon", "C"), ("weekly_mon", "P"),
        ("weekly_thu", "C"), ("weekly_thu", "P"),
    }  # fmt: skip
    dates = sorted({date.fromisoformat(o["trade_date"]) for o in opts})
    assert len(dates) == OPTION_DAYS and dates[-1] == TODAY  # 최근 5거래일(잠정 오늘 포함)
    assert len(opts) == OPTION_DAYS * 6 * 3


def test_expiry_roll_keeps_the_settled_series_out_of_the_label_input(c: TestClient) -> None:
    days = _get(c, "/api/gex/futures-oi?days=60")["data"]["days"]
    exp = next(x for x in days if x["expiry_day"])
    assert exp["trade_date"] == "2026-09-10" and exp["near_expiry"] == "202612"
    nxt = days[days.index(exp) + 1]
    # 다음 날 9월물 결제 소멸분은 단순 차에만(라벨 입력 증감에는 없다)
    assert nxt["oi_change_raw"] < nxt["oi_change"] - 10_000


def test_empty_store_is_404_with_the_job_name() -> None:
    r = oi_api_world(empty=True).authed().get("/api/gex/futures-oi")
    assert r.status_code == 404
    assert r.json() == {"code": "no_data", "message": NO_OI}
    assert "gex.krx_derivatives" in NO_OI


@pytest.mark.parametrize("q", ["days=5", "days=121", "days=abc"])
def test_bad_days_are_422_without_echo(c: TestClient, q: str) -> None:
    r = c.get(f"/api/gex/futures-oi?{q}")
    assert r.status_code == 422
    assert "abc" not in r.text


def test_needs_a_session(aw: GexApiWorld) -> None:
    assert aw.client().get("/api/gex/futures-oi").status_code == 401

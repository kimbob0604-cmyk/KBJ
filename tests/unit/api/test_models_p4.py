"""P4 응답 모델 — 모든 데이터 응답이 봉투(source·as_of·quality), 적용 불가 사유는 `NaReason` 전부,
금액 칸은 정수, 모르는 칸 거부(docs/p4_design.md §5.2).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from kbj.core.fin_rows import NaReason
from kbj.core.quality import Quality
from kbj.services.api.__main__ import openapi_text
from kbj.services.api.models.filings import OverhangSummaryView, WindowView
from kbj.services.api.models.fin import Financials, FinChecks
from kbj.services.api.models.stock import MultipleCell, ScenarioCase, StockHeader

P4_PREFIXES = ("/api/stock", "/api/filings", "/api/watchlist")


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return json.loads(openapi_text())


def _p4_paths(spec: dict[str, Any]) -> dict[str, Any]:
    return {p: ops for p, ops in spec["paths"].items() if p.startswith(P4_PREFIXES)}


def test_p4_routes_are_declared(spec: dict[str, Any]) -> None:
    paths = set(_p4_paths(spec))
    want = {
        "/api/stock/search",
        *(
            f"/api/stock/{{code}}/{s}"
            for s in (
                "header", "chart", "financials", "report", "report.xlsx", "segments",
                "consensus", "valuation", "scenario", "overhang", "insiders", "disclosures",
                "earnings",
            )
        ),
        "/api/filings/feed",
        "/api/filings/earnings",
        "/api/filings/overhang",
        "/api/filings/insiders",
        "/api/filings/schedule",
        "/api/watchlist",
        # P8 묶음 JNL — 관심종목 쓰기(test_routes_journal.py)
        "/api/watchlist/{list_name}/{code}",
        "/api/watchlist/{list_name}/order",
    }  # fmt: skip
    assert paths == want


def test_every_p4_data_route_returns_an_envelope(spec: dict[str, Any]) -> None:
    schemas = spec["components"]["schemas"]
    for path, ops in _p4_paths(spec).items():
        if "get" not in ops:  # P8 관심종목 쓰기 경로(PUT·DELETE·POST — test_routes_journal.py)
            continue
        resp = ops["get"]["responses"]
        assert "401" in resp and "404" in resp and "422" in resp, path
        if path.endswith(".xlsx"):
            content = resp["200"]["content"]
            xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            assert content[xlsx]["schema"]["format"] == "binary"
            continue
        ref = resp["200"]["content"]["application/json"]["schema"]["$ref"]
        name = ref.rsplit("/", 1)[-1]
        assert name.startswith("Envelope_"), path
        env = schemas[name]
        assert {"source", "as_of", "quality", "data", "generated_at"} <= set(env["required"])
        assert env["properties"]["as_of"]["format"] == "date-time"


def test_na_reason_enum_has_every_value(spec: dict[str, Any]) -> None:
    """M 이 더한 currency_mismatch·missing_input 까지 — 프런트 타입이 모든 사유를 안다."""
    enum = spec["components"]["schemas"]["NaReason"]["enum"]
    assert set(enum) == {r.value for r in NaReason}
    assert {"currency_mismatch", "missing_input"} <= set(enum)


def test_no_tam_identifier(spec: dict[str, Any]) -> None:
    """'TAM' 식별자를 새로 만들지 않는다 — 시나리오 적정가(D-P4-12)."""
    text = json.dumps(spec, ensure_ascii=False)
    assert "tam" not in {w.lower() for w in text.replace('"', " ").split()}
    assert "Scenario" in spec["components"]["schemas"]


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        MultipleCell(value=1.0, applicable=True, na_reason=None, junk=1)  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        ScenarioCase(case="mid", method="PER", eps=None, eps_source=None, multiple=None,  # pyright: ignore[reportArgumentType]
                     multiple_source=None, tp=None, upside_pct=None, na_reason=None,
                     quality=Quality.OK)  # fmt: skip


def test_money_fields_are_integers() -> None:
    with pytest.raises(ValidationError):
        OverhangSummaryView(
            as_of=date(2026, 11, 16), potential_new_shares=1.5, exercisable_shares=0,  # pyright: ignore[reportArgumentType]
            exchange_shares=0, denominator=None, denom_source=None, ratio_pct=None,
            exercisable_ratio_pct=None, listed_shares=None, login_ratio_pct=None, amount=None,
            itm_shares=None, quality=Quality.OK, notes=[],
        )  # fmt: skip
    w = WindowView(
        days=30, as_of=date(2026, 11, 16), net_shares=-1, buy_shares=0, sell_shares=1,
        n_reports=1, n_reporters=1, ratio_pct=None, reason_known_pct=None, amount=None,
        amount_missing=0, quality=Quality.OK,
    )  # fmt: skip
    assert w.amount is None


def test_na_reason_values_validate() -> None:
    f = Financials(
        applicable=False, na_reason="no_filings", code="994080", corp_code="99400080",  # pyright: ignore[reportArgumentType]
        basis="q", fs_div=None, currency=None, tier=None, rows=[], ratios=None,
        checks=FinChecks(diff3m_max_pct=None, n_derived=0, n_estimated=0),
    )  # fmt: skip
    assert f.na_reason is NaReason.NO_FILINGS
    with pytest.raises(ValidationError):
        Financials(
            applicable=False, na_reason="no_idea", code="994080", corp_code="99400080",  # pyright: ignore[reportArgumentType]
            basis="q", fs_div=None, currency=None, tier=None, rows=[], ratios=None,
            checks=FinChecks(diff3m_max_pct=None, n_derived=0, n_estimated=0),
        )  # fmt: skip


def test_header_kind_values() -> None:
    with pytest.raises(ValidationError):
        StockHeader(
            code="994010", name=None, market=None, kind="warrant", common_code=None,  # pyright: ignore[reportArgumentType]
            corp_code=None, corp_name=None, status_flags=None, listed_on=None, acc_mt=None,
            induty_code=None, last=None, is_watch=False, detail_tier=False, newhigh=None,
        )  # fmt: skip

"""`/api/stock/*` — 합성 20종목 세계에서 페이지 8 라우트가 기대 행렬(`p4_expect.yaml`)대로 답한다
(docs/p4_design.md §5·§8.4·§8.6).

본다: 섹션 상태(200·적용 불가 `na_reason`·404 `no_data`)·봉투 모델 검증·500 0·`NaN`/`Infinity` 0,
세션 없이 401, 모르는 코드 404 `unknown_code`, 쿼리 422, 우선주 → 회사 재무·배수, 엑셀 내려받기
머리글·ETag, 외부 호출 0, 정지 종목 차트(거래 없는 봉 제외)·신고가 3축 배지.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from kbj.core.quality import Quality
from kbj.core.rows import Bar
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.filings import Disclosures, Earnings, Insiders, Overhang
from kbj.services.api.models.fin import Financials, ReportView, Segments
from kbj.services.api.models.flows import StockFlowDetail
from kbj.services.api.models.stock import (
    Consensus,
    PriceChart,
    Scenario,
    StockHeader,
    Valuation,
)
from kbj.services.api.readers.stock import sma
from kbj.store.repos import MemoryRepos
from tests.unit.api.api_world_p4 import P4ApiWorld, build_p4_api_world, expect, shared

SECTIONS: dict[str, tuple[str, type[BaseModel]]] = {
    "header": ("header", StockHeader),
    "chart": ("chart", PriceChart),
    "financials": ("financials", Financials),
    "report": ("report", ReportView),
    "segments": ("segments", Segments),
    "consensus": ("consensus", Consensus),
    "valuation": ("valuation", Valuation),
    "scenario": ("scenario", Scenario),
    "overhang": ("overhang", Overhang),
    "insiders": ("insiders", Insiders),
    "disclosures": ("disclosures", Disclosures),
    "earnings": ("earnings", Earnings),
}
# 섹션 → 모델(봉투 data) — 다른 시험 파일(웹 픽스처)이 같이 쓴다
STOCK_MODELS: dict[str, type[BaseModel]] = {k: m for k, (_p, m) in SECTIONS.items()}
P4_PATHS: tuple[str, ...] = (
    "/api/stock/search?q=%EB%AA%A8%EC%9D%98",
    *(f"/api/stock/994010/{p}" for p, _m in SECTIONS.values()),
    "/api/stock/994010/report.xlsx",
    "/api/filings/feed",
    "/api/filings/earnings",
    "/api/filings/overhang",
    "/api/filings/insiders",
    "/api/filings/schedule",
    "/api/watchlist",
)


@pytest.fixture(scope="module")
def pw() -> P4ApiWorld:
    return shared()


@pytest.fixture(scope="module")
def c(pw: P4ApiWorld) -> TestClient:
    return pw.authed()


def _no_nan(text: str) -> None:
    def bad(x: str) -> Any:
        raise AssertionError(f"JSON 에 {x}")

    json.loads(text, parse_constant=bad)
    for word in ("NaN", "Infinity"):
        assert word not in text


def _check(route: str, exp: Any, r: Any) -> None:  # TestClient 응답(httpx 호환)
    assert r.status_code != 500, r.text
    if route == "report_xlsx":
        if exp == "no_data":
            assert r.status_code == 404 and r.json()["code"] == "no_data"
        else:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
        return
    if exp == "no_data":
        assert r.status_code == 404, r.text
        body = r.json()
        assert body["code"] == "no_data" and body["message"].startswith("아직 없음")
        return
    assert r.status_code == 200, r.text
    _no_nan(r.text)
    body = r.json()
    model = StockFlowDetail if route == "flows" else STOCK_MODELS[route]
    Envelope[model].model_validate(body)
    assert (
        body["source"].strip() and body["as_of"] and body["quality"] in {q.value for q in Quality}
    )
    parts = body["source"].split("+")
    assert len(parts) == len(set(parts)), body["source"]  # 원천은 한 번씩만(KRX+DART+KRX… 금지)
    data = body["data"]
    if route == "header":
        want = exp if isinstance(exp, dict) else {"status": exp, "flags": [], "stale": False}
        assert (data["status_flags"] or []) == want["flags"]
        assert data["last"]["stale"] is want["stale"]
        if want["stale"]:
            assert body["quality"] == "stale"
        return
    if route == "valuation":
        cur = data["current"]
        bands = {b["metric"]: b for b in data["bands"]}
        for m, cell in exp["current"].items():
            _cell_matches(cur[m]["applicable"], cur[m]["na_reason"], cell)
            if cell == "ok":
                assert isinstance(cur[m]["value"], float)
        for m, cell in exp["band"].items():
            _cell_matches(bands[m]["applicable"], bands[m]["na_reason"], cell)
        return
    if route == "scenario":
        if exp.startswith("na:"):
            assert data["applicable"] is False and data["method"] == "INSUFFICIENT"
            assert data["na_reason"] == exp[3:]
        else:
            assert data["applicable"] is True and data["method"] == exp[3:]
            assert data["cases"]["base"]["tp"] is not None
        return
    if isinstance(exp, str) and exp.startswith("na:"):
        assert data["applicable"] is False and data["na_reason"] == exp[3:]
        return
    assert exp == "ok"
    if "applicable" in data:
        assert data["applicable"] is True


def _cell_matches(applicable: bool, reason: str | None, cell: str) -> None:
    if cell == "ok":
        assert applicable is True and reason is None
    else:
        assert applicable is False and reason == cell[3:], (applicable, reason, cell)


def _path(code: str, route: str) -> str:
    if route == "flows":
        return f"/api/flows/stock/{code}"
    if route == "report_xlsx":
        return f"/api/stock/{code}/report.xlsx"
    return f"/api/stock/{code}/{SECTIONS[route][0]}"


@pytest.mark.parametrize("code", sorted(expect()["stocks"]))
def test_section_matrix(c: TestClient, code: str) -> None:
    """§8.4 — 20종목 × 라우트 14개: 500 0, 봉투 검증, 상태가 기대 행렬과 같다."""
    row = expect()["stocks"][code]
    for route in expect()["routes"]:
        r = c.get(_path(code, route))
        want = row[route]
        try:
            _check(route, want, r)
        except AssertionError as e:
            raise AssertionError(f"{code} {route}: {e}") from e


@pytest.mark.parametrize("path", [p for p in P4_PATHS] + ["/api/stock/994010/valuation?metric=pbr"])
def test_no_session_is_401(pw: P4ApiWorld, path: str) -> None:
    r = pw.client().get(path)
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


@pytest.mark.parametrize("section", [*SECTIONS, "report.xlsx"])
def test_unknown_code_is_404(c: TestClient, section: str) -> None:
    path = section if section == "report.xlsx" else SECTIONS[section][0]
    r = c.get(f"/api/stock/999999/{path}")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "unknown_code"
    assert "999999" not in r.text  # 입력값을 되돌리지 않는다


@pytest.mark.parametrize(
    "path",
    [
        "/api/stock/search",
        "/api/stock/search?q=",
        "/api/stock/search?q=" + "가" * 21,
        "/api/stock/search?q=a&limit=21",
        "/api/stock/994010/chart?days=0",
        "/api/stock/994010/chart?days=1241",
        "/api/stock/994010/financials?basis=y",
        "/api/stock/994010/financials?n=21",
        "/api/stock/994010/valuation?metric=evs",
        "/api/stock/994010/insiders?days=45",
        "/api/stock/994010/disclosures?days=366",
        "/api/stock/994010/disclosures?kind=Bad-Kind",
    ],
)
def test_bad_query_is_422(c: TestClient, path: str) -> None:
    r = c.get(path)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_request"


@pytest.mark.parametrize("path", ["/api/stock/99401/header", "/api/stock/abcdef/header"])
def test_bad_code_shape_has_no_route(c: TestClient, path: str) -> None:
    assert c.get(path).status_code in (404, 422)


def test_pref_uses_company_figures(c: TestClient) -> None:
    """우선주(#2) — 재무·컨센서스·배수는 회사(보통주 994010) 것, 시세는 자기 코드."""
    fin_p = c.get("/api/stock/994015/financials").json()
    fin_c = c.get("/api/stock/994010/financials").json()
    assert fin_p["data"]["rows"] == fin_c["data"]["rows"]
    assert fin_p["data"]["corp_code"] == fin_c["data"]["corp_code"] == "99400010"
    assert any("우선주" in n for n in fin_p["notes"])
    con = c.get("/api/stock/994015/consensus").json()["data"]
    assert con["code"] == "994010"
    val_p = c.get("/api/stock/994015/valuation").json()["data"]
    val_c = c.get("/api/stock/994010/valuation").json()["data"]
    for m in ("per", "pbr", "psr", "ev_ebitda"):  # 회사 배수 — 보통주와 같은 값(가격은 제 코드)
        assert val_p["current"][m] == val_c["current"][m], m
    assert {k["code"] for k in val_p["classes"]} == {"994010", "994015"}
    head = c.get("/api/stock/994015/header").json()["data"]
    assert head["kind"] == "pref" and head["common_code"] == "994010"
    chart_p = c.get("/api/stock/994015/chart?days=5").json()["data"]
    chart_c = c.get("/api/stock/994010/chart?days=5").json()["data"]
    assert chart_p["bars"] != chart_c["bars"]


def test_report_xlsx_headers(c: TestClient, pw: P4ApiWorld) -> None:
    r = c.get("/api/stock/994010/report.xlsx")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, no-cache"
    disp = r.headers["content-disposition"]
    assert disp.startswith("attachment;") and "filename*=UTF-8''" in disp
    assert "%EC%9E%AC%EB%AC%B4%EB%A6%AC%ED%8F%AC%ED%8A%B8.xlsx" in disp  # '재무리포트.xlsx'
    row = pw.repos.fin.report("994010")
    assert row is not None and r.content == row.xlsx
    again = c.get("/api/stock/994010/report.xlsx", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304
    # 우선주는 보통주 리포트
    assert c.get("/api/stock/994015/report.xlsx").content == row.xlsx


def test_report_view_matches_stored_payload(c: TestClient, pw: P4ApiWorld) -> None:
    body = c.get("/api/stock/994110/report").json()
    data = body["data"]
    row = pw.repos.fin.report("994110")
    assert row is not None
    assert data["fs_div_used"] == "OFS" and data["xlsx_available"] is True
    assert data["blocks"]["quarterly"] == json.loads(json.dumps(row.payload["quarterly"]))
    assert data["warnings"] == list(row.warnings)
    assert body["source"] == "DART"


def test_financials_annual_sums_quarters(c: TestClient) -> None:
    q = c.get("/api/stock/994010/financials?basis=q&n=20").json()["data"]["rows"]
    a = c.get("/api/stock/994010/financials?basis=a&n=4").json()["data"]["rows"]
    assert a, "연간 행이 있어야 한다"
    for row in a:
        year = row["fiscal_year"]
        qs = [x for x in q if x["fiscal_year"] == year]
        if len(qs) != 4:
            continue
        assert row["revenue"] == sum(x["revenue"] for x in qs)
        assert row["op"] == sum(x["op"] for x in qs)
        assert row["period"] == f"{year}FY" and row["fiscal_q"] is None


def test_financials_yoy_sign_change_is_text(c: TestClient) -> None:
    """metrics §9 — YoY 의 부호가 바뀌면 비율 대신 None + '흑자전환'·'적자전환'(재무 엔진 비율 행과
    같은 규칙). #8 적자(2025 3Q 부터): 2025Q3 영업이익 YoY = 적자전환, 둘 다 적자인 2026Q3 는
    비율."""
    rows = {
        r["period"]: r for r in c.get("/api/stock/994070/financials?n=20").json()["data"]["rows"]
    }
    q3 = rows["2025Q3"]
    assert q3["op"] < 0 < rows["2024Q3"]["op"]
    assert q3["op_yoy"] is None and "영업이익 적자전환" in q3["notes"]
    assert q3["rev_yoy"] is not None  # 매출은 부호가 그대로 — 비율
    both = rows["2026Q3"]
    assert both["op"] < 0 and q3["op"] < 0
    assert both["op_yoy"] == pytest.approx((both["op"] - q3["op"]) / abs(q3["op"]) * 100)
    assert not any("전환" in n for n in both["notes"])
    ratio = c.get("/api/stock/994070/financials").json()["data"]["ratios"]
    assert ratio["period"] == "2026Q3" and ratio["op_yoy"] == pytest.approx(both["op_yoy"])


def test_financials_labels_and_fallbacks(c: TestClient) -> None:
    """3월 결산 라벨(#7), 별도만(#12), 반기 보고 derived(#6), 외국 통화(#19)."""
    rows = c.get("/api/stock/994060/financials?n=4").json()["data"]["rows"]
    assert any(r["label"] == "FY26 2Q (26.07~26.09)" for r in rows)
    ofs = c.get("/api/stock/994110/financials").json()
    assert ofs["data"]["fs_div"] == "OFS" and any("OFS" in n for n in ofs["notes"])
    reit = c.get("/api/stock/994050/financials?n=4").json()["data"]
    assert reit["checks"]["n_derived"] >= 1
    assert all(r["revenue"] is None for r in reit["rows"] if r["derived"])
    usd = c.get("/api/stock/994180/financials").json()
    assert usd["data"]["currency"] == "USD" and any("환산하지 않는다" in n for n in usd["notes"])
    tier = c.get("/api/stock/994150/financials").json()["data"]
    assert tier["tier"] == "main"


def test_header_flags_and_newhigh(c: TestClient, pw: P4ApiWorld) -> None:
    halted = c.get("/api/stock/994030/header").json()
    assert halted["data"]["status_flags"] == ["halted"]
    assert halted["data"]["last"]["last_trade"] == "2026-11-06"
    assert any("정지" in n for n in halted["notes"])
    # 신고가 3축 — board 산출을 그대로(못 채운 축 None, 60일·252일 축은 없다 — ADR 0017)
    sd = pw.repos.board.stock_days(date(2026, 11, 13))
    assert sd
    for code in ("994010", "994020", "994160"):
        nh = c.get(f"/api/stock/{code}/header").json()["data"]["newhigh"]
        basis = sd[code].extra["close_basis"]
        assert nh["label"] == basis["label"] and nh["basis"] == "close"
        assert set(nh) >= {"d120", "w52", "hist"} and "d60" not in nh and "w252" not in nh
        for k in ("d120", "w52", "hist"):
            want = True if basis["hits"][k] else (None if basis["gap"][k] is None else False)
            assert nh[k] is want, (code, k)
    new = c.get("/api/stock/994020/header").json()["data"]["newhigh"]  # 신규상장 — 창 못 채움
    assert new["w52"] is None and new["d120"] is None
    assert c.get("/api/stock/994010/header").json()["data"]["newhigh"]["w52"] is not None


def test_header_detail_tier_and_watch(c: TestClient) -> None:
    assert c.get("/api/stock/994010/header").json()["data"]["detail_tier"] is True
    other = c.get("/api/stock/994150/header").json()["data"]
    assert other["is_watch"] is False and other["detail_tier"] is False


def test_chart_sma_and_events(c: TestClient) -> None:
    body = c.get("/api/stock/994160/chart?days=60").json()
    data = body["data"]
    closes = [b["c"] for b in data["bars"]]
    assert len(data["sma"]["20"]) == len(closes) == len(data["sma"]["120"])
    assert data["sma"]["20"][-1] == pytest.approx(sum(closes[-20:]) / 20)
    assert [e["kind"] for e in data["events"]] == ["split"]  # 액면분할(#17) 2026-10-26
    assert data["adjusted"] is False
    assert sma([1.0, 2.0, None, 4.0], 2) == [None, 1.5, None, None]


def test_valuation_series_weekly_and_metric(c: TestClient) -> None:
    per = c.get("/api/stock/994010/valuation?metric=per").json()["data"]
    pbr = c.get("/api/stock/994010/valuation?metric=pbr").json()["data"]
    weeks = [date.fromisoformat(p["date"]).isocalendar()[:2] for p in per["series"]]
    assert len(weeks) == len(set(weeks)) > 10
    assert per["metric"] == "per" and pbr["metric"] == "pbr"
    assert [p["value"] for p in per["series"]] != [p["value"] for p in pbr["series"]]
    assert per["mktcap_company"] and per["applicable"] is True


def test_valuation_reasons_from_job(c: TestClient) -> None:
    """적용 불가 사유는 밸류 작업이 정해 저장한 것 그대로(외국 통화 #19·재무 미제출 #9)."""
    usd = c.get("/api/stock/994180/valuation").json()["data"]
    assert usd["na_reason"] == "currency_mismatch" and usd["current"]["per"]["value"] is None
    nof = c.get("/api/stock/994080/scenario").json()["data"]
    assert nof["na_reason"] == "no_filings" and nof["cases"]["base"]["tp"] is None


def test_consensus_contents(c: TestClient, pw: P4ApiWorld) -> None:
    body = c.get("/api/stock/994010/consensus").json()
    data = body["data"]
    assert data["label"] == "KIS 추정"
    assert data["lost"] == ["분기 컨센서스", "분기 어닝 서프라이즈", "컨센서스 최고·최저"]
    assert any("KIS 교체로 잃음" in n for n in body["notes"])
    assert data["ntm"]["fy1_ym"] < (data["ntm"]["fy2_ym"] or "999999")
    t = data["target"]
    assert t["low"] <= t["median"] <= t["high"] and t["n_brokers"] >= 3
    assert data["opinions"]["n_brokers"] >= t["n_brokers"]
    assert len(data["recent"]) <= 20
    thin = c.get("/api/stock/994020/consensus").json()["data"]["target"]
    assert thin["quality"] == "estimated"  # 증권사 1~2곳


def test_no_external_calls(pw: P4ApiWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """API 는 외부를 부르지 않는다(계약 ⑩ — 런타임 가드, P4 라우트)."""
    calls: list[str] = []

    def boom(*a: Any, **k: Any) -> Any:
        calls.append(repr(a)[:80])
        raise AssertionError("외부 호출 금지")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    c = pw.authed()
    for path in P4_PATHS:
        assert c.get(path).status_code in (200, 404), path
    for code in ("994015", "994080", "994180"):
        for p, _m in SECTIONS.values():
            assert c.get(f"/api/stock/{code}/{p}").status_code in (200, 404)
    assert calls == []


# ── 저장소를 바꾸는 시험(따로 만든 세계) ──────────────────────────────────────────────


@pytest.fixture(scope="module")
def mutated() -> Iterator[P4ApiWorld]:
    def change(repos: MemoryRepos) -> None:
        # KRX 일별처럼 정지일에 시·고·저 0, 거래량 0 행을 넣는다(#4 정지 — 차트에서 빠져야 한다)
        zero = [
            Bar("994030", d, 0.0, 0.0, 0.0, 12670.0, 0, 0, "krx", "KRX", Quality.OK)
            for d in (date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 13))
        ]
        repos.market.upsert_daily_bars(zero, loaded_by="test")
        # 정지가 아닌데 거래가 없던 날(코넥스 #10 — 마지막 날 거래량 0 행): '정지'로 단정하지 않는다
        repos.market.upsert_daily_bars(
            [
                Bar(
                    "994090",
                    date(2026, 11, 13),
                    0.0,
                    0.0,
                    0.0,
                    5000.0,
                    0,
                    0,
                    "krx",
                    "KRX",
                    Quality.OK,
                )
            ],
            loaded_by="test",
        )
        # 우선주 매핑이 없는 경우(R13) — 994015 의 share_class 를 지운다
        repos.consensus.share_class_t.remove(lambda sc: sc.code == "994015")
        # 관심종목을 비운다 — 컨센서스 수집 대상이 시총 상위(top_n=0)뿐이 된다
        repos.journal.watch_t.remove(lambda _i: True)
        # #16(CFS·OFS 둘 다 있는 주요계정 층)의 2025 2Q CFS 를 지운다 — 연간·YoY 가 기준을 섞지
        # 않는지(2025 연간은 OFS 네 분기, 2026 2Q YoY 는 CFS 전년 없음 → None)
        repos.fin.quarter_t.remove(
            lambda q: (
                q.corp_code == "99400150"
                and (q.fiscal_year, q.fiscal_q, q.fs_div) == (2025, 2, "CFS")
            )
        )

    yield build_p4_api_world(valuation=False, transform=change)


def test_chart_drops_untraded_bars(mutated: P4ApiWorld) -> None:
    c = mutated.authed()
    data = c.get("/api/stock/994030/chart?days=30").json()["data"]
    assert data["bars"][-1]["date"] == "2026-11-06"
    assert all(b["v"] and b["v"] > 0 and b["o"] for b in data["bars"])
    assert data["last_trade"] == "2026-11-06"


def test_no_trade_day_is_not_called_halted(mutated: P4ApiWorld) -> None:
    c = mutated.authed()
    head = c.get("/api/stock/994090/header").json()
    assert head["data"]["status_flags"] in (None, [])
    assert head["data"]["last"]["stale"] is True and head["quality"] == "stale"
    assert any(n.startswith("거래 없음 — 마지막 거래") for n in head["notes"]), head["notes"]
    assert not any(n.startswith("정지") for n in head["notes"])
    chart = c.get("/api/stock/994090/chart?days=5").json()
    assert chart["data"]["bars"][-1]["date"] != "2026-11-13"
    assert any(n.startswith("거래 없음") for n in chart["notes"])
    halted = c.get("/api/stock/994030/chart?days=5").json()
    assert any(n.startswith("정지 — 마지막 거래 2026-11-06") for n in halted["notes"])


def test_pref_without_mapping_is_no_data(mutated: P4ApiWorld) -> None:
    c = mutated.authed()
    head = c.get("/api/stock/994015/header").json()
    assert head["data"]["common_code"] is None
    for p in ("financials", "consensus", "valuation", "overhang", "disclosures"):
        r = c.get(f"/api/stock/994015/{p}")
        assert r.status_code == 404 and "share_class" in r.json()["message"], p


def test_consensus_outside_universe_with_old_snaps(mutated: P4ApiWorld) -> None:
    """관심종목이 비어 수집 대상이 아니어도, 받아 둔 스냅이 있으면 보여 주고 그 사실을 적는다."""
    c = mutated.authed()
    body = c.get("/api/stock/994010/consensus").json()
    assert body["data"]["applicable"] is True
    assert any("수집 대상이 아니다" in n for n in body["notes"])
    none = c.get("/api/stock/994150/consensus").json()["data"]
    assert none["na_reason"] == "not_in_consensus_universe"


def test_valuation_without_rows_is_no_data(mutated: P4ApiWorld) -> None:
    c = mutated.authed()
    for path in ("/api/stock/994010/valuation", "/api/stock/994010/scenario"):
        r = c.get(path)
        assert r.status_code == 404 and "fin.valuation_band" in r.json()["message"]


def test_company_reason_without_valuation_rows(mutated: P4ApiWorld) -> None:
    """밸류 작업이 아직 행을 쓰지 않았어도 회사 단위로 정해지는 사유(외국 통화·재무 미제출)."""
    c = mutated.authed()
    usd = c.get("/api/stock/994180/valuation").json()["data"]
    assert usd["na_reason"] == "currency_mismatch"
    assert {b["na_reason"] for b in usd["bands"]} == {"currency_mismatch"}
    assert usd["current"]["per_fwd"]["na_reason"] == "currency_mismatch"
    nof = c.get("/api/stock/994080/scenario").json()["data"]
    assert nof["applicable"] is False and nof["na_reason"] == "no_filings"
    assert nof["method"] == "INSUFFICIENT" and nof["cases"]["bull"]["tp"] is None


def test_annual_and_yoy_do_not_mix_bases(mutated: P4ApiWorld) -> None:
    """metrics §9 — 같은 기준(fs_div) 안에서만 더하고 비교한다(재무 엔진 비율 행과 같은 규칙)."""
    c = mutated.authed()
    q = c.get("/api/stock/994150/financials?basis=q&n=20").json()["data"]["rows"]
    by = {r["period"]: r for r in q}
    ofs = {
        (x.fiscal_year, x.fiscal_q): x for x in mutated.repos.fin.quarters("99400150", fs_div="OFS")
    }
    cfs = {
        (x.fiscal_year, x.fiscal_q): x for x in mutated.repos.fin.quarters("99400150", fs_div="CFS")
    }
    assert (2025, 2) not in cfs and by["2025Q2"]["op"] == ofs[(2025, 2)].op  # 그 분기만 OFS
    assert by["2026Q2"]["op"] == cfs[(2026, 2)].op and by["2026Q2"]["op_yoy"] is None
    a = c.get("/api/stock/994150/financials?basis=a&n=4").json()["data"]["rows"]
    y2025 = next(r for r in a if r["fiscal_year"] == 2025)
    assert y2025["op"] == sum(ofs[(2025, k)].op or 0 for k in (1, 2, 3, 4))
    y2024 = next(r for r in a if r["fiscal_year"] == 2024)
    assert y2024["op"] == sum(cfs[(2024, k)].op or 0 for k in (1, 2, 3, 4))
    # 2025(OFS) 의 YoY 는 OFS 2024 와 — CFS 2024 와 섞지 않는다
    ofs_2024 = sum(ofs[(2024, k)].op or 0 for k in (1, 2, 3, 4))
    assert y2025["op_yoy"] == pytest.approx((y2025["op"] - ofs_2024) / abs(ofs_2024) * 100)


def test_search(c: TestClient) -> None:
    body = c.get("/api/stock/search?q=99K").json()
    assert [i["code"] for i in body["data"]["items"]] == ["99K170"]
    name = c.get("/api/stock/search?q=모의전자").json()["data"]
    assert [i["code"] for i in name["items"]][:2] == ["994010", "994015"]
    exact = c.get("/api/stock/search?q=994010").json()["data"]["items"][0]
    assert exact["code"] == "994010" and exact["is_watch"] is True
    limited = c.get("/api/stock/search?q=모의&limit=3").json()["data"]
    assert len(limited["items"]) == 3 and limited["n_total"] == 20

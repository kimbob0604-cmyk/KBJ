"""P4 완료 기준 — "종목 상세가 관심 종목 20개에서 오류 없이 나온다"
(docs/PLAN.md §8 P4, p4_design §0.3·§8.4).

합성 20종목 세계(`p4_world` — 신규상장·거래정지·우선주·스팩·리츠·비12월 결산·적자·재무 미제출·KONEX·
금융업·OFS·자본잠식·오버행·내부자·관심 밖·액면분할·영숫자 코드·외국기업·정리매매)에 P4 **실제
처리기**를 등록부 cron 순서대로 돌린 뒤(`p4_pipeline.py` — 가짜 DART·KIS·KRX, 가짜 시계
2026-11-16 21:30, 메모리 저장소) 페이지 8 라우트 14개 × 20종목을 부른다.

단언(§8.4): ① 500 = 0 ② 모든 200 봉투가 모델 검증 통과(source·as_of·quality) ③ 섹션 상태 =
`tests/fixtures/synthetic/p4_expect.yaml` 행렬 ④ 값이 들어간 칸에 `NaN`·`Infinity` 0 ⑤ 처리기·라우트
로그에 traceback 0 ⑥ 같은 세계를 공개 내보내기로: DART 재무가 있는 종목마다 `fin/<code>.json`(공개
안전 검사 통과·출처 DART 만·관심종목 흔적 0). 그 밖에 손계산 칸(오버행 #14 비율·내부자 #15 90일
순증감)과 그날 알림(관심종목 잠정실적 1통 — 정정 공시가 다시 울리지 않음, 리비전 1통)을 본다.

판정 함수(봉투·섹션 상태)는 API 시험(`tests/unit/api/test_routes_stock.py` — 묶음 A)의 것을 그대로
쓴다(두 벌 금지). 그 시험은 세계 참값을 저장소에 바로 넣은 세계를, 이 시험은 처리기를 거친 세계를
본다.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.core.calendar import TradingCalendar
from kbj.services.public_export.export import export_all
from kbj.services.public_export.manifest import MANIFEST_NAME, check_tree
from tests.acceptance.p4_pipeline import END, P4Pipeline
from tests.unit.api.api_world_p4 import expect
from tests.unit.api.test_routes_stock import _check, _path
from tests.unit.public_export.pub_rows import EVENTS, MemReader, PubWorld

TODAY = date(2026, 11, 16)


def _stocks() -> list[str]:
    return sorted(expect()["stocks"])


# ── 세계가 처리기로 만들어졌다 ─────────────────────────────────────────────────────────────


def test_world_has_twenty_stocks_and_the_expected_routes() -> None:
    assert len(_stocks()) == 20
    assert len(expect()["routes"]) == 14


def test_every_pipeline_run_finished_without_failure(pipeline: P4Pipeline) -> None:
    """처리기 실패 0 — 대기 정기보고서가 없는 fin.quarterly 는 skipped(정상)."""
    bad = [(r.job, r.as_of, r.status, r.detail) for r in pipeline.runs if r.status == "failed"]
    assert bad == []
    for job in (
        "filings.corp_code", "filings.company_profile", "fin.backfill", "filings.dart_feed",
        "krx.daily", "fin.valuation_band", "fin.quarterly", "filings.derive", "earnings.alerts",
        "earnings.backfill", "consensus.snapshot", "reports.dart_excel",
    ):  # fmt: skip
        runs = pipeline.runs_of(job)
        assert runs and all(r.status in ("ok", "skipped") for r in runs), job
        assert any(r.status == "ok" for r in runs), job
    # 등록부 cron 그대로 — 평일 07:00~19:59 매분(11-13·11-16)
    assert len(pipeline.runs_of("filings.dart_feed")) == 2 * 13 * 60
    # 정기보고서(11-13·11-16 접수)를 fin.quarterly 가 실제로 받았다(PIT 공시일이 그날)
    loaded = sum(int(r.detail.get("loaded", 0)) for r in pipeline.runs_of("fin.quarterly"))
    assert loaded > 0
    # 리포트는 처음 만든 뒤 재무가 바뀐 종목만 다시(11-16 정기보고서)
    built = [int(r.detail.get("built", 0)) for r in pipeline.runs_of("reports.dart_excel")]
    assert built[0] > 0 and built[-1] > 0 and sum(built[1:-1]) == 0


def test_no_traceback_in_handler_logs(pipeline: P4Pipeline) -> None:  # ⑤(처리기)
    assert [r.getMessage() for r in pipeline.tracebacks.records] == []


# ── 페이지 8 라우트 14개 × 20종목 ─────────────────────────────────────────────────────────

# 알려진 결함(묶음 S 가 이 시험으로 찾았다 — 결과 requests "S-R1"). P3 `krx.daily` 는 주식
# 유니버스의
# 종류를 넣지 않는다(kind=None — 분류 정본은 board 엔진 `kbj.engines.board.kinds.of`, krx_daily
# 머리말).
# P4 소비자(컨센서스 대상 `kbj.engines.consensus.universe.consensus_na`·수집기 우선주 판정, 밸류
# `kbj.engines.valuation.classes`·`services.engine.valuation` 의 스팩·우선주, API
# `readers.stock.ref`)는
# `UniverseRow.kind` 가 채워져 있다고 보아 실제 KRX 유니버스에서는 ① 컨센서스 대상이 0(전 종목
# not_in_consensus_universe) ② 우선주가 회사에 이어지지 않음(#2 의 회사 섹션 404) ③ 스팩 판정
# 실패(#5
# PER 적용)가 된다. 단위 시험의 세계는 유니버스에 참값 종류를 넣어 가렸다. 고치면(kind 가 None 이면
# `kinds.of(code, name)`) 이 칸들은 XPASS 로 실패하니 표에서 지운다(strict).
_KIND_BUG = "S-R1: krx.daily 유니버스 kind=None 을 P4 소비자가 분류하지 않는다(kinds.of 미사용)"
_PREF_COMPANY = (
    "financials", "report", "report_xlsx", "segments", "consensus", "valuation", "scenario",
    "overhang", "insiders", "disclosures", "earnings",
)  # fmt: skip
KNOWN_BUG: dict[tuple[str, str], str] = {
    **{
        (code, "consensus"): _KIND_BUG
        for code, row in expect()["stocks"].items()
        if row["consensus"] in ("ok", "no_data")
    },
    **{("994015", route): _KIND_BUG for route in _PREF_COMPANY},
    ("994040", "valuation"): _KIND_BUG,
}


def _cells() -> list[Any]:
    out: list[Any] = []
    for code in _stocks():
        for route in expect()["routes"]:
            why = KNOWN_BUG.get((code, route))
            marks = [pytest.mark.xfail(strict=True, reason=why)] if why else []
            out.append(pytest.param(code, route, marks=marks, id=f"{code}-{route}"))
    return out


@pytest.mark.parametrize(("code", "route"), _cells())
def test_section_matrix(
    client: TestClient, code: str, route: str, caplog: pytest.LogCaptureFixture
) -> None:
    """① 500 0 ② 봉투 검증 ③ 상태 = 기대 행렬 ④ NaN·Infinity 0 ⑤ traceback 0(라우트)."""
    with caplog.at_level(logging.INFO):
        r = client.get(_path(code, route))
    assert r.status_code != 500, r.text  # ① 은 알려진 결함 칸에도 성립해야 한다 — xfail 밖에서 먼저
    _check(route, expect()["stocks"][code][route], r)
    assert [rec.getMessage() for rec in caplog.records if rec.exc_info] == []


def test_no_500_anywhere_including_known_bug_cells(client: TestClient) -> None:
    """① 은 결함 칸까지 전부 — 결함이 있어도 섹션은 격리된 상태(200·404)로 답한다."""
    codes = [
        (code, route, client.get(_path(code, route)).status_code)
        for code in _stocks()
        for route in expect()["routes"]
    ]
    assert [c for c in codes if c[2] >= 500] == []
    assert {c[2] for c in codes} <= {200, 404}


@pytest.mark.xfail(strict=True, reason=_KIND_BUG)
def test_universe_kind_is_resolved_for_p4_consumers(pipeline: P4Pipeline) -> None:
    """결함 S-R1 의 뿌리 — 실제 krx.daily 를 거친 유니버스에서도 P4 컨센서스 대상 규칙이 보통주를
    안다."""
    from kbj.engines.consensus.universe import consensus_na

    uni = {u.code: u for u in pipeline.repos.market.universe(date(2026, 11, 13))}
    assert uni["994010"].kind is None  # P3 규칙 그대로(kind 는 원천이 정한 ETF·ETN 만)
    assert consensus_na(uni["994010"]) is None


def test_overhang_ratio_is_the_hand_calculation(client: TestClient) -> None:
    """#14 CB 3·BW 1(리픽싱·일부 청구) — 잠재 주식·비율 = 손계산(p4_expect hand)."""
    hand = expect()["hand"]["overhang_994130"]
    body = client.get("/api/stock/994130/overhang").json()
    s = body["data"]["summary"]
    assert s["potential_new_shares"] == hand["potential_new_shares"]
    assert s["exercisable_shares"] == hand["exercisable_shares"]
    assert s["denominator"] == hand["denominator"]
    assert s["ratio_pct"] == pytest.approx(hand["ratio_pct"], abs=1e-3)
    assert s["exercisable_ratio_pct"] == pytest.approx(hand["exercisable_ratio_pct"], abs=1e-3)


def test_halted_stock_chart_ends_before_the_halt(client: TestClient, pipeline: P4Pipeline) -> None:
    """#4 거래정지(최근 5거래일) — krx.daily 를 거친 차트의 마지막 봉 = 정지 전 마지막
    거래일(§8.4)."""
    s = pipeline.w.by_no(4)
    assert "halted" in s.flags
    last_world = max(b.date for b in pipeline.w.bars(s.code))
    bars = client.get(f"/api/stock/{s.code}/chart").json()["data"]["bars"]
    assert bars and bars[-1]["date"] == last_world.isoformat()
    assert all(b["o"] and b["v"] for b in bars[-5:])  # 정지일 0 봉(시가 0·거래량 0)이 끼지 않는다


def test_insider_windows_are_the_hand_calculation(client: TestClient) -> None:
    """#15 내부자 대량 매도 + 5% 보고 — 30·90·180일 창 = 손계산(정정은 늦은 접수번호만)."""
    hand = expect()["hand"]["insider_994140"]
    body = client.get("/api/stock/994140/insiders").json()
    wins = {w["days"]: w for w in body["data"]["windows"]}
    for days, want in hand.items():
        got = wins[int(days)]
        for k in ("net_shares", "buy_shares", "sell_shares", "n_reports", "n_reporters"):
            assert got[k] == want[k], (days, k)
        assert got["ratio_pct"] == pytest.approx(want["ratio_pct"], abs=1e-6), days


# ── 그날 알림(§8.5 와 같은 세계) ─────────────────────────────────────────────────────────


def test_alert_earnings_once_for_the_watchlist_prelim(pipeline: P4Pipeline) -> None:
    """관심종목 잠정실적 중 우선순위 ≤ 2 만 1통 — 정정 공시(16:20)는 같은 subject 라 다시 울리지
    않는다."""
    want = expect()["alerts"]["alert.earnings"]
    calls = [c for c in pipeline.notify.calls if c.get("kind") == "alert.earnings"]
    assert {c["subject"] for c in calls} == {want["subject"]}
    sent = [c for c in calls if c["subject"] == want["subject"]]
    assert len(sent) == want["count"]
    # 본문 숫자는 입력에서만(템플릿 — LLM 없음): 같은 텍스트가 지어낸 'NaN' 을 싣지 않는다
    assert all("NaN" not in c["text"] and "None" not in c["text"] for c in calls)


@pytest.mark.xfail(strict=True, reason=_KIND_BUG + " — 컨센서스 대상 0 이라 리비전 묶음이 없다")
def test_alert_revision_once_a_day_for_the_watchlist(pipeline: P4Pipeline) -> None:
    """리비전 묶음 하루 1통(16:00 — ADR 0018). 관심 밖·그날 스냅이 없는 종목은 싣지 않는다."""
    want = expect()["alerts"]["alert.revision"]
    runs = [r for r in pipeline.runs_of("consensus.snapshot") if r.as_of == TODAY.isoformat()]
    assert len(runs) == 1 and runs[0].status == "ok", runs
    sent = [
        c
        for c in pipeline.notify.calls
        if c.get("kind") == "alert.revision" and str(c.get("as_of", ""))[:10] == str(TODAY)
    ]
    assert len(sent) == want["count"]
    text = sent[0]["text"]
    for code in want["codes"]:
        assert code in text, code
    others = {s.code for s in pipeline.w.stocks} - set(want["codes"])
    assert not [c for c in others if c in text]


# ── ⑥ 공개 내보내기 ──────────────────────────────────────────────────────────────────────


def _corp_rows(pipeline: P4Pipeline) -> list[dict[str, Any]]:
    """`pub_filings.corp_code` 행(열 이름 dict) — filings.corp_code 처리기가 쓴 것."""
    return [
        {
            "corp_code": c.corp_code,
            "stock_code": c.stock_code or None,
            "corp_name": c.corp_name,
            "modify_date": c.modify_date,
            "source": "DART",
            "quality": "ok",
            "received_at": at,
        }
        for c, at in pipeline.corp_store.rows.values()
    ]


@pytest.fixture(scope="module")
def public_out(pipeline: P4Pipeline, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("p4-public")
    world = PubWorld(repos=pipeline.repos.public(), corp_code_rows=_corp_rows(pipeline))
    export_all(
        None, out, now=END, cal=TradingCalendar.default(), events=EVENTS,
        reader=MemReader(world), fin_cfg=pipeline.fin_cfg, reports_cfg=pipeline.reports_cfg,
    )  # fmt: skip
    return out


def test_public_export_tree_is_safe(public_out: Path) -> None:
    assert check_tree(public_out) == []
    assert (public_out / MANIFEST_NAME).exists()


def test_public_fin_file_for_every_company_with_dart_financials(
    public_out: Path, pipeline: P4Pipeline
) -> None:
    want = set(expect()["public_fin_codes"])
    got = {p.stem for p in (public_out / "fin").glob("*.json")}
    assert want <= got, sorted(want - got)
    watch = {i.code for i in pipeline.repos.journal.watchlist("main")}
    for code in sorted(want):
        text = (public_out / "fin" / f"{code}.json").read_text(encoding="utf-8")
        doc = json.loads(text)
        assert doc["source"] == "DART" and doc["as_of"] and doc["quality"], code
        assert doc["data"]["code"] == code
        # 공개판은 관심종목을 모른다(D-P4-13) — 표식·목록 0
        lowered = text.lower()
        for word in ("watch", "prv_journal", "관심"):
            assert word not in lowered, (code, word)
    # 관심 목록 자체가 공개 산출물 어디에도 없다(순서 흔적 포함)
    listing = json.dumps(sorted(watch))
    for p in public_out.rglob("*.json"):
        assert listing not in p.read_text(encoding="utf-8"), p.name

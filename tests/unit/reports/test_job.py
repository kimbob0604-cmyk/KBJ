"""`reports.dart_excel` 작업(kbj.reports.dart_excel.job) — 메모리 저장소 + 합성 세계.

docs/p4_design.md §1.5·§3.2·§4.6. 본다: 대상(관심종목 ∪ 공개 데모, 우선주·재무 없음·외국 통화 제외),
원시 행 캐시로 만든 엑셀이 legacy 골든과 같음(같은 행이면 같은 결과), 원문 메모(호출 절반), 입력
요약이 같으면 건너뜀, 공급계약 공시가 늘면 다시 만듦, 부문 매출, 종목별 실패·예산 닫힘·크기 상한.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from kbj.core.fin_rows import WatchItem
from kbj.core.time import KST
from kbj.data.budget import BudgetExhausted
from kbj.reports.dart_excel import job
from kbj.services.collectors import fin
from tests.fixtures.synthetic.p4_world import CLOCK
from tests.golden.dart_report.golden import PIPELINE, read_json
from tests.oracles.xlsx_normalize import diff, normalize
from tests.unit.reports.world_state import WorldState, disclosure_row, make_state

WATCH = ("994010", "994015", "994110", "994180", "994080")
CORPS = {"99400010", "99400110", "99400180", "99400080"}


def _state() -> WorldState:
    st = make_state(corps=CORPS, detail=set(WATCH))
    items = [
        WatchItem("main", code, code, CLOCK - timedelta(days=30), i, None, "import:sd")
        for i, code in enumerate(WATCH)
    ]
    st.journal.replace_watchlist("main", items, loaded_by="test", now=CLOCK)
    res = fin.quarterly(st.ctx("fin.quarterly"))
    assert res.status == "ok", res.detail
    return st


@pytest.fixture(scope="module")
def built() -> tuple[WorldState, Any]:
    st = _state()
    n0 = len(st.dart.calls)
    res = job.run(st.ctx("reports.dart_excel"))
    st.extra["_calls"] = len(st.dart.calls) - n0
    return st, res


def test_대상과_건너뜀_사유(built: tuple[WorldState, Any]) -> None:
    st, res = built
    assert res.status == "ok", res.detail
    d = res.detail
    assert d["built"] == 2 and d["skipped.not_in_dart"] == 1  # 994015 우선주
    assert d["skipped.currency"] == 1 and d["skipped.no_financials"] == 1  # 994180·994080
    assert st.repos.fin.report("994010") is not None and st.repos.fin.report("994180") is None
    r = st.repos.fin.report("994110")
    assert r is not None and r.fs_div_used == "OFS" and r.engine_version == job.ENGINE_VERSION
    assert (r.year_from, r.year_to, r.built_on) == (2023, 2026, date(2026, 11, 16))


@pytest.mark.parametrize("stock", ["994010", "994110"])
def test_원시_행_캐시로_만든_엑셀이_legacy_골든과_같다(
    built: tuple[WorldState, Any], stock: str
) -> None:
    st, _ = built
    row = st.repos.fin.report(stock)
    assert row is not None and row.xlsx is not None
    exp = read_json(PIPELINE / f"{stock}.json.gz")
    out: list[str] = []
    diff(stock, exp["workbook"], normalize(row.xlsx), out)
    assert not out, "\n".join(out)
    assert row.payload["meta"]["stock_code"] == stock
    assert list(row.warnings) == exp["inputs"]["warnings"]


def test_원문은_한_번만_재무제표는_캐시(built: tuple[WorldState, Any]) -> None:
    st, res = built
    assert res.detail["raw_hits"] > 0
    legacy_calls = sum(
        len(read_json(PIPELINE / f"{s}.json.gz")["calls"]) for s in ("994010", "994110")
    )
    assert st.extra["_calls"] < legacy_calls / 2  # 재무제표 캐시 + 원문 메모


def test_부문_매출을_사업보고서에서(built: tuple[WorldState, Any]) -> None:
    st, res = built
    segs = st.repos.fin.segments("99400010")
    assert res.detail["segments"] > 0 and {s.bsns_year for s in segs} == {2023, 2024, 2025}
    assert {s.segment for s in segs} == {"모의부문A(전자부품)", "모의부문B(장비)", "기타"}


def test_입력이_같으면_건너뛰고_계약이_늘면_다시() -> None:
    st = _state()
    assert job.run(st.ctx("reports.dart_excel")).detail["built"] == 2
    n = len(st.dart.calls)
    again = job.run(st.ctx("reports.dart_excel", run_id="run-2"))
    assert again.detail["skipped.unchanged"] == 2 and len(st.dart.calls) == n
    w = st.world
    src = next(d for d in w.disclosures if d.corp_code == "99400110" and "공급계약" in d.title)
    new = replace(disclosure_row(src), rcept_no="20261117800009", rcept_dt=date(2026, 11, 17))
    st.repos.filings.upsert_disclosures([new], loaded_by="test", now=CLOCK)
    nxt = datetime(2026, 11, 17, 21, 0, tzinfo=KST)
    res = job.run(st.ctx("reports.dart_excel", run_id="run-3", now=nxt))
    assert res.detail["built"] == 1 and res.detail["skipped.unchanged"] == 1
    assert st.repos.fin.report_dates("994110") == [date(2026, 11, 16), date(2026, 11, 17)]


def test_종목별_실패와_예산_닫힘() -> None:
    st = _state()
    real = st.client

    class Broken:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

        def disclosures(self, corp: str, *a: Any, **k: Any) -> Any:
            if corp == "99400110":
                raise self.exc
            return real.disclosures(corp, *a, **k)

    st.extra["dart"] = Broken(BudgetExhausted("dart", CLOCK.date(), 18000, 18000))
    res = job.run(st.ctx("reports.dart_excel"))
    assert res.status == "failed" and res.detail["stopped"] == "BudgetExhausted"
    assert "BudgetExhausted" in res.detail["reason"]
    assert any(r.status == "failed" for r in st.claims.rows)


def test_DART_자료_없음은_그_종목만_건너뛴다() -> None:
    from kbj.data.public.dart.client import DartError

    st = _state()
    real = st.client

    class NoFile:
        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

        def disclosures(self, corp: str, *a: Any, **k: Any) -> Any:
            if corp == "99400110":
                raise DartError("014", "파일이 존재하지 않습니다.", "document.xml")
            return real.disclosures(corp, *a, **k)

    st.extra["dart"] = NoFile()
    res = job.run(st.ctx("reports.dart_excel"))
    assert res.status == "ok" and res.detail["skipped.dart_no_data"] == 1
    assert any(r.status == "failed" for r in st.claims.rows)  # 내일 다시 본다


def test_엑셀_크기_상한() -> None:
    st = _state()
    st.reports_cfg = st.reports_cfg.model_copy(update={"xlsx_max_bytes": 1000})
    res = job.run(st.ctx("reports.dart_excel"))
    assert res.status == "failed" and res.detail["failures"] == 2
    assert "2종목 실패" in res.detail["reason"]
    assert st.repos.fin.report("994010") is None

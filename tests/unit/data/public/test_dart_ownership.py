"""지분공시 — kbj.data.public.dart.ownership(묶음 F, §1.3·§4.4·§8.2).

elestock·majorstock 은 회사의 보고 전체를 준다 [확인 필요 #32]. 합성 세계 #15(모의에너지)의 소유보고
6건·대량보유 3건(정정 포함)을 읽어 참값(`P4World.insider_reports`·`major_reports`)과 맞춘다 — 변동
사유는 원문 표에서. 오류 문구에 보고자 이름을 싣지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import replace
from functools import cache
from pathlib import Path
from typing import Any

from kbj.data.public.dart import ownership as O
from kbj.data.public.dart.client import DartClient
from kbj.data.public.dart.doctext import largest
from tests.fakes.dart_server import KEY, FakeDart
from tests.fixtures.synthetic.p4_world import CLOCK, build_world
from tests.unit.data.public._support import RecordingLimiter

DOCS = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "dart" / "p4_docs.json"


@cache
def _docs() -> dict[str, Any]:
    return json.loads(DOCS.read_text(encoding="utf-8"))["docs"]


def client_for(fake: FakeDart) -> DartClient:
    return DartClient(
        KEY, transport=fake.transport("test"), limiter=RecordingLimiter(), retries=1,
        backoff_s=0, now=lambda: CLOCK,
    )  # fmt: skip


def test_insider_history_matches_truth_with_reason_from_the_document() -> None:
    w = build_world()
    corp = w.by_no(15).company_corp
    got = O.fetch_insider(client_for(FakeDart(lambda: CLOCK, world=w)), corp)
    assert got.bad == []
    truth = {r.rcept_no: r for r in w.insider_reports}
    assert {r.rcept_no for r in got.rows} == set(truth)
    for r in got.rows:
        reason = O.parse_reason(largest(list(w.documents(r.rcept_no))) or "")
        assert replace(r, reason=reason) == truth[r.rcept_no]
        assert r.reason is None  # 구조화 응답에는 사유 칸이 없다(추정) — 처리기가 원문에서 붙인다


def test_registered_and_major_holder_markers() -> None:
    w = build_world()
    by = {r.rcept_no: r for r in w.insider_reports}
    entity = by[w.rcept("e15.ins_major")]
    assert (entity.registered, entity.major_holder, entity.position) == (None, True, None)
    officer = by[w.rcept("e15.ins_cfo")]
    assert (officer.registered, officer.major_holder) == (False, False)
    row = {"rcept_no": "20261116000099", "corp_code": "99400140", "rcept_dt": "20261116",
           "repror": "가상", "isu_exctv_rgist_at": "등기 임원", "isu_main_shrholdr": "주요 주주",
           "sp_stock_lmp_cnt": "1,000", "sp_stock_lmp_irds_cnt": "-500"}  # fmt: skip
    (r,) = O.parse_insider_rows([row]).rows
    assert (r.registered, r.major_holder, r.shares_delta, r.ratio_after) == (True, True, -500, None)


def test_major_holder_reports_match_truth() -> None:
    w = build_world()
    got = O.fetch_major(client_for(FakeDart(lambda: CLOCK, world=w)), w.by_no(15).company_corp)
    assert got.bad == []
    assert sorted(got.rows, key=lambda r: r.rcept_no) == sorted(
        w.major_reports, key=lambda r: r.rcept_no
    )


def test_bad_rows_are_counted_without_the_reporter_name() -> None:
    rows = [
        {"rcept_no": "20261116000001", "corp_code": "99400140", "repror": ""},  # 보고자 없음
        {"rcept_no": "nope", "corp_code": "99400140", "repror": "비밀이름"},
        {"rcept_no": "20261116000002", "corp_code": "99400140", "repror": "가상",
         "rcept_dt": "-", "sp_stock_lmp_cnt": "-"},
    ]  # fmt: skip
    got = O.parse_insider_rows(rows)
    assert got.bad == ["20261116000001", "nope"]
    assert "비밀이름" not in json.dumps(got.bad, ensure_ascii=False)
    (ok,) = got.rows
    assert ok.rcept_dt.isoformat() == "2026-11-16"  # 접수일이 비면 접수번호 앞 8자리
    assert ok.shares_after is None  # '-' 는 0 이 아니라 None


def test_empty_company_is_an_empty_result() -> None:
    c = client_for(FakeDart(lambda: CLOCK, world=build_world()))
    assert O.fetch_insider(c, "99400010").rows == []
    assert O.fetch_major(c, "99400010").rows == []


def test_reason_from_document_table() -> None:
    assert O.parse_reason(str(_docs()["insider_doc"]["html"])) == "장내매도(-)"
    assert O.parse_reason(str(_docs()["generic_notice"]["html"])) is None

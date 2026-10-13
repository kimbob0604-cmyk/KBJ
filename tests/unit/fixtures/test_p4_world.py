"""합성 20종목 세계(`tests/fixtures/synthetic/p4_world.py`)·기대 행렬(`p4_expect.yaml`)의 시험.

근거: docs/p4_design.md §1.1(M — 결정성·20종목 SYNTHETIC·코드 형식)·§8.4(완료 시험 표 20행).
`tests/fixtures/` 아래에는 시험 파일을 두지 않는다 — 그래서 여기(tests/unit/fixtures)에 둔다.
여기서 쓰는 SD·DR 정규식은 legacy 원본의 모양을 시험 안에 다시 적은 것이다(legacy 를 import 하지
않는다).
"""

from __future__ import annotations

import io
import itertools
import json
import re
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.core.fin_rows import NaReason, PrelimScope, is_corp_code, is_stock_code
from kbj.core.rows import GROUP4, INST7, Investor
from kbj.core.time import KST
from kbj.data.private.krx.models import parse_base_info_rows
from kbj.data.private.krx.stocks import parse_stock_rows
from kbj.data.public.dart.corp_code import parse_corp_codes
from kbj.store.legacy_import import transforms as tf
from tests.fakes.kis_server import SYMBOLS
from tests.fixtures.synthetic.p4_world import (
    CLOCK,
    LAST_KRX_DAY,
    ROUTES,
    SEED,
    TODAY,
    P4World,
    build_world,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic"
EXPECT = FIX / "p4_expect.yaml"


@pytest.fixture(scope="module")
def w() -> P4World:
    return build_world()


@pytest.fixture(scope="module")
def expect() -> dict[str, Any]:
    with EXPECT.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert isinstance(doc, dict)
    return doc


def _at(hhmm: str, day: date = TODAY) -> datetime:
    h, m = hhmm.split(":")
    return datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=KST)


# ── 결정성·형식 ──────────────────────────────────────────────────────────────────────


def test_same_seed_same_world_other_seed_other_world(w: P4World) -> None:
    assert build_world() is w  # 캐시
    fresh = build_world.__wrapped__(SEED)
    assert fresh is not w and fresh.digest() == w.digest()
    other = build_world.__wrapped__(SEED + 1)
    assert other.digest() != w.digest()
    # 시드가 바꾸는 것은 잡음뿐 — 종목·사건·손계산 기대값은 같다
    assert [s.record() for s in other.stocks] == [s.record() for s in w.stocks]
    assert other.expect_overhang() == w.expect_overhang()


def test_twenty_synthetic_stocks_with_valid_codes(w: P4World) -> None:
    assert [s.no for s in w.stocks] == list(range(1, 21))
    recs = [s.record() for s in w.stocks]
    assert all(str(r["_source"]).startswith("SYNTHETIC") for r in recs)
    codes = [s.code for s in w.stocks]
    assert len(set(codes)) == 20 and all(is_stock_code(c) for c in codes)
    assert all(c.startswith("99") for c in codes)  # 합성 대역
    corps = [c.corp_code for c in w.corps]
    assert len(set(corps)) == len(corps) == 29  # 상장 19 + 비상장 잡음 10
    assert all(is_corp_code(c) and c.startswith("994") for c in corps)
    # 다른 가짜·합성 fixture 의 코드와 겹치지 않는다
    assert not set(codes) & set(SYMBOLS)
    krx = json.loads((FIX / "krx" / "stock_daily.json").read_text(encoding="utf-8"))
    krx_codes = {r["ISU_CD"] for mk in ("kospi", "kosdaq") for rows in krx[mk].values()
                 for r in rows}  # fmt: skip
    assert not set(codes) & krx_codes
    fake_dart_stock = {f"99{i:04d}" for i in range(0, 40, 2)}  # tests/fakes/dart_server.corp_zip
    fake_dart_corp = {f"{90000000 + i:08d}" for i in range(40)}
    assert not set(codes) & fake_dart_stock and not set(corps) & fake_dart_corp
    # 기록·응답에 SYNTHETIC 표시
    assert str(w.dart_json("company.json", {"corp_code": "99400010"}, now=CLOCK)["_source"]
               ).startswith("SYNTHETIC")  # fmt: skip
    assert all(str(u.flags["_source"]).startswith("SYNTHETIC") for u in w.universe())


# ── §8.4 표 20행의 함정이 세계에 정말 있다 ───────────────────────────────────────────


def test_section_8_4_cases_are_present(w: P4World) -> None:
    s = w.by_no
    # 1·2 우선주: 끝자리 ≠ 0, 이름 '…우', corp_code 없음 → 회사 = 1번
    assert s(2).kind == "pref" and s(2).code[-1] != "0" and s(2).name.endswith("우")
    assert s(2).corp_code is None and s(2).company_corp == s(1).corp_code
    assert s(2).common_code == s(1).code
    # 3 신규상장 20거래일
    assert len(w.bars(s(3).code)) == 20 and s(3).listed_on == w.trading_days[-20]
    # 4 거래정지 마지막 5거래일 — 봉 없음, 스냅은 halted·종가 그대로
    last5 = set(w.trading_days[-5:])
    assert not [b for b in w.bars(s(4).code) if b.date in last5]
    halted = [x for x in w.snaps(code=s(4).code) if x.date in last5]
    assert len(halted) == 5 and all(x.status_flags == ("halted",) for x in halted)
    assert len({x.close for x in halted}) == 1 and all(x.volume == 0 for x in halted)
    # 5 스팩 매출 0
    assert s(5).kind == "spac" and all(q.revenue == 0 for q in w.quarter_truth(s(5).company_corp))
    # 6 리츠 — 반기·연간 보고서만
    periodic6 = [d.title for d in w.disclosures if d.corp_code == s(6).corp_code
                 and d.kind == "periodic"]  # fmt: skip
    assert periodic6 and all(t.startswith(("반기보고서", "사업보고서")) for t in periodic6)
    # 7 3월 결산 — 라벨
    q7 = w.quarter_truth(s(7).company_corp)[-1]
    assert q7.period.label == "FY26 2Q (26.07~26.09)"
    # 8 적자 — PIT(11-13) TTM 지배순익 < 0, 그 전에는 흑자
    q8 = [q for q in w.quarter_truth(s(8).company_corp) if q.period.end <= date(2026, 6, 30)]
    assert sum(q.ni_owner for q in q8[-4:]) < 0 and sum(q.ni_owner for q in q8[-12:-8]) > 0
    # 9 재무 미제출·관리종목
    assert not w.quarter_truth(s(9).company_corp) and "managed" in s(9).flags
    assert [d for d in w.disclosures if d.corp_code == s(9).corp_code]
    # 10 코넥스
    assert s(10).market == "KONEX" and w.corp(s(10).company_corp).corp_cls == "N"
    # 11 금융업
    assert w.corp(s(11).company_corp).is_financial
    assert w.quarter_truth(s(11).company_corp)[-1].cogs is None
    # 12 OFS 만
    assert w.corp(s(12).company_corp).fs_divs == ("OFS",)
    # 13 자본잠식 — 2026 1Q 부터 자본 < 0. 밸류 as_of(11-13) PIT 상태표는 2026 3Q(11-13 접수)
    q13 = {q.key: q for q in w.quarter_truth(s(13).company_corp)}
    assert q13[(2026, 2)].total_equity < 0 < q13[(2025, 2)].total_equity
    assert q13[(2026, 3)].equity_owner < 0
    assert w.disclosure("r13.2026.11014").rcept_dt == LAST_KRX_DAY
    # 14 CB 3 · BW 1 + 조정·청구
    ev = [e for e in w.overhang_events if e.corp_code == s(14).corp_code]
    assert sorted(e.instrument.value for e in ev) == ["bw", "cb", "cb", "cb", "paid_in"]
    kinds = sorted(a.kind for a in w.overhang_adjusts)
    assert kinds == ["exercise", "exercise", "price_adjust", "price_adjust"]
    # 15 내부자 매도 + 5% 보고(정정 포함)
    assert any(
        r.shares_delta is not None and r.shares_delta <= -1_000_000 for r in w.insider_reports
    )
    assert any(d.title.startswith("[기재정정]주식등의대량보유") for d in w.disclosures)
    # 16 관심 밖·상세 층 아님
    assert not s(16).watch and not s(16).detail_tier and s(16).consensus == "none"
    # 17 액면분할 5:1 — 상장주식수 ×5, 시총은 이어진다(±15%)
    split = w.splits[0]
    assert (split.code, split.ratio) == (s(17).code, 5.0)
    before = w.trading_days[w.trading_days.index(split.effective_date) - 1]
    sn = {x.date: x for x in w.snaps(code=s(17).code)}
    assert sn[split.effective_date].shares == 5 * (sn[before].shares or 0)
    cap0, cap1 = sn[before].mktcap or 0, sn[split.effective_date].mktcap or 0
    assert 0.85 < cap1 / cap0 < 1.15
    # 18 영숫자 코드
    assert re.fullmatch(r"[0-9]{2}[A-Z][0-9]{3}", s(18).code)
    # 19 외국기업 — 재무 통화 USD
    assert w.corp(s(19).company_corp).currency == "USD"
    assert {q.currency for q in w.quarter_truth(s(19).company_corp)} == {"USD"}
    # 20 정리매매
    assert "liquidation" in s(20).flags
    assert w.snaps(LAST_KRX_DAY, code=s(20).code)[0].status_flags == ("liquidation",)


# ── DART 응답 ─────────────────────────────────────────────────────────────────────────


def test_list_json_pages_newest_first_and_hides_future(w: P4World) -> None:
    p1 = w.dart_json("list.json", {"bgn_de": "20261116", "page_count": "100"}, now=CLOCK)
    assert p1["status"] == "000" and p1["total_page"] >= 3 and len(p1["list"]) == 100
    p3 = w.dart_json("list.json", {"bgn_de": "20261116", "page_count": "100", "page_no": "3"},
                     now=CLOCK)  # fmt: skip
    seen = [x["rcept_no"] for x in p1["list"]] + [x["rcept_no"] for x in p3["list"]]
    assert len(seen) == len(set(seen))
    # 접수 시각 내림차순 — 11-16 #15 정정 잠정실적(16:20)이 원 공시(15:35)보다 앞
    order = [x["rcept_no"] for x in p1["list"]]
    assert order.index(w.rcept("e15.prelim_fix")) < order.index(w.rcept("e15.prelim"))
    # 아직 '접수'되지 않은 공시는 안 보인다
    noon = w.dart_json("list.json", {"bgn_de": "20261116", "page_count": "100"}, now=_at("12:00"))
    assert noon["total_count"] < p1["total_count"]
    assert all(_at("12:00") >= w.disclosure_by_rcept(x["rcept_no"]).posted_at
               for x in noon["list"])  # fmt: skip
    # 거르기: 회사·공시유형 / 없는 날은 013
    a = w.dart_json("list.json", {"corp_code": "99400010", "bgn_de": "20200101",
                                  "end_de": "20261231", "pblntf_ty": "A", "page_count": "100"},
                    now=CLOCK)  # fmt: skip
    titles = [x["report_nm"].removeprefix("[기재정정]") for x in a["list"]]
    assert titles and all(t.startswith(("분기", "반기", "사업")) for t in titles)
    assert w.dart_json("list.json", {"bgn_de": "20261115"}, now=CLOCK)["status"] == "013"


def test_corp_zip_and_company_follow_dart_shapes(w: P4World) -> None:
    corps = parse_corp_codes(w.corp_zip())
    listed = {c.stock_code: c.corp_code for c in corps if c.stock_code}
    assert len(corps) == 29 and len(listed) == 19
    assert "994015" not in listed  # 우선주는 고유번호 표에 없다
    assert listed["99K170"] == "99400170"
    c7 = w.dart_json("company.json", {"corp_code": "99400060"}, now=CLOCK)
    assert (c7["acc_mt"], c7["corp_cls"], c7["stock_code"]) == ("03", "Y", "994060")
    assert w.dart_json("company.json", {"corp_code": "99499999"}, now=CLOCK)["status"] == "013"
    assert {p.corp_code for p in w.profiles} == set(listed.values())


def test_statements_reconstruct_truth_ofs_fallback_and_pit(w: P4World) -> None:
    def rows(
        corp: str, year: int, reprt: str, fs: str, now: datetime = CLOCK
    ) -> list[dict[str, str]]:
        params = {"corp_code": corp, "bsns_year": str(year), "reprt_code": reprt, "fs_div": fs}
        body = w.dart_json("fnlttSinglAcntAll.json", params, now=now)
        return list(body.get("list") or [])

    def cum(rs: list[dict[str, str]], acc_id: str) -> int:
        r = next(x for x in rs if x["account_id"] == acc_id)
        return int(r.get("thstrm_add_amount") or r["thstrm_amount"])

    # DR 방식 누적 → 분기 단독(4Q = 연간 − 3분기 누적)이 참값과 같다
    truth = {q.key: q for q in w.quarter_truth("99400010", "CFS")}
    for year in (2024, 2025):
        c = [cum(rows("99400010", year, r, "CFS"), "ifrs-full_Revenue")
             for r in ("11013", "11012", "11014", "11011")]  # fmt: skip
        quarters = [c[0], c[1] - c[0], c[2] - c[1], c[3] - c[2]]
        assert quarters == [truth[(year, k)].revenue for k in (1, 2, 3, 4)]
    # 정정 보고서가 접수된 뒤에는 정정본 접수번호
    fy25 = rows("99400010", 2025, "11011", "CFS")
    assert {r["rcept_no"] for r in fy25} == {w.rcept("r1.2025.11011.fix")}
    # 1번은 포괄손익계산서(CIS) — 그 밖은 IS
    assert {r["sj_div"] for r in fy25} == {"BS", "CIS", "CF"}
    # OFS 만 있는 회사: CFS 는 013(연결 미작성), OFS 는 행
    assert not rows("99400110", 2025, "11011", "CFS")
    assert rows("99400110", 2025, "11011", "OFS")
    # PIT — 11-16 17:10 접수 전에는 2026 3Q 가 없다(#15)
    assert not rows("99400140", 2026, "11014", "CFS", _at("17:00"))
    assert rows("99400140", 2026, "11014", "CFS", _at("17:30"))
    # 3개월값 대조 함정(#4 2025 3Q 매출 — 1.5% 차이)
    r4 = next(x for x in rows("99400030", 2025, "11014", "CFS")
              if x["account_id"] == "ifrs-full_Revenue")  # fmt: skip
    t4 = {q.key: q for q in w.quarter_truth("99400030", "CFS")}[(2025, 3)].revenue
    assert abs(int(r4["thstrm_amount"]) / t4 - 1.015) < 1e-3
    # 금융업은 영업수익·영업비용, 매출원가 없음
    f11 = rows("99400100", 2026, "11014", "CFS")
    assert {"dart_OperatingRevenue", "dart_OperatingExpenses"} <= {r["account_id"] for r in f11}
    assert "ifrs-full_CostOfSales" not in {r["account_id"] for r in f11}
    # 외국기업 통화
    assert {r["currency"] for r in rows("99400180", 2026, "11014", "CFS")} == {"USD"}
    # 리츠 — 1·3분기 보고서 없음
    assert not rows("99400050", 2026, "11013", "OFS") and rows("99400050", 2026, "11012", "OFS")
    # 주요계정(다중회사): 쉼표 금액, 회사별 연결·별도
    multi = w.dart_json("fnlttMultiAcnt.json", {"corp_code": "99400010,99400110",
                                                "bsns_year": "2025", "reprt_code": "11011"},
                        now=CLOCK)  # fmt: skip
    got = {(r["corp_code"], r["fs_div"]) for r in multi["list"]}
    assert got == {("99400010", "CFS"), ("99400010", "OFS"), ("99400110", "OFS")}
    assert "," in multi["list"][0]["thstrm_amount"]


def test_share_totals_match_truth(w: P4World) -> None:
    body = w.dart_json("stockTotqySttus.json", {"corp_code": "99400130", "bsns_year": "2026",
                                                "reprt_code": "11014"}, now=CLOCK)  # fmt: skip
    common = next(r for r in body["list"] if r["se"] == "보통주")
    assert common["istc_totqy"] == "41,250,000"
    sc = [x for x in w.share_counts if x.corp_code == "99400130"]
    assert max(sc, key=lambda x: x.period_end).common_issued == 41_250_000


# ── 원문 ───────────────────────────────────────────────────────────────────────────────

# SD earnings_parser.parse_preliminary_earnings:181 의 정규식 모양(시험 안에 다시 적음)
_NUM = r"([\-\d,]+|-)"
_PCT = r"([\-\d,.]+|-)"
_ROW = rf"당해실적\s+{_NUM}\s+{_NUM}\s+{_PCT}\s+(\S+)\s+{_NUM}\s+{_PCT}"


def _clean(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _n(s: str) -> int:
    return int(s.replace(",", ""))


def test_prelim_forms_parse_with_sd_patterns(w: P4World) -> None:
    unit_mult = {"억원": 100_000_000, "백만원": 1_000_000, "천원": 1_000}
    for p in w.prelims:
        files = w.documents(p.rcept_no)
        assert len(files) == 1
        text = _clean(files[0][1])
        unit = re.search(r"단위\s*[:：]\s*([가-힣%원,\s]+)", text[:3000])
        assert unit is not None
        label = next(k for k in unit_mult if k in unit.group(1))
        assert unit_mult[label] == p.unit_mult
        for metric, name in (("revenue", "매출액"), ("op", "영업이익"), ("ni", "당기순이익")):
            m5 = re.search(rf"{name}\s+{_ROW}", text)
            m2 = re.search(rf"{name}\s+당해실적\s+{_NUM}\s+{_NUM}", text)
            assert (m5 or m2) is not None
            cur = _n((m5 or m2).group(1)) * p.unit_mult  # type: ignore[union-attr]
            assert cur == getattr(p, f"{metric}_cur")
            if m5 is not None:
                assert _n(m5.group(5)) * p.unit_mult == getattr(p, f"{metric}_prev_y")
            else:
                assert getattr(p, f"{metric}_prev_y") is None  # 2열 폼(#18)
        # 본문 범위 표기가 제목보다 우선(#18 은 제목이 별도 꼴이지만 본문은 연결)
        if p.scope is PrelimScope.STANDALONE:
            assert "별도재무제표" in text
        else:
            assert "연결재무제표" in text
        assert p.period_inferred == ("실적기간" not in text)
    by = {w.disclosure_by_rcept(p.rcept_no).ident: p for p in w.prelims}
    assert by["e18.prelim"].scope is PrelimScope.CONSOLIDATED
    assert w.disclosure("e18.prelim").title == "영업(잠정)실적(공정공시)"
    # #15: 영업익 YoY ≥ 20%(YOY_BIG_UP — 우선순위 1), 정정본도 같은 기간·범위
    p15, f15 = by["e15.prelim"], by["e15.prelim_fix"]
    assert p15.op_cur is not None and p15.op_prev_y is not None
    assert (p15.op_cur - p15.op_prev_y) / abs(p15.op_prev_y) >= 0.2
    assert (
        (p15.period, p15.scope) == (f15.period, f15.scope) == ("2026Q3", PrelimScope.CONSOLIDATED)
    )


def test_overhang_documents_and_structured_rows(w: P4World) -> None:
    cb = w.dart_json("cvbdIsDecsn.json", {"corp_code": "99400130", "bgn_de": "20240101",
                                          "end_de": "20261116"}, now=CLOCK)  # fmt: skip
    assert [r["bd_tm"] for r in cb["list"]] == ["1", "2", "3"]
    first = cb["list"][0]
    assert (first["bd_fta"], first["cv_prc"], first["cvisstk_cnt"]) == (
        "10,000,000,000", "12,000", "833,333",
    )  # fmt: skip
    # 구조화 응답이 빈 칸일 때 쓰는 원문 폴백 — SD overhang_parser 정규식 모양
    html = w.documents(w.rcept("e14.cb1"))[0][1]
    assert re.search(r"전환가\s*[액]?\s*[:：]?\s*([\d,]+)\s*원", html)
    assert re.search(r"사채의?\s*액면\s*총액\s*[:：]?\s*([\d,]+)\s*원", html)
    assert re.search(r"(?:전환|행사)\s*청구\s*기간\s*[:：]?\s*\d{4}-\d{2}-\d{2}\s*~", html)
    bw_html = w.documents(w.rcept("e14.bw1"))[0][1]
    assert "전환가" not in bw_html and re.search(
        r"행사가\s*[액]?\s*[:：]?\s*([\d,]+)\s*원", bw_html
    )
    paid = w.documents(w.rcept("e14.paid"))[0][1]
    assert re.search(r"신주\s*상장\s*예정일\s*[:：]?\s*2026-07-15", paid)
    # 조정·청구는 원문만(구조화 API 없음) — 대상 결정 공시를 회차로 잇는다
    adj = _clean(w.documents(w.rcept("e14.adj2"))[0][1])
    assert "조정후 전환가액(원)" in adj and "7,200" in adj
    assert all(a.target_rcept_no for a in w.overhang_adjusts)
    # 미래(접수 전) 원문은 없다
    assert w.dart_bytes("document.xml", {"rcept_no": w.rcept("e14.bwex")}, now=_at("14:00")) is None
    z = w.dart_bytes("document.xml", {"rcept_no": w.rcept("e14.bwex")}, now=CLOCK)
    assert z is not None and zipfile.ZipFile(io.BytesIO(z)).namelist()


def test_ownership_rows_and_corrections(w: P4World) -> None:
    ele = w.dart_json("elestock.json", {"corp_code": "99400140"}, now=CLOCK)["list"]
    assert len(ele) == 6 and ele[-1]["sp_stock_lmp_irds_cnt"] == "-500,000"
    maj = w.dart_json("majorstock.json", {"corp_code": "99400140"}, now=CLOCK)["list"]
    assert [m["stkrt"] for m in maj] == ["5.12", "5.21", "6.30"]
    assert w.insider_base == {w.rcept("e15.ins_major_fix"): w.rcept("e15.ins_major")}
    assert w.dart_json("elestock.json", {"corp_code": "99400010"}, now=CLOCK)["status"] == "013"


def test_g2_periodic_documents_have_three_files_and_notes(w: P4World) -> None:
    r1 = w.rcept("r1.2026.11014")
    files = dict(w.documents(r1))
    assert len(files) == 3  # 본문·연결 주석·별도 주석 — 이중 합산 함정
    body = _clean(files[f"{r1}.xml"])
    for title in ("사업부문별 매출실적", "비용의 성격별 분류", "금융수익", "금융원가", "기타수익",
                  "기타비용"):  # fmt: skip
        assert title in body
    assert "당분기 누적" in body  # 분기 보고서는 3개월·누적 두 열
    # 연결 표 합계(누적, 백만원) = 손익계산서(매출 − 영업이익)
    qs = [q for q in w.quarter_truth("99400010", "CFS") if q.period.year == 2026]
    cost_cum = sum(q.revenue - q.op for q in qs)
    total = re.search(r"비용의 성격별 분류 \(단위: 백만원\) .*? 합계 \S+ (\S+)", body)
    assert total is not None and _n(total.group(1)) * 1_000_000 == cost_cum
    r12 = w.rcept("r12.2025.11011")
    assert "천원" in w.documents(r12)[0][1] and len(w.documents(r12)) == 2


# ── 시세·투자자(KRX·KIS) ──────────────────────────────────────────────────────────────


def test_krx_rows_parse_with_p3_parser(w: P4World) -> None:
    raw = w.krx_daily_rows(LAST_KRX_DAY)
    total = 0
    for ep, market in (("/sto/stk_bydd_trd", "kospi"), ("/sto/ksq_bydd_trd", "kosdaq"),
                       ("/sto/knx_bydd_trd", "konex")):  # fmt: skip
        ok, bad = parse_stock_rows(raw[ep], market=market)  # pyright: ignore[reportArgumentType]
        assert not bad
        total += len(ok)
    assert total == 20
    base = w.krx_base_rows(LAST_KRX_DAY)
    for ep, rows in base.items():
        ok_b, bad_b = parse_base_info_rows(rows, endpoint=ep, bas_dd=LAST_KRX_DAY)
        assert ok_b and not bad_b
    bars = w.bars()
    assert {b.source for b in bars} == {"krx"}
    assert min(b.date for b in bars) == date(2021, 6, 1)
    assert max(b.date for b in bars) == LAST_KRX_DAY


def test_investor_rows_close_checks(w: P4World) -> None:
    rows = w.investors()
    assert rows and {r.date for r in rows} <= set(w.trading_days[-20:])
    by: dict[tuple[str, date], dict[Investor, int]] = {}
    for r in rows:
        assert r.net_value is not None
        by.setdefault((r.code, r.date), {})[r.investor] = r.net_value
    for vals in by.values():
        assert sum(vals[g] for g in GROUP4) == 0  # 검산 ①
        assert sum(vals[i] for i in INST7) == vals[Investor.INSTITUTION]  # 검산 ②
    assert not [r for r in rows if r.code == "994030" and r.date in set(w.trading_days[-5:])]


# ── 컨센서스(KIS 의미값) ──────────────────────────────────────────────────────────────


def _fy1_metric(w: P4World, code: str, metric: str, day: date) -> float:
    rows = [r for r in w.consensus_rows(code, day) if r.is_estimate]
    fy1 = min(rows, key=lambda r: r.fiscal_ym)
    v = getattr(fy1, metric)
    assert v is not None
    return float(v)


def test_consensus_revisions_have_designed_signals(w: P4World) -> None:
    t = TODAY
    base = max(d for d in w.trading_days if d <= t - timedelta(days=30))

    def pct(code: str, metric: str = "op") -> float:
        a, b = _fy1_metric(w, code, metric, t), _fy1_metric(w, code, metric, base)
        return (a - b) / abs(b) * 100

    assert 5 <= pct("994010") < 15  # UP
    assert pct("994060") >= 15  # STRONG_UP
    assert pct("994140") <= -15  # STRONG_DOWN
    assert _fy1_metric(w, "994070", "ni", base) < 0 < _fy1_metric(w, "994070", "ni", t)  # TURN
    assert abs(pct("994100")) < 5
    # 관심 밖·유니버스 밖·빈 응답 종목은 행이 없다
    for code in ("994040", "994050", "994080", "994090", "994120", "994150", "994180",
                 "994190", "994015"):  # fmt: skip
        assert not w.consensus_rows(code)
    # 실적(A) 행 = DART 참값 연간 합(억원 단위로 맞춤)
    a = [r for r in w.consensus_rows("994010", t) if not r.is_estimate]
    fy25 = next(r for r in a if r.fiscal_ym == "202512")
    rev25 = sum(q.revenue for q in w.quarter_truth("99400010") if q.period.year == 2025)
    assert fy25.revenue == round(rev25 / 1e8) * 100_000_000
    assert {r.fiscal_ym for r in w.consensus_rows("994060", t)} >= {"202603", "202703"}


def test_opinions_cover_window_rules(w: P4World) -> None:
    brokers = {o.code: {x.broker for x in w.opinion_rows(o.code)} for o in w.opinions}
    assert len(brokers["994010"]) == 6 and len(brokers["994020"]) == 2
    nr = [o for o in w.opinions if o.opinion == "Not Rated"]
    assert nr and all(o.target_price == 0.0 for o in nr)
    # 1번의 마지막 증권사는 90일 창 밖(오래된 의견만)
    stale = [o for o in w.opinion_rows("994010") if o.broker == "예시증권"]
    assert stale and max(o.opinion_date for o in stale) < TODAY - timedelta(days=90)
    # opinion_prev 는 같은 증권사의 바로 앞 의견
    rows = w.opinion_rows("994030")
    for prev, cur in itertools.pairwise(rows):
        if prev.broker == cur.broker:
            assert cur.opinion_prev == prev.opinion


# ── 관심종목 ─────────────────────────────────────────────────────────────────────────


def test_watchlist_is_19_and_sd_doc_imports(w: P4World) -> None:
    codes = [x.code for x in w.watchlist]
    assert len(codes) == 19 and "994150" not in codes
    assert [x.sort for x in w.watchlist] == list(range(19))
    assert {x.source for x in w.watchlist} == {"import:sd"}
    doc = w.sd_watchlist_doc()
    cands: list[Any] = []
    for i, item in enumerate(doc):
        cands.extend(tf.sd_watchlist({**item, "_index": i}))
    kept = [c for c in cands if isinstance(c, dict)]
    assert [c["code"] for c in kept] == codes
    assert len(cands) - len(kept) == 1  # 미국 티커 1건(not_kr)


# ── 기대 행렬(p4_expect.yaml) ─────────────────────────────────────────────────────────

_STATUS = re.compile(r"ok|no_data|ok:(PER|PBR)|na:[a-z_]+")


def _check_status(v: object) -> None:
    assert isinstance(v, str) and _STATUS.fullmatch(v), v
    if v.startswith("na:"):
        NaReason(v[3:])  # 모르는 사유면 ValueError


def test_expect_matrix_covers_twenty_stocks_and_fourteen_routes(
    w: P4World, expect: dict[str, Any]
) -> None:
    assert str(expect["_source"]).startswith("SYNTHETIC")
    assert expect["seed"] == SEED and tuple(expect["routes"]) == ROUTES
    assert len(ROUTES) == 14
    assert datetime.fromisoformat(expect["clock"]) == CLOCK
    stocks = expect["stocks"]
    assert list(stocks) == [s.code for s in w.stocks]
    for s in w.stocks:
        row = stocks[s.code]
        assert row["case"] == s.no and set(row) == {"case", *ROUTES}  # 'no' 는 YAML 에서 false
        for route in ROUTES:
            v = row[route]
            if route == "valuation":
                assert set(v) == {"current", "band"}
                for part in v.values():
                    assert set(part) == {"per", "pbr", "psr", "ev_ebitda"}
                    for cell in part.values():
                        _check_status(cell)
            elif route == "header":
                status = v if isinstance(v, str) else v["status"]
                _check_status(status)
                flags = [] if isinstance(v, str) else v["flags"]
                assert tuple(flags) == s.flags
            else:
                _check_status(v)
        # 세계와 맞물림
        if s.consensus == "none" and s.market != "KONEX":
            assert row["consensus"] == "na:not_in_consensus_universe"
        if s.consensus == "empty":
            assert row["consensus"] == "no_data"
        if not s.detail_tier:
            assert row["valuation"]["current"]["ev_ebitda"] == "na:not_detail_tier"
    assert stocks["994015"]["valuation"] == stocks["994010"]["valuation"]  # 우선주 = 회사 배수
    assert stocks["994090"]["consensus"] == "na:konex_no_consensus"
    assert set(expect["public_fin_codes"]) == {
        s.code for s in w.stocks if s.kind != "pref" and w.quarter_truth(s.company_corp)
    }


def test_hand_calculations_match_world(w: P4World, expect: dict[str, Any]) -> None:
    oh = w.expect_overhang()
    want = expect["hand"]["overhang_994130"]
    for k, v in want.items():
        assert oh[k] == v, k
    # 손계산을 한 번 더 — 정의를 그대로 적은 산수(엔진과 독립)
    by_hand = (
        7_000_000_000 // 12_000
        + 15_000_000_000 // 7_200
        + 5_800_000_000 // 11_000
        + 5_000_000_000 // 9_000
    )
    assert oh["potential_new_shares"] == by_hand
    ins = w.expect_insider_windows()
    for days, vals in expect["hand"]["insider_994140"].items():
        assert ins[int(days)] == vals
    assert ins[90]["net_shares"] == -300_000 + 20_000 - 1_150_000 - 500_000
    # 창은 닫힌 구간 [t − W, t](docs/metrics.md §13) — 10-15 보고(+20,000)는 t=11-14 의 30일 창
    # 첫날이라 들어가고, t=11-15 에는 빠진다
    assert w.expect_insider_windows(date(2026, 11, 14))[30]["buy_shares"] == 20_000
    assert w.expect_insider_windows(date(2026, 11, 15))[30]["buy_shares"] == 0
    # 유상증자 결정 행은 권면총액이 없다(fin_rows.OverhangEvent — 증자는 face_value None)
    paid = [e for e in w.overhang_events if e.instrument.value == "paid_in"]
    assert paid and all(e.face_value is None and e.shares for e in paid)
    alert = expect["alerts"]["alert.earnings"]
    p15 = next(p for p in w.prelims if p.rcept_no == w.rcept("e15.prelim"))
    assert alert["subject"] == f"{p15.corp_code}:{p15.period}:{p15.scope.value}"

"""분기 정규화(`normalize`) — 합성 세계 참값과 원 단위로 같은지(docs/p4_design.md §4.5·§8.3).

사례: #1 연결(CFS)·별도(OFS) 따로, #12 OFS 만, #4 3개월값 ≠ 누적 차분(1.5% — estimated), #11 금융업
(영업수익·EBITDA 없음), #7 3월 결산(라벨·기간), #6 반기 보고만(차분 derived), #19 외국 통화(USD
그대로). 주요계정 층: 직전 누적을 저장한 분기 단독 행에서 되살린다.
"""

from __future__ import annotations

from functools import cache

import pytest

from kbj.core.fin_rows import REPRT, CorpProfile, QuarterRow, StatementRaw
from kbj.core.quality import Quality
from kbj.engines.financials.normalize import (
    cumulative_from_quarters,
    quarter_rows,
    rcept_date,
    report_of_raw,
)
from tests.fixtures.synthetic.p4_world import P4World, build_world

FIN = ("64", "65", "66")


@cache
def world() -> P4World:
    return build_world()


def raws(corp: str, fs: str, *, multi: bool = False) -> list[StatementRaw]:
    w = world()
    out: list[StatementRaw] = []
    for year in range(2020, 2027):
        for rc in REPRT:
            if multi:
                js = w.dart_json("fnlttMultiAcnt.json",
                                 {"corp_code": corp, "bsns_year": str(year), "reprt_code": rc},
                                 now=w.clock)  # fmt: skip
                rows = [r for r in js.get("list", []) if r["fs_div"] == fs]
            else:
                js = w.dart_json("fnlttSinglAcntAll.json",
                                 {"corp_code": corp, "bsns_year": str(year), "reprt_code": rc,
                                  "fs_div": fs}, now=w.clock)  # fmt: skip
                rows = js.get("list", [])
            if rows:
                out.append(StatementRaw(corp, year, rc, fs, rows[0]["rcept_no"], tuple(rows),  # type: ignore[arg-type]
                                        rows[0].get("currency")))  # fmt: skip
    return out


def profile(corp: str) -> CorpProfile:
    return next(p for p in world().profiles if p.corp_code == corp)


def rows_of(corp: str, fs: str) -> list[QuarterRow]:
    reps = [report_of_raw(r) for r in raws(corp, fs)]
    return quarter_rows(reps, profile(corp), tier="full", corp_code=corp, financial_prefixes=FIN)


@pytest.mark.parametrize(("corp", "fs"), [("99400010", "CFS"), ("99400010", "OFS"),
                                          ("99400110", "OFS"), ("99400160", "CFS")])  # fmt: skip
def test_참값과_원_단위로_같다(corp: str, fs: str) -> None:
    truth = {t.key: t for t in world().quarter_truth(corp, fs)}
    rows = rows_of(corp, fs)
    assert len(rows) == len(truth) and not any(r.derived for r in rows)
    for r in rows:
        t = truth[(r.fiscal_year, r.fiscal_q)]
        assert (r.revenue, r.cogs, r.op, r.pretax, r.tax, r.ni) == (
            t.revenue, t.cogs, t.op, t.pretax, t.tax, t.ni)  # fmt: skip
        assert r.ni_owner == (t.ni_owner if fs == "CFS" else t.ni)
        assert r.d_and_a == t.dep + t.amort and r.ebitda == t.ebitda
        assert (r.total_assets, r.total_liabilities, r.total_equity, r.cash, r.short_inv) == (
            t.total_assets, t.total_liabilities, t.total_equity, t.cash, t.short_inv)  # fmt: skip
        assert r.total_debt == t.total_debt
        assert r.net_debt == t.total_debt - t.cash - t.short_inv
        assert r.equity_owner == (t.equity_owner if fs == "CFS" else t.total_equity)
        assert r.minority == (t.minority if fs == "CFS" else 0)
        assert r.eps_basic == pytest.approx(t.eps, abs=1.0)  # 보고서는 원 단위 반올림
        assert (r.period_start, r.period_end) == (t.period.start, t.period.end)
        assert r.tier == "full" and r.currency == "KRW" and r.quality == Quality.OK
        assert r.rcept_dt is not None and r.rcept_dt >= r.period_end


def test_3개월값이_차분과_1퍼센트_넘게_다르면_추정() -> None:
    rows = {r.period: r for r in rows_of("99400030", "CFS")}
    r = rows["2025Q3"]
    assert r.quality == Quality.ESTIMATED and r.diff3m_max_pct == pytest.approx(1.4778, abs=1e-3)
    assert r.reported_3m["revenue"] != r.revenue  # 값은 차분(대조 열만 3개월값)
    assert any("정정 반영 차이" in n for n in r.notes)
    assert rows["2025Q2"].quality == Quality.OK and rows["2025Q2"].diff3m_max_pct == 0.0


def test_금융업은_매출총이익_EBITDA_없음() -> None:
    rows = rows_of("99400100", "CFS")
    r = rows[-1]
    assert r.revenue is not None and r.revenue > 0  # 영업수익
    assert r.cogs is None and r.gross_profit is None and r.ebitda is None
    assert any("financial_sector" in n for n in r.notes)


def test_3월_결산_기간과_라벨() -> None:
    rows = rows_of("99400060", "CFS")
    last = rows[-1]
    assert (last.fiscal_year, last.fiscal_q) == (2026, 2)
    assert (str(last.period_start), str(last.period_end)) == ("2026-07-01", "2026-09-30")


def test_반기_보고만_하면_분기_단독이_없다() -> None:
    rows = rows_of("99400050", "OFS")
    assert rows and all(r.derived and r.revenue is None for r in rows)
    assert all(r.total_assets is not None for r in rows)  # 상태표는 시점값


def test_외국_통화는_환산하지_않는다() -> None:
    rows = rows_of("99400180", "CFS")
    assert {r.currency for r in rows} == {"USD"}
    t = next(x for x in world().quarter_truth("99400180", "CFS") if x.key == (2026, 3))
    r = next(x for x in rows if x.period == "2026Q3")
    assert r.revenue == t.revenue  # 달러 그대로


def test_결산월_모름은_추정() -> None:
    reps = [report_of_raw(r) for r in raws("99400110", "OFS")[:2]]
    rows = quarter_rows(reps, None, tier="full", corp_code="99400110")
    assert all(r.quality == Quality.ESTIMATED for r in rows)
    assert all("결산월 모름" in r.notes[0] for r in rows)


def test_주요계정_층은_직전_누적을_되살린다() -> None:
    """3Q 주요계정만 받은 날 — 1Q·2Q 분기 단독 행(저장분)의 합으로 반기 누적을 만들어 차분."""
    corp = "99400150"  # #16 관심 밖(주요계정 층)
    mraws = {(r.bsns_year, r.reprt_code): r for r in raws(corp, "CFS", multi=True)}
    stored: list[QuarterRow] = []
    for rc in ("11013", "11012"):
        reps = [report_of_raw(mraws[(2026, rc)])]
        if rc == "11012":
            prev = cumulative_from_quarters(stored, 2026, 1, "CFS")
            assert prev is not None
            reps.insert(0, prev)
        stored += quarter_rows(reps, profile(corp), tier="main", corp_code=corp,
                               keep=[(2026, 1 if rc == "11013" else 2)])  # fmt: skip
    prev = cumulative_from_quarters(stored, 2026, 2, "CFS")
    assert prev is not None
    got = quarter_rows([prev, report_of_raw(mraws[(2026, "11014")])], profile(corp), tier="main",
                       corp_code=corp, keep=[(2026, 3)])  # fmt: skip
    t = next(x for x in world().quarter_truth(corp, "CFS") if x.key == (2026, 3))
    (r,) = got
    assert (r.revenue, r.op, r.pretax, r.ni) == (t.revenue, t.op, t.pretax, t.ni)
    assert r.tier == "main" and r.d_and_a is None and r.ebitda is None
    assert r.ni_owner == r.ni and r.quality == Quality.ESTIMATED  # 지배순이익 칸 없음 → 대체
    # 주요계정 응답에는 차입금·단기금융상품·현금 줄이 없다 — 0 으로 채우지 않는다(검증 보강)
    assert (r.total_debt, r.short_inv, r.cash, r.net_debt) == (None, None, None, None)
    assert r.total_assets == t.total_assets and r.total_equity == t.total_equity
    assert cumulative_from_quarters(stored, 2026, 3, "CFS") is None  # 3Q 저장 전


def test_접수일은_접수번호_앞_8자리() -> None:
    assert str(rcept_date("20261113800001")) == "2026-11-13"
    assert (
        rcept_date("2026") is None
        and rcept_date(None) is None
        and rcept_date("20261340000000") is None
    )


def _is_rows(rows: list[tuple[str, str, int, int]], rcept: str) -> StatementRaw:
    """손익 행만 있는 1분기 보고서(account_id, account_nm, 3개월값, 누적값)."""
    out = [{"rcept_no": rcept, "sj_div": "IS", "account_id": aid, "account_nm": nm,
            "thstrm_amount": str(m3), "thstrm_add_amount": str(cum), "currency": "KRW"}
           for aid, nm, m3, cum in rows]  # fmt: skip
    return StatementRaw("99400010", 2025, "11013", "CFS", rcept, tuple(out))


@pytest.mark.parametrize(("cum", "quality"), [(10_100, Quality.OK), (10_101, Quality.ESTIMATED)])
def test_3개월값_대조_1퍼센트_경계(cum: int, quality: Quality) -> None:
    """정확히 1% 는 ok, 넘으면 estimated(§4.5 — '1% 넘으면') — 검증 보강."""
    raw = _is_rows([("ifrs-full_Revenue", "매출액", 10_000, cum)], "20250515000001")
    (r,) = quarter_rows([report_of_raw(raw)], profile("99400010"), tier="full",
                        corp_code="99400010")  # fmt: skip
    assert r.revenue == cum and r.reported_3m == {"revenue": 10_000}
    assert r.quality == quality


def test_금융업_매출_동의어는_금융업일_때만() -> None:
    """legacy 매출 이름이 없는 보험사('보험수익') — 금융업이면 매출 자리, 아니면 None(검증 보강)."""
    rows = [("-표준계정코드 미사용-", "보험수익", 500, 500),
            ("dart_OperatingIncomeLoss", "영업이익", 50, 50)]  # fmt: skip
    raw = _is_rows(rows, "20250515000001")
    fin_prof = profile("99400100")  # #11 금융업(KSIC 64~66)
    (r,) = quarter_rows([report_of_raw(raw)], fin_prof, tier="full", corp_code="99400010",
                        financial_prefixes=FIN)  # fmt: skip
    assert r.revenue == 500 and any("보험수익" in n for n in r.notes)
    (o,) = quarter_rows([report_of_raw(raw)], profile("99400010"), tier="full",
                        corp_code="99400010", financial_prefixes=FIN)  # fmt: skip
    assert o.revenue is None and o.op == 50

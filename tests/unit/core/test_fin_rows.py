"""P4 행 자료형(kbj/core/fin_rows.py) — frozen, 금액은 원 단위 정수, naive 시각 거부,
기간·형식 검증.

근거: docs/p4_design.md §1.1(M 시험 칸)·§3.4, docs/metrics.md §9~§15. 값은 전부 합성이다.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime

import pytest

from kbj.core import fin_rows as fr
from kbj.core.quality import Quality

NOW = datetime(2026, 11, 16, 1, 0, tzinfo=UTC)
NAIVE = datetime(2026, 11, 16, 10, 0)  # noqa: DTZ001 — 거부되는지 본다
CORP = "99400010"
CODE = "994010"
RC = "20261113000123"


def disclosure(**kw: object) -> fr.DisclosureRow:
    base: dict[str, object] = {
        "rcept_no": RC, "corp_code": CORP, "stock_code": CODE, "corp_name": "합성전자",
        "corp_cls": "Y", "title": "연결재무제표기준영업(잠정)실적(공정공시)",
        "base_title": "연결재무제표기준영업(잠정)실적(공정공시)", "filer": "합성전자",
        "rcept_dt": date(2026, 11, 13), "remark": None, "kind": "prelim", "is_correction": False,
        "first_seen_at": NOW,
    }  # fmt: skip
    base.update(kw)
    return fr.DisclosureRow(**base)  # type: ignore[arg-type]


def quarter(**kw: object) -> fr.QuarterRow:
    base: dict[str, object] = {
        "corp_code": CORP, "fiscal_year": 2026, "fiscal_q": 3, "fs_div": "CFS",
        "period_start": date(2026, 7, 1), "period_end": date(2026, 9, 30),
        "rcept_dt": date(2026, 11, 13), "tier": "full", "revenue": 1_000_000_000_000,
        "op": 120_000_000_000,
    }  # fmt: skip
    base.update(kw)
    return fr.QuarterRow(**base)  # type: ignore[arg-type]


# ── 공통 규칙 ──────────────────────────────────────────────────────────────────────────────


def test_rows_are_frozen() -> None:
    row = disclosure()
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.title = "x"  # type: ignore[misc]
    q = quarter()
    with pytest.raises(dataclasses.FrozenInstanceError):
        q.revenue = 1  # type: ignore[misc]


@pytest.mark.parametrize("bad", [1.5e12, True, "1000"])
def test_money_must_be_won_integers(bad: object) -> None:
    with pytest.raises(TypeError, match="정수"):
        quarter(revenue=bad)


def test_floats_must_be_finite_and_none_stays_none() -> None:
    with pytest.raises(ValueError, match="유한"):
        quarter(eps_basic=float("nan"))
    with pytest.raises(ValueError, match="유한"):
        fr.BandRow(
            CODE, date(2026, 11, 13), "per", 1.0, 2.0, 3.0, 4.0, float("inf"), 1.0, 9.0,
            300, 0, 3.0, 50.0, date(2021, 11, 13),
        )  # fmt: skip
    q = quarter(op=None)
    assert q.op is None  # 없는 값은 None — 0 으로 채우지 않는다


def test_naive_times_and_datetime_in_date_fields_are_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        disclosure(first_seen_at=NAIVE)
    with pytest.raises(TypeError, match="date"):
        disclosure(rcept_dt=NOW)
    with pytest.raises(TypeError, match="date"):
        quarter(rcept_dt="2026-11-13")


@pytest.mark.parametrize(
    ("field", "value"),
    [("rcept_no", "2026111300012"), ("corp_code", "9940001"), ("stock_code", "99401"),
     ("stock_code", "994o10"), ("corp_cls", "X"), ("kind", "Prelim")],
)  # fmt: skip
def test_identifier_formats(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        disclosure(**{field: value})


def test_alphanumeric_stock_code_and_public_source_is_dart_only() -> None:
    assert disclosure(stock_code="99K010").stock_code == "99K010"  # 영숫자 단축코드(§8.4 #18)
    assert disclosure(stock_code=None).stock_code is None
    with pytest.raises(ValueError, match="DART"):
        disclosure(source="KIS")  # 공개 표에 로그인 원천을 쓰지 않는다(ADR 0002)
    with pytest.raises(ValueError, match="DART"):
        quarter(source="naver")
    assert disclosure().link.endswith(RC)


def test_quality_values() -> None:
    assert disclosure(quality="estimated").quality is Quality.ESTIMATED
    with pytest.raises(ValueError, match="quality"):
        disclosure(quality="good")


# ── 회계 기간·보고서 코드 ─────────────────────────────────────────────────────────────────


def test_reprt_and_quarter_helpers() -> None:
    assert fr.quarter_of_reprt("11013") == 1 and fr.quarter_of_reprt("11011") == 4
    assert fr.reprt_of_quarter(2) == "11012" and fr.reprt_of_quarter(3) == "11014"
    with pytest.raises(ValueError):
        fr.quarter_of_reprt("99999")
    with pytest.raises(ValueError):
        fr.reprt_of_quarter(5)


def test_fiscal_period_labels_and_checks() -> None:
    dec = fr.FiscalPeriod(2025, 1, date(2025, 1, 1), date(2025, 3, 31))
    assert dec.calendar_year_end and dec.label == "2025 1Q" and dec.key == "2025Q1"
    mar = fr.FiscalPeriod(2025, 2, date(2025, 7, 1), date(2025, 9, 30))  # 3월 결산 2분기
    assert not mar.calendar_year_end
    assert mar.label == "FY25 2Q (25.07~25.09)"  # §8.4 #7
    jun = fr.FiscalPeriod(2026, 1, date(2025, 7, 1), date(2025, 9, 30))  # 6월 결산
    assert jun.label == "FY26 1Q (25.07~25.09)"
    with pytest.raises(ValueError, match="3개월"):
        fr.FiscalPeriod(2025, 1, date(2025, 1, 1), date(2025, 6, 30))
    with pytest.raises(ValueError, match="1~4"):
        fr.FiscalPeriod(2025, 5, date(2025, 1, 1), date(2025, 3, 31))


def test_quarter_row_pit_and_checks() -> None:
    q = quarter()
    assert q.period == "2026Q3"
    assert not q.known_by(date(2026, 11, 12)) and q.known_by(date(2026, 11, 13))
    assert not quarter(rcept_dt=None).known_by(date(2030, 1, 1))  # 접수일 모름 → PIT 에서 빠진다
    with pytest.raises(ValueError, match="접수일"):
        quarter(rcept_dt=date(2026, 9, 1))
    with pytest.raises(ValueError, match="reported_3m"):
        quarter(reported_3m={"price": 1})
    assert quarter(reported_3m={"revenue": 5}).reported_3m == {"revenue": 5}


# ── 공시 행 ───────────────────────────────────────────────────────────────────────────────


def test_prelim_row_rules() -> None:
    row = fr.PrelimRow(
        rcept_no=RC, corp_code=CORP, rcept_dt=date(2026, 10, 8), scope=fr.PrelimScope.CONSOLIDATED,
        fiscal_year=2026, fiscal_q=3, is_annual=False, period_end=date(2026, 9, 30),
        period_inferred=True, unit_mult=100_000_000, revenue_cur=7_900_000_000_000,
        op_cur=-1_200_000_000, op_prev_y=500_000_000, eps_cur=-12.5,
        yoy_reported={"revenue": 3.1, "op": None}, quality=Quality.ESTIMATED,
    )  # fmt: skip
    assert row.period == "2026Q3" and row.scope is fr.PrelimScope.CONSOLIDATED
    with pytest.raises(ValueError, match="미래"):
        dataclasses.replace(row, period_end=date(2026, 12, 31))
    with pytest.raises(ValueError, match="unit_mult"):
        dataclasses.replace(row, unit_mult=10)
    with pytest.raises(ValueError, match="연간"):
        dataclasses.replace(row, is_annual=True)
    annual = dataclasses.replace(row, fiscal_q=4, is_annual=True, period_end=date(2025, 12, 31))
    assert annual.period == "2026FY"
    with pytest.raises(ValueError, match="yoy_reported"):
        dataclasses.replace(row, yoy_reported={"price": 1.0})


def test_schedule_statutory_is_an_estimate() -> None:
    args = (CORP, date(2026, 9, 30), "statutory", date(2026, 11, 16), 2026, 3)
    with pytest.raises(ValueError, match="estimated"):
        fr.ScheduleRow(*args)  # type: ignore[arg-type]
    assert fr.ScheduleRow(*args, quality=Quality.ESTIMATED).kind == "statutory"  # type: ignore[arg-type]


def test_overhang_rows() -> None:
    ev = fr.OverhangEvent(
        RC, CORP, date(2026, 3, 2), fr.Instrument.CB, 30_000_000_000, 10_000.0, 3_000_000,
        7_000.0, date(2027, 3, 2), date(2029, 2, 2), date(2029, 3, 2), None, "공모", "api",
    )  # fmt: skip
    assert ev.instrument is fr.Instrument.CB
    with pytest.raises(ValueError, match="청구기간"):
        dataclasses.replace(ev, exercise_start=date(2030, 1, 1))
    with pytest.raises(ValueError, match="0 보다"):
        dataclasses.replace(ev, price=0.0)
    with pytest.raises(ValueError, match="invalid"):
        fr.OverhangAdjust(RC, CORP, date(2026, 6, 1), None, "exercise", None, 1, 1, None)
    bad = fr.OverhangAdjust(
        RC, CORP, date(2026, 6, 1), None, "exercise", None, 1, 1, None,
        note="대상 결정 공시를 찾지 못했다", quality=Quality.INVALID,
    )  # fmt: skip
    assert bad.target_rcept_no is None
    with pytest.raises(ValueError, match="활성"):
        fr.OverhangState(CORP, date(2026, 11, 16), RC, "cb", 1, 1.0, 1, False, True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="분모"):
        fr.OverhangSummary(CORP, date(2026, 11, 16), 1, 0, 0, None, None, 1.0, None)
    with pytest.raises(ValueError, match="0 으로"):
        fr.OverhangSummary(CORP, date(2026, 11, 16), 1, 0, 0, 0, "dart_share_count", None, None)


def test_insider_window_sum_rule_and_share_count() -> None:
    w = fr.InsiderWindow(CORP, date(2026, 11, 16), 90, -500, 100, 600, 3, 2, -0.5, 50.0)
    assert w.net_shares == w.buy_shares - w.sell_shares
    with pytest.raises(ValueError, match="net_shares"):
        dataclasses.replace(w, net_shares=1)
    with pytest.raises(ValueError, match="days"):
        dataclasses.replace(w, days=60)
    sc = fr.ShareCount(CORP, date(2026, 9, 30), RC, 10_000_000, 500_000, 300_000, 0)
    assert sc.common_outstanding == 9_700_000
    assert dataclasses.replace(sc, treasury_common=None).common_outstanding is None


def test_statement_raw_copies_rows() -> None:
    src = [{"account_id": "ifrs-full_Revenue", "thstrm_amount": "1"}]
    raw = fr.StatementRaw(CORP, 2026, "11014", "CFS", RC, tuple(src))
    src[0]["thstrm_amount"] = "2"
    assert raw.rows[0]["thstrm_amount"] == "1" and raw.n_rows == 1
    with pytest.raises(ValueError, match="reprt_code"):
        fr.StatementRaw(CORP, 2026, "11015", "CFS", RC, ())


def test_ratio_na_reasons_must_match_empty_values() -> None:
    r = fr.RatioRow(CORP, 2026, 3, "CFS", roe=None, na_reasons={"roe": "capital_impairment"})
    assert r.na_reasons == {"roe": "capital_impairment"} and r.period == "2026Q3"
    with pytest.raises(ValueError, match="값도"):
        fr.RatioRow(CORP, 2026, 3, "CFS", roe=1.0, na_reasons={"roe": "loss"})
    with pytest.raises(ValueError):
        fr.RatioRow(CORP, 2026, 3, "CFS", na_reasons={"roe": "unknown_reason"})


def test_report_row_size_cap_and_digest() -> None:
    kw: dict[str, object] = {
        "stock_code": CODE, "built_on": date(2026, 11, 16), "corp_code": CORP, "year_from": 2023,
        "year_to": 2026, "fs_div_used": "CFS", "payload": {"blocks": {}}, "xlsx": b"PK\x03\x04",
        "warnings": ("주석 표 없음",), "engine_version": "dr-1", "input_digest": "abc",
        "generated_at": NOW,
    }  # fmt: skip
    row = fr.ReportRow(**kw)  # type: ignore[arg-type]
    assert row.xlsx_sha256 is not None and len(row.xlsx_sha256) == 64
    with pytest.raises(ValueError, match="2097152"):
        fr.ReportRow(**{**kw, "xlsx": b"x" * (fr.XLSX_MAX_BYTES + 1)})  # type: ignore[arg-type]


# ── 로그인 행 ─────────────────────────────────────────────────────────────────────────────


def test_consensus_rows() -> None:
    c = fr.ConsensusRow(CODE, date(2026, 11, 16), "202612", True, 1, 2, 3, 3, 1500.0)
    assert c.fiscal_end == date(2026, 12, 31) and c.source == "KIS"
    assert dataclasses.replace(c, fiscal_ym="202603").fiscal_end == date(2026, 3, 31)
    with pytest.raises(ValueError, match="YYYYMM"):
        dataclasses.replace(c, fiscal_ym="2026-12")
    with pytest.raises(ValueError, match="estimated"):
        fr.TargetConsensus(CODE, date(2026, 11, 16), 10.0, 10.0, 11.0, 9.0, 2, 90)
    t = fr.TargetConsensus(
        CODE, date(2026, 11, 16), 10.0, 10.0, 11.0, 9.0, 2, 90, quality=Quality.ESTIMATED
    )
    assert t.n_brokers == 2
    with pytest.raises(ValueError, match="TURN"):
        fr.RevisionRow(CODE, date(2026, 11, 16), "op", "202612", 7, 1.0, -1.0, None, 200.0, "TURN")
    with pytest.raises(ValueError, match="000000"):
        fr.RevisionRow(CODE, date(2026, 11, 16), "target_mean", "202612", 7, 1, 1, None, 0.0, None)


def test_valuation_rows() -> None:
    v = fr.ValuationDay(
        CODE, date(2026, 11, 13), 10**13, 50_000.0, "2026Q3:CFS", per=None,
        flags={"na": {"per": "loss"}, "out_of_cap": []},
    )  # fmt: skip
    assert v.per is None
    with pytest.raises(ValueError):
        dataclasses.replace(v, flags={"na": {"per": "nope"}})
    no_band = fr.BandRow(
        CODE, date(2026, 11, 13), "per", None, None, None, None, None, None, None, 20, 0, None,
        None, None, na_reason=fr.NaReason.SHORT_HISTORY,
    )  # fmt: skip
    assert no_band.na_reason is fr.NaReason.SHORT_HISTORY
    with pytest.raises(ValueError, match="na_reason"):
        dataclasses.replace(no_band, na_reason=None)
    with pytest.raises(ValueError, match="p10"):
        fr.BandRow(
            CODE, date(2026, 11, 13), "per", 5.0, 4.0, 6.0, 7.0, 8.0, 1.0, 9.0, 300, 0, 6.0,
            50.0, None,
        )  # fmt: skip
    with pytest.raises(ValueError, match="근거"):
        fr.ScenarioRow(
            CODE, date(2026, 11, 13), "base", "PER", 100.0, None, 10.0, None, 1000.0, 1.0
        )
    insufficient = fr.ScenarioRow(
        CODE, date(2026, 11, 13), "base", "INSUFFICIENT", None, None, None, None, None, None,
        na_reason=fr.NaReason.SHORT_HISTORY,
    )  # fmt: skip
    assert insufficient.tp is None


def test_surprise_watch_and_share_class() -> None:
    s = fr.SurpriseRow(RC, CODE, CORP, "2026Q3", "consolidated", "YOY_BIG_UP", 1, op_yoy=25.0)  # type: ignore[arg-type]
    assert s.scope is fr.PrelimScope.CONSOLIDATED
    with pytest.raises(ValueError, match="period"):
        dataclasses.replace(s, period="2026-3")
    w = fr.WatchItem("main", CODE, "합성전자", NOW, 0, source="import:sd")
    assert w.sort == 0
    with pytest.raises(ValueError, match="smallint"):
        dataclasses.replace(w, sort=40_000)
    with pytest.raises(ValueError):
        dataclasses.replace(w, source="naver")
    with pytest.raises(ValueError, match="naive"):
        dataclasses.replace(w, added_at=NAIVE)
    assert fr.ShareClass("994015", None, "pref", "994010").common_code == "994010"
    with pytest.raises(ValueError, match="다른"):
        fr.ShareClass("994015", None, "pref", "994015")
    with pytest.raises(ValueError, match="자기"):
        fr.ShareClass("994010", CORP, "common", "994015")


def test_na_reason_values_cover_the_api_list() -> None:
    """설계 §5.2 의 na_reason 10개 + 이 묶음이 더한 2개(currency_mismatch·missing_input)."""
    assert {r.value for r in fr.NaReason} == {
        "loss", "capital_impairment", "no_revenue", "financial_sector", "short_history",
        "not_detail_tier", "not_in_consensus_universe", "no_filings", "spac",
        "konex_no_consensus", "currency_mismatch", "missing_input",
    }  # fmt: skip
    assert [i.value for i in fr.Instrument] == ["cb", "bw", "eb", "paid_in", "bonus"]
    fin_prefixes = ("64", "65", "66")  # config/fin.yaml valuation.financial_ksic_prefixes
    assert fr.CorpProfile(CORP, CODE, 3, "641", "Y", None).is_financial(fin_prefixes) is True
    assert fr.CorpProfile(CORP, CODE, 3, "26429", "Y", None).is_financial(fin_prefixes) is False
    assert fr.CorpProfile(CORP, CODE, 3, "641", "Y", None).is_financial(("65",)) is False
    assert fr.CorpProfile(CORP, CODE, 12, None, "Y", None).is_financial(fin_prefixes) is None
    with pytest.raises(ValueError, match="acc_mt"):
        fr.CorpProfile(CORP, CODE, 13, None, "Y", None)

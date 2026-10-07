"""KRX 주식·ETF·ETN·지수·종목기본정보 행 모델(D7 — 거래대금·NAV·상장좌수·순자산). 합성 fixture.

필드 이름은 KRX OpenAPI 카탈로그 기준 [실측 필요] — 이 시험은 '그 이름이면 이렇게 읽는다'를
고정한다.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.data.private.krx.models import (
    KrxEtfDaily,
    KrxRowsError,
    KrxStockDaily,
    krx_number,
    parse_base_info_rows,
    parse_etf_daily_rows,
    parse_etn_daily_rows,
    parse_index_daily_rows,
    parse_stock_daily_rows,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "krx"
D29, D30 = date(2026, 9, 29), date(2026, 9, 30)


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def stock_rows(market: str = "kospi", day: str = "20260930") -> list[dict[str, Any]]:
    return fixture("stock_daily.json")[market][day]


def etf_rows(day: str) -> list[dict[str, Any]]:
    return fixture("etf_daily.json")[day]


# ── 주식 ──


@pytest.mark.parametrize("market", ["kospi", "kosdaq"])
@pytest.mark.parametrize("day", ["20260929", "20260930"])
def test_every_stock_row_parses_with_source_and_as_of(market: str, day: str) -> None:
    rows = stock_rows(market, day)
    ep = "/sto/stk_bydd_trd" if market == "kospi" else "/sto/ksq_bydd_trd"
    ok, bad = parse_stock_daily_rows(rows, endpoint=ep)
    assert bad == [] and len(ok) == len(rows)
    for r in ok:
        assert r.source == f"KRX:{ep.lstrip('/')}"
        assert r.as_of == date(int(day[:4]), int(day[4:6]), int(day[6:]))
        assert r.quality is Quality.OK
        assert isinstance(r.acc_trdval, int) and isinstance(r.mktcap, int)


def test_stock_amounts_are_integer_won_and_prices_decimal() -> None:
    raw = stock_rows()[0]
    (r,), _ = parse_stock_daily_rows([raw], endpoint="/sto/stk_bydd_trd")
    assert r.acc_trdval == int(raw["ACC_TRDVAL"])  # 원 — 억원으로 접지 않는다(metrics §0)
    assert r.mktcap == int(raw["MKTCAP"]) == int(raw["TDD_CLSPRC"]) * int(raw["LIST_SHRS"])
    assert r.tdd_clsprc == Decimal(raw["TDD_CLSPRC"])
    assert r.fluc_rt == Decimal(raw["FLUC_RT"])


def test_second_day_change_is_against_the_first_day_close() -> None:
    first = {r["ISU_CD"]: r for r in stock_rows(day="20260929")}
    ok, _ = parse_stock_daily_rows(stock_rows(day="20260930"), endpoint="/sto/stk_bydd_trd")
    for r in ok:
        prev = Decimal(first[r.isu_cd]["TDD_CLSPRC"])
        assert r.tdd_clsprc is not None and r.cmpprevdd_prc == r.tdd_clsprc - prev


def test_suspended_like_row_keeps_zero_volume_not_none() -> None:
    raw = next(r for r in stock_rows() if r["ACC_TRDVOL"] == "0")
    (r,), _ = parse_stock_daily_rows([raw], endpoint="/sto/stk_bydd_trd")
    assert r.acc_trdvol == 0 and r.acc_trdval == 0  # 0 은 '거래 없음' — None('못 받음')과 다르다
    assert r.quality is Quality.OK


@pytest.mark.parametrize(
    ("raw", "want"),
    [("", None), (" - ", None), ("-", None), ("1,234", "1234"), (" 7 ", "7"), ("무액면", None)],
)
def test_krx_number_strings(raw: str, want: str | None) -> None:
    assert krx_number(raw) == want


def test_blank_and_dash_values_are_none_and_close_missing_is_invalid() -> None:
    raw = dict(stock_rows()[0]) | {"TDD_CLSPRC": "", "ACC_TRDVAL": "-", "MKTCAP": "1,000"}
    (r,), _ = parse_stock_daily_rows([raw], endpoint="/sto/stk_bydd_trd")
    assert r.tdd_clsprc is None and r.acc_trdval is None and r.mktcap == 1000
    assert r.quality is Quality.INVALID


@pytest.mark.parametrize(
    ("field", "value"),
    [("ACC_TRDVAL", "12.5"), ("BAS_DD", "2026-09-30"), ("TDD_CLSPRC", "abc"), ("ISU_CD", "")],
)
def test_bad_stock_rows_are_isolated(field: str, value: str) -> None:
    rows = stock_rows()[:3]
    rows[1] = dict(rows[1]) | {field: value}
    ok, bad = parse_stock_daily_rows(rows, endpoint="/sto/stk_bydd_trd")
    assert len(ok) == 2 and [e.index for e in bad] == [1]
    assert "abc" not in bad[0].error  # 입력값은 오류 문구에 싣지 않는다


def test_renamed_fields_are_not_read_as_an_empty_day() -> None:
    """필드 이름이 바뀌어 한 행도 안 맞으면 실패 — 휴장일(빈 목록)과 구분한다."""
    rows = [{k.lower(): v for k, v in r.items()} for r in stock_rows()]
    with pytest.raises(KrxRowsError, match="모두 형식 오류"):
        parse_stock_daily_rows(rows, endpoint="/sto/stk_bydd_trd")
    assert parse_stock_daily_rows([], endpoint="/sto/stk_bydd_trd") == ([], [])


def test_alternate_field_names_from_et_are_accepted() -> None:
    """ET FIELD 후보(ISU_SRT_CD·ISU_ABBRV)로 와도 읽는다."""
    raw = dict(stock_rows()[0])
    raw["ISU_SRT_CD"] = raw.pop("ISU_CD")
    raw["ISU_ABBRV"] = raw.pop("ISU_NM")
    r = KrxStockDaily.model_validate(raw)
    assert r.isu_cd == raw["ISU_SRT_CD"] and r.isu_nm == raw["ISU_ABBRV"]


# ── ETF (D7 — 순유입 원장의 입력) ──


@pytest.mark.parametrize("day", ["20260929", "20260930"])
def test_every_etf_row_parses_with_nav_shares_and_net_assets(day: str) -> None:
    rows = etf_rows(day)
    ok, bad = parse_etf_daily_rows(rows)
    assert bad == [] and len(ok) == len(rows)
    for r in ok:
        assert r.source == "KRX:etp/etf_bydd_trd" and r.quality is Quality.OK
        assert r.nav is not None and r.list_shrs is not None and r.net_assets is not None
        assert isinstance(r.acc_trdval, int)
        # 순자산 ≈ 상장좌수 × NAV(원 반올림) — 허용오차 1원
        assert abs(Decimal(r.net_assets) - r.list_shrs * r.nav) <= 1


def test_etf_flow_inputs_follow_metrics_formula() -> None:
    """순유입 = (좌수ₜ − 좌수ₜ₋₁) × NAVₜ, 가격효과 = 좌수ₜ₋₁ × (NAVₜ − NAVₜ₋₁) — 검산 ③ 이 맞는
    입력이다.

    (계산 자체는 P5 엔진 몫. 여기서는 모델이 그 계산에 필요한 값을 손실 없이 주는지 본다.)
    """
    a = {r.isu_cd: r for r in parse_etf_daily_rows(etf_rows("20260929"))[0]}
    b = {r.isu_cd: r for r in parse_etf_daily_rows(etf_rows("20260930"))[0]}
    both = sorted(set(a) & set(b))
    assert both and set(b) - set(a)  # 둘째 날 신규 상장(전날 행 없음)이 있다
    for code in both:
        x, y = a[code], b[code]
        assert x.list_shrs and y.list_shrs and x.nav and y.nav and x.net_assets and y.net_assets
        inflow = (y.list_shrs - x.list_shrs) * y.nav
        price = x.list_shrs * (y.nav - x.nav)
        assert abs((y.net_assets - x.net_assets) - (inflow + price)) <= 2  # 반올림 오차만


def test_etf_missing_nav_is_invalid() -> None:
    raw = dict(etf_rows("20260930")[0]) | {"NAV": ""}
    assert KrxEtfDaily.model_validate(raw).quality is Quality.INVALID


def test_etn_index_and_base_info_rows_parse() -> None:
    etn, bad = parse_etn_daily_rows(fixture("etn_daily.json")["20260930"])
    assert bad == [] and all(r.per1secu_indic_val is not None for r in etn)
    idx, bad = parse_index_daily_rows(
        fixture("index_daily.json")["kospi"]["20260930"], endpoint="/idx/kospi_dd_trd"
    )
    assert bad == [] and {r.idx_nm for r in idx} >= {"코스피", "코스피 200"}
    assert all(r.source == "KRX:idx/kospi_dd_trd" and r.clsprc_idx for r in idx)
    info, bad = parse_base_info_rows(
        fixture("base_info.json")["kospi"], endpoint="/sto/stk_isu_base_info", bas_dd=D30
    )
    assert bad == [] and all(r.as_of == D30 for r in info)  # 응답에 기준일이 없으면 요청일
    assert {r.kind_stkcert_tp_nm for r in info} == {"보통주", "우선주"}
    assert any(r.parval is None for r in info)  # 무액면
    assert all(r.isu_cd.startswith("KR7") and len(r.isu_cd) == 12 for r in info)


def test_index_accepts_et_style_price_names() -> None:
    raw = dict(fixture("index_daily.json")["kosdaq"]["20260930"][0])
    raw["TDD_CLSPRC"] = raw.pop("CLSPRC_IDX")
    (r,), _ = parse_index_daily_rows([raw], endpoint="/idx/kosdaq_dd_trd")
    assert r.clsprc_idx == Decimal(raw["TDD_CLSPRC"])


def test_base_info_response_date_wins_over_the_requested_one() -> None:
    raw = dict(fixture("base_info.json")["kosdaq"][0]) | {"BAS_DD": "20260929"}
    (r,), _ = parse_base_info_rows([raw], endpoint="/sto/ksq_isu_base_info", bas_dd=D30)
    assert r.as_of == D29

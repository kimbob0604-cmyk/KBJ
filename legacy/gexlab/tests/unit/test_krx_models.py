"""KRX 파생상품 일별 모델 — KRX Open API 응답과 같은 형태의 합성 fixture(tests/fixtures/krx —
`scripts/make_synthetic_fixtures.py`)로 고정.

원래 GEXLAB 은 로컬에서 2026-09-23 옵션 전체 16,724행·2010-01-04 236행을 모두 파싱해 봤다(오류 0,
상품군이 PROD_NM 과 전부 일치). 커밋한 fixture 는 이름 형식마다 한두 행이다(이름·형식은 원본과 같고
코드·값은 합성).
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from data.krx.models import (
    KrxFuturesDaily,
    KrxOptionDaily,
    parse_futures_rows,
    parse_option_name,
    parse_option_rows,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "krx"

PROD_FAMILY = {
    "코스피200 옵션": "kospi200",
    "미니코스피200 옵션": "mini_kospi200",
    "코스피200 위클리(목) 옵션": "kospi200_weekly_thu",
    "코스피200 위클리(월) 옵션": "kospi200_weekly_mon",
    "코스닥150 옵션": "kosdaq150",
    "코스닥150 위클리(목) 옵션": "kosdaq150_weekly_thu",
    "코스닥150 위클리(월) 옵션": "kosdaq150_weekly_mon",
    "미국달러 옵션": "usd",
}


def opt_rows(day: str) -> list[dict[str, Any]]:
    return json.loads((FIX / "opt_daily.json").read_text(encoding="utf-8"))[day]


def opt(name: str, day: str = "20260923", nth: int = 0) -> KrxOptionDaily:
    rows = [r for r in opt_rows(day) if r["ISU_NM"] == name]
    return KrxOptionDaily.model_validate(rows[nth])


def fut_rows() -> list[dict[str, Any]]:
    return json.loads((FIX / "fut_daily.json").read_text(encoding="utf-8"))["rows"]


# ── 옵션 ──


@pytest.mark.parametrize("day", ["20260923", "20100104"])
def test_every_fixture_option_row_parses_and_family_matches_prod_nm(day: str) -> None:
    rows = opt_rows(day)
    ok, bad = parse_option_rows(rows)
    assert bad == [] and len(ok) == len(rows)
    for r in ok:
        assert r.family == PROD_FAMILY[r.prod_nm]
        assert r.cp == r.rght_tp_nm[0]
        assert r.bas_dd == date(int(day[:4]), int(day[4:6]), int(day[6:]))


def test_kospi200_monthly_day_row() -> None:
    r = opt("코스피200 C 202610 1,100.0 (정규)")
    assert (r.family, r.cp, r.expiry_token, r.expiry) == ("kospi200", "C", "202610", "202610")
    assert r.strike == Decimal("1100.0")
    assert r.session == "day"
    assert r.imp_volt == Decimal("35.20")
    assert r.tdd_clsprc == Decimal("44.70") and r.nxtdd_bas_prc == Decimal("44.70")
    assert (r.acc_trdvol, r.acc_opnint_qty) == (72, 2613)
    assert r.traded
    assert r.isu_cd == "B016AZC8"


def test_night_row_iv_placeholder_is_none() -> None:
    raw = next(r for r in opt_rows("20260923") if r["ISU_NM"].endswith("1,100.0 (야간)"))
    assert raw["IMP_VOLT"] == "0.00"
    r = opt("코스피200 C 202610 1,100.0 (야간)")
    assert r.session == "night"
    assert r.imp_volt is None
    assert r.traded and r.acc_trdvol == 1
    assert r.tdd_clsprc == Decimal("49.35")
    # 같은 종목의 정규 행과 ISU_CD 가 같다 — 키는 (bas_dd, isu_cd, session)
    assert r.isu_cd == opt("코스피200 C 202610 1,100.0 (정규)").isu_cd


def test_untraded_padded_strike_row() -> None:
    r = opt("코스피200 C 202610   745.0 (정규)")
    assert r.strike == Decimal("745.0")
    assert not r.traded
    assert r.tdd_clsprc is None and r.tdd_opnprc is None and r.cmpprevdd_prc is None
    assert r.imp_volt == Decimal("31.50")  # 거래 없어도 정규 행엔 IV 가 붙는다


# 이름 → "상품군 콜풋 만기표기 6자리만기 행사가 세션" (세션 없음은 -)
NAME_CASES = [
    ("미니코스피 C 202610   752.5 (야간)", "mini_kospi200 C 202610 202610 752.5 night"),
    ("미니코스피 P 202610 1,100.0 (정규)", "mini_kospi200 P 202610 202610 1100.0 day"),
    ("코스피위클리 C 2609W4 1,100.0 (정규)", "kospi200_weekly_thu C 2609W4 260904 1100.0 day"),
    ("코스피위클리 P 2610W1 1,100.0 (야간)", "kospi200_weekly_thu P 2610W1 261001 1100.0 night"),
    ("코스피위클리M C 2609W4 1,100.0 (정규)", "kospi200_weekly_mon C 2609W4 260904 1100.0 day"),
    ("코스피위클리M P 2609W4   970.0 (야간)", "kospi200_weekly_mon P 2609W4 260904 970.0 night"),
    ("코스닥150 C 202610 1,000 (정규)", "kosdaq150 C 202610 202610 1000 day"),
    ("코스닥150 P 202610   875 (야간)", "kosdaq150 P 202610 202610 875 night"),
    ("코스닥위클리 C 2609W4 1,175", "kosdaq150_weekly_thu C 2609W4 260904 1175 -"),
    ("코스닥위클리M C 2609W4 1,200", "kosdaq150_weekly_mon C 2609W4 260904 1200 -"),
    ("코스피200 C 201001 185.0 (정규)", "kospi200 C 201001 201001 185.0 day"),
    ("미국달러 C 201001 1,120.0", "usd C 201001 201001 1120.0 -"),
]


@pytest.mark.parametrize(("name", "want"), NAME_CASES)
def test_parse_option_name_patterns(name: str, want: str) -> None:
    family, cp, token, expiry, strike, session = want.split()
    p = parse_option_name(name)
    assert (p.family, p.cp, p.expiry_token, p.expiry, p.strike) == (
        family,
        cp,
        token,
        expiry,
        Decimal(strike),
    )
    assert p.session == (None if session == "-" else session)


def test_weekly_expiry_normalised_to_kis_code() -> None:
    assert opt("코스피위클리 C 2609W4 1,100.0 (정규)").expiry == "260904"
    assert opt("코스피위클리 P 2610W1 1,100.0 (야간)").expiry == "261001"
    assert opt("코스피위클리M C 2609W4 1,100.0 (정규)").expiry == "260904"


def test_kosdaq_weekly_duplicate_rows_without_session() -> None:
    # 끝 표기 없이 같은 ISU_CD 가 두 행 — 하나는 IMP_VOLT 0.00 (값 없음)
    a = opt("코스닥위클리 C 2609W4 1,175", nth=0)
    b = opt("코스닥위클리 C 2609W4 1,175", nth=1)
    assert a.isu_cd == b.isu_cd
    assert a.session is None and b.session is None
    assert {a.imp_volt, b.imp_volt} == {None, Decimal("23.90")}


def test_2010_rows() -> None:
    r = opt("코스피200 P 201003 240.0 (정규)", day="20100104")
    assert (r.family, r.expiry, r.strike, r.session) == (
        "kospi200",
        "201003",
        Decimal("240.0"),
        "day",
    )
    assert r.imp_volt == Decimal("20.80") and r.acc_trdvol == 151
    usd = opt("미국달러 C 201001 1,120.0", day="20100104")
    assert (usd.family, usd.session) == ("usd", None)


def test_zero_iv_on_any_row_is_none() -> None:
    raw = dict(opt_rows("20260923")[0]) | {"IMP_VOLT": "0.00"}
    assert raw["ISU_NM"].endswith("(정규)")
    assert KrxOptionDaily.model_validate(raw).imp_volt is None


def test_unknown_prefix_is_other() -> None:
    raw = dict(opt_rows("20260923")[0]) | {"ISU_NM": "새지수 C 202610 1,100.0 (정규)"}
    assert KrxOptionDaily.model_validate(raw).family == "other"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("RGHT_TP_NM", "PUT"),  # 이름은 C
        ("ISU_NM", "코스피200 C 202610 1,100.0 (기타)"),  # 모르는 세션
        ("ISU_NM", "코스피200 X 202610 1,100.0 (정규)"),
        ("ISU_NM", "코스피200 C 2026 1,100.0 (정규)"),
        ("ISU_NM", "코스피200 C 202610"),
        ("BAS_DD", "2026-09-23"),
        ("ACC_TRDVOL", "1.5"),
    ],
)
def test_bad_option_rows_are_rejected(field: str, value: str) -> None:
    raw = dict(opt_rows("20260923")[0]) | {field: value}
    with pytest.raises(ValidationError):
        KrxOptionDaily.model_validate(raw)


def test_parse_option_rows_isolates_bad_rows() -> None:
    rows = opt_rows("20260923")[:3]
    rows[1] = dict(rows[1]) | {"ISU_NM": "이상한 이름"}
    ok, bad = parse_option_rows(rows)
    assert len(ok) == 2
    assert [e.index for e in bad] == [1]


# ── 선물 ──


def test_futures_rows_session_from_mkt_nm() -> None:
    ok, bad = parse_futures_rows(fut_rows())
    assert bad == []
    assert [(r.isu_nm, r.mkt_nm, r.session) for r in ok] == [
        ("미니코스피 F 202610 (야간)", "야간", "night"),
        ("미니코스피 F 202610 (주간)", "정규", "day"),
        ("미니코스피 F 202611 (야간)", "야간", "night"),
    ]
    assert [(r.family, r.expiry) for r in ok] == [
        ("mini_kospi200", "202610"),
        ("mini_kospi200", "202610"),
        ("mini_kospi200", "202611"),
    ]
    night, day = ok[0], ok[1]
    assert night.setl_prc is None and day.setl_prc == Decimal("1120.35")
    assert night.spot_prc == Decimal("1124.85")
    assert (day.acc_trdvol, day.acc_trdval, day.acc_opnint_qty) == (119476, 6688489500000, 63233)
    assert day.traded and day.bas_dd == date(2026, 9, 23)


def test_futures_session_falls_back_to_name_suffix() -> None:
    raw = {k: v for k, v in fut_rows()[1].items() if k != "MKT_NM"}
    assert KrxFuturesDaily.model_validate(raw).session == "day"
    raw["ISU_NM"] = "미니코스피 F 202610"
    assert KrxFuturesDaily.model_validate(raw).session is None


@pytest.mark.parametrize(
    ("mkt", "name"),
    [("야간", "미니코스피 F 202610 (주간)"), ("장외", "미니코스피 F 202610 (주간)")],
)
def test_futures_session_conflicts_are_rejected(mkt: str, name: str) -> None:
    raw = dict(fut_rows()[1]) | {"MKT_NM": mkt, "ISU_NM": name}
    with pytest.raises(ValidationError):
        KrxFuturesDaily.model_validate(raw)


@pytest.mark.parametrize(
    ("name", "family", "expiry"),
    [
        ("코스피200 F 202612 (주간)", "kospi200", "202612"),
        ("코스닥150 F 202612 (주간)", "kosdaq150", "202612"),
        ("금 F 202612 (주간)", "other", "202612"),
        ("코스피200 SP 202612-202703 (주간)", "other", None),
    ],
)
def test_futures_name_family(name: str, family: str, expiry: str | None) -> None:
    raw = dict(fut_rows()[1]) | {"ISU_NM": name}
    r = KrxFuturesDaily.model_validate(raw)
    assert (r.family, r.expiry, r.session) == (family, expiry, "day")

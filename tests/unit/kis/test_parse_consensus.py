"""KIS 컨센서스 파서(묶음 K) — 합성 fixture(p4_consensus.json)·단위·빈 응답·의견 중복·결산년월.

실측 아님 — 응답 모양은 `kbj.data.private.kis.consensus` 상수 표([확인 필요 #29])와 가짜 KIS
서버가 같이 따른다. 합성 20종목 세계(p4_world)의 의미값과 파서 결과가 같은지는
tests/unit/fakes/test_kis_server_p4.py.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from kbj.config.fin import load_fin
from kbj.core.quality import Quality
from kbj.data.private.kis.consensus import (
    AMOUNT_UNIT_KRW,
    COLUMNS,
    IND_ROWS,
    OUT_HEAD,
    OUT_IND,
    OUT_PERIODS,
    OUT_PL,
    PL_ROWS,
    TR_ESTIMATE,
    TR_OPINION,
    params_estimate,
    params_opinion,
    parse_estimate,
    parse_fiscal_ym,
    parse_opinion,
)
from kbj.data.private.kis.datasets import CONSENSUS_TRS
from kbj.data.private.kis.parse import KisEmpty, KisParseError, KisShapeError

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "kis"
DAY = date(2026, 11, 16)


def load() -> dict[str, Any]:
    return json.loads((FIX / "p4_consensus.json").read_text(encoding="utf-8"))


def case(name: str) -> dict[str, Any]:
    return load()[name]


def test_fixture_is_synthetic_and_matches_the_generator() -> None:
    assert load()["_source"].startswith("SYNTHETIC")
    spec = importlib.util.spec_from_file_location("_make_p4", FIX / "make_p4.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (FIX / "p4_consensus.json").read_text(encoding="utf-8") == mod.render()
    assert mod.WINDOW == load_fin().consensus.opinion_window_days


def test_tr_specs_come_from_the_single_constant() -> None:
    assert (TR_ESTIMATE.tr_id, TR_ESTIMATE.path) == CONSENSUS_TRS["consensus_estimate"]
    assert (TR_OPINION.tr_id, TR_OPINION.path) == CONSENSUS_TRS["invest_opinion"]
    for name, got in load().items():
        if name != "_source":
            assert got["tr_id"] in (TR_ESTIMATE.tr_id, TR_OPINION.tr_id), name


def test_params() -> None:
    assert params_estimate("994010") == {"SHT_CD": "994010"}
    p = params_opinion("994010", date(2026, 8, 18), DAY)
    assert p["FID_INPUT_ISCD"] == "994010"
    assert (p["FID_INPUT_DATE_1"], p["FID_INPUT_DATE_2"]) == ("20260818", "20261116")
    with pytest.raises(ValueError, match="거꾸로"):
        params_opinion("994010", DAY, date(2026, 8, 18))


# ── 추정실적 ───────────────────────────────────────────────────────────────────────────


def test_estimate_full_converts_units_and_flags() -> None:
    c = case("estimate_full")
    rows = parse_estimate(c["body"], "994010", snap_date=DAY)
    assert [(r.fiscal_ym, r.is_estimate) for r in rows] == [
        ("202412", False),
        ("202512", False),
        ("202612", True),
        ("202712", True),
        ("202812", True),
    ]
    pl = c["body"][OUT_PL]
    ind = c["body"][OUT_IND]
    rev_row, op_row, ni_row = (
        pl[PL_ROWS.index("revenue")],
        pl[PL_ROWS.index("op")],
        pl[PL_ROWS.index("ni")],
    )
    for col, r in zip(COLUMNS, rows, strict=True):
        assert r.revenue == int(rev_row[col]) * AMOUNT_UNIT_KRW  # 억원 → 원
        assert r.op == int(op_row[col]) * AMOUNT_UNIT_KRW
        assert r.ni == int(ni_row[col]) * AMOUNT_UNIT_KRW
        assert r.ebitda == int(ind[IND_ROWS.index("ebitda")][col]) * AMOUNT_UNIT_KRW
        assert r.eps == float(ind[IND_ROWS.index("eps")][col])  # 원/주 그대로
        assert r.ni_owner is None  # 순이익 기준 미확인 — 지어내지 않는다
        assert r.quality is Quality.OK and r.source == "KIS" and r.snap_date == DAY
        assert r.extra["period"] == "annual" and r.extra["estdate"] == "2026-11-16"
        assert "is_estimate_inferred" not in r.extra
    # 증감율 칸은 extra(계산에 쓰지 않는다), 첫 열은 빈 값이라 없다
    assert "revenue_growth_pct" not in rows[0].extra
    assert rows[1].extra["revenue_growth_pct"] == pytest.approx(float(pl[1]["data2"]))


def test_estimate_march_fiscal_and_thin() -> None:
    m = parse_estimate(case("estimate_march")["body"], "994060", snap_date=DAY)
    assert {r.fiscal_ym[4:] for r in m} == {"03"}
    assert next(r.fiscal_ym for r in m if r.is_estimate) == "202703"
    t = parse_estimate(case("estimate_thin")["body"], "994020", snap_date=DAY)
    assert [(r.fiscal_ym, r.is_estimate) for r in t] == [
        ("202512", False),
        ("202612", True),
        ("202712", True),
    ]


def test_estimate_negative_values() -> None:
    rows = parse_estimate(case("estimate_loss_sep")["body"], "994070", snap_date=date(2026, 9, 1))
    fy1 = next(r for r in rows if r.fiscal_ym == "202612")
    assert fy1.ni is not None and fy1.ni < 0 and fy1.op is not None and fy1.op < 0


def test_estimate_empty_is_kis_empty_not_zero() -> None:
    with pytest.raises(KisEmpty):
        parse_estimate(case("estimate_empty")["body"], "994080", snap_date=DAY)


def test_estimate_seeded_symbol() -> None:
    c = case("estimate_seeded")
    code = c["params"]["SHT_CD"]
    rows = parse_estimate(c["body"], code, snap_date=date(2026, 10, 7))
    assert len(rows) == 5 and sum(r.is_estimate for r in rows) == 3


def _body(periods: list[str], **cells: list[str]) -> dict[str, Any]:
    def row(name: str) -> dict[str, str]:
        vals = cells.get(name, [""] * len(periods))
        return dict(zip(COLUMNS, vals, strict=False))

    return {
        "rt_cd": "0",
        OUT_HEAD: {"sht_cd": "994010", "estdate": "20261116"},
        OUT_PERIODS: [{"dt": p} for p in periods],
        OUT_PL: [row(n) for n in PL_ROWS],
        OUT_IND: [row(n) for n in IND_ROWS],
    }


def test_estimate_without_markers_infers_by_fiscal_end_and_marks_estimated() -> None:
    body = _body(["2025.12", "2026.12", "2027.12"], revenue=["100", "110", "120"])
    rows = parse_estimate(body, "994010", snap_date=DAY)
    assert [(r.fiscal_ym, r.is_estimate) for r in rows] == [
        ("202512", False),
        ("202612", True),
        ("202712", True),
    ]
    assert all(r.quality is Quality.ESTIMATED for r in rows)
    assert all(r.extra["is_estimate_inferred"] for r in rows)


def test_estimate_with_only_actual_markers_keeps_future_years_as_inferred_estimates() -> None:
    """표지가 실적(A)에만 있는 모양 — 표지 없는 미래 결산 열은 실적일 수 없다(FY1·FY2 유지)."""
    body = _body(["2024.12A", "2025.12A", "2026.12", "2027.12"], revenue=["90", "95", "100", "110"])
    rows = parse_estimate(body, "994010", snap_date=DAY)
    assert [(r.fiscal_ym, r.is_estimate, r.quality) for r in rows] == [
        ("202412", False, Quality.OK),
        ("202512", False, Quality.OK),
        ("202612", True, Quality.ESTIMATED),
        ("202712", True, Quality.ESTIMATED),
    ]
    assert [r.extra.get("is_estimate_inferred", False) for r in rows] == [False, False, True, True]
    # 표지가 추정(E)에만 있는 모양 — 표지 없는 지난 결산 열은 실적(그대로 ok)
    mixed = parse_estimate(_body(["2025.12", "2026.12E"], revenue=["95", "100"]), "994010",
                           snap_date=DAY)  # fmt: skip
    assert [(r.is_estimate, r.quality) for r in mixed] == [
        (False, Quality.OK),
        (True, Quality.OK),
    ]


def test_estimate_decimal_eok_and_bad_values() -> None:
    body = _body(["202612E"], revenue=["1,234.5"], eps=["1,050.5"])
    (r,) = parse_estimate(body, "994010", snap_date=DAY)
    assert r.revenue == 123_450_000_000 and r.eps == 1050.5
    with pytest.raises(KisParseError):  # 금액이 원으로도 정수가 아니다 — 조용히 자르지 않는다
        parse_estimate(_body(["202612E"], revenue=["0.000000001"]), "994010", snap_date=DAY)
    with pytest.raises(KisParseError):
        parse_estimate(_body(["202612E"], revenue=["abc"]), "994010", snap_date=DAY)


def test_estimate_all_zero_or_blank_columns_are_empty() -> None:
    with pytest.raises(KisEmpty):
        parse_estimate(_body(["202612E", "202712E"], revenue=["0", "0"]), "994010", snap_date=DAY)
    # 머리가 빈 열은 버린다
    rows = parse_estimate(
        _body(["202612E", "", ""], revenue=["100", "", ""]), "994010", snap_date=DAY
    )
    assert [r.fiscal_ym for r in rows] == ["202612"]


def test_estimate_shape_errors() -> None:
    body = _body(["202612E"], revenue=["100"])
    body[OUT_HEAD]["sht_cd"] = "994020"
    with pytest.raises(KisParseError, match="종목코드"):
        parse_estimate(body, "994010", snap_date=DAY)
    short = _body(["202612E"], revenue=["100"])
    short[OUT_PL] = short[OUT_PL][:3]
    with pytest.raises(KisShapeError):
        parse_estimate(short, "994010", snap_date=DAY)
    dup = _body(["202612E", "2026.12E"], revenue=["100", "110"])
    with pytest.raises(KisShapeError, match="겹친다"):
        parse_estimate(dup, "994010", snap_date=DAY)
    with pytest.raises(KisShapeError):
        parse_estimate({"rt_cd": "0"}, "994010", snap_date=DAY)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("202612", ("202612", None)),
        ("2026.12", ("202612", None)),
        ("2026/03E", ("202603", True)),
        ("2026.12(E)", ("202612", True)),
        ("2025.12 (A)", ("202512", False)),
        ("202612e", ("202612", True)),
        ("2026.13", None),
        ("", None),
        ("FY2026", None),
    ],
)
def test_parse_fiscal_ym(text: str, want: tuple[str, bool | None] | None) -> None:
    assert parse_fiscal_ym(text) == want


# ── 투자의견 ───────────────────────────────────────────────────────────────────────────


def test_opinion_rows_keep_broker_names_and_zero_targets() -> None:
    rows = parse_opinion(case("opinion_not_rated")["body"], "994030")
    assert rows == sorted(rows, key=lambda o: (o.opinion_date, o.broker))
    nr = [o for o in rows if o.opinion == "Not Rated"]
    assert len(nr) == 1 and nr[0].target_price == 0.0 and nr[0].broker == "샘플증권"
    assert {o.opinion_prev for o in rows if o.opinion_prev is not None} == {"매수"}
    assert all(o.source == "KIS" and o.quality is Quality.OK for o in rows)
    assert {o.opinion for o in parse_opinion(case("opinion_neutral")["body"], "994070")} == {
        "매수",
        "중립",
    }


def test_opinion_empty_is_kis_empty() -> None:
    with pytest.raises(KisEmpty):
        parse_opinion(case("opinion_empty")["body"], "994080")


def test_opinion_duplicates_and_bad_rows() -> None:
    def r(d: str, b: str, t: str) -> dict[str, str]:
        return {"stck_bsop_date": d, "mbcr_name": b, "invt_opnn": "매수", "hts_goal_prc": t}

    body = {
        "rt_cd": "0",
        "output": [
            r("20261110", "가상증권", "10000"),
            r("20261110", "가상증권", "11000"),  # 같은 증권사·같은 날 — 앞의 것
            r("2026111", "모의증권", "9000"),  # 날짜 형식 틀림 — 버린다
            r("20261101", "", "9000"),  # 증권사 없음 — 버린다
            r("20261101", "모의증권", ""),  # 목표가 미제시 — None
        ],
    }
    rows = parse_opinion(body, "994010")
    assert [(o.broker, o.opinion_date, o.target_price) for o in rows] == [
        ("모의증권", date(2026, 11, 1), None),
        ("가상증권", date(2026, 11, 10), 10000.0),
    ]
    with pytest.raises(KisShapeError):
        parse_opinion({"rt_cd": "0", "output": [r("bad", "", "1")]}, "994010")

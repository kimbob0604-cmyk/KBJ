"""KIS 지수선물옵션 마스터 파서 — 마스터 줄 형식 그대로인 합성 줄
(tests/fixtures/synthetic/kis/master_lines.json — 생성기
`legacy/gexlab/scripts/make_synthetic_fixtures.py`, 옵션 코드·표준코드는 합성 규칙)로 고정.

GEXLAB `tests/unit/test_kis_master.py` 17개를 그대로 옮겼다(import 경로와 fixture 폴더만 바꿈 — 합성
fixture 는 `tests/fixtures/synthetic/kis/` 의 복사본). 새로: 복사본이 생성기 출력과 같고 합성 표시가
있는지.
"""

from __future__ import annotations

import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from kbj.data.private.kis.master import (
    MRKT_CLS_FAMILY,
    MasterRow,
    expiry_code,
    master_text_from_zip,
    parse_master,
    parse_master_line,
    parse_master_zip,
    rows_by_series,
    series_for,
    series_of_codes,
)

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "tests" / "fixtures" / "synthetic" / "kis"
GX_FIX = ROOT / "legacy" / "gexlab" / "tests" / "fixtures" / "kis"


def lines() -> list[str]:
    return json.loads((FIX / "master_lines.json").read_text(encoding="utf-8"))["lines"]


def by_code() -> dict[str, MasterRow]:
    return {r.code: r for r in parse_master("\n".join(lines()))}


def board_codes(name: str) -> list[str]:
    b = json.loads((FIX / name).read_text(encoding="utf-8"))
    return [r["optn_shrn_iscd"] for r in b["output1"] + b["output2"]]


def test_every_fixture_line_parses() -> None:
    rows = parse_master("\n".join(lines()) + "\n\n")
    assert len(rows) == len(lines())


def test_futures_line() -> None:
    f = by_code()["A01612"]
    assert (f.kind, f.name, f.isin, f.underlying) == ("1", "F 202612", "KR4A16120003", "KOSPI200")
    assert (f.cp, f.strike, f.moneyness) == ("", None, "")
    assert not f.is_option
    assert f.series == "F 202612"
    assert (f.family, f.expiry_token, f.expiry) == ("kospi200", "202612", "202612")


@pytest.mark.parametrize(
    ("code", "family", "expiry"),
    [
        ("A05610", "mini_kospi200", "202610"),
        ("A06612", "kosdaq150", "202612"),
        ("A04610", "other", "202610"),  # 변동성지수 선물
        ("AA4612", "other", "202612"),  # '고배당50   F 202612' — 이름에 공백 채움
        ("D0161201", "other", None),  # 스프레드 'SP 2612-2703'
    ],
)
def test_non_option_lines(code: str, family: str, expiry: str | None) -> None:
    r = by_code()[code]
    assert r.strike is None and r.cp == "" and r.moneyness == ""
    assert (r.family, r.expiry) == (family, expiry)


def test_monthly_atm_class_line_is_a_call_not_dropped() -> None:
    # 다섯째 필드 '1' 은 ATM 구분 — 콜/풋이 아니다 (2026-09-28 버그 재발 방지)
    c = by_code()["B01610ZCI"]
    assert c.name == "C 202610 1,125.0"
    assert (c.cp, c.strike, c.moneyness) == ("C", Decimal("1125.00"), "1")
    p = by_code()["C01610ZCI"]
    assert (p.cp, p.strike, p.moneyness) == ("P", Decimal("1125.00"), "1")
    # 같은 행사가 옆 칸: 콜 1127.5 는 OTM(3), 풋 1127.5 는 ITM(2)
    assert by_code()["B01610ZCJ"].moneyness == "3"
    assert by_code()["C01610ZCJ"].moneyness == "2"


def test_monthly_series_and_padded_strike() -> None:
    r = by_code()["B01610Z8A"]
    assert r.name == "C 202610   745.0"
    assert (r.cp, r.strike, r.series) == ("C", Decimal("745.00"), "C 202610")
    assert (r.family, r.expiry) == ("kospi200", "202610")


@pytest.mark.parametrize(
    ("code", "family", "cp", "strike", "series", "expiry"),
    [
        ("B05610ZCI", "mini_kospi200", "C", "1125.00", "미니C 202610", "202610"),
        ("B09FFWZCI", "kospi200_weekly_thu", "C", "1125.00", "위클리C 2610W1", "261001"),
        ("C09FFWZCI", "kospi200_weekly_thu", "P", "1125.00", "위클리P 2610W1", "261001"),
        ("BAFBZWZCI", "kospi200_weekly_mon", "C", "1125.00", "위클리M C 2609W4", "260904"),
        ("BAFC0WZCI", "kospi200_weekly_mon", "C", "1125.00", "위클리M C 2610W1", "261001"),
        ("B06610ZE6", "kosdaq150", "C", "1275.00", "코스닥150C 202610", "202610"),
        ("C06610ZAK", "kosdaq150", "P", "950.00", "코스닥150P 202610", "202610"),
        ("BAJ37WZFU", "kosdaq150_weekly_thu", "C", "1425.00", "코스닥위클리C 2610W1", "261001"),
        ("CAK48WZEG", "kosdaq150_weekly_mon", "P", "1300.00", "코스닥위클리M P 2609W4", "260904"),
    ],
)
def test_option_families(
    code: str, family: str, cp: str, strike: str, series: str, expiry: str
) -> None:
    r = by_code()[code]
    assert (r.family, r.cp, r.strike, r.series, r.expiry) == (
        family,
        cp,
        Decimal(strike),
        series,
        expiry,
    )


def test_board_codes_all_found_in_master_with_same_strike() -> None:
    m = by_code()
    for name in ("callput_202610.json", "callput_wkm_260904.json"):
        b = json.loads((FIX / name).read_text(encoding="utf-8"))
        for side, cp in (("output1", "C"), ("output2", "P")):
            for row in b[side]:
                r = m[row["optn_shrn_iscd"]]
                assert r.cp == cp
                assert r.strike == Decimal(row["acpr"])


def test_series_of_codes() -> None:
    rows = parse_master("\n".join(lines()))
    assert series_of_codes(rows, board_codes("callput_wkm_260904.json")) == [
        "위클리M C 2609W4",
        "위클리M P 2609W4",
    ]
    codes = [*board_codes("callput_202610.json"), "UNKNOWN", "A01612"]
    assert series_of_codes(rows, codes) == [
        "C 202610",
        "P 202610",
    ]


def test_rows_by_series_sorted_options_only() -> None:
    s = rows_by_series(parse_master("\n".join(lines())))
    assert "F 202612" not in s
    calls = s["C 202610"]
    strikes = [r.strike for r in calls if r.strike is not None]
    assert len(strikes) == len(calls)
    assert strikes == sorted(strikes)
    assert strikes[0] == Decimal("745.00") and strikes[-1] == Decimal("1595.00")
    assert all(r.cp == "C" for r in calls)


def test_series_for_market_class() -> None:
    rows = parse_master("\n".join(lines()))
    wk = series_for(rows, MRKT_CLS_FAMILY["WKM"], "260904", "P")
    assert {r.series for r in wk} == {"위클리M P 2609W4"}
    assert len(wk) == 20  # fixture 에 실은 전광판 행 수
    assert series_for(rows, MRKT_CLS_FAMILY["WKI"], "261001", "C")[0].code == "B09FFWZB1"
    assert series_for(rows, "kospi200", "202611", "C") == []


def test_parse_master_zip_cp949() -> None:
    text = "\n".join(lines()) + "\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fo_idx_code_mts.mst", text.encode("cp949"))
    rows = parse_master_zip(buf.getvalue())
    assert len(rows) == len(lines())
    assert any(r.name == "위클리M C 2609W4 1,125.0" for r in rows)


def test_parse_master_zip_needs_one_file() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.mst", b"")
        z.writestr("b.mst", b"")
    with pytest.raises(ValueError, match="한 개"):
        parse_master_zip(buf.getvalue())


@pytest.mark.parametrize(
    "line",
    [
        "5|B01610A51|KR4B016AA511|C 202610 1,125.0|1",  # 필드 부족
        "5||KR4B016AA511|C 202610 1,125.0|1|01125.00| |2001|KOSPI200",  # 코드 없음
        "5|B01610A51|KR4B016AA511|C 202610 1,125.0|1|abc| |2001|KOSPI200",  # 행사가
        "5|B01610A51|KR4B016AA511|C 202610 1,125.0|C|01125.00| |2001|KOSPI200",  # ATM 구분
    ],
)
def test_malformed_lines_raise(line: str) -> None:
    with pytest.raises(ValueError):
        parse_master_line(line)
    with pytest.raises(ValueError, match="2번째 줄"):
        parse_master(lines()[0] + "\n" + line)


def test_blank_line_is_skipped() -> None:
    assert parse_master_line("   ") is None


@pytest.mark.parametrize(
    ("token", "code"),
    [("202610", "202610"), ("2609W4", "260904"), ("2610W1", "261001"), ("201001", "201001")],
)
def test_expiry_code(token: str, code: str) -> None:
    assert expiry_code(token) == code


@pytest.mark.parametrize("token", ["0610", "2609W", "26094", "W4", ""])
def test_expiry_code_rejects(token: str) -> None:
    with pytest.raises(ValueError):
        expiry_code(token)


def test_master_text_from_zip_keeps_the_text_and_refuses_non_zip() -> None:
    text = "\n".join(lines()) + "\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fo_idx_code_mts.mst", text.encode("cp949"))
    assert master_text_from_zip(buf.getvalue()) == text
    with pytest.raises(ValueError, match="zip"):
        master_text_from_zip(b"<html>maintenance</html>")


# ── 새로: 합성 fixture 복사본 확인 ──


@pytest.mark.parametrize(
    "name", ["master_lines.json", "callput_202610.json", "callput_wkm_260904.json"]
)
def test_synthetic_fixtures_match_the_generator_output(name: str) -> None:
    """복사본은 합성 표시를 달고 있고, legacy GX 의 생성기 출력과 바이트까지 같다."""
    data = (FIX / name).read_bytes()
    assert json.loads(data)["_source"].startswith("SYNTHETIC")
    original = GX_FIX / name
    if not original.exists():
        pytest.skip("legacy GX fixture 가 없다(P9 정리 뒤) — 생성기를 옮긴 뒤 이 대조를 바꾼다")
    assert data == original.read_bytes()

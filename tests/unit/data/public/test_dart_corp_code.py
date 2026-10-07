"""DART 고유번호 파서(kbj.data.public.dart.corp_code) — 상장/비상장·형식 오류·크기 상한·XML 폭탄.

새로 쓴 시험(설계 §1.4). corpCode.xml 은 공개 등급이라 실데이터 발췌를 넣어도 되지만 키가 없어 합성
zip(DART 원본 모양)으로 시작한다 — 키를 받은 뒤 `tests/fixtures/public/dart/` 발췌로 바꾼다(R21).
"""

from __future__ import annotations

import io
import zipfile
from datetime import date

import pytest

from kbj.data.public.dart.corp_code import (
    CorpCode,
    CorpCodeFormatError,
    corp_xml_from_zip,
    listed_map,
    parse_corp_codes,
    parse_corp_xml,
)
from tests.unit.data.public._support import corp_zip

ROWS = [
    ("00126380", "가상전자", "029460", "20260901"),
    ("00999999", "비상장회사", "", "20250101"),
    ("00888888", "영문코드회사", "0001A0", "20260102"),
]


def test_parses_listed_and_unlisted() -> None:
    codes = parse_corp_codes(corp_zip(ROWS))
    assert [c.corp_code for c in codes] == ["00126380", "00999999", "00888888"]
    first = codes[0]
    assert first == CorpCode(
        corp_code="00126380",
        stock_code="029460",
        corp_name="가상전자",
        modify_date=date(2026, 9, 1),
    )
    assert first.source == "DART"
    assert codes[1].stock_code is None  # 공백 한 칸 → 비상장
    assert listed_map(codes) == {"029460": "00126380", "0001A0": "00888888"}


@pytest.mark.parametrize(
    ("rows", "match"),
    [
        ([("0012638", "x", "029460", "20260901")], "8자리"),
        ([("00126380", "x", "29460", "20260901")], "stock_code"),
        ([("00126380", "x", "029460", "2026-09-01")], "modify_date"),
        ([("00126380", "a", "", "20260901"), ("00126380", "b", "", "20260901")], "겹친다"),
        ([], "회사가 없다"),
    ],
)
def test_format_errors_fail_the_whole_file(
    rows: list[tuple[str, str, str, str]], match: str
) -> None:
    with pytest.raises(CorpCodeFormatError, match=match):
        parse_corp_codes(corp_zip(rows))


def test_listed_map_needs_listed_rows_and_resolves_reused_codes_by_date() -> None:
    with pytest.raises(CorpCodeFormatError, match="상장 종목이 없다"):
        listed_map(parse_corp_codes(corp_zip([ROWS[1]])))
    reused = parse_corp_codes(
        corp_zip(
            [
                ("00000001", "옛 회사", "123450", "20100101"),
                ("00000002", "새 회사", "123450", "20260101"),
            ]
        )
    )
    assert listed_map(reused) == {"123450": "00000002"}
    tie = parse_corp_codes(
        corp_zip(
            [("00000001", "가", "123450", "20260101"), ("00000002", "나", "123450", "20260101")]
        )
    )
    with pytest.raises(CorpCodeFormatError, match="두 회사"):
        listed_map(tie)


def test_zip_shape_and_size_limits() -> None:
    with pytest.raises(CorpCodeFormatError, match="zip 이 아니다"):
        corp_xml_from_zip(b"<result><status>013</status></result>")
    with pytest.raises(CorpCodeFormatError, match="열 수 없다"):
        corp_xml_from_zip(b"PK\x03\x04broken")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.xml", "<result/>")
        zf.writestr("b.xml", "<result/>")
    with pytest.raises(CorpCodeFormatError, match="하나가 아니다"):
        corp_xml_from_zip(buf.getvalue())
    big = corp_zip(ROWS)
    with pytest.raises(CorpCodeFormatError, match="크다"):
        corp_xml_from_zip(big, max_xml_bytes=100)
    assert corp_xml_from_zip(big).startswith(b"<?xml")


def test_entity_expansion_is_refused() -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><result><list>&b;</list></result>'
    )
    with pytest.raises(CorpCodeFormatError, match="XML"):
        parse_corp_xml(bomb)

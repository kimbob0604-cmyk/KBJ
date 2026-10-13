"""`corp_index.json` — 상장사 목록(검색·이름표). 합성 공개 표(`pub_rows`)로 본다(DB 없음)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from kbj.services.public_export.corp_index import (
    COLUMNS,
    NAME,
    Q_CORPS,
    build_corp_index,
    corps_from_rows,
    load_corps,
    market_of,
)
from kbj.services.public_export.manifest import check_payload
from tests.unit.public_export.pub_rows import CODES, DUP_CODE, HOLD, NOW, REIT, reader


def _index(fin: set[str] | None = None, rep: set[str] | None = None) -> dict[str, list[object]]:
    corps = load_corps(reader())
    f = build_corp_index(corps, NOW, fin_codes=fin or set(), report_codes=rep or set())
    data = json.loads(f.render(NOW))["data"]
    assert data["columns"] == list(COLUMNS)
    assert data["n"] == len(data["rows"])
    return {r[0]: r for r in data["rows"]}


def test_listed_companies_only_and_duplicate_code_dropped() -> None:
    rows = _index()
    assert sorted(rows) == sorted(CODES.values())  # 비상장(종목코드 없음)·겹친 코드 없음
    assert DUP_CODE not in rows
    corps = load_corps(reader())
    assert corps.duplicates == 1
    f = build_corp_index(corps, NOW)
    assert any("겹친 1건" in n for n in f.notes)


def test_columns_market_and_fiscal_month() -> None:
    rows = _index(fin={CODES[REIT]}, rep={CODES[REIT]})
    assert rows["Q00001"] == ["Q00001", "합성전자", "KOSPI", 12, False, False]
    assert rows["Q00002"][2:4] == ["KOSDAQ", 3]
    # 기업개황이 없으면 법인 구분은 가장 최근 공시 행, 결산월은 null(12월로 보지 않는다)
    assert rows[CODES[HOLD]][2:4] == ["KONEX", None]
    assert rows[CODES[REIT]][4:] == [True, True]


def test_envelope_is_dart_only_and_clean() -> None:
    corps = load_corps(reader())
    f = build_corp_index(corps, NOW)
    assert (f.name, f.source, f.quality) == (NAME, "DART", "ok")
    assert f.as_of == corps.received_at
    assert check_payload(f.name, f.render(NOW)) == []


def test_stale_when_corp_code_not_refreshed() -> None:
    corps = load_corps(reader())
    f = build_corp_index(corps, NOW + timedelta(days=8))
    assert f.quality == "stale"
    assert any("7일" in n for n in f.notes)


def test_empty_table_is_refused() -> None:
    with pytest.raises(ValueError, match="비었다"):
        build_corp_index(corps_from_rows([]), NOW)


@pytest.mark.parametrize(
    ("cls", "market"), [("Y", "KOSPI"), ("K", "KOSDAQ"), ("N", "KONEX"), ("E", None), (None, None)]
)
def test_market_of(cls: str | None, market: str | None) -> None:
    assert market_of(cls) == market


def test_reads_one_query() -> None:
    r = reader()
    load_corps(r)
    assert [c[0] for c in r.calls] == [Q_CORPS.name]

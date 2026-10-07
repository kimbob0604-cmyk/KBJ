"""KOSIS 어댑터(kbj.data.public.kosis) — HTTPS·파라미터·통계부호·오류 봉투·키 가리기.

새로 쓴 시험(설계 §1.4). 응답은 개발 가이드(0201 통계자료) 모양의 합성 JSON 이다. 오류 코드 표는
[추정]이라 키를 받은 뒤 실측으로 고친다(probe_results §7 #10). 공개 실데이터로 바꾸는 것은 R21.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from kbj.core.quality import Quality
from kbj.data.public.kosis.client import (
    KosisClient,
    KosisError,
    KosisKeyError,
    KosisThrottled,
    parse_dt,
)
from kbj.data.public.kosis.datasets import DATASETS, ORG_ID
from tests.unit.data.public._support import FakeServer, RecordingLimiter

KEY = "FAKEKOSISKEY+abc/def="


def _row(prd: str, dt: str) -> dict[str, Any]:
    return {
        "ORG_ID": "101",
        "TBL_ID": "DT_1C8015",
        "TBL_NM": "경기종합지수(2020=100)(10차)",
        "C1": "A03",
        "C1_NM": "동행지수순환변동치",
        "C1_OBJ_NM": "지수별",
        "ITM_ID": "T1",
        "ITM_NM": "지수",
        "UNIT_NM": "2020=100",
        "PRD_SE": "M",
        "PRD_DE": prd,
        "DT": dt,
        "LST_CHN_DE": "2026-09-30",
    }


def _make(reply: Any) -> tuple[KosisClient, FakeServer, RecordingLimiter]:
    server = FakeServer(reply)
    limiter = RecordingLimiter()
    return (
        KosisClient(SecretStr(KEY), limiter=limiter, transport=server.transport()),
        server,
        limiter,
    )


def test_parameter_data_sends_https_request_with_params() -> None:
    rows = [_row("202607", "98.7"), _row("202608", "-")]
    c, server, limiter = _make(lambda r: (200, json.dumps(rows, ensure_ascii=False)))
    got = c.parameter_data("101", "DT_1C8015", "A03", "T1", "M", start="202607", end="202608")
    req = server.requests[0]
    assert req.url.scheme == "https" and req.url.host == "kosis.kr"
    assert req.url.path == "/openapi/Param/statisticsParameterData.do"
    p = server.params()
    assert p["apiKey"] == KEY
    expected = {
        "method": "getList",
        "orgId": "101",
        "tblId": "DT_1C8015",
        "objL1": "A03",
        "itmId": "T1",
        "prdSe": "M",
        "format": "json",
        "jsonVD": "Y",
        "startPrdDe": "202607",
        "endPrdDe": "202608",
    }
    assert {k: p[k] for k in expected} == expected
    assert "newEstPrdCnt" not in p
    assert len(limiter.acquired) == 1
    a, b = got
    assert (a.source, a.prd_de, a.value, a.quality) == (
        "KOSIS:DT_1C8015",
        "202607",
        Decimal("98.7"),
        Quality.OK,
    )
    assert a.classes == (("A03", "동행지수순환변동치"),)
    assert a.as_of == "202607"  # 값이 가리키는 시점(PRD_DE) — 절대 규칙 1
    assert b.value is None and b.raw_value == "-" and b.quality is Quality.INVALID


def test_non_object_rows_fail_instead_of_being_skipped() -> None:
    body = json.dumps([_row("202607", "98.7"), "x"], ensure_ascii=False)
    c, _, _ = _make(lambda r: (200, body))
    with pytest.raises(KosisError, match="객체가 아닌 행"):
        c.parameter_data("101", "DT_1C8015", "A03", "T1", "M", newest=1)


def test_newest_and_more_classifications() -> None:
    c, server, _ = _make(lambda r: (200, "[]"))
    assert (
        c.parameter_data("101", "DT_1DA7001S", "ALL", "ALL", "M", newest=3, obj_more=("0", "1"))
        == []
    )
    p = server.params()
    assert (p["newEstPrdCnt"], p["objL2"], p["objL3"]) == ("3", "0", "1")
    assert "startPrdDe" not in p


def test_argument_validation_happens_before_calling() -> None:
    c, server, _ = _make(lambda r: (200, "[]"))
    with pytest.raises(ValueError, match="newest"):
        c.parameter_data("101", "T", "A", "I", "M", start="202601", end="202602", newest=2)
    with pytest.raises(ValueError, match="prdSe"):
        c.parameter_data("101", "T", "A", "I", "W")
    with pytest.raises(ValueError, match="start"):
        c.parameter_data("101", "T", "A", "I", "M", start="202602")
    with pytest.raises(ValueError, match="objL1"):
        c.parameter_data("101", "T", " ", "I", "M")
    assert server.requests == []


@pytest.mark.parametrize(
    ("code", "exc"),
    [("10", KosisKeyError), ("11", KosisKeyError), ("40", KosisThrottled), ("21", KosisError)],
)
def test_error_envelope(code: str, exc: type[KosisError]) -> None:
    body = json.dumps({"err": code, "errMsg": f"오류 apiKey={KEY}"}, ensure_ascii=False)
    c, _, limiter = _make(lambda r: (200, body))
    with pytest.raises(exc) as e:
        c.parameter_data("101", "DT_1C8015", "A03", "T1", "M", newest=1)
    assert e.value.code == code and f"err={code}" in str(e.value)
    assert KEY not in str(e.value)
    assert limiter.slowdowns == (1 if exc is KosisThrottled else 0)
    assert e.value.critical == (exc is KosisKeyError)


def test_no_data_code_is_an_empty_list() -> None:
    c, _, _ = _make(lambda r: (200, '{"err":"30","errMsg":"데이터가 존재하지 않습니다."}'))
    assert c.parameter_data("101", "DT_1C8015", "A03", "T1", "M", newest=1) == []


def test_transport_and_format_errors_hide_the_key() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timeout {request.url}", request=request)

    c = KosisClient(SecretStr(KEY), limiter=RecordingLimiter(), transport=httpx.MockTransport(boom))
    with pytest.raises(KosisError) as e:
        c.meta("101", "DT_1C8015")
    assert "ReadTimeout" in str(e.value) and KEY not in str(e.value)
    c2, _, _ = _make(lambda r: (200, "<html>maintenance</html>"))
    with pytest.raises(KosisError, match="JSON"):
        c2.meta("101", "DT_1C8015")
    assert KEY not in repr(c2)


def test_meta_returns_rows_as_strings() -> None:
    c, server, _ = _make(lambda r: (200, '[{"TBL_NM":"경기종합지수","CNT":12,"X":null}]'))
    assert c.meta("101", "DT_1C8015") == [{"TBL_NM": "경기종합지수", "CNT": "12", "X": ""}]
    p = server.params()
    assert (p["method"], p["type"]) == ("getMeta", "TBL")


def test_parse_dt_symbols_are_none() -> None:
    assert parse_dt("1,234.5") == Decimal("1234.5")
    assert [parse_dt(s) for s in ("", "-", "x", "…", None)] == [None] * 5


def test_datasets_are_domestic_public_tables() -> None:
    assert {d.dataset for d in DATASETS} >= {
        "DT_1C8015",
        "DT_1JH20201",
        "DT_1DA7001S",
        "DT_1J22003",
    }
    assert all(d.tier.value == "public" and d.store == "pub_macro.series" for d in DATASETS)
    assert all(d.notes.startswith(f"orgId {ORG_ID}") for d in DATASETS)

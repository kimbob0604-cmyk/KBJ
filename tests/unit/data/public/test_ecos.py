"""ECOS 어댑터(kbj.data.public.ecos) — 공개 표만·INFO-200·602 감속·그날 닫기·범위 나누기·키 가리기.

새로 쓴 시험(설계 §1.4). 응답은 개발 가이드(OA-1020/1030/1040) 모양의 합성 JSON 이다. 공개
실데이터 fixture(한국은행 작성 표만 — probe_results §4.3)는 키를 받은 뒤
`tests/fixtures/public/ecos/` 로 바꾼다.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from pydantic import SecretStr

from kbj.core.quality import Quality
from kbj.data.public.ecos.client import (
    PUBLIC_TABLES,
    RESTRICTED_TABLES,
    EcosClient,
    EcosClosed,
    EcosError,
    EcosKeyError,
    EcosRangeTooLarge,
    EcosTable,
    EcosThrottled,
    EcosTransport,
    TierError,
    public_table_problems,
    split_period,
)
from kbj.data.public.ecos.datasets import DATASETS
from tests.fakes.clock import FakeClock
from tests.unit.data.public._support import T0, FakeServer, RecordingLimiter

KEY = "FAKEECOSKEY0123456789"


def _row(time: str, value: str, item: str = "010200000") -> dict[str, Any]:
    return {
        "STAT_CODE": "817Y002",
        "STAT_NAME": "1.3.2.1. 시장금리(일별)",
        "ITEM_CODE1": item,
        "ITEM_NAME1": "국고채(3년)",
        "ITEM_CODE2": None,
        "ITEM_NAME2": None,
        "UNIT_NAME": "연%",
        "WGT": None,
        "TIME": time,
        "DATA_VALUE": value,
    }


def _ok(service: str, rows: list[dict[str, Any]], total: int | None = None) -> str:
    n = len(rows) if total is None else total
    return json.dumps({service: {"list_total_count": n, "row": rows}}, ensure_ascii=False)


def _result(code: str, msg: str) -> str:
    return json.dumps({"RESULT": {"CODE": code, "MESSAGE": msg}}, ensure_ascii=False)


def _segments(req: httpx.Request) -> list[str]:
    return [unquote(s) for s in req.url.raw_path.decode().split("/")[2:]]  # /api/ 다음


def _make(reply: Any, **kw: Any) -> tuple[EcosClient, FakeServer, RecordingLimiter, FakeClock]:
    server = FakeServer(reply)
    limiter = RecordingLimiter()
    clock = FakeClock(T0)
    c = EcosClient(
        SecretStr(KEY),
        limiter=limiter,
        transport=server.transport(),
        now=clock,
        monotonic=clock.monotonic,
        **kw,
    )
    return c, server, limiter, clock


def test_search_builds_the_path_and_parses_rows() -> None:
    rows = [_row("20261002", "2.581"), _row("20261005", ""), _row("20261006", "2.603")]
    c, server, limiter, _ = _make(lambda r: (200, _ok("StatisticSearch", rows)))
    got = c.search("817Y002", "D", "20261001", "20261006", ("010200000",))
    seg = _segments(server.requests[0])
    assert seg == [
        "StatisticSearch",
        KEY,
        "json",
        "kr",
        "1",
        "10000",
        "817Y002",
        "D",
        "20261001",
        "20261006",
        "010200000",
    ]
    assert server.requests[0].url.scheme == "https"
    assert len(got) == 3 and limiter.acquired[0][1] == "ecos:StatisticSearch"
    a, missing, b = got
    assert (a.source, a.time, a.value, a.quality) == (
        "ECOS:817Y002",
        "20261002",
        Decimal("2.581"),
        Quality.OK,
    )
    assert a.item_codes == ("010200000",) and a.unit == "연%"
    assert a.as_of == "20261002"  # 값이 가리키는 시점(TIME) — 절대 규칙 1
    assert missing.value is None and missing.quality is Quality.INVALID and missing.raw_value == ""
    assert b.value == Decimal("2.603")


def test_non_object_rows_fail_instead_of_being_skipped() -> None:
    rows: list[Any] = [_row("20261002", "1"), 7]
    body = json.dumps({"StatisticSearch": {"list_total_count": 2, "row": rows}})
    c, _, _, _ = _make(lambda r: (200, body))
    with pytest.raises(EcosError, match="객체가 아닌 행"):
        c.search("817Y002", "D", "20261001", "20261002")


def test_info_200_is_an_empty_result() -> None:
    c, _, _, _ = _make(lambda r: (200, _result("INFO-200", "해당하는 데이터가 없습니다.")))
    assert c.search("722Y001", "D", "20261003", "20261003") == []


def test_non_numeric_value_fails_loudly() -> None:
    c, _, _, _ = _make(lambda r: (200, _ok("StatisticSearch", [_row("20261002", "N/A")])))
    with pytest.raises(EcosError, match="숫자"):
        c.search("817Y002", "D", "20261002", "20261002")


def test_invalid_key_is_critical_and_hidden() -> None:
    c, _, _, _ = _make(lambda r: (200, _result("INFO-100", f"인증키가 유효하지 않습니다 {KEY}")))
    with pytest.raises(EcosKeyError) as e:
        c.search("817Y002", "D", "20261002", "20261002")
    assert e.value.critical and e.value.code == "INFO-100"
    assert KEY not in str(e.value)


def test_transport_error_message_has_no_key_even_though_the_key_is_in_the_path() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed {request.url}", request=request)

    limiter = RecordingLimiter()
    t = EcosTransport(SecretStr(KEY), limiter=limiter, transport=httpx.MockTransport(boom))
    with pytest.raises(EcosError) as e:
        t.statistic_search("817Y002", "D", "20261002", "20261002")
    assert "ConnectError" in str(e.value)
    assert KEY not in str(e.value)
    assert "EcosTransport(key=***)" == repr(t) and KEY not in repr(
        EcosClient(SecretStr(KEY), limiter=limiter)
    )


def test_602_slows_down_and_closes_the_day_after_three() -> None:
    c, server, limiter, clock = _make(
        lambda r: (200, _result("ERROR-602", "과도한 OpenAPI호출로 이용이 제한되었습니다.")),
        close_after_throttles=3,
    )
    for _ in range(2):
        with pytest.raises(EcosThrottled) as e:
            c.search("817Y002", "D", "20261002", "20261002")
        assert not isinstance(e.value, EcosClosed)
    with pytest.raises(EcosClosed):
        c.search("817Y002", "D", "20261002", "20261002")
    assert limiter.slowdowns == 3 and len(server.requests) == 3
    with pytest.raises(EcosClosed):  # 그날은 부르지 않는다
        c.search("817Y002", "D", "20261002", "20261002")
    assert len(server.requests) == 3
    clock.advance(timedelta(days=1).total_seconds())  # 다음 KST 날짜에는 다시 연다
    server.reply = lambda r: (200, _ok("StatisticSearch", [_row("20261006", "2.6")]))
    assert len(c.search("817Y002", "D", "20261006", "20261006")) == 1


def test_range_too_large_is_split_in_halves() -> None:
    def reply(req: httpx.Request) -> tuple[int, str]:
        seg = _segments(req)
        start, end = seg[8], seg[9]
        if (int(end) - int(start)) > 3:  # 같은 달 안 날짜만 쓰는 시험 — 4일 넘으면 범위 과다
            return 200, _result("ERROR-400", "검색범위가 적정범위를 초과하여 60초 TIMEOUT")
        return 200, _ok("StatisticSearch", [_row(start, "2.5"), _row(end, "2.6")])

    c, server, _, _ = _make(reply)
    rows = c.search("817Y002", "D", "20260901", "20260912")
    ranges = [(_segments(r)[8], _segments(r)[9]) for r in server.requests]
    assert ranges[0] == ("20260901", "20260912")
    leaves = [x for x in ranges[1:] if int(x[1]) - int(x[0]) <= 3]
    assert leaves == [
        ("20260901", "20260903"),
        ("20260904", "20260906"),
        ("20260907", "20260909"),
        ("20260910", "20260912"),
    ]
    assert len(rows) == 8 and rows[0].time == "20260901" and rows[-1].time == "20260912"


def test_range_too_large_on_a_single_period_is_raised() -> None:
    c, _, _, _ = _make(lambda r: (200, _result("ERROR-400", "검색범위 초과")))
    with pytest.raises(EcosRangeTooLarge):
        c.search("817Y002", "D", "20260901", "20260901")
    assert split_period("M", "202401", "202412") == (("202401", "202406"), ("202407", "202412"))
    assert split_period("Q", "2020Q1", "2021Q4") == (("2020Q1", "2020Q4"), ("2021Q1", "2021Q4"))
    assert split_period("A", "2000", "2001") == (("2000", "2000"), ("2001", "2001"))
    assert split_period("D", "20260228", "20260303") == (
        ("20260228", "20260301"),
        ("20260302", "20260303"),
    )
    assert split_period("S", "2020S1", "2021S2") is None


def test_pages_by_list_total_count() -> None:
    def reply(req: httpx.Request) -> tuple[int, str]:
        first, last = int(_segments(req)[4]), int(_segments(req)[5])
        rows = [_row(f"2026{1000 + i:04d}"[:8], "1.0") for i in range(first, min(last, 5) + 1)]
        return 200, _ok("StatisticSearch", rows, total=5)

    server = FakeServer(reply)
    t = EcosTransport(
        SecretStr(KEY), limiter=RecordingLimiter(), transport=server.transport(), page_size=2
    )
    rows = t.statistic_search("817Y002", "D", "20261001", "20261031")
    assert len(rows) == 5
    assert [(_segments(r)[4], _segments(r)[5]) for r in server.requests] == [
        ("1", "2"),
        ("3", "4"),
        ("5", "6"),
    ]


def test_public_client_refuses_other_agency_tables_without_calling() -> None:
    c, server, _, _ = _make(lambda r: (200, _ok("StatisticSearch", [])))
    for code in RESTRICTED_TABLES:
        with pytest.raises(TierError, match=code):
            c.search(code, "D", "20261001", "20261002")
        with pytest.raises(TierError):
            c.item_list(code)
    with pytest.raises(TierError, match="공개 판정 전"):
        c.search("999Y999", "M", "202601", "202602")
    assert server.requests == []
    # 전송층은 등급을 보지 않는다 — 로그인 쪽(ecos_restricted)이 재사용한다
    c.transport.statistic_search("802Y001", "D", "20261001", "20261002")
    assert len(server.requests) == 1


def test_table_and_item_lists() -> None:
    tables = [
        {
            "P_STAT_CODE": "0000000",
            "STAT_CODE": "817Y002",
            "STAT_NAME": "시장금리(일별)",
            "CYCLE": "D",
            "SRCH_YN": "Y",
            "ORG_NAME": "한국은행",
        },
        {
            "P_STAT_CODE": "0000000",
            "STAT_CODE": "802Y001",
            "STAT_NAME": "주식시장(일)",
            "CYCLE": "D",
            "SRCH_YN": "Y",
            "ORG_NAME": "한국거래소",
        },
    ]
    items = [
        {
            "STAT_CODE": "817Y002",
            "ITEM_CODE": "010200000",
            "ITEM_NAME": "국고채(3년)",
            "GRP_CODE": "Group1",
            "CYCLE": "D",
            "START_TIME": "19981113",
            "END_TIME": "20261006",
            "DATA_CNT": "6900",
            "UNIT_NAME": "연%",
        },
    ]

    def reply(req: httpx.Request) -> tuple[int, str]:
        svc = _segments(req)[0]
        return 200, _ok(svc, tables if svc == "StatisticTableList" else items)

    c, _, _, _ = _make(reply)
    got = c.table_list()
    assert [(t.stat_code, t.org_name, t.searchable) for t in got] == [
        ("817Y002", "한국은행", True),
        ("802Y001", "한국거래소", True),
    ]
    it = c.item_list("817Y002")
    assert it[0].item_code == "010200000" and it[0].data_count == 6900


def test_public_table_problems_flags_missing_and_other_agencies() -> None:
    ok = [
        EcosTable(
            stat_code=s,
            stat_name="",
            cycle="",
            org_name="한국은행",
            searchable=True,
            parent_code="",
        )
        for s in PUBLIC_TABLES
    ]
    assert public_table_problems(ok) == []
    bad = [*ok[1:], ok[0].model_copy(update={"org_name": "서울외국환중개"})]
    assert public_table_problems(bad) == [
        f"{ok[0].stat_code}: 작성기관이 서울외국환중개 — 공개 표에서 뺀다"
    ]
    assert any("목록에 없다" in p for p in public_table_problems(ok[1:]))


def test_input_validation_happens_before_calling() -> None:
    c, server, _, _ = _make(lambda r: (200, _ok("StatisticSearch", [])))
    with pytest.raises(ValueError, match="주기"):
        c.search("817Y002", "W", "20261001", "20261002")
    with pytest.raises(ValueError, match="항목"):
        c.search("817Y002", "D", "20261001", "20261002", ("a/b",))
    with pytest.raises(ValueError):
        EcosClient(SecretStr(" "), limiter=RecordingLimiter())
    assert server.requests == []


def test_datasets_are_public_tables_only() -> None:
    ids = [d.dataset for d in DATASETS]
    assert len(ids) == len(set(ids))
    assert set(ids) <= PUBLIC_TABLES
    assert not set(ids) & set(RESTRICTED_TABLES)
    assert all(d.store == "pub_macro.series" and d.limiter == "ecos" for d in DATASETS)

"""공공데이터포털 공통 전송층(kbj.data.datago) — 키 두 형태·게이트웨이 오류 봉투·예산 닫기·리미터.

새로 쓴 시험(ET `board/ingest/datago.py` 에는 시험이 없었다 — 설계 §1.4). 응답 모양은 포털 공통
봉투(probe_results §1, DG:15100475 오류 코드 표)를 따른 합성 문자열이다. 키는 가짜 값이다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from kbj.data.budget import BudgetExhausted, DailyBudget
from kbj.data.datago import (
    DatagoAccessDenied,
    DatagoAgencyError,
    DatagoError,
    DatagoFormatError,
    DatagoGone,
    DatagoKeyError,
    DatagoQuotaExceeded,
    DatagoThrottled,
    DatagoTransport,
    GwAction,
    key_forms,
    parse_amount,
    parse_gw_error,
    unwrap_items,
    unwrap_xml_items,
)
from kbj.data.ratelimit import Priority
from tests.fakes.clock import FakeClock

# 포털 'Decoding' 형태(base64 꼴 — +·/·= 포함)와 그 'Encoding' 형태. 가짜 값이다
RAW_DECODED = "AbCd+EfGh/IjKl0123456789MnOpQrStUvWxYz==" * 2
RAW_ENCODED = quote(RAW_DECODED, safe="")
DS = "15101609"
PATH = "/1220000/Itemtrade/getItemtradeList"
T0 = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)  # 10:00 KST


def _gw(code: str, name: str, msg: str = "SERVICE ERROR") -> str:
    return (
        "<OpenAPI_ServiceResponse><cmmMsgHeader>"
        f"<errMsg>{msg}</errMsg><returnAuthMsg>{name}</returnAuthMsg>"
        f"<returnReasonCode>{code}</returnReasonCode>"
        "</cmmMsgHeader></OpenAPI_ServiceResponse>"
    )


OK_XML = (
    "<response><header><resultCode>00</resultCode><resultMsg>정상</resultMsg></header>"
    "<body><items><item><year>2026.08</year><hsCode>8542</hsCode></item></items>"
    "<totalCount>1</totalCount></body></response>"
)


class RecordingLimiter:
    """`RateLimiter` 모양 — 허가 요청과 감속 신호를 센다."""

    def __init__(self) -> None:
        self.acquired: list[tuple[Priority, str]] = []
        self.slowdowns = 0

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        self.acquired.append((priority, tr_id))

    def on_rate_limited(self) -> float:
        self.slowdowns += 1
        return 1.0


class Portal:
    """가짜 포털 — 요청마다 `reply(serviceKey, 요청)` 의 (상태, 본문)을 돌려주고 요청을 남긴다."""

    def __init__(self, reply: Callable[[str, httpx.Request], tuple[int, str]]) -> None:
        self.reply = reply
        self.requests: list[httpx.Request] = []

    def keys(self) -> list[str]:
        return [parse_qs(urlsplit(str(r.url)).query)["serviceKey"][0] for r in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = parse_qs(urlsplit(str(request.url)).query)["serviceKey"][0]
        status, body = self.reply(key, request)
        return httpx.Response(status, content=body.encode())


def _make(
    portal: Portal,
    *,
    raw: str = RAW_ENCODED,
    cap: int = 100,
    waits: tuple[float, ...] = (3.0, 6.0, 12.0),
) -> tuple[DatagoTransport, RecordingLimiter, DailyBudget, list[float], FakeClock]:
    clock = FakeClock(T0)
    limiter = RecordingLimiter()
    budget = DailyBudget(fakeredis.FakeRedis(), "datago", cap, scope=DS)
    slept: list[float] = []
    t = DatagoTransport(
        SecretStr(raw),
        limiters={DS: limiter},
        budgets={DS: budget},
        transport=httpx.MockTransport(portal.handler),
        retry_waits_s=waits,
        now=clock,
        sleep=slept.append,
        monotonic=clock.monotonic,
    )
    return t, limiter, budget, slept, clock


def _no_key(text: str) -> None:
    for form in (RAW_DECODED, RAW_ENCODED, quote_plus(RAW_DECODED)):
        assert form not in text


# ── 순수 함수 ───────────────────────────────────────────────────────────────────────────────


def test_key_forms_tries_decoded_first_only_when_percent_encoded() -> None:
    assert key_forms(RAW_ENCODED) == [("디코딩", RAW_DECODED), ("그대로", RAW_ENCODED)]
    assert key_forms(RAW_DECODED) == [("그대로", RAW_DECODED)]
    assert key_forms("  plainkey \n") == [("그대로", "plainkey")]
    with pytest.raises(ValueError):
        key_forms("   ")


@pytest.mark.parametrize(
    ("code", "name", "action"),
    [
        ("22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR", GwAction.QUOTA),
        ("23", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR", GwAction.THROTTLE),
        ("30", "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", GwAction.KEY),
        ("31", "DEADLINE_HAS_EXPIRED_ERROR", GwAction.KEY),
        ("12", "NO_OPENAPI_SERVICE_ERROR", GwAction.GONE),
        ("20", "SERVICE_ACCESS_DENIED_ERROR", GwAction.DENIED),
        ("05", "SERVICETIME_OUT", GwAction.RETRY),
        ("99", "UNKNOWN_ERROR", GwAction.FAIL),
    ],
)
def test_parse_gw_error_reads_the_xml_envelope(code: str, name: str, action: GwAction) -> None:
    gw = parse_gw_error(_gw(code, name))
    assert gw is not None
    assert (gw.code, gw.name, gw.action) == (code, name, action)
    assert f"GW {code}" in gw.described


def test_parse_gw_error_normalizes_and_falls_back() -> None:
    one_digit = parse_gw_error(_gw("5", "SERVICETIME_OUT"))
    assert one_digit is not None and one_digit.code == "05"
    named_only = parse_gw_error(
        "<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>"
        "SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"
    )
    assert named_only is not None and named_only.code == "30"
    bare = parse_gw_error("<OpenAPI_ServiceResponse></OpenAPI_ServiceResponse>")
    assert bare is not None and bare.code == "99"
    # 기관 봉투(JSON 헤더)에 게이트웨이 이름이 담겨 오는 경우
    js = (
        '{"response":{"header":{"resultCode":"22",'
        '"resultMsg":"LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"}}}'
    )
    in_json = parse_gw_error(js)
    assert in_json is not None and in_json.action is GwAction.QUOTA


def test_parse_gw_error_leaves_normal_and_agency_nodata_alone() -> None:
    assert parse_gw_error(OK_XML) is None
    assert parse_gw_error('{"response":{"header":{"resultCode":"00"}}}') is None
    # 기관 03 NODATA_ERROR 는 기관 오류(빈 결과) — 전송층이 따로 할 일이 없다
    nodata = (
        "<response><header><resultCode>03</resultCode>"
        "<resultMsg>NODATA_ERROR</resultMsg></header></response>"
    )
    assert parse_gw_error(nodata) is None


def test_unwrap_items_handles_single_dict_empty_string_and_errors() -> None:
    one = {
        "response": {
            "header": {"resultCode": "00"},
            "body": {"items": {"item": {"a": "1"}}, "totalCount": "1"},
        }
    }
    assert unwrap_items(one) == ([{"a": "1"}], 1)
    empty = {"response": {"header": {"resultCode": "00"}, "body": {"items": "", "totalCount": 0}}}
    assert unwrap_items(empty) == ([], 0)
    assert unwrap_items({"response": {"header": {"resultCode": "03"}}}) == ([], 0)
    with pytest.raises(DatagoAgencyError, match="resultCode=99"):
        unwrap_items(
            {"response": {"header": {"resultCode": "99", "resultMsg": "조회기간 초과"}}}, DS
        )
    with pytest.raises(DatagoFormatError, match="body"):
        unwrap_items({"response": {"header": {"resultCode": "00"}}}, DS)
    with pytest.raises(DatagoFormatError):
        unwrap_items([], DS)


def test_unwrap_xml_items_reads_rows_and_total() -> None:
    from defusedxml.ElementTree import fromstring

    rows, total = unwrap_xml_items(fromstring(OK_XML))
    assert rows == [{"year": "2026.08", "hsCode": "8542"}] and total == 1
    bad = fromstring(
        "<response><header><resultCode>99</resultCode>"
        "<resultMsg>필수 누락</resultMsg></header></response>"
    )
    with pytest.raises(DatagoAgencyError, match="99"):
        unwrap_xml_items(bad, DS)


def test_parse_amount_strips_commas_and_spaces() -> None:
    from decimal import Decimal

    assert parse_amount(" 13,886,115") == Decimal("13886115")
    assert parse_amount("-1,200") == Decimal("-1200")
    assert parse_amount("12.50") == Decimal("12.50")
    assert parse_amount("") is None and parse_amount(" - ") is None and parse_amount(None) is None
    with pytest.raises(ValueError):
        parse_amount("abc")
    with pytest.raises(ValueError):
        parse_amount("NaN")


# ── 전송 ────────────────────────────────────────────────────────────────────────────────────


def test_encoded_key_is_sent_decoded_and_the_form_is_remembered() -> None:
    portal = Portal(lambda k, r: (200, OK_XML))
    t, limiter, budget, _, clock = _make(portal)
    resp = t.call(DS, PATH, {"strtYymm": "202608", "endYymm": "202608"}, want="xml")
    assert resp.items() == ([{"year": "2026.08", "hsCode": "8542"}], 1)
    assert portal.keys() == [RAW_DECODED]  # httpx 가 한 번 인코딩한 것을 포털이 푼 값
    assert t.key_form == "디코딩" and resp.key_form == "디코딩"
    assert limiter.acquired == [(Priority.P3, DS)]
    assert budget.used(clock.now()) == 1
    assert "***" in repr(t)
    _no_key(repr(t))


def test_key_error_moves_to_the_next_form_and_counts_every_call() -> None:
    def reply(key: str, _: httpx.Request) -> tuple[int, str]:
        if key == RAW_DECODED:
            return 200, _gw("30", "SERVICE_KEY_IS_NOT_REGISTERED_ERROR")
        return 200, OK_XML

    portal = Portal(reply)
    t, limiter, budget, _, clock = _make(portal)
    t.call(DS, PATH, {}, want="xml")
    assert portal.keys() == [RAW_DECODED, RAW_ENCODED]
    assert t.key_form == "그대로"
    assert budget.used(clock.now()) == 2 and len(limiter.acquired) == 2  # 실패한 호출도 센다
    t.call(DS, PATH, {}, want="xml")  # 통한 형태를 먼저 쓴다
    assert portal.keys()[-1] == RAW_ENCODED and len(portal.requests) == 3


def test_http_401_also_counts_as_a_key_error() -> None:
    portal = Portal(lambda k, r: (401, "Unauthorized") if k == RAW_DECODED else (200, OK_XML))
    t, *_ = _make(portal)
    t.call(DS, PATH, {}, want="xml")
    assert t.key_form == "그대로"


def test_all_forms_rejected_is_critical_and_hides_the_key() -> None:
    portal = Portal(lambda k, r: (200, _gw("30", "SERVICE_KEY_IS_NOT_REGISTERED_ERROR")))
    t, *_ = _make(portal)
    with pytest.raises(DatagoKeyError) as e:
        t.call(DS, PATH, {}, want="xml")
    assert e.value.critical and e.value.gw is not None and e.value.gw.code == "30"
    assert "SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in str(e.value)
    assert "디코딩" in str(e.value) and "그대로" in str(e.value)
    _no_key(str(e.value))


def test_daily_quota_closes_the_budget_for_the_day() -> None:
    portal = Portal(
        lambda k, r: (200, _gw("22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"))
    )
    t, _, budget, _, clock = _make(portal)
    with pytest.raises(DatagoQuotaExceeded) as e:
        t.call(DS, PATH, {}, want="xml")
    assert not e.value.critical
    reason = budget.closed_reason(clock.now())
    assert reason is not None and "GW 22" in reason
    with pytest.raises(BudgetExhausted):  # 그날은 부르지 않는다
        t.call(DS, PATH, {}, want="xml")
    assert len(portal.requests) == 1


def test_per_second_limit_slows_the_limiter_and_retries() -> None:
    replies = iter(
        [
            (200, _gw("23", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR")),
            (200, OK_XML),
        ]
    )
    portal = Portal(lambda k, r: next(replies))
    t, limiter, _, slept, _ = _make(portal)
    t.call(DS, PATH, {}, want="xml")
    assert limiter.slowdowns == 1 and slept == [3.0] and len(portal.requests) == 2


def test_per_second_limit_that_never_clears_is_throttled() -> None:
    portal = Portal(lambda k, r: (429, "API rate limit exceeded"))
    t, limiter, _, slept, _ = _make(portal, waits=(1.0, 2.0))
    with pytest.raises(DatagoThrottled) as e:
        t.call(DS, PATH, {}, want="xml")
    assert e.value.retryable and e.value.status == 429
    assert limiter.slowdowns == 3 and slept == [1.0, 2.0] and len(portal.requests) == 3


def test_agency_timeout_and_connection_resets_are_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # 예외 문구에 키가 든 주소가 실린다 — 사유에서 지워져야 한다
            raise httpx.ConnectError(f"reset {request.url}", request=request)
        if calls["n"] == 2:
            return httpx.Response(200, content=_gw("05", "SERVICETIME_OUT").encode())
        return httpx.Response(200, content=OK_XML.encode())

    clock = FakeClock(T0)
    slept: list[float] = []
    t = DatagoTransport(
        SecretStr(RAW_ENCODED),
        limiters={DS: RecordingLimiter()},
        budgets={DS: DailyBudget(None, "datago", 100, scope=DS)},
        transport=httpx.MockTransport(handler),
        now=clock,
        sleep=slept.append,
        monotonic=clock.monotonic,
    )
    t.call(DS, PATH, {}, want="xml")
    assert slept == [3.0, 6.0] and calls["n"] == 3


def test_transport_errors_that_persist_raise_without_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection reset by peer {request.url}", request=request)

    t = DatagoTransport(
        SecretStr(RAW_ENCODED),
        limiters={DS: RecordingLimiter()},
        budgets={DS: DailyBudget(None, "datago", 100, scope=DS)},
        transport=httpx.MockTransport(handler),
        retry_waits_s=(0.0,),
        sleep=lambda _: None,
    )
    with pytest.raises(DatagoError) as e:
        t.call(DS, PATH, {"strtYymm": "202608"}, want="xml")
    assert "ConnectError" in str(e.value)
    _no_key(str(e.value))
    assert "serviceKey=***" in str(e.value)


@pytest.mark.parametrize(
    ("status", "code", "name", "exc"),
    [
        (403, "20", "SERVICE_ACCESS_DENIED_ERROR", DatagoAccessDenied),
        (200, "12", "NO_OPENAPI_SERVICE_ERROR", DatagoGone),
        (200, "11", "NO_MANDATORY_REQUEST_PARAMETERS_ERROR", DatagoError),
    ],
)
def test_gateway_errors_carry_the_reason_not_just_the_status(
    status: int, code: str, name: str, exc: type[DatagoError]
) -> None:
    portal = Portal(lambda k, r: (status, _gw(code, name)))
    t, *_ = _make(portal)
    with pytest.raises(exc) as e:
        t.call(DS, PATH, {}, want="xml")
    assert name in str(e.value) and f"GW {code}" in str(e.value)
    assert e.value.gw is not None and e.value.gw.code == code
    assert len(portal.requests) == 1  # 다른 키 형태·재시도 없음


def test_server_errors_without_envelope_are_retried_then_fail() -> None:
    portal = Portal(lambda k, r: (502, "Bad Gateway"))
    t, *_ = _make(portal, waits=(0.5,))
    with pytest.raises(DatagoError) as e:
        t.call(DS, PATH, {}, want="xml")
    assert e.value.retryable and "HTTP 502" in str(e.value) and len(portal.requests) == 2


def test_json_responses_parse_and_bad_bodies_are_format_errors() -> None:
    body = (
        '{"response":{"header":{"resultCode":"00"},'
        '"body":{"items":{"item":[{"basDt":"20261002"}]},"totalCount":1}}}'
    )
    portal = Portal(lambda k, r: (200, body))
    t, *_ = _make(portal)
    assert t.call(DS, PATH, {"resultType": "json"}, want="json").items() == (
        [{"basDt": "20261002"}],
        1,
    )
    portal.reply = lambda k, r: (200, "점검 중 <잘린")
    with pytest.raises(DatagoFormatError, match="JSON"):
        t.call(DS, PATH, {}, want="json").items()
    with pytest.raises(DatagoFormatError, match="XML"):
        t.call(DS, PATH, {}, want="xml").xml()


def test_unregistered_dataset_and_caller_key_are_refused() -> None:
    portal = Portal(lambda k, r: (200, OK_XML))
    t, *_ = _make(portal)
    with pytest.raises(KeyError, match="15100475"):
        t.call("15100475", PATH, {}, want="xml")
    with pytest.raises(ValueError, match="serviceKey"):
        t.call(DS, PATH, {"serviceKey": "x"}, want="xml")
    with pytest.raises(ValueError, match="path"):
        t.call(DS, "https://evil.example/x", {}, want="xml")
    with pytest.raises(ValueError):
        DatagoTransport(SecretStr("k"), limiters={"not-an-id": RecordingLimiter()}, budgets={})
    assert portal.requests == []


def test_xml_with_entities_is_refused() -> None:
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><response>&b;</response>'
    )
    portal = Portal(lambda k, r: (200, bomb))
    t, *_ = _make(portal)
    with pytest.raises(DatagoFormatError, match="XML"):
        t.call(DS, PATH, {}, want="xml").items()


def test_for_datasets_shares_redis_buckets_and_counters_per_dataset() -> None:
    r = fakeredis.FakeRedis()
    clock = FakeClock(T0)
    portal = Portal(lambda k, req: (200, OK_XML))
    kw: dict[str, Any] = {"transport": httpx.MockTransport(portal.handler), "now": clock}
    a = DatagoTransport.for_datasets(SecretStr(RAW_ENCODED), r, [DS, "15101612"], clock=clock, **kw)
    b = DatagoTransport.for_datasets(SecretStr(RAW_ENCODED), r, [DS], clock=clock, **kw)
    a.call(DS, PATH, {}, want="xml")
    b.call(DS, PATH, {}, want="xml")
    assert a.datasets() == ("15101609", "15101612")
    # 두 '프로세스'가 같은 데이터셋 카운터를 이어 센다 — budget:datago:<ID>:<KST 날짜>
    assert r.get("budget:datago:15101609:20261007") == b"2"
    assert r.get("budget:datago:15101612:20261007") is None
    digest = hashlib.sha256(DS.encode()).hexdigest()[:16]
    assert r.exists(f"rl:datago:{digest}") == 1


def test_agency_error_message_never_carries_a_key() -> None:
    """기관이 오류 문구에 요청 URL·키를 되돌려 보내도 예외 문구에는 남지 않는다(절대 규칙 5)."""
    from kbj.data.datago import DatagoAgencyError

    fake_key = "Zm9vYmFyMTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3eHl6QUJDREVGR0g%3D%3D"
    url = f"https://apis.data.go.kr/x?serviceKey={fake_key}&pageNo=1"
    msg = f"SERVICE ERROR url={url} key {fake_key}"
    e = DatagoAgencyError("15100475", "99", msg)
    assert fake_key not in str(e)
    assert fake_key not in e.agency_message
    assert "SERVICE ERROR" in e.agency_message
    assert e.code == "99"


@pytest.mark.parametrize("want", ["xml", "json"])
def test_agency_error_echoing_a_short_key_is_scrubbed_by_the_transport(want: str) -> None:
    """긴 토큰 가리기로 못 잡는 짧은 키도, 전송층이 아는 키라서 기관 오류 문구에서 지운다."""
    short = "shortKey12345"
    xml = (
        "<response><header><resultCode>99</resultCode>"
        f"<resultMsg>bad {short} end</resultMsg></header></response>"
    )
    js = f'{{"response": {{"header": {{"resultCode": "10", "resultMsg": "bad {short}"}}}}}}'
    portal = Portal(lambda k, req: (200, xml if want == "xml" else js))
    t, _, _, _, _ = _make(portal, raw=short)
    resp = t.call(DS, PATH, {}, want="xml" if want == "xml" else "json")
    with pytest.raises(DatagoAgencyError) as e:
        resp.items()
    assert short not in str(e.value)
    assert short not in e.value.agency_message
    assert e.value.code in ("99", "10")
    assert "bad" in e.value.agency_message
    assert short not in repr(resp)

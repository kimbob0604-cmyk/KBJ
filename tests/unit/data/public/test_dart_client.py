"""DART 클라이언트(kbj.data.public.dart.client) — 캐시 키에 키 없음·013·020 예산 닫기·재시도·스로틀.

새로 쓴 시험(설계 §1.4 — dart-report `tests_smoke.py` 는 리포트 모듈 시험이라 P4). 응답은 OpenDART
개발 가이드 모양의 합성 JSON·zip 이다. 키는 가짜 값이다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.data.budget import BudgetExhausted, DailyBudget
from kbj.data.public.dart.client import (
    DartClient,
    DartError,
    DartKeyMissing,
    DartQuotaExceeded,
    decode_status,
    describe_status,
)
from kbj.data.public.dart.disclosures import company, list_disclosures, stock_actions
from kbj.data.ratelimit import Priority
from tests.fakes.clock import FakeClock
from tests.unit.data.public._support import T0, FakeServer, RecordingLimiter, corp_zip

KEY = "0123456789abcdef0123456789abcdef01234567"  # 40자 가짜 키
OTHER_KEY = "fedcba9876543210fedcba9876543210fedcba98"
STATUS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?><result><status>{code}</status>'
    "<message>{msg}</message></result>"
)


def _make(
    server: FakeServer,
    *,
    key: str = KEY,
    budget: DailyBudget | None = None,
    cache_dir: Path | None = None,
    retries: int = 3,
) -> tuple[DartClient, RecordingLimiter, list[float], FakeClock]:
    clock = FakeClock(T0)
    limiter = RecordingLimiter()
    slept: list[float] = []
    c = DartClient(
        SecretStr(key),
        cache_dir=cache_dir,
        limiter=limiter,
        budget=budget,
        transport=server.transport(),
        retries=retries,
        now=clock,
        sleep=slept.append,
        monotonic=clock.monotonic,
    )
    return c, limiter, slept, clock


def _json(obj: Any) -> tuple[int, str]:
    return 200, json.dumps(obj, ensure_ascii=False)


def test_get_json_adds_key_and_throttles_every_call() -> None:
    server = FakeServer(
        lambda r: _json({"status": "000", "corp_name": "가상전자", "induty_code": "264"})
    )
    c, limiter, _, _ = _make(server)
    out = c.get_json("company.json", {"corp_code": "00000001"})
    assert out["corp_name"] == "가상전자"
    assert server.requests[0].url.path == "/api/company.json"
    p = server.params()
    assert p["crtfc_key"] == KEY and p["corp_code"] == "00000001"
    assert limiter.acquired == [(Priority.P3, "dart:company.json")]
    with pytest.raises(ValueError, match="crtfc_key"):
        c.get_json("company.json", {"crtfc_key": "x"})


def test_cache_key_has_no_api_key(tmp_path: Path) -> None:
    server = FakeServer(lambda r: _json({"status": "000", "list": [{"account_nm": "매출액"}]}))
    a, _, _, _ = _make(server, key=KEY, cache_dir=tmp_path)
    b, _, _, _ = _make(server, key=OTHER_KEY, cache_dir=tmp_path)
    params = {"corp_code": "00000001", "bsns_year": "2025", "reprt_code": "11011", "fs_div": "CFS"}
    pa = a._cache_path("fnlttSinglAcntAll.json", {**params, "crtfc_key": KEY}, "json")  # pyright: ignore[reportPrivateUsage]
    pb = b._cache_path("fnlttSinglAcntAll.json", params, "json")  # pyright: ignore[reportPrivateUsage]
    assert pa == pb and pa is not None
    assert KEY not in pa.name
    a.financials("00000001", 2025, "11011")
    b.financials("00000001", 2025, "11011")  # 다른 키여도 같은 캐시를 읽는다 — 두 번째는 HTTP 없음
    assert len(server.requests) == 1
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert KEY not in text and OTHER_KEY not in text and "crtfc_key" not in text


def test_list_json_is_never_cached(tmp_path: Path) -> None:
    server = FakeServer(lambda r: _json({"status": "000", "list": [], "total_page": 1}))
    c, _, _, _ = _make(server, cache_dir=tmp_path)
    c.disclosures("00000001", "20261006", "20261006")
    c.disclosures("00000001", "20261006", "20261006")
    assert len(server.requests) == 2 and list(tmp_path.iterdir()) == []


def test_013_is_an_empty_list_and_other_status_raises() -> None:
    server = FakeServer(lambda r: _json({"status": "013", "message": "조회된 데이타가 없습니다."}))
    c, _, _, _ = _make(server)
    assert c.get_json("list.json", {"corp_code": "00000001"})["list"] == []
    server.reply = lambda r: _json({"status": "010", "message": "등록되지 않은 키입니다."})
    with pytest.raises(DartError) as e:
        c.get_json("list.json", {"corp_code": "00000001"})
    assert e.value.code == "010" and e.value.critical
    assert "status=010 등록되지 않은 인증키" in str(e.value)
    server.reply = lambda r: _json({"status": "800", "message": "점검"})
    with pytest.raises(DartError) as e:
        c.get_json("list.json", {})
    assert not e.value.critical and "시스템 점검" in str(e.value)


def test_020_closes_the_daily_budget() -> None:
    r = fakeredis.FakeRedis()
    budget = DailyBudget(r, "dart", 18000)
    server = FakeServer(
        lambda req: _json({"status": "020", "message": "요청 제한을 초과하였습니다."})
    )
    c, _, _, clock = _make(server, budget=budget)
    with pytest.raises(DartQuotaExceeded) as e:
        c.get_json("list.json", {})
    assert "020" in str(e.value)
    reason = budget.closed_reason(clock.now())
    assert reason is not None and "020" in reason
    assert r.get("budget:dart:20261007") == b"1"
    with pytest.raises(BudgetExhausted):  # 그날은 부르지 않는다
        c.get_json("company.json", {"corp_code": "00000001"})
    assert len(server.requests) == 1


def test_budget_counts_failed_calls_and_stops_at_the_cap() -> None:
    budget = DailyBudget(fakeredis.FakeRedis(), "dart", 2)
    server = FakeServer(lambda r: (503, "busy"))
    c, _, slept, clock = _make(server, budget=budget, retries=3)
    with pytest.raises(BudgetExhausted):
        c.get_json("list.json", {})
    assert len(server.requests) == 2 and budget.used(clock.now()) == 2
    assert slept == [0.8, 1.6]


def test_server_errors_are_retried_with_backoff() -> None:
    replies = iter([(502, "bad gateway"), (500, "oops"), _json({"status": "000", "list": []})])
    server = FakeServer(lambda r: next(replies))
    c, limiter, slept, _ = _make(server)
    assert c.get_json("list.json", {}, retries=3)["list"] == []
    assert slept == [0.8, 1.6] and len(limiter.acquired) == 3


def test_client_errors_are_not_retried_and_carry_the_reason() -> None:
    server = FakeServer(
        lambda r: (404, "<result><status>101</status><message>부적절한 접근</message></result>")
    )
    c, _, slept, _ = _make(server)
    with pytest.raises(DartError) as e:
        c.get_json("list.json", {})
    assert e.value.status == 404 and "status=101" in str(e.value) and slept == []


def test_transport_error_messages_hide_the_key() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    c = DartClient(
        SecretStr(KEY),
        limiter=RecordingLimiter(),
        transport=httpx.MockTransport(boom),
        sleep=lambda _: None,
    )
    with pytest.raises(DartError) as e:
        c.get_json("list.json", {"corp_code": "00000001"}, retries=2)
    assert "ConnectError" in str(e.value) and "2회 실패" in str(e.value)
    assert KEY not in str(e.value) and "crtfc_key=***" in str(e.value)
    assert KEY not in repr(c)


def test_zip_endpoints_corp_codes_and_013_none() -> None:
    zip_bytes = corp_zip(
        [
            ("00126380", "가상전자", "029460", "20260901"),
            ("00999999", "비상장회사", "", "20250101"),
        ]
    )

    def reply(req: httpx.Request) -> tuple[int, bytes | str]:
        if req.url.path.endswith("corpCode.xml"):
            return 200, zip_bytes
        return 200, STATUS_XML.format(code="013", msg="조회된 데이타가 없습니다.")

    server = FakeServer(reply)
    c, _, _, _ = _make(server)
    assert c.corp_map() == {"029460": "00126380"}
    assert c.corp_code("29460") == ("00126380", "가상전자")
    assert len(server.requests) == 1  # 한 번 받아 들고 있는다
    with pytest.raises(DartError, match="찾지 못했다"):
        c.corp_code("000001")
    assert c.get_zip("document.xml", {"rcept_no": "20260921000123"}) is None
    assert c.document_texts("20260921000123") == []


def test_non_zip_error_bodies_raise_with_status() -> None:
    server = FakeServer(
        lambda r: (200, STATUS_XML.format(code="014", msg="파일이 존재하지 않습니다."))
    )
    c, _, _, _ = _make(server)
    with pytest.raises(DartError) as e:
        c.get_raw("document.xml", {"rcept_no": "1"})
    assert e.value.code == "014" and "파일이 존재하지 않음" in str(e.value)
    server.reply = lambda r: (200, "<html>maintenance</html>")
    with pytest.raises(DartError, match="zip"):
        c.get_raw("document.xml", {"rcept_no": "1"})


def test_document_texts_decodes_euc_kr() -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("20260921000123.xml", "주요사항보고서(가상)".encode("euc-kr"))
        zf.writestr("20260921000123_00760.xml", "<BODY>합성</BODY>".encode())
    server = FakeServer(lambda r: (200, buf.getvalue()))
    c, _, _, _ = _make(server)
    texts = dict(c.document_texts("20260921000123"))
    assert texts["20260921000123.xml"] == "주요사항보고서(가상)"
    assert texts["20260921000123_00760.xml"] == "<BODY>합성</BODY>"


def test_disclosure_helpers_on_a_real_client() -> None:
    page = {
        "status": "000",
        "page_no": 1,
        "total_page": 3,
        "total_count": 250,
        "list": [
            {
                "corp_code": "00126380",
                "corp_name": "가상전자",
                "stock_code": "029460",
                "corp_cls": "K",
                "report_nm": "주식분할결정",
                "rcept_no": "20260921000123",
                "flr_nm": "가상전자",
                "rcept_dt": "20260921",
                "rm": "코",
            },
            {
                "corp_code": "00999999",
                "corp_name": "비상장회사",
                "stock_code": " ",
                "corp_cls": "E",
                "report_nm": "기타경영사항",
                "rcept_no": "20260921000999",
                "flr_nm": "",
                "rcept_dt": "20260921",
                "rm": "",
            },
        ],
    }

    def reply(req: httpx.Request) -> tuple[int, bytes | str]:
        if req.url.path.endswith("corpCode.xml"):
            return 200, corp_zip([("00126380", "가상전자", "029460", "20260901")])
        if req.url.path.endswith("company.json"):
            return _json({"status": "000", "corp_name": "가상전자", "induty_code": "264"})
        return _json(page)

    server = FakeServer(reply)
    c, _, _, _ = _make(server)
    got = list_disclosures(c, "2026-09-21")
    p = server.params()
    assert (p["bgn_de"], p["end_de"], p["page_no"], p["page_count"]) == (
        "20260921",
        "20260921",
        "1",
        "100",
    )
    assert "corp_cls" not in p  # 전 시장
    assert (got.total_page, got.total_count, len(got.items)) == (3, 250, 2)
    first = got.items[0]
    assert first.kind == "capital" and first.date == "2026-09-21" and first.source == "DART"
    assert first.link.endswith("rcpNo=20260921000123")
    assert got.items[1].stock_code is None and got.items[1].filer is None
    assert list(got.by_stock()) == ["029460"]
    list_disclosures(c, "20260921", corp_cls="Y")
    assert server.params()["corp_cls"] == "Y"
    acts = stock_actions(c, "029460", "2026-01-01", "2026-09-30")
    assert acts == [
        {
            "title": "주식분할결정",
            "date": "2026-09-21",
            "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260921000123",
        }
    ]
    info = company(c, "029460")
    assert info == {
        "code": "029460",
        "corp_code": "00126380",
        "name": "가상전자",
        "induty_code": "264",
        "source": "dart",
    }
    with pytest.raises(ValueError):
        list_disclosures(c, "2026/09/21")


def test_status_helpers() -> None:
    assert decode_status(STATUS_XML.format(code="020", msg="요청 제한 초과")) == (
        "020",
        "요청 제한 초과",
    )
    assert decode_status("<html/>") is None
    assert describe_status(STATUS_XML.format(code="800", msg="점검")).startswith(
        "status=800 시스템 점검"
    )
    assert describe_status("garbage").startswith("응답을 해석하지 못했다")


def test_key_missing_and_length_warning(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(DartKeyMissing) as e:
        DartClient("  ", limiter=RecordingLimiter())
    assert e.value.critical
    with pytest.raises(DartKeyMissing):
        DartClient.from_settings(Settings(_env_file=None), fakeredis.FakeRedis())  # pyright: ignore[reportCallIssue]
    with caplog.at_level(logging.WARNING, logger="kbj.data.public.dart.client"):
        DartClient("short-fake-key", limiter=RecordingLimiter())
    assert "40자" in caplog.text and "short-fake-key" not in caplog.text
    with pytest.raises(ValueError, match="보고서 코드"):
        DartClient(KEY, limiter=RecordingLimiter()).financials("00000001", 2025, "99999")


def test_from_settings_uses_shared_limiter_and_budget() -> None:
    r = fakeredis.FakeRedis()
    settings = Settings(_env_file=None, dart_api_key=SecretStr(KEY))  # pyright: ignore[reportCallIssue]
    server = FakeServer(lambda req: _json({"status": "000", "list": []}))
    clock = FakeClock(T0)
    c = DartClient.from_settings(settings, r, clock=clock, transport=server.transport(), now=clock)
    c.get_json("list.json", {})
    assert r.get("budget:dart:20261007") == b"1"
    assert r.keys("rl:dart:*") != []

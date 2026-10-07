"""금융위 주식시세(15094808, V2) — 경로·쪽 넘기기·행 격리·구간 거르기(새로 쓴 시험, 합성).

전송층은 묶음 C 의 `DatagoTransport`(가짜 포털 = httpx.MockTransport). 응답 `item` 은 합성
fixture(tests/fixtures/synthetic/krx/fsc_stock_price.json). 키는 가짜 값이다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from pydantic import SecretStr

from kbj.core.quality import Quality
from kbj.data.budget import DailyBudget
from kbj.data.datago import DatagoAgencyError, DatagoTransport
from kbj.data.private.fsc_stock_price.client import (
    DATASET_ID,
    PATH,
    FscFormatError,
    FscStockPriceClient,
)
from kbj.data.ratelimit import Priority

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "krx"
KEY = "FakeDatagoKey+0123456789/abcdefghij=="
D30 = date(2026, 9, 30)
T0 = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)  # 14:00 KST


def items() -> list[dict[str, Any]]:
    return json.loads((FIX / "fsc_stock_price.json").read_text(encoding="utf-8"))["20260930"]


class NullLimiter:
    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        return None

    def on_rate_limited(self) -> float:
        return 1.0


def envelope(rows: list[dict[str, Any]], total: int | None) -> dict[str, Any]:
    body: dict[str, Any] = {"items": {"item": rows}, "numOfRows": 0, "pageNo": 1}
    if total is not None:
        body["totalCount"] = total
    return {
        "response": {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."}, "body": body}
    }


class Portal:
    """가짜 포털 — 요청을 기록하고 `rows` 를 `numOfRows`·`pageNo` 로 잘라 준다."""

    def __init__(self, rows: list[dict[str, Any]], *, total: bool = True) -> None:
        self.rows = rows
        self.total = total
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        n = int(req.url.params["numOfRows"])
        page = int(req.url.params["pageNo"])
        chunk = self.rows[(page - 1) * n : page * n]
        return httpx.Response(200, json=envelope(chunk, len(self.rows) if self.total else None))


def make(
    handler: Callable[[httpx.Request], httpx.Response], **kw: Any
) -> tuple[FscStockPriceClient, DailyBudget]:
    budget = DailyBudget(None, "datago", 100, scope=DATASET_ID)
    t = DatagoTransport(
        SecretStr(KEY),
        limiters={DATASET_ID: NullLimiter()},
        budgets={DATASET_ID: budget},
        transport=httpx.MockTransport(handler),
        retry_waits_s=(),
        now=lambda: T0,
        sleep=lambda _: None,
    )
    return FscStockPriceClient(t, **kw), budget


def test_day_uses_the_v2_path_and_returns_every_stock() -> None:
    portal = Portal(items())
    c, budget = make(portal)
    rows, bad = c.day(D30)
    assert bad == [] and len(rows) == len(items())
    (req,) = portal.requests
    assert req.url.path == PATH == "/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"
    p = req.url.params
    assert (p["basDt"], p["resultType"], p["numOfRows"], p["pageNo"]) == (
        "20260930",
        "json",
        "10000",
        "1",
    )
    assert budget.used(T0) == 1
    assert [r.code for r in rows] == sorted(r.code for r in rows)
    for r in rows:
        assert r.source == "DATAGO:15094808" and r.as_of == D30 and r.quality is Quality.OK
        assert isinstance(r.tr_prc, int) and isinstance(r.mrkt_tot_amt, int)


def test_a_prefix_is_stripped_from_the_short_code() -> None:
    raw = next(x for x in items() if x["srtnCd"].startswith("A"))
    c, _ = make(Portal([raw]))
    (r,), _ = c.day(D30)
    assert r.code == raw["srtnCd"][1:] and r.srtn_cd == raw["srtnCd"]


def test_values_match_the_krx_rows_of_the_same_day() -> None:
    """교차검증의 전제: 합성 금융위 행 = 합성 KRX 행(같은 날)."""
    krx = json.loads((FIX / "stock_daily.json").read_text(encoding="utf-8"))
    by_code = {r["ISU_CD"]: r for m in ("kospi", "kosdaq") for r in krx[m]["20260930"]}
    c, _ = make(Portal(items()))
    rows, _ = c.day(D30)
    for r in rows:
        k = by_code[r.code]
        assert r.clpr == Decimal(k["TDD_CLSPRC"]) and r.tr_prc == int(k["ACC_TRDVAL"])


def test_pages_until_short_page_even_without_total_count() -> None:
    """totalCount 가 없어도 쪽이 꽉 차면 다음 쪽을 부른다(ET fetch_day 교훈)."""
    portal = Portal(items(), total=False)
    c, budget = make(portal, page_size=4)
    rows, _ = c.day(D30)
    assert len(rows) == len(items())
    assert [int(r.url.params["pageNo"]) for r in portal.requests] == [1, 2, 3]
    assert budget.used(T0) == 3


def test_too_many_pages_is_an_error() -> None:
    c, _ = make(Portal(items(), total=False), page_size=2, max_pages=2)
    with pytest.raises(FscFormatError, match="2쪽을 넘었다"):
        c.day(D30)


def test_bad_rows_are_returned_not_dropped() -> None:
    rows = [dict(x) for x in items()[:4]]
    rows[0]["clpr"] = ""  # 종가 없음 → 행은 남고 invalid
    rows[1]["srtnCd"] = "12"  # 코드 형식 틀림 → RowError
    rows[2]["basDt"] = "20260929"  # 다른 날 → RowError
    rows[3]["trPrc"] = "1.5"  # 정수 아님 → RowError
    c, _ = make(Portal(rows))
    ok, bad = c.day(D30)
    assert [r.quality for r in ok] == [Quality.INVALID]
    assert [e.index for e in bad] == [1, 2, 3]
    assert "1.5" not in bad[2].error  # 입력값은 문구에 싣지 않는다


def test_no_row_fits_is_a_format_error_and_empty_day_is_empty() -> None:
    c, _ = make(Portal([{"foo": "bar"}]))
    with pytest.raises(FscFormatError, match="모두 형식 오류"):
        c.day(D30)
    c, _ = make(Portal([]))
    assert c.day(D30) == ([], [])


def test_ohlcv_adds_a_day_to_end_and_filters_code_and_range() -> None:
    base = items()[0]
    code = base["srtnCd"].removeprefix("A")
    rows = [
        base | {"basDt": "20260928"},  # 구간 밖
        base | {"basDt": "20260930"},
        base | {"basDt": "20260929"},
        base | {"basDt": "20261001"},  # '미만' 이 '이하' 로 동작했을 때 끝 다음 날 — 거른다
        items()[1] | {"basDt": "20260929"},  # likeSrtnCd 부분 일치로 온 다른 종목
    ]
    portal = Portal(rows)
    c, _ = make(portal)
    got, bad = c.ohlcv(code, date(2026, 9, 29), D30)
    assert bad == []
    assert [(r.code, r.bas_dt) for r in got] == [(code, date(2026, 9, 29)), (code, D30)]
    p = portal.requests[0].url.params
    assert (p["likeSrtnCd"], p["beginBasDt"], p["endBasDt"]) == (code, "20260929", "20261001")


def test_ohlcv_refuses_bad_arguments_before_calling() -> None:
    portal = Portal([])
    c, _ = make(portal)
    with pytest.raises(ValueError):
        c.ohlcv("12", D30, D30)
    with pytest.raises(ValueError):
        c.ohlcv("990010", D30, date(2026, 9, 1))
    assert portal.requests == []


@pytest.mark.parametrize("echo", [KEY, quote(KEY, safe="")])
def test_agency_error_comes_through_without_the_key(echo: str) -> None:
    """기관 오류(`resultCode` 99)는 전송층 예외 그대로 올라온다. 기관이 키를(그대로·퍼센트 인코딩)
    문구에 되돌려 줘도 이 클라이언트까지 올라온 예외에는 키가 없다(가리기는 전송층 `DatagoResponse
    .items()` 몫 — 이 클라이언트가 다시 감싸며 원문을 되살리지 않는지 본다)."""

    def handler(_: httpx.Request) -> httpx.Response:
        msg = f"기간 초과 serviceKey={echo}"
        return httpx.Response(
            200, json={"response": {"header": {"resultCode": "99", "resultMsg": msg}}}
        )

    c, _ = make(handler)
    with pytest.raises(DatagoAgencyError) as e:
        c.day(D30)
    assert e.value.code == "99" and "기간 초과" in str(e.value)
    for form in (KEY, quote(KEY, safe="")):
        assert form not in str(e.value) and form not in e.value.agency_message


def test_transport_must_carry_the_dataset() -> None:
    t = DatagoTransport(
        SecretStr(KEY),
        limiters={"15101609": NullLimiter()},
        budgets={"15101609": DailyBudget(None, "datago", 1, scope="15101609")},
        transport=httpx.MockTransport(Portal([])),
    )
    with pytest.raises(ValueError, match=DATASET_ID):
        FscStockPriceClient(t)

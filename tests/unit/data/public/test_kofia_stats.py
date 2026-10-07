"""금투협 종합통계 어댑터(kbj.data.public.fsc_kofia_stats) — JSON 봉투·쪽 넘기기·`endBasDt` 미만.

새로 쓴 시험(설계 §1.4). 응답은 공식 문서 모양의 합성 fixture(`tests/fixtures/synthetic/datago/
kofia_*.json`, 고정 시드). 키를 받은 뒤 공개 실데이터로 바꾼다(R21).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from kbj.core.quality import Quality
from kbj.data.public.fsc_kofia_stats.client import (
    DATASET,
    NUMERIC,
    OPERATIONS,
    PRIMARY,
    KofiaFormatError,
    KofiaStatsClient,
)
from tests.unit.data.public._support import FakeServer, Reply, datago_transport, synth


def _client(
    reply: str | Reply, *, page_size: int = 10_000
) -> tuple[KofiaStatsClient, FakeServer, dict[str, Any]]:
    if isinstance(reply, str):
        body = reply
        server = FakeServer(lambda r: (200, body))
    else:
        server = FakeServer(reply)
    t, limiters, budgets = datago_transport(server, [DATASET])
    extra: dict[str, Any] = {"limiters": limiters, "budgets": budgets}
    return KofiaStatsClient(t, page_size=page_size), server, extra


def test_credit_balance_parses_values_and_adds_a_day_to_end() -> None:
    c, server, _ = _client(synth("kofia_credit.json"))
    rows = c.credit_balance(date(2026, 9, 30), date(2026, 10, 2))
    assert server.paths() == [
        f"/1160100/service/GetKofiaStatisticsInfoService/{OPERATIONS['credit']}"
    ]
    p = server.params()
    # endBasDt 는 '미만' — 끝 날(10-02)을 넣으려고 하루 더해 부른다
    assert (p["beginBasDt"], p["endBasDt"]) == ("20260930", "20261003")
    assert (p["resultType"], p["numOfRows"], p["pageNo"]) == ("json", "10000", "1")
    assert [r.bas_dt for r in rows] == [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)]
    r = rows[0]
    assert r.source == "DATAGO:15094809/credit" and r.op == "credit"
    assert set(r.values) == set(NUMERIC["credit"])
    assert all(isinstance(v, Decimal) for v in r.values.values())
    raw = json.loads(synth("kofia_credit.json"))["response"]["body"]["items"]["item"][0]
    assert r.values["crdTrFingWhl"] == Decimal(raw["crdTrFingWhl"])
    # 절대 규칙 1 — 기준일과 품질(대표 값이 있으면 ok, 비면 invalid)
    assert (r.as_of, r.quality) == (date(2026, 9, 30), Quality.OK)
    blank = r.model_copy(update={"values": {**r.values, PRIMARY["credit"]: None}})
    assert blank.quality is Quality.INVALID


def test_rows_outside_the_requested_range_are_dropped() -> None:
    """`endBasDt` 가 '이하'로 동작해 하루 더 온 행이 있어도 요청 구간만 돌려준다."""
    c, _, _ = _client(synth("kofia_capital.json"))
    rows = c.market_capital(date(2026, 9, 30), date(2026, 10, 1))
    assert [r.bas_dt for r in rows] == [date(2026, 9, 30), date(2026, 10, 1)]
    assert rows[0].values["ucolMnyVsOppsTrdRlImpt"] is not None
    with pytest.raises(ValueError, match="늦다"):
        c.market_capital(date(2026, 10, 2), date(2026, 10, 1))


def test_pages_until_short_page_or_total() -> None:
    items = json.loads(synth("kofia_cma.json"))["response"]["body"]["items"]["item"]

    def reply(req: httpx.Request) -> tuple[int, str]:
        page = int(dict(req.url.params)["pageNo"])
        chunk = items[(page - 1) * 3 : page * 3]
        body = {
            "response": {
                "header": {"resultCode": "00"},
                "body": {"totalCount": len(items), "items": {"item": chunk}},
            }
        }
        return 200, json.dumps(body, ensure_ascii=False)

    c, server, extra = _client(reply, page_size=3)
    rows = c.cma(date(2026, 9, 30), date(2026, 10, 1))
    assert len(rows) == len(items) == 4
    assert [server.params(i)["pageNo"] for i in range(len(server.requests))] == ["1", "2"]
    assert {r.labels["mngInvTgt"] for r in rows} == {"MMF", "RP"}
    assert len(extra["limiters"][DATASET].acquired) == 2  # 쪽마다 리미터·예산


def test_fund_nav_sends_required_params() -> None:
    c, server, _ = _client(synth("kofia_fund.json"))
    rows = c.fund_nav(date(2026, 10, 2), "주식형", "공모")
    p = server.params()
    assert (p["basDt"], p["ctg"], p["tstMthdCtg"]) == ("20261002", "주식형", "공모")
    assert len(rows) == 1 and rows[0].labels == {"ctg": "주식형", "tstMthdCtg": "공모"}
    with pytest.raises(ValueError):
        c.fund_nav(date(2026, 10, 2), " ", "공모")


def test_empty_items_string_is_no_rows() -> None:
    empty = '{"response":{"header":{"resultCode":"00"},"body":{"totalCount":0,"items":""}}}'
    c, _, _ = _client(empty)
    assert c.credit_balance(date(2026, 10, 5), date(2026, 10, 5)) == []


def test_missing_fields_and_bad_values_fail() -> None:
    js = json.loads(synth("kofia_credit.json"))
    del js["response"]["body"]["items"]["item"][0]["sbscCapLn"]
    c, _, _ = _client(json.dumps(js))
    with pytest.raises(KofiaFormatError, match="sbscCapLn"):
        c.credit_balance(date(2026, 9, 30), date(2026, 10, 2))
    js = json.loads(synth("kofia_credit.json"))
    js["response"]["body"]["items"]["item"][0]["crdTrFingWhl"] = "약 33조"
    c, _, _ = _client(json.dumps(js, ensure_ascii=False))
    with pytest.raises(KofiaFormatError, match="숫자"):
        c.credit_balance(date(2026, 9, 30), date(2026, 10, 2))
    js = json.loads(synth("kofia_credit.json"))
    js["response"]["body"]["items"]["item"][0]["basDt"] = "2026-09-30"
    c, _, _ = _client(json.dumps(js))
    with pytest.raises(KofiaFormatError, match="basDt"):
        c.credit_balance(date(2026, 9, 30), date(2026, 10, 2))


def test_client_needs_the_dataset_on_the_transport() -> None:
    t, _, _ = datago_transport(FakeServer(lambda r: (200, "")), ["15101609"])
    with pytest.raises(ValueError, match=DATASET):
        KofiaStatsClient(t)

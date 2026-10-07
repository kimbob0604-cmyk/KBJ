"""가짜 공공데이터포털(`httpx.MockTransport`) — 금투협 종합통계·금융위 시세·관세청. 합성만.

응답 본문은 묶음 C·D 의 합성 fixture(tests/fixtures/synthetic/datago/*·krx/fsc_*.json —
고정 시드 생성기)를 틀로 쓴다. 날짜(`basDt`)는 요청 파라미터의 날짜로 바꿔 준다 — 어댑터의
기간 거르기를 통과하게.

- 경로 끝(오퍼레이션)으로 고른다: 금투협 4종(`getGrantingOfCreditBalanceInfo` …), 금융위
  주식 V2(`getStockPriceInfo_V2`)·지수(`getStockMarketIndex`), 관세청(XML fixture 그대로).
- `inject(code, count, *, path_part=None)`: 게이트웨이 오류 XML 봉투(`22` 일 한도·`23` 초당·
  `30` 키 …).
- 기록 `seen`(시각·consumer·경로·파라미터 — `serviceKey` 는 빼고)·`calls(경로 끝)`.
"""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any, Final

import httpx

ROOT: Final = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"
KEY: Final = "datago-sim-fake-service-key"

KOFIA_OPS: Final[dict[str, str]] = {
    "getGrantingOfCreditBalanceInfo": "kofia_credit.json",
    "getSecuritiesMarketTotalCapitalInfo": "kofia_capital.json",
    "getFundTotalNetEssetInfo": "kofia_fund.json",
    "getCMAStatus": "kofia_cma.json",
}
CUSTOMS_XML: Final[dict[str, str]] = {
    "getItemtradeList": "customs_items.xml",
    "getNationtradeList": "customs_countries.xml",
    "getNitemtradeList": "customs_item_country.xml",
}
GW_NAMES: Final[dict[str, str]] = {
    "22": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
    "23": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR",
    "30": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
    "05": "SERVICETIME_OUT",
    "12": "NO_OPENAPI_SERVICE_ERROR",
}


def gw_error_xml(code: str) -> str:
    """게이트웨이 오류 봉투(OpenAPI_ServiceResponse/cmmMsgHeader)."""
    name = GW_NAMES.get(code, "UNKNOWN_ERROR")
    return (
        "<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
        f"<returnAuthMsg>{name}</returnAuthMsg><returnReasonCode>{code}</returnReasonCode>"
        "</cmmMsgHeader></OpenAPI_ServiceResponse>"
    )


@cache
def _json(rel: str) -> dict[str, Any]:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _envelope(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {
                "numOfRows": 10000,
                "pageNo": 1,
                "totalCount": len(items),
                "items": {"item": items},
            },
        }
    }


def _day(params: dict[str, str]) -> str | None:
    """요청이 가리키는 날(`basDt` 또는 `beginBasDt`)."""
    return params.get("basDt") or params.get("beginBasDt")


def _with_day(items: list[dict[str, Any]], day: str | None) -> list[dict[str, Any]]:
    out = copy.deepcopy(items)
    if day:
        for r in out:
            r["basDt"] = day
    return out


@dataclass(frozen=True)
class DatagoSeen:
    at: datetime
    consumer: str
    path: str
    params: dict[str, str]
    status: int


class FakeDatago:
    def __init__(self, now: Callable[[], datetime], *, key: str = KEY) -> None:
        self._now = now
        self.key = key
        self.seen: list[DatagoSeen] = []
        self._inject: list[list[Any]] = []  # [code, count, 경로에 든 문자열 | None]
        self._lock = threading.Lock()

    def transport(self, consumer: str = "unknown") -> httpx.MockTransport:
        def handle(req: httpx.Request) -> httpx.Response:
            return self.handle(req, consumer)

        return httpx.MockTransport(handle)

    def inject(self, code: str, count: int = 1, *, path_part: str | None = None) -> None:
        with self._lock:
            self._inject.append([code, count, path_part])

    def _take(self, path: str) -> str | None:
        for rule in self._inject:
            if rule[1] > 0 and (rule[2] is None or rule[2] in path):
                rule[1] -= 1
                return str(rule[0])
        return None

    def _body(self, op: str, params: dict[str, str]) -> tuple[int, str, str]:
        """(상태, 본문, content-type)."""
        day = _day(params)
        if op in KOFIA_OPS:
            items = _json(f"datago/{KOFIA_OPS[op]}")["response"]["body"]["items"]["item"]
            return 200, json.dumps(_envelope(_with_day(items, day)), ensure_ascii=False), "json"
        if op == "getStockPriceInfo_V2":
            block = _json("krx/fsc_stock_price.json")
            first = sorted(k for k in block if k.isdigit())[0]
            items = _with_day(block[first], day)
            return 200, json.dumps(_envelope(items), ensure_ascii=False), "json"
        if op == "getStockMarketIndex":
            name = params.get("idxNm", "")
            items = [r for r in _json("krx/fsc_index_price.json")["items"] if r["idxNm"] == name]
            items = _with_day(items[:1], day)
            return 200, json.dumps(_envelope(items), ensure_ascii=False), "json"
        if op in CUSTOMS_XML:
            return 200, (ROOT / "datago" / CUSTOMS_XML[op]).read_text(encoding="utf-8"), "xml"
        return 200, gw_error_xml("12"), "xml"

    def handle(self, req: httpx.Request, consumer: str = "unknown") -> httpx.Response:
        now = self._now()
        path = req.url.path
        params = {k: v for k, v in req.url.params.items() if k != "serviceKey"}
        with self._lock:
            forced = self._take(path)
            if req.url.params.get("serviceKey") != self.key:
                forced = "30"
            if forced is not None:
                self.seen.append(DatagoSeen(now, consumer, path, params, 200))
                return httpx.Response(
                    200, text=gw_error_xml(forced), headers={"content-type": "text/xml"}
                )
            status, body, kind = self._body(path.rsplit("/", 1)[-1], params)
            self.seen.append(DatagoSeen(now, consumer, path, params, status))
            ctype = "application/json" if kind == "json" else "text/xml;charset=UTF-8"
            return httpx.Response(status, text=body, headers={"content-type": ctype})

    def calls(self, path_part: str | None = None) -> list[DatagoSeen]:
        with self._lock:
            return [s for s in self.seen if path_part is None or path_part in s.path]


__all__ = ["KEY", "DatagoSeen", "FakeDatago", "gw_error_xml"]

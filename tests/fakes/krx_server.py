"""가짜 KRX OPEN API — 주식·지수·ETP·파생 일별(`httpx.MockTransport`). 합성 데이터만(U3).

GEXLAB `tests/fakes/krx_server.py`(`FakeKrx` — `publish`·`requested()`·`calls()`·`stale`·`fail`)의
KBJ 판. GX 판은 파생 두 엔드포인트만 알고 GX 모듈을 import 해 그대로 옮기지 않았다. 행은 묶음 D 의
합성 fixture(tests/fixtures/synthetic/krx/*.json — 고정 시드 생성기 make_synthetic.py)를 틀로 쓰고
`BAS_DD` 만 그날로 바꾼다.

- 공표: `publish(day)` 는 바로, `publish_at(day, when)` 은 가짜 시계가 when 에 이르면 그날 행을 낸다
  (KRX 는 D+1 08:00 공표 — conflict_map E1). 아직 내지 않은 날은 휴장일과 같은 HTTP 200
  `{"OutBlock_1": []}`(GX probe 관측, `stale="empty"`). `stale="previous"` 면 마지막으로 낸 날의 행.
- `fail[엔드포인트] = 상태` 면 그 상태로 거절한다(본문에 받은 인증키를 되풀이 — 가림 시험용).
- 인증키(`AUTH_KEY` 헤더)가 다르면 401 `Unauthorized Key`. 기록은 `seen`(시각·consumer·경로·basDd),
  `requested()` 는 (엔드포인트, basDd) 차례.
- `extra[엔드포인트] = [행…]` 이면 템플릿 뒤에 그 행을 붙인다(`BAS_DD` 는 그날로). P3 시뮬레이션
  (묶음 S)이 가짜 KIS 종목(`tests.fakes.kis_server.SYMBOLS`)을 KRX 확정 원장에도 두려고 쓴다 —
  실제 KRX 일별은 KIS 가 주는 종목을 모두 덮으므로 두 가짜의 종목 집합이 같아야 보드 확정
  비율(0.9)이 현실과 같아진다.
"""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any, Final, Literal

import httpx

FIX: Final = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic" / "krx"
KEY: Final = "krx-SIM-fake-key-0123456789"
API_PREFIX: Final = "/svc/apis"

ENDPOINTS: Final[tuple[str, ...]] = (
    "/sto/stk_bydd_trd",
    "/sto/ksq_bydd_trd",
    "/sto/knx_bydd_trd",
    "/sto/stk_isu_base_info",
    "/sto/ksq_isu_base_info",
    "/idx/kospi_dd_trd",
    "/idx/kosdaq_dd_trd",
    "/idx/krx_dd_trd",
    "/etp/etf_bydd_trd",
    "/etp/etn_bydd_trd",
    "/drv/fut_bydd_trd",
    "/drv/opt_bydd_trd",
)


def _first_day(block: Any) -> list[dict[str, Any]]:
    """`{basDd: [행]}` 이면 첫 날, `{"rows": [...]}`·`{"items": [...]}` 면 그 목록."""
    if isinstance(block, list):
        return [dict(r) for r in block]
    if isinstance(block, dict):
        for k in ("rows", "items"):
            if k in block:
                return [dict(r) for r in block[k]]
        days = sorted(k for k in block if k.isdigit())
        if days:
            return [dict(r) for r in block[days[0]]]
    raise ValueError("합성 fixture 모양을 모른다")


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@cache
def _templates() -> dict[str, list[dict[str, Any]]]:
    stock, base, index = (
        _load("stock_daily.json"),
        _load("base_info.json"),
        _load("index_daily.json"),
    )
    # 코넥스 1종목은 코스닥 첫 행을 틀로 쓰되 **코드·이름을 바꾼다** — 코스닥 코드(998010)를 그대로
    # 두면 같은 (code, date, source) 원장 행을 두 시장이 덮어쓴다(묶음 S — 메인이 본 충돌).
    # 997xxx 는 합성 코스피(9900xx)·코스닥(998xxx)·ETF(995xxx)와 겹치지 않는 대역이다.
    konex = [
        {
            **r,
            "ISU_CD": "997" + str(r.get("ISU_CD", "000"))[-3:],
            "ISU_NM": "가상코넥스",
            "MKT_NM": "KONEX",
        }
        for r in _first_day(stock["kosdaq"])[:1]
    ]
    return {
        "/sto/stk_bydd_trd": _first_day(stock["kospi"]),
        "/sto/ksq_bydd_trd": _first_day(stock["kosdaq"]),
        "/sto/knx_bydd_trd": konex,
        "/sto/stk_isu_base_info": _first_day(base["kospi"]),
        "/sto/ksq_isu_base_info": _first_day(base["kosdaq"]),
        "/idx/kospi_dd_trd": _first_day(index["kospi"]),
        "/idx/kosdaq_dd_trd": _first_day(index["kosdaq"]),
        "/idx/krx_dd_trd": _first_day(index["kospi"]),
        "/etp/etf_bydd_trd": _first_day(_load("etf_daily.json")),
        "/etp/etn_bydd_trd": _first_day(_load("etn_daily.json")),
        "/drv/fut_bydd_trd": _first_day(_load("fut_daily.json")),
        "/drv/opt_bydd_trd": _first_day(_load("opt_daily.json")),
    }


def rows_for(endpoint: str, day: date) -> list[dict[str, Any]]:
    """그 엔드포인트의 합성 행(사본) — `BAS_DD` 를 그날로."""
    rows = copy.deepcopy(_templates()[endpoint])
    for r in rows:
        r["BAS_DD"] = f"{day:%Y%m%d}"
    return rows


@dataclass(frozen=True)
class Seen:
    at: datetime
    consumer: str
    endpoint: str
    bas_dd: str
    status: int
    rows: int


class FakeKrx:
    def __init__(
        self,
        now: Callable[[], datetime],
        *,
        key: str = KEY,
        stale: Literal["empty", "previous"] = "empty",
        extra: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    ) -> None:
        self._now = now
        self.extra: dict[str, list[dict[str, Any]]] = {
            ep: [dict(r) for r in rows] for ep, rows in (extra or {}).items()
        }
        self.key = key
        self.stale = stale
        self.fail: dict[str, int] = {}
        self.seen: list[Seen] = []
        self._at: dict[str, datetime] = {}  # basDd → 공표 시각
        self._last: str | None = None
        self._lock = threading.Lock()

    def publish(self, day: date) -> None:
        self.publish_at(day, datetime.min.replace(tzinfo=self._now().tzinfo))

    def publish_at(self, day: date, when: datetime) -> None:
        """가짜 시계가 when 에 이르면 그 거래일 행을 낸다."""
        if when.tzinfo is None:
            raise ValueError("naive datetime 금지")
        with self._lock:
            self._at[f"{day:%Y%m%d}"] = when

    def _published(self, key: str, now: datetime) -> bool:
        at = self._at.get(key)
        return at is not None and now >= at

    def transport(self, consumer: str = "unknown") -> httpx.MockTransport:
        def handle(req: httpx.Request) -> httpx.Response:
            return self.handle(req, consumer)

        return httpx.MockTransport(handle)

    def handle(self, req: httpx.Request, consumer: str = "unknown") -> httpx.Response:
        now = self._now()
        path = req.url.path
        endpoint = path[len(API_PREFIX) :] if path.startswith(API_PREFIX) else path
        bas_dd = req.url.params.get("basDd", "")
        with self._lock:

            def reply(status: int, body: Any, n: int = 0) -> httpx.Response:
                self.seen.append(Seen(now, consumer, endpoint, bas_dd, status, n))
                if isinstance(body, str):
                    return httpx.Response(status, text=body)
                return httpx.Response(status, json=body)

            if endpoint not in ENDPOINTS:
                return reply(404, "no such api")
            if req.headers.get("AUTH_KEY", "") != self.key:
                return reply(401, {"respMsg": "Unauthorized Key", "respCode": "401"})
            status = self.fail.get(endpoint)
            if status is not None:
                return reply(status, f"rejected AUTH_KEY={req.headers.get('AUTH_KEY', '')}\nmore")
            key = bas_dd
            if not self._published(key, now):
                if self.stale == "previous" and self._last is not None:
                    key = self._last
                else:
                    return reply(200, {"OutBlock_1": []})
            self._last = key
            day = date(int(key[:4]), int(key[4:6]), int(key[6:8]))
            rows = rows_for(endpoint, day)
            rows += [{**r, "BAS_DD": f"{day:%Y%m%d}"} for r in self.extra.get(endpoint, ())]
            return reply(200, {"OutBlock_1": rows}, len(rows))

    # ── 조회(시험) ───────────────────────────────────────────────────────────────────
    def requested(self) -> list[tuple[str, str]]:
        """(엔드포인트, basDd) 차례대로."""
        with self._lock:
            return [(s.endpoint, s.bas_dd) for s in self.seen]

    def calls(self, endpoint: str | None = None) -> int:
        with self._lock:
            return sum(1 for s in self.seen if endpoint in (None, s.endpoint))

    def successes(self) -> list[Seen]:
        with self._lock:
            return [s for s in self.seen if s.status == 200 and s.rows > 0]


__all__ = ["ENDPOINTS", "KEY", "FakeKrx", "Seen", "rows_for"]

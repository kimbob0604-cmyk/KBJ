"""가짜 KRX Open API (파생상품 일별매매정보) — `httpx.MockTransport` 라우터. 네트워크 없음.

- 행은 커밋된 원본 발췌(`tests/fixtures/krx/*.json`, basDd 20260923)를 `BAS_DD` 만 바꿔 낸다 —
  `publish(날짜)` 한 거래일만. 옵션 18행(코스피200 계열 13·코스닥150 5)·선물 3행(미니 선물)
- 아직 내지 않은 날은 KRX 가 휴장일에 주는 HTTP 200 `{"OutBlock_1": []}`(2026-09-28 probe
  09-24·25) — 갱신 전도 이렇게 온다고 본다(`stale="empty"`, 미실측). `stale="previous"` 면
  마지막으로 낸 날의 행을 그대로 준다(갱신 전 KRX 가 전날 것을 주는 경우)
- `fail[경로]` = HTTP 상태(예: 401 인증 오류·500)면 그 경로는 그 상태로 거절한다. 본문에 받은
  인증키를 되풀이해 싣는다 — 오류 문구 가림을 시험하려고
- 받은 요청은 `seen` 에, 경로별 호출 수는 `calls(경로)` 로
- `KrxMemoryStore`: scheduler KRX 적재의 가짜 저장소 — 유니크 키 (거래일, 종목, 세션) DO UPDATE 와
  코스피200 계열만 쓰는 규칙(data/store.py `krx_loadable`)을 흉내낸다. `fail_read`(읽기 전부)·
  `fail_listing`(상장 목록만)·`fail_write`
"""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from data.krx.eod import FUT_DAILY, OPT_DAILY, KrxClient
from data.krx.models import KrxFuturesDaily, KrxOptionDaily
from data.store import KrxKind, StoreError, krx_loadable

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "krx"
FIXTURE_DAY = "20260923"
KEY = "krx-FAKE-secret-key-0123456789"


@cache
def _fixture_rows() -> dict[str, list[dict[str, Any]]]:
    opt = json.loads((FIX / "opt_daily.json").read_text(encoding="utf-8"))[FIXTURE_DAY]
    fut = json.loads((FIX / "fut_daily.json").read_text(encoding="utf-8"))["rows"]
    return {OPT_DAILY: opt, FUT_DAILY: fut}


def fixture_rows(endpoint: str, day: date | None = None) -> list[dict[str, Any]]:
    """원본 발췌 행(사본). day 를 주면 `BAS_DD` 를 그날로 바꾼다."""
    rows = copy.deepcopy(_fixture_rows()[endpoint])
    if day is not None:
        for r in rows:
            r["BAS_DD"] = f"{day:%Y%m%d}"
    return rows


class FakeKrx:
    def __init__(self, *, stale: Literal["empty", "previous"] = "empty") -> None:
        self.stale = stale
        self.days: dict[str, dict[str, list[dict[str, Any]]]] = {}  # basDd → 경로 → 행
        self.fail: dict[str, int] = {}
        self.seen: list[httpx.Request] = []
        self.on_request: Callable[[httpx.Request], None] | None = None  # 부를 때 끼워 넣을 일
        self._last: str | None = None
        self._lock = threading.Lock()

    def publish(
        self,
        day: date,
        *,
        options: list[dict[str, Any]] | None = None,
        futures: list[dict[str, Any]] | None = None,
    ) -> None:
        """그 거래일 행을 낸다(기본은 원본 발췌를 그날로)."""
        key = f"{day:%Y%m%d}"
        self.days[key] = {
            OPT_DAILY: options if options is not None else fixture_rows(OPT_DAILY, day),
            FUT_DAILY: futures if futures is not None else fixture_rows(FUT_DAILY, day),
        }
        self._last = key

    def calls(self, endpoint: str | None = None) -> int:
        with self._lock:
            return sum(1 for r in self.seen if endpoint is None or r.url.path.endswith(endpoint))

    def requested(self) -> list[tuple[str, str]]:
        """(경로 끝, basDd) 차례대로."""
        with self._lock:
            return [
                ("/" + "/".join(r.url.path.split("/")[-2:]), r.url.params.get("basDd", ""))
                for r in self.seen
            ]

    def handle(self, req: httpx.Request) -> httpx.Response:
        with self._lock:
            self.seen.append(req)
        if self.on_request is not None:
            self.on_request(req)
        endpoint = next((e for e in (OPT_DAILY, FUT_DAILY) if req.url.path.endswith(e)), None)
        if endpoint is None:
            return httpx.Response(404, text="no such api")
        status = self.fail.get(endpoint)
        if status is not None:
            key = req.headers.get("AUTH_KEY", "")
            return httpx.Response(status, text=f"rejected AUTH_KEY={key}\nmore")
        day = req.url.params.get("basDd", "")
        got = self.days.get(day)
        if got is None and self.stale == "previous" and self._last is not None:
            got = self.days[self._last]
        rows = got[endpoint] if got is not None else []
        return httpx.Response(200, json={"OutBlock_1": copy.deepcopy(rows)})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self, **kw: Any) -> KrxClient:
        return KrxClient(SecretStr(KEY), transport=self.transport, **kw)


class KrxMemoryStore:
    def __init__(self) -> None:
        self.options: dict[tuple[date, str, str], KrxOptionDaily] = {}
        self.futures: dict[tuple[date, str, str], KrxFuturesDaily] = {}
        self.writes: list[tuple[KrxKind, int, datetime]] = []
        self.reads = 0
        self.fail_read = False
        self.fail_listing = False
        self.fail_write = False
        self._lock = threading.Lock()

    def krx_loaded(self, kind: KrxKind, trade_date: date) -> bool:
        with self._lock:
            self.reads += 1
            if self.fail_read:
                raise StoreError("krx_opt_daily: OperationalError: connection refused")
            table = self.options if kind == "options" else self.futures
            return any(k[0] == trade_date for k in table)

    def write_krx_options(self, rows: Sequence[KrxOptionDaily], *, ts: datetime) -> int:
        return self._write("options", self.options, rows, ts)

    def write_krx_futures(self, rows: Sequence[KrxFuturesDaily], *, ts: datetime) -> int:
        return self._write("futures", self.futures, rows, ts)

    def krx_option_listing(self, trade_date: date) -> list[tuple[str, str, str, Decimal]]:
        with self._lock:
            self.reads += 1
            if self.fail_read or self.fail_listing:
                raise StoreError("krx_opt_daily: OperationalError: connection refused")
            got = {
                (r.family, r.expiry, r.cp, r.strike.quantize(Decimal("0.01")))
                for (d, _, _), r in self.options.items()
                if d == trade_date
            }
            return sorted(got)

    def _write[R: (KrxOptionDaily, KrxFuturesDaily)](
        self,
        kind: KrxKind,
        table: dict[tuple[date, str, str], R],
        rows: Sequence[R],
        ts: datetime,
    ) -> int:
        with self._lock:
            if self.fail_write:
                raise StoreError(f"{kind}: OperationalError: server closed the connection")
            keep = [r for r in rows if krx_loadable(r)]
            for r in keep:
                assert r.session is not None and r.isu_cd is not None
                table[(r.bas_dd, r.isu_cd, r.session)] = r
            self.writes.append((kind, len(keep), ts))
            return len(keep)

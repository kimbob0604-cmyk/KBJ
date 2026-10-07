"""KRX 클라이언트 — 주식·지수·ETP 엔드포인트, 리미터·일 예산, 401·429·5xx 갈래(새로 쓴 시험).

가짜 KRX(httpx.MockTransport)로만 — 네트워크 없음. 응답은 합성(tests/fixtures/synthetic/krx).
GX 에서 옮긴 기본 시험은 `test_krx_client.py`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.data.budget import BudgetExhausted, DailyBudget
from kbj.data.private.krx.client import (
    ENDPOINTS,
    ETF_DAILY,
    KrxClient,
    KrxError,
    KrxKeyError,
    KrxNotSubscribed,
    KrxThrottled,
    dataset_of,
)
from kbj.data.ratelimit import Priority, scoped_key
from kbj.store.redis_keys import budget_key, krx_calls_key

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "krx"
KEY = "krx-SECRET-key-0123456789"
D = date(2026, 9, 30)
T0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)  # 09:00 KST


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class RecordingLimiter:
    """`RateLimiter` 모양 — 허가 요청과 감속 신호를 센다."""

    def __init__(self) -> None:
        self.acquired: list[tuple[Priority, str, float | None]] = []
        self.slowdowns = 0

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        self.acquired.append((priority, tr_id, timeout))

    def on_rate_limited(self) -> float:
        self.slowdowns += 1
        return 1.0


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def client(
    handler: Callable[[httpx.Request], httpx.Response], **kw: Any
) -> tuple[KrxClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    kw.setdefault("now", Clock(T0))
    return KrxClient(SecretStr(KEY), transport=httpx.MockTransport(handle), **kw), seen


def ok(rows: list[dict[str, Any]]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(200, json={"OutBlock_1": rows})


@pytest.mark.parametrize(
    ("call", "path"),
    [
        (lambda c: c.stock_daily("kospi", D), "/svc/apis/sto/stk_bydd_trd"),
        (lambda c: c.stock_daily("kosdaq", D), "/svc/apis/sto/ksq_bydd_trd"),
        (lambda c: c.stock_daily("konex", D), "/svc/apis/sto/knx_bydd_trd"),
        (lambda c: c.stock_base_info("kospi", D), "/svc/apis/sto/stk_isu_base_info"),
        (lambda c: c.stock_base_info("kosdaq", D), "/svc/apis/sto/ksq_isu_base_info"),
        (lambda c: c.index_daily("kospi", D), "/svc/apis/idx/kospi_dd_trd"),
        (lambda c: c.index_daily("kosdaq", D), "/svc/apis/idx/kosdaq_dd_trd"),
        (lambda c: c.index_daily("krx", D), "/svc/apis/idx/krx_dd_trd"),
        (lambda c: c.etf_daily(D), "/svc/apis/etp/etf_bydd_trd"),
        (lambda c: c.etn_daily(D), "/svc/apis/etp/etn_bydd_trd"),
        (lambda c: c.futures_daily(D), "/svc/apis/drv/fut_bydd_trd"),
        (lambda c: c.options_daily(D), "/svc/apis/drv/opt_bydd_trd"),
    ],
)
def test_named_endpoints_hit_their_paths_with_bas_dd(
    call: Callable[[KrxClient], list[dict[str, Any]]], path: str
) -> None:
    c, seen = client(ok([]))
    assert call(c) == []
    (req,) = seen
    assert req.url.path == path
    assert dict(req.url.params) == {"basDd": "20260930"}
    assert req.headers["AUTH_KEY"] == KEY and KEY not in str(req.url)


def test_endpoint_list_matches_the_named_methods() -> None:
    assert len(ENDPOINTS) == len(set(ENDPOINTS)) == 12
    assert dataset_of(ETF_DAILY) == "etp/etf_bydd_trd"


def test_stock_rows_come_back_as_sent() -> None:
    rows = fixture("stock_daily.json")["kospi"]["20260930"]
    c, _ = client(ok(rows))
    assert c.stock_daily("kospi", D) == rows


def test_limiter_and_budget_are_taken_before_each_call() -> None:
    lim = RecordingLimiter()
    budget = DailyBudget(None, "krx", 3, key_fn=krx_calls_key)
    c, seen = client(ok([]), limiter=lim, budget=budget, acquire_timeout=5.0)
    for _ in range(3):
        c.etf_daily(D)
    assert [a[1] for a in lim.acquired] == [ETF_DAILY] * 3
    assert lim.acquired[0] == (Priority.P3, ETF_DAILY, 5.0)
    assert budget.used(T0) == 3
    with pytest.raises(BudgetExhausted):
        c.etf_daily(D)
    assert len(seen) == 3  # 상한이면 부르지 않는다
    assert c.calls == 3


def test_failed_calls_still_count_against_the_budget() -> None:
    budget = DailyBudget(None, "krx", 10, key_fn=krx_calls_key)
    c, _ = client(lambda _: httpx.Response(500, text="oops"), budget=budget)
    with pytest.raises(KrxError) as e:
        c.etf_daily(D)
    assert e.value.retryable and e.value.status == 500
    assert budget.used(T0) == 1


def test_not_subscribed_turns_the_endpoint_off_for_that_kst_day() -> None:
    now = Clock(T0)
    budget = DailyBudget(None, "krx", 10, key_fn=krx_calls_key)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("etf_bydd_trd"):
            return httpx.Response(401, json={"respMsg": "Unauthorized API Call", "respCode": "401"})
        return httpx.Response(200, json={"OutBlock_1": []})

    c, seen = client(handler, budget=budget, now=now)
    with pytest.raises(KrxNotSubscribed) as e:
        c.etf_daily(D)
    assert not e.value.critical and e.value.status == 401 and e.value.endpoint == ETF_DAILY
    with pytest.raises(KrxNotSubscribed, match="부르지 않았다"):
        c.etf_daily(D)
    assert len(seen) == 1 and budget.used(T0) == 1  # 두 번째는 예산도 쓰지 않는다
    assert c.stock_daily("kospi", D) == []  # 다른 엔드포인트는 그대로
    now.at = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)  # 다음 날 00:00 KST
    with pytest.raises(KrxNotSubscribed):
        c.etf_daily(D)
    assert len(seen) == 3  # 다음 날은 다시 불러 본다


def test_other_401_is_a_critical_key_error() -> None:
    c, _ = client(lambda _: httpx.Response(401, text=f"Unauthorized Key {KEY}"))
    with pytest.raises(KrxKeyError) as e:
        c.stock_daily("kospi", D)
    assert e.value.critical and KEY not in str(e.value)


def test_429_slows_the_limiter_and_is_retryable() -> None:
    lim = RecordingLimiter()
    c, _ = client(lambda _: httpx.Response(429, text="Too Many Requests"), limiter=lim)
    with pytest.raises(KrxThrottled) as e:
        c.index_daily("kospi", D)
    assert lim.slowdowns == 1 and e.value.retryable


def test_transport_errors_are_retryable_and_keep_no_key() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timed out AUTH_KEY={KEY}")

    c, _ = client(boom)
    with pytest.raises(KrxError) as e:
        c.etf_daily(D)
    assert e.value.retryable and KEY not in str(e.value)


def test_bad_arguments_are_refused_before_calling() -> None:
    c, seen = client(ok([]))
    with pytest.raises(TypeError):
        c.daily(ETF_DAILY, datetime(2026, 9, 30, tzinfo=UTC))
    with pytest.raises(ValueError):
        c.daily("https://example.invalid/x", D)
    assert seen == []


def test_from_settings_uses_the_shared_krx_bucket_and_daily_counter() -> None:
    r = fakeredis.FakeRedis()
    s = Settings(_env_file=None, krx_api_key=KEY)  # pyright: ignore[reportCallIssue]
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"OutBlock_1": []})

    c = KrxClient.from_settings(s, r, transport=httpx.MockTransport(handle), now=Clock(T0))
    c.stock_daily("kospi", D)
    raw: list[bytes] = r.keys("*")  # pyright: ignore[reportAssignmentType]
    keys = {k.decode() for k in raw}
    assert krx_calls_key(date(2026, 10, 1)) in keys  # KST 날짜(GX 와 같은 키)
    assert scoped_key("krx", KEY) in keys  # rl:krx:<키 해시>
    assert all(KEY not in k for k in keys)
    assert int(r.get(krx_calls_key(date(2026, 10, 1))) or 0) == 1  # pyright: ignore[reportArgumentType]


def test_backfill_counts_against_both_budgets() -> None:
    r = fakeredis.FakeRedis()
    s = Settings(_env_file=None, krx_api_key=KEY)  # pyright: ignore[reportCallIssue]
    c = KrxClient.from_settings(
        s,
        r,
        backfill=True,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"OutBlock_1": []})),
        now=Clock(T0),
    )
    c.stock_daily("kosdaq", D)
    day = date(2026, 10, 1)
    assert int(r.get(krx_calls_key(day)) or 0) == 1  # pyright: ignore[reportArgumentType]
    assert int(r.get(budget_key("krx", "backfill", day)) or 0) == 1  # pyright: ignore[reportArgumentType]


def test_exhausted_backfill_budget_does_not_eat_the_shared_daily_budget() -> None:
    """백필 상한에 걸린 시도는 부르지 않고, 공용 `krx:calls`(krx.daily 몫)도 올리지 않는다."""
    main = DailyBudget(None, "krx", 100, key_fn=krx_calls_key)
    backfill = DailyBudget(None, "krx", 1, scope="backfill")
    c, seen = client(ok([]), budget=main, extra_budgets=[backfill])
    c.stock_daily("kospi", D)
    for _ in range(5):
        with pytest.raises(BudgetExhausted):
            c.stock_daily("kospi", D)
    assert len(seen) == 1 and c.calls == 1
    assert (main.used(T0), backfill.used(T0)) == (1, 1)


def test_from_settings_without_redis_still_limits_and_counts() -> None:
    s = Settings(_env_file=None, krx_api_key=KEY)  # pyright: ignore[reportCallIssue]
    c = KrxClient.from_settings(s, transport=httpx.MockTransport(ok([])), now=Clock(T0))
    assert c.etf_daily(D) == [] and c.calls == 1
    assert KEY not in repr(c)

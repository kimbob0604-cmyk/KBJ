"""하루 호출 예산(kbj.data.budget) — GEXLAB `test_scheduler_krx.py` 의 예산 시험 4개 승격 + 새 시험.

승격한 4개(GX :75·:89·:101·:127)에서 바꾼 것:
- import 경로: `services.scheduler.krx.KrxCallBudget`·`KrxCapReached` →
  `kbj.data.budget.krx_budget`·`BudgetExhausted`(같은 이름으로 받아 시험 본문은 그대로),
  `services.bus.krx_calls_key` → `kbj.store.redis_keys.krx_calls_key`
- 로그 위치·이름: 예산이 어댑터 계층으로 옮겨 와 로거 `services.scheduler.krx` →
  `kbj.data.budget`, 사건 이름 `krx_calls_redis_failed` → `budget_redis_failed`(모든 출처 공용)

GX :115(설정 `KRX_DAILY_CALL_CAP` 기본 200)는 옮기지 않는다 — 상한은 이제 config/limits.yaml
(8,000 — test_limits.py)이다.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import fakeredis
import pytest

from kbj.data.budget import BudgetExhausted as KrxCapReached
from kbj.data.budget import DailyBudget
from kbj.data.budget import krx_budget as KrxCallBudget
from kbj.store.redis_keys import budget_closed_key, budget_key, krx_calls_key

KST = ZoneInfo("Asia/Seoul")
D28 = date(2026, 9, 28)
AT = datetime(2026, 9, 28, 8, 5, tzinfo=KST)


# ── 승격(GX test_scheduler_krx.py) ──


def test_budget_counts_per_kst_day_and_survives_a_restart() -> None:
    r = fakeredis.FakeRedis()
    b = KrxCallBudget(r, cap=5)
    late = datetime(2026, 9, 28, 23, 59, tzinfo=KST)
    assert [b.take(late), b.take(late)] == [1, 2]
    midnight = datetime(2026, 9, 29, 0, 0, tzinfo=KST)  # UTC 로는 아직 09-28 15:00
    assert b.take(midnight) == 1 and b.used(midnight) == 1
    assert r.get(krx_calls_key(D28)) == b"2"
    ttl = r.ttl(krx_calls_key(D28))
    assert isinstance(ttl, int) and 0 < ttl <= 3 * 86400
    again = KrxCallBudget(r, cap=5)  # 재기동 — Redis 셈을 이어 받는다
    assert again.used(late) == 2 and again.take(late) == 3


def test_budget_hard_stops_at_the_cap_without_counting() -> None:
    r = fakeredis.FakeRedis()
    b = KrxCallBudget(r, cap=2)
    b.take(AT)
    b.take(AT)
    with pytest.raises(KrxCapReached, match="2/2"):
        b.take(AT)
    assert b.used(AT) == 2 and r.get(krx_calls_key(D28)) == b"2"
    with pytest.raises(ValueError):
        KrxCallBudget(r, cap=0)


def test_budget_keeps_its_own_count_when_redis_is_down(caplog: pytest.LogCaptureFixture) -> None:
    server = fakeredis.FakeServer()
    b = KrxCallBudget(fakeredis.FakeRedis(server=server), cap=2)
    server.connected = False
    with caplog.at_level(logging.WARNING, logger="kbj.data.budget"):
        assert [b.take(AT), b.take(AT)] == [1, 2]
        with pytest.raises(KrxCapReached):
            b.take(AT)
    failed = [r for r in caplog.records if "budget_redis_failed" in r.getMessage()]
    assert len(failed) == 1  # 한 번만 알린다
    server.connected = True
    assert b.used(AT) == 2  # 이 프로세스 셈이 Redis(0)보다 크다


def test_budget_rejects_naive_datetimes() -> None:
    b = KrxCallBudget(None, cap=1)
    with pytest.raises(ValueError, match="naive"):
        b.take(datetime(2026, 9, 28, 8, 5))  # noqa: DTZ001 — 일부러 naive


# ── 새로 ──


def test_keys_by_source_and_scope() -> None:
    r = fakeredis.FakeRedis()
    dart = DailyBudget(r, "dart", 10)
    datago = DailyBudget(r, "datago", 10, scope="15100475")
    assert dart.key(D28) == budget_key("dart", None, D28) == "budget:dart:20260928"
    assert datago.key(D28) == "budget:datago:15100475:20260928"
    assert datago.name == "datago:15100475"
    dart.take(AT)
    datago.take(AT)
    datago.take(AT)
    assert (dart.used(AT), datago.used(AT)) == (1, 2)  # 범위가 다르면 따로 센다
    assert DailyBudget(r, "datago", 10, scope="15101609").used(AT) == 0
    with pytest.raises(ValueError):
        DailyBudget(r, "DART", 10)  # 출처 이름은 소문자
    with pytest.raises(ValueError):
        DailyBudget(r, "dart", 10, ttl_s=3600)  # 하루보다 짧은 수명이면 그날 셈이 사라진다


def test_krx_budget_shares_the_gx_key_with_legacy() -> None:
    """legacy GX `KrxCallBudget` 가 같은 Redis 에 센 수를 이어 받는다(전환 기간 공유)."""
    r = fakeredis.FakeRedis()
    r.set(krx_calls_key(D28), 7)  # legacy GX 가 센 수
    b = KrxCallBudget(r, cap=8)
    assert b.used(AT) == 7 and b.take(AT) == 8
    with pytest.raises(KrxCapReached, match="8/8"):
        b.take(AT)


def test_kst_midnight_boundary() -> None:
    b = DailyBudget(fakeredis.FakeRedis(), "dart", 1)
    before = datetime(2026, 10, 6, 14, 59, 59, tzinfo=ZoneInfo("UTC"))  # 23:59:59 KST
    after = before + timedelta(seconds=1)  # 10-07 00:00 KST
    assert b.take(before) == 1
    with pytest.raises(KrxCapReached):
        b.take(before)
    assert b.take(after) == 1  # 새 날
    assert b.key(DailyBudget.day_of(after)) == "budget:dart:20261007"


def test_two_instances_share_the_count_and_the_cap() -> None:
    server = fakeredis.FakeServer()
    a = DailyBudget(fakeredis.FakeRedis(server=server), "dart", 3)
    b = DailyBudget(fakeredis.FakeRedis(server=server), "dart", 3)
    assert [a.take(AT), b.take(AT), a.take(AT)] == [1, 2, 3]
    with pytest.raises(KrxCapReached):
        b.take(AT)
    assert a.used(AT) == b.used(AT) == 3 and b.remaining(AT) == 0


def test_concurrent_takes_never_exceed_the_cap() -> None:
    """읽고-비교-올리기가 Lua 한 번 — 스레드 8개(서로 다른 연결)가 몰려도 상한을 넘지 않는다."""
    server = fakeredis.FakeServer()
    cap = 25
    budgets = [DailyBudget(fakeredis.FakeRedis(server=server), "dart", cap) for _ in range(8)]
    got: list[int] = []
    refused: list[int] = []
    lock = threading.Lock()

    def worker(bud: DailyBudget) -> None:
        for _ in range(10):
            try:
                n = bud.take(AT)
            except KrxCapReached:
                with lock:
                    refused.append(1)
            else:
                with lock:
                    got.append(n)

    ts = [threading.Thread(target=worker, args=(x,)) for x in budgets]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(got) == list(range(1, cap + 1)) and len(refused) == 80 - cap


def test_exhaust_closes_the_day_for_every_instance(caplog: pytest.LogCaptureFixture) -> None:
    """공공데이터포털 GW `22`·DART `020` → 그날 닫기. 다른 프로세스도 닫힌 것을 안다. 다음 날은
    열린다."""
    server = fakeredis.FakeServer()
    a = DailyBudget(fakeredis.FakeRedis(server=server), "datago", 100, scope="15100475")
    b = DailyBudget(fakeredis.FakeRedis(server=server), "datago", 100, scope="15100475")
    a.take(AT)
    secret = "serviceKey=fake-service-key-0123"
    with caplog.at_level(logging.WARNING, logger="kbj.data.budget"):
        a.exhaust(AT, f"GW 22 LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR {secret}")
    events = [json.loads(r.getMessage())["event"] for r in caplog.records]
    assert events == ["budget_exhausted"]
    with pytest.raises(KrxCapReached, match="닫힘") as e:
        b.take(AT)
    assert e.value.reason is not None and "LIMITED_NUMBER" in e.value.reason
    assert "fake-service-key" not in str(e.value)  # 사유는 가려서 남긴다
    assert b.closed_reason(AT) is not None and b.remaining(AT) == 0
    assert b.used(AT) == 1  # 닫혀도 센 수는 그대로
    stored = server_value(server, budget_closed_key(a.key(AT.date())))
    assert stored is not None and "fake-service-key" not in stored
    nxt = AT + timedelta(days=1)
    assert b.closed_reason(nxt) is None and b.take(nxt) == 1


def test_exhaust_without_redis_closes_this_process() -> None:
    b = DailyBudget(None, "dart", 10)
    b.exhaust(AT, "020 요청 제한 초과")
    with pytest.raises(KrxCapReached, match="020"):
        b.take(AT)


def test_closed_budget_does_not_count() -> None:
    r = fakeredis.FakeRedis()
    b = DailyBudget(r, "dart", 10)
    b.exhaust(AT, "020")
    with pytest.raises(KrxCapReached):
        b.take(AT)
    assert r.get(b.key(AT.date())) is None and b.used(AT) == 0


def server_value(server: fakeredis.FakeServer, key: str) -> str | None:
    raw = fakeredis.FakeRedis(server=server).get(key)
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)

"""KBJ 에서 더한 engine 시험 (P7 묶음 ENG — 승격 시험 밖, docs/p7_design.md §8 '엔진').

- KBJ 저장소 메모리판(`MemoryGexRepo` — GX 싱크와 같은 메서드 이름, D-P7-8)으로 사이클 한 번:
  같은 합성 체인을 GX 메모리 흉내(`MemoryEngineStore`)로 돌린 것과 레벨·지표·행사가 GEX 가 같다
- 엔진 상수는 config/gex.yaml `engine:` 과 같고, 엔진의 분봉 TR 은 어댑터 상수와 같다(엔진은
  어댑터를 모른다 — 계약 ⑨ — 그래서 값을 두 곳에 적고 여기서 맞춘다)
- `ws_connection_events`(HIRO 리셋 사유 — KBJ 저장소에 더함)는 시작 전 마지막 사건과 창 안
  사건을 준다
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis

from kbj.config.gex import load_gex
from kbj.core.gex.records import ExpiryRecord, HealthEvent
from kbj.data.private.kis.rest import MINUTE_TR
from kbj.engines.gex.futures import FUTURES_TR
from kbj.services.engine import gex as svc_mod
from kbj.services.engine.gex import EngineContext, EnginePublisher, EngineService
from kbj.services.runtime import MemoryHealthSink
from kbj.services.runtime.bus import ChainReady
from kbj.store.repos.gex import WS_GATEWAY_SERVICE, MemoryGexRepo
from tests.unit.engines.gex.engine_inputs import (
    CAL,
    MemoryEngineStore,
    default_chain,
    fut_row,
    seed_master,
    synthetic_series,
)
from tests.unit.engines.gex.test_engine_service import NOV, TUE, Clock, T


def _service(store: Any, clock: Clock) -> EngineService:
    redis = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    seed_master(redis, "\n".join(default_chain().master_lines()) + "\n")
    return EngineService(
        store,
        store,
        EnginePublisher(redis),
        EngineContext(redis, CAL, clock=lambda: clock.mono),
        CAL,
        health=MemoryHealthSink(),
        now=clock.now,
        mono=lambda: clock.mono,
        registry=(),
    )


def test_a_cycle_over_the_kbj_repo_writes_what_the_gx_store_got() -> None:
    chain = synthetic_series("", "202611", NOV, T)
    fut = fut_row(T - timedelta(seconds=5))
    ready = ChainReady(ts=T, trade_date=TUE, session="day", series=(("", "202611"),))

    gx = MemoryEngineStore()
    gx.chain += chain
    gx.futures.append(fut)
    gx.add_expiry("", "202611", NOV)
    gx_result = _service(gx, Clock(T)).on_ready(ready)

    repo = MemoryGexRepo()
    repo.seed_chain(chain)
    repo.seed_futures([fut])
    repo.seed_expiries(
        [
            ExpiryRecord(
                ts=T, trade_date=TUE, session="day", mrkt_cls="", expiry="202611",
                last_trade_date=NOV, source="kis",
            )
        ]
    )  # fmt: skip
    kbj_result = _service(repo, Clock(T)).on_ready(ready)

    assert gx_result is not None and kbj_result is not None
    assert kbj_result.levels == gx_result.levels and kbj_result.metrics == gx_result.metrics
    levels = repo.levels_latest("all")
    assert levels and {(r.name, r.value, r.quality, r.flag) for r in levels} == {
        (r.name, r.value, r.quality, r.flag) for r in gx.levels.values() if r.scope == "all"
    }
    assert sorted(r.gex for r in repo.strike_profile(T)) == sorted(
        r.gex for r in gx.strike_gex.values()
    )
    assert {r.metric for r in repo.metrics_series("net_gex", T, T)} == {"net_gex"}


def test_engine_constants_match_the_config_file_and_the_adapter() -> None:
    cfg = load_gex().engine
    assert svc_mod.CATCHUP_S == cfg.catchup_s
    assert svc_mod.CATCHUP_GRACE.total_seconds() == cfg.grace_s
    assert svc_mod.DAILY_RETRY_S == cfg.daily_retry_s
    assert FUTURES_TR == MINUTE_TR


def test_ws_connection_events_give_the_last_kind_before_and_the_window() -> None:
    repo = MemoryGexRepo()
    t0 = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)

    def ev(at: datetime, kind: str, service: str = WS_GATEWAY_SERVICE) -> HealthEvent:
        return HealthEvent.model_validate(
            {"ts": at, "trade_date": TUE, "session": "day", "service": "poller",
             "kind": kind, "level": "warning", "message": kind}
        ).model_copy(update={"service": service})  # fmt: skip

    repo.write_health(
        [
            ev(t0 - timedelta(minutes=5), "ws_connected"),
            ev(t0 - timedelta(minutes=1), "ws_disconnected"),
            ev(t0 + timedelta(minutes=1), "ws_connect_failed"),
            ev(t0 + timedelta(minutes=2), "ws_connected", service="engine"),  # 다른 서비스
            ev(t0 + timedelta(minutes=3), "kis_token_refreshed"),  # 다른 종류
        ]
    )
    kinds = ("ws_connected", "ws_disconnected", "ws_connect_failed")
    before, inside = repo.ws_connection_events(t0, t0 + timedelta(minutes=10), kinds)
    assert before == "ws_disconnected"
    assert inside == [(t0 + timedelta(minutes=1), "ws_connect_failed")]
    assert repo.ws_connection_events(t0 - timedelta(hours=1), t0 - timedelta(hours=1), kinds) == (
        None,
        [],
    )

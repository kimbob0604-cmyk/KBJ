"""engine 서비스 (services/engine/service.py·publish.py·context.py — docs/phase3_design.md §1·§2).

가짜 입력(메모리 저장소의 체인·선물 행, fakeredis 의 chain.ready·마스터·poller 문맥)으로 한 사이클
→ 표·채널·키 산출을 본다:

- chain.ready 한 건 → levels·metrics·strike_gex·option_iv 저장, engine.levels·engine.metrics 발행
  (visible 만 — shadow 는 저장만), engine:latest(quality·as_of), engine:basis
- 알림을 놓치면 10초 따라잡기(그 세션 max(ts)), 이미 계산한 as_of 는 다시 하지 않는다
- 야간은 야간 행만. S_ref 없음·사이클 실패 → 직전 engine:latest 를 stale 로 + health(10분에 한 번)
- 표 하나의 쓰기 실패·Redis 장애가 나머지를 막지 않는다. 확정 베이시스는 재기동을 넘는다
- 구독 루프·하트비트, 문맥(근월물 — 분기 만기일 15:20 뒤 차월물, 전 행사가, 결제월)
- 일별 지표(POST_DAY 한 번): 마지막 주간 사이클의 가장 가까운 월물 ATM IV(없으면 invalid — 다음
  월물로 넘어가지 않는다) → atm_iv_daily·랭크·퍼센타일·IV − HV,
  KRX 백필은 이력 없는 날(자체 값이 invalid 였던 날 포함)만 한 번, 실패하면 1분 뒤 다시, visible
  이면 engine.metrics 로
"""

from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import fakeredis
import pytest

import services.engine.evaluate as ev_mod
from core.calendar import state_at
from core.features import FeatureWatcher
from services.auth.health import MemoryHealthSink
from services.bus import (
    CHAIN_READY,
    ENGINE_BASIS_KEY,
    ENGINE_LATEST_KEY,
    ENGINE_LEVELS,
    ENGINE_METRICS,
    BasisBook,
    ChainReady,
    EngineLatest,
    EngineLevels,
    EngineMetrics,
    heartbeat_key,
)
from services.chain_feed import context_snapshot
from services.engine.context import EngineContext
from services.engine.daily import DAILY_METRICS, daily_ts, monthly_last_trade, nearest_monthly
from services.engine.extended import REGISTRY
from services.engine.publish import EnginePublisher, mark_stale
from services.engine.records import MetricRecord
from services.engine.registry import CycleView, Flag, MetricPlugin, PluginValue
from services.engine.service import EngineService
from services.poller.context import ChainContext, ExpiryInfo, Series
from services.runtime import Heartbeater
from tests.fakes.engine_inputs import (
    CAL,
    NEAR,
    MemoryEngineStore,
    fut_row,
    krx_option_day,
    krx_settles,
    kst,
    synthetic_series,
)
from tests.fakes.kis_server import default_chain, quarterly_expiry_chain
from tests.unit.test_poller_service import seed_master

TUE = date(2026, 10, 13)
WED = date(2026, 10, 14)
NOV = date(2026, 11, 12)
T = kst(TUE, 10, 0)


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at
        self.mono = 0.0

    def now(self) -> datetime:
        return self.at

    def advance(self, s: float) -> None:
        self.at += timedelta(seconds=s)
        self.mono += s


class Rig:
    def __init__(
        self,
        at: datetime = T,
        *,
        registry: Any = (),
        server: Any = None,
        master: bool = True,
        features: Any = None,
    ) -> None:
        self.clock = Clock(at)
        self.server = server or fakeredis.FakeServer()
        self.redis = fakeredis.FakeRedis(server=self.server)
        if master:  # scheduler 가 둔 마스터 — 근월물 코드·전 행사가
            seed_master(self.redis, "\n".join(default_chain().master_lines()) + "\n")
        self.sub = self.redis.pubsub(ignore_subscribe_messages=True)
        self.sub.subscribe(ENGINE_LEVELS, ENGINE_METRICS)
        self.store = MemoryEngineStore()
        self.store.add_expiry("", "202611", NOV)
        self.health = MemoryHealthSink()
        self.publisher = EnginePublisher(self.redis)
        self.context = EngineContext(self.redis, CAL, clock=lambda: self.clock.mono)
        self.svc = EngineService(
            self.store,
            self.store,
            self.publisher,
            self.context,
            CAL,
            health=self.health,
            now=self.clock.now,
            mono=lambda: self.clock.mono,
            heartbeat=Heartbeater(self.redis, "engine", now=self.clock.now),
            registry=registry,
            features=features,
        )

    def add_cycle(self, at: datetime, **kw: Any) -> ChainReady:
        """at 의 합성 월물 202611 체인 + 근월물 선물 행(5초 앞)을 저장소에 넣고 그 알림."""
        info = state_at(at, CAL)
        assert info.trade_date is not None and info.session is not None
        src = "fill" if info.session == "night" else "board"
        self.store.chain += synthetic_series("", "202611", NOV, at, source=src, **kw)
        self.store.futures.append(fut_row(at - timedelta(seconds=5)))
        return ChainReady(
            ts=at, trade_date=info.trade_date, session=info.session, series=(("", "202611"),)
        )

    def published(self) -> list[Any]:
        out: list[Any] = []
        quiet = 0
        while quiet < 4:  # 구독 확인(채널 둘)도 None 으로 온다
            m = self.sub.get_message(timeout=0.01)
            if m is None:
                quiet += 1
                continue
            quiet = 0
            model = EngineLevels if m["channel"] == ENGINE_LEVELS.encode() else EngineMetrics
            out.append(model.model_validate_json(m["data"]))
        return out

    def latest(self) -> EngineLatest:
        raw = self.redis.get(ENGINE_LATEST_KEY)
        assert raw is not None
        return EngineLatest.model_validate_json(raw)  # type: ignore[arg-type]


# ── 한 사이클 ──


def test_a_chain_ready_runs_one_cycle_that_is_stored_published_and_kept() -> None:
    rig = Rig()
    ready = rig.add_cycle(T)
    result = rig.svc.on_ready(ready)
    assert result is not None and result.status == "ok"
    st = rig.store
    assert st.levels and st.metrics and st.strike_gex and st.option_iv
    rows = [*st.levels.values(), *st.metrics.values(), *st.strike_gex.values()]
    assert {(r.ts, r.trade_date, r.session) for r in rows} == {(T, TUE, "day")}
    assert {(k[1], k[2]) for k in st.levels} >= {("all", "call_wall"), ("0dte", "flip")}
    msgs = rig.published()
    (levels,) = [m for m in msgs if isinstance(m, EngineLevels)]
    (metrics,) = [m for m in msgs if isinstance(m, EngineMetrics)]
    assert levels.as_of == T and levels.quality == result.quality == "ok"
    assert {(x.scope, x.name) for x in levels.levels} == {
        (r.scope, r.name) for r in st.levels.values()
    }
    assert {m.metric for m in metrics.metrics} == {"net_gex", "dex", "atm_iv", "expiry_gamma"}
    latest = rig.latest()
    assert (latest.as_of, latest.stale, latest.near_code) == (T, False, NEAR)
    assert latest.s_ref == 1095.10 and latest.s_ref_quality == "ok"
    assert [(s.label, s.status) for s in latest.series] == [("M:202611", "evaluated")]
    book = BasisBook.model_validate_json(rig.redis.get(ENGINE_BASIS_KEY))  # type: ignore[arg-type]
    assert set(book.entries) == {"M:202611"} and book.near_code == NEAR
    # 같은 알림(또는 더 이른 ts)은 다시 계산하지 않는다
    assert rig.svc.on_ready(ready) is None and rig.svc.stats.cycles == 1


def test_missed_notifications_are_caught_up_every_ten_seconds() -> None:
    """알림이 올 여유(poller 최대 대기 30초 + 5초) 안의 행은 아직 놓친 것이 아니다 — 따라잡기가
    알림을 앞질러 반쪽 사이클을 돌지 않는다. 여유보다 오래된 행이 as_of 뒤에 있으면 놓친 것이라 그
    세션 max(ts) 까지(여유 안의 새 행까지) 한 사이클."""
    rig = Rig()
    rig.add_cycle(T)
    rig.svc.tick()  # 행이 막 왔다 — 알림을 기다린다
    assert rig.svc.stats.catchups == 0 and rig.svc.last_as_of is None
    rig.clock.advance(40)  # T+40 — 여유(35초)가 지났다
    rig.svc.tick()
    assert rig.svc.stats.catchups == 1 and rig.svc.last_as_of == T
    rig.add_cycle(T + timedelta(seconds=30))
    rig.clock.advance(10)  # T+50 — T+30 행은 여유 안
    rig.svc.tick()
    assert rig.svc.stats.catchups == 1
    rig.add_cycle(T + timedelta(seconds=64))  # 여유 안의 새 행
    rig.clock.advance(16)  # T+66 — T+30 행이 여유를 넘었다
    rig.svc.tick()
    assert rig.svc.stats.catchups == 2
    assert rig.svc.last_as_of == T + timedelta(seconds=64)  # 놓친 T+30 과 여유 안 T+64 까지
    rig.clock.advance(10)
    reads = rig.store.reads
    rig.svc.tick()  # 놓친 행이 없다 — 여유 앞 max(ts) 만 읽는다
    assert rig.svc.stats.catchups == 2 and rig.store.reads == reads + 1
    assert rig.redis.get(heartbeat_key("engine")) is not None


def test_a_pending_notification_is_not_pre_empted_by_the_catch_up() -> None:
    rig = Rig()
    ready = rig.add_cycle(T)
    for _ in range(3):  # 30초 동안 10초마다 — 알림이 오는 중
        rig.svc.tick()
        rig.clock.advance(10)
    assert rig.svc.stats.catchups == 0 and not rig.store.levels
    assert rig.svc.on_ready(ready) is not None and rig.svc.last_as_of == T
    rig.clock.advance(60)
    rig.svc.tick()  # 알림이 덮은 행 — 따라잡을 것이 없다
    assert rig.svc.stats.catchups == 0 and rig.svc.stats.cycles == 1


def test_right_after_the_close_the_last_rows_are_still_caught_up() -> None:
    rig = Rig(kst(TUE, 15, 47))  # POST_DAY — 5분 앞(15:42)의 주간 세션을 본다
    rig.add_cycle(kst(TUE, 15, 44, 50))
    rig.svc.tick()
    assert rig.svc.last_as_of == kst(TUE, 15, 44, 50)
    later = Rig(kst(TUE, 16, 30))
    later.add_cycle(kst(TUE, 15, 44, 50))
    later.svc.tick()
    assert later.svc.last_as_of is None  # 한참 지난 장 밖 — 보지 않는다


def test_the_night_cycle_uses_night_rows_and_the_cm_quote() -> None:
    night = kst(TUE, 21, 0)
    rig = Rig(night)
    rig.add_cycle(kst(TUE, 15, 40), oi=9999)  # 주간 전광판 행 — 섞지 않는다
    rig.store.chain += synthetic_series("", "202611", NOV, night, source="fill")
    rig.store.futures.append(fut_row(night - timedelta(seconds=2), "1094.00"))  # CM 단건
    ready = ChainReady(ts=night, trade_date=WED, session="night", series=(("", "202611"),))
    result = rig.svc.on_ready(ready)
    assert result is not None and result.s_ref is not None
    assert result.s_ref.price == Decimal("1094.00") and result.s_ref.quote.market == "CM"
    ivs = [r for r in rig.store.option_iv.values() if r.session == "night"]
    assert ivs and {r.quote_source for r in ivs} == {"fill"} and {r.oi for r in ivs} == {500}
    assert rig.latest().session == "night" and rig.latest().trade_date == WED


# ── stale·실패 격리 ──


def test_no_s_ref_keeps_the_previous_output_marked_stale() -> None:
    rig = Rig()
    rig.svc.on_ready(rig.add_cycle(T))
    before = len(rig.store.levels)
    night = kst(TUE, 21, 0)
    rig.clock.at = night
    rig.store.chain += synthetic_series("", "202611", NOV, night, source="fill")  # CM 시세 없음
    for i in range(2):
        at = night + timedelta(seconds=30 * i)
        ready = ChainReady(ts=at, trade_date=WED, session="night", series=(("", "202611"),))
        result = rig.svc.on_ready(ready)
        assert result is not None and result.status == "no_s_ref"
    latest = rig.latest()
    assert latest.as_of == T and latest.stale and latest.stale_reason == "no_s_ref"
    assert latest.quality == "stale" and latest.stale_at == night
    assert all(x.quality != "ok" for x in [*latest.levels, *latest.metrics])
    # S_ref·시리즈 F·입력 품질도 — 새로 계산하지 못한 값이 'S_ref ok' 로 남지 않는다
    assert latest.s_ref == 1095.10 and latest.s_ref_quality == "stale"
    (s,) = latest.series
    assert (s.forward_quality, s.input_quality) == ("stale", "stale")
    assert len(rig.store.levels) == before  # 새 행 없음
    assert rig.health.kinds() == ["engine_no_s_ref"]  # 두 사이클이어도 한 번
    assert rig.svc.stats.no_s_ref == 2


def test_mark_stale_lowers_every_quality_but_keeps_worse_ones_and_values() -> None:
    rig = Rig()
    rig.svc.on_ready(rig.add_cycle(T))
    base = rig.latest()
    (s,) = base.series
    odd = base.model_copy(
        update={
            "s_ref_quality": "estimated",
            "series": (
                s.model_copy(update={"input_quality": "invalid"}),
                s.model_copy(
                    update={"label": "WKM:261005", "status": "failed", "forward_quality": None}
                ),
            ),
        }
    )
    at = T + timedelta(minutes=1)
    got = mark_stale(odd, "cycle_failed", at)
    assert (got.stale, got.stale_reason, got.stale_at) == (True, "cycle_failed", at)
    assert got.s_ref == base.s_ref and got.s_ref_quality == "estimated"
    a, b = got.series
    assert (a.forward, a.forward_quality, a.input_quality) == (s.forward, "stale", "invalid")
    assert (b.forward_quality, b.input_quality) == (None, "stale")
    assert [x.value for x in got.levels] == [x.value for x in base.levels]
    none = mark_stale(base.model_copy(update={"s_ref": None, "s_ref_quality": None}), "x", at)
    assert none.s_ref_quality is None


def test_a_failed_cycle_marks_stale_and_is_retried_by_the_catch_up() -> None:
    rig = Rig()
    rig.svc.on_ready(rig.add_cycle(T))
    ready = rig.add_cycle(T + timedelta(seconds=30))
    rig.store.fail_reads = True
    assert rig.svc.on_ready(ready) is None
    latest = rig.latest()
    assert latest.stale and latest.stale_reason == "cycle_failed" and latest.as_of == T
    assert latest.s_ref_quality == "stale" and latest.series[0].forward_quality == "stale"
    assert "engine_cycle_failed" in rig.health.kinds() and rig.svc.stats.failures == 1
    rig.store.fail_reads = False
    rig.clock.advance(10)  # 실패한 사이클은 여유를 기다리지 않고 다음 따라잡기에서 다시
    rig.svc.tick()
    assert rig.svc.last_as_of == ready.ts and not rig.latest().stale


def test_a_persistent_cycle_failure_sends_health_once_per_ten_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """설계 §1 — 같은 (종류, 대상) health 는 10분에 한 번. 사이클 실패의 대상은 세션·예외 종류라
    as_of 가 바뀌어도 알림마다(≈30초) 새 health 를 내지 않는다(로그는 매번). 같은 종류라도 예외가
    다르면 따로."""
    import services.engine.service as svc_mod

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("evaluate bug")

    monkeypatch.setattr(svc_mod, "evaluate_cycle", boom)
    rig = Rig()
    at = T
    for _ in range(10):  # 5분 — 30초 알림 + 10초 따라잡기
        rig.svc.on_ready(rig.add_cycle(at))
        for _ in range(3):
            rig.clock.advance(10)
            rig.svc.tick()
        at += timedelta(seconds=30)
    assert rig.svc.stats.failures > 10
    assert len(rig.health.of("engine_cycle_failed")) == 1
    rig.store.fail_reads = True  # 다른 예외(입력 읽기 실패) — 따로 한 번
    rig.svc.on_ready(rig.add_cycle(at))
    assert len(rig.health.of("engine_cycle_failed")) == 2
    rig.store.fail_reads = False
    for _ in range(60):  # 10분 뒤 — 같은 실패가 이어지면 다시 한 번
        rig.clock.advance(10)
        rig.svc.tick()
    assert len(rig.health.of("engine_cycle_failed")) == 3
    # 묶음 기록은 10분 지난 것을 버린다 — 실패 사이클마다 늘지 않는다
    assert len(rig.svc._emitted) <= 2


def test_one_failing_table_write_does_not_stop_the_rest() -> None:
    rig = Rig()
    rig.store.fail_writes = {"strike_gex"}
    rig.svc.on_ready(rig.add_cycle(T))
    assert rig.store.levels and rig.store.metrics and rig.store.option_iv
    assert not rig.store.strike_gex
    assert rig.health.kinds() == ["engine_write_failed"] and rig.svc.stats.write_failures == 1
    assert rig.latest().as_of == T  # 발행도 했다


def test_a_redis_outage_does_not_stop_computing_and_storing() -> None:
    rig = Rig()
    ready = rig.add_cycle(T)
    rig.server.connected = False
    result = rig.svc.on_ready(ready)
    assert result is not None and result.status == "ok"
    assert rig.store.levels and rig.publisher.stats.failed >= 2  # 발행·최신·베이시스
    rig.server.connected = True
    assert rig.redis.get(ENGINE_LATEST_KEY) is None


def test_shadow_metrics_are_stored_but_not_published() -> None:
    def vex(view: CycleView) -> list[PluginValue]:
        return [PluginValue("all", 2.0, "ok")]

    rig = Rig(registry=[MetricPlugin("vex", "vex", vex)])
    rig.svc.on_ready(rig.add_cycle(T))
    (row,) = [m for m in rig.store.metrics.values() if m.metric == "vex"]
    assert row.flag == "shadow" and row.value == 2.0
    msgs = [p for p in rig.published() if isinstance(p, EngineMetrics)]
    published = [m.metric for p in msgs for m in p.metrics]
    assert "vex" not in published and "net_gex" in published
    assert "vex" not in {m.metric for m in rig.latest().metrics}


def test_shadow_levels_and_core_metrics_are_stored_but_not_published() -> None:
    """Phase 2 핵심을 shadow 로 내리면 계산·저장(flag = shadow)은 하고 engine.levels·engine.metrics·
    engine:latest 에서 빠진다 — 나머지 레벨·지표는 그대로 나간다."""
    rig = Rig()
    flags: dict[str, Flag] = {"flip": "shadow", "net_gex": "shadow"}
    rig.svc.flags = flags
    rig.svc.on_ready(rig.add_cycle(T))
    stored = {(k[1], k[2]): r.flag for k, r in rig.store.levels.items()}
    assert stored[("all", "flip")] == "shadow" and stored[("all", "call_wall")] == "visible"
    assert {m.flag for m in rig.store.metrics.values() if m.metric == "net_gex"} == {"shadow"}
    msgs = rig.published()
    (levels,) = [m for m in msgs if isinstance(m, EngineLevels)]
    (metrics,) = [m for m in msgs if isinstance(m, EngineMetrics)]
    assert "flip" not in {x.name for x in levels.levels}
    assert "call_wall" in {x.name for x in levels.levels}
    assert {m.metric for m in metrics.metrics} == {"dex", "atm_iv", "expiry_gamma"}
    latest = rig.latest()
    assert "flip" not in {x.name for x in latest.levels}
    assert "net_gex" not in {m.metric for m in latest.metrics}
    assert latest.quality == "ok"  # 사이클 품질(범위 all 순GEX)은 그대로 낸다


def _flag_file(path: Path, text: str, mtime_ns: int) -> Path:
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def test_the_flag_file_is_reread_every_thirty_seconds(tmp_path: Path) -> None:
    """기동 때 읽은 플래그로 돌고, 30초마다 파일이 바뀌었으면 다시 읽는다 — 다음 사이클부터 새
    플래그(shadow → visible 이면 그때부터 발행)."""
    clock = Clock(T)
    p = _flag_file(tmp_path / "features.yaml", "metrics:\n  vex: shadow\n", 1_000_000_000)
    watcher = FeatureWatcher(p, mono=lambda: clock.mono)

    def vex(view: CycleView) -> list[PluginValue]:
        return [PluginValue("all", 2.0, "ok")]

    rig = Rig(registry=[MetricPlugin("vex", "vex", vex)], features=watcher)
    rig.clock = clock  # 감시자와 같은 단조 시계
    rig.svc._mono = lambda: clock.mono  # pyright: ignore[reportPrivateUsage]
    assert rig.svc.flags is not None and rig.svc.flags["vex"] == "shadow"
    assert rig.svc.flags["net_gex"] == "visible"  # 적지 않은 이름은 카탈로그 기본값
    _flag_file(p, "metrics:\n  vex: visible\n", 2_000_000_000)
    clock.advance(10)
    rig.svc.tick()
    assert rig.svc.flags["vex"] == "shadow"  # 아직 30초 전
    clock.advance(20)
    rig.svc.tick()
    assert rig.svc.flags["vex"] == "visible"
    rig.svc.on_ready(rig.add_cycle(clock.at))
    (row,) = [m for m in rig.store.metrics.values() if m.metric == "vex"]
    assert row.flag == "visible"
    msgs = [p for p in rig.published() if isinstance(p, EngineMetrics)]
    assert "vex" in [m.metric for p in msgs for m in p.metrics]
    assert not [e for e in rig.health.events if e.kind == "engine_flags_invalid"]


def test_a_bad_flag_file_keeps_the_flags_and_sends_health_once(tmp_path: Path) -> None:
    clock = Clock(T)
    p = _flag_file(tmp_path / "features.yaml", "metrics:\n  flip: shadow\n", 1_000_000_000)
    watcher = FeatureWatcher(p, mono=lambda: clock.mono)
    rig = Rig(features=watcher)
    rig.svc._mono = lambda: clock.mono  # pyright: ignore[reportPrivateUsage]
    _flag_file(p, "metrics:\n  flip: off\n  vexx: visible\n", 2_000_000_000)
    for _ in range(3):
        clock.advance(30)
        rig.svc.tick()
    assert rig.svc.flags is not None and rig.svc.flags["flip"] == "shadow"
    bad = [e for e in rig.health.events if e.kind == "engine_flags_invalid"]
    assert len(bad) == 1 and "vexx" in bad[0].detail and bad[0].severity == "warning"
    _flag_file(p, "metrics:\n  flip: visible\n", 3_000_000_000)
    clock.advance(30)
    rig.svc.tick()
    assert rig.svc.flags["flip"] == "visible"


def test_charm_is_computed_every_two_minutes_of_cycle_time_and_on_a_new_session() -> None:
    """등록부 기본값(services/engine/extended.py): cex 만 2분 주기(metrics §4.2), 나머지는
    사이클마다. 주기는 사이클 시각(as_of)으로 세고, 세션이 바뀌면 바로 계산한다."""
    rig = Rig(registry=REGISTRY)
    with_cex: list[datetime] = []
    for i in range(10):  # 30초 간격 10 사이클 — 4분 30초
        at = T + timedelta(seconds=30 * i)
        r = rig.svc.on_ready(rig.add_cycle(at))
        assert r is not None and {"vex", "gex_pc", "iv_term", "skew_25d"} <= set(r.plugins_run)
        if "cex" in r.plugins_run:
            with_cex.append(at)
    assert with_cex == [T, T + timedelta(seconds=120), T + timedelta(seconds=240)]
    stored = sorted({k[0] for k in rig.store.metrics if k[1] == "cex"})
    assert stored == with_cex
    assert all(m.flag == "shadow" for m in rig.store.metrics.values() if m.metric == "cex")
    (cex_plugin,) = [p for p in REGISTRY if p.name == "cex"]
    last = with_cex[-1]
    due = rig.svc._due((TUE, "day"), last + timedelta(seconds=119))  # pyright: ignore[reportPrivateUsage]
    assert not due(cex_plugin)
    # 마지막 cex 뒤 2분이 안 됐어도 세션(귀속 거래일·세션)이 바뀌면 바로
    due = rig.svc._due((WED, "night"), last + timedelta(seconds=30))  # pyright: ignore[reportPrivateUsage]
    assert due(cex_plugin)
    night = kst(TUE, 18, 0, 30)
    r = rig.svc.on_ready(rig.add_cycle(night))
    assert r is not None and "cex" in r.plugins_run and r.session == "night"


def test_the_confirmed_basis_survives_a_restart() -> None:
    rig = Rig()
    rig.svc.on_ready(rig.add_cycle(T))
    basis = rig.svc.basis.entries["M:202611"]
    # 재기동 — 같은 Redis, 다음 날 주간. 풋 행이 없어 선물 대체 F 가 이어 받은 베이시스를 쓴다
    again = Rig(kst(WED, 10, 0), server=rig.server)
    assert again.svc.basis.entries["M:202611"] == basis
    assert again.svc.last_as_of == T  # 직전 실행의 마지막 as_of 를 이어 받는다
    at = kst(WED, 10, 0)
    again.store.chain += [r for r in synthetic_series("", "202611", NOV, at) if r.cp == "C"]
    again.store.futures.append(fut_row(at - timedelta(seconds=5)))
    result = again.svc.on_ready(
        ChainReady(ts=at, trade_date=WED, session="day", series=(("", "202611"),))
    )
    assert result is not None
    (o,) = result.series
    assert o.basis == basis.basis and o.basis_age == 1 and o.ev is not None
    assert o.ev.forward.reasons == ("futures_fallback", "basis_carried")


# ── 구독 루프 ──


def test_the_run_loop_takes_chain_ready_from_redis_and_stops() -> None:
    rig = Rig(kst(TUE, 16, 30))  # 장 밖 — 따라잡기가 없어 알림으로만 돈다
    ready = rig.add_cycle(T)
    stop = threading.Event()
    th = threading.Thread(target=rig.svc.run, args=(stop, lambda: rig.redis), daemon=True)
    th.start()
    deadline = time.monotonic() + 10
    while not rig.store.levels and time.monotonic() < deadline:
        rig.redis.publish(CHAIN_READY, b"{not json")
        rig.redis.publish(CHAIN_READY, ready.model_dump_json())
        time.sleep(0.05)
    stop.set()
    th.join(5)
    assert not th.is_alive()
    assert rig.store.levels and rig.svc.stats.ready >= 1 and rig.svc.stats.bad_ready >= 1
    assert rig.svc.stats.catchups == 0


# ── 문맥 ──


def test_the_context_gives_the_live_near_month_strikes_and_futures_months() -> None:
    chain = quarterly_expiry_chain()
    r = fakeredis.FakeRedis()
    seed_master(r, "\n".join(chain.master_lines()) + "\n")
    ctx = ChainContext(chain.master_rows())
    ctx.set_listed("", ["202612", "202701"])
    ctx.set_expiry(Series("", "202612"), ExpiryInfo(date(2026, 12, 10), "kis"))
    ctx.set_futures_codes(["A01612", "A01703"])
    from services.bus import CHAIN_CONTEXT_KEY

    r.set(CHAIN_CONTEXT_KEY, context_snapshot(ctx, kst(date(2026, 12, 10), 8, 0)).model_dump_json())
    ec = EngineContext(r, CAL, clock=lambda: 0.0)
    ec.refresh(force=True)
    assert ec.poller_context
    day = date(2026, 12, 10)  # 분기 만기일
    assert ec.near_code(kst(day, 15, 19)) == "A01612"
    assert ec.near_code(kst(day, 15, 21)) == "A01703"  # 15:20 뒤 차월물
    assert ec.futures_months() == {"A01612": "202612", "A01703": "202703"}
    ks = ec.strikes(("", "202612"))
    assert ks[0] == Decimal("745.0") and ks[-1] == Decimal("1595.0") and list(ks) == sorted(ks)
    assert ec.strikes(("WKM", "999999")) == ()


def test_without_master_the_near_code_comes_from_the_futures_rows() -> None:
    rig = Rig(master=False)
    rig.add_cycle(T)
    rig.store.futures.append(
        fut_row(T - timedelta(seconds=5), "1085.00", code="A01703", remaining_days=150)
    )
    result = rig.svc.on_ready(
        ChainReady(ts=T, trade_date=TUE, session="day", series=(("", "202611"),))
    )
    assert result is not None and result.near_code == NEAR
    assert rig.health.kinds() == ["engine_near_code_fallback"]
    with_master = Rig()
    with_master.add_cycle(T)
    result = with_master.svc.on_ready(
        ChainReady(ts=T, trade_date=TUE, session="day", series=(("", "202611"),))
    )
    assert result is not None and result.near_code == NEAR and with_master.health.kinds() == []
    (o,) = result.series
    assert o.ev is not None and o.ev.forward.atm == Decimal("1095.0")


@pytest.mark.parametrize("bad", [b"{", b'{"ts": 1}', b"null"])
def test_bad_messages_are_counted_and_ignored(bad: bytes) -> None:
    rig = Rig()
    assert rig.svc.on_message({"type": "message", "data": bad}) is None
    assert rig.svc.stats.bad_ready == 1 and rig.svc.stats.cycles == 0
    assert rig.svc.on_message({"type": "subscribe", "data": 1}) is None


# ── 일별 지표 (POST_DAY) ──


def _close_cycles(rig: Rig, day: date) -> None:
    """그날 주간 마지막 두 사이클(15:44:00·15:44:30)."""
    for at in (kst(day, 15, 44, 0), kst(day, 15, 44, 30)):
        assert rig.svc.on_ready(rig.add_cycle(at)) is not None


def _prior(n: int, end: date) -> list[date]:
    out: list[date] = []
    d = end
    for _ in range(n):
        d = CAL.prev_trading_day(d)
        out.append(d)
    return out[::-1]


def _daily(rig: Rig, day: date) -> dict[str, Any]:
    """그 거래일 일별 지표(IV 랭크 등 — 같은 시각의 딜러 가정 점검은 빼고)."""
    ts = daily_ts(day)
    return {
        k[1]: m
        for k, m in rig.store.metrics.items()
        if k[0] == ts and k[2] == "all" and k[1] in DAILY_METRICS
    }


def test_post_day_computes_the_daily_metrics_once() -> None:
    rig = Rig(kst(TUE, 15, 44))
    _close_cycles(rig, TUE)
    rig.svc.tick()  # 아직 주간
    assert rig.svc.stats.dailies == 0 and not _daily(rig, TUE)
    rig.clock.advance(6 * 60)  # 15:50 — POST_DAY
    rig.svc.tick()
    assert rig.svc.stats.dailies == 1
    got = _daily(rig, TUE)
    assert set(got) == {"atm_iv_daily", "iv_rank", "iv_percentile", "iv_hv"}
    (close,) = [
        m
        for m in rig.store.metrics.values()
        if (m.metric, m.key, m.ts) == ("atm_iv", "M:202611", kst(TUE, 15, 44, 30))
    ]
    atm = got["atm_iv_daily"]
    assert (atm.value, atm.quality, atm.payload["series"]) == (close.value, "ok", "M:202611")
    assert {m.flag for m in got.values()} == {"shadow"} and atm.trade_date == TUE
    assert got["iv_rank"].value is None and got["iv_rank"].payload["n"] == 1  # 이력 없음
    rig.clock.advance(10)
    rig.svc.tick()  # 그날은 한 번
    assert rig.svc.stats.dailies == 1
    assert "engine_daily_failed" not in rig.health.kinds()


def test_the_daily_backfills_krx_once_and_ranks_with_hv() -> None:
    rig = Rig(kst(TUE, 15, 44))
    days = _prior(25, TUE)
    kis = {"202611": NOV}
    for i, d in enumerate(days):
        expiry = nearest_monthly(d, kis, CAL)
        last = monthly_last_trade(expiry, kis, CAL)
        assert last is not None
        rig.store.krx_options += krx_option_day(d, expiry, last, sigma=0.18 + 0.002 * i)
    rets = [0.008 if i % 2 else -0.006 for i in range(len(days) - 1)]
    rig.store.krx_futures += krx_settles(days, "202612", rets)
    _close_cycles(rig, TUE)
    rig.clock.advance(6 * 60)
    rig.svc.tick()
    backfill = [
        m for m in rig.store.metrics.values() if m.metric == "atm_iv_daily" and m.trade_date < TUE
    ]
    assert sorted(m.trade_date for m in backfill) == days
    assert all(m.payload["source"] == "krx" and m.value is not None for m in backfill)
    assert len(rig.store.krx_iv_reads) == 25
    got = _daily(rig, TUE)
    rank = got["iv_rank"]
    assert rank.value is not None and rank.payload["n"] == 26
    assert rank.payload["sources"] == ["krx", "self"] and rank.quality == "estimated"
    hv = got["iv_hv"]
    assert hv.value is not None and hv.payload["hv_end"] == days[-1].isoformat()
    assert hv.payload["contracts"] == ["202612"]
    # 다음 거래일 — KRX 백필은 이력 없는 날만: 다시 읽지 않는다
    rig.clock.at = kst(WED, 15, 44)
    _close_cycles(rig, WED)
    rig.clock.advance(6 * 60)
    rig.svc.tick()
    assert rig.svc.stats.dailies == 2 and len(rig.store.krx_iv_reads) == 25
    assert _daily(rig, WED)["iv_rank"].payload["n"] == 27


def test_the_daily_value_is_the_nearest_monthly_of_the_last_cycle_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """마지막 주간 사이클에서 가장 가까운 월물(202611)이 실패해 다음 달(202612) 행만 있으면 그 값을
    오늘 값(이력)으로 저장하지 않는다 — invalid(`nearest_monthly_missing`)."""
    rig = Rig(kst(TUE, 15, 44))
    rig.store.add_expiry("", "202612", date(2026, 12, 10))
    _close_cycles(rig, TUE)
    at = kst(TUE, 15, 44, 50)  # 마지막 사이클 — 202611 평가가 실패한다
    rig.store.chain += synthetic_series("", "202612", date(2026, 12, 10), at, sigma=0.35)
    ready = rig.add_cycle(at)
    real = ev_mod.evaluate_expiry

    def flaky(quotes: Any, *a: Any, **kw: Any) -> Any:
        qs = list(quotes)
        if qs[0].expiry == "202611":
            raise ZeroDivisionError("boom")
        return real(qs, *a, **kw)

    monkeypatch.setattr(ev_mod, "evaluate_expiry", flaky)
    result = rig.svc.on_ready(ready)
    assert result is not None
    assert {o.label: o.status for o in result.series} == {
        "M:202611": "failed",
        "M:202612": "evaluated",
    }
    monkeypatch.undo()
    rig.clock.advance(6 * 60)
    rig.svc.tick()
    atm = _daily(rig, TUE)["atm_iv_daily"]
    assert (atm.value, atm.quality) == (None, "invalid")
    assert atm.payload["series"] == "M:202611"
    assert atm.payload["reasons"] == ["nearest_monthly_missing"]


def _daily_row(d: date, source: str, value: float | None, quality: str) -> MetricRecord:
    return MetricRecord(
        ts=daily_ts(d),
        trade_date=d,
        session="day",
        metric="atm_iv_daily",
        scope="all",
        value=value,
        payload={"source": source, "reasons": [] if value is not None else ["no_close"]},
        quality=quality,  # type: ignore[arg-type]
        flag="shadow",
    )


def test_a_day_whose_own_value_was_invalid_is_backfilled_from_krx_once() -> None:
    """자체 값이 invalid 였던 날(마감 사이클 없음 등 — 그날 KRX 는 아직 없었다)은 다음 날 KRX 가
    그날 것을 내면 KRX 로 백필해 그 행을 덮는다. KRX 로 계산한 날(값을 못 냈어도)과 자체 값이 성한
    날은 다시 계산하지 않는다."""
    rig = Rig(kst(TUE, 15, 44))
    kis = {"202611": NOV}
    d1 = CAL.prev_trading_day(TUE)  # 자체 invalid → KRX 백필
    d2 = CAL.prev_trading_day(d1)  # KRX 로도 못 냈다(invalid krx) — 다시 안 한다
    d3 = CAL.prev_trading_day(d2)  # 자체 값이 성하다 — 다시 안 한다
    rig.store.write_metrics(
        [
            _daily_row(d1, "self", None, "invalid"),
            _daily_row(d2, "krx", None, "invalid"),
            _daily_row(d3, "self", 0.21, "stale"),
        ]
    )
    for d in (d3, d2, d1):
        expiry = nearest_monthly(d, kis, CAL)
        last = monthly_last_trade(expiry, kis, CAL)
        assert last is not None
        rig.store.krx_options += krx_option_day(d, expiry, last, sigma=0.19)
    rig.store.krx_futures += krx_settles([d3, d2, d1], "202612", [0.001, -0.002])
    _close_cycles(rig, TUE)
    rig.clock.advance(6 * 60)
    rig.svc.tick()
    assert rig.store.krx_iv_reads == [(d1, nearest_monthly(d1, kis, CAL))]
    got = rig.store.metrics[(daily_ts(d1), "atm_iv_daily", "all", "")]
    assert got.payload["source"] == "krx" and got.quality in ("ok", "estimated")
    assert got.value == pytest.approx(0.19, abs=5e-4)
    rank = _daily(rig, TUE)["iv_rank"]  # d3(자체 stale)·d1(KRX)·오늘
    assert rank.payload["n"] == 3 and rank.payload["sources"] == ["krx", "self"]
    # 다음 거래일 — d1 은 이제 KRX 행이라 다시 읽지 않는다
    rig.clock.at = kst(WED, 15, 44)
    _close_cycles(rig, WED)
    rig.clock.advance(6 * 60)
    rig.svc.tick()
    assert rig.svc.stats.dailies == 2 and len(rig.store.krx_iv_reads) == 1


def test_a_failed_daily_is_retried_a_minute_later() -> None:
    rig = Rig(kst(TUE, 15, 50))
    rig.store.fail_reads = True
    rig.svc.tick()
    failed = [e.detail for e in rig.health.of("engine_daily_failed")]
    assert sum(d.startswith("일별 지표") for d in failed) == 1 and rig.svc.stats.dailies == 0
    rig.store.fail_reads = False
    rig.clock.advance(30)
    rig.svc.tick()  # 아직 1분 안
    assert rig.svc.stats.dailies == 0
    rig.clock.advance(31)
    rig.svc.tick()
    assert rig.svc.stats.dailies == 1
    atm = _daily(rig, TUE)["atm_iv_daily"]  # 사이클이 없었다 — 오늘 값 없음
    assert (atm.value, atm.quality) == (None, "invalid")


def test_visible_daily_metrics_are_published_on_engine_metrics() -> None:
    rig = Rig(kst(TUE, 15, 44))
    flags: dict[str, Flag] = {"iv_rank": "visible", "atm_iv_daily": "visible"}
    rig.svc.flags = flags
    _close_cycles(rig, TUE)
    rig.published()  # 사이클 발행은 비운다
    rig.clock.advance(6 * 60)
    rig.svc.tick()
    (msg,) = [p for p in rig.published() if isinstance(p, EngineMetrics)]
    assert msg.as_of == daily_ts(TUE) and (msg.trade_date, msg.session) == (TUE, "day")
    assert {m.metric for m in msg.metrics} == {"iv_rank", "atm_iv_daily"}
    assert msg.quality == "estimated"  # 랭크 — 창이 짧다

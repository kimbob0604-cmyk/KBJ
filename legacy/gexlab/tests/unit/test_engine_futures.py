"""engine 선물 지표 (services/engine/futures.py·service.py — metrics §7).

- 분봉 조회 원문 output1(합성 fixture `minute_day.json` — 실측과 같은 형태)을 검증해 core 에
  넘긴다 — 다섯 행(시장
  베이시스 = 자체 선물가 − 지수·이론 베이시스·괴리율·OI 증감·체결강도, key = 선물 종목코드)
- 교차검증: 이론가 − 지수 대 KIS basis 가 0.05pt 를 넘으면 health — 실측 응답(KIS basis = 이론
  베이시스)은 통과한다
- 값이 없으면 그 행만 null·invalid(`field_missing`)
- 서비스: 1분마다 마지막으로 본 시각 뒤 원문만(기동하면 하루 앞부터), 태그 없거나 검증 실패인
  응답은 그것만 건너뛰고 센다, 플래그대로(shadow 저장만·visible 발행·off 읽지 않음)
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from core.metrics.futures import futures_metrics
from data.kis.models import MinuteQuote
from services.bus import EngineMetrics
from services.engine.futures import (
    FUTURES_METRICS,
    FUTURES_TR,
    basis_health,
    futures_rows,
    quote_of,
)
from services.engine.records import MetricRecord
from services.engine.registry import Flag
from tests.fakes.engine_inputs import kst
from tests.unit.test_engine_service import TUE, Rig

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "kis" / "minute_day.json"
AFTER = kst(TUE, 16, 5)  # scheduler 가 주간 분봉을 받은 시각


def body() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def out1(**over: str) -> dict[str, Any]:
    return body()["output1"] | over


def test_the_measured_output1_is_validated_into_the_core_quote() -> None:
    m = MinuteQuote.model_validate(out1())
    assert (m.futs_shrn_iscd, m.basis, m.kospi200_nmix, m.otst_stpl_qty_icdc) == (
        "A01612",
        Decimal("5.85"),
        Decimal("1097.62"),
        1310,
    )
    q = quote_of(out1())
    assert (q.code, q.price, q.basis, q.divergence, q.strength, q.theory_price) == (
        "A01612",
        Decimal("1098.05"),
        Decimal("5.85"),
        Decimal("-0.49"),
        Decimal("97.37"),
        Decimal("1103.47"),
    )
    blank = quote_of(out1(basis=" ", tday_rltv=""))
    assert blank.basis is None and blank.strength is None
    with pytest.raises(ValidationError):
        quote_of(out1(futs_shrn_iscd=""))
    with pytest.raises(ValidationError):
        quote_of(out1(futs_prpr="abc"))


def test_five_rows_carry_the_kis_values_and_the_cross_check() -> None:
    fm = futures_metrics(quote_of(out1()))
    rows = {r.metric: r for r in futures_rows(AFTER, TUE, "day", fm, "shadow")}
    assert tuple(rows) == FUTURES_METRICS
    assert {(r.scope, r.key, r.ts, r.trade_date, r.session, r.flag) for r in rows.values()} == {
        ("all", "A01612", AFTER, TUE, "day", "shadow")
    }
    assert {m: r.value for m, r in rows.items()} == {
        "futures_basis": pytest.approx(0.43),  # 자체 1098.05 − 1097.62
        "futures_theory_basis": pytest.approx(5.85),
        "futures_divergence": -0.49,
        "futures_oi_change": 1310.0,
        "futures_strength": 97.37,
    }
    assert {r.quality for r in rows.values()} == {"ok"}
    p = rows["futures_theory_basis"].payload
    assert (p["kis_basis"], p["basis_gap"], p["basis_check"], p["tolerance"]) == (
        5.85,
        0.0,
        True,
        0.05,
    )
    assert (p["price"], p["index"], p["source"]) == (1098.05, 1097.62, FUTURES_TR)
    missing = {
        r.metric: r
        for r in futures_rows(
            AFTER, TUE, "day", futures_metrics(quote_of(out1(tday_rltv=""))), "shadow"
        )
    }
    assert missing["futures_strength"].value is None
    assert missing["futures_strength"].quality == "invalid"
    assert missing["futures_strength"].payload["reasons"] == ["field_missing"]
    assert missing["futures_basis"].quality == "ok"


def test_the_cross_check_health() -> None:
    assert basis_health(futures_metrics(quote_of(out1()))) is None  # fixture: KIS basis = 이론
    h = basis_health(futures_metrics(quote_of(out1(basis="0.43"))))  # 시장 베이시스가 오면
    assert h is not None and h.kind == "engine_futures_basis_check" and h.subject == "A01612"
    assert "5.42" in h.detail
    edge = out1(basis="5.90")  # 이론 5.85 과 0.05 차 — 경계는 통과
    assert basis_health(futures_metrics(quote_of(edge))) is None
    assert basis_health(futures_metrics(quote_of(out1(kospi200_nmix="0.00")))) is None


# ── 서비스 ──


def record(rig: Rig, at: datetime, b: dict[str, Any], d: date | None = TUE, s: str = "day") -> None:
    rig.store.kis_rest.append((at, d, s, FUTURES_TR, b))


def fut_rows(rig: Rig) -> list[MetricRecord]:
    return [m for m in rig.store.metrics.values() if m.metric.startswith("futures_")]


def test_the_service_turns_new_recorded_responses_into_rows_every_minute() -> None:
    rig = Rig(kst(TUE, 16, 10))
    record(rig, kst(TUE, 16, 10) - timedelta(days=2), body())  # 기동 하루 앞보다 옛 것 — 안 본다
    record(rig, AFTER, body())
    record(rig, AFTER + timedelta(seconds=1), {"rt_cd": "1", "msg1": "오류"})  # output1 없음
    record(rig, AFTER + timedelta(seconds=2), body() | {"output1": out1(futs_prpr="x")})
    record(rig, AFTER + timedelta(seconds=3), body(), d=None, s="")  # 태그 없음
    rig.svc.tick()
    rows = fut_rows(rig)
    assert len(rows) == 5 and {r.ts for r in rows} == {AFTER}
    assert {r.flag for r in rows} == {"shadow"}
    assert rig.svc.stats.futures_rows == 5 and rig.svc.stats.bad_futures == 2
    assert rig.health.of("engine_futures_basis_check") == []  # fixture 응답은 교차검증 통과
    later = AFTER + timedelta(minutes=2)
    record(rig, later, body() | {"output1": out1(basis="0.43")})
    rig.clock.advance(30)
    rig.svc.tick()
    assert len(fut_rows(rig)) == 5  # 1분이 안 됐다
    rig.clock.advance(30)
    rig.svc.tick()
    assert len(fut_rows(rig)) == 10
    (h,) = rig.health.of("engine_futures_basis_check")  # basis 0.43 — 이론 5.85 와 어긋남
    assert h.at == kst(TUE, 16, 11)  # 1분 뒤 읽을 때
    rig.clock.advance(60)
    rig.svc.tick()
    assert len(fut_rows(rig)) == 10  # 이미 본 응답은 다시 읽지 않는다


def test_futures_rows_follow_the_flag() -> None:
    rig = Rig(kst(TUE, 16, 10))
    off: dict[str, Flag] = {"futures": "off"}
    rig.svc.flags = off
    record(rig, AFTER, body())
    rig.svc.tick()
    assert fut_rows(rig) == [] and rig.health.of("engine_futures_basis_check") == []
    rig = Rig(kst(TUE, 16, 10))
    on: dict[str, Flag] = {"futures": "visible"}
    rig.svc.flags = on
    record(rig, AFTER, body())
    rig.svc.tick()
    got = [m for m in rig.published() if isinstance(m, EngineMetrics)]
    (msg,) = got
    assert {x.metric for x in msg.metrics} == set(FUTURES_METRICS)
    assert msg.as_of == AFTER and (msg.trade_date, msg.session) == (TUE, "day")


def test_a_failed_read_is_isolated_and_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig(kst(TUE, 16, 10))
    record(rig, AFTER, body())
    real = rig.store.kis_rest_outputs
    calls: list[int] = []

    def flaky(*a: Any, **k: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db down")
        return real(*a, **k)

    monkeypatch.setattr(rig.store, "kis_rest_outputs", flaky)
    rig.svc.tick()
    assert fut_rows(rig) == [] and "engine_flow_failed" in rig.health.kinds()
    rig.clock.advance(60)
    rig.svc.tick()
    assert len(fut_rows(rig)) == 5  # 본 시각을 옮기지 않았다 — 다음 주기에 다시 읽는다

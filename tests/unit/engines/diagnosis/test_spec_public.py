"""metrics §D1~§D5 공공지표 진단·§D4 매크로 위험 게이지 — p7_world 합성 입력·판정 표 전 조합."""

from __future__ import annotations

import itertools
from datetime import datetime
from decimal import Decimal

import pytest

from kbj.config.diag import load_diag
from kbj.core.p7_rows import DiagIndicatorRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.diagnosis.bands import Obs
from kbj.engines.diagnosis.public import (
    PUBLIC_KEYS,
    READY_EXPORT,
    PublicInputs,
    macro_gauge,
    public_indicators,
)
from tests.fixtures.synthetic.p7_world import build_world

CFG = load_diag()
SRC = "KBJ:diag.public"


def _obs(key: str, at: datetime) -> list[Obs]:
    w = build_world()
    return [Obs(p.day, p.value, Quality.OK, "SYN") for p in w.as_of(key, at)]


def _inputs(at: datetime, *, trade: bool = True) -> PublicInputs:
    return PublicInputs(
        credit_spread=_obs("credit_spread", at), ktb3y=_obs("ktb3y", at),
        credit_loan=_obs("credit_balance", at), deposits=_obs("deposits", at),
        export_yoy=_obs("export_yoy", at) if trade else (), export_ready=trade,
    )  # fmt: skip


def test_public_keys_match_config_order() -> None:
    assert CFG.keys("public") == PUBLIC_KEYS


def test_public_indicators_from_world() -> None:
    w = build_world()
    at = datetime(2026, 12, 10, 8, 40, tzinfo=KST)
    rows = public_indicators(_inputs(at), CFG, day=w.clock.date(), as_of=at, source=SRC)
    by = {r.key: r for r in rows}
    assert tuple(by) == PUBLIC_KEYS
    # 08:40 에는 그날 18:00 공표분이 없다 — 마지막 관측은 전 거래일
    assert by["credit_spread"].inputs["obs_day"] == "2026-12-09"
    # 금투협 D+1 13:00 — 08:40 에는 전전 거래일까지(지연 3일 표본이 있으면 더 앞)
    assert str(by["deposits"].inputs["obs_day"]) <= "2026-12-08"
    assert by["credit_spread"].state in ("narrow", "normal", "wide")
    for k in ("credit_balance", "deposits", "rate_direction"):
        assert by[k].state is not None and by[k].inputs["change_days"] == CFG.change_days
    # 수출 모멘텀은 잠정치 — ok 가 아니다. 1·11·21일 관측만이라 이력이 짧다
    assert by["export_momentum"].quality is not Quality.OK
    assert by["export_momentum"].unit == "%"


def test_rate_direction_is_bp_change_hand_computed() -> None:
    w = build_world()
    at = datetime(2026, 12, 10, 8, 40, tzinfo=KST)
    pts = [p for p in w.as_of("ktb3y", at) if p.value is not None]
    want = (pts[-1].value - pts[-1 - CFG.change_days].value) * 100  # type: ignore[operator]
    rows = public_indicators(_inputs(at), CFG, day=w.clock.date(), as_of=at, source=SRC)
    got = next(r for r in rows if r.key == "rate_direction")
    assert got.value == want.quantize(Decimal("0.0001"))


def test_export_not_ready_reason() -> None:
    at = datetime(2026, 12, 10, 8, 40, tzinfo=KST)
    rows = public_indicators(_inputs(at, trade=False), CFG, day=at.date(), as_of=at, source=SRC)
    r = next(r for r in rows if r.key == "export_momentum")
    assert r.value is None and READY_EXPORT in r.reasons


def _row(key: str, state: str | None) -> DiagIndicatorRow:
    at = datetime(2026, 12, 10, 8, 40, tzinfo=KST)
    return DiagIndicatorRow(
        trade_date=at.date(), key=key, value=None if state is None else Decimal(1), unit="%",
        state=state,  # type: ignore[arg-type]
        pct_rank=None, inputs={}, source=SRC, as_of=at,
        quality=Quality.ESTIMATED if state is None else Quality.OK,
    )  # fmt: skip


# 공개 다섯 지표의 상태 → 점수(위험 선호 방향 — config/diag.yaml)
_STATES = {
    "credit_spread": {"narrow": 1, "normal": 0, "wide": -1, None: None},
    "credit_balance": {"high": 1, "normal": 0, "low": -1, None: None},
    "deposits": {"high": 1, "normal": 0, "low": -1, None: None},
    "export_momentum": {"high": 1, "normal": 0, "low": -1, None: None},
    "rate_direction": {"low": 1, "normal": 0, "high": -1, None: None},
}


def test_d4_gauge_full_table() -> None:
    at = datetime(2026, 12, 10, 8, 40, tzinfo=KST)
    keys = list(_STATES)
    for combo in itertools.product(*(list(_STATES[k]) for k in keys)):
        rows = [_row(k, s) for k, s in zip(keys, combo, strict=True)]
        scores = [_STATES[k][s] for k, s in zip(keys, combo, strict=True)]
        have = [s for s in scores if s is not None]
        g = macro_gauge(rows, CFG, day=at.date(), as_of=at, source=SRC)
        if len(have) < CFG.gauge.min_components:
            assert g.label == "hold" and g.score is None and g.quality is not Quality.OK
            continue
        s = sum(have)
        assert g.score == s
        assert g.label == ("risk_on" if s >= 2 else "risk_off" if s <= -2 else "neutral")
        assert len(g.parts) == len(have)
        assert (g.quality is Quality.OK) == (len(have) == len(keys))


def test_gauge_rejects_login_indicator() -> None:
    at = datetime(2026, 12, 10, 8, 40, tzinfo=KST)
    with pytest.raises(ValueError, match="공개 지표만"):
        macro_gauge([_row("breadth_ma20", "high")], CFG, day=at.date(), as_of=at, source=SRC)

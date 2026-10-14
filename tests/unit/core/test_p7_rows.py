"""`kbj.core.p7_rows` — P7 행 자료형 검증(docs/p7_design.md §1.1·§3.3, metrics §D0~§D9)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from kbj.core import p7_rows as pr
from kbj.core.quality import Quality

NOW = datetime(2026, 10, 13, 7, 0, tzinfo=UTC)
D = date(2026, 10, 13)


def ind(**kw: Any) -> pr.DiagIndicatorRow:
    base: dict[str, Any] = dict(
        trade_date=D, key="credit_spread", value=Decimal("0.62"), unit="%p", state="normal",
        pct_rank=None, inputs={"aa_minus": "3.10", "ktb3y": "2.48"}, source="KBJ:diag.public",
        as_of=NOW, quality=Quality.OK,
    )  # fmt: skip
    base.update(kw)
    return pr.DiagIndicatorRow(**base)


def fg(**kw: Any) -> pr.FearGreedRow:
    base: dict[str, Any] = dict(
        trade_date=D, score=Decimal("50.0"), band="neutral",
        components={"vol": Decimal(40), "breadth": Decimal(60), "credit_balance": Decimal(50),
                    "foreign": Decimal(50), "credit_spread": Decimal(50)},
        source="KBJ:diag.daily", as_of=NOW, quality=Quality.OK,
    )  # fmt: skip
    base.update(kw)
    return pr.FearGreedRow(**base)


def verdict(**kw: Any) -> pr.VerdictRow:
    base: dict[str, Any] = dict(
        trade_date=D, label="risk_on", score=2, parts={"fear_greed": 1, "credit_spread": 1},
        event_flags=("expiry",), source="KBJ:diag.daily", as_of=NOW, quality=Quality.ESTIMATED,
    )  # fmt: skip
    base.update(kw)
    return pr.VerdictRow(**base)


def test_indicator_ok_and_frozen_inputs() -> None:
    r = ind()
    assert r.quality is Quality.OK and r.reasons == ()
    with pytest.raises(TypeError):
        r.inputs["x"] = 1  # type: ignore[index]


def test_indicator_rejects_bad_values() -> None:
    with pytest.raises(ValueError, match="ok 일 수 없다"):
        ind(value=None, state=None)
    with pytest.raises(ValueError, match="구간"):
        ind(value=None, quality=Quality.INVALID)  # state 가 남아 있음
    with pytest.raises(TypeError, match="Decimal"):
        ind(value=0.62)
    with pytest.raises(ValueError, match="범위"):
        ind(pct_rank=Decimal(101))
    with pytest.raises(ValueError, match="중 하나"):
        ind(state="hot")
    with pytest.raises(ValueError, match="naive"):
        ind(as_of=datetime(2026, 10, 13, 7))  # noqa: DTZ001 — naive 거부 시험
    with pytest.raises(TypeError, match="date"):
        ind(trade_date=NOW)
    with pytest.raises(ValueError, match="소문자"):
        ind(key="Credit")


def test_indicator_missing_value_not_ok() -> None:
    r = ind(value=None, state=None, quality=Quality.ESTIMATED, reasons=["이력 부족"])
    assert r.reasons == ("이력 부족",)


@pytest.mark.parametrize(
    ("score", "band"),
    [("0", "extreme_fear"), ("24.9", "extreme_fear"), ("25", "fear"), ("44.9", "fear"),
     ("45", "neutral"), ("55", "neutral"), ("55.1", "greed"), ("75", "greed"),
     ("75.1", "extreme_greed"), ("100", "extreme_greed")],
)  # fmt: skip
def test_fear_greed_band_edges(score: str, band: str) -> None:
    assert pr.fear_greed_band(Decimal(score)) == band
    assert fg(score=Decimal(score), band=band).band == band


def test_fear_greed_rules() -> None:
    with pytest.raises(ValueError, match="맞지 않는다"):
        fg(score=Decimal(80), band="neutral")
    with pytest.raises(ValueError, match="같이"):
        fg(score=None, band="neutral", quality=Quality.INVALID)
    with pytest.raises(ValueError, match="ok 일 수 없다"):
        fg(score=None, band=None)
    r = fg(score=None, band=None, quality=Quality.INVALID, reasons=["성분 3개"])
    assert r.score is None


def test_verdict_and_gauge_hold_rules() -> None:
    assert verdict().event_flags == ("expiry",)
    with pytest.raises(ValueError, match="중 하나"):
        verdict(event_flags=("holiday",))
    with pytest.raises(ValueError, match="−1·0·\\+1"):
        verdict(parts={"x": 2})
    with pytest.raises(ValueError, match="점수를 둘 수 없다"):
        verdict(label="hold", score=0, quality=Quality.INVALID)
    with pytest.raises(ValueError, match="ok 일 수 없다"):
        verdict(label="hold", score=None, quality=Quality.OK)
    with pytest.raises(ValueError, match="점수가 있어야"):
        verdict(score=None)
    g = pr.GaugeRow(
        trade_date=D, label="hold", score=None, parts={"credit_spread": 1},
        source="KBJ:diag.public", as_of=NOW, quality=Quality.INVALID, reasons=("성분 1개",),
    )  # fmt: skip
    assert g.label == "hold"


def test_event_and_vol_index() -> None:
    e = pr.EventMarker(
        date=D, kind="expiry", title="월물 만기", source="KBJ:calendar", as_of=NOW,
        quality=Quality.OK, detail={"series": "K200"},
    )  # fmt: skip
    assert e.detail["series"] == "K200"
    with pytest.raises(ValueError):
        pr.EventMarker(
            date=D,
            kind="holiday",  # pyright: ignore[reportArgumentType]
            title="x",
            source="s",
            as_of=NOW,
            quality=Quality.OK,
        )
    v = pr.VolIndexRow(
        trade_date=D, index_id="vkospi", close=Decimal("18.2"), source="KRX:idx/drvprod_dd_trd",
        as_of=NOW, quality=Quality.OK,
    )  # fmt: skip
    assert v.close == Decimal("18.2")
    with pytest.raises(ValueError, match="ok 일 수 없다"):
        pr.VolIndexRow(
            trade_date=D,
            index_id="vkospi",
            close=None,
            source="KRX:x",
            as_of=NOW,
            quality=Quality.OK,
        )


def test_gex_level_view() -> None:
    v = pr.GexLevelView(
        ts=NOW, trade_date=D, session="day", scope="all", name="flip", value=None,
        flag="visible", source="KBJ:gex.engine", as_of=NOW, quality=Quality.OK,
    )  # fmt: skip
    assert v.value is None  # Flip 없음은 ok 와 함께 올 수 있다
    with pytest.raises(ValueError, match="유한"):
        pr.GexLevelView(
            ts=NOW,
            trade_date=D,
            session="day",
            scope="all",
            name="flip",
            value=float("nan"),
            flag="visible",
            source="KBJ:gex.engine",
            as_of=NOW,
            quality=Quality.OK,
        )
    with pytest.raises(ValueError, match="중 하나"):
        pr.GexLevelView(
            ts=NOW,
            trade_date=D,
            session="day",
            scope="weekly",  # pyright: ignore[reportArgumentType]
            name="flip",
            value=1.0,
            flag="visible",
            source="KBJ:gex.engine",
            as_of=NOW,
            quality=Quality.OK,
        )


def test_constant_lists_match_literals() -> None:
    assert set(pr.VERDICT_LABELS) == {"risk_on", "neutral", "risk_off", "hold"}
    assert len(pr.DIAG_PUB_TABLES) == 3 and len(pr.DIAG_PRV_TABLES) == 4

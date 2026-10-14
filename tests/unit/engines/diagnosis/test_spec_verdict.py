"""metrics §D8 종합 판정 — GEX 체제·근거 셋·보류·이벤트 꼬리표·품질."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from kbj.config.diag import load_diag
from kbj.core.p7_rows import DiagIndicatorRow, EventMarker, FearGreedRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.diagnosis.verdict import (
    GexRegime,
    event_flags,
    gex_regime,
    top_reasons,
    verdict,
)

CFG = load_diag()
AT = datetime(2026, 12, 10, 16, 0, tzinfo=KST)
DAY = AT.date()
SRC = "KBJ:diag.daily"


def _ind(key: str, state: str, q: Quality = Quality.OK) -> DiagIndicatorRow:
    return DiagIndicatorRow(
        trade_date=DAY, key=key, value=Decimal(1), unit="%", state=state,  # type: ignore[arg-type]
        pct_rank=Decimal(50), inputs={}, source=SRC, as_of=AT, quality=q,
    )  # fmt: skip


def _fg(score: str) -> FearGreedRow:
    from kbj.core.p7_rows import fear_greed_band

    s = Decimal(score)
    return FearGreedRow(
        trade_date=DAY, score=s, band=fear_greed_band(s), components={}, source=SRC, as_of=AT,
        quality=Quality.OK,
    )  # fmt: skip


def test_gex_regime() -> None:
    assert gex_regime(GexRegime(351.0, 350.5, True, Quality.OK)) == 1
    assert gex_regime(GexRegime(349.0, 350.5, True, Quality.OK)) == -1
    assert gex_regime(GexRegime(350.5, 350.5, True, Quality.OK)) == 0
    assert gex_regime(GexRegime(351.0, None, True, Quality.OK)) == 0  # Flip 없음 0
    assert gex_regime(GexRegime(351.0, 350.0, False, Quality.OK)) is None  # visible 아님
    assert gex_regime(GexRegime(351.0, 350.0, True, Quality.INVALID)) is None
    assert gex_regime(None) is None


def test_d8_risk_on_with_top_three() -> None:
    rows = [
        _ind("credit_spread", "narrow"), _ind("breadth_ma20", "high"),
        _ind("kr_vs_global", "normal"), _ind("foreign_flow", "extreme_high"),
    ]  # fmt: skip
    v = verdict(CFG, day=DAY, as_of=AT, source=SRC, fear=_fg("60"), indicators=rows,
                gex=GexRegime(351.0, 350.5, True, Quality.OK))  # fmt: skip
    assert v.parts == {"fear_greed": 1, "credit_spread": 1, "breadth_ma20": 1, "kr_vs_global": 0,
                       "foreign_flow": 1, "gex": 1}  # fmt: skip
    assert v.score == 5 and v.label == "risk_on" and v.quality is Quality.OK
    assert top_reasons(v.parts, CFG.verdict.top_reasons or 3) == [
        "+공포탐욕", "+신용스프레드", "+시장폭",
    ]  # fmt: skip


def test_d8_risk_off_and_neutral() -> None:
    rows = [_ind("credit_spread", "wide"), _ind("breadth_ma20", "low"),
            _ind("kr_vs_global", "normal"), _ind("foreign_flow", "normal")]  # fmt: skip
    v = verdict(CFG, day=DAY, as_of=AT, source=SRC, fear=_fg("50"), indicators=rows, gex=None)
    assert v.score == -2 and v.label == "risk_off"
    assert "gex" not in v.parts and v.quality is Quality.ESTIMATED
    rows[1] = _ind("breadth_ma20", "high")
    v2 = verdict(CFG, day=DAY, as_of=AT, source=SRC, fear=_fg("50"), indicators=rows, gex=None)
    assert v2.score == 0 and v2.label == "neutral"


def test_d8_hold_below_min_components() -> None:
    rows = [_ind("credit_spread", "narrow"), _ind("breadth_ma20", "high")]
    v = verdict(CFG, day=DAY, as_of=AT, source=SRC, fear=None, indicators=rows, gex=None)
    assert v.label == "hold" and v.score is None and v.quality is not Quality.OK
    assert any("보류" in r for r in v.reasons)


def test_d8_quality_is_worst_input() -> None:
    rows = [_ind("credit_spread", "narrow", Quality.STALE), _ind("breadth_ma20", "high"),
            _ind("kr_vs_global", "high"), _ind("foreign_flow", "high")]  # fmt: skip
    v = verdict(CFG, day=DAY, as_of=AT, source=SRC, fear=_fg("60"), indicators=rows,
                gex=GexRegime(1.0, 0.5, True, Quality.OK))  # fmt: skip
    assert v.quality is Quality.STALE


def test_event_flags() -> None:
    ev = [
        EventMarker(date=date(2026, 12, 10), kind="expiry", title="월물 만기",
        source="KBJ:calendar",
                    as_of=AT, quality=Quality.OK),
        EventMarker(date=date(2026, 12, 11), kind="earnings_cluster", title="실적 몰림 12종목",
                    source="X", as_of=AT, quality=Quality.ESTIMATED),
        EventMarker(date=date(2026, 12, 21), kind="export_flash", title="수출 속보", source="X",
                    as_of=AT, quality=Quality.ESTIMATED),
    ]  # fmt: skip
    assert event_flags(ev, [date(2026, 12, 10), date(2026, 12, 11)]) == (
        "earnings_cluster", "expiry",
    )  # fmt: skip
    v = verdict(CFG, day=DAY, as_of=AT, source=SRC, fear=None, indicators=[], gex=None,
                flags=event_flags(ev, [DAY]))  # fmt: skip
    assert v.event_flags == ("expiry",)

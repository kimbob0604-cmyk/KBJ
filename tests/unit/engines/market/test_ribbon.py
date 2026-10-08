"""상단 띠 — 쏠림 손계산 1건·경기민감 대 방어·세션·금통위·준비 중 칩(docs/metrics.md §6.4·§6.5)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from kbj.config.markets import load_markets
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality, Sourced
from kbj.core.rows import IndexBar
from kbj.core.time import KST
from kbj.engines.flows.totals import market_investor_totals
from kbj.engines.market.ribbon import (
    CHIP_ORDER,
    PENDING,
    RibbonInputs,
    cyclical_vs_defensive,
    ribbon,
    semi_skew,
    semi_skew_from_ledger,
    session_chip,
)
from tests.fixtures.synthetic.ledger_gen import generate
from tests.unit.engines.flows.flow_rows import days, ledger_of, lrow

D = days(3)
CAL = TradingCalendar(extra_closed=[date(2026, 10, 9)])  # 한글날(명시)
SEMIS = tuple(load_markets().ribbon.semis)


def _kst(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=KST)


# ── 반도체 쏠림 ─────────────────────────────────────────────────────────────────────────


def test_쏠림_손계산_metrics_6_4() -> None:
    """두 종목 시총 40%·수익률 +3%, 코스피 +1% → r_ex = −0.333…%, 쏠림 = 3.333…%p."""
    res = semi_skew([(3.0, 20), (3.0, 20)], 1.0, 100)
    assert res.value == pytest.approx(10 / 3)
    assert res.parts["r_ex"] == pytest.approx(-1 / 3)
    assert res.parts["r_semi"] == pytest.approx(3.0)


def test_시총_합이_0_이하면_invalid() -> None:
    res = semi_skew([(3.0, 100)], 1.0, 100)
    assert res.value is None and res.quality is Quality.INVALID and res.reason
    assert semi_skew([], 1.0, 100).value is None


def _kospi(c0: float, c1: float) -> list[IndexBar]:
    return [
        IndexBar("0001", D[0], "코스피", c0, c0, c0, c0, None, None, "krx", Quality.OK),
        IndexBar("0001", D[1], "코스피", c1, c1, c1, c1, None, None, "krx", Quality.OK),
    ]


def _skew_ledger(over: dict | None = None):
    s1, s2 = SEMIS
    rows = [
        lrow(s1, D[0], close=100.0, mktcap=20),
        lrow(s1, D[1], close=103.0, mktcap=21),
        lrow(s2, D[0], close=200.0, mktcap=20),
        lrow(s2, D[1], close=206.0, mktcap=21),
        lrow("A00003", D[0], mktcap=60),
        lrow("A00003", D[1], mktcap=60),
        lrow("A00004", D[0], mktcap=999, kind="etf"),  # ETF 는 코스피 시총에 넣지 않는다
    ]
    rows = [(over or {}).get((r.code, r.date), r) for r in rows]
    return ledger_of(rows, D[:2])


def test_원장으로_쏠림() -> None:
    res = semi_skew_from_ledger(_skew_ledger(), _kospi(1000.0, 1010.0), D[1], semis=SEMIS)
    assert res.value == pytest.approx(10 / 3)
    assert res.quality is Quality.OK
    s = res.sourced()
    assert s is not None and s.as_of.hour == 15 and s.source == "KRX+KIS"


def test_입력이_하나라도_없으면_None() -> None:
    s1, _ = SEMIS
    led = _skew_ledger({(s1, D[1]): lrow(s1, D[1], close=None)})
    res = semi_skew_from_ledger(led, _kospi(1000.0, 1010.0), D[1], semis=SEMIS)
    assert res.value is None and res.reason and res.sourced() is None
    no_index = semi_skew_from_ledger(_skew_ledger(), _kospi(1000.0, 1010.0)[:1], D[1], semis=SEMIS)
    assert no_index.value is None and "코스피" in (no_index.reason or "")
    short = semi_skew_from_ledger(_skew_ledger(), _kospi(1.0, 1.0), D[1], semis=SEMIS, period=5)
    assert short.value is None
    miss = _skew_ledger({("A00003", D[0]): lrow("A00003", D[0], mktcap=None)})
    res2 = semi_skew_from_ledger(miss, _kospi(1000.0, 1010.0), D[1], semis=SEMIS)
    assert res2.value is None and "시총 결측" in (res2.reason or "")


# ── 경기민감 대 방어 ─────────────────────────────────────────────────────────────────────


def _ib(code: str, c0: float, c1: float) -> list[IndexBar]:
    return [
        IndexBar(code, D[0], None, c0, c0, c0, c0, None, None, "krx", Quality.OK),
        IndexBar(code, D[1], None, c1, c1, c1, c1, None, None, "krx", Quality.OK),
    ]


def test_경기민감_대_방어() -> None:
    bars = {
        "C001": _ib("C001", 100, 102),
        "C002": _ib("C002", 100, 104),
        "F001": _ib("F001", 100, 101),
    }
    res = cyclical_vs_defensive(bars, D[1], cyclical=["C001", "C002"], defensive=["F001"])
    assert res.value == pytest.approx(2.0)
    assert res.parts == pytest.approx({"cyclical": 3.0, "defensive": 1.0})
    pending = cyclical_vs_defensive(bars, D[1], cyclical=[], defensive=["F001"])
    assert pending.value is None and "[확인 필요]" in (pending.reason or "")
    missing = cyclical_vs_defensive(bars, D[1], cyclical=["X001"], defensive=["F001"])
    assert missing.value is None


# ── 세션 ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("now", "label", "until"),
    [
        (_kst(2026, 10, 7, 8, 0), "장 시작 전", _kst(2026, 10, 7, 9)),
        (_kst(2026, 10, 7, 10, 0), "정규장", _kst(2026, 10, 7, 15, 30)),
        (_kst(2026, 10, 7, 16, 0), "장 마감", _kst(2026, 10, 7, 18)),
        (_kst(2026, 10, 7, 20, 0), "야간장", _kst(2026, 10, 8, 6)),
        (_kst(2026, 10, 8, 2, 0), "야간장", _kst(2026, 10, 8, 6)),
        (_kst(2026, 10, 8, 16, 0), "장 마감", _kst(2026, 10, 12, 9)),  # 금 휴장 앞 목요일 밤 없음
        (_kst(2026, 10, 10, 12, 0), "휴장", _kst(2026, 10, 12, 9)),
    ],
)
def test_세션_칩(now: datetime, label: str, until: datetime) -> None:
    chip = session_chip(now, CAL)
    assert chip.value is not None and chip.value.value == label
    assert chip.tier == "public" and chip.value.quality is Quality.OK
    assert datetime.fromisoformat(chip.detail["until"]) == until
    assert chip.detail["remaining_s"] == int((until - now).total_seconds())


def test_세션_naive_거부() -> None:
    with pytest.raises(ValueError):
        session_chip(datetime(2026, 10, 7, 10, 0), CAL)  # noqa: DTZ001


# ── 띠 전체 ─────────────────────────────────────────────────────────────────────────────


def test_띠_순서_등급_준비_중() -> None:
    m = generate(2)
    now = _kst(2026, 10, 7, 10, 30)
    turnover = Sourced[int](value=5, source="KIS", as_of=now, quality=Quality.ESTIMATED)
    newhigh = Sourced[int](value=12, source="KIS", as_of=now, quality=Quality.ESTIMATED)
    chips = ribbon(
        RibbonInputs(
            turnover=turnover,
            turnover_ratio=1.2,
            investors=market_investor_totals(m.market_investors),
            newhigh=newhigh,
            newhigh_universe=2500,
            next_mpc=date(2026, 10, 22),
        ),
        now,
        CAL,
    )
    assert [c.key for c in chips] == list(CHIP_ORDER)
    by = {c.key: c for c in chips}
    assert by["bok_mpc"].label == "금통위 D-15" and by["bok_mpc"].tier == "public"
    assert by["bok_mpc"].value is not None and by["bok_mpc"].value.value == 15
    mt = by["market_turnover"]
    assert mt.tags == ("장중(지수 기준)", "NXT 미포함") and mt.tier == "login"
    assert mt.detail["ratio_avg20"] == 1.2
    fs = by["foreign_spot"]
    assert fs.value is not None and fs.tags == ("마감",) and fs.value.as_of.hour == 15
    assert by["newhigh_count"].value == newhigh
    assert by["semi_skew"].value is None and by["semi_skew"].note
    assert by["cyc_def"].value is None and "준비 중" in (by["cyc_def"].note or "")
    for key, (_, tier, phase) in PENDING.items():
        assert by[key].value is None and by[key].phase_pending == phase and by[key].tier == tier


def test_값이_없으면_None_과_사유() -> None:
    chips = {c.key: c for c in ribbon(RibbonInputs(), _kst(2026, 10, 7, 10), CAL)}
    for key in ("market_turnover", "foreign_spot", "inst_spot", "newhigh_count", "bok_mpc"):
        assert chips[key].value is None and chips[key].note
    assert chips["session"].value is not None

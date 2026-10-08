"""시장폭 — unknown·SMA20 분모·신고가·상하한가(docs/metrics.md §6.2·§6.7)."""

from __future__ import annotations

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import Label
from kbj.engines.board.aggregate import breadth as board_breadth
from kbj.engines.flows.ledger import build_ledger
from kbj.engines.market.breadth import market_breadth
from tests.fixtures.synthetic.ledger_gen import generate, ledger_inputs
from tests.unit.engines.flows.flow_rows import days, ledger_of, lrow

D = days(21)
LAST = D[-1]


def _label(code: str, kind: str, basis: str = "close", d=LAST) -> Label:
    return Label(code, d, basis, kind, 1, "kis", Quality.ESTIMATED)  # type: ignore[arg-type]


def test_값이_없거나_invalid_면_unknown() -> None:
    rows = [
        lrow("A00001", LAST, chg_pct=1.0),
        lrow("A00002", LAST, chg_pct=0.0),
        lrow("A00003", LAST, chg_pct=-2.0),
        lrow("A00004", LAST, chg_pct=None),
        lrow("A00005", LAST, chg_pct=5.0, quality=Quality.INVALID),
        lrow("A00006", LAST, chg_pct=3.0, kind="etf"),  # 주식 아님 — 대상 밖
    ]
    b = market_breadth(ledger_of(rows, D), LAST, [])
    assert (b.up, b.flat, b.down, b.unknown, b.total) == (1, 1, 1, 2, 3)
    assert any("invalid 1행" in n for n in b.notes)


def test_보드_breadth_를_그대로_쓴다() -> None:
    m = generate(13)
    led = build_ledger(**ledger_inputs(m))  # type: ignore[arg-type]
    b = market_breadth(led, m.last_day, [])
    rows = [
        r for r in led.day(m.last_day)
        if r.market in ("KOSPI", "KOSDAQ") and r.kind in ("common", "pref", "spac")
    ]  # fmt: skip
    want = board_breadth([{"chg_pct": r.chg_pct} for r in rows])
    assert (b.up, b.flat, b.down, b.unknown) == (
        want["up"], want["flat"], want["down"], want["unknown"]
    )  # fmt: skip


def test_20일선_위_비율_20봉_미만은_분모에서_뺀다() -> None:
    rows = [lrow("A00001", d, close=100.0) for d in D[1:-1]]
    rows.append(lrow("A00001", LAST, close=200.0))  # 20봉 — 위
    rows += [lrow("A00002", d, close=100.0) for d in D[1:]]  # 20봉 — 같음(위 아님)
    rows += [lrow("A00003", d, close=100.0) for d in D[5:]]  # 16봉 — 분모 밖
    b = market_breadth(ledger_of(rows, D), LAST, [])
    assert (b.above_ma20_n, b.ma20_base) == (1, 2)
    assert b.above_ma20_pct == 50.0


def test_SMA20_계산_불가면_None() -> None:
    b = market_breadth(ledger_of([lrow("A00001", LAST)], D), LAST, [])
    assert b.above_ma20_pct is None and b.ma20_base == 0


def test_신고가는_종가_기준_52주_이상만() -> None:
    labels = [
        _label("A00001", "w52"),
        _label("A00002", "hist"),
        _label("A00003", "d120"),  # 120일은 52주 이상이 아니다(ADR 0017 — 옛 d60 자리)
        _label("A00004", "w52", basis="high"),
        _label("A00005", "w52", d=D[0]),
    ]
    b = market_breadth(ledger_of([lrow("A00001", LAST)], D), LAST, labels, board_universe=40)
    assert b.newhigh_n == 2
    assert b.newhigh_pct == pytest.approx(5.0)
    assert b.quality is Quality.ESTIMATED  # 당일 보드는 잠정
    assert market_breadth(ledger_of([lrow("A00001", LAST)], D), LAST, labels).newhigh_pct is None


def test_상한가_하한가() -> None:
    rows = [
        lrow("A00001", LAST, chg_pct=29.9),
        lrow("A00002", LAST, chg_pct=29.5),
        lrow("A00003", LAST, chg_pct=29.4),
        lrow("A00004", LAST, chg_pct=-29.6),
    ]
    b = market_breadth(ledger_of(rows, D), LAST, [])
    assert (b.limit_up, b.limit_down) == (2, 1)
    with pytest.raises(ValueError):
        market_breadth(ledger_of(rows, D), LAST, [], limit_move_pct=0)

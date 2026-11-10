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


# ── 시장별 N일선 위 비율(§6.2 — 2026-11-10 첫 화면) ─────────────────────────────────────


def _bar(code: str, d, close: float | None, q: Quality = Quality.OK):  # type: ignore[no-untyped-def]
    from kbj.core.rows import Bar

    return Bar(code, d, close, close, close, close, None, None, "krx", "KRX", q)


def test_ma_breadth_fills_old_days_from_history_and_splits_markets() -> None:
    from kbj.engines.market.breadth import ma_breadth

    d = days(121)
    led_days = d[-60:]  # 원장 창 60일 — 그 앞은 일봉 이력
    last = d[-1]
    rows = [lrow("K00001", x, close=100.0) for x in led_days[:-1]]
    rows.append(lrow("K00001", last, close=150.0))  # 20·60·120 위
    rows += [lrow("K00002", x, close=100.0) for x in led_days[:-1]]
    rows.append(lrow("K00002", last, close=101.0))  # 20·60 위, 120 아래(옛 종가 200)
    rows += [lrow("Q00001", x, close=100.0, market="KOSDAQ") for x in led_days[:-1]]
    rows.append(lrow("Q00001", last, close=90.0, market="KOSDAQ"))  # 모두 아래
    rows += [lrow("Q00002", x, close=100.0, market="KOSDAQ") for x in led_days[-30:]]  # 30봉
    rows += [lrow("E00001", x, close=100.0, kind="etf") for x in led_days]  # 주식 아님
    hist = {
        "K00001": [_bar("K00001", x, 100.0) for x in d[:-60]],
        "K00002": [_bar("K00002", x, 200.0) for x in d[:-60]],
        # 원장 창 안 날짜의 일봉은 원장이 이긴다(이력 값 무시)
        "Q00001": [_bar("Q00001", x, 100.0) for x in d[:-60]] + [_bar("Q00001", last, 1.0)],
        "Q00002": [_bar("Q00002", x, 100.0, Quality.INVALID) for x in d[:-60]],
    }
    out = ma_breadth(ledger_of(rows, led_days), last, hist)
    by = {m.market: {s.ma: (s.above, s.base) for s in m.shares} for m in out}
    assert by["KOSPI"] == {20: (2, 2), 60: (2, 2), 120: (1, 2)}
    # Q00002: 30봉 — 20일선만 분모(같음 → 위 아님), 60·120 은 분모 밖(invalid 이력은 없는 것)
    assert by["KOSDAQ"] == {20: (0, 2), 60: (0, 1), 120: (0, 1)}
    k = next(m for m in out if m.market == "KOSPI")
    assert [s.pct for s in k.shares] == [100.0, 100.0, 50.0] and k.date == last
    assert k.quality == Quality.OK and k.source and not k.notes


def test_ma_breadth_twenty_matches_market_breadth_and_short_axis_note() -> None:
    from kbj.engines.market.breadth import ma_breadth

    m = generate(13)
    led = build_ledger(**ledger_inputs(m))  # type: ignore[arg-type]
    b = market_breadth(led, m.last_day, [])
    out = ma_breadth(led, m.last_day, {})
    assert sum(x.shares[0].above for x in out) == b.above_ma20_n
    assert sum(x.shares[0].base for x in out) == b.ma20_base
    assert all(x.shares[2].base == 0 and x.shares[2].pct is None for x in out)
    assert all(any("120일선" in n for n in x.notes) for x in out)


def test_updown_by_market_sums_to_combined() -> None:
    m = generate(13)
    led = build_ledger(**ledger_inputs(m))  # type: ignore[arg-type]
    both = market_breadth(led, m.last_day, [])
    parts = [market_breadth(led, m.last_day, [], markets=(x,)) for x in ("KOSPI", "KOSDAQ")]
    for f in ("up", "flat", "down", "unknown", "total"):
        assert sum(getattr(p, f) for p in parts) == getattr(both, f), f

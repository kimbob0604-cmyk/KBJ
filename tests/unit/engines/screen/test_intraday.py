"""cis 30분봉 엔진(kbj.engines.screen.intraday) — 손으로 따라갈 수 있는 합성 5분봉."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta

import pytest

from kbj.config.screen import load_screen
from kbj.core.intraday_rows import IntraBar
from kbj.core.time import KST
from kbj.engines.screen.backtest import Signal
from kbj.engines.screen.intraday import Watch, bars30, fetch_pairs, run_intraday, watch_days

CFG = load_screen().cis30
DAYS = [date(2026, 10, 19) + timedelta(days=k) for k in range(5)]  # 월~금
SLOTS = (
    [time(9, 0)]
    + [
        (datetime(2000, 1, 3, 9, 0, tzinfo=KST) + timedelta(minutes=5 * k)).time()
        for k in range(1, 78)
    ]
    + [time(15, 30)]
)  # 09:00 ~ 15:25 + 15:30 = 79개

Path = Callable[[int], tuple[float, float, float, float, int]]  # 5분봉 순번 → o,h,l,c,v


def flat(k: int) -> tuple[float, float, float, float, int]:
    return 10_000, 10_010, 9_990, 10_000, 1_000


def _day(code: str, d: date, path: Path) -> list[IntraBar]:
    out = []
    for k, t in enumerate(SLOTS):
        o, h, lo, c, v = path(k)
        out.append(IntraBar(code, datetime.combine(d, t, KST), d, o, h, lo, c, v))
    return out


def breakout(after: Path) -> Path:
    """10:00~10:30 봉(5분봉 12~17)에서 10,000 → 10,300 거래량 실어 돌파, 그 뒤는 after."""

    def f(k: int) -> tuple[float, float, float, float, int]:
        if k < 12:
            return flat(k)
        if k < 18:
            p = 10_000 + 50 * (k - 11)
            return p - 50, p + 5, p - 55, p, 10_000
        return after(k)

    return f


def _run(days_paths: list[Path], **upd: object):
    bars = [b for d, p in zip(DAYS, days_paths, strict=False) for b in _day("000100", d, p)]
    watch = {DAYS[1]: [Watch("000100", DAYS[0], 4.0)], DAYS[2]: [Watch("000100", DAYS[0], 4.0)]}
    cfg = CFG.model_copy(update=upd)
    return run_intraday({"000100": bars}, watch, DAYS[1 : len(days_paths)], cfg, calendar=DAYS)


def test_30분봉_묶기() -> None:
    b30 = bars30(_day("000100", DAYS[0], flat))
    assert len(b30) == 13 and b30[0].slot == 0 and b30[-1].last_slot
    assert b30[-1].end - b30[-1].first == 6  # 15:00~15:25 여섯 개 + 15:30 동시호가


def test_돌파하면_다음_봉_시가에_사고_2퍼_손절() -> None:
    def drop(k: int) -> tuple[float, float, float, float, int]:
        return 10_300, 10_310, 10_000, 10_050, 2_000  # 저가가 손절가(10,300 × 0.98 = 10,094) 아래

    res = _run([flat, breakout(drop)])
    t = res.trades[0]
    assert res.signals == 1 and t.entry_date == DAYS[1] and t.entry_px == 10_300
    assert t.reason == "stop" and t.exit_px == pytest.approx(10_300 * 0.98) and t.days == 1


def test_추적_손절() -> None:
    def run_up(k: int) -> tuple[float, float, float, float, int]:
        if k < 30:
            p = 10_300 + 40 * (k - 17)
            return p, p + 10, p - 10, p + 5, 2_000
        return 10_500, 10_510, 10_200, 10_250, 2_000  # 최고가 −4% 아래로

    res = _run([flat, breakout(run_up)], add_frac=0.0)
    t = res.trades[0]
    assert t.reason == "trail" and t.exit_px > t.entry_px


def test_당일_매매면_그날_종가() -> None:
    hold: Path = lambda k: (10_300, 10_310, 10_290, 10_300, 2_000)  # noqa: E731
    res = _run([flat, breakout(hold)], overnight=False)
    assert res.trades[0].reason == "eod" and res.trades[0].exit_px == 10_300


def test_보유_기한() -> None:
    hold: Path = lambda k: (10_300, 10_310, 10_290, 10_300, 2_000)  # noqa: E731
    res = _run([flat, breakout(hold), hold, hold], max_hold_days=2)
    t = res.trades[0]
    assert t.reason == "hold" and t.exit_date == DAYS[2] and t.days == 2


def test_거래량이_약하거나_첫_봉이면_안_산다() -> None:
    def weak(k: int) -> tuple[float, float, float, float, int]:
        o, h, lo, c, _ = breakout(flat)(k)
        return o, h, lo, c, 1_000

    assert _run([flat, breakout(flat)], vol_mult=99).trades == ()
    assert _run([flat, weak]).signals == 0

    def opening(k: int) -> tuple[float, float, float, float, int]:
        if k < 6:
            p = 10_000 + 50 * (k + 1)
            return p - 50, p + 5, p - 55, p, 10_000
        return 10_300, 10_310, 10_290, 10_300, 1_000

    assert _run([flat, opening]).signals == 0  # 09:00 봉 돌파


def test_불타기() -> None:
    def run_up(k: int) -> tuple[float, float, float, float, int]:
        p = 10_300 + 30 * (k - 17)
        return p, p + 5, p - 5, p + 3, 2_000

    res = _run([flat, breakout(run_up)])
    assert res.trades[0].adds == 1


def test_감시·받을_날() -> None:
    sig = {
        DAYS[0]: [Signal(DAYS[0], "000100", 4.0, 10_000)],
        DAYS[2]: [Signal(DAYS[2], "000200", 5.0, 1)],
    }
    w = watch_days(sig, DAYS, 2)
    assert [x.code for x in w[DAYS[1]]] == ["000100"] and {x.code for x in w[DAYS[3]]} == {"000200"}
    assert DAYS[0] not in w
    pairs = fetch_pairs(sig, DAYS, 2)
    assert pairs[0] == ("000100", DAYS[0]) and ("000200", DAYS[4]) in pairs and len(pairs) == 6

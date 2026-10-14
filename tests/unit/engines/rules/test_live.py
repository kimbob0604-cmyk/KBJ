"""`kbj.engines.rules.live` — 슬롯 시세 → 부분 봉, 우니버스·대상 자르기, 보유, 신호 키(묶음 EVL)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import Bar, Snap
from kbj.core.rules_rows import TradeRow
from kbj.core.time import KST
from kbj.engines.rules.dsl import Rule, validate_rule
from kbj.engines.rules.live import (
    LIMIT_PCT,
    close_eval_ts,
    holdings,
    limit_flags,
    select_targets,
    signal_id,
    slot_from_quote,
    universe_codes,
)

TS = datetime(2026, 10, 27, 10, 20, tzinfo=KST)
D = date(2026, 10, 27)


def _snap(chg: float | None = 1.0, flags: tuple[str, ...] | None = ()) -> Snap:
    return Snap(
        code="Q80015",
        date=D,
        name=None,
        market="KOSDAQ",
        kind=None,
        close=12_853.0,
        chg_pct=chg,
        volume=1_000,
        turnover=None,
        turnover_is_estimate=True,
        mktcap=None,
        shares=None,
        status_flags=flags,
        source="kis",
        venue="KRX",
        quality=Quality.ESTIMATED,
    )


def _bar() -> Bar:
    return Bar(
        code="Q80015",
        date=D,
        open=12_000.0,
        high=12_900.0,
        low=11_950.0,
        close=12_853.0,
        volume=1_000,
        turnover=None,
        source="kis",
        venue="KRX",
        quality=Quality.ESTIMATED,
    )


def _rule(universe: dict[str, object], mode: str = "intraday", prio: int = 2) -> Rule:
    return validate_rule(
        {
            "v": 1,
            "name": "t",
            "universe": universe,
            "mode": mode,
            "when": {"all": [{"ind": "change_pct", "op": ">", "value": 0}]},
            "alert": {"priority": prio},
        }
    )


def test_limit_flags_threshold() -> None:
    assert limit_flags(None) == ()
    assert limit_flags(LIMIT_PCT) == ("upper_limit",)
    assert limit_flags(-LIMIT_PCT) == ("lower_limit",)
    assert limit_flags(LIMIT_PCT - 0.01) == ()


def test_slot_from_quote_keeps_values_estimated_and_flags() -> None:
    s = slot_from_quote(_snap(chg=29.9, flags=("halted",)), _bar(), slot_ts=TS, received_at=TS)
    assert (s.open, s.high, s.low, s.price, s.volume) == (
        12_000.0,
        12_900.0,
        11_950.0,
        12_853.0,
        1_000,
    )
    assert s.quality is Quality.ESTIMATED
    assert s.flags == ("halted", "upper_limit")
    assert s.source == "KIS:watch_quotes_intraday"
    assert s.slot_ts == TS


def test_slot_from_quote_never_upgrades_quality_and_unknown_status() -> None:
    snap = _snap(flags=None)
    s = slot_from_quote(snap, _bar(), slot_ts=TS)
    assert s.flags == ()  # 상태를 모르면 플래그 없음(지어내지 않는다)
    with pytest.raises(ValueError, match="종목"):
        slot_from_quote(
            snap,
            Bar("Q80016", D, 1.0, 1.0, 1.0, 1.0, 1, None, "kis", "KRX", Quality.OK),
            slot_ts=TS,
        )


def test_universe_codes_each_kind() -> None:
    wl = {"main": ["Q80003", "Q80001", "Q80003"]}
    assert universe_codes(_rule({"watchlist": "main"}), watchlists=wl, holdings=[]) == [
        "Q80003",
        "Q80001",
    ]
    assert universe_codes(_rule({"watchlist": "other"}), watchlists=wl, holdings=[]) == []
    assert universe_codes(_rule({"codes": ["Q80009"]}), watchlists=wl, holdings=[]) == ["Q80009"]
    assert universe_codes(_rule({"portfolio": True}), watchlists=wl, holdings=["Q80007"]) == [
        "Q80007"
    ]
    mk = {"KOSPI": ["Q80001"], "KOSDAQ": ["Q80002"]}
    r = _rule({"market": "ALL"}, mode="close")
    assert universe_codes(r, watchlists=wl, holdings=[], market=mk) == ["Q80001", "Q80002"]
    with pytest.raises(ValueError, match="마감"):
        universe_codes(r, watchlists=wl, holdings=[])


def test_select_targets_orders_by_priority_then_position_and_truncates() -> None:
    entries = [(2, ["A00001", "A00002", "A00003"]), (1, ["A00003", "A00004"])]
    t = select_targets(entries, 3)
    assert t.codes == ("A00003", "A00004", "A00001")  # 우선순위 1 규칙의 종목이 먼저
    assert set(t.codes) | set(t.truncated) == {"A00001", "A00002", "A00003", "A00004"}
    assert t.truncated == ("A00002",)
    assert select_targets(entries, 60).truncated == ()
    with pytest.raises(ValueError):
        select_targets(entries, 0)


def _trade(code: str, side: Literal["buy", "sell"], d: date, qty: int, i: int) -> TradeRow:
    return TradeRow(
        i,
        code,
        None,
        side,
        d,
        Decimal("100"),
        qty,
        Decimal("0"),
        Decimal("0"),
        None,
        None,
        None,
        "api",
        TS,
    )


def test_holdings_net_quantity_and_since() -> None:
    d0 = date(2026, 1, 5)
    trades = [
        _trade("Q80006", "buy", d0, 10, 1),
        _trade("Q80006", "sell", d0 + timedelta(days=3), 10, 2),  # 전량 매도
        _trade("Q80006", "buy", d0 + timedelta(days=10), 5, 3),  # 다시 보유 — 시작일이 바뀐다
        _trade("Q80007", "buy", d0, 3, 4),
        _trade("Q80007", "sell", d0 + timedelta(days=1), 1, 5),
        _trade("Q80008", "buy", d0 + timedelta(days=40), 1, 6),  # upto 뒤
    ]
    h = holdings(trades, d0 + timedelta(days=20))
    assert h == {"Q80006": d0 + timedelta(days=10), "Q80007": d0}


def test_close_eval_ts_and_signal_id_deterministic() -> None:
    ts = close_eval_ts(D)
    assert ts == datetime(2026, 10, 27, 15, 30, tzinfo=KST)
    a = signal_id("close", ts, "r_aaaaaaaaaaaa", 1, "Q80010")
    assert a == signal_id("close", ts, "r_aaaaaaaaaaaa", 1, "Q80010")
    assert a != signal_id("close", ts, "r_aaaaaaaaaaaa", 2, "Q80010")
    assert re.fullmatch(r"sc_[0-9A-HJKMNP-TV-Z]{26}", a)
    b = signal_id("intraday", TS, "r_aaaaaaaaaaaa", 1, "Q80010")
    assert b.startswith("si_")
    # ULID 시각 부분이 시간 순서를 지킨다
    c = signal_id("intraday", TS + timedelta(minutes=10), "r_aaaaaaaaaaaa", 1, "Q80010")
    assert b[3:13] < c[3:13]
    with pytest.raises(ValueError):
        signal_id("close", datetime(2026, 1, 1), "r_aaaaaaaaaaaa", 1, "Q80010")  # noqa: DTZ001

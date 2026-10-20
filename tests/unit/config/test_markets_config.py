"""config/markets.yaml·calendar_events.yaml 해석(kbj.config.markets — docs/p3_design.md §3.9)."""

from __future__ import annotations

import copy
import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.config.files import ConfigFileError
from kbj.config.markets import (
    load_calendar_events,
    load_markets,
    parse_calendar_events,
    parse_markets,
)
from kbj.config.settings import Settings

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.upper().startswith("KBJ_"):
            monkeypatch.delenv(name)


def settings() -> Settings:
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def raw(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


def test_repo_markets_has_the_design_defaults() -> None:
    m = load_markets(settings=settings())
    assert m.kis.venues == ("KRX",)  # D-P3-9
    assert m.screen.min_avg_turnover_krw == 30_000_000_000  # 300억(metrics §3)
    assert (m.screen.spike_min, m.screen.spike_min_prior_days, m.screen.streak_min) == (1.5, 10, 3)
    p = m.etf.premium_warn_pct  # KRX 괴리율 관리 기준(국내 1%·해외 2% — 2026-10-20)
    assert (p.default, p.overseas, p.bond) == (1.0, 2.0, 1.0) and m.etf.watch_top_n == 50
    assert m.etf.investor_min_net_asset_krw == 100_000_000_000
    h = m.etf.holdings
    assert (h.qty_floor, h.action_pp, h.max_gap_days) == (100, 2.0, 14)  # ET tracker.py 그대로
    assert m.etf.split_tol == 0.02 and 10 in m.etf.split_ratios
    assert m.reconcile.close_tol_pct == 0.0 and m.reconcile.turnover_tol_pct == 0.5
    assert set(m.market.intraday_indices) == {"0001", "1001", "2001"}
    # KRX 지수 일별의 공백 뺀 이름(2026-10-18 DB 실측) — 이름이 겹치지 않는다
    assert len(m.market.sector_indices) == 46 and "코스닥건설" in m.market.sector_indices
    assert m.ribbon.semis == ("005930", "000660")


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("kis", "venues"), ["KRX", "KRX"], "겹친다"),
        (("kis", "venues"), ["NASDAQ"], "kis"),
        (("screen", "spike_min"), 1.0, "spike_min"),
        (("screen", "min_avg_turnover_krw"), 1.5e10, "min_avg_turnover_krw"),
        (("etf", "split_ratios"), [1, 10], "split_ratios"),
        (("market", "sector_indices"), ["코스 피"], "공백"),
        (("market", "sector_indices"), ["건설", "건설"], "겹친다"),
        (("etf", "holdings", "qty_flor"), 100, "qty_flor"),  # 모르는 키(철자)
    ],
)
def test_bad_markets_values_fail_loudly(path: tuple[str, ...], value: object, match: str) -> None:
    data = copy.deepcopy(raw("markets.yaml"))
    node = data
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    with pytest.raises(ConfigFileError, match=match):
        parse_markets(data)


def test_calendar_events_are_sorted_and_upcoming() -> None:
    ev = load_calendar_events(settings=settings())
    assert all(e.kind == "bok_mpc" for e in ev.events) and len(ev.events) == 8
    nxt = ev.upcoming(date(2026, 10, 7))
    assert nxt is not None and nxt.date == date(2026, 10, 22)
    assert ev.upcoming(date(2026, 10, 22)) == nxt  # 당일 포함
    assert ev.upcoming(date(2026, 11, 27)) is None  # 2027 일정은 아직 없다 — 지어내지 않는다


def test_bad_calendar_events() -> None:
    data = raw("calendar_events.yaml")
    unsorted = copy.deepcopy(data)
    unsorted["events"].reverse()
    with pytest.raises(ConfigFileError, match="날짜 순"):
        parse_calendar_events(unsorted)
    dup = copy.deepcopy(data)
    dup["events"].insert(1, dup["events"][0])
    with pytest.raises(ConfigFileError, match="두 번"):
        parse_calendar_events(dup)
    other = copy.deepcopy(data)
    other["events"][0]["kind"] = "fomc"
    with pytest.raises(ConfigFileError):
        parse_calendar_events(other)

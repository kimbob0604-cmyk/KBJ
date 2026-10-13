"""합성 P5 세계(`tests/fixtures/synthetic/p5_world.py`)·기대값(`p5_expect.yaml`)의 시험.

근거: docs/p5_design.md §1.1(FND — 결정성·`_source: SYNTHETIC`·검산 ①~④ 합성 원장 0 차이)·부록 A.
`tests/fixtures/` 아래에는 시험 파일을 두지 않는다 — 그래서 여기(tests/unit/fixtures)에 둔다.
"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.config.macro import load_macro
from kbj.core.p5_rows import MINUTE_INVESTORS
from kbj.core.quality import Quality
from tests.fakes.kis_server import SYMBOLS
from tests.fixtures.synthetic.p4_world import build_world as build_p4
from tests.fixtures.synthetic.p5_world import (
    CLOCK,
    LAST_KRX_DAY,
    MINUTE_PAIRS,
    SEED,
    STOCK_CODES,
    SYNTHETIC,
    TODAY,
    P5World,
    build_world,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic"
EXPECT = FIX / "p5_expect.yaml"


@pytest.fixture(scope="module")
def w() -> P5World:
    return build_world()


@pytest.fixture(scope="module")
def expect() -> dict[str, Any]:
    with EXPECT.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert isinstance(doc, dict)
    return doc  # pyright: ignore[reportUnknownVariableType]


# ── 결정성·합성 표시·코드 대역 ─────────────────────────────────────────────


def test_deterministic_and_seed_sensitive(w: P5World) -> None:
    assert build_world() is w  # 캐시
    assert w.seed == SEED
    other = build_world(SEED + 1)
    assert other.digest() != w.digest()
    build_world.cache_clear()
    again = build_world()
    assert again.digest() == w.digest()  # 새로 만들어도 같은 바이트
    # 구조(종목·심은 충돌·검산 항등식)는 시드와 무관
    assert [s.code for s in other.stocks] == [s.code for s in w.stocks]
    assert other.minute_checks() == {"c1": 0, "c2": 0, "c3": 0}
    assert other.program_checks() == {"c4": 0}


def test_every_record_is_marked_synthetic(w: P5World, expect: dict[str, Any]) -> None:
    n = 0
    for rec in w.records():
        assert rec["_source"] == SYNTHETIC, rec.get("kind")
        n += 1
    assert n > 30_000
    assert SYNTHETIC.startswith("SYNTHETIC")
    assert str(expect["_source"]).startswith("SYNTHETIC")
    assert expect["seed"] == SEED and expect["clock"] == CLOCK.isoformat()


def test_codes_do_not_overlap_other_fakes(w: P5World, expect: dict[str, Any]) -> None:
    mine = {s.code for s in w.stocks} | {e.code for e in w.etfs}
    assert not mine & set(SYMBOLS)  # P3 가짜 KIS 대역
    p4 = build_p4()
    assert not mine & {s.code for s in p4.stocks}
    assert all(c.startswith("997") for c in mine)
    assert expect["stocks"] == {
        "n": len(STOCK_CODES),
        "first": STOCK_CODES[0],
        "last": STOCK_CODES[-1],
    }
    for f in FIX.rglob("*.json"):  # 다른 합성 fixture(P3 ETF·KRX 등)와도 겹치지 않는다
        text = f.read_text(encoding="utf-8", errors="ignore")
        assert not any(f'"{c}"' in text for c in mine), f.name


def test_krx_bars(w: P5World, expect: dict[str, Any]) -> None:
    e = expect["krx"]
    assert len(w.krx_days) == e["n_days"]
    assert w.krx_days[0].isoformat() == e["first_day"]
    assert w.krx_days[-1] == LAST_KRX_DAY == date.fromisoformat(e["last_day"])
    for s in w.stocks:
        bars = w.bars(s.code)
        assert [b.date for b in bars] == list(w.krx_days)
        assert all(b.low is not None and b.high is not None and b.low <= b.high for b in bars)
    assert TODAY not in w.krx_days  # KRX 는 D+1 공표 — 오늘 행은 아직 없다


# ── 매크로·캘린더 ─────────────────────────────────────────────────


def test_macro_series_ids_come_from_config_and_tiers_match(
    w: P5World, expect: dict[str, Any]
) -> None:
    cfg = load_macro()
    tier = {s.id: s.tier for s in cfg.series}
    for m in w.macro_meta:
        assert tier.get(m.series_id) == m.tier, m.series_id
    e = expect["macro"]
    assert sum(m.tier == "public" for m in w.macro_meta) == e["n_public"]
    assert sum(m.tier == "private" for m in w.macro_meta) == e["n_private"]
    pub = {r.source for r in w.macro_rows("public")}
    assert pub <= {"ECOS", "KOSIS", "FRED"}
    assert "YAHOO" in {r.source for r in w.macro_rows("private")}


def test_macro_missing_lag_and_derived(w: P5World, expect: dict[str, Any]) -> None:
    e = expect["macro"]
    for miss in e["missing"]:
        row = next(r for r in w.series(miss["series"]) if r.period == miss["period"])
        assert row.value is None and row.quality == Quality.INVALID
    for lag in e["lagged"]:
        assert w.series(lag["series"])[-1].period == lag["last_period"]
    assert w.expect_macro() == e["derived"]


def test_calendar(w: P5World, expect: dict[str, Any]) -> None:
    e = expect["calendar"]
    assert len(w.calendar) == e["n"]
    assert sum(ev.is_public for ev in w.calendar) == e["n_public"]
    assert [ev.kind for ev in w.calendar if not ev.is_public] == e["private_kinds"]
    assert all(ev.forecast is None for ev in w.calendar if ev.is_public)


# ── ETF·미국·금투협 ─────────────────────────────────────────────


def test_etf(w: P5World, expect: dict[str, Any]) -> None:
    e = expect["etf"]
    assert len(w.etfs) == e["n"]
    assert [d.isoformat() for d in w.holding_dates] == e["holding_dates"]
    assert w.expect_etf_consensus() == e["consensus"]
    prev_d, cur_d = w.holding_dates
    a = next(x for x in w.etfs if x.code == "997560")
    new_a = {h.code for h in w.holdings(a.fund_id, cur_d)} - {
        h.code for h in w.holdings(a.fund_id, prev_d)
    }
    assert set(e["single_new"]) < new_a  # A 만 담은 종목 — 동시 편입에서 빠진다
    assert not set(e["single_new"]) & set(e["consensus"])
    newest = [x.code for x in w.etfs if len(w.etf_days(x.code)) < 21]
    assert newest == e["new_listing"]
    assert [x.code for x in w.etfs if x.leverage is not None] == e["leveraged"]
    for x in w.etfs:
        for snap in (prev_d, cur_d):
            rows = w.holdings(x.fund_id, snap)
            if rows:
                assert abs(sum(r.wt or 0 for r in rows) - 100) < 0.01


def test_us(w: P5World, expect: dict[str, Any]) -> None:
    e = expect["us"]
    assert len(w.us_universe) == e["n_universe"]
    assert w.expect_us() == {k: e[k] for k in ("new_highs", "held", "funds")}
    assert all(w.us_bars(s)[-1].date.isoformat() == e["us_day"] for s in e["new_highs"])
    assert sorted(s for s in w._us_bars if s.startswith("^")) == e["indices"]  # pyright: ignore[reportPrivateUsage]


def test_kofia(w: P5World, expect: dict[str, Any]) -> None:
    e = expect["kofia"]
    assert len(w.market_stats) == e["rows"]
    for stat, day in e["last"].items():
        assert max(r.date for r in w.market_stats if r.stat == stat).isoformat() == day
    assert {r.source for r in w.market_stats} == {"DATAGO"}


# ── F2: 1분·프로그램(검산 ①~④ 합성 원장 0 차이) ─────────────────────


def test_minutes_shape_and_checks(w: P5World, expect: dict[str, Any]) -> None:
    e = expect["f2"]
    assert [p.id for p in MINUTE_PAIRS] == e["pairs"]
    assert len(MINUTE_INVESTORS) == e["investors"]
    rows = w.minutes()
    assert len(rows) == e["minute_rows"] == len(MINUTE_PAIRS) * e["minutes"] * e["investors"]
    stamps = sorted({m.ts for m in w.minutes("k200_fut")})
    assert len(stamps) == e["minutes"]
    assert stamps[0].time() == time(9, 0) and stamps[-1].time() == time(15, 30)
    assert all(m.trade_date.isoformat() == e["today"] for m in rows)
    checks = {**w.minute_checks(), **w.program_checks()}
    assert checks == e["checks"]
    # 누적값은 줄지 않는다(매수·매도 대금)
    last: dict[tuple[str, str], int] = {}
    for m in w.minutes("kospi"):
        key = (m.market_code, m.investor)
        assert (m.buy_value or 0) >= last.get(key, 0)
        last[key] = m.buy_value or 0


def test_minute_drop_variant_leaves_gaps(w: P5World) -> None:
    drop = {time(10, 0), time(10, 1)}
    rows = w.minutes("k200_fut", drop=drop)
    assert len(rows) == (391 - 2) * len(MINUTE_INVESTORS)
    assert not any(m.ts.time() in drop for m in rows)


def test_program_shape(w: P5World, expect: dict[str, Any]) -> None:
    e = expect["f2"]["program"]
    assert len(w.program_days) == e["daily_rows"]
    assert len(w.program_intraday) == e["intraday_rows"]
    assert len(w.program_investors) == e["investor_rows"]
    scan = [r for r in w.program_stocks if r.scope == "scan"]
    assert sorted({r.code for r in scan}) == e["scan_codes"]
    assert len(scan) == len(e["scan_codes"]) * e["scan_slots"]
    assert sum(r.scope == "sweep" for r in w.program_stocks) == e["sweep_rows"]
    assert w.expect_program_top() == e["top5"]
    assert {r.quality for r in scan} == {Quality.ESTIMATED}


def test_brief_materials(w: P5World, expect: dict[str, Any]) -> None:
    for day, want in expect["brief"].items():
        ms = w.materials_on(date.fromisoformat(day))
        assert ms
        assert sorted(m.name for m in ms if m.status == "late") == want["late"]
        assert sorted(m.name for m in ms if m.status == "missing") == want["missing"]
        for m in ms:
            assert (m.as_of is None) == (m.status == "missing")

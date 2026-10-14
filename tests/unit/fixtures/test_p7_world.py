"""`tests/fixtures/synthetic/p7_world.py` — 결정성·합성 표시·손계산 기대값(p7_expect.yaml) 대조.

P7 묶음(ENG·ING·DIAG·OUT·WRAP)이 같은 세계를 쓴다. 실데이터가 아니다(`_source: SYNTHETIC`).
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import yaml

from kbj.core.time import KST
from kbj.store.repos.gex import MemoryGexRepo
from tests.fixtures.synthetic.p7_world import SYN, build_world

ROOT = Path(__file__).resolve().parents[3]
EXPECT: dict[str, Any] = yaml.safe_load(
    (ROOT / "tests" / "fixtures" / "synthetic" / "p7_expect.yaml").read_text(encoding="utf-8")
)


def test_deterministic_and_marked_synthetic() -> None:
    a = build_world()
    b = build_world.__wrapped__()  # 캐시를 건너뛴 새 객체
    assert a.digest() == b.digest()
    assert build_world(20261202).digest() != a.digest()
    assert EXPECT["_source"].startswith("SYNTHETIC") and SYN.startswith("SYNTHETIC")
    assert all(r["_source"] == SYN for r in a.records())
    assert a.seed == EXPECT["seed"] and a.clock.isoformat() == EXPECT["clock"]


def test_counts_match_expect() -> None:
    w = build_world()
    c = EXPECT["counts"]
    assert len(w.days) == EXPECT["days"]["n"]
    assert (w.days[0].isoformat(), w.days[-1].isoformat()) == (
        EXPECT["days"]["first"], EXPECT["days"]["last"],
    )  # fmt: skip
    assert len(w.chain) == c["chain"]
    assert sum(r.quality == "invalid" for r in w.chain) == c["chain_invalid"]
    assert len(w.futures) == c["futures"] and len(w.expiries) == c["expiries"]
    assert len(w.investor) == c["investor"]
    assert sum(r.session == "night" for r in w.investor) == c["investor_night"]
    assert len(w.levels) == c["levels"] and len(w.metrics) == c["metrics"]
    assert sum(m.flag == "shadow" for m in w.metrics) == c["metrics_shadow"]
    assert len(w.strike_gex) == c["strike_gex"]
    assert len(w.events) == c["events_with_trade"]
    assert len(build_world(with_trade=False).events) == c["events_without_trade"]
    assert len(w.series["export_yoy"]) == c["export_flash_points"]
    assert "export_yoy" not in build_world(with_trade=False).series


def test_engine_outputs_and_repo_seed() -> None:
    w = build_world()
    cycles = [datetime.combine(w.clock.date(), time.fromisoformat(t), tzinfo=KST)
              for t in EXPECT["cycles"]]  # fmt: skip
    assert [c.astimezone(KST) for c in sorted({r.ts for r in w.levels})] == cycles
    flips = [lv.value for lv in sorted(w.levels, key=lambda r: r.ts)
             if lv.scope == "all" and lv.name == "flip"]  # fmt: skip
    assert flips == EXPECT["flip_all"]
    last_net = max((m for m in w.metrics if m.metric == "net_gex"), key=lambda m: m.ts)
    assert last_net.value == EXPECT["net_gex_last"]
    repo = MemoryGexRepo()
    w.fill_gex(repo)
    rows = repo.chain_latest(w.clock.date(), "day", w.clock)
    assert len(rows) == EXPECT["chain_latest_day_rows"]
    assert all(r.quality != "invalid" for r in rows)
    assert [lv.value for lv in repo.levels_latest("all") if lv.name == "flip"] == [350.5]
    assert repo.flags_of(max(w.cycles))["vex"] == "shadow"
    # 야간 투자자별 없음(D-P7-6) — 저장소에서도
    assert repo.investor_latest(w.clock.date(), "night", w.clock) == []
    combos = Counter((r.market_code, r.sector_code) for r in w.investor)
    assert len(combos) == 7


def test_series_point_in_time_and_planted_gaps() -> None:
    w = build_world()
    s = EXPECT["series"]
    assert sorted(k for k in w.series if k != "export_yoy") == sorted(s["keys"])
    assert sum(p.value is None for p in w.series["vkospi"]) == s["vkospi_missing"]
    late = [p for p in w.series["credit_balance"]
            if (p.published_at.date() - p.day).days >= 2 and p.published_at.weekday() < 5
            and p.published_at.date() > w.days[w.days.index(p.day) + 1]]  # fmt: skip
    assert len(late) == s["credit_late"]
    # 시점 입력: 오늘 14:00 엔 오늘 신용스프레드(18:00 공표)·어제 신용잔고(오늘 13:00) 여부
    cs = w.as_of("credit_spread", w.clock)
    assert cs[-1].day < w.clock.date()
    first_pub = w.series["credit_spread"][0].published_at
    assert first_pub.astimezone(KST).time() == time(18, 0)
    assert w.series["vkospi"][0].published_at.astimezone(KST).time() == time(8, 0)


def test_events_match_expect() -> None:
    w = build_world()
    by: dict[str, list[str]] = {}
    for e in w.events:
        by.setdefault(e.kind, []).append(e.day.isoformat())
    ev = EXPECT["events"]
    assert sorted(by["expiry"]) == ev["expiry"]
    assert by["mpc"] == ev["mpc"]
    assert by["export_flash"] == ev["export_flash"]
    assert {d.isoformat(): n for d, n in w.earnings_counts.items() if n >= 10} == ev[
        "earnings_cluster"
    ]
    assert w.clock.date() == date(2026, 12, 10)

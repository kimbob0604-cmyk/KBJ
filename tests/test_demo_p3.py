"""P3 데모(scripts/demo_p3_server.py) — 금액을 키운 합성 세계: 기본 화면이 차고 검산이 유지된다."""

from __future__ import annotations

import importlib.util
from collections import defaultdict
from pathlib import Path
from typing import Any

from kbj.core.rows import INST7, Investor
from tests.unit.api.api_world import build_world

ROOT = Path(__file__).resolve().parents[1]


def _demo() -> Any:
    path = ROOT / "scripts" / "demo_p3_server.py"
    spec = importlib.util.spec_from_file_location("demo_p3_server", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scaled_ledger_keeps_checks_1_and_2() -> None:
    demo = _demo()
    world = build_world(transform=demo.scale_market)
    by: dict[tuple[str, Any, str], dict[Investor, int]] = defaultdict(dict)
    for r in world.market.investors:
        if r.net_value is not None:
            by[(r.code, r.date, r.source)][r.investor] = r.net_value
    for vals in by.values():
        if Investor.INDIVIDUAL not in vals or Investor.OTHER_CORP not in vals:
            continue  # 장중 잠정(외국인·기관만)
        four = (
            vals[Investor.FOREIGN] + vals.get(Investor.FOREIGN_OTHER, 0)
            + vals[Investor.INSTITUTION] + vals[Investor.OTHER_CORP] + vals[Investor.INDIVIDUAL]
        )  # fmt: skip
        assert four == 0
        assert sum(vals[i] for i in INST7) == vals[Investor.INSTITUTION]


def test_default_screener_is_filled_at_demo_scale() -> None:
    """기본 필터(일평균 300억·5일·보통주 — config 그대로)로 데모 첫 화면에 종목이 나온다."""
    demo = _demo()
    world = build_world(transform=demo.scale_market)
    client = world.client()
    world.login(client)
    r = client.get(
        "/api/flows/screen",
        params={"mode": "value", "period": 5, "market": "all", "share_class": "common",
                "min_avg_turnover": 30_000_000_000, "include_flagged": "false"},
    )  # fmt: skip
    assert r.status_code == 200, r.text
    rows = r.json()["data"]["rows"]
    assert len(rows) >= 10
    top = rows[0]
    assert top["turnover_avg"] >= 100_000_000_000  # 상위 종목 일평균 1,000억 이상(실제 시장 규모)
    assert any(abs(x["foreign"] or 0) >= 10_000_000_000 for x in rows)  # 순매수 수백억 단위

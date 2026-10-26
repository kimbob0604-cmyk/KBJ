"""`screen` 프런트 시험 픽스처 — `web/test/fixtures/api/screen/**`(페이지 11 '스크리너').

P3 합성 세계(`tests.unit.api.api_world.build_world` — 고정 시드)에 마지막 거래일 거래량 급증·박스
돌파 종목 하나(`SPIKE_CODE`)와 윗꼬리 종목 하나(`WICK_CODE`)를 더해 `/api/screen/cis` 응답을 낸다.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Final

from kbj.config.screen import load_screen
from kbj.core.quality import Quality
from kbj.core.rows import Bar, Snap
from tests.unit.api.api_world import LOADED_BY, World, build_world

__all__ = ["DOMAIN", "SPIKE_CODE", "WICK_CODE", "add_spikes", "render"]

DOMAIN: Final = "screen"
SPIKE_CODE: Final = "990001"
WICK_CODE: Final = "990002"
_LAST: Final = {  # (시가, 고가, 저가, 종가) — 직전 창은 10,000 박스(고가 10,200)
    SPIKE_CODE: ("합성돌파", 10_100.0, 11_000.0, 10_050.0, 10_950.0),
    WICK_CODE: ("합성꼬리", 10_100.0, 12_000.0, 10_000.0, 10_500.0),
}


def add_spikes(world: World) -> date:
    """마지막 거래일에 거래량 6배인 두 종목을 더한다. 반환: 그 거래일."""
    cfg = load_screen().cis
    m = world.repos.market
    day = m.trading_days(world.market.last_day, 1)[0]
    days = sorted(m.trading_days(day, cfg.lookback_days + 1))
    bars: list[Bar] = []
    snaps: list[Snap] = []
    for code, (name, o, h, lo, c) in _LAST.items():
        bars += [
            Bar(code=code, date=d, open=10_000, high=10_200, low=9_800, close=10_000,
                volume=100_000, turnover=2_000_000_000, source="krx", venue="KRX",
                quality=Quality.OK)
            for d in days[:-1]
        ]  # fmt: skip
        bars.append(
            Bar(code=code, date=days[-1], open=o, high=h, low=lo, close=c, volume=600_000,
                turnover=int(c * 600_000), source="krx", venue="KRX", quality=Quality.OK)
        )  # fmt: skip
        snaps.append(
            Snap(code=code, date=day, name=name, market="KOSPI", kind="common", close=c,
                 chg_pct=(c / 10_000 - 1) * 100, volume=600_000, turnover=int(c * 600_000),
                 turnover_is_estimate=False, mktcap=None, shares=None, status_flags=(),
                 source="krx", venue="KRX", quality=Quality.OK)
        )  # fmt: skip
    m.upsert_daily_bars(bars, loaded_by=LOADED_BY)
    m.upsert_snapshots(snaps, loaded_by=LOADED_BY)
    return day


def _dump(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def render() -> dict[str, str]:
    world = build_world()
    add_spikes(world)
    c = world.client()
    world.login(c)
    r = c.get("/api/screen/cis")
    if r.status_code != 200:
        raise RuntimeError(f"/api/screen/cis: HTTP {r.status_code} {r.text[:200]}")
    return {"cis.json": _dump(r.json())}

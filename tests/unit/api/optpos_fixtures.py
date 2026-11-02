"""옵션 행사가별 추정 포지션 API 세계·프런트 픽스처(ADR 0044 — `web/test/fixtures/api/gex_optpos/`).

합성 5거래일 세계(`tests/fixtures/synthetic/opt_pos_world.py` — 고정 시드, KIS·KRX 원자료 아님)를
메모리 저장소(`repos.opt_pos`)에 싣고, 가짜 시계(2026-11-03 18:00 KST)에서 앱 응답을 낸다.
다시 만들기: `uv run python -m tests.unit.api.optpos_fixtures`
(시험 `test_optpos_fixtures_are_fresh`).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Final

from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.store.repos import memory_repos
from kbj.store.repos.opt_pos import OptPosDayRow, OptPosStrikeRow
from tests.fixtures.synthetic.opt_pos_world import WORLD_DAYS, world_rows
from tests.unit.api.api_world import FakeClock
from tests.unit.api.web_fixtures_p7.gex import GexApiWorld

__all__ = ["CLOCK", "FILES", "OUT", "optpos_api_world", "render"]

CLOCK: Final = datetime(2026, 11, 3, 18, 0, tzinfo=KST)
ROOT: Final = Path(__file__).resolve().parents[3]
OUT: Final = ROOT / "web" / "test" / "fixtures" / "api" / "gex_optpos"
FILES: Final[tuple[tuple[str, str], ...]] = (
    ("option_positions.json", "/api/gex/option-positions"),
    ("option_positions_prsn.json", "/api/gex/option-positions?investor=prsn"),
)


def optpos_api_world(*, empty: bool = False, krx: bool = True) -> GexApiWorld:
    repos = memory_repos()
    cal = TradingCalendar.default()
    if not empty:
        for inv in ("frgn", "prsn"):
            rows, stats, oi = world_rows(cal, inv)
            repos.opt_pos.strikes += [
                OptPosStrikeRow(r.trade_date, inv, r.mrkt_cls, r.expiry, r.strike, r.cp, r.buy,
                                r.sell, r.close, r.board_oi, r.delta,
                                None if r.delta is None else "self")
                for r in rows
            ]  # fmt: skip
            repos.opt_pos.days += [
                OptPosDayRow(s.trade_date, inv, s.market_code, s.sector_code, s.minutes,
                             s.gap_minutes, last_ts=s.last_ts, futures_net=s.futures_net,
                             fut_price=s.fut_price, reasons=s.reasons,
                             computed_at=CLOCK.replace(hour=16, minute=5))
                for s in stats
            ]  # fmt: skip
            if krx:
                repos.opt_pos.krx[WORLD_DAYS[-1]] = oi
    return GexApiWorld(None, repos, FakeClock(CLOCK))


def _dump(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def render() -> dict[str, str]:
    out: dict[str, str] = {}
    c = optpos_api_world().authed()
    for name, path in FILES:
        r = c.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        out[name] = _dump(r.json())
    r = optpos_api_world(empty=True).authed().get("/api/gex/option-positions")
    out["option_positions_empty.json"] = _dump({"status": r.status_code, **r.json()})
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    files = render()
    for p in OUT.glob("*.json"):
        if p.name not in files:
            p.unlink()
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    sys.stdout.write(f"{len(files)}개 → {OUT}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

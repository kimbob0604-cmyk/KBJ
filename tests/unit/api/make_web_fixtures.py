"""프런트 시험 픽스처 만들기 — `web/test/fixtures/api/*.json`(합성 응답, docs/p3_design.md §1.6).

`uv run python -m tests.unit.api.make_web_fixtures` 로 다시 만든다. 입력은 합성 세계(고정 시드 —
`tests/fixtures/synthetic/ledger_gen.py`·신고가 골든 입력)뿐이라 공개 레포에 넣어도 된다. 장 마감 뒤
(`*_close`)와 장중 슬롯(`*_live`) 두 시점을 만든다. 세션 토큰 같은 무작위 값은 고정
자리표시로 바꾼다.

픽스처 이름 = 경로의 `/` 를 `_` 로(예 `market/summary` → `market_summary.json`). 같은 경로를 다른
쿼리·시점으로 만든 것은 접미사를 붙인다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from kbj.services.api.readers.board import BOARD_DAILY_ETA
from tests.unit.api.api_world import ROOT, World, build_world

OUT: Final = ROOT / "web" / "test" / "fixtures" / "api"

# (파일 이름, 경로, 장중 시점인가)
FIXTURES: Final[tuple[tuple[str, str, bool], ...]] = (
    ("market_summary.json", "/api/market/summary", False),
    ("market_summary_live.json", "/api/market/summary", True),
    ("market_sectors.json", "/api/market/sectors?period=5", False),
    ("market_ribbon.json", "/api/market/ribbon", True),
    ("board_newhigh.json", "/api/board/newhigh", False),
    ("board_sectors.json", "/api/board/sectors", False),
    ("board_events.json", "/api/board/events", False),
    ("board_rankings.json", "/api/board/rankings", False),
    ("flows_screen.json", "/api/flows/screen?mode=both&period=5&min_avg_turnover=0", False),
    ("flows_investors.json", "/api/flows/investors", False),
    ("flows_stock.json", "/api/flows/stock/Q00003?days=20", False),
    ("flows_intraday.json", "/api/flows/intraday", True),
    ("etf_flows.json", "/api/etf/flows?period=5", False),
    ("etf_types.json", "/api/etf/types?period=5", False),
    ("etf_premium.json", "/api/etf/premium?basis=inav", True),
    ("etf_holdings_changes.json", "/api/etf/holdings/changes", False),
)
AUTH: Final[dict[str, Any]] = {
    "auth_me.json": {"user": "kbj-user", "csrf_token": "csrf-token-synthetic"},
    "auth_login.json": {"csrf_token": "csrf-token-synthetic"},
    "no_data.json": {"code": "no_data", "message": f"아직 없음 — {BOARD_DAILY_ETA}"},
    "unauthorized.json": {"code": "unauthorized", "message": "로그인이 필요하다", "detail": {}},
}


def render(world: World | None = None) -> dict[str, str]:
    """파일 이름 → JSON 글자(결정적)."""
    w = world or build_world()
    c = w.client()
    w.login(c)
    after_close = w.clock()
    live = w.slot.replace(minute=5)
    out: dict[str, str] = {}
    for name, path, is_live in FIXTURES:
        w.clock.set(live if is_live else after_close)
        r = c.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        out[name] = json.dumps(r.json(), ensure_ascii=False, indent=2) + "\n"
    for name, body in AUTH.items():
        out[name] = json.dumps(body, ensure_ascii=False, indent=2) + "\n"
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in render().items():
        (OUT / name).write_text(text, encoding="utf-8")
    print(f"{len(FIXTURES) + len(AUTH)}개 → {OUT.relative_to(Path(ROOT))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

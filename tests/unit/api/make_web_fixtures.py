"""프런트 시험 픽스처 만들기 — `web/test/fixtures/api/*.json`(합성 응답, docs/p3_design.md §1.6).

`uv run python -m tests.unit.api.make_web_fixtures` 로 다시 만든다. 입력은 합성 세계(고정 시드 —
`tests/fixtures/synthetic/ledger_gen.py`·신고가 골든 입력)뿐이라 공개 레포에 넣어도 된다. 장 마감 뒤
(`*_close`)와 장중 슬롯(`*_live`) 두 시점을 만든다. 세션 토큰 같은 무작위 값은 고정 자리표시로
바꾼다.

픽스처 이름 = 경로의 `/` 를 `_` 로(예 `market/summary` → `market_summary.json`). 같은 경로를 다른
쿼리·시점으로 만든 것은 접미사를 붙인다.

P4(docs/p4_design.md §1.8 — 묶음 A): 합성 20종목 세계(`tests/fixtures/synthetic/p4_world.py` — 고정
시드, 처리기·엔진으로 메모리 저장소에 올린 것 — `api_world_p4`)의 응답을 하위 폴더에 둔다.
- `stock/<코드>/<섹션>.json` — 페이지 8 섹션 12개 + `flows`(P3 `/api/flows/stock/<코드>`), 20종목
  전부. 404(아직 없음·적용 불가 아님)는 그 본문(`{code: no_data, message}`)을 그대로 둔다.
- `stock/_index.json` — {코드: {섹션: HTTP 상태}}(`report_xlsx` 는 내려받기 상태만 — 파일은 두지
  않는다)
- `stock/search.json`, `filings/{feed,earnings,overhang,insiders,schedule}.json`,
  `watchlist/main.json`

P5(docs/p5_design.md §1.1): 도메인마다 모듈 하나(`tests/unit/api/web_fixtures_p5/<도메인>.py` —
`render()`)가 `web/test/fixtures/api/<도메인>/**` 만 쓴다(`render_p5`). `--only <도메인>` 은 그
도메인
폴더만 다시 쓰고(P3·P4 는 건드리지 않는다), 인자가 없으면 전부 다시 쓴다.
"""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from kbj.services.api.readers.board import BOARD_DAILY_ETA
from tests.unit.api.api_world import ROOT, World, build_world
from tests.unit.api.api_world_p4 import P4ApiWorld, expect, shared
from tests.unit.api.web_fixtures_p5 import DOMAINS as P5_DOMAINS

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


# P4 — (파일 이름, 경로 꼬리). 경로는 `/api/stock/<코드>/<꼬리>`
P4_SECTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("header", "header"),
    ("chart", "chart?days=120"),
    ("financials", "financials"),
    ("report", "report"),
    ("segments", "segments"),
    ("consensus", "consensus"),
    ("valuation", "valuation"),
    ("scenario", "scenario"),
    ("overhang", "overhang"),
    ("insiders", "insiders"),
    ("disclosures", "disclosures?days=90"),
    ("earnings", "earnings"),
)
P4_OTHER: Final[tuple[tuple[str, str], ...]] = (
    ("stock/search.json", "/api/stock/search?q=%EB%AA%A8%EC%9D%98"),
    ("filings/feed.json", "/api/filings/feed?limit=40"),
    ("filings/earnings.json", "/api/filings/earnings?from=2026-11-10&to=2026-11-16"),
    ("filings/overhang.json", "/api/filings/overhang?days=90"),
    ("filings/insiders.json", "/api/filings/insiders?days=30"),
    ("filings/schedule.json", "/api/filings/schedule?days=60"),
    ("watchlist/main.json", "/api/watchlist"),
)
P4_DIRS: Final[tuple[str, ...]] = ("stock", "filings", "watchlist")


def _dump(body: object) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def p4_codes() -> list[str]:
    return sorted(expect()["stocks"])


def render_p4(world: P4ApiWorld | None = None) -> dict[str, str]:
    """P4 픽스처 — 하위 폴더 상대 경로 → JSON 글자(결정적: 가짜 시계·고정 시드)."""
    w = world or shared()
    c = w.authed()
    out: dict[str, str] = {}
    index: dict[str, dict[str, int]] = {}
    for code in p4_codes():
        st: dict[str, int] = {}
        for name, tail in (*P4_SECTIONS, ("flows", "")):
            path = f"/api/flows/stock/{code}" if name == "flows" else f"/api/stock/{code}/{tail}"
            r = c.get(path)
            if r.status_code not in (200, 404):
                raise RuntimeError(f"{path}: HTTP {r.status_code}")
            st[name] = r.status_code
            out[f"stock/{code}/{name}.json"] = _dump(r.json())
        st["report_xlsx"] = c.get(f"/api/stock/{code}/report.xlsx").status_code
        index[code] = st
    out["stock/_index.json"] = _dump(index)
    for rel, path in P4_OTHER:
        r = c.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        out[rel] = _dump(r.json())
    return out


def p4_files_on_disk() -> set[str]:
    return {
        str(p.relative_to(OUT))
        for d in P4_DIRS
        if (OUT / d).is_dir()
        for p in (OUT / d).rglob("*.json")
    }


def _p5_domains(only: str | None) -> tuple[str, ...]:
    if only is None:
        return P5_DOMAINS
    if only not in P5_DOMAINS:
        raise ValueError(f"모르는 P5 도메인: {only!r} (있는 것: {', '.join(P5_DOMAINS)})")
    return (only,)


def render_p5(only: str | None = None) -> dict[str, str]:
    """P5 픽스처 — `<도메인>/<상대 경로>` → JSON 글자. 도메인 모듈의 `render()` 를 모은다(경로는
    도메인 폴더 안 — 절대 경로·`..` 는 거부)."""
    out: dict[str, str] = {}
    for d in _p5_domains(only):
        mod = importlib.import_module(f"tests.unit.api.web_fixtures_p5.{d}")
        if getattr(mod, "DOMAIN", None) != d:
            raise ValueError(f"web_fixtures_p5/{d}.py 의 DOMAIN 이 {d!r} 가 아니다")
        files: dict[str, str] = mod.render()
        for rel, text in files.items():
            parts = rel.split("/")
            if rel.startswith("/") or ".." in parts or not rel.endswith(".json"):
                raise ValueError(f"{d}: 픽스처 경로 형식({rel!r}) — 도메인 폴더 안의 .json 만")
            out[f"{d}/{rel}"] = text
    return out


def p5_files_on_disk(only: str | None = None) -> set[str]:
    return {
        p.relative_to(OUT).as_posix()
        for d in _p5_domains(only)
        if (OUT / d).is_dir()
        for p in (OUT / d).rglob("*.json")
    }


def _write(files: dict[str, str], stale: set[str]) -> None:
    for rel in stale - set(files):
        (OUT / rel).unlink()
    for rel, text in files.items():
        path = OUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="make_web_fixtures", description="프런트 시험 픽스처 만들기")
    ap.add_argument("--only", choices=P5_DOMAINS, help="이 P5 도메인 폴더만 다시 쓴다")
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    only: str | None = args.only
    p5 = render_p5(only)
    _write(p5, p5_files_on_disk(only))
    if only is not None:
        print(f"P5 {only} {len(p5)}개 → {OUT.relative_to(Path(ROOT))}/{only}")
        return 0
    for name, text in render().items():
        (OUT / name).write_text(text, encoding="utf-8")
    p4 = render_p4()
    _write(p4, p4_files_on_disk())
    print(
        f"{len(FIXTURES) + len(AUTH)}개 + P4 {len(p4)}개 + P5 {len(p5)}개 → "
        f"{OUT.relative_to(Path(ROOT))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

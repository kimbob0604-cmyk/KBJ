"""공개 진단 JSON(`diag/summary·history·events.json` — docs/p7_design.md §7, 묶음 DIAG).

p7_world 합성 공개 입력으로 `diag.public` 을 돌려 `pub_diag` 를 채우고, 같은 Query 이름에 답하는
메모리
읽기로 파일을 만든다. 로그인 판정 키(fear_greed·verdict·gex*) 0·manifest 상한 안·행이 없으면 파일 0.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Any

from kbj.config.diag import load_diag
from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.services.engine import diag_pub
from kbj.services.public_export.diag_json import HISTORY_POINTS, build_diag
from kbj.services.public_export.manifest import FILE_CAPS, diag_problems
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.diag import MemoryDiagPublicRepo
from kbj.store.repos.memory import memory_repos
from tests.unit.engines.diagnosis.diag_seed import DiagReader, seed_public, world

CAL = TradingCalendar.default()
NOW = datetime(2026, 12, 10, 9, 0, tzinfo=KST)


def _filled(days: int = 3) -> Any:
    repos = memory_repos()
    seed_public(repos, world(), received_at=NOW)
    d = date(2026, 12, 10)
    picked: list[date] = []
    while len(picked) < days:
        picked.insert(0, d)
        d = CAL.prev_trading_day(d)
    for x in picked:
        diag_pub.run(JobContext(
            job="diag.public", as_of=x.isoformat(), run_id="t", attempt=1,
            now=datetime.combine(x, time(8, 40), tzinfo=KST),
            resources={"repos": repos, "kr": CAL, "diag_config": load_diag()},
        ))  # fmt: skip
    return repos


def test_empty_tables_make_no_files() -> None:
    out = build_diag(DiagReader(MemoryDiagPublicRepo()), now=NOW)
    assert out.files == () and out.skipped == ()


def test_files_have_public_keys_only_and_fit_caps() -> None:
    repos = _filled()
    out = build_diag(DiagReader(repos.diag_pub), now=NOW)
    names = [f.name for f in out.files]
    assert names == ["diag/summary.json", "diag/history.json", "diag/events.json"]
    for f in out.files:
        raw = json.loads(json.dumps(f.data, default=str))
        assert diag_problems(f.name, raw) == []
        assert len(json.dumps(f.data, default=str, separators=(",", ":"))) < FILE_CAPS[f.name]
        assert f.source == "KBJ:diag.public"
    summary = out.files[0].data
    assert summary["trade_date"] == "2026-12-10"
    assert {c["key"] for c in summary["indicators"]} == set(load_diag().keys("public"))
    assert all(c["tier"] == "public" for c in summary["indicators"])
    g = summary["gauge"]
    assert g is not None and g["label"] in ("risk_on", "neutral", "risk_off", "hold")
    # 카드의 상태 점수는 게이지 성분에서 — 성분 없는 지표는 None
    for c in summary["indicators"]:
        assert c["score"] == g["parts"].get(c["key"])
    # 이벤트는 오늘 이후만, 첫 이벤트는 그날 월물 만기(D-0)
    assert summary["events"][0] == {
        "date": "2026-12-10", "kind": "expiry", "title": "월물 만기", "days_left": 0,
        "quality": "ok",
    }  # fmt: skip


def test_history_series_points_capped() -> None:
    repos = _filled(days=4)
    out = build_diag(DiagReader(repos.diag_pub), now=NOW)
    hist = out.files[1].data
    pts = hist["series"]["credit_spread"]["points"]
    assert [p["trade_date"] for p in pts][-1] == "2026-12-10" and len(pts) == 4
    assert len(pts) <= HISTORY_POINTS and len(hist["gauge"]) == 4
    assert "fear_greed" not in json.dumps(hist) and "verdict" not in json.dumps(hist)

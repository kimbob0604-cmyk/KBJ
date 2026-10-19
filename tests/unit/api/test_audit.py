"""화면 데이터 점검(audit) — 비밀번호 없이 모든 GET 화면 API 를 한 번씩 부른다."""

from __future__ import annotations

from kbj.services.api.audit import count_empty, get_paths, render, run_audit, show
from tests.unit.api.api_world import World


def test_paths_fill_code_and_skip_other_params(world: World) -> None:
    paths = get_paths(world.app(), "005930")
    assert "/api/market/summary" in paths
    assert "/api/stock/005930/header" in paths
    assert not any("{" in p for p in paths)
    assert not any(p.startswith(("/api/auth", "/api/webhook")) for p in paths)


def test_audit_runs_without_login_and_reports_every_path(world: World) -> None:
    app = world.app()
    rows = run_audit(app, world.clock, "005930")
    assert len(rows) == len(get_paths(app, "005930"))
    assert all(r.status != 401 for r in rows)
    summary = next(r for r in rows if r.path == "/api/market/summary")
    assert summary.status == 200 and summary.as_of and summary.quality
    out = render(rows)
    assert "/api/market/summary" in out and "오류(5xx)" in out


def test_audit_does_not_open_the_real_login(world: World) -> None:
    app = world.app()
    run_audit(app, world.clock)
    assert world.client().get("/api/market/summary").status_code == 401


def test_show_prints_one_body(world: World) -> None:
    assert '"as_of"' in show(world.app(), "/api/market/summary")


def test_count_empty() -> None:
    assert count_empty({"a": [], "b": None, "c": [1, {"d": []}], "e": 0}) == 3

"""`/api/ops/*` 합성 앱·픽스처(페이지 13 최소판 — 보기 전용 표 셋, ADR 0037).

- 시각 2026-10-16(금) 21:00 KST — 아침·마감 브리핑 마감이 지났다.
- 기본(`ops_status.json`): notifier 하트비트 75초(기준 60초 — WARN), 오늘 board.daily 1회 실패 뒤
  재시도 성공, 규칙 알림 1통 발송 실패(사유 가림), 수집 결측 1 → 전체 WARN.
- `ops_status_fail.json`: gex.engine 하트비트 15분 없음 → FAIL.
- `ops_status_none.json`: 운영 원천이 없는 실행(DB 접속 설정 없음) → 404 본문(화면 `.msg`).
"""

from __future__ import annotations

import json
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any, Final

import fakeredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kbj.config.files import REPO_ROOT
from kbj.config.markets import load_calendar_events, load_markets
from kbj.core.calendar import TradingCalendar
from kbj.services.api.app import create_app
from kbj.services.api.readers.ops import STATE_ATTR, OpsContext
from kbj.services.ops.source import MemoryOpsSource, NotifyRow
from kbj.store.repos import memory_repos
from tests.unit.api.api_world import BASE, FakeClock, make_settings
from tests.unit.ops.ops_world import EXPECT, fresh_ages, kst, run_row, week_source

__all__ = ["DAY", "NOW", "OUT", "make_app", "render", "source"]

DAY: Final = date(2026, 10, 16)
NOW: Final = kst(DAY, time(21, 0))
OUT: Final = REPO_ROOT / "web" / "test" / "fixtures" / "api"


def source() -> MemoryOpsSource:
    src = week_source()
    src.runs = [r for r in src.runs if not (r.job == "board.daily" and r.as_of == DAY.isoformat())]
    src.runs.append(run_row("board.daily", DAY, time(15, 45), "failed", 1))
    src.runs.append(run_row("board.daily", DAY, time(15, 45), "ok", 2))
    at = kst(DAY, time(13, 5))
    src.notify.append(
        NotifyRow("alert.rule", "E8", "failed", at, None, as_of=DAY, parts=1,
                  reason="telegram 429 — 재시도 한도")
    )  # fmt: skip
    src.collection_gap_ts.append(kst(DAY, time(10, 31)))
    return src


def make_app(*, ops: OpsContext | None = None, stale: dict[str, float] | None = None) -> FastAPI:
    clock = FakeClock(NOW)
    app = create_app(
        make_settings(),
        redis=fakeredis.FakeRedis(),
        repos=memory_repos(),
        now=clock,
        cal=TradingCalendar.default(),
        markets=load_markets(),
        events=load_calendar_events(),
        static_dir=False,
    )
    if ops is None:
        ages = fresh_ages(NOW, stale={"notifier": 75.0, **(stale or {})})
        ops = OpsContext(source(), EXPECT, ages)
    setattr(app.state, STATE_ATTR, ops)
    return app


def _login(c: TestClient) -> None:
    from tests.unit.api.api_world import PASSWORD, USER

    r = c.post("/api/auth/login", json={"username": USER, "password": PASSWORD},
               headers={"Origin": BASE})  # fmt: skip
    assert r.status_code == 200, r.text


def _get(app: FastAPI, path: str) -> tuple[int, Any]:
    with TestClient(app, base_url=BASE) as c:
        _login(c)
        r = c.get(path)
        return r.status_code, r.json()


def _text(body: Any) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def _no_source_app() -> FastAPI:
    app = make_app()
    delattr(app.state, STATE_ATTR)
    return app


def render() -> dict[str, str]:
    """파일 이름(`ops_*.json` — 픽스처 폴더 바로 아래) → JSON 글자(결정적)."""
    out: dict[str, str] = {}
    base = make_app()
    for name, path in (
        ("ops_status.json", "/api/ops/status"),
        ("ops_jobs.json", f"/api/ops/jobs?date={DAY.isoformat()}"),
        ("ops_notify.json", f"/api/ops/notify?date={DAY.isoformat()}"),
        ("ops_jobs_empty.json", f"/api/ops/jobs?date={(DAY + timedelta(days=4)).isoformat()}"),
    ):
        code, body = _get(base, path)
        assert code == 200, (name, body)
        out[name] = _text(body)
    code, body = _get(make_app(stale={"engine": 900.0}), "/api/ops/status")
    assert code == 200 and body["data"]["level"] == "FAIL"
    out["ops_status_fail.json"] = _text(body)
    code, body = _get(_no_source_app(), "/api/ops/status")
    assert code == 404, body
    out["ops_status_none.json"] = _text(body)
    return out


def files_on_disk(out: Path = OUT) -> set[str]:
    return {p.name for p in out.glob("ops_*.json")}


def main() -> None:  # pragma: no cover — 손으로 다시 쓰기
    files = render()
    for stale in files_on_disk() - set(files):
        (OUT / stale).unlink()
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    print(f"썼다: {len(files)}개 → {OUT}")


if __name__ == "__main__":  # pragma: no cover
    main()

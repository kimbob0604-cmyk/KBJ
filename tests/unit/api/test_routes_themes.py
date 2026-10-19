"""`/api/themes*` — 페이지 9 테마·밸류체인(ADR 0039, metrics §31). 합성 세계(api_world + 합성 테마
마스터 + 보드 종목 하루). 웹 픽스처(`web/test/fixtures/api/themes/*`)가 최신인지·모델 모양인지도
본다."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable
from datetime import UTC, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kbj.core.theme_rows import SOURCE_LOGIN, SOURCE_MASTER
from kbj.services.api.models.common import Envelope, NoDataBody
from kbj.services.api.models.theme import ThemeCatalog, ThemeDetail, ThemeStrength
from kbj.services.api.readers.theme import NO_BOARD, NO_CLASSES, NO_MASTER, NOT_A_THEME
from kbj.services.collectors import theme_classes, theme_master
from kbj.services.scheduler.handlers import JobContext
from tests.fixtures.synthetic.theme_master import build_files
from tests.unit.api.api_world import World, build_world
from tests.unit.api.theme_world import golden_names, seed_board
from tests.unit.api.web_fixtures_p9 import themes as fx

PATHS = ("/api/themes", "/api/themes/strength", "/api/themes/syn_1_1")


@pytest.fixture(scope="module")
def world() -> World:
    return fx.build()


def _get(w: World, path: str) -> tuple[int, Any]:
    with TestClient(w.app(), base_url="https://testserver") as c:
        w.login(c)
        r = c.get(path)
        return r.status_code, r.json()


@pytest.mark.parametrize("path", PATHS)
def test_no_session_is_401(world: World, path: str) -> None:
    with world.client() as c:
        r = c.get(path)
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"


def test_catalog_is_public_fields_only(world: World) -> None:
    code, body = _get(world, "/api/themes")
    assert code == 200
    env = Envelope[ThemeCatalog].model_validate(body)
    assert env.source == SOURCE_MASTER and env.quality.value == "ok"
    assert env.data.n_themes == 36 and env.data.sections[0].section == "기술"
    text = json.dumps(body, ensure_ascii=False)
    for k in ('"naver"', '"members"', '"reason"', '"code"', "원본 테마"):
        assert k not in text, k  # 네이버 유래 칸·이름 0
    chains = {g.key: g.themes for g in env.data.valuechains}
    ids = {t.id: t for s in env.data.sections for t in s.themes}
    assert all(ids[i].valuechain == k for k, v in chains.items() for i in v)


@pytest.mark.parametrize("period", [1, 5, 20])
def test_strength_ranks_by_period(world: World, period: int) -> None:
    code, body = _get(world, f"/api/themes/strength?period={period}")
    assert code == 200
    env = Envelope[ThemeStrength].model_validate(body)
    assert env.source.endswith(SOURCE_LOGIN) and env.data.period == period
    key = {1: "chg_pct", 5: "ret_5d", 20: "ret_20d"}[period]
    vals = [getattr(r.stats, key) for r in env.data.rows]
    known = [v for v in vals if v is not None]
    assert known == sorted(known, reverse=True) and vals[: len(known)] == known
    assert [r.rank for r in env.data.rows] == list(range(1, 37))
    assert all(r.stats.n_priced > 0 for r in env.data.rows)
    assert env.data.chains and all(c.stats.n_members > 0 for c in env.data.chains)
    assert any("21거래일" in n for n in env.notes)


def test_detail_members_reasons_and_prices(world: World) -> None:
    code, body = _get(world, "/api/themes/syn_1_1")
    assert code == 200
    d = Envelope[ThemeDetail].model_validate(body).data
    assert d.naver and all(n.n_members > 0 for n in d.naver)
    assert d.members and all(
        m.reasons and set(m.naver) <= {n.no for n in d.naver} for m in d.members
    )
    caps = [m.mktcap for m in d.members if m.mktcap is not None]
    assert caps == sorted(caps, reverse=True)
    assert d.stats is not None and d.stats.n_members == len(d.members)


def test_not_found_and_validation(world: World) -> None:
    code, body = _get(world, "/api/themes/nope_x")
    assert code == 404 and body == {"code": "no_data", "message": NOT_A_THEME}
    assert _get(world, "/api/themes/strength?period=3")[0] == 422
    assert _get(world, "/api/themes/Bad-Id")[0] == 422


def test_empty_states_are_404_with_job_hint() -> None:
    assert (
        _get(fx.build(board=False, master=False, classes=False), "/api/themes")[1]["message"]
        == NO_CLASSES
    )
    no_master = fx.build(master=False)
    assert _get(no_master, "/api/themes/strength")[1]["message"] == NO_MASTER
    assert _get(no_master, "/api/themes")[0] == 200  # 공개 분류는 따로
    no_board = fx.build(board=False)
    assert _get(no_board, "/api/themes/strength")[1]["message"] == NO_BOARD
    code, body = _get(no_board, "/api/themes/syn_1_1")  # 상세는 시세 없이도
    assert code == 200 and body["data"]["stats"] is None and body["data"]["trade_date"] is None


def test_strength_reads_the_last_board_day_before_today() -> None:
    """오늘 보드가 아직 없어도(주말·월요일 아침·16:00 전) 지난 보드 날로 계산하고 그 날을 보인다.
    산출 JSON 만 있고 종목 하루가 빈 날은 건너뛴다."""
    w = fx.build()
    day = w.repos.board.last_day(w.clock().date())
    assert day is not None
    w.clock.set(w.clock() + timedelta(days=4))  # 보드는 나흘 전 거래일뿐
    code, body = _get(w, "/api/themes/strength")
    assert code == 200
    env = Envelope[ThemeStrength].model_validate(body)
    assert env.data.trade_date == day and env.as_of.date() == day
    assert f"시세 {day.isoformat()} 마감(보드)" in env.notes
    assert all(r.stats.n_priced > 0 for r in env.data.rows)
    # 그 뒤 날에 산출 JSON 만 있고 종목 하루가 없다 — 앞 보드 날을 쓴다(목록·상세 모두)
    later = day + timedelta(days=1)
    for (d, name), art in list(w.repos.board.artifact_rows.items()):
        if d == day:
            w.repos.board.artifact_rows[(later, name)] = dataclasses.replace(art, trade_date=later)
    assert w.repos.board.last_day(w.clock().date()) == later
    code, body = _get(w, "/api/themes/strength")
    assert code == 200 and body["data"]["trade_date"] == day.isoformat()
    code, body = _get(w, "/api/themes/syn_1_1")
    assert code == 200 and body["data"]["trade_date"] == day.isoformat()
    assert body["data"]["stats"] is not None


class _Files:
    """theme_master_client 대역 — 받은 파일 이름을 남긴다."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.seen: list[str] = []

    def fetch(self, name: str) -> bytes:
        self.seen.append(name)
        return self.files[name]

    def fetch_all(self, names: Iterable[str]) -> dict[str, bytes]:
        return {n: self.fetch(n) for n in names}

    def close(self) -> None:  # 작업이 만든 클라이언트만 닫는다 — 주입한 것은 부르지 않는다
        raise AssertionError("주입한 클라이언트를 닫았다")


def test_jobs_write_what_page9_reads() -> None:
    """두 작업 처리기(theme.master_sync → theme.classes_sync)가 쓴 표를 API 가 그대로 읽는다 —
    보드는 앞선 거래일 하루뿐(사용자 로컬 상황: 작업을 손으로 돌린 주말·월요일 아침)."""
    w = build_world()
    day = w.repos.board.last_day(w.clock().date())
    assert day is not None
    seed_board(w.repos, day)
    now = w.clock() + timedelta(days=3)
    w.clock.set(now)
    # 수집 전 — 분류·강도 모두 '아직 없음'(분류가 먼저)
    assert _get(w, "/api/themes")[1]["message"] == NO_CLASSES
    assert _get(w, "/api/themes/strength")[1]["message"] == NO_CLASSES
    raw = _Files(build_files(anchor_names=golden_names(), built_at=now - timedelta(days=1)))
    for mod in (theme_master, theme_classes):
        ctx = JobContext(
            job=mod.JOB, as_of=now.date().isoformat(), run_id="r1", attempt=1,
            now=now.astimezone(UTC), keys=(),
            resources={"repos": w.repos, "theme_master_client": raw},
        )  # fmt: skip
        res = mod.run(ctx)
        assert res.status == "ok" and res.detail["changed"] is True, (mod.JOB, res.detail)
    code, body = _get(w, "/api/themes")
    assert code == 200 and body["data"]["n_themes"] == 36 and body["quality"] == "ok"
    code, body = _get(w, "/api/themes/strength?period=5")
    assert code == 200, body
    env = Envelope[ThemeStrength].model_validate(body)
    assert env.data.trade_date == day and len(env.data.rows) == 36
    assert not any("기준 시각이 다르다" in n for n in env.notes)  # 두 판의 built_at 이 같다


def test_stale_and_mismatched_builds() -> None:
    w = fx.build()
    w.clock.set(w.clock() + timedelta(days=30))
    _, body = _get(w, "/api/themes")
    assert body["quality"] == "stale"
    # 공개 분류만 새 판 — 편입 판과 기준 시각이 다르면 stale + notes
    w2 = fx.build()
    from kbj.config.theme import load_theme
    from kbj.engines.theme.classes import parse_classes
    from tests.fixtures.synthetic.theme_master import build_files

    newer = build_files(built_at=w2.clock() - timedelta(hours=1))
    c = parse_classes(newer, cfg=load_theme().master)
    w2.repos.theme_pub.replace_themes(c.themes, loaded_by="t", received_at=w2.clock())
    _, body = _get(w2, "/api/themes/strength")
    # 보드 마감값이 잠정(estimated)이라 그보다 낮아지지는 않는다 — ok 는 아니고 notes 에 적는다
    assert body["quality"] in ("stale", "estimated")
    assert any("기준 시각이 다르다" in n for n in body["notes"])
    _, cat = _get(w2, "/api/themes")
    assert cat["quality"] == "ok"  # 공개 분류만 보면 판이 하나


def test_web_fixtures_are_fresh_and_valid() -> None:
    """다르면 `uv run python -m tests.unit.api.web_fixtures_p9.themes` 로 다시 쓴다."""
    want = fx.render()
    assert set(want) == fx.files_on_disk()
    for rel, text in want.items():
        assert (fx.OUT / rel).read_text(encoding="utf-8") == text, f"{rel} 이 낡았다"
    load = lambda n: json.loads((fx.OUT / fx.DIR / n).read_text(encoding="utf-8"))  # noqa: E731
    Envelope[ThemeCatalog].model_validate(load("catalog.json"))
    for p in (1, 5, 20):
        Envelope[ThemeStrength].model_validate(load(f"strength_{p}.json"))
    for n in ("detail.json", "detail_plain.json"):
        Envelope[ThemeDetail].model_validate(load(n))
    for n in ("none.json", "strength_none.json"):
        NoDataBody.model_validate(load(n))
    # 공개 파일 data = 로그인 API 분류 data(같은 모양·같은 값 — 화면이 같은 코드로 그린다)
    pub = load("public.json")
    assert pub["data"] == load("catalog.json")["data"]
    Envelope[ThemeCatalog].model_validate(pub)

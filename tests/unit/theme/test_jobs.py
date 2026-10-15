"""테마 마스터 작업 — `theme.master_sync`(로그인)·`theme.classes_sync`(공개)(ADR 0039).

가짜 HTTP 서버(`httpx.MockTransport` — 합성 파일)로 받는다(원천 주소는 이 컨테이너에서 막혀 있을 수
있다 — 실제 호출 0). 바뀜 감지(built_at 같으면 나머지 파일을 받지 않는다)·검증 거부(옛 판
유지)·원자적 교체· 오래된 판 거부·전송 실패를 본다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from kbj.config.theme import load_theme
from kbj.data.http import FetchError
from kbj.data.public.stock_dashboard.client import (
    ThemeMasterClient,
    ThemeMasterError,
    ThemeMasterThrottled,
)
from kbj.data.ratelimit import Priority
from kbj.data.spec import DataKey
from kbj.services.collectors import theme_classes, theme_master
from kbj.services.scheduler.handlers import JobContext, resolve
from kbj.store.repos.theme import MemoryThemeRepo
from kbj.store.repos.theme_pub import MemoryThemePublicRepo
from tests.fixtures.synthetic.theme_master import BUILT_AT, build_files

NOW = datetime(2026, 10, 11, 3, 40, tzinfo=UTC)  # 일 12:40 KST
BASE = "https://raw.test/owner/repo/main/data/master"
CFG = load_theme()


class _Limiter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.slowed = 0

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        self.calls.append(tr_id)

    def on_rate_limited(self) -> float:
        self.slowed += 1
        return 0.5


class FakeRaw:
    """raw 파일 서버 — 경로 끝의 파일 이름으로 답한다. 받은 경로를 남긴다."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.seen: list[str] = []
        self.status: dict[str, int] = {}

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        assert path.startswith("/owner/repo/main/data/master/"), path
        name = path.rsplit("/", 1)[-1]
        self.seen.append(name)
        if name in self.status:
            return httpx.Response(self.status[name], content=b"no")
        body = self.files.get(name)
        return (
            httpx.Response(404, content=b"404")
            if body is None
            else httpx.Response(200, content=body)
        )

    def client(self) -> ThemeMasterClient:
        return ThemeMasterClient(
            _Limiter(), base_url=BASE, transport=httpx.MockTransport(self.handler)
        )


def _ctx(job: str, raw: FakeRaw, **res: object) -> JobContext:
    key = DataKey("STOCK_DASHBOARD", job, NOW.date().isoformat())
    return JobContext(
        job=job, as_of="2026-10-11", run_id="r1", attempt=1, now=NOW, keys=(key,),
        resources={"theme_master_client": raw.client(), "theme_config": CFG, **res},
    )  # fmt: skip


def _master(raw: FakeRaw, repo: MemoryThemeRepo):
    return theme_master.run(_ctx("theme_master", raw, theme_repo=repo))


def _classes(raw: FakeRaw, repo: MemoryThemePublicRepo):
    return theme_classes.run(_ctx("theme_classes", raw, theme_pub_repo=repo))


def test_owners_resolve() -> None:
    assert resolve("kbj.services.collectors.theme_master:run") is theme_master.run
    assert resolve("kbj.services.collectors.theme_classes:run") is theme_classes.run


def test_first_load_then_unchanged_skips_other_files() -> None:
    raw = FakeRaw(build_files())
    repo = MemoryThemeRepo()
    res = _master(raw, repo)
    assert res.status == "ok" and res.detail["changed"] is True and res.collected
    assert sorted(raw.seen) == sorted(build_files())  # 일곱 다
    load = repo.last_load()
    assert load is not None and load.built_at == BUILT_AT
    assert len(repo.members()) == load.counts["members"] and len(repo.stocks()) == 2100
    assert {r.source for r in repo.links()} == {
        "stock-dashboard:data/master+naver(stock-dashboard 수집)"
    }
    raw.seen.clear()
    again = _master(raw, repo)
    assert again.status == "ok" and again.rows == 0 and again.detail["changed"] is False
    assert raw.seen == ["meta.json"]  # 바뀌지 않았으면 meta 만


def test_new_build_replaces_everything_at_once() -> None:
    repo = MemoryThemeRepo()
    assert _master(FakeRaw(build_files()), repo).status == "ok"
    newer = build_files(seed=7, built_at=BUILT_AT + timedelta(days=7))
    res = _master(FakeRaw(newer), repo)
    assert res.status == "ok" and res.detail["changed"] is True
    load = repo.last_load()
    assert load is not None and load.built_at == BUILT_AT + timedelta(days=7)
    assert {m.as_of for m in repo.members()} == {BUILT_AT + timedelta(days=7)}  # 옛 판 줄 0
    assert len(repo.loads.all()) == 2  # 적재 기록은 쌓인다


def test_validation_failure_keeps_old_build() -> None:
    repo = MemoryThemeRepo()
    _master(FakeRaw(build_files()), repo)
    before = (repo.members(), repo.last_load())
    shrunk = build_files(built_at=BUILT_AT + timedelta(days=7), drop_members=0.5)
    res = _master(FakeRaw(shrunk), repo)
    assert res.status == "failed" and res.collected == ()
    assert "검증 실패" in res.detail["reason"] and res.detail["n_problems"] >= 1
    assert (repo.members(), repo.last_load()) == before  # 옛 판 그대로


def test_older_build_is_refused() -> None:
    repo = MemoryThemeRepo()
    _master(FakeRaw(build_files()), repo)
    old = build_files(built_at=BUILT_AT - timedelta(days=7))
    res = _master(FakeRaw(old), repo)
    assert res.status == "failed" and "오래됐다" in res.detail["reason"]


def test_write_failure_midway_changes_nothing() -> None:
    repo = MemoryThemeRepo()
    _master(FakeRaw(build_files()), repo)
    before = {n: t.all() for n, t in repo.tables.items()}
    repo.fail_after = 3
    with pytest.raises(RuntimeError):
        _master(FakeRaw(build_files(seed=9, built_at=BUILT_AT + timedelta(days=1))), repo)
    assert {n: t.all() for n, t in repo.tables.items()} == before
    assert repo.last_load() is not None and repo.last_load().built_at == BUILT_AT  # pyright: ignore[reportOptionalMemberAccess]


def test_transport_failures_raise_not_swallow() -> None:
    raw = FakeRaw(build_files())
    raw.status["stocks.csv"] = 500
    repo = MemoryThemeRepo()
    with pytest.raises(ThemeMasterError):
        _master(raw, repo)
    assert repo.last_load() is None  # 일부만 적재하지 않는다
    raw2 = FakeRaw(build_files())
    raw2.status["meta.json"] = 429
    c = raw2.client()
    with pytest.raises(ThemeMasterThrottled):
        c.fetch("meta.json")
    with pytest.raises(ValueError):
        c.fetch("../x.json")


def test_classes_job_writes_public_table_only_user_fields() -> None:
    raw = FakeRaw(build_files())
    pub = MemoryThemePublicRepo()
    res = _classes(raw, pub)
    assert res.status == "ok" and res.detail["themes"] == 36
    assert sorted(raw.seen) == ["meta.json", "themes.json"]  # 네이버 파일은 받지 않는다
    assert {t.source for t in pub.themes()} == {"stock-dashboard:data/master"}
    raw.seen.clear()
    same = _classes(raw, pub)
    assert same.detail["changed"] is False and raw.seen == ["meta.json"]
    # 검증 실패(대테마 하한) → 옛 판 유지
    import json

    files = build_files(built_at=BUILT_AT + timedelta(days=7))
    doc = json.loads(files["themes.json"])
    doc["themes"] = doc["themes"][:5]
    files["themes.json"] = json.dumps(doc, ensure_ascii=False).encode()
    bad = _classes(FakeRaw(files), pub)
    assert bad.status == "failed" and len(pub.themes()) == 36
    old = _classes(FakeRaw(build_files(built_at=BUILT_AT - timedelta(days=1))), pub)
    assert old.status == "failed" and "오래됐다" in old.detail["reason"]


def test_client_joins_base_path_and_caps_size() -> None:
    raw = FakeRaw({"meta.json": b"x" * 100})
    lim = _Limiter()
    c = ThemeMasterClient(
        lim, base_url=BASE, transport=httpx.MockTransport(raw.handler), max_bytes=50
    )
    with pytest.raises(FetchError, match="보다 크다"):  # 자르지 않는다
        c.fetch("meta.json")
    assert lim.calls == ["stock_dashboard:theme_master"]
    with pytest.raises(ValueError):
        ThemeMasterClient(lim, base_url="http://x")


def test_jobs_build_the_real_client_when_not_injected() -> None:
    """운영 경로 — 자원에 클라이언트가 없으면 설정·Redis 로 만든다(요청은 하지 않는다)."""
    import fakeredis

    from kbj.config.settings import Settings

    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    for mod in (theme_master, theme_classes):
        ctx = JobContext(
            job="x", as_of="2026-10-11", run_id="r", attempt=1, now=NOW, settings=settings,
            resources={"redis": fakeredis.FakeRedis(), "theme_config": CFG},
        )  # fmt: skip
        client, owned = mod._client(ctx, CFG)  # pyright: ignore[reportPrivateUsage]
        assert isinstance(client, ThemeMasterClient) and owned
        client.close()

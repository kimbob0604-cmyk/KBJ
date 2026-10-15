"""테마 마스터 받기 작업 — `theme.master_sync`(주 1회, ADR 0039 · 사용자 결정 2026-10-15).

stock-dashboard 레포가 GitHub Actions('종목 마스터 갱신' — 일 06:00 KST, 바뀌면 PR)로 만든
`data/master/` 파일 일곱을 받아, 검증을 통과한 판만 저장소에 **한 번에** 바꿔 넣는다.

1. `meta.json` 을 받아 `built_at` 을 본다. 직전 적재 판(`prv_themes.master_load`)과 같으면 할 일이
   없다 (`ok`, rows 0, changed False — 나머지 파일을 받지 않는다). 직전 판보다 **오래된** 판이면
   실패(되돌림은 사람이 확인 — 조용히 옛 판으로 바꾸지 않는다).
2. 나머지 여섯 파일을 받는다. 하나라도 실패하면 실패(일부만 적재하지 않는다).
3. 순수 엔진(`kbj.engines.theme.master.parse_master`)이 모양·참조·개수 하한·직전 판 대비 감소율을
   본다. 걸리면 실패 — 적재하지 않는다(옛 판 유지). 실행기가 재시도 뒤 실패 알림을
   보낸다(ops.watchdog).
4. 저장소 `replace_master` 가 한 트랜잭션에서 공개 대테마 표·로그인 표를 통째로 바꾼다.

원천 표기: 공개 행 `stock-dashboard:data/master`, 로그인 행 `…+naver(stock-dashboard 수집)`, as_of =
`built_at`. KBJ 는 네이버에 직접 접속하지 않는다(CLAUDE.md §2 — 이 작업은 GitHub raw 파일만 받는다).
기록(`detail`)에는 개수·시각·문제 문장만(값 없음). 오류는 삼키지 않는다(절대 규칙 4).

자원(시험 주입): `theme_repo`(없으면 `repos.theme` → Pg), `theme_master_client`(없으면 설정·Redis 로
만든다 — `redis` 자원), `theme_config`.
"""

from __future__ import annotations

from typing import Any, Final

from kbj.config.theme import ThemeConfig, load_theme
from kbj.engines.theme.master import FILES, META, MasterInvalid, built_at_of, parse_master
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.theme import PgThemeRepo, ThemeRepo

__all__ = ["JOB", "run"]

JOB: Final = "theme.master_sync"
_MB: Final = 1024 * 1024


def _config(ctx: JobContext) -> ThemeConfig:
    return ctx.resources.get("theme_config") or load_theme(settings=ctx.settings)


def _repo(ctx: JobContext) -> ThemeRepo:
    got = ctx.resources.get("theme_repo")
    if got is not None:
        return got
    repos = ctx.resources.get("repos")
    return repos.theme if repos is not None else PgThemeRepo(ctx.resource("connect"))


def _client(ctx: JobContext, cfg: ThemeConfig) -> tuple[Any, bool]:
    """(클라이언트, 이 작업이 만들었나 — 만들었으면 닫는다)."""
    got = ctx.resources.get("theme_master_client")
    if got is not None:
        return got, False
    if ctx.settings is None:
        raise RuntimeError("설정이 주입되지 않았다(리미터)")
    # raw 파일 클라이언트는 공개 어댑터에 하나(ECOS 방식 — 등급은 데이터셋이 정한다, ADR 0039)
    from kbj.data.public.stock_dashboard.client import ThemeMasterClient

    client = ThemeMasterClient.from_settings(
        ctx.settings,
        ctx.resource("redis"),
        base_url=cfg.master.base_url,
        max_bytes=cfg.master.max_file_mb * _MB,
    )
    return client, True


def _failed(reason: str, **detail: Any) -> JobResult:
    return JobResult("failed", detail={"reason": reason, **detail})


def run(ctx: JobContext) -> JobResult:
    cfg = _config(ctx)
    repo = _repo(ctx)
    last = repo.last_load()
    client, owned = _client(ctx, cfg)
    try:
        meta = client.fetch(META)
        try:
            built = built_at_of(meta)
        except MasterInvalid as e:
            return _failed(
                "테마 마스터 검증 실패 — 적재하지 않음(옛 판 유지)", problems=list(e.problems)
            )
        if last is not None and built == last.built_at:
            return JobResult(
                "ok",
                tuple(ctx.keys),
                rows=0,
                detail={"changed": False, "built_at": built.isoformat()},
            )
        if last is not None and built < last.built_at:
            return _failed(
                "원천 판(built_at)이 직전 적재 판보다 오래됐다 — 적재하지 않음(사람이 확인)",
                built_at=built.isoformat(),
                last_built_at=last.built_at.isoformat(),
            )
        files = {META: meta, **client.fetch_all(f for f in FILES if f != META)}
    finally:
        if owned:
            client.close()
    try:
        master = parse_master(files, cfg=cfg.master, previous=None if last is None else last.counts)
    except MasterInvalid as e:
        return _failed(
            "테마 마스터 검증 실패 — 적재하지 않음(옛 판 유지)",
            built_at=built.isoformat(),
            problems=list(e.problems[:10]),
            n_problems=len(e.problems),
        )
    n = repo.replace_master(master, loaded_by=JOB, received_at=ctx.now)
    return JobResult(
        "ok",
        tuple(ctx.keys),
        rows=n,
        detail={
            "changed": True,
            "built_at": built.isoformat(),
            "counts": dict(master.load.counts),
            "n_warnings": len(master.load.warnings),
            "warnings": list(master.load.warnings[:5]),
        },
    )

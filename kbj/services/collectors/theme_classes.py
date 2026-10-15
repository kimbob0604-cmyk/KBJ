"""대테마 분류 받기 작업(공개) — `theme.classes_sync`(주 1회, `theme.master_sync` 뒤 — ADR 0039).

stock-dashboard `data/master/` 의 `meta.json`·`themes.json` 만 받아 사용자가 만든 대테마 분류
(id·이름·섹션·밸류체인)를 공개 표 `pub_themes.theme` 에 통째로 바꿔 넣는다(한 트랜잭션). themes.json
안의 네이버 테마 목록은 읽지 않는다(`kbj.engines.theme.classes`).

- 로그인 원천(`STOCK_DASHBOARD:theme_master`)을 받는 작업은 공개 표에 쓰지 않는다(등록부 규칙 — ADR
  0002). 그래서 공개 분류는 이 공개 작업이 따로 받는다. 굳은 의존(`depends_on theme.master_sync
  hard`)으로 로그인 판 검증이 통과한 날만 돈다 — 두 등급의 판(built_at)이 어긋나는 때를
  줄인다(어긋나면 API 가 notes 에 적는다).
- built_at 이 지금 공개 표의 as_of 와 같으면 할 일이 없다(`ok`, rows 0). 더 오래된 판이면
  실패(사람이 확인).
- 검증(형식·중복·대테마 ≥ 하한·직전 대비 감소율)에 걸리면 실패 — 적재하지 않는다(옛 판 유지).
- 이 모듈은 공개 등급 코드만 import 한다(계약 ㉑ — 로그인 원천·저장소를 모른다). 자원(시험 주입):
  `theme_pub_repo`(없으면 `repos.theme_pub` → Pg), `theme_master_client`, `theme_config`.
"""

from __future__ import annotations

from typing import Any, Final

from kbj.config.theme import ThemeConfig, load_theme
from kbj.engines.theme.classes import (
    META,
    THEMES,
    ClassesInvalid,
    built_at_of,
    parse_classes,
)
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.theme_pub import PgThemePublicRepo, ThemePublicRepo

__all__ = ["JOB", "run"]

JOB: Final = "theme.classes_sync"
_MB: Final = 1024 * 1024
_REASON: Final = "대테마 분류 검증 실패 — 적재하지 않음(옛 판 유지)"


def _config(ctx: JobContext) -> ThemeConfig:
    return ctx.resources.get("theme_config") or load_theme(settings=ctx.settings)


def _repo(ctx: JobContext) -> ThemePublicRepo:
    got = ctx.resources.get("theme_pub_repo")
    if got is not None:
        return got
    repos = ctx.resources.get("repos")
    return repos.theme_pub if repos is not None else PgThemePublicRepo(ctx.resource("connect"))


def _client(ctx: JobContext, cfg: ThemeConfig) -> tuple[Any, bool]:
    got = ctx.resources.get("theme_master_client")
    if got is not None:
        return got, False
    if ctx.settings is None:
        raise RuntimeError("설정이 주입되지 않았다(리미터)")
    from kbj.data.public.stock_dashboard.client import ThemeMasterClient

    client = ThemeMasterClient.from_settings(
        ctx.settings,
        ctx.resource("redis"),
        base_url=cfg.master.base_url,
        max_bytes=cfg.master.max_file_mb * _MB,
    )
    return client, True


def run(ctx: JobContext) -> JobResult:
    cfg = _config(ctx)
    repo = _repo(ctx)
    now_rows = repo.themes()
    last = max((r.as_of for r in now_rows), default=None)
    client, owned = _client(ctx, cfg)
    try:
        meta = client.fetch(META)
        try:
            built = built_at_of(meta)
        except ClassesInvalid as e:
            return JobResult("failed", detail={"reason": _REASON, "problems": list(e.problems)})
        if last is not None and built == last:
            return JobResult(
                "ok", tuple(ctx.keys), rows=0,
                detail={"changed": False, "built_at": built.isoformat()},
            )  # fmt: skip
        if last is not None and built < last:
            return JobResult(
                "failed",
                detail={
                    "reason": "원천 판이 지금 공개 판보다 오래됐다 — 적재하지 않음(사람이 확인)",
                    "built_at": built.isoformat(),
                    "last_built_at": last.isoformat(),
                },
            )
        themes_raw = client.fetch(THEMES)
    finally:
        if owned:
            client.close()
    try:
        got = parse_classes(
            {META: meta, THEMES: themes_raw}, cfg=cfg.master, previous=len(now_rows) or None
        )
    except ClassesInvalid as e:
        return JobResult(
            "failed",
            detail={
                "reason": _REASON,
                "built_at": built.isoformat(),
                "problems": list(e.problems[:10]),
                "n_problems": len(e.problems),
            },
        )
    n = repo.replace_themes(got.themes, loaded_by=JOB, received_at=ctx.now)
    sections = sorted({t.section for t in got.themes})
    return JobResult(
        "ok",
        tuple(ctx.keys),
        rows=n,
        detail={
            "changed": True,
            "built_at": built.isoformat(),
            "themes": len(got.themes),
            "sections": len(sections),
            "valuechains": len({t.valuechain for t in got.themes if t.valuechain}),
        },
    )

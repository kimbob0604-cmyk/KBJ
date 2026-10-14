""".gitignore — 코드 패키지는 무시되지 않고, 산출물·비밀·토큰 캐시는 무시된다(ADR 0003 §2-5).

`git check-ignore --no-index` 로 경로 규칙만 본다(파일이 실제로 있을 필요 없음).

P9 WRAP(ADR 0038): legacy/ 를 비워 legacy/** 경로 사례 17개를 지웠다(그 산출물 규칙도
.gitignore 에서 지웠다 — 지킬 대상이 없다). 비밀·토큰·루트 산출물·관심종목 사례는 그대로,
하위 폴더 .env 사례 1개(legacy/gexlab/.env 자리)를 더했다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

KEPT = [
    "kbj/data/public/x.py",
    "kbj/data/private/x.py",
    "kbj/store/migrations/0001_schemas.sql",
    "fixtures/synthetic/a.json",
    ".env.example",
    # P4 — 합성 관심종목·API 시험 fixture 는 막지 않는다
    "tests/fixtures/synthetic/p4_world.py",
    "tests/fixtures/synthetic/p4_expect.yaml",
    "web/test/fixtures/api/watchlist.json",
    "web/test/fixtures/api/watchlist/main.json",
    "kbj/services/watchlist/__main__.py",
]
IGNORED = [
    ".env",
    ".env.local",
    "sub/.env",
    "id.key",
    "secrets/a.txt",
    "cache/kis_token.json",
    "x/.kis_token.json",
    "x/kis.token.json",
    "data/x.json",
    "state/x.json",
    "raw/x.json",
    "probe_out/x.json",
    "fixtures/real/x.json",
    "x.sqlite",
    "x.parquet",
    "x.xlsx",
    # P4 — 사용자 관심종목 파일(개인 데이터 — U-P4-5)
    "cache/server_watchlist.json",
    "server_watchlist.json",
    "backup/server_watchlist.json",
    "watchlist.json",
    "my_watchlist.csv",
]


def ignored(path: str) -> bool:
    r = subprocess.run(  # noqa: S603 — 고정 인자
        ["git", "-C", str(ROOT), "check-ignore", "-q", "--no-index", path],  # noqa: S607
        check=False,
    )
    assert r.returncode in (0, 1), f"git check-ignore 오류: {path}"
    return r.returncode == 0


@pytest.mark.parametrize("path", KEPT)
def test_code_and_fixtures_are_not_ignored(path: str) -> None:
    assert not ignored(path)


@pytest.mark.parametrize("path", IGNORED)
def test_outputs_and_secrets_are_ignored(path: str) -> None:
    assert ignored(path)

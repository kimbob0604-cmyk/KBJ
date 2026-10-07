""".gitignore — 코드 패키지는 무시되지 않고, 산출물·비밀·토큰 캐시는 무시된다(ADR 0003 §2-5).

`git check-ignore --no-index` 로 경로 규칙만 본다(파일이 실제로 있을 필요 없음).
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
    "legacy/gexlab/data/kis/x.py",
    "legacy/gexlab/tests/golden/raw/synthetic.jsonl",
    "legacy/gexlab/tests/fixtures/kis/synthetic.json",
    "legacy/stock_dashboard/data/valuechain_map.json",
    "fixtures/synthetic/a.json",
    ".env.example",
    "legacy/gexlab/.env.example",
]
IGNORED = [
    ".env",
    ".env.local",
    "legacy/gexlab/.env",
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
    "legacy/gexlab/tests/fixtures/real/x.json",
    "legacy/gexlab/state/spool/x",
    "legacy/etf_traker/board/state/xdigest/x.json",
    "legacy/etf_traker/monitor/bok/cache/x.json",
    "legacy/etf_traker/dart-report/.cache/corp.json",
    "legacy/etf_traker/dart-report/out/a.xlsx",
    "legacy/etf_traker/flowlab/ci-out/x.csv",
    "legacy/stock_dashboard/db/dashboard.db",
    "x.sqlite",
    "x.parquet",
    "x.xlsx",
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

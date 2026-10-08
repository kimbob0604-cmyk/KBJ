"""CLI(`python -m kbj.services.public_export`)·`build_public_site.sh --dry-run`·JS 합치기 계약.

- CLI build/check 를 새 프로세스로 돌린다(DB 없이, 시각 고정).
- 우리가 만든 산출물을 W 의 `web/scripts/merge-public-data.mjs`(planMerge)가 받아들이는지, 로그인
  등급 출처를 심으면 거부하는지 node 로 확인한다(두 검사기가 같은 규칙인지).
- build_public_site.sh --dry-run: 데이터 → python 검사 → merge → check-bundle 까지 끝까지.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
NOW = "2026-10-07T05:30:00+09:00"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 가 없다(공개 빌드 검사는 node 22)")


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KBJ_PUBLIC_EXPORT")}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "kbj.services.public_export", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        check=False,
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("pub") / "data"
    p = _cli("build", "--out", str(out), "--now", NOW, "--no-db")
    assert p.returncode == 0, p.stderr
    return out


def test_cli_build_and_check(built: Path) -> None:
    assert sorted(x.name for x in built.iterdir()) == [
        "calendar.json",
        "events.json",
        "manifest.json",
    ]
    p = _cli("check", str(built))
    assert p.returncode == 0, p.stderr
    assert "문제 0건" in p.stdout


def test_cli_check_fails_on_tamper(built: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad"
    shutil.copytree(built, bad)
    env = json.loads((bad / "events.json").read_text(encoding="utf-8"))
    env["source"] = "KIS"
    (bad / "events.json").write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
    p = _cli("check", str(bad))
    assert p.returncode == 1
    assert "KIS" not in p.stderr.replace("로그인 등급 출처", "")  # 값은 출력하지 않는다


def test_cli_rejects_naive_now(tmp_path: Path) -> None:
    p = _cli("build", "--out", str(tmp_path / "x"), "--now", "2026-10-07T05:30:00", "--no-db")
    assert p.returncode == 1
    assert "시간대" in p.stderr


def test_cli_usage_error() -> None:
    assert _cli().returncode == 2


def _plan(manifest_dir: Path) -> dict[str, object]:
    script = (
        "import { planMerge } from './web/scripts/merge-public-data.mjs';\n"
        "import { readdirSync, readFileSync } from 'node:fs';\n"
        "const dir = process.argv[1]; const files = {};\n"
        "for (const n of readdirSync(dir)) files[n] = readFileSync(dir + '/' + n, 'utf8');\n"
        "console.log(JSON.stringify(planMerge(JSON.parse(files['manifest.json']), files)));\n"
    )
    assert NODE is not None
    p = subprocess.run(  # noqa: S603
        [NODE, "--input-type=module", "-e", script, str(manifest_dir)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(p.stdout)


@needs_node
def test_js_merge_accepts_our_output(built: Path) -> None:
    plan = _plan(built)
    assert plan["errors"] == []
    assert plan["copy"] == ["calendar.json", "events.json", "manifest.json"]


@needs_node
def test_js_merge_rejects_planted_login_source(built: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad"
    shutil.copytree(built, bad)
    env = json.loads((bad / "calendar.json").read_text(encoding="utf-8"))
    env["data"]["days"][0]["source"] = "KRX"
    (bad / "calendar.json").write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
    plan = _plan(bad)
    assert plan["copy"] == [] and plan["errors"]


@needs_node
def test_build_public_site_dry_run() -> None:
    env = {**os.environ, "PYTHON": sys.executable}
    env = {k: v for k, v in env.items() if not k.startswith("KBJ_PUBLIC_EXPORT")}
    p = subprocess.run(  # noqa: S603
        ["bash", str(ROOT / "scripts" / "build_public_site.sh"), "--dry-run"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        check=False,
    )
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "dry-run 통과" in p.stdout
    assert "금지 문자열·예산 위반 0건" in p.stdout


def test_build_public_site_usage() -> None:
    p = subprocess.run(  # noqa: S603
        ["bash", str(ROOT / "scripts" / "build_public_site.sh"), "--nope"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert p.returncode == 2

"""`python -m kbj.services.personal calls` — 작업 트리 검사·git 기준 비교·목록, 그리고 레포의
실제 `calls/`(ADR 0042). 콜 내용은 합성, git 저장소는 임시 폴더에 만든다."""

from __future__ import annotations

import io
import shutil
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from kbj.config.files import REPO_ROOT
from kbj.services.personal import calls as calls_cli

NOW = datetime(2026, 10, 27, 3, 0, tzinfo=UTC)  # KST 12:00
REL = "calls/2026/2026-10-27-000001-alpha-thesis.md"
TEMPLATE = (REPO_ROOT / "calls" / "TEMPLATE.md").read_text(encoding="utf-8")


def _call(target: str = "13000", body_extra: str = "") -> str:
    return (
        "---\n"
        "id: 2026-10-27-000001-alpha-thesis\n"
        "opened: 2026-10-27\n"
        "market: KR\n"
        'code: "000001"\n'
        "name: 합성종목\n"
        "direction: long\n"
        "horizon_months: 12\n"
        "sector: 반도체\n"
        "entry_mcap_eok: 10000\n"
        "entry_mcap_as_of: 2026-10-27\n"
        "entry_mcap_source: 합성\n"
        f"target_mcap_eok: {target}\n"
        "benchmark: KOSPI\n"
        "kill_criteria:\n  - 합성 킬 조건\n"
        "status: open\n"
        "---\n\n## 논리\n\n- 합성\n\n## 갱신\n" + body_extra
    )


def _run(repo: Path, *argv: str, now: datetime = NOW) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = calls_cli.cli(list(argv), None, now=lambda: now, repo=repo, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)  # noqa: S603,S607


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:  # pragma: no cover
        pytest.skip("git 없음")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path, "calls/README.md", "# calls\n")
    _write(tmp_path, REL, _call())
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "콜")
    return tmp_path


def test_check_working_tree_ok(repo: Path) -> None:
    code, out, err = _run(repo, "check")
    assert code == 0, err
    assert "콜 1개 · 문제 0개" in out


def test_check_flags_stray_and_broken_files(repo: Path) -> None:
    _write(repo, "calls/note.md", "x")
    _write(repo, "calls/2026/2026-10-27-000002-beta.md", "no front matter")
    code, _out, err = _run(repo, "check")
    assert code == 1
    assert "calls/note.md" in err
    assert "calls/2026/2026-10-27-000002-beta.md" in err


def test_against_ref_blocks_edits_and_deletes(repo: Path) -> None:
    assert _run(repo, "check", "--against", "HEAD")[0] == 0
    _write(repo, REL, _call(target="15000"))
    code, _out, err = _run(repo, "check", "--against", "HEAD")
    assert code == 1
    assert "target_mcap_eok" in err
    _write(repo, REL, _call(body_extra="- 2026-11-01: 갱신\n"))
    assert _run(repo, "check", "--against", "HEAD")[0] == 0
    (repo / REL).unlink()
    code, _out, err = _run(repo, "check", "--against", "HEAD")
    assert code == 1
    assert "지우지 않는다" in err


def test_against_ref_blocks_backdated_new_call(repo: Path) -> None:
    rel2 = "calls/2026/2026-10-20-000002-beta-thesis.md"
    text = _call().replace("2026-10-27-000001-alpha-thesis", "2026-10-20-000002-beta-thesis")
    text = text.replace("2026-10-27", "2026-10-20").replace('"000001"', '"000002"')
    _write(repo, rel2, text)
    code, _out, err = _run(repo, "check", "--against", "HEAD")
    assert code == 1
    assert "소급" in err
    # 기준 없이 형식만 보면 통과(소급 검사는 비교할 때만)
    assert _run(repo, "check")[0] == 0


def test_bad_ref_is_usage_error(repo: Path) -> None:
    assert _run(repo, "check", "--against", "no-such-ref")[0] == 2
    assert _run(repo, "check", "--against=--all")[0] == 2


def test_list(repo: Path) -> None:
    code, out, _err = _run(repo, "list")
    assert code == 0
    assert "KR:000001" in out
    assert "+30.0%" in out


def test_template_filled_in_is_valid(tmp_path: Path) -> None:
    """TEMPLATE.md 를 이름대로 복사하면 그대로 통과해야 한다(예시 값이 규칙과 어긋나지 않게)."""
    _write(tmp_path, "calls/2026/2026-10-27-000000-example-thesis.md", TEMPLATE)
    code, _out, err = _run(tmp_path, "check", now=datetime(2026, 10, 28, tzinfo=UTC))
    assert code == 0, err


def test_repository_calls_are_valid() -> None:
    """레포의 실제 calls/ — 형식·모순 검사(미래 날짜는 CI calls 잡이 오늘 기준으로 본다)."""
    code, _out, err = _run(REPO_ROOT, "check", "--today", date(2100, 1, 1).isoformat())
    assert code == 0, err

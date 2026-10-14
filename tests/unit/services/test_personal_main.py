"""`python -m kbj.services.personal` 진입점 — `--help`·사용법 오류는 DB 없이 끝난다
(P8 WRAP — JNL 요청).

저장소는 처음 쓸 때 만든다(`_LazyRepos`). DB 주소가 없으면 그때 '실패' 한 줄과 종료 코드 2.
"""

from __future__ import annotations

import pytest

from kbj.services import personal


@pytest.mark.parametrize("group", personal.GROUPS)
def test_help_needs_no_database(
    group: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("KBJ_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as ei:
        personal.main([group, "--help"])
    assert ei.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_unknown_group_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert personal.main(["nope"]) == 2
    assert "사용법" in capsys.readouterr().err


def test_missing_database_fails_when_repos_are_first_used(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(personal, "_pg_repos", _no_db)
    assert personal.main(["rules", "list"]) == 2
    assert "KBJ_DATABASE_URL" in capsys.readouterr().err


def _no_db() -> object:
    raise ValueError("KBJ_DATABASE_URL 이 필요하다")

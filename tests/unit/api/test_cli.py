"""`python -m kbj.services.api` — hash-password 는 stdout 에만, openapi --check 는 신선도
(docs/p3_design.md §1.6·§8.4)."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from kbj.services.api.__main__ import OPENAPI_PATH, main, openapi_text
from kbj.services.api.auth import verify_password

PW = "a-long-enough-password"


def _asker(*answers: str):
    it = iter(answers)
    return lambda _prompt: next(it)


def test_hash_password_stdout_only(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    caplog.set_level(logging.DEBUG)
    out, err = io.StringIO(), io.StringIO()
    before = set(tmp_path.iterdir())
    assert main(["hash-password"], out=out, err=err, ask=_asker(PW, PW)) == 0
    line = out.getvalue().strip()
    assert line.startswith("scrypt$n=32768$r=8$p=1$")
    assert verify_password(PW, line)
    assert err.getvalue() == ""
    logs = "\n".join(r.getMessage() for r in caplog.records)
    assert PW not in logs and line not in logs
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize(("a", "b"), [(PW, PW + "x"), ("", ""), ("short", "short")])
def test_hash_password_rejects(a: str, b: str) -> None:
    out, err = io.StringIO(), io.StringIO()
    assert main(["hash-password"], out=out, err=err, ask=_asker(a, b)) == 1
    assert out.getvalue() == ""
    assert PW not in err.getvalue()


def test_openapi_check_fresh() -> None:
    out = io.StringIO()
    assert main(["openapi", "--check"], out=out) == 0, (
        "web/src/api/openapi.json 이 낡았다 — `uv run python -m kbj.services.api openapi`"
    )


def test_openapi_check_detects_stale(tmp_path: Path) -> None:
    p = tmp_path / "openapi.json"
    p.write_text("{}\n", encoding="utf-8")
    assert main(["openapi", "--check", "--out", str(p)], out=io.StringIO()) == 1
    assert main(["openapi", "--out", str(p)], out=io.StringIO()) == 0
    assert main(["openapi", "--check", "--out", str(p)], out=io.StringIO()) == 0
    assert p.read_text(encoding="utf-8") == OPENAPI_PATH.read_text(encoding="utf-8")


def test_openapi_is_deterministic() -> None:
    assert openapi_text() == openapi_text()

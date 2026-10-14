"""P9 GXI 진입점 — `python -m kbj.services.gex_poller --help`·
`python -m kbj.services.engine.gex_report`(probe #61·#62 명령). 설정·DB 없이 안내만 하고,
하위 명령이 틀리면 2."""

from __future__ import annotations

import subprocess
import sys

import pytest

from kbj.services.engine.gex_report.__main__ import main


def test_report_help_lists_both_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "nogap" in out and "shadow" in out


def test_report_without_or_with_an_unknown_subcommand_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([]) == 2
    assert main(["levels"]) == 2
    assert "모르는 하위 명령" in capsys.readouterr().err


@pytest.mark.parametrize("cmd", ["nogap", "shadow"])
def test_report_subcommand_help(cmd: str) -> None:
    with pytest.raises(SystemExit) as e:
        main([cmd, "--help"])
    assert e.value.code == 0


def test_poller_help_needs_no_settings() -> None:
    got = subprocess.run(
        [sys.executable, "-m", "kbj.services.gex_poller", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert got.returncode == 0, got.stderr
    assert "gex-poller" in got.stdout

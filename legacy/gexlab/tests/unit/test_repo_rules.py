"""CLAUDE.md 불변 규칙 중 파일로 확인할 수 있는 것."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_env_is_gitignored() -> None:
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines


def test_no_real_broker_before_phase9() -> None:
    assert not (ROOT / "exec" / "broker_kis_real.py").exists()


def test_env_example_live_trading_false() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "LIVE_TRADING=false" in text

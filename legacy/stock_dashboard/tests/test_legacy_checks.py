"""stock_dashboard 검사 스크립트 래퍼 (KBJ P1).

원본 stock-dashboard 에는 pytest 시험이 없고 `scripts/check_*.py` 10개가 각자 종료코드(0 = 통과)로
결과를 낸다. 여기서는 그 스크립트를 **고치지 않고** 하나씩 subprocess 로 돌려 종료코드 0 을 확인한다.

- `SERVER_NO_STARTUP=1` — server.py 를 import 하는 경로가 있어도 부팅 작업(스케줄러·수집)을 띄우지 않는다.
- 네트워크 없이: 환경을 최소로 새로 만들고(키·토큰 환경변수를 물려주지 않는다) HTTP(S) 프록시를
  닫힌 포트로 돌려, 실수로 나가는 요청이 있으면 바로 실패하게 한다.
- `PYTHONDONTWRITEBYTECODE=1` — legacy 디렉터리에 __pycache__ 를 남기지 않는다.
- `scripts/test_collectors.py`·`test_data_json.py` 는 네이버·yfinance 실호출이라 넣지 않았다
  (U4 대상, P3~P5 에서 KRX·KIS 로 교체 — MIGRATION.md).

실행(이 디렉터리에서): python -m pytest tests -q
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

CHECKS = (
    "check_brief_sections",
    "check_closing_brief",
    "check_etf_marking",
    "check_futures_us_index",
    "check_new_high_logic",
    "check_newhigh_flow",
    "check_newhigh_full_list",
    "check_ohlcv_autofill",
    "check_watchdog_alerts",
    "check_watchdog_gating",
)
DEAD_PROXY = "http://127.0.0.1:9"
TIMEOUT_S = 600


def _offline_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR",
                                      "SYSTEMROOT") if k in os.environ}
    env.update(
        SERVER_NO_STARTUP="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
        HTTP_PROXY=DEAD_PROXY, HTTPS_PROXY=DEAD_PROXY, ALL_PROXY=DEAD_PROXY,
        http_proxy=DEAD_PROXY, https_proxy=DEAD_PROXY, all_proxy=DEAD_PROXY,
        NO_PROXY="", no_proxy="",
    )
    return env


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, env=_offline_env(), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_S, check=False,
    )


def test_check_scripts_listed() -> None:
    """검사 스크립트가 늘거나 줄면 이 목록도 같이 고친다(조용히 빠지는 것 방지)."""
    assert sorted(p.stem for p in SCRIPTS.glob("check_*.py")) == sorted(CHECKS)


@pytest.mark.parametrize("name", CHECKS)
def test_check_script(name: str) -> None:
    proc = _run([str(SCRIPTS / f"{name}.py")])
    assert proc.returncode == 0, (
        f"{name} 종료코드 {proc.returncode}\n--- stdout(끝)\n{proc.stdout[-4000:]}"
        f"\n--- stderr(끝)\n{proc.stderr[-4000:]}"
    )


def test_synthetic_fixtures_reproducible() -> None:
    """합성 시드·인라인 합성 값이 생성기(seed 고정) 출력과 같다(U3)."""
    proc = _run([str(SCRIPTS / "make_synthetic_fixtures.py"), "--check"])
    assert proc.returncode == 0, proc.stdout + proc.stderr

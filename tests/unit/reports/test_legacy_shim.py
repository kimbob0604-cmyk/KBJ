"""legacy dart-report shim 다리 시험 — kbj 쪽에서 legacy 스크립트 둘을 돌린다(docs/p4_design.md
§8.1).

- `tests_smoke.py`(원본 그대로): 출력 `built. quarters= 14 annual= 4 bridge steps= 9` 가 shim 뒤에도
  같다.
- `tests_bridge.py`(P4 신규): `dartreport.*` 이름이 kbj 정본 객체 자체이고, 옛 CLI 의 매핑 기본값이
  옮긴 파일(`config/dart_report/mapping.yaml`)이며, Streamlit app.py 가 없다.

legacy 폴더는 import 루트가 따로라(`dartreport`·`run`) 하위 프로세스에서 그 폴더를 작업 디렉터리로
돌린다(scripts/test_legacy.sh 와 같은 방식). legacy 스크립트를 돌리는 다리 시험이라 `legacy` 표식 —
CI 는 legacy (etf-rest) 잡에서 돈다(P3 관례).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.legacy

ROOT = Path(__file__).resolve().parents[3]
DR = ROOT / "legacy" / "etf_traker" / "dart-report"


def _run(script: str) -> str:
    env = {**os.environ, "PYTHONPATH": str(DR), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(  # noqa: S603 — 고정 인자(현재 해석기 + 레포 안 스크립트)
        [sys.executable, script], cwd=DR, env=env, capture_output=True, text=True, timeout=300,
        check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout


def test_스모크_출력이_shim_뒤에도_같다() -> None:
    out = _run("tests_smoke.py")
    assert out.strip().splitlines()[-1] == "built. quarters= 14 annual= 4 bridge steps= 9"
    smoke = DR / "out" / "_smoke.xlsx"
    assert smoke.is_file()
    smoke.unlink()
    if not any(smoke.parent.iterdir()):
        smoke.parent.rmdir()  # tests_smoke.py 가 만든 폴더(.gitignore legacy/**/out/)


def test_다리_이름이_kbj_정본_객체다() -> None:
    assert _run("tests_bridge.py").strip().splitlines()[-1] == "bridge ok: 5 modules"

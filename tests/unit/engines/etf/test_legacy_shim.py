"""legacy ET `etf_tracker_v9` shim 다리 시험 — themes·collectors·adapters 가 kbj 정본을 쓴다.

P5(ADR 0024, `legacy/etf_traker/MIGRATION.md` 'P5'): `tracker.py`(와 report·render·dash·
live_update)는 kbj `etf.collect`·`etf.report`(`kbj/reports/etf.py`)가 대체해 지웠다. tracker 가
감싸던 계산(펀드 짝·CU 보정 뒤 ±2%p·ETF 제외·수량 하한)은 정본 `kbj.engines.etf.holdings` 의
`tests/unit/engines/etf/test_holdings.py` 가 지킨다 — 여기는 남은 shim 만 본다.

legacy 폴더는 import 루트가 따로라(collectors·themes 같은 일반 이름) 하위 프로세스에서 그 폴더를
작업 디렉터리로 돌린다(scripts/test_legacy.sh 와 같은 방식). 네트워크는 쓰지 않는다 — 어댑터는
가짜 전송.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# legacy 폴더를 하위 프로세스로 도는 다리 시험(P5 전에는 tracker 가 import 시점에 requests —
# legacy 의존성 그룹 — 를 불렀다). dev 만 설치한 CI test (kbj) 잡에서는 빼고, 모든 그룹을
# 설치하는 legacy (etf-rest) 잡에서 `pytest -m legacy` 로 돌린다(건너뛰지 않는다).
pytestmark = pytest.mark.legacy

ROOT = Path(__file__).resolve().parents[4]
ET = ROOT / "legacy" / "etf_traker" / "etf_tracker_v9"

SCRIPT = r"""
import json, sys
from datetime import date
import httpx
sys.path.insert(0, sys.argv[1])
import collectors, themes
from kbj.data.private.etf_issuers.base import IssuerHttp
from tests.fakes.etf_issuer_server import FakeIssuers, funds_of

out = {}
out["classify"] = [themes.classify("KODEX 반도체"), themes.classify("KODEX 레버리지")]
out["issuer"] = themes.issuer_of("UNICORN 무엇")
out["adapters"] = sorted(collectors.ADAPTERS)

# 옛 인터페이스(문자열 날짜·dict 행)로 kbj 어댑터를 부른다 — 가짜 운용사 전송
w = FakeIssuers(latest=date(2026, 10, 6))
collectors._HTTP = IssuerHttp(httpx.Client(transport=w.transport()), sleep=lambda s: None)
k = collectors.ADAPTERS["kodex"]()
uni = k.universe()
rows, real = k.holdings(uni[0][0], "2026-10-07")
out["uni"] = uni[0]
out["real"] = real
out["row"] = sorted(rows.items())[0]
import adapters.rise as R
out["rise"] = [R.Rise.KEY, R.Rise.DEPTH, R.Rise.HISTORY]

print(json.dumps(out, ensure_ascii=False))
"""


def test_legacy_shims_use_kbj() -> None:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))
    p = subprocess.run(  # noqa: S603 — 고정 인자, 이 레포의 해석기
        [sys.executable, "-c", SCRIPT, str(ET)], cwd=ET, env=env, capture_output=True,
        text=True, timeout=120, check=False,
    )  # fmt: skip
    assert p.returncode == 0, p.stderr[-2000:]
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["classify"] == ["반도체", None]
    assert out["issuer"] == "현대차증권"
    # 네이버 TOP10 폴백(NaverTop10)은 웨이브 3 묶음 S 가 지웠다(D-P3-12·U4) — 운용사 9곳만
    assert out["adapters"] == sorted(
        ["kodex", "tiger", "timefolio", "sol", "ace", "hanaro", "koact", "plus", "rise"]
    )
    assert out["uni"][1] == "970000" and out["real"] == "2026-10-06"
    code, row = out["row"]
    assert code.startswith("99") and set(row) == {"name", "qty", "wt", "val"}
    assert out["rise"] == ["rise", "full", True]

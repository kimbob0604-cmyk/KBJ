"""legacy ET `etf_tracker_v9` shim 다리 시험 — tracker·themes·collectors 가 kbj 정본을 쓴다.

legacy 폴더는 import 루트가 따로라(collectors·themes 같은 일반 이름) 하위 프로세스에서 그 폴더를
작업 디렉터리로 돌린다(scripts/test_legacy.sh 와 같은 방식). 네트워크는 쓰지 않는다 — tracker 의
sqlite 는 메모리, 어댑터는 가짜 전송.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
ET = ROOT / "legacy" / "etf_traker" / "etf_tracker_v9"

SCRIPT = r"""
import json, sqlite3, sys
from datetime import date
import httpx
sys.path.insert(0, sys.argv[1])
import collectors, themes, tracker
from kbj.data.private.etf_issuers.base import IssuerHttp
from tests.fakes.etf_issuer_server import FakeIssuers, funds_of

out = {}
out["classify"] = [themes.classify("KODEX 반도체"), themes.classify("KODEX 레버리지")]
out["issuer"] = themes.issuer_of("UNICORN 무엇")
out["adapters"] = sorted(collectors.ADAPTERS)
out["qty_floor"] = tracker.QTY_FLOOR

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

# tracker.analyze(sqlite) == 옛 규칙(CU 보정 뒤 ±2%p, ETF 제외)
c = sqlite3.connect(":memory:")
c.executescript(tracker.DDL)
c.execute("INSERT INTO fund(fund_id, depth) VALUES('f1', 'full')")
c.execute("INSERT INTO etf_ticker VALUES('995010', 'x')")
codes = [f"10{i:03d}0" for i in range(8)]
for d, mult in (("2026-10-06", 1.0), ("2026-10-07", 1.1)):
    for code in codes:
        q = 1000 * mult
        if d == "2026-10-07" and code == codes[0]:
            q = 1150
        c.execute("INSERT INTO holding VALUES('f1', ?, ?, ?, ?, 1.0, 0)", (d, code, code, q))
    c.execute("INSERT INTO holding VALUES('f1', ?, '995010', 'etf', 500, 1.0, 0)", (d,))
c.execute("INSERT INTO holding VALUES('f1', '2026-10-07', '200010', 'new', 50, 1.0, 0)")
out["pairs"] = tracker.fund_pairs(c)
out["changes"] = [(r[3], r[2], r[12], r[13]) for r in tracker.analyze(c, "2026-10-08")]
print(json.dumps(out, ensure_ascii=False))
"""


def test_legacy_tracker_uses_kbj() -> None:
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
    assert out["qty_floor"] == 100
    assert out["uni"][1] == "970000" and out["real"] == "2026-10-06"
    code, row = out["row"]
    assert code.startswith("99") and set(row) == {"name", "qty", "wt", "val"}
    assert out["rise"] == ["rise", "full", True]
    assert out["pairs"] == [["f1", "2026-10-07", "2026-10-06", 1]]
    assert out["changes"] == [["NEW", "200010", None, None], ["ADD", "100000", 15.0, 5.0]]

"""데모 compose 연기 시험 — 떠 있는 데모(docker-compose.demo.yml)에 로그인해 대표 API 를 부른다.

P9 WRAP(docs/p9_design.md §0.3 첫 줄 — CI 잡 `demo (compose)`, ADR 0034). 합성 데이터만 도는
데모라 키가 없다. 순서: `/api/health` 200 → 데모 계정 로그인(`demo` / `KBJ_DEMO_PASSWORD`, 기본은
README 의 고정 데모값) → 페이지 1·2·3·4·6·7·10·11·12·13 대표 경로가 200 이고 `data` 칸이 있다
(5·8 은 404 no_data 도 정상). 응답 본문은 찍지 않는다(경로·상태만).

    uv run python scripts/demo_smoke.py                      # http://127.0.0.1:8765
    uv run python scripts/demo_smoke.py --url http://127.0.0.1:8765 --wait 180

종료 코드 0 = 통과, 1 = 실패, 2 = 서버가 제때 뜨지 않음.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence
from typing import Final

import httpx

DEMO_USER: Final = "demo"
# README 의 고정 데모값(합성 데이터 전용 — p9 §11 Q3). 운영 계정이 아니다
DEFAULT_PASSWORD: Final = "kbj-demo-only"  # noqa: S105
# (페이지, 경로) — tests/unit/demo/test_demo_offline.py 의 대표 경로와 같은 뜻(200 만 고름)
PATHS: Final[tuple[tuple[int, str], ...]] = (
    (1, "/api/market/summary"),
    (2, "/api/board/newhigh"),
    (3, "/api/diag/summary"),
    (3, "/api/gex/levels"),
    (4, "/api/flows/investors"),
    (6, "/api/macro/tabs"),
    (7, "/api/trade/flash"),
    (9, "/api/themes"),
    (9, "/api/themes/strength"),
    (10, "/api/etf/flows"),
    (11, "/api/rules"),
    (12, "/api/backtest/runs"),
    (12, "/api/journal/trades"),
    (13, "/api/ops/status"),
    (13, "/api/ops/jobs"),
)
# 데이터가 없을 수 있는 경로(404 no_data 도 정상 — 화면은 .msg)
OPTIONAL: Final[tuple[tuple[int, str], ...]] = (
    (5, "/api/filings/feed"),
    (8, "/api/stock/search?q=0"),
)


def wait_health(c: httpx.Client, wait_s: float) -> bool:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            if c.get("/api/health").status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2.0)
    return False


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="데모 compose 연기 시험")
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--wait", type=float, default=180.0, help="health 를 기다릴 초")
    args = ap.parse_args(argv)
    password = os.environ.get("KBJ_DEMO_PASSWORD") or DEFAULT_PASSWORD
    origin = args.url.rstrip("/")
    with httpx.Client(base_url=origin, timeout=30.0) as c:
        if not wait_health(c, args.wait):
            print(f"데모가 {args.wait:.0f}초 안에 뜨지 않았다(/api/health)", file=sys.stderr)
            return 2
        r = c.post(
            "/api/auth/login",
            json={"username": DEMO_USER, "password": password},
            headers={"Origin": origin},
        )
        if r.status_code != 200:
            print(f"로그인 실패: HTTP {r.status_code}", file=sys.stderr)
            return 1
        bad: list[str] = []
        for page, path in PATHS:
            r = c.get(path)
            ok = r.status_code == 200 and isinstance(r.json(), dict) and "data" in r.json()
            print(f"페이지 {page:>2} {path}: {r.status_code}{'' if ok else ' ✗'}")
            if not ok:
                bad.append(f"{path} {r.status_code}")
        for page, path in OPTIONAL:
            r = c.get(path)
            ok = r.status_code in (200, 404)
            print(f"페이지 {page:>2} {path}: {r.status_code}{'' if ok else ' ✗'}")
            if not ok:
                bad.append(f"{path} {r.status_code}")
    if bad:
        print(f"실패 {len(bad)}건: {', '.join(bad)}", file=sys.stderr)
        return 1
    print(f"데모 연기 시험 통과: 경로 {len(PATHS) + len(OPTIONAL)}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

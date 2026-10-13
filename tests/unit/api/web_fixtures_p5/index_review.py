"""P5 2차 픽스처 `web/test/fixtures/api/index_review/**` — 지수 정기변경(묶음 IDX).

`/api/index-review*` 응답. 합성 세계(`tests/fixtures/synthetic/index_world.py` — 고정 시드)를
메모리 저장소에 싣고 index.calendar·index.predict 를 돌린 앱
(`tests/unit/api/index_review_world.py`)에서
응답을 `{상대 경로: JSON 글자}` 로 낸다(결정적 — 가짜 시계). 실데이터 없음(구성종목·컷오프 추정은
로그인 등급 — 합성만).
"""

from __future__ import annotations

import json
from typing import Final

from tests.unit.api.index_review_world import index_api_world

__all__ = ["DOMAIN", "STOCK_ADD", "STOCK_DELETE", "render"]

DOMAIN: Final = "index_review"
# 합성 세계의 MSCI 2026-11 편입 후보 1위·편출 후보
# (골든 tests/golden/index_review/expect.json 과 같은 세계)
STOCK_ADD: Final = "993023"
STOCK_DELETE: Final = "993072"
USER_CUTOFF: Final = 2 * 10**12

# (파일, 세계 종류, 경로)
FILES: Final[tuple[tuple[str, str, str], ...]] = (
    ("review_msci.json", "base", "/api/index-review"),
    ("review_kospi200.json", "base", "/api/index-review?index=kospi200"),
    ("review_kosdaq150.json", "base", "/api/index-review?index=kosdaq150"),
    ("review_msci_2026-05.json", "base", "/api/index-review?index=msci_korea&review=2026-05"),
    ("review_user.json", "user", "/api/index-review"),
    ("review_empty.json", "empty", "/api/index-review"),
    (f"stock_{STOCK_ADD}.json", "base", f"/api/index-review/stock/{STOCK_ADD}"),
    (f"stock_{STOCK_DELETE}.json", "base", f"/api/index-review/stock/{STOCK_DELETE}"),
    ("stock_none.json", "base", "/api/index-review/stock/000000"),
)


def render() -> dict[str, str]:
    """도메인 폴더 기준 상대 경로 → JSON 글자(결정적 — 가짜 시계·고정 시드)."""
    clients = {
        "base": index_api_world().authed(),
        "user": index_api_world(user_cutoff=USER_CUTOFF).authed(),
        "empty": index_api_world(members=False).authed(),
    }
    out: dict[str, str] = {}
    for name, world, path in FILES:
        r = clients[world].get(path)
        if r.status_code != 200:
            raise RuntimeError(f"{path}: HTTP {r.status_code}")
        out[name] = json.dumps(r.json(), ensure_ascii=False, indent=2) + "\n"
    return out

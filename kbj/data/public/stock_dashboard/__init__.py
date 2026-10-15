"""stock-dashboard 테마 마스터 어댑터 — 공개 쪽(대테마 분류 데이터셋·raw 파일 클라이언트, ADR 0039).

KBJ 는 네이버에 직접 접속하지 않는다(CLAUDE.md §2). stock-dashboard 레포가 GitHub Actions 로 만든
`data/master/` 파일만 받는다. 네이버 유래 데이터셋은 `kbj.data.private.stock_dashboard`.
"""

from __future__ import annotations

from kbj.data.public.stock_dashboard.client import (
    ThemeMasterClient,
    ThemeMasterError,
    ThemeMasterThrottled,
)
from kbj.data.public.stock_dashboard.datasets import DATASETS

__all__ = ["DATASETS", "ThemeMasterClient", "ThemeMasterError", "ThemeMasterThrottled"]

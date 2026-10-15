"""stock-dashboard 대테마 분류 논리 데이터셋 — **공개**(ADR 0039 — 사용자 결정 2026-10-15).

대테마 분류(id·이름·섹션·밸류체인)는 사용자가 stock-dashboard 에서 직접 만든 것이라 공개 등급이다
(DATA_TIERS §2 9 행 '분류 사전'). 받는 파일은 `meta.json`·`themes.json` 둘이고, themes.json 안의
네이버 테마 목록(`naver`·`excluded_naver`)은 **읽지 않는다**(공개 작업은 사용자 작성 칸만 꺼낸다 —
네이버 유래 자료는 로그인 데이터셋 `STOCK_DASHBOARD:theme_master` 가 받는다). 리미터·주소는 로그인
쪽과 같다.
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import DatasetSpec, Tier

SOURCE: Final = "STOCK_DASHBOARD"
LIMITER: Final = "stock_dashboard"

DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec(
        id=f"{SOURCE}:theme_classes",
        source=SOURCE,
        dataset="theme_classes",
        tier=Tier.PUBLIC,
        limiter=LIMITER,
        budget=None,
        published="stock-dashboard 마스터 갱신 일 06:00 KST — 바뀌면 PR, 머지 뒤 main",
        as_of_kind="run_date",
        store="pub_themes.theme",
        notes=(
            "작업 theme.classes_sync(collectors.theme_classes:run — theme.master_sync 뒤). "
            "raw data/master/meta.json·themes.json 의 대테마 id·name·section·valuechain 만"
        ),
    ),
)

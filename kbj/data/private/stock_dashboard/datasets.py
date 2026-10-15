"""stock-dashboard 테마 마스터 논리 데이터셋(ADR 0039 — 사용자 결정 2026-10-15).

등급 **로그인**: 받는 파일 묶음에 네이버 유래 자료(테마 이름·편입 종목·사유·업종)가 들어 있다(원천의
원천이 네이버 — KBJ 는 네이버에 직접 접속하지 않고 stock-dashboard 가 만든 파일만 받는다). 대테마
분류(사용자가 만든 것)는 공개 데이터셋 `STOCK_DASHBOARD:theme_classes`(공개 작업
theme.classes_sync)가 따로 받는다 — 로그인 원천을 받는 작업은 공개 표에 쓰지 않는다(등록부 규칙, ADR
0002). 리미터 `stock_dashboard`(config/limits.yaml — 키 없음, 주 1회 파일 7개). 일 한도 없음 → 예산
없음.
"""

from __future__ import annotations

from typing import Final

from kbj.data.public.stock_dashboard.datasets import LIMITER, SOURCE
from kbj.data.spec import DatasetSpec, Tier

DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec(
        id=f"{SOURCE}:theme_master",
        source=SOURCE,
        dataset="theme_master",
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=None,
        published="stock-dashboard 마스터 갱신 일 06:00 KST — 바뀌면 PR, 머지 뒤 main",
        as_of_kind="run_date",
        store="prv_themes.member",
        notes=(
            "작업 theme.master_sync(collectors.theme_master:run). raw 파일 data/master/ 7개"
            "(meta·themes·naver_themes·naver_theme_members·stocks·industry_groups·"
            "type_overrides) — "
            "base_url 은 config/theme.yaml. meta.json built_at 이 바뀐 판만 적재"
        ),
    ),
)

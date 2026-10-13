"""미 재무부 논리 데이터셋(공개 — 사용자 결정 2026-10-13 Q8, DATA_TIERS §1 행 추가 요청).

카탈로그 `PLANNED` 의 `TREASURY:yield_curve` 를 이 모듈로 옮기는 일은 FND 몫(MAC 요청).
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import DatasetSpec, Tier

SOURCE: Final = "TREASURY"

DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec(
        id=f"{SOURCE}:yield_curve",
        source=SOURCE,
        dataset="yield_curve",
        tier=Tier.PUBLIC,
        limiter="treasury",
        budget=None,
        published="미국 영업일 장 마감 뒤 [실측 필요 #45]",
        as_of_kind="us_trade_date",
        store="pub_macro.series",
        notes=(
            "Daily Treasury Par Yield Curve CSV(연도 한 해치) — "
            "만기 열 이름이 config/macro.yaml item "
            "[실측 필요 #45]. 작업 macro.morning"
        ),
    ),
)

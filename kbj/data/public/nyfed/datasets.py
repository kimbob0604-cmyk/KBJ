"""뉴욕연은 논리 데이터셋(공개 — 사용자 결정 2026-10-13 Q8, DATA_TIERS §1 행 추가 요청).

카탈로그 `PLANNED` 의 `NYFED:rates` 를 이 모듈로 옮기는 일은 FND 몫(MAC 요청).
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import DatasetSpec, Tier

SOURCE: Final = "NYFED"

DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec(
        id=f"{SOURCE}:rates",
        source=SOURCE,
        dataset="rates",
        tier=Tier.PUBLIC,
        limiter="nyfed",
        budget=None,
        published="다음 영업일 08:00 ET [실측 필요 #45]",
        as_of_kind="us_trade_date",
        store="pub_macro.series",
        notes="SOFR·EFFR(search.json — refRates.percentRate %) [실측 필요 #45]. 작업 macro.morning",
    ),
)

"""FRED 공개 논리 데이터셋 — 작업 등록부·선점·카탈로그용(docs/p5_design.md §1.2·§3.5, D-P5-6).

등급 **공개**: 미국 정부 작성 시리즈(`gov_series`)와 발표 일정(`release_dates`). 저작권 시리즈는
`kbj.data.private.fred_restricted`(로그인). 카탈로그 `PLANNED` 의 같은 id 를 이 모듈로 옮기는 일은
FND 몫(`kbj/data/catalog.py` — MAC 요청). 리미터 `fred`(공개·로그인 같은 버킷 — 2/s [제안]).
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import DatasetSpec, Tier

SOURCE: Final = "FRED"
LIMITER: Final = "fred"

DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec(
        id=f"{SOURCE}:gov_series",
        source=SOURCE,
        dataset="gov_series",
        tier=Tier.PUBLIC,
        limiter=LIMITER,
        budget=None,
        published="시리즈마다 다르다(일별 금리는 다음 영업일 아침 ET) [실측 필요 #45]",
        as_of_kind="us_trade_date",
        store="pub_macro.series",
        notes=(
            "미국 정부 작성 시리즈만(config/macro.yaml dataset FRED:gov_series — DGS10·DGS2·DGS3MO·"
            "T10Y2Y·DFF·CPIAUCSL·PCEPILFE·UNRATE·PAYEMS·GDPC1). 값 '.' = 결측 → None·invalid. "
            "작업 macro.morning"
        ),
    ),
    DatasetSpec(
        id=f"{SOURCE}:release_dates",
        source=SOURCE,
        dataset="release_dates",
        tier=Tier.PUBLIC,
        limiter=LIMITER,
        budget=None,
        published="수시(발표 일정 — FRED releases/dates) [실측 필요 #45]",
        as_of_kind="run_date",
        store="pub_macro.calendar",
        notes=(
            "미국 정부 지표 발표일 → 경제 캘린더 공개 행(source fred, D-P5-8). 발표 이름 → 지표 "
            "종류 대응은 kbj.engines.macro.calendar(FRED_RELEASES). 작업 macro.morning"
        ),
    ),
)

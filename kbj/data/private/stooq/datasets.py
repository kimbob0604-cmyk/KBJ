"""Stooq 논리 데이터셋 — 따로 두지 않는다(빈 묶음).

Stooq CSV 는 Nasdaq 히스토리 실패 종목의 폴백이라 `NASDAQ:daily` 논리 데이터셋 안에 있다(D-P5-18 —
행 source 'stooq'). 리미터는 따로(`config/limits.yaml stooq`).
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import DatasetSpec

SOURCE: Final = "STOOQ"
LIMITER: Final = "stooq"

DATASETS: Final[tuple[DatasetSpec, ...]] = ()

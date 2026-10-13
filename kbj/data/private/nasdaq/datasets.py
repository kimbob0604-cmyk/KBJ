"""Nasdaq 논리 데이터셋 — 작업 등록부·선점·카탈로그용 메타데이터(docs/p5_design.md §1.6·§3.5).

등급 **로그인**(DATA_TIERS §1 — 약관 미확인 [확인 필요 §10 Q6], ADR 0001 Q10). 리미터 `nasdaq`
(config/limits.yaml — 2/s [제안]). 일 한도 공표 없음 → 예산 없음.

⚠ 카탈로그 등록은 FND 몫이다(`kbj/data/catalog.py` — 지금 같은 id 가 `PLANNED` 에 있다). 그 줄을
지우고 `_ADAPTERS` 에 이 `DATASETS` 를 더하면 된다(두 벌 금지 — id 가 겹치면 import 때 실패).
`NASDAQ:daily` 는 Stooq 폴백을 포함한 논리 데이터셋이다(행 source 에 실제 출처 — D-P5-18). Stooq 는
따로 데이터셋을 두지 않는다(`kbj.data.private.stooq.datasets` 는 빈 묶음).
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import DatasetSpec, Tier

SOURCE: Final = "NASDAQ"
LIMITER: Final = "nasdaq"

DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec(
        id=f"{SOURCE}:screener",
        source=SOURCE,
        dataset="screener",
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=None,
        published="수시(주 1회 갱신이면 충분)",
        as_of_kind="run_date",
        store="prv_market.us_universe",
        notes=(
            "미국 유니버스 — 작업 us.universe(collectors.us:universe). api.nasdaq.com 스크리너, "
            "거래소 NASDAQ·NYSE·AMEX(config/us.yaml universe.exchanges). 칸 lastsale·pctchange·"
            "volume·marketCap·sector·industry — 단위(달러·%) [확인 필요 #47]. 약관 미확인 → 로그인"
        ),
    ),
    DatasetSpec(
        id=f"{SOURCE}:daily",
        source=SOURCE,
        dataset="daily",
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=None,
        published="미국 정규장 마감(16:00 ET) 뒤",
        as_of_kind="us_trade_date",
        store="prv_market.daily_bar",
        notes=(
            "미국 종목 일봉 — 작업 us.eod(collectors.us:eod). Nasdaq history(/api/quote/{sym}/"
            "historical — 클래스주 표기 후보를 차례로) 실패 종목은 Stooq CSV 폴백(리미터 stooq). "
            "daily_bar market='US' source 에 실제 출처. 수정주가 여부는 adjust_probe "
            "[확인 필요 #47]"
        ),
    ),
)

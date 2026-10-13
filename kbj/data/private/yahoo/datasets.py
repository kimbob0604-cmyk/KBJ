"""Yahoo 논리 데이터셋 — 작업 등록부·선점·카탈로그용 메타데이터(docs/p5_design.md §1.1·§3.5).

등급 **로그인**(DATA_TIERS §1 — Yahoo 는 약관상 로그인). 카탈로그 PLANNED 에서 옮겼다(P5 FND).
리미터 `yahoo`(config/limits.yaml — 1/s [제안]). 일 한도 공표 없음 → 예산 없음.
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import DatasetSpec, Tier

SOURCE: Final = "YAHOO"
LIMITER: Final = "yahoo"

DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec(
        id=f"{SOURCE}:us_index_daily",
        source=SOURCE,
        dataset="us_index_daily",
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=None,
        published="미국 정규장 마감(16:00 ET) 뒤",
        as_of_kind="us_trade_date",
        store="prv_market.daily_bar",
        notes=(
            "미국 지수 일봉(S&P500·나스닥·다우 등 — 심볼 목록 config/us.yaml, US 묶음) — 작업 "
            "us.eod. "
            "daily_bar market='US' asset='index' source='yahoo'. v8 chart close 는 분할 조정·배당 "
            "미조정(ET parse_yahoo 머리말). 장중 봉 구분 [확인 필요 #46]"
        ),
    ),
    DatasetSpec(
        id=f"{SOURCE}:macro",
        source=SOURCE,
        dataset="macro",
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=None,
        published="미국 정규장 마감 뒤(환율·원자재는 24시간 — 받은 시각 기준)",
        as_of_kind="us_trade_date",
        store="prv_macro.series",
        notes=(
            "SD _MACRO_TICKERS 12종(USD/KRW·USD/JPY·EUR/USD·USD/CNY·WTI·금·은·구리·천연가스·美10년 "
            "^TNX·VIX·BTC — config/macro.yaml 시리즈, MAC 묶음) — 작업 macro.morning_prv. 심볼 "
            "응답·"
            "한도·429 형태 [확인 필요 #46]"
        ),
    ),
)

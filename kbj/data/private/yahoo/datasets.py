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
    DatasetSpec(
        id=f"{SOURCE}:global_quotes",
        source=SOURCE,
        dataset="global_quotes",
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=None,
        published="해외 장중 실시간(지연 시세) — 5분마다",
        as_of_kind="minute",
        store="prv_market.global_quote",
        notes=(
            "글로벌 프록시(2026-11-02 — stock-dashboard 화면을 옮김): "
            "해외 상장 SKHY·SMSN.IL·HY9H.F, 선물 NQ=F·ES=F, ^TNX·CL=F·KRW=X·EURUSD=X, "
            "미국 ETF DRAM·EWY·SOXX. v8 chart range=1d 의 "
            "meta.regularMarketPrice·chartPreviousClose. 작업 global.proxy"
        ),
    ),
    DatasetSpec(
        id=f"{SOURCE}:global_daily",
        source=SOURCE,
        dataset="global_daily",
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=None,
        published="각 거래소 마감 뒤(미국 16:00 ET·도쿄 15:30 JST·서울 15:30 KST)",
        as_of_kind="run_date",
        store="prv_market.global_daily",
        notes=(
            "상대 시총·연도별 수익률(2026-11-02 — stock-dashboard 화면을 옮김): "
            "종목(.KS·미국·일본)·"
            "환율 KRW=X·JPY=X 일봉 2년, 지수 ^GSPC·^IXIC·^KS11·^KQ11 월봉 전 기간(period1=0 — "
            "range=max 월봉은 석 달씩 묶인다). 진행 중인 봉은 쓰지 않는다. 작업 global.daily"
        ),
    ),
    DatasetSpec(
        id=f"{SOURCE}:quote_caps",
        source=SOURCE,
        dataset="quote_caps",
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=None,
        published="장중 실시간(지연) — 하루 한 번 받는다",
        as_of_kind="run_date",
        store="prv_market.global_cap",
        notes=(
            "해외 종목 현재 시가총액(현지 통화) — v7 quote(쿠키 fc.yahoo.com + crumb). MU·TSM·AVGO·"
            "NVDA·6981.T·4062.T. 국내 시총은 스냅을 쓴다. 작업 global.daily"
        ),
    ),
)

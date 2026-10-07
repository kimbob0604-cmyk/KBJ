"""금투협 종합통계 논리 데이터셋 — 작업 등록부·선점·카탈로그용 메타데이터(설계 §2·§6.7).

등급 **공개**(15094809 포털 "제한 없음", 공공누리 1유형 — DATA_TIERS §1). 저장 표
`pub_market_stats.*`(마이그레이션 0011, P5). 수집 작업 `market_stats.kofia`(13:30, P5)가
`@ prev_trading_day` 로 받는다. 리미터·예산은 포털 ID `15094809` 하나를 네 오퍼레이션이 나눠 쓴다.
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import DatasetSpec, Tier

SOURCE: Final = "DATAGO"
LIMITER: Final = "datago"
_PUBLISHED: Final = "적재 일 1회, 연계 당일 13시 이후 개방 — 영업일 D 값이 D+1 13시 [실측 필요]"


def _kofia(op: str, *, store: str, notes: str) -> DatasetSpec:
    dataset = f"15094809/{op}"
    return DatasetSpec(
        id=f"{SOURCE}:{dataset}",
        source=SOURCE,
        dataset=dataset,
        tier=Tier.PUBLIC,
        limiter=LIMITER,
        budget=LIMITER,
        published=_PUBLISHED,
        as_of_kind="prev_trading_day",
        store=store,
        notes=notes,
    )


DATASETS: Final[tuple[DatasetSpec, ...]] = (
    _kofia(
        "credit",
        store="pub_market_stats.credit_balance",
        notes=(
            "getGrantingOfCreditBalanceInfo 신용공여잔고추이 — 상단 띠 신용잔고 변화. "
            "단위 원 [실측 필요]"
        ),
    ),
    _kofia(
        "capital",
        store="pub_market_stats.market_capital",
        notes=(
            "getSecuritiesMarketTotalCapitalInfo 증시자금추이 — 투자자예탁금·반대매매. 오퍼레이션 "
            "이름 대소문자 [실측 필요]"
        ),
    ),
    _kofia(
        "fund",
        store="pub_market_stats.fund_nav",
        notes=(
            "getFundTotalNetEssetInfo 펀드순자산총액 — basDt·ctg·tstMthdCtg 필수 표기 [실측 필요]"
        ),
    ),
    _kofia(
        "cma",
        store="pub_market_stats.cma",
        notes="getCMAStatus 일자별 CMA 현황 — 대기 자금(보조)",
    ),
)

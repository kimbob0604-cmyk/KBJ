"""ECOS 논리 데이터셋(공개 — 한국은행 작성 표만) — 작업 등록부·선점·카탈로그용(설계 §2·§6.7).

저장 표는 `pub_macro.series`(마이그레이션 0011, P5 — 시리즈 단위 등급). 타기관 작성 표(802Y001·
731Y001·901Y056·901Y009)는 로그인 등급이라 여기 없다(`kbj.data.private.ecos_restricted` — 묶음 D,
`prv_macro.series`). 수집 작업: `macro.evening`(17:00 — 817Y002·722Y001 @ trade_date),
`macro.monthly`(09:10 — 월간 표 @ month), P5. 표·항목 코드는 `docs/probe_results.md` §4.3.
"""

from __future__ import annotations

from typing import Final

from kbj.data.public.ecos.client import PUBLIC_TABLES
from kbj.data.spec import AsOfKind, DatasetSpec, Tier

SOURCE: Final = "ECOS"
LIMITER: Final = "ecos"
STORE: Final = "pub_macro.series"


def _ecos(stat_code: str, *, as_of: AsOfKind, published: str, notes: str) -> DatasetSpec:
    if stat_code not in PUBLIC_TABLES:
        raise ValueError(f"공개 데이터셋은 한국은행 작성 표만: {stat_code}")
    return DatasetSpec(
        id=f"{SOURCE}:{stat_code}",
        source=SOURCE,
        dataset=stat_code,
        tier=Tier.PUBLIC,
        limiter=LIMITER,
        budget=None,  # 일 한도 미공표 — 602 로만 막는다(probe_results §4.1)
        published=published,
        as_of_kind=as_of,
        store=STORE,
        notes=notes,
    )


_MONTHLY: Final = "월간 — 공표일은 지표마다 다르다 [실측 필요]"

DATASETS: Final[tuple[DatasetSpec, ...]] = (
    _ecos(
        "817Y002",
        as_of="trade_date",
        published="당일 17시경 반영(2026-10-06 조사 때 당일 값 있음), 기존 배치 16:30 [실측 필요]",
        notes=(
            "시장금리(일별). 국고채 3년 010200000·10년 010210000·1·2·5·20·30·50년, "
            "회사채 3년 AA- 010300000·BBB- 010320000(공개 신용스프레드), 통안·CD·CP·콜·KOFR"
        ),
    ),
    _ecos(
        "722Y001",
        as_of="trade_date",
        published="금통위 결정일 반영(일·월·분기·연 주기)",
        notes="한국은행 기준금리 및 여수신금리 — 기준금리 0101000",
    ),
    _ecos(
        "721Y001",
        as_of="month",
        published=_MONTHLY,
        notes="시장금리(월·분기·연) — 항목 코드 [실측 필요]",
    ),
    _ecos(
        "404Y014",
        as_of="month",
        published=_MONTHLY,
        notes="생산자물가지수(기본분류) 총지수 *AA, 2020=100",
    ),
    _ecos(
        "402Y014",
        as_of="month",
        published=_MONTHLY,
        notes="수출물가지수(기본분류) 총지수 *AA(W 원화·C 계약통화·D 달러), 2020=100",
    ),
    _ecos(
        "401Y015",
        as_of="month",
        published=_MONTHLY,
        notes="수입물가지수(기본분류) 총지수 *AA(W·C·D), 2020=100",
    ),
    _ecos(
        "161Y005",
        as_of="month",
        published=_MONTHLY,
        notes="M2 평잔 계절조정 BBHS00(십억원). 옛 표 101Y003 은 2004-09 에서 끝나 쓰지 않는다",
    ),
    _ecos(
        "301Y013",
        as_of="month",
        published=_MONTHLY,
        notes="국제수지 — 상품수지 100000, 경상수지 [실측 필요](백만달러)",
    ),
    _ecos(
        "513Y001",
        as_of="month",
        published=_MONTHLY,
        notes="경제심리지수 — 항목 코드 [실측 필요]",
    ),
    _ecos(
        "200Y102",
        as_of="quarter",
        published="분기 — 속보·잠정 공표일 [실측 필요]",
        notes="주요지표(분기지표) GDP 실질·계절조정·전기비 10111",
    ),
    _ecos(
        "731Y003",
        as_of="trade_date",
        published="일별 [실측 필요: 주기·반영 시각]",
        notes=(
            "원화의 대미달러·대위안·대엔 환율(한국은행 작성 — 공개용 환율). 항목 코드 [실측 필요]. "
            "731Y001(서울외국환중개)은 로그인"
        ),
    ),
)

"""관세청 수출입 논리 데이터셋 — 작업 등록부·선점·카탈로그용 메타데이터(설계 §2·§6.7).

등급은 모두 **공개**(이용허락 "제한 없음" — DATA_TIERS §1, probe_results §2.1)라 저장 표는
`pub_trade.*`(마이그레이션 0012, P6). 수집 작업(`trade.customs_tenday`·
`trade.customs_monthly`)은 P6. 리미터·예산은 포털 데이터셋 ID 마다(`limits.yaml` `datago` —
25/s, 9,500/일).
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import AsOfKind, DatasetSpec, Tier

SOURCE: Final = "DATAGO"
LIMITER: Final = "datago"


def _dg(portal_id: str, *, as_of: AsOfKind, store: str, published: str, notes: str) -> DatasetSpec:
    return DatasetSpec(
        id=f"{SOURCE}:{portal_id}",
        source=SOURCE,
        dataset=portal_id,
        tier=Tier.PUBLIC,
        limiter=LIMITER,
        budget=LIMITER,
        published=published,
        as_of_kind=as_of,
        store=store,
        notes=notes,
    )


_MONTHLY: Final = "매월 15일경 전월까지 현행화(정정·취하 반영) [실측 필요: 반영 시각]"
_TEN_DAY: Final = "1~10일분 11일, 1~20일분 21일, 월 전체 익월 1일 [실측 필요: 반영 시각]"

DATASETS: Final[tuple[DatasetSpec, ...]] = (
    _dg(
        "15100475",
        as_of="month",
        store="pub_trade.item_country_monthly",
        published=_MONTHLY,
        notes=(
            "품목별 국가별 수출입실적 getNitemtradeList. 국가(cntyCd) 필수, 조회기간 12개월 이내. "
            "hsSgn 생략 시 전 품목인지·자릿수 [실측 필요]. 운영 심의승인"
        ),
    ),
    _dg(
        "15101609",
        as_of="month",
        store="pub_trade.item_monthly",
        published=_MONTHLY,
        notes=(
            "품목별 수출입실적 getItemtradeList. hsCode 숫자형 앞자리 0 손실 보정. "
            "기간 한도·월 합계 = 관세청 확정 발표 [실측 필요]. 운영 심의승인"
        ),
    ),
    _dg(
        "15101612",
        as_of="month",
        store="pub_trade.country_monthly",
        published=_MONTHLY,
        notes="국가별 수출입실적 getNationtradeList. 중량 없음·건수 있음. 운영 자동승인",
    ),
    _dg(
        "15134343",
        as_of="month",
        store="pub_trade.sigungu_monthly",
        published=_MONTHLY,
        notes=(
            "시군구별 품목별 getSigunguPerPrlstPerAcrs. 시도(sidoCd)×HS6(HsSgn) 필수, 시군구는 "
            "이름만. 2026-07 시도 코드 개편(12 신설, 29·46 은 이전 신고분). 관심 HS6 만 받는다 "
            "(probe_results F3). 운영 심의승인"
        ),
    ),
    _dg(
        "15157908",
        as_of="ten_day",
        store="pub_trade.ten_day",
        published=_TEN_DAY,
        notes="수출 주요품목별 10일 단위 잠정치. 고정 11열(itemUsdAmt00~10, 천 달러) — 열 대응",
    ),
    _dg(
        "15157941",
        as_of="ten_day",
        store="pub_trade.ten_day",
        published=_TEN_DAY,
        notes="수출 주요국가별 10일 단위 잠정치. 고정 11열 — 열 대응 저장",
    ),
    _dg(
        "15157901",
        as_of="ten_day",
        store="pub_trade.ten_day",
        published=_TEN_DAY,
        notes="수입 주요품목별 10일 단위 잠정치. 고정 11열 — 열 대응 저장",
    ),
    _dg(
        "15157909",
        as_of="ten_day",
        store="pub_trade.ten_day",
        published=_TEN_DAY,
        notes=(
            "수입 주요국가별 10일 단위 잠정치. 참고문서 없음 — 10년 한도·형식은 같은 계열 기준 "
            "[실측 필요]"
        ),
    ),
)

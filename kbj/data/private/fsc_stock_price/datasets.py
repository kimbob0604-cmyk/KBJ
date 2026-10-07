"""금융위 주식시세 논리 데이터셋 — 작업 등록부·선점·카탈로그용 메타데이터(설계 §2).

등급 **로그인**(공공누리 4유형, 제3자 제공·재배포 금지 — 원천 KRX, DATA_TIERS §1). 저장 표
`prv_market.daily_bar`(`source='fsc'` — KRX 일별 교차검증용, 마이그레이션 0003). 수집 작업은 P3
이후(등록부 행은 F 담당 — 작업 이름 미정 [확인 필요]). 리미터·예산은 포털 ID `15094808` 하나
(config/limits.yaml `datago` — 25/s, 9,500/일).
"""

from __future__ import annotations

from typing import Final

from kbj.data.private.fsc_stock_price.client import DATASET_ID, PATH
from kbj.data.spec import DatasetSpec, Tier, Venue

SOURCE: Final = "DATAGO"
LIMITER: Final = "datago"

DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec(
        id=f"{SOURCE}:{DATASET_ID}",
        source=SOURCE,
        dataset=DATASET_ID,
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=LIMITER,
        published="영업일 D+1 13시 이후 일 1회(probe_results §3.3) [실측 필요]",
        as_of_kind="prev_trading_day",
        store="prv_market.daily_bar",
        notes=(
            f"{PATH}(V2 — F7). 필드 basDt·srtnCd·isinCd·itmsNm·mrktCtg·clpr·vs·fltRt·mkp·hipr·"
            "lopr·trqu·trPrc(거래대금, 원)·lstgStCnt·mrktTotAmt(원) — V1 문서 기준, V2 차이 "
            "[실측 필요 — probe_results §7 #8]. 비수정 가격. KRX:sto/*_bydd_trd 와 대조하는 "
            "교차검증 출처(source='fsc'). 원천 KRX 라 NXT 체결분 포함 여부 [실측 필요]"
        ),
        venues=(Venue.KRX,),
    ),
)

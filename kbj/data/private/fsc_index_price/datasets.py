"""금융위 지수시세 논리 데이터셋 — 작업 등록부·선점·카탈로그용 메타데이터(설계 §2).

등급 **로그인**(공공누리 4유형 — 원천 KRX). 저장 표 `prv_market.daily_bar`(asset=index,
`source='fsc'` — KRX 지수 일별 교차검증용). 포털 ID `15094807` 은 **[확인 필요]**(DATA_TIERS 에 지수
시세정보 ID 없음 — client 머리말). 수집 작업은 P3 이후(등록부 행은 F 담당).
"""

from __future__ import annotations

from typing import Final

from kbj.data.private.fsc_index_price.client import DATASET_ID, PATH
from kbj.data.spec import DatasetSpec, Tier

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
        published="영업일 D+1 13시 이후 일 1회(주식시세와 같다고 가정) [실측 필요]",
        as_of_kind="prev_trading_day",
        store="prv_market.daily_bar",
        notes=(
            f"{PATH}(ET V1 경로 — V2 존재 여부 [실측 필요]). 포털 ID [확인 필요]. 필드 basDt·idxNm·"
            "idxCsf·clpr·vs·fltRt·mkp·hipr·lopr·trqu·trPrc·lstgMrktTotAmt [실측 필요]. "
            "KRX:idx/*_dd_trd 와 대조하는 교차검증 출처(source='fsc')"
        ),
    ),
)

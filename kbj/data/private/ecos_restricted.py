"""한국은행 ECOS 의 **타기관 작성 표** — 로그인 등급(DATA_TIERS §1, probe_results F6·§4.3).

ECOS 약관상 통계 저작권은 통계작성기관에 있다(제2조5). 공개판은 한국은행 작성 표만 쓰고
(`kbj.data.public.ecos.client.PUBLIC_TABLES`), 다른 기관이 작성한 표는 여기서만 받는다:

| 표 | 작성기관 | 쓰임 |
|---|---|---|
| 802Y001 주식시장(일) | 한국거래소 | 코스피·외국인 순매수(로그인판) — 항목 `0001000`·`0030000` |
| 731Y001 대원화환율 | 서울외국환중개 | 원/달러 `0000001`·위안 `0000053`·엔 `0000002` |
| 901Y056 증시주변자금 | 금융투자협회 | 월 단위 대조(일 단위는 공개 15094809) |
| 901Y009 소비자물가지수 | 국가데이터처 | 공개 판정 전(probe_results §7 #11) — 판정 전까지 로그인 |

- 전송·쪽 넘기기·오류 처리(`INFO-200` 빈 결과, `602` 감속·그날 닫기, `400` 기간 나누기)는 공개 쪽
  `kbj.data.public.ecos.client.EcosTransport`(묶음 C)를 그대로 쓴다 — 로그인 → 공개 import 는 계약
  ①이 허용한다(반대는 금지). 같은 ECOS 키라 리미터도 같은 버킷 `rl:ecos:<키 해시>` 다.
- 이 입구는 **`RESTRICTED_TABLES` 의 표만** 받는다. 공개 표(한국은행 작성)를 여기로 받으면 `prv_*`
  에 쌓여 공개판에 못 쓰게 되므로 `TierError` 로 거절하고, 목록에 없는 표도 등급 판정 전이라
  거절한다(판정 뒤 `RESTRICTED_TABLES` 에 더한다 — 묶음 C 파일).
- 저장 표 `prv_macro.series`(마이그레이션 0011, P5 — 시리즈 단위 등급). 수집 작업은 P5.
"""

from __future__ import annotations

from typing import Any, Final

import httpx
from pydantic import SecretStr
from redis import Redis

from kbj.config.settings import Settings
from kbj.data.limits import Limits, load_limits
from kbj.data.public.ecos.client import (
    LIMITS_SOURCE,
    PUBLIC_TABLES,
    RESTRICTED_TABLES,
    TIMEOUT_S,
    EcosItem,
    EcosKeyError,
    EcosRow,
    EcosTransport,
    TierError,
)
from kbj.data.ratelimit import Clock, RateLimiter, RedisRateLimiter
from kbj.data.spec import AsOfKind, DatasetSpec, Tier

SOURCE: Final = "ECOS"
STORE: Final = "prv_macro.series"


def require_restricted(stat_code: str) -> None:
    """로그인 등급(타기관 작성) 표가 아니면 `TierError`."""
    if stat_code in RESTRICTED_TABLES:
        return
    if stat_code in PUBLIC_TABLES:
        raise TierError(
            f"ECOS {stat_code}: 한국은행 작성 공개 표 — kbj.data.public.ecos 로 받는다"
            "(로그인 쪽에 받으면 prv_* 에 쌓여 공개판에 못 쓴다)"
        )
    raise TierError(
        f"ECOS {stat_code}: 등급 판정 전 표 — 작성기관(StatisticTableList ORG_NAME)을 확인해 "
        "공개면 PUBLIC_TABLES, 타기관이면 RESTRICTED_TABLES 에 더한다"
    )


class EcosRestrictedClient:
    """로그인 등급 ECOS — `RESTRICTED_TABLES`(타기관 작성 표)만 받는다."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        limiter: RateLimiter,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_S,
        **kw: Any,
    ) -> None:
        self.transport = EcosTransport(
            api_key, limiter=limiter, transport=transport, timeout=timeout, **kw
        )

    def __repr__(self) -> str:
        return "EcosRestrictedClient(key=***)"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> EcosRestrictedClient:
        """`KBJ_ECOS_KEY` + 공개 쪽과 같은 Redis 리미터 `rl:ecos:<키 해시>`(limits.yaml `ecos`)."""
        if settings.ecos_key is None:
            raise EcosKeyError("", "KBJ_ECOS_KEY 가 없다")
        src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
        limiter = RedisRateLimiter.scoped(
            redis, LIMITS_SOURCE, settings.ecos_key.get_secret_value(), src.rate_config(), clock
        )
        close_after = src.close_after_throttles or 3
        return cls(settings.ecos_key, limiter=limiter, close_after_throttles=close_after, **kw)

    def close(self) -> None:
        self.transport.close()

    def search(
        self, stat_code: str, cycle: str, start: str, end: str, items: tuple[str, ...] = ()
    ) -> list[EcosRow]:
        """값 조회(StatisticSearch). 타기관 표가 아니면 `TierError`(부르지 않는다)."""
        require_restricted(stat_code)
        return self.transport.statistic_search(stat_code, cycle, start, end, items)

    def item_list(self, stat_code: str) -> list[EcosItem]:
        """항목 목록(StatisticItemList). 타기관 표만."""
        require_restricted(stat_code)
        return self.transport.statistic_item_list(stat_code)


# ── 논리 데이터셋(로그인) — 카탈로그(묶음 F)가 모은다 ─────────────────────────────────────────


def _ecos(stat_code: str, *, as_of: AsOfKind, published: str, notes: str) -> DatasetSpec:
    if stat_code not in RESTRICTED_TABLES:
        raise ValueError(f"로그인 데이터셋은 RESTRICTED_TABLES 의 표만: {stat_code}")
    return DatasetSpec(
        id=f"{SOURCE}:{stat_code}",
        source=SOURCE,
        dataset=stat_code,
        tier=Tier.PRIVATE,
        limiter=LIMITS_SOURCE,
        budget=None,  # 일 한도 미공표 — 602 로만 막는다(probe_results §4.1)
        published=published,
        as_of_kind=as_of,
        store=STORE,
        notes=f"작성기관 {RESTRICTED_TABLES[stat_code]}. {notes}",
    )


DATASETS: Final[tuple[DatasetSpec, ...]] = (
    _ecos(
        "802Y001",
        as_of="trade_date",
        published="일별 — 반영 시각 [실측 필요]",
        notes="주식시장(일) — 코스피 0001000·외국인 순매수 0030000(억원)",
    ),
    _ecos(
        "731Y001",
        as_of="trade_date",
        published="일별 — 반영 시각 [실측 필요]",
        notes=(
            "주요국 통화의 대원화환율 — 원/달러 매매기준율 0000001·위안 0000053·엔 0000002. "
            "공개용 환율은 한국은행 작성 731Y003(공개 쪽)"
        ),
    ),
    _ecos(
        "901Y056",
        as_of="month",
        published="월간 [실측 필요]",
        notes=(
            "증시주변자금 — 예탁금 S23A·장내파생 예수금 S23B·RP S23C·미수금 S23D·신용융자 S23E·"
            "신용대주 S23F(원). 일 단위 공개값은 DATAGO:15094809/capital·/credit"
        ),
    ),
    _ecos(
        "901Y009",
        as_of="month",
        published="월간 [실측 필요]",
        notes=(
            "소비자물가지수 총지수 0(2020=100). 공개 판정 전(probe_results §7 #11) — 공개판은 "
            "KOSIS DT_1J22003 으로 같은 지수를 받는다"
        ),
    ),
)

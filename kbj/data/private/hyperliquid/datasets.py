"""Hyperliquid 논리 데이터셋 — 글로벌 프록시(장 밖 한국 주식 가늠, stock-dashboard 화면을 옮김).

등급 **로그인**(거래소 시세 — 공개 내보내기에 싣지 않는다). 키 없음. 리미터 `hyperliquid`
(config/limits.yaml — 5분에 1건). 일 한도 공표 없음 → 예산 없음.
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import DatasetSpec, Tier

SOURCE: Final = "HYPERLIQUID"
LIMITER: Final = "hyperliquid"

DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec(
        id=f"{SOURCE}:perp_ctx",
        source=SOURCE,
        dataset="perp_ctx",
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=None,
        published="24시간 실시간(무기한 선물)",
        as_of_kind="minute",
        store="prv_market.global_quote",
        notes=(
            "POST /info {type: metaAndAssetCtxs, dex: xyz} — 삼성전자(xyz:SMSN)·"
            "SK하이닉스(xyz:SKHX)·"
            "SK하이닉스 ADR(xyz:SKHY)·현대차(xyz:HYUNDAI)·코스피200(xyz:KR200). markPx·prevDayPx·"
            "funding(1시간)·dayNtlVlm. 작업 global.proxy(collectors.global_proxy:run)"
        ),
    ),
)

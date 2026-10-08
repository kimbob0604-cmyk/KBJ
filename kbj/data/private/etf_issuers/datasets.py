"""ETF 운용사 논리 데이터셋 — 작업 등록부·선점·카탈로그가 쓰는 메타데이터(docs/p3_design.md §3.3).

`kbj/data/catalog.py` 의 `PLANNED` 에 있던 `ETF_ISSUERS:pdf` 를 옮겼다(두 벌 금지 — 카탈로그 시험이
겹침을 잡는다). 저장 표는 0009 `prv_etf.holding`(P2 계획의 `prv_etf.holdings` 를 확정 이름으로).
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import DatasetSpec, Tier

SOURCE: Final = "ETF_ISSUERS"
LIMITER: Final = "etf_issuers"  # config/limits.yaml — 운용사 호스트별 scoped 리미터 [제안]

DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec(
        id=f"{SOURCE}:pdf",
        source=SOURCE,
        dataset="pdf",
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=None,
        published="운용사마다 다르다(대개 장 시작 전 — 전 거래일 기준 PDF)",
        as_of_kind="trade_date",
        store="prv_etf.holding",
        notes=(
            "ETF 구성종목(PDF) — 운용사 9곳(KODEX·TIGER·TIMEFOLIO·SOL·ACE·HANARO·KoAct·PLUS·RISE). "
            "약관이 운용사마다 달라 로그인 등급. 작업 etf.collect(P3 로 당김 — D-P3-12). "
            "운용사 실응답·약관 [실측 필요 — 체크리스트 #27]. 네이버 TOP10 폴백 없음(U4). "
            "펀드 목록은 prv_etf.fund, 변동은 prv_etf.change_log(엔진 kbj.engines.etf.holdings)"
        ),
    ),
)

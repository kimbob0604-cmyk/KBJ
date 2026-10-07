"""데이터셋 카탈로그 — 논리 데이터셋 전부를 한곳에 모은다(설계 §1.7·§6.5, §11.3).

- 각 어댑터의 `datasets.py`(또는 모듈 안 `DATASETS`)를 **명시적으로** 모은다. 자동 탐색은
  하지 않는다 — 새 어댑터는 아래 `_ADAPTERS` 에 한 줄을 더한다.
- 어댑터가 아직 없는 출처(P3~P5 — NASDAQ·Yahoo·FRED·미 재무부·뉴욕연은·ForexFactory·Anthropic·
  ETF 운용사)의 데이터셋은 `PLANNED` 로 여기 둔다. 작업 등록부(`config/jobs.yaml`)가 그 데이터 키를
  P2 부터 등록해 두어 다른 작업이 같은 데이터를 받지 못하게 하려는 것이다(설계 §6.5). 어댑터가
  생기면 그 `datasets.py` 로 옮기고 여기서 지운다(두 벌 금지 — 시험이 겹침을 잡는다).
- 등급은 DATA_TIERS §1 을 따른다: 애매하면 로그인. FRED 는 미국 정부 시리즈만 공개, 미 재무부·
  뉴욕연은은 미국 정부 공표 자료라 공개로 두되 [확인 필요].
- id 가 겹치면 import 할 때 실패한다(조용히 덮어쓰지 않는다).
- 데이터 키를 만들 때 거래소 구분(`venues` — KRX·NXT·TOTAL)이 있는 데이터셋은 `keys_for` 가
  거래소마다 하나씩 펼친다(묶음 A·D 요청 — `DatasetSpec.key` 는 venue 가 필수).
"""

from __future__ import annotations

import functools
from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import Final

from kbj.data.private import ecos_restricted
from kbj.data.private.fsc_index_price import datasets as fsc_index_datasets
from kbj.data.private.fsc_stock_price import datasets as fsc_stock_datasets
from kbj.data.private.kis import datasets as kis_datasets
from kbj.data.private.krx import datasets as krx_datasets
from kbj.data.public.customs import datasets as customs_datasets
from kbj.data.public.dart import datasets as dart_datasets
from kbj.data.public.ecos import datasets as ecos_datasets
from kbj.data.public.fsc_kofia_stats import datasets as kofia_datasets
from kbj.data.public.kosis import datasets as kosis_datasets
from kbj.data.spec import AsOfKind, DataKey, DatasetSpec, Tier, Venue

__all__ = [
    "PLANNED",
    "UnknownDataset",
    "all_datasets",
    "claimable",
    "get",
    "keys_for",
]


class UnknownDataset(KeyError):
    """카탈로그에 없는 데이터셋 id."""


def _planned(
    source: str,
    dataset: str,
    *,
    tier: Tier,
    limiter: str,
    as_of: AsOfKind,
    store: str,
    published: str,
    notes: str,
) -> DatasetSpec:
    return DatasetSpec(
        id=f"{source}:{dataset}",
        source=source,
        dataset=dataset,
        tier=tier,
        limiter=limiter,
        budget=None,
        published=published,
        as_of_kind=as_of,
        store=store,
        notes=f"[어댑터 없음 — 계획] {notes}",
    )


# 어댑터가 아직 없는 출처 — 리미터 이름은 그 어댑터가 생길 때 limits.yaml 에 더한다 [확인 필요].
PLANNED: Final[tuple[DatasetSpec, ...]] = (
    _planned(
        "NASDAQ",
        "screener",
        tier=Tier.PRIVATE,
        limiter="nasdaq",
        as_of="run_date",
        store="prv_market.us_universe",
        published="수시(주 1회 갱신이면 충분)",
        notes="미국 유니버스 — 작업 us.universe(P3). 약관 미확인이라 로그인 [확인 필요]",
    ),
    _planned(
        "NASDAQ",
        "daily",
        tier=Tier.PRIVATE,
        limiter="nasdaq",
        as_of="us_trade_date",
        store="prv_market.us_daily_bar",
        published="미국 정규장 마감(16:00 ET) 뒤",
        notes="미국 종목 일봉 — 작업 us.eod(P3). ET us-board 의 Nasdaq 출처",
    ),
    _planned(
        "YAHOO",
        "us_index_daily",
        tier=Tier.PRIVATE,
        limiter="yahoo",
        as_of="us_trade_date",
        store="prv_market.us_daily_bar",
        published="미국 정규장 마감 뒤",
        notes="미국 지수 일봉 — 작업 us.eod(P3). Yahoo 는 로그인(DATA_TIERS §1)",
    ),
    _planned(
        "YAHOO",
        "macro",
        tier=Tier.PRIVATE,
        limiter="yahoo",
        as_of="us_trade_date",
        store="prv_macro.series",
        published="미국 정규장 마감 뒤",
        notes="달러지수·원자재·VIX 등 — 작업 macro.morning(P5)",
    ),
    _planned(
        "FRED",
        "gov_series",
        tier=Tier.PUBLIC,
        limiter="fred",
        as_of="us_trade_date",
        store="pub_macro.series",
        published="시리즈마다 다르다 [실측 필요]",
        notes=(
            "미국 정부 시리즈만(저작권 시리즈는 로그인 — 따로 데이터셋을 둔다). "
            "작업 macro.morning(P5)"
        ),
    ),
    _planned(
        "TREASURY",
        "yield_curve",
        tier=Tier.PUBLIC,
        limiter="treasury",
        as_of="us_trade_date",
        store="pub_macro.series",
        published="미국 영업일 장 마감 뒤 [실측 필요]",
        notes="미 재무부 수익률 곡선 — 정부 공표 자료라 공개 [확인 필요]. 작업 macro.morning(P5)",
    ),
    _planned(
        "NYFED",
        "rates",
        tier=Tier.PUBLIC,
        limiter="nyfed",
        as_of="us_trade_date",
        store="pub_macro.series",
        published="다음 영업일 08:00 ET [실측 필요]",
        notes="SOFR·EFFR 등 — 공개 [확인 필요]. 작업 macro.morning(P5)",
    ),
    _planned(
        "FF",
        "calendar",
        tier=Tier.PRIVATE,
        limiter="ff",
        as_of="run_date",
        store="prv_macro.calendar",
        published="수시",
        notes="ForexFactory 경제 일정 — 로그인(0001 prv_macro 주석). 작업 macro.morning(P5)",
    ),
    _planned(
        "ANTHROPIC",
        "guru",
        tier=Tier.PRIVATE,
        limiter="anthropic",
        as_of="run_date",
        store="prv_journal.guru_digest",
        published="작업 실행 때(LLM 요약 — 수치는 만들지 않는다, 절대 규칙 3)",
        notes="구루 브리핑 요약(ET xdigest). 작업 guru.research(P5). 표 이름 [확인 필요]",
    ),
    _planned(
        "ETF_ISSUERS",
        "pdf",
        tier=Tier.PRIVATE,
        limiter="etf_issuers",
        as_of="trade_date",
        store="prv_etf.holdings",
        published="운용사마다 다르다(대개 장 시작 전)",
        notes="ETF 구성종목(PDF) — 운용사 약관이 달라 로그인. 작업 etf.collect(P5)",
    ),
)

_ADAPTERS: Final[tuple[Sequence[DatasetSpec], ...]] = (
    # 공개(kbj/data/public)
    dart_datasets.DATASETS,
    ecos_datasets.DATASETS,
    kosis_datasets.DATASETS,
    customs_datasets.DATASETS,
    kofia_datasets.DATASETS,
    # 로그인(kbj/data/private)
    kis_datasets.DATASETS,
    krx_datasets.DATASETS,
    fsc_stock_datasets.DATASETS,
    fsc_index_datasets.DATASETS,
    ecos_restricted.DATASETS,
    # 어댑터가 아직 없는 출처
    PLANNED,
)


def _collect(groups: Iterable[Sequence[DatasetSpec]]) -> dict[str, DatasetSpec]:
    out: dict[str, DatasetSpec] = {}
    for group in groups:
        for spec in group:
            if spec.id in out:
                raise ValueError(f"카탈로그 id 가 겹친다: {spec.id}")
            out[spec.id] = spec
    return out


@functools.cache
def _catalog() -> Mapping[str, DatasetSpec]:
    return MappingProxyType(_collect(_ADAPTERS))


def all_datasets() -> dict[str, DatasetSpec]:
    """id → 명세 전부(복사본)."""
    return dict(_catalog())


def get(dataset_id: str) -> DatasetSpec:
    """id(`<source>:<dataset>`)의 명세. 없으면 `UnknownDataset`."""
    try:
        return _catalog()[dataset_id]
    except KeyError:
        raise UnknownDataset(dataset_id) from None


def claimable(spec: DatasetSpec) -> bool:
    """선점(ops.data_claim) 대상인가. 저장하지 않는 즉석 조회(`event` + store 없음)는 아니다
    (§6.5 끝).

    리미터는 선점과 관계없이 탄다.
    """
    return not (spec.as_of_kind == "event" and not spec.store)


def keys_for(spec: DatasetSpec, as_of: str, venues: Sequence[Venue] | None = None) -> list[DataKey]:
    """그 데이터셋의 데이터 키들. 거래소를 나누면 거래소마다 하나(`venues` 를 주면 그 일부만).

    거래소를 나누지 않는 데이터셋에 `venues` 를 주면 `ValueError`.
    """
    if not spec.venues:
        if venues:
            raise ValueError(f"{spec.id}: 거래소를 나누지 않는 데이터셋이다")
        return [spec.key(as_of)]
    chosen = tuple(venues) if venues else spec.venues
    return [spec.key(as_of, v) for v in chosen]

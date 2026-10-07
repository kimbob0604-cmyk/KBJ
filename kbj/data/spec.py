"""데이터셋 명세·데이터 키 — 작업 등록부·선점(claim)·카탈로그가 같은 말을 쓰게 한다(설계 §1.1·§6.5).

- **논리 데이터셋**: 같은 TR·엔드포인트라도 쓰임이 다르면 다른 id 다(`KIS:stock_quote_eod` 대
  `KIS:watch_quotes_intraday`). id 는 `<출처>:<데이터셋>`(예: `KRX:sto/stk_bydd_trd`,
  `DART:corpCode`).
- **등급은 폴더·스키마로**(DATA_TIERS §3, ADR 0002): `tier=public` ⇔ 저장 표가 `pub_*`. 로그인
  등급을 `pub_*` 에 쓰거나, 공개 등급을 `prv_*` 에 두는 명세는 만들 수 없다(검증 오류). 저장하지
  않는 즉석 조회(`KIS:quote_on_demand`)는 `store=""`.
- **데이터 키** `(source, dataset, as_of, venue)`: 성공 수집은 키마다 한 번(`ops.data_claim`).
  `venue` 는 거래소 구분(KRX·NXT·통합 — docs/metrics.md §1)이다. 거래소를 나눠 받는 데이터셋은
  명세의 `venues` 에 적고, 키를 만들 때 그중 하나를 넣는다(`DatasetSpec.key`). 나누지 않는
  데이터셋은 빈 문자열 — 그래서 `DataKey(source, dataset, as_of)` 세 값으로 만든 키도 그대로 쓴다.
- 이 모듈은 메타데이터(문자열)뿐이다. 각 어댑터의 `datasets.py` 가 `DATASETS` 를 만들고
  `kbj/data/catalog.py`(묶음 F)가 모은다.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final, Literal, NamedTuple, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ID_SOURCE = re.compile(r"[A-Z][A-Z0-9_]*")
_DATASET = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]*")
_LIMITER = re.compile(r"[a-z][a-z0-9_]*")
_STORE = re.compile(r"(?P<schema>(?:pub|prv)_[a-z][a-z0-9_]*)\.[a-z][a-z0-9_]*")


class Tier(StrEnum):
    """데이터 등급(DATA_TIERS §1). 값이 그대로 문서·검증 메시지에 나간다."""

    PUBLIC = "public"
    PRIVATE = "private"


class Venue(StrEnum):
    """거래소 구분(docs/metrics.md §1 — KRX·넥스트레이드를 나눠 저장하고 합계를 따로 둔다).

    `TOTAL` 은 출처가 통합으로 주는 값(예: KIS 시장 구분 '통합')이다. 응답이 통합인지 KRX 만인지는
    [실측 필요] — 실측 전에는 받은 그대로의 구분으로 키를 만들고 합계는 계산하지 않는다.
    """

    KRX = "KRX"
    NXT = "NXT"
    TOTAL = "TOTAL"


AsOfKind = Literal[
    "trade_date",  # 그날(KST) — 거래일이 아니면 실행 안 함
    "prev_trading_day",  # 전 거래일 — 예: 2026-10-06(화) → 10-02(금, 10-05 대체공휴일)
    "us_trade_date",  # 끝난 미국 세션 날짜(뉴욕 날짜)
    "run_date",  # 실행 날짜(KST)
    "minute",  # 분 시작(KST)
    "slot10m",  # 10분 구간 시작(KST)
    "ten_day",  # YYYYMM-1·-2·-3(1~10·~20·말일)
    "month",  # 공표 기준 월
    "quarter",  # 공표 기준 분기
    "event",  # 이벤트 id(DART 접수번호 등)
]
AS_OF_KINDS: Final[tuple[str, ...]] = get_args(AsOfKind)


class DataKey(NamedTuple):
    """선점·중복 판정의 단위. `as_of` 는 `AsOfKind` 규칙으로 만든 문자열(예: `2026-10-02`).

    `venue` 는 거래소를 나눠 받는 데이터셋만 채운다(`Venue` 값). 나머지는 빈 문자열.
    """

    source: str
    dataset: str
    as_of: str
    venue: str = ""

    @property
    def dataset_id(self) -> str:
        return f"{self.source}:{self.dataset}"

    def label(self) -> str:
        """사람이 읽는 한 줄(로그·오류 문구) — `KRX:sto/stk_bydd_trd@2026-10-02[NXT]`."""
        tail = f"[{self.venue}]" if self.venue else ""
        return f"{self.dataset_id}@{self.as_of}{tail}"


class DatasetSpec(BaseModel):
    """논리 데이터셋 하나. 같은 `(source, dataset)` 은 등록부 전체에서 한 작업만 받는다(§6.5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str  # "<source>:<dataset>"
    source: str  # 대문자 출처 이름: KIS·KRX·DART·DATAGO·ECOS·KOSIS·NASDAQ·YAHOO …
    dataset: str  # 출처 안 이름: TR 대신 논리 이름(KIS), 엔드포인트(KRX `sto/stk_bydd_trd`) 등
    tier: Tier
    limiter: str  # config/limits.yaml 의 출처 이름(소문자) — 이 데이터셋 호출이 타는 리미터
    budget: str | None  # 일 예산 이름(limits.yaml 출처) — 없으면 None
    published: str  # 공표 시각·주기 설명(예: "D+1 08:00 KST") — 사람이 읽는 값
    as_of_kind: AsOfKind
    store: str  # 저장 표 `스키마.표`(pub_*·prv_*), 저장하지 않으면 ""
    notes: str = ""  # [실측 필요]·[확인 필요]·TR 번호·필드 이름 등
    venues: tuple[Venue, ...] = Field(default=())  # 거래소를 나눠 받으면 그 구분들

    @model_validator(mode="after")
    def _check(self) -> DatasetSpec:
        if not _ID_SOURCE.fullmatch(self.source):
            raise ValueError(f"source 는 대문자 식별자여야 한다: {self.source!r}")
        if not _DATASET.fullmatch(self.dataset):
            raise ValueError(f"dataset 형식이 틀렸다: {self.dataset!r}")
        if self.id != f"{self.source}:{self.dataset}":
            raise ValueError(f"id 는 '<source>:<dataset>' 이어야 한다: {self.id!r}")
        for name, value in (("limiter", self.limiter), ("budget", self.budget)):
            if value is not None and not _LIMITER.fullmatch(value):
                raise ValueError(f"{name} 는 limits.yaml 출처 이름(소문자)이어야 한다: {value!r}")
        if self.store:
            m = _STORE.fullmatch(self.store)
            if m is None:
                raise ValueError(f"store 는 '<pub_|prv_스키마>.<표>' 이어야 한다: {self.store!r}")
            public_store = m["schema"].startswith("pub_")
            if public_store != (self.tier is Tier.PUBLIC):
                raise ValueError(
                    f"{self.id}: 등급 {self.tier.value} 인데 저장 표가 {self.store} — "
                    "공개 등급은 pub_*, 로그인 등급은 prv_* 에만 쓴다(ADR 0002)"
                )
        if len(set(self.venues)) != len(self.venues):
            raise ValueError(f"{self.id}: venues 가 겹친다")
        if not self.published.strip():
            raise ValueError(f"{self.id}: published(공표 시각 설명)가 비었다")
        return self

    def key(self, as_of: str, venue: Venue | None = None) -> DataKey:
        """이 데이터셋의 데이터 키. 거래소를 나누는 데이터셋은 `venue` 가 필수이고 `venues` 안이어야
        하며, 나누지 않는 데이터셋에 `venue` 를 주면 오류다."""
        if not as_of or any(c.isspace() for c in as_of):
            raise ValueError(f"as_of 형식이 틀렸다: {as_of!r}")
        if self.venues:
            if venue is None or venue not in self.venues:
                allowed = ",".join(v.value for v in self.venues)
                raise ValueError(f"{self.id}: venue 는 {allowed} 중 하나여야 한다: {venue!r}")
            return DataKey(self.source, self.dataset, as_of, venue.value)
        if venue is not None:
            raise ValueError(f"{self.id}: 거래소를 나누지 않는 데이터셋이다(venue={venue.value})")
        return DataKey(self.source, self.dataset, as_of)

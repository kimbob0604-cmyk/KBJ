"""DART 논리 데이터셋(공개) — 작업 등록부·선점·카탈로그용 메타데이터(설계 §2·§6.7).

리미터 `dart`(8/s)·일 예산 `dart`(18,000 — limits.yaml). 저장 표는 `pub_filings.*`·`pub_fin.*`
(0004 `corp_code` 는 P2, 나머지는 0008·0009 — P4). P2 에 실제로 도는 작업은 `filings.corp_code` 뿐.
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import AsOfKind, DatasetSpec, Tier

SOURCE: Final = "DART"
LIMITER: Final = "dart"


def _dart(dataset: str, *, as_of: AsOfKind, store: str, published: str, notes: str) -> DatasetSpec:
    return DatasetSpec(
        id=f"{SOURCE}:{dataset}",
        source=SOURCE,
        dataset=dataset,
        tier=Tier.PUBLIC,
        limiter=LIMITER,
        budget=LIMITER,
        published=published,
        as_of_kind=as_of,
        store=store,
        notes=notes,
    )


DATASETS: Final[tuple[DatasetSpec, ...]] = (
    _dart(
        "corpCode",
        as_of="run_date",
        store="pub_filings.corp_code",
        published="수시 갱신(전체 파일) — 매일 03:05 KST 에 갈아 넣는다",
        notes=(
            "corpCode.xml zip(수 MB). 상장사 stock_code 6자, 비상장은 공백. "
            "작업 filings.corp_code(P2)"
        ),
    ),
    _dart(
        "list",
        as_of="minute",
        store="pub_filings.disclosure",
        published="접수 즉시(장중 수시) — 접수 시각은 응답에 없다(rcept_dt 날짜뿐)",
        notes="list.json 공시검색. 작업 filings.dart_feed(매분 07:00~19:59, P4)",
    ),
    _dart(
        "document",
        as_of="event",
        store="pub_filings.overhang",
        published="공시 접수 뒤(원문 zip)",
        notes=(
            "document.xml 공시원문 — 오버행·잠정실적 파싱 입력. 원문 자체는 저장하지 않는다 "
            "[제안]. 저장 표는 P4 에서 earnings_actual 과 나눌지 확정 [확인 필요]"
        ),
    ),
    _dart(
        "company",
        as_of="run_date",
        store="pub_filings.corp_code",
        published="수시",
        notes="company.json 기업개황 — 표준산업분류(induty_code). corp_code 표에 덧붙인다 [제안]",
    ),
    _dart(
        "fnlttSinglAcntAll",
        as_of="quarter",
        store="pub_fin.quarterly",
        published="정기보고서 접수 뒤(분기·반기 45일, 사업 90일 안)",
        notes="단일회사 전체 재무제표(CFS·OFS). 보고서 코드 11013·11012·11014·11011",
    ),
    _dart(
        "fnlttMultiAcnt",
        as_of="quarter",
        store="pub_fin.quarterly",
        published="정기보고서 접수 뒤",
        notes="다중회사 주요계정(ET board/ingest/financials.py fetch_batch) — 100개씩",
    ),
)

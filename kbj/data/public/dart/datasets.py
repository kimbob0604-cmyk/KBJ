"""DART 논리 데이터셋(공개) — 작업 등록부·선점·카탈로그용 메타데이터(설계 §2·§6.7, P4 §3.3).

리미터 `dart`(8/s)·일 예산 `dart`(18,000 — limits.yaml). 저장 표는 `pub_filings.*`(0004 `corp_code`
는
P2, 나머지는 0010 — P4)·`pub_fin.*`(0011 — P4). 카탈로그 `store` 는 표 하나다(등록부 검증 — 그 표가
작업
`writes` 에 있어야 한다). 한 데이터셋이 여러 표에 쓰면 나머지는 작업 `writes` 에만 적는다.

P4(docs/p4_design.md §3.3·D-P4-25): 접수번호·회사 단위로 수시로 받는 것은 `event`(처리기가 선점
저장소
`claims` 자원으로 직접 `claim → complete/fail` 한다 — 실행기는 event 키를 미리 잡지 않는다). 구조화
엔드포인트 이름·파라미터·필드는 **[확인 필요 #31~#33]**(키를 받은 뒤 실측 — 필드 이름은 파서 모듈의
상수
하나에 모은다).
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
        notes=(
            "list.json 공시검색 — 증분(1쪽부터, 이미 본 접수번호가 나오면 멈춤 — §4.1). 정렬 "
            "기본값 [확인 필요 #28]. 작업 filings.dart_feed(매분 07:00~19:59, P4)"
        ),
    ),
    _dart(
        "document",
        as_of="event",
        store="pub_filings.earnings_prelim",
        published="공시 접수 뒤(원문 zip)",
        notes=(
            "document.xml 공시원문 — 잠정실적·전환청구·전환가 조정·결산실적공시예고 원문 파싱, "
            "구조화 응답이 빈 칸일 때 오버행 폴백. 원문 자체는 저장하지 않는다(D-P4-3). 데이터 키 "
            "= 접수번호. 그 밖에 쓰는 표(overhang_adjust 등)는 작업 writes 에만"
        ),
    ),
    # 주요사항보고서 구조화(DS005) — corp_code + 기간으로 묻고 접수번호로 고른다 [확인 필요 #31]
    _dart(
        "cvbdIsDecsn",
        as_of="event",
        store="pub_filings.overhang_event",
        published="주요사항보고서(전환사채권 발행결정) 접수 뒤",
        notes="전환사채(CB) — 권면총액·전환가액·전환청구기간·리픽싱 하한. 데이터 키 = 접수번호",
    ),
    _dart(
        "bdwtIsDecsn",
        as_of="event",
        store="pub_filings.overhang_event",
        published="주요사항보고서(신주인수권부사채권 발행결정) 접수 뒤",
        notes=(
            "신주인수권부사채(BW) — 행사가액·권리행사기간(분리형도 같은 식). 데이터 키 = 접수번호"
        ),
    ),
    _dart(
        "exbdIsDecsn",
        as_of="event",
        store="pub_filings.overhang_event",
        published="주요사항보고서(교환사채권 발행결정) 접수 뒤",
        notes="교환사채(EB) — 교환가액·교환 대상 주식(신주 아님). 데이터 키 = 접수번호",
    ),
    _dart(
        "piicDecsn",
        as_of="event",
        store="pub_filings.overhang_event",
        published="주요사항보고서(유상증자결정) 접수 뒤",
        notes=(
            "유상증자 — 신주 수·배정 방식(제3자배정 → 추정 보호예수 D-P4-18). 데이터 키 = 접수번호"
        ),
    ),
    _dart(
        "fricDecsn",
        as_of="event",
        store="pub_filings.overhang_event",
        published="주요사항보고서(무상증자결정) 접수 뒤",
        notes="무상증자 — 신주 수·신주 상장 예정일. 데이터 키 = 접수번호",
    ),
    _dart(
        "tsstkAqDecsn",
        as_of="event",
        store="pub_filings.buyback_event",
        published="주요사항보고서(자기주식 취득 결정) 접수 뒤",
        notes="자기주식 취득 — 예정 주식 수·금액·기간·방법. 데이터 키 = 접수번호",
    ),
    _dart(
        "tsstkDpDecsn",
        as_of="event",
        store="pub_filings.buyback_event",
        published="주요사항보고서(자기주식 처분 결정) 접수 뒤",
        notes="자기주식 처분. 데이터 키 = 접수번호",
    ),
    # 지분공시 — 응답은 회사의 보고 전체가 온다(새 접수번호만 저장) [확인 필요 #32]
    _dart(
        "elestock",
        as_of="event",
        store="pub_filings.insider_report",
        published="임원·주요주주 특정증권등 소유상황보고서 접수 뒤",
        notes=(
            "elestock.json — 보고자·증감·소유비율(변동 사유 칸 유무 [확인 필요]). 데이터 키 = "
            "접수번호"
        ),
    ),
    _dart(
        "majorstock",
        as_of="event",
        store="pub_filings.major_holder_report",
        published="주식등의 대량보유상황보고서 접수 뒤",
        notes="majorstock.json — 5% 대량보유(목록만 — 창 합계는 내지 않는다). 데이터 키 = 접수번호",
    ),
    _dart(
        "stockTotqySttus",
        as_of="event",
        store="pub_filings.share_count",
        published="정기보고서 접수 뒤",
        notes=(
            "정기보고서 주요정보 — 주식의 총수 현황(보통·우선 발행·자기주식) [확인 필요 #33]. "
            "오버행·내부자 비율·BPS 의 공개 분모. 데이터 키 = <corp_code>:<접수번호>. 작업 "
            "fin.quarterly"
        ),
    ),
    _dart(
        "hyslrSttus",
        as_of="event",
        store="pub_filings.largest_holder",
        published="정기보고서 접수 뒤",
        notes=(
            "정기보고서 주요정보 — 최대주주 현황(최대주주 + 특수관계인 기말 보유, 종류별 '계' 행 — "
            "실측 2026-10-21). 지수 유동비율 추정의 전략적 보유(metrics §24.2). 데이터 키 = "
            "<corp_code>:<접수번호>. 작업 fin.quarterly"
        ),
    ),
    _dart(
        "company",
        as_of="run_date",
        store="pub_filings.corp_profile",
        published="수시",
        notes=(
            "company.json 기업개황 — 결산월(acc_mt)·표준산업분류(induty_code)·법인구분·설립일. "
            "corp_code 표는 filings.corp_code 가 매일 갈아 넣으므로 따로 둔다(두 작업이 한 표를 "
            "쓰지 않게). 작업 filings.company_profile(주 1회)"
        ),
    ),
    _dart(
        "fnlttSinglAcntAll",
        as_of="event",
        store="pub_fin.statement_raw",
        published="정기보고서 접수 뒤(분기·반기 45일, 사업 90일 안)",
        notes=(
            "단일회사 전체 재무제표(CFS·OFS) — 상세 층(관심종목 ∪ 컨센서스 유니버스 ∪ 공개 "
            "데모)만. 보고서 코드 11013·11012·11014·11011. 데이터 키 = "
            "<corp>:<연도>:<보고서>:<CFS|OFS>. 원시 행 → 정규화(pub_fin.quarterly·ratio 는 작업 "
            "writes 에만). 작업 fin.quarterly·fin.backfill"
        ),
    ),
    _dart(
        "fnlttMultiAcnt",
        as_of="event",
        store="pub_fin.quarterly",
        published="정기보고서 접수 뒤",
        notes=(
            "다중회사 주요계정(ET board/ingest/financials.py fetch_batch) — 100개 묶음, 전 상장사 "
            "주요계정 층. 데이터 키 = <실행일>:<연도>:<보고서>:<묶음 n>(run_date 로 두면 07:30 뒤 "
            "발화가 전부 duplicate 로 멈춘다 — D-P4-25)"
        ),
    ),
    _dart(
        "report_inputs",
        as_of="event",
        store="pub_fin.report",
        published="작업 reports.dart_excel 실행 때(매일 21:00)",
        notes=(
            "논리 데이터셋 — dart-report 파이프라인 한 번이 부르는 "
            "list.json·fnlttSinglAcntAll·document.xml 묶음(같은 (source, dataset) 은 한 작업만 — "
            "DART:list·document 는 dart_feed 몫). statement_raw 가 있으면 그것을 읽어 호출을 "
            "줄인다. 데이터 키 = <종목>:<실행일>"
        ),
    ),
)

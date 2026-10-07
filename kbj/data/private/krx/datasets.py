"""KRX 논리 데이터셋 — 작업 등록부·선점(claim)·카탈로그가 쓰는 메타데이터(설계 §2·§6.7,
docs/metrics.md §1·§4).

등급은 모두 **로그인**(DATA_TIERS §1 — KRX OpenAPI 원천 데이터 재배포 금지)이라 저장 표는 `prv_*`
뿐이다. 데이터셋 이름은 엔드포인트 경로(`sto/stk_bydd_trd`)다. 수집 작업은 P3 이후:
`krx.daily`(08:05, 10분마다 10:00 까지 — 주식·지수·ETP @ prev_trading_day), `market.backfill`(수동 —
백필 예산), `gex.krx_derivatives`(legacy GX — P7, `runner: external`).

- 리미터·일 예산은 모두 `krx`(config/limits.yaml — 2/s [추정], 8,000/일 [확인 필요 — 메인 결정
  D5]). 데이터셋 하나 = 하루 한 번 호출(재시도 포함 몇 번)이라 하루 12개 × 재시도 ≈ 수십~백여 건.
- 공표: 파생은 다음 영업일 08:00 갱신(GX #16 실측). 주식·지수·ETP 공표 시각은 [실측 필요] —
  같다고 보고 `prev_trading_day` 로 받는다.
- **거래소 구분**(docs/metrics.md §1): 거래대금·ETF 수급 원장은 KRX·넥스트레이드(NXT)를 나눠
  저장한다. KRX OpenAPI 일별은 KRX 시장 체결분으로 보고 `venues=(KRX,)` — 데이터 키에 `KRX` 가
  들어간다(`DatasetSpec.key(as_of, Venue.KRX)`). NXT 체결분이 섞여 오면(통합) `TOTAL` 로 바꾼다
  [실측 필요 — probe_results §7 #14]. 지수·종목기본정보·파생은 거래소를 나누지 않는다.
- 필드 이름은 `models.py`(행 모델) 기준이며 [실측 필요].
- 이 모듈은 문자열 메타데이터뿐이다. 카탈로그(`kbj/data/catalog.py`, 묶음 F)가 모은다.
"""

from __future__ import annotations

from typing import Final

from kbj.data.private.krx.client import (
    ETF_DAILY,
    ETN_DAILY,
    FUT_DAILY,
    IDX_KOSDAQ_DAILY,
    IDX_KOSPI_DAILY,
    IDX_KRX_DAILY,
    KNX_DAILY,
    KSQ_BASE_INFO,
    KSQ_DAILY,
    OPT_DAILY,
    STK_BASE_INFO,
    STK_DAILY,
    dataset_of,
)
from kbj.data.spec import AsOfKind, DatasetSpec, Tier, Venue

SOURCE: Final = "KRX"
LIMITER: Final = "krx"
BUDGET: Final = "krx"
_KRX_ONLY: Final = (Venue.KRX,)
_PUBLISHED: Final = (
    "다음 영업일 08:00 KST 갱신(파생은 GX #16 실측, "
    "주식·지수·ETP 는 같은 시각으로 가정 [실측 필요])"
)
_STOCK_FIELDS: Final = (
    "필드: BAS_DD·ISU_CD(단축코드)·ISU_NM·MKT_NM·SECT_TP_NM(소속부)·TDD_OPNPRC·TDD_HGPRC·"
    "TDD_LWPRC·TDD_CLSPRC·CMPPREVDD_PRC·FLUC_RT·ACC_TRDVOL·ACC_TRDVAL(거래대금, 원)·MKTCAP(원)·"
    "LIST_SHRS [실측 필요]. 행 모델 models.KrxStockDaily, 저장 모양 stocks.KrxStockRow. "
    "거래대금 원장(metrics §1 — 시장 거래대금·회전율·급증 배수). 정규장·시간외 구분 여부와 "
    "NXT 체결분 포함 여부 [실측 필요]. 비수정 가격(adjusted=false)"
)


def _krx(
    endpoint: str,
    *,
    store: str,
    notes: str,
    venues: tuple[Venue, ...] = (),
    as_of: AsOfKind = "prev_trading_day",
    published: str = _PUBLISHED,
) -> DatasetSpec:
    dataset = dataset_of(endpoint)
    return DatasetSpec(
        id=f"{SOURCE}:{dataset}",
        source=SOURCE,
        dataset=dataset,
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=BUDGET,
        published=published,
        as_of_kind=as_of,
        store=store,
        notes=notes,
        venues=venues,
    )


DATASETS: Final[tuple[DatasetSpec, ...]] = (
    # ── 주식 일별(거래대금 원장 — D7) ────────────────────────────────────────────────────────
    _krx(
        STK_DAILY,
        store="prv_market.daily_bar",
        notes=f"유가증권(KOSPI) 전 종목 일별매매정보. {_STOCK_FIELDS}",
        venues=_KRX_ONLY,
    ),
    _krx(
        KSQ_DAILY,
        store="prv_market.daily_bar",
        notes=f"코스닥 전 종목 일별매매정보. {_STOCK_FIELDS}",
        venues=_KRX_ONLY,
    ),
    _krx(
        KNX_DAILY,
        store="prv_market.daily_bar",
        notes=(
            "코넥스 전 종목 일별매매정보(시장 거래대금에는 넣지 않는다 [확인 필요]). "
            f"{_STOCK_FIELDS}"
        ),
        venues=_KRX_ONLY,
    ),
    # ── 종목기본정보(유니버스) ───────────────────────────────────────────────────────────────
    _krx(
        STK_BASE_INFO,
        store="prv_market.universe",
        notes=(
            "유가증권 종목기본정보 — ISU_CD(표준코드)·ISU_SRT_CD·ISU_NM·ISU_ABBRV·LIST_DD·"
            "MKT_TP_NM·SECUGRP_NM·SECT_TP_NM·KIND_STKCERT_TP_NM(보통주·우선주)·PARVAL·"
            "LIST_SHRS [실측 필요]. "
            "SD universe_sync_monthly·kr_universe_daily 대체"
        ),
    ),
    _krx(
        KSQ_BASE_INFO,
        store="prv_market.universe",
        notes="코스닥 종목기본정보 — 필드는 유가증권과 같다 [실측 필요]",
    ),
    # ── 지수 일별 ───────────────────────────────────────────────────────────────────────────
    _krx(
        IDX_KOSPI_DAILY,
        store="prv_market.daily_bar",
        notes=(
            "KOSPI 시리즈 지수 일별(업종지수 포함, daily_bar asset=index) — IDX_CLSS·IDX_NM·"
            "CLSPRC_IDX·CMPPREVDD_IDX·FLUC_RT·OPNPRC_IDX·HGPRC_IDX·LWPRC_IDX·ACC_TRDVOL·ACC_TRDVAL·"
            "MKTCAP [실측 필요]. 행 모델 models.KrxIndexDaily"
        ),
    ),
    _krx(
        IDX_KOSDAQ_DAILY,
        store="prv_market.daily_bar",
        notes="KOSDAQ 시리즈 지수 일별(업종지수 포함) — 필드는 KOSPI 시리즈와 같다 [실측 필요]",
    ),
    _krx(
        IDX_KRX_DAILY,
        store="prv_market.daily_bar",
        notes="KRX 시리즈 지수 일별(KRX300 등) — 필드는 KOSPI 시리즈와 같다 [실측 필요]",
    ),
    # ── ETP 일별(ETF 수급 원장 — D7) ────────────────────────────────────────────────────────
    _krx(
        ETF_DAILY,
        store="prv_etf.etf_daily",
        notes=(
            "ETF 일별매매정보 — NAV(장 마감 확정치)·LIST_SHRS(상장좌수)·INVSTASST_NETASST_TOTAMT"
            "(순자산총액, 원)·ACC_TRDVAL(거래대금, 원)·TDD_CLSPRC·MKTCAP·IDX_IND_NM(기초지수)·"
            "OBJ_STKPRC_IDX [실측 필요: 필드 이름·공표 시각]. 행 모델 models.KrxEtfDaily. "
            "순유입 = Σ(좌수ₜ − 좌수ₜ₋₁) × NAVₜ, 가격효과, 검산 ③(metrics §4). 저장 표는 P5 "
            "0013(prv_etf) 에서 확정 [확인 필요]. ETF 의 NXT 거래 여부 [실측 필요]"
        ),
        venues=_KRX_ONLY,
    ),
    _krx(
        ETN_DAILY,
        store="prv_market.daily_bar",
        notes=(
            "ETN 일별매매정보(daily_bar asset=etn) — PER1SECU_INDIC_VAL(지표가치)·INDIC_VAL_AMT·"
            "ACC_TRDVAL·LIST_SHRS [실측 필요]. 시장 거래대금에는 넣지 않는다(metrics §1)"
        ),
        venues=_KRX_ONLY,
    ),
    # ── 파생 일별(legacy GX — P7 까지 runner: external) ───────────────────────────────────────
    _krx(
        FUT_DAILY,
        store="prv_gex.krx_fut_daily",
        notes=(
            "선물 일별매매정보(GX 실측 #15) — 세션은 MKT_NM(정규·야간). 행 모델 "
            "models.KrxFuturesDaily. gex.krx_derivatives(legacy GX KrxDaily)"
        ),
        published="다음 영업일 08:00 KST 갱신(GX #16 실측)",
    ),
    _krx(
        OPT_DAILY,
        store="prv_gex.krx_opt_daily",
        notes=(
            "옵션 일별매매정보(GX 실측 #15, 하루 약 16,700행) — 세션·만기·행사가는 ISU_NM 에서. "
            "행 모델 models.KrxOptionDaily. gex.krx_derivatives(legacy GX KrxDaily)"
        ),
        published="다음 영업일 08:00 KST 갱신(GX #16 실측)",
    ),
)

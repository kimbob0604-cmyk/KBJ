"""KIS REST 응답 pydantic 모델 (docs/phase1_design.md §2·§8, docs/probe_results.md #11~#17).

KIS 는 모든 값을 문자열로 준다. 여기서 한 번만 타입을 입힌다.

- 가격·행사가·그릭스·IV → `Decimal`, 수량·대금 → `int`, 날짜(YYYYMMDD) → `date`
- 빈 문자열(공백만 있는 값 포함) → `None`. 앞뒤 공백은 벗긴다
- 모르는 필드는 버린다(`extra="ignore"`). 필드 이름은 KIS 원문 그대로 둔다(raw 녹화와 대조용)
- 분봉 시각(야간 24~30시 표기)의 변환은 `core/calendar` 몫이라 여기선 원문 문자열을 그대로 둔다

대금 단위(실측 행으로 역산, 2026-09-28): 전광판·단건·분봉의 `acml_tr_pbmn` 은 천원
(옵션 2계약 × 약 63pt × 250,000 = 약 3,150만 원 → `31463`), 투자자별 `*_tr_pbmn` 은 백만원
(선물 외국인 매도 56,937계약 × 약 1,100pt × 250,000 = 약 15.7조 → `15706184`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator


def blank_to_none(v: object) -> object:
    """문자열이면 앞뒤 공백을 벗기고, 비었으면 None."""
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return v


def parse_yyyymmdd(v: object) -> object:
    """'20261008' → date(2026, 10, 8). 빈 값은 None.

    pydantic 기본 파서는 숫자 문자열을 Unix 시각으로 읽어서 직접 푼다.
    """
    v = blank_to_none(v)
    if isinstance(v, str):
        if not re.fullmatch(r"\d{8}", v):
            raise ValueError(f"YYYYMMDD 형식이 아니다: {v!r}")
        return date(int(v[:4]), int(v[4:6]), int(v[6:]))
    return v


Dec = Annotated[Decimal | None, BeforeValidator(blank_to_none)]
Int = Annotated[int | None, BeforeValidator(blank_to_none)]
Str = Annotated[str | None, BeforeValidator(blank_to_none)]
Ymd = Annotated[date | None, BeforeValidator(parse_yyyymmdd)]
ReqDec = Annotated[Decimal, BeforeValidator(blank_to_none)]
ReqStr = Annotated[str, BeforeValidator(blank_to_none)]


class KisModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


def _positive(v: Decimal | None) -> Decimal | None:
    return v if v is not None and v > 0 else None


# ── 옵션 전광판 (FHPIF05030100) ───────────────────────────────────────────────


class CallPutRow(KisModel):
    """옵션 전광판 콜·풋 한 행 (output1 콜 · output2 풋, 41필드 — #11).

    행사가 내림차순, 콜·풋 각 100행 상한. `atm_cls_name` 은 선물가 기준 ATM 과 달라
    쓰지 않는다(#11a).
    """

    optn_shrn_iscd: ReqStr  # 단축코드
    acpr: ReqDec  # 행사가
    unch_prpr: Dec = None
    optn_prpr: Dec = None
    optn_prdy_vrss: Dec = None
    prdy_vrss_sign: Str = None
    optn_prdy_ctrt: Dec = None
    optn_bidp: Dec = None
    optn_askp: Dec = None
    tmvl_val: Dec = None
    nmix_sdpr: Dec = None
    acml_vol: Int = None
    seln_rsqn: Int = None
    shnu_rsqn: Int = None
    acml_tr_pbmn: Int = None  # 천원
    hts_otst_stpl_qty: Int = None  # 미결제약정
    otst_stpl_qty_icdc: Int = None  # 미결제약정 증감
    delta_val: Dec = None
    gama: Dec = None
    vega: Dec = None
    theta: Dec = None
    rho: Dec = None
    hts_ints_vltl: Dec = None  # KIS IV (%)
    invl_val: Dec = None
    esdg: Dec = None
    dprt: Dec = None
    hist_vltl: Dec = None
    hts_thpr: Dec = None
    optn_oprc: Dec = None
    optn_hgpr: Dec = None
    optn_lwpr: Dec = None
    optn_mxpr: Dec = None
    optn_llam: Dec = None
    atm_cls_name: Str = None
    rgbf_vrss_icdc: Int = None
    total_askp_rsqn: Int = None
    total_bidp_rsqn: Int = None
    futs_antc_cnpr: Dec = None
    futs_antc_cntg_vrss: Dec = None
    antc_cntg_vrss_sign: Str = None
    antc_cntg_prdy_ctrt: Dec = None

    @property
    def code(self) -> str:
        return self.optn_shrn_iscd

    @property
    def strike(self) -> Decimal:
        return self.acpr

    @property
    def oi(self) -> int | None:
        return self.hts_otst_stpl_qty

    @property
    def iv_kis(self) -> Decimal | None:
        """KIS IV. 못 구한 행(거래 없는 깊은 ITM 등)엔 `0.0000` 이 와서 None 으로 돌린다(실측)."""
        return _positive(self.hts_ints_vltl)


class CallPutBoard(KisModel):
    """옵션 전광판 응답 본문. `calls` = output1, `puts` = output2."""

    rt_cd: Str = None
    msg_cd: Str = None
    msg1: Str = None
    calls: tuple[CallPutRow, ...] = Field(validation_alias="output1")
    puts: tuple[CallPutRow, ...] = Field(validation_alias="output2")


# ── 옵션 월물리스트 ───────────────────────────────────────────────────────────


class OptionListRow(KisModel):
    """월물리스트 한 행 (#12). 전광판 `FID_MTRT_CNT` 에는 6자리 `mtrt_yymm` 을 쓴다.

    월물은 YYYYMM(`202610`), 위클리는 YYMMWW(`260904`). 4자리 `mtrt_yymm_code` 로는 0행이 온다.
    """

    mtrt_yymm: Annotated[str, BeforeValidator(blank_to_none), Field(pattern=r"^\d{6}$")]
    mtrt_yymm_code: Annotated[str, BeforeValidator(blank_to_none), Field(pattern=r"^\d{4}$")]


# ── 선물 전광판 (FHPIF05030200) ───────────────────────────────────────────────


class FuturesBoardRow(KisModel):
    """선물 전광판 한 행 (output, 20필드)."""

    futs_shrn_iscd: ReqStr
    hts_kor_isnm: Str = None
    futs_prpr: Dec = None
    futs_prdy_vrss: Dec = None
    prdy_vrss_sign: Str = None
    futs_prdy_ctrt: Dec = None
    hts_thpr: Dec = None
    acml_vol: Int = None
    futs_askp: Dec = None
    futs_bidp: Dec = None
    hts_otst_stpl_qty: Int = None
    futs_hgpr: Dec = None
    futs_lwpr: Dec = None
    hts_rmnn_dynu: Int = None  # 잔존일수
    total_askp_rsqn: Int = None
    total_bidp_rsqn: Int = None
    futs_antc_cnpr: Dec = None
    futs_antc_cntg_vrss: Dec = None
    antc_cntg_vrss_sign: Str = None
    antc_cntg_prdy_ctrt: Dec = None


class FuturesBoard(KisModel):
    rt_cd: Str = None
    msg_cd: Str = None
    msg1: Str = None
    rows: tuple[FuturesBoardRow, ...] = Field(validation_alias="output")


# ── 선물옵션 단건 현재가 (FHMIF10000000) ──────────────────────────────────────


class PriceOutput(KisModel):
    """단건 현재가 output1 (30필드 — #11a).

    옵션이면 `acpr`(행사가)·그릭스·IV 가 채워진다. 호가는 없다.
    """

    hts_kor_isnm: Str = None
    futs_prpr: Dec = None  # 현재가 (옵션도 이 이름)
    futs_prdy_vrss: Dec = None
    prdy_vrss_sign: Str = None
    futs_prdy_clpr: Dec = None
    futs_prdy_ctrt: Dec = None
    acml_vol: Int = None
    acml_tr_pbmn: Int = None  # 천원
    hts_otst_stpl_qty: Int = None
    otst_stpl_qty_icdc: Int = None
    futs_oprc: Dec = None
    futs_hgpr: Dec = None
    futs_lwpr: Dec = None
    futs_mxpr: Dec = None
    futs_llam: Dec = None
    futs_sdpr: Dec = None
    hts_thpr: Dec = None
    dprt: Dec = None
    futs_last_tr_date: Ymd = None  # 최종거래일
    hts_rmnn_dynu: Int = None  # 잔존일수
    futs_lstn_medm_hgpr: Dec = None
    futs_lstn_medm_lwpr: Dec = None
    delta_val: Dec = None
    gama: Dec = None
    theta: Dec = None
    vega: Dec = None
    rho: Dec = None
    hist_vltl: Dec = None
    hts_ints_vltl: Dec = None
    acpr: Dec = None  # 행사가 (선물은 비거나 0)

    @property
    def strike(self) -> Decimal | None:
        return _positive(self.acpr)

    @property
    def price(self) -> Decimal | None:
        return self.futs_prpr

    @property
    def oi(self) -> int | None:
        return self.hts_otst_stpl_qty

    @property
    def iv_kis(self) -> Decimal | None:
        return _positive(self.hts_ints_vltl)


# ── 투자자별 매매동향 (FHPTJ04030000) ─────────────────────────────────────────

Investor = Literal[
    "frgn",  # 외국인
    "prsn",  # 개인
    "orgn",  # 기관계
    "scrt",  # 증권 (딜러 프록시)
    "ivtr",  # 투신
    "pe_fund",  # 사모
    "bank",  # 은행
    "insu",  # 보험
    "mrbn",  # 종금
    "fund",  # 기금
    "etc_orgt",  # 기타단체
    "etc_corp",  # 기타법인
]
INVESTORS: tuple[Investor, ...] = (
    "frgn",
    "prsn",
    "orgn",
    "scrt",
    "ivtr",
    "pe_fund",
    "bank",
    "insu",
    "mrbn",
    "fund",
    "etc_orgt",
    "etc_corp",
)
# 측정값 → KIS 접미사 후보. 순매수 수량은 투자자마다 이름이 다르다(실측 2026-09-28:
# pe_fund·etc_orgt·etc_corp 는 `_ntby_vol`, 나머지 9종은 `_ntby_qty`)
_MEASURES: dict[str, tuple[str, ...]] = {
    "sell_qty": ("seln_vol",),
    "buy_qty": ("shnu_vol",),
    "net_qty": ("ntby_qty", "ntby_vol"),
    "sell_value": ("seln_tr_pbmn",),
    "buy_value": ("shnu_tr_pbmn",),
    "net_value": ("ntby_tr_pbmn",),
}


class InvestorFlow(KisModel):
    """투자자 한 종의 매도·매수·순매수 (수량 계약, 대금 백만원)."""

    investor: Investor
    sell_qty: Int = None
    buy_qty: Int = None
    net_qty: Int = None
    sell_value: Int = None
    buy_value: Int = None
    net_value: Int = None


class InvestorRow(KisModel):
    """투자자별 매매동향 한 행: 투자자 12종 × 매도·매수·순매수 × 수량·대금 = 72필드 (#13)."""

    flows: tuple[InvestorFlow, ...]

    @model_validator(mode="before")
    @classmethod
    def _gather(cls, data: Any) -> Any:
        if not isinstance(data, Mapping) or "flows" in data:
            return data
        raw: Mapping[str, Any] = data
        flows: list[dict[str, Any]] = []
        for inv in INVESTORS:
            f: dict[str, Any] = {"investor": inv}
            for measure, suffixes in _MEASURES.items():
                key = next((f"{inv}_{s}" for s in suffixes if f"{inv}_{s}" in raw), None)
                if key is None:
                    raise ValueError(f"투자자 필드가 없다: {inv}_{suffixes[0]}")
                f[measure] = raw[key]
            flows.append(f)
        return {"flows": flows}

    def to_long(self) -> list[InvestorFlow]:
        """긴 형식 (`investor_flow` 테이블 행 12개)."""
        return list(self.flows)

    def flow(self, investor: Investor) -> InvestorFlow:
        return next(f for f in self.flows if f.investor == investor)


# ── 선물옵션 분봉 (inquire-time-fuopchartprice) ───────────────────────────────


def _hhmmss_upto_30(v: object) -> object:
    v = blank_to_none(v)
    if isinstance(v, str):
        if not re.fullmatch(r"\d{6}", v):
            raise ValueError(f"HHMMSS 형식이 아니다: {v!r}")
        mm, ss = int(v[2:4]), int(v[4:])
        # 야간(시장 CM) 봉은 야간 시작일 날짜 + 24~30시 확장 표기 (#17). 야간은 06:00 에 끝나
        # 마지막 봉이 300000 — 그 뒤(300001~)는 세션 밖
        if int(v) > 300000 or mm > 59 or ss > 59:
            raise ValueError(f"시각 범위 밖: {v!r}")
    return v


class MinuteBar(KisModel):
    """분봉 한 개 (output2). 시각은 원문 그대로 — 야간 봉 `(20260922, 300000)` = 09-23 06:00 KST.

    변환(UTC·trade_date)은 core/calendar 가 한다.
    """

    stck_bsop_date: Annotated[str, BeforeValidator(blank_to_none), Field(pattern=r"^\d{8}$")]
    stck_cntg_hour: Annotated[str, BeforeValidator(_hhmmss_upto_30)]
    futs_oprc: ReqDec
    futs_hgpr: ReqDec
    futs_lwpr: ReqDec
    futs_prpr: ReqDec  # 종가
    cntg_vol: Int = None
    acml_tr_pbmn: Int = None  # 천원, 누적


class MinuteQuote(KisModel):
    """분봉 조회 output1 — 조회 시점 선물 시세(metrics §7 선물 필드, 실측 `minute_day.json`).

    basis: KIS 베이시스(실측으로는 이론가 − 지수와 같다 — metrics §7 [확인 필요]), dprt: 괴리율(%),
    otst_stpl_qty_icdc: 미결제약정 증감(계약), tday_rltv: 체결강도(%), kospi200_nmix: 코스피200
    지수, hts_thpr: 이론가. 0 이하 가격·지수의 해석(없음)은 core.metrics.futures 가 한다.
    """

    futs_shrn_iscd: ReqStr
    futs_prpr: Dec = None
    basis: Dec = None
    dprt: Dec = None
    otst_stpl_qty_icdc: Int = None
    tday_rltv: Dec = None
    kospi200_nmix: Dec = None
    hts_thpr: Dec = None


# ── 공용 ─────────────────────────────────────────────────────────────────────


def output_rows(body: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    """응답 본문의 `output*` 를 행 목록으로. dict 한 개면 1행, 없거나 이상하면 빈 목록."""
    v = body.get(key)
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list):
        return [r for r in v if isinstance(r, dict)]
    return []


@dataclass(frozen=True)
class RowError:
    index: int
    error: str


def parse_rows[M: BaseModel](
    model: type[M], rows: Iterable[Mapping[str, Any]]
) -> tuple[list[M], list[RowError]]:
    """행 단위로 검증한다. 한 행이 틀려도 나머지는 살린다(예외 격리 — CLAUDE.md)."""
    ok: list[M] = []
    bad: list[RowError] = []
    for i, r in enumerate(rows):
        try:
            ok.append(model.model_validate(r))
        except ValidationError as e:
            bad.append(RowError(i, str(e.errors(include_url=False, include_input=False))))
    return ok, bad

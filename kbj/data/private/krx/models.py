"""KRX OpenAPI 일별 응답 행 모델 — 파생(GX 승격)과 주식·지수·ETF·ETN·종목기본정보(신규).

KRX 는 값을 모두 문자열로 준다. 여기서 한 번만 타입을 입힌다. 행 단위로 검증한다 — 한 행이
틀려도 나머지는 살린다(`parse_rows` — 예외 격리, CLAUDE.md 절대 규칙 4). 모르는 필드는 버린다.

파생 (승격 원본: GEXLAB `data/krx/models.py`, 공통 도우미는 GEXLAB `data/kis/models.py`
`blank_to_none`·`parse_yyyymmdd`·`Dec`·`Int`·`RowError`·`parse_rows`)
--------------------------------------------------------------------------------------------
- 선물(`/drv/fut_bydd_trd`): 세션은 `MKT_NM`(정규·야간). 이름 끝은 `(주간)`·`(야간)`
- 옵션(`/drv/opt_bydd_trd`): `MKT_NM` 이 없어 세션은 `ISU_NM` 끝 `(정규)`·`(야간)` 으로만 안다.
  코스닥150 위클리·미국달러 등은 끝 표기가 없다(세션 None). 만기·행사가 필드도 없어 이름에서 읽는다.

이름 표본 (GX 2026-09-23·2010-01-04 원본 — 형식만):

    코스피200 C 202610 1,100.0 (정규)      미니코스피 C 202610   752.5 (야간)
    코스피위클리 C 2609W4 1,000.0 (정규)   코스피위클리M P 2609W4   970.0 (야간)
    코스닥150 C 202610 1,000 (정규)        코스닥위클리M C 2609W4 1,200
    미국달러 C 201001 1,120.0              미니코스피 F 202610 (야간)

`IMP_VOLT`: 야간 행은 전부 `0.00`(값 없음 표시, 실제 IV 아님) → None. IV 0 은 어느 행에서도 의미가
없어 None 으로 둔다. 거래량 0 인 정규 행에는 만기별로 같은 값(행사가별 아님)이 자주 붙는다 — 쓸 때
`traded` 로 거른다. 코스닥150 위클리는 끝 표기 없이 같은 `ISU_CD` 가 두 행씩 온다 — 세션은 둘 다
None 이라 `(bas_dd, isu_cd, session)` 키가 겹치므로 적재는 코스피200 계열만 한다(GX).

주식·지수·ETF·ETN·종목기본정보 (신규 — 메인 결정 D7, docs/metrics.md §1·§4)
--------------------------------------------------------------------------------------------
- 필드 이름은 KRX OpenAPI 카탈로그 기준 **[실측 필요]**(probe_results §7 #14). 이름 후보가 둘 이상인
  칸은 `AliasChoices` 로 둘 다 받는다(ET `board/ingest/krx.py:FIELD` 후보 포함) — 개편되면 여기만
  고친다.
- 금액(거래대금 `ACC_TRDVAL`·시가총액 `MKTCAP`·순자산 `INVSTASST_NETASST_TOTAMT`)은 **원 단위
  정수**(metrics §0 — 억·조 환산은 화면에서만). 가격·NAV·등락률은 `Decimal`.
- 숫자 문자열의 쉼표는 턴다. 빈 값·`-` 는 None(값 없음 — 0 으로 채우지 않는다, ET D-019). 그 밖에
  숫자가 아니면 그 행은 검증 오류(형식이 바뀐 것 — 삼키지 않는다).
- 응답 행이 있는데 **한 행도** 모델에 맞지 않으면 `KrxRowsError` — 필드 이름이 바뀐 것을 빈 날로
  읽지 않는다(ET `_isu_to_code` 머리말의 '제일 나쁜 실패 방식').
- 각 행은 `source`(`KRX:<엔드포인트>`)·`as_of`(= `bas_dd`)·`quality` 를 낸다(절대 규칙 1).
- 거래소 구분(docs/metrics.md §1): KRX OpenAPI 일별은 **KRX 시장 체결분**으로 본다(넥스트레이드
  체결분 포함 여부 [실측 필요]) — 데이터셋 명세의 `venues=(KRX,)`(`datasets.py`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from kbj.core.quality import Quality
from kbj.data.private.kis.master import Family, expiry_code

# ── 공통 도우미 (GX data/kis/models.py) ─────────────────────────────────────────────────────


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


# ── 파생 (GX data/krx/models.py 그대로) ────────────────────────────────────────────────────

Session = Literal["day", "night"]

_SESSION_SUFFIX: dict[str, Session] = {"정규": "day", "주간": "day", "야간": "night"}
_SESSION_MKT: dict[str, Session] = {"정규": "day", "야간": "night"}

# ISU_NM 앞부분 → 상품군 (KRX 표기 — KIS 마스터 표기와 다르다)
_KRX_PREFIX: dict[str, Family] = {
    "코스피200": "kospi200",
    "미니코스피": "mini_kospi200",
    "코스피위클리": "kospi200_weekly_thu",  # PROD_NM '코스피200 위클리(목) 옵션'
    "코스피위클리M": "kospi200_weekly_mon",  # PROD_NM '코스피200 위클리(월) 옵션'
    "코스닥150": "kosdaq150",
    "코스닥위클리": "kosdaq150_weekly_thu",
    "코스닥위클리M": "kosdaq150_weekly_mon",
    "미국달러": "usd",
}

_OPT_NAME = re.compile(
    r"^(?P<prefix>\S+)\s+(?P<cp>[CP])\s+(?P<exp>\d{6}|\d{4}W\d)\s+"
    r"(?P<strike>\d{1,3}(?:,\d{3})*(?:\.\d+)?)(?:\s+\((?P<sess>[^)]+)\))?$"
)
_FUT_NAME = re.compile(r"^(?P<prefix>.+?)\s+F\s+(?P<exp>\d{6})(?:\s+\((?P<sess>[^)]+)\))?$")
_SUFFIX = re.compile(r"\((?P<sess>[^)]+)\)$")


@dataclass(frozen=True)
class OptionName:
    family: Family
    cp: Literal["C", "P"]
    expiry_token: str  # '202610', '2609W4'
    expiry: str  # KIS 6자리 '202610', '260904'
    strike: Decimal
    session: Session | None


def _session_of(tag: str | None) -> Session | None:
    if tag is None:
        return None
    if tag not in _SESSION_SUFFIX:
        raise ValueError(f"모르는 세션 표기: ({tag})")
    return _SESSION_SUFFIX[tag]


def parse_option_name(name: str) -> OptionName:
    """옵션 `ISU_NM` 을 상품군·콜풋·만기·행사가·세션으로. 형식이 다르면 ValueError."""
    m = _OPT_NAME.fullmatch(name.strip())
    if m is None:
        raise ValueError(f"옵션 종목명 형식이 아니다: {name!r}")
    token = m["exp"]
    cp: Literal["C", "P"] = "C" if m["cp"] == "C" else "P"
    return OptionName(
        family=_KRX_PREFIX.get(m["prefix"], "other"),
        cp=cp,
        expiry_token=token,
        expiry=expiry_code(token),
        strike=Decimal(m["strike"].replace(",", "")),
        session=_session_of(m["sess"]),
    )


Ymd = Annotated[date, BeforeValidator(parse_yyyymmdd)]
Text = Annotated[str, BeforeValidator(blank_to_none)]


class _KrxDaily(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    bas_dd: Ymd = Field(validation_alias="BAS_DD")  # = trade_date
    prod_nm: Text = Field(validation_alias="PROD_NM")
    isu_cd: Text = Field(validation_alias="ISU_CD")
    isu_nm: Text = Field(validation_alias="ISU_NM")
    tdd_opnprc: Dec = Field(default=None, validation_alias="TDD_OPNPRC")
    tdd_hgprc: Dec = Field(default=None, validation_alias="TDD_HGPRC")
    tdd_lwprc: Dec = Field(default=None, validation_alias="TDD_LWPRC")
    tdd_clsprc: Dec = Field(default=None, validation_alias="TDD_CLSPRC")
    cmpprevdd_prc: Dec = Field(default=None, validation_alias="CMPPREVDD_PRC")
    acc_trdvol: Int = Field(default=None, validation_alias="ACC_TRDVOL")
    acc_trdval: Int = Field(default=None, validation_alias="ACC_TRDVAL")  # 원
    acc_opnint_qty: Int = Field(default=None, validation_alias="ACC_OPNINT_QTY")

    @property
    def traded(self) -> bool:
        return (self.acc_trdvol or 0) > 0


class KrxFuturesDaily(_KrxDaily):
    """선물 일별 한 행. 세션은 `MKT_NM`, 없으면 이름 끝 표기."""

    mkt_nm: Annotated[str | None, BeforeValidator(blank_to_none)] = Field(
        default=None, validation_alias="MKT_NM"
    )
    spot_prc: Dec = Field(default=None, validation_alias="SPOT_PRC")
    setl_prc: Dec = Field(default=None, validation_alias="SETL_PRC")  # 야간 행은 빈 값
    session: Session | None = None
    family: Family = "other"
    expiry: str | None = None  # 이름이 '… F YYYYMM' 꼴일 때만

    @model_validator(mode="before")
    @classmethod
    def _derive(cls, data: Any) -> Any:
        if not isinstance(data, Mapping) or "ISU_NM" not in data:
            return data
        d: dict[str, Any] = dict(data)
        name = str(d["ISU_NM"]).strip()
        mkt = str(d.get("MKT_NM") or "").strip()
        suffix = _SUFFIX.search(name)
        by_name = _session_of(suffix["sess"]) if suffix else None
        by_mkt = _SESSION_MKT.get(mkt)
        if mkt and by_mkt is None:
            raise ValueError(f"모르는 MKT_NM: {mkt!r}")
        if by_mkt and by_name and by_mkt != by_name:
            raise ValueError(f"MKT_NM({mkt})과 이름 끝 표기가 다르다: {name!r}")
        d["session"] = by_mkt or by_name
        m = _FUT_NAME.fullmatch(name)
        if m is not None:
            d["family"] = _KRX_PREFIX.get(m["prefix"], "other")
            d["expiry"] = m["exp"]
        return d


class KrxOptionDaily(_KrxDaily):
    """옵션 일별 한 행. 상품군·콜풋·만기·행사가·세션은 `ISU_NM` 에서 읽는다."""

    rght_tp_nm: Literal["CALL", "PUT"] = Field(validation_alias="RGHT_TP_NM")
    imp_volt: Dec = Field(default=None, validation_alias="IMP_VOLT")  # %
    nxtdd_bas_prc: Dec = Field(default=None, validation_alias="NXTDD_BAS_PRC")
    family: Family
    cp: Literal["C", "P"]
    expiry_token: str
    expiry: str  # KIS 6자리 (월물 YYYYMM, 위클리 YYMMWW)
    strike: Decimal
    session: Session | None

    @model_validator(mode="before")
    @classmethod
    def _derive(cls, data: Any) -> Any:
        if not isinstance(data, Mapping) or "ISU_NM" not in data:
            return data
        d: dict[str, Any] = dict(data)
        p = parse_option_name(str(d["ISU_NM"]))
        tp = str(d.get("RGHT_TP_NM") or "").strip()
        if tp and tp[0] != p.cp:
            raise ValueError(f"RGHT_TP_NM({tp})과 이름의 콜/풋({p.cp})이 다르다")
        d.update(
            family=p.family,
            cp=p.cp,
            expiry_token=p.expiry_token,
            expiry=p.expiry,
            strike=p.strike,
            session=p.session,
        )
        if p.session == "night":
            d["IMP_VOLT"] = None  # 야간 행 '0.00' 은 값 없음 표시
        return d

    @field_validator("imp_volt")
    @classmethod
    def _zero_iv_is_missing(cls, v: Decimal | None) -> Decimal | None:
        return v if v is not None and v > 0 else None


def parse_futures_rows(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[list[KrxFuturesDaily], list[RowError]]:
    return parse_rows(KrxFuturesDaily, rows)


def parse_option_rows(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[list[KrxOptionDaily], list[RowError]]:
    """행 단위 격리 — 모르는 이름 형식 한 행이 그날 적재 전체를 막지 않는다."""
    return parse_rows(KrxOptionDaily, rows)


# ── 주식·지수·ETF·ETN·종목기본정보 (신규 — D7) ─────────────────────────────────────────────

_MISSING: Final = frozenset({"", "-"})
# 숫자가 아니지만 '값 없음' 으로 읽는 표기(종목기본정보 액면가 '무액면')
_NO_VALUE_WORDS: Final = frozenset({"무액면"})


def krx_number(v: object) -> object:
    """KRX 숫자 문자열 → 쉼표를 턴 문자열. 빈 값·`-`·'무액면' 은 None. 형 변환은 pydantic 이."""
    if isinstance(v, str):
        s = v.strip()
        if s in _MISSING or s in _NO_VALUE_WORDS:
            return None
        return s.replace(",", "")
    return v


Num = Annotated[Decimal | None, BeforeValidator(krx_number)]  # 가격·NAV·등락률·지수
Won = Annotated[int | None, BeforeValidator(krx_number)]  # 원 단위 금액 — 정수
Qty = Annotated[int | None, BeforeValidator(krx_number)]  # 수량·주식수·좌수
OptText = Annotated[str | None, BeforeValidator(blank_to_none)]
OptYmd = Annotated[date | None, BeforeValidator(parse_yyyymmdd)]

# 칸 → 응답 필드 이름 후보(앞이 KRX OpenAPI 카탈로그 이름, 뒤는 ET `board/ingest/krx.py:FIELD`
# 후보). 응답이 개편되면 여기만 고친다 [실측 필요 — probe_results §7 #14]
STOCK_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    "bas_dd": ("BAS_DD",),
    "isu_cd": ("ISU_SRT_CD", "ISU_CD"),
    "isu_nm": ("ISU_ABBRV", "ISU_NM"),
    "mkt_nm": ("MKT_NM",),
    "sect_tp_nm": ("SECT_TP_NM",),  # 소속부(코스닥 우량기업부 등) — 업종이 아닐 수 있다
    "idx_ind_nm": ("IDX_IND_NM",),  # 업종명(ET 첫 후보 — 주식 일별에 있는지 [실측 필요])
    "tdd_clsprc": ("TDD_CLSPRC",),
    "cmpprevdd_prc": ("CMPPREVDD_PRC",),
    "fluc_rt": ("FLUC_RT",),
    "tdd_opnprc": ("TDD_OPNPRC",),
    "tdd_hgprc": ("TDD_HGPRC",),
    "tdd_lwprc": ("TDD_LWPRC",),
    "acc_trdvol": ("ACC_TRDVOL",),
    "acc_trdval": ("ACC_TRDVAL",),
    "mktcap": ("MKTCAP",),
    "list_shrs": ("LIST_SHRS",),
}


def _alias(*names: str) -> AliasChoices:
    return AliasChoices(*names)


def _f(field: str) -> AliasChoices:
    return _alias(*STOCK_FIELDS[field])


class _KrxRow(BaseModel):
    """주식·지수·ETP 행 공통 — 출처·기준일·품질(절대 규칙 1)."""

    # 파이썬 이름(`isu_cd`)으로는 받지 않는다 — 필드 이름이 소문자로 바뀐 응답을 맞는 행으로
    # 읽지 않게
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=False)

    source: str = "KRX"  # `KRX:<엔드포인트>` — parse_* 가 넣는다
    bas_dd: Ymd = Field(validation_alias=_alias("BAS_DD", "bas_dd"))

    @property
    def as_of(self) -> date:
        """값이 가리키는 거래일(받은 시각이 아니다)."""
        return self.bas_dd


class KrxStockDaily(_KrxRow):
    """유가·코스닥·코넥스 일별매매정보 한 행(`sto/stk_bydd_trd`·`ksq_bydd_trd`·`knx_bydd_trd`).

    `acc_trdval` 거래대금(원) — 시장 거래대금·회전율·급증 배수의 원장(metrics §1). 정규장·시간외를
    나눠 주는지 [실측 필요].
    """

    isu_cd: Text = Field(validation_alias=_f("isu_cd"))  # 단축코드(6자리) 또는 표준코드
    isu_nm: Text = Field(validation_alias=_f("isu_nm"))
    mkt_nm: OptText = Field(default=None, validation_alias=_f("mkt_nm"))
    sect_tp_nm: OptText = Field(default=None, validation_alias=_f("sect_tp_nm"))
    idx_ind_nm: OptText = Field(default=None, validation_alias=_f("idx_ind_nm"))
    tdd_clsprc: Num = Field(default=None, validation_alias=_f("tdd_clsprc"))
    cmpprevdd_prc: Num = Field(default=None, validation_alias=_f("cmpprevdd_prc"))
    fluc_rt: Num = Field(default=None, validation_alias=_f("fluc_rt"))  # %
    tdd_opnprc: Num = Field(default=None, validation_alias=_f("tdd_opnprc"))
    tdd_hgprc: Num = Field(default=None, validation_alias=_f("tdd_hgprc"))
    tdd_lwprc: Num = Field(default=None, validation_alias=_f("tdd_lwprc"))
    acc_trdvol: Qty = Field(default=None, validation_alias=_f("acc_trdvol"))
    acc_trdval: Won = Field(default=None, validation_alias=_f("acc_trdval"))  # 원
    mktcap: Won = Field(default=None, validation_alias=_f("mktcap"))  # 원
    list_shrs: Qty = Field(default=None, validation_alias=_f("list_shrs"))

    @property
    def quality(self) -> Quality:
        """종가가 없으면 invalid(화면·계산에 쓰지 않는다). 거래대금만 없으면 ok — 못 받은 칸은
        None 으로 남고 원장 계산이 그 칸을 뺀다."""
        return Quality.OK if self.tdd_clsprc is not None else Quality.INVALID


class KrxEtfDaily(_KrxRow):
    """ETF 일별매매정보 한 행(`etp/etf_bydd_trd`) — ETF 수급 원장(metrics §4).

    순유입 = Σ(상장좌수ₜ − 상장좌수ₜ₋₁) × NAVₜ, 가격효과 = Σ 상장좌수ₜ₋₁ × (NAVₜ − NAVₜ₋₁) 의 입력이
    `nav`·`list_shrs`, 검산 ③ 의 순자산이 `invstasst_netasst_totamt`. NAV 는 장 마감 확정치만(장중
    iNAV 는 KIS `FHPST02400000` — 괴리율 경고용). 필드 이름·공표 시각 [실측 필요].
    """

    isu_cd: Text = Field(validation_alias=_alias("ISU_SRT_CD", "ISU_CD"))
    isu_nm: Text = Field(validation_alias=_alias("ISU_ABBRV", "ISU_NM"))
    tdd_clsprc: Num = Field(default=None, validation_alias=_alias("TDD_CLSPRC"))
    cmpprevdd_prc: Num = Field(default=None, validation_alias=_alias("CMPPREVDD_PRC"))
    fluc_rt: Num = Field(default=None, validation_alias=_alias("FLUC_RT"))
    nav: Num = Field(default=None, validation_alias=_alias("NAV"))
    tdd_opnprc: Num = Field(default=None, validation_alias=_alias("TDD_OPNPRC"))
    tdd_hgprc: Num = Field(default=None, validation_alias=_alias("TDD_HGPRC"))
    tdd_lwprc: Num = Field(default=None, validation_alias=_alias("TDD_LWPRC"))
    acc_trdvol: Qty = Field(default=None, validation_alias=_alias("ACC_TRDVOL"))
    acc_trdval: Won = Field(default=None, validation_alias=_alias("ACC_TRDVAL"))  # 원
    mktcap: Won = Field(default=None, validation_alias=_alias("MKTCAP"))  # 원
    invstasst_netasst_totamt: Won = Field(  # 순자산총액(원)
        default=None, validation_alias=_alias("INVSTASST_NETASST_TOTAMT")
    )
    list_shrs: Qty = Field(default=None, validation_alias=_alias("LIST_SHRS"))  # 상장좌수
    idx_ind_nm: OptText = Field(default=None, validation_alias=_alias("IDX_IND_NM"))  # 기초지수
    obj_stkprc_idx: Num = Field(default=None, validation_alias=_alias("OBJ_STKPRC_IDX"))
    cmpprevdd_idx: Num = Field(default=None, validation_alias=_alias("CMPPREVDD_IDX"))
    fluc_rt_idx: Num = Field(default=None, validation_alias=_alias("FLUC_RT_IDX"))

    @property
    def net_assets(self) -> int | None:
        """순자산총액(원)."""
        return self.invstasst_netasst_totamt

    @property
    def quality(self) -> Quality:
        """순유입 계산에 필요한 종가·NAV·상장좌수 중 하나라도 없으면 invalid."""
        need = (self.tdd_clsprc, self.nav, self.list_shrs)
        return Quality.OK if all(x is not None for x in need) else Quality.INVALID


class KrxEtnDaily(_KrxRow):
    """ETN 일별매매정보 한 행(`etp/etn_bydd_trd`). 지표가치(`PER1SECU_INDIC_VAL`) [실측 필요]."""

    isu_cd: Text = Field(validation_alias=_alias("ISU_SRT_CD", "ISU_CD"))
    isu_nm: Text = Field(validation_alias=_alias("ISU_ABBRV", "ISU_NM"))
    tdd_clsprc: Num = Field(default=None, validation_alias=_alias("TDD_CLSPRC"))
    cmpprevdd_prc: Num = Field(default=None, validation_alias=_alias("CMPPREVDD_PRC"))
    fluc_rt: Num = Field(default=None, validation_alias=_alias("FLUC_RT"))
    per1secu_indic_val: Num = Field(default=None, validation_alias=_alias("PER1SECU_INDIC_VAL"))
    tdd_opnprc: Num = Field(default=None, validation_alias=_alias("TDD_OPNPRC"))
    tdd_hgprc: Num = Field(default=None, validation_alias=_alias("TDD_HGPRC"))
    tdd_lwprc: Num = Field(default=None, validation_alias=_alias("TDD_LWPRC"))
    acc_trdvol: Qty = Field(default=None, validation_alias=_alias("ACC_TRDVOL"))
    acc_trdval: Won = Field(default=None, validation_alias=_alias("ACC_TRDVAL"))
    mktcap: Won = Field(default=None, validation_alias=_alias("MKTCAP"))
    indic_val_amt: Won = Field(default=None, validation_alias=_alias("INDIC_VAL_AMT"))
    list_shrs: Qty = Field(default=None, validation_alias=_alias("LIST_SHRS"))
    idx_ind_nm: OptText = Field(default=None, validation_alias=_alias("IDX_IND_NM"))

    @property
    def quality(self) -> Quality:
        return Quality.OK if self.tdd_clsprc is not None else Quality.INVALID


class KrxIndexDaily(_KrxRow):
    """지수 일별시세 한 행(`idx/kospi_dd_trd`·`kosdaq_dd_trd`·`krx_dd_trd`).

    가격 칸은 KRX 카탈로그 이름(`CLSPRC_IDX` 등)이 먼저, ET 가 쓰던 주식 이름(`TDD_CLSPRC`)이 다음
    후보다 — ET `fetch_index` 는 주식 이름만 봐서 지수 값을 못 받았을 수 있다 [실측 필요].
    """

    idx_clss: OptText = Field(default=None, validation_alias=_alias("IDX_CLSS"))  # 계열 구분
    idx_nm: Text = Field(validation_alias=_alias("IDX_NM"))
    clsprc_idx: Num = Field(default=None, validation_alias=_alias("CLSPRC_IDX", "TDD_CLSPRC"))
    cmpprevdd_idx: Num = Field(
        default=None, validation_alias=_alias("CMPPREVDD_IDX", "CMPPREVDD_PRC")
    )
    fluc_rt: Num = Field(default=None, validation_alias=_alias("FLUC_RT"))
    opnprc_idx: Num = Field(default=None, validation_alias=_alias("OPNPRC_IDX", "TDD_OPNPRC"))
    hgprc_idx: Num = Field(default=None, validation_alias=_alias("HGPRC_IDX", "TDD_HGPRC"))
    lwprc_idx: Num = Field(default=None, validation_alias=_alias("LWPRC_IDX", "TDD_LWPRC"))
    acc_trdvol: Qty = Field(default=None, validation_alias=_alias("ACC_TRDVOL"))
    acc_trdval: Won = Field(default=None, validation_alias=_alias("ACC_TRDVAL"))
    mktcap: Won = Field(default=None, validation_alias=_alias("MKTCAP"))

    @property
    def quality(self) -> Quality:
        return Quality.OK if self.clsprc_idx is not None else Quality.INVALID


class KrxStockBaseInfo(_KrxRow):
    """종목기본정보 한 행(`sto/stk_isu_base_info`·`ksq_isu_base_info`) — 유니버스(종류·상장일).

    응답에 기준일이 없을 수 있어 parse 가 요청한 `bas_dd` 를 넣는다 [실측 필요].
    """

    isu_cd: Text = Field(validation_alias=_alias("ISU_CD"))  # 표준코드(ISIN)
    isu_srt_cd: Text = Field(validation_alias=_alias("ISU_SRT_CD"))  # 단축코드
    isu_nm: Text = Field(validation_alias=_alias("ISU_NM"))
    isu_abbrv: OptText = Field(default=None, validation_alias=_alias("ISU_ABBRV"))
    isu_eng_nm: OptText = Field(default=None, validation_alias=_alias("ISU_ENG_NM"))
    list_dd: OptYmd = Field(default=None, validation_alias=_alias("LIST_DD"))
    mkt_tp_nm: OptText = Field(default=None, validation_alias=_alias("MKT_TP_NM"))
    secugrp_nm: OptText = Field(default=None, validation_alias=_alias("SECUGRP_NM"))  # 증권구분
    sect_tp_nm: OptText = Field(default=None, validation_alias=_alias("SECT_TP_NM"))  # 소속부
    kind_stkcert_tp_nm: OptText = Field(  # 주식종류(보통주·우선주 …)
        default=None, validation_alias=_alias("KIND_STKCERT_TP_NM")
    )
    parval: Num = Field(default=None, validation_alias=_alias("PARVAL"))  # 액면가(무액면 None)
    list_shrs: Qty = Field(default=None, validation_alias=_alias("LIST_SHRS"))

    @property
    def quality(self) -> Quality:
        return Quality.OK


class KrxRowsError(ValueError):
    """응답 행이 있는데 한 행도 모델에 맞지 않는다 — 필드 이름이 바뀐 것으로 본다(빈 날 아님)."""

    def __init__(self, source: str, n: int, first: RowError) -> None:
        self.source = source
        self.n = n
        self.first = first
        super().__init__(f"{source}: {n}행 모두 형식 오류 — 첫 행: {first.error[:300]}")


def parse_strict[M: _KrxRow](
    model: type[M],
    rows: Iterable[Mapping[str, Any]],
    *,
    source: str,
    bas_dd: date | None = None,
) -> tuple[list[M], list[RowError]]:
    """행 단위로 검증하고 `source`(와 응답에 없을 때 `bas_dd`)를 넣는다.

    행이 있는데 하나도 맞지 않으면 `KrxRowsError`. 일부만 틀리면 (맞은 행, 틀린 행 목록).
    """
    extra: dict[str, Any] = {"source": source}
    if bas_dd is not None:
        extra["bas_dd"] = f"{bas_dd:%Y%m%d}"
    # 응답의 `BAS_DD` 가 있으면 그것이 먼저다(별칭 순서) — 넣는 `bas_dd` 는 없을 때만 쓰인다
    items = [{**dict(r), **extra} for r in rows]
    ok, bad = parse_rows(model, items)
    if items and not ok:
        raise KrxRowsError(source, len(items), bad[0])
    return ok, bad


def parse_stock_daily_rows(
    rows: Iterable[Mapping[str, Any]], *, endpoint: str
) -> tuple[list[KrxStockDaily], list[RowError]]:
    """주식 일별 행들. `endpoint` 는 `/sto/stk_bydd_trd` 등(출처 표기에 들어간다)."""
    return parse_strict(KrxStockDaily, rows, source=f"KRX:{endpoint.lstrip('/')}")


def parse_etf_daily_rows(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[list[KrxEtfDaily], list[RowError]]:
    """ETF 일별 행들(출처 `KRX:etp/etf_bydd_trd`)."""
    return parse_strict(KrxEtfDaily, rows, source="KRX:etp/etf_bydd_trd")


def parse_etn_daily_rows(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[list[KrxEtnDaily], list[RowError]]:
    """ETN 일별 행들(출처 `KRX:etp/etn_bydd_trd`)."""
    return parse_strict(KrxEtnDaily, rows, source="KRX:etp/etn_bydd_trd")


def parse_index_daily_rows(
    rows: Iterable[Mapping[str, Any]], *, endpoint: str
) -> tuple[list[KrxIndexDaily], list[RowError]]:
    """지수 일별 행들. `endpoint` 는 `/idx/kospi_dd_trd` 등."""
    return parse_strict(KrxIndexDaily, rows, source=f"KRX:{endpoint.lstrip('/')}")


def parse_base_info_rows(
    rows: Iterable[Mapping[str, Any]], *, endpoint: str, bas_dd: date
) -> tuple[list[KrxStockBaseInfo], list[RowError]]:
    """종목기본정보 행들. 응답에 `BAS_DD` 가 있으면 그 값, 없으면 요청한 `bas_dd`."""
    return parse_strict(KrxStockBaseInfo, rows, source=f"KRX:{endpoint.lstrip('/')}", bas_dd=bas_dd)

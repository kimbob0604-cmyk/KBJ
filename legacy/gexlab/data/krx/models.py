"""KRX Open API 파생상품 일별매매정보 모델 (docs/phase1_design.md §8, docs/probe_results.md #15).

- 선물(`/drv/fut_bydd_trd`): 세션은 `MKT_NM`(정규·야간). 이름 끝은 `(주간)`·`(야간)`
- 옵션(`/drv/opt_bydd_trd`): `MKT_NM` 이 없어 세션은 `ISU_NM` 끝 `(정규)`·`(야간)` 으로만 안다.
  코스닥150 위클리·미국달러 등은 끝 표기가 없다(세션 None). 만기·행사가 필드도 없어 이름에서 읽는다.

이름 표본 (2026-09-23·2010-01-04 원본):

    코스피200 C 202610 1,100.0 (정규)      미니코스피 C 202610   752.5 (야간)
    코스피위클리 C 2609W4 1,000.0 (정규)   코스피위클리M P 2609W4   970.0 (야간)
    코스닥150 C 202610 1,000 (정규)        코스닥위클리M C 2609W4 1,200
    미국달러 C 201001 1,120.0              미니코스피 F 202610 (야간)

`IMP_VOLT`: 야간 행은 전부 `0.00` (값 없음 표시, 실제 IV 아님) → None. IV 0 은 어느 행에서도
의미가 없어 None 으로 둔다. 거래량 0 인 정규 행에는 만기별로 같은 값(행사가별 아님)이 자주
붙는다 — 쓸 때 `traded` 로 거른다.

코스닥150 위클리는 끝 표기 없이 같은 `ISU_CD` 가 두 행씩 온다(2026-09-23: 108종목 × 2). 한 행은
`IMP_VOLT` 0.00·거래량 0 이라 야간 행으로 보이지만 확인 전이라 세션은 둘 다 None 으로 둔다 —
`(bas_dd, isu_cd, session)` 키가 겹치므로 적재는 코스피200 계열만 한다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from data.kis.master import Family, expiry_code
from data.kis.models import Dec, Int, RowError, blank_to_none, parse_rows, parse_yyyymmdd

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

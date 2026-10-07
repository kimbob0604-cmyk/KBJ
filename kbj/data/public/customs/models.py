"""관세청 수출입(공공데이터포털) 응답 모델·정규화 — 공개 등급(DATA_TIERS §1, 이용허락 "제한 없음").

명세: `docs/probe_results.md` §2(키 없이 공식 문서·swagger 로 조사 — 실호출 확인은 [실측 필요]).

- 모든 행은 `source`(`DATAGO:<포털 ID>`)·`as_of`(기준 월·10일 구간)·`quality` 를 가진다
  (절대 규칙 1). 월간 행은 수출·수입 금액이 있으면 `ok`, 비면 `invalid`. 10일 단위 값은 잠정치라
  `estimated`.
- **금액·중량·건수 문자열**: 쉼표·앞 공백이 붙어 온다(10일 잠정치 `" 13,886,115"`).
  `kbj.data.datago.parse_amount` 가 푼다. 빈 값·`-` 는 None, 그 밖에 숫자가 아니면 `ValueError`
  (형식이 바뀐 것 — 삼키지 않는다).
- **HS 코드 앞자리 0**: 15101609 swagger 는 `hsCode` 를 number 로 적었다 — `0106…` 이 `106…`
  으로 올 수 있다 [실측 필요]. HS 는 2·4·6·10단위(짝수 자리)라 홀수 자리면 앞에 0 을 붙인다
  (`normalize_hs`).
- **10일 단위 잠정치**(15157908·941·901·909): 응답 열이 의미가 아니라 번호(`itemUsdAmt00`~`10`)다.
  번호 → 이름 대응(`TEN_DAY_COLUMNS`)을 여기 저장하고, 열 구성이 바뀌거나(번호가 늘거나 빠짐)
  swagger 설명이 저장한 이름과 다르면 `TenDayColumnsChanged` 로 실패한다(probe_results §2.6 [주의]).
- **시도 코드 개편(2026-07-01)**: 전남광주통합특별시 `12`(7월 이후 신고분), 광주 `29`·전남 `46` 은
  7월 이전 신고분에만(probe_results F4·§2.5). `SIDO_CODES` 에 쓸 수 있는 기간을 둔다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from kbj.core.quality import Quality

SOURCE_PREFIX: Final = "DATAGO"

TenDayKind = Literal["exp_item", "exp_country", "imp_item", "imp_country"]
TEN_DAY_KINDS: Final[tuple[TenDayKind, ...]] = (
    "exp_item",
    "exp_country",
    "imp_item",
    "imp_country",
)

# 10일 잠정치 열 번호 → 이름(probe_results §2.6 표 — swagger `description`·참고문서 기준).
# 00 은 모두 "전체". 관세청이 10대 품목·국가 구성을 바꾸면 이 표와 응답이 어긋난다 → 실패
TEN_DAY_COLUMNS: Final[Mapping[TenDayKind, tuple[str, ...]]] = {
    "exp_item": (
        "전체",
        "반도체",
        "철강제품",
        "승용차",
        "석유제품",
        "무선통신기기",
        "선박",
        "자동차부품",
        "컴퓨터주변기기",
        "정밀기기",
        "가전제품",
    ),
    "exp_country": (
        "전체",
        "중국",
        "미국",
        "유럽연합",
        "베트남",
        "홍콩",
        "일본",
        "대만",
        "인도",
        "싱가포르",
        "말레이시아",
    ),
    "imp_item": (
        "전체",
        "반도체",
        "원유",
        "기계류",
        "가스",
        "반도체 제조용 장비",
        "정밀기기",
        "석유제품",
        "무선통신기기",
        "승용차",
        "석탄",
    ),
    "imp_country": (
        "전체",
        "중국",
        "미국",
        "유럽연합",
        "일본",
        "베트남",
        "호주",
        "대만",
        "사우디아라비아",
        "러시아연방",
        "말레이시아",
    ),
}
TEN_DAY_COLUMN_COUNT: Final = 11
_AMOUNT_COL = re.compile(r"itemUsdAmt(\d{2})")
_SPAN = re.compile(r"01\s*~\s*(\d{2})")


@dataclass(frozen=True)
class SidoCode:
    """시도 코드 하나와 쓸 수 있는 신고 기간(YYYYMM, 양 끝 포함 — None 은 끝없음)."""

    code: str
    name: str
    valid_from: str | None = None
    valid_to: str | None = None

    def valid_in(self, ym: str) -> bool:
        return (self.valid_from is None or ym >= self.valid_from) and (
            self.valid_to is None or ym <= self.valid_to
        )


# 관세청조회코드_v1.3.xlsx '시도코드' 시트(18행) — probe_results §2.5
SIDO_CODES: Final[Mapping[str, SidoCode]] = {
    s.code: s
    for s in (
        SidoCode("11", "서울특별시"),
        SidoCode("12", "전남광주통합특별시", valid_from="202607"),
        SidoCode("26", "부산광역시"),
        SidoCode("27", "대구광역시"),
        SidoCode("28", "인천광역시"),  # 시군구 코드 개편 — sggNm 표기 변화 [실측 필요]
        SidoCode("29", "광주광역시", valid_to="202606"),
        SidoCode("30", "대전광역시"),
        SidoCode("31", "울산광역시"),
        SidoCode("36", "세종특별자치시"),
        SidoCode("41", "경기도"),
        SidoCode("43", "충청북도"),
        SidoCode("44", "충청남도"),
        SidoCode("46", "전라남도", valid_to="202606"),
        SidoCode("47", "경상북도"),
        SidoCode("48", "경상남도"),
        SidoCode("50", "제주특별자치도"),
        SidoCode("51", "강원특별자치도"),
        SidoCode("52", "전북특별자치도"),
    )
}

# 합계 행으로 보는 기간 표기 [추정 — 합계 행이 오는지 자체가 실측 필요(probe_results §2.2 ④)]
_TOTAL_MARKERS: Final = frozenset({"총계", "합계", "계", "전체"})
_MISSING: Final = frozenset({"", "-"})  # HS·기간 빈 값 표기
_PERIOD = re.compile(r"(\d{4})\s*[.\-/년]?\s*(\d{1,2})\s*월?")


class CustomsFormatError(ValueError):
    """응답 모양이 명세와 다르다 — 열이 빠졌거나 값 형식이 바뀌었다."""


class TenDayColumnsChanged(CustomsFormatError):
    """10일 잠정치 열 번호 → 이름 대응이 저장한 것과 다르다(관세청이 10대 구성을 바꿨다)."""


def normalize_hs(raw: object) -> str | None:
    """HS 코드 문자열. 숫자형으로 와서 앞자리 0 이 빠진 홀수 자리(1·3·5·9)는 0 을 붙인다.

    빈 값·합계 표기는 None. 숫자가 아니면 `ValueError`.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s in _MISSING or s in _TOTAL_MARKERS:
        return None
    if s.endswith(".0"):  # number 로 직렬화된 값(`106.0`) [추정]
        s = s[:-2]
    if not s.isdigit():
        raise ValueError(f"HS 코드가 숫자가 아니다: {s[:20]!r}")
    if len(s) % 2 == 1 and len(s) + 1 in (2, 4, 6, 10):
        s = "0" + s
    return s


def parse_period(raw: object) -> str | None:
    """기간 표기 → `YYYYMM`. `2016.01`·`201601`·`2016-01`·`2016년 01월` 을 받는다.

    합계 표기(`총계` 등)는 None. 모르는 형식은 `ValueError`.
    """
    s = str(raw if raw is not None else "").strip()
    if s in _TOTAL_MARKERS:
        return None
    m = _PERIOD.fullmatch(s)
    if m is None:
        raise ValueError(f"기간 형식을 모른다: {s[:20]!r}")
    month = int(m.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"월이 틀렸다: {s[:20]!r}")
    return f"{m.group(1)}{month:02d}"


class TradeRow(BaseModel):
    """월간 수출입 한 행(15100475 품목×국가·15101609 품목·15101612 국가 공통).

    금액은 미화 달러(수출 FOB·수입 CIF), 중량은 순중량 kg(probe_results §2.1 공통). 그 데이터셋에
    없는 열은 None(15101612 는 중량 대신 건수).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str  # DATAGO:<포털 ID>
    period: str | None  # YYYYMM. None = 합계 행
    country: str | None = None  # ISO 2자리(statCd)
    country_name: str | None = None
    hs: str | None = None
    item_name: str | None = None
    exp_weight_kg: Decimal | None = None
    exp_usd: Decimal | None = None
    imp_weight_kg: Decimal | None = None
    imp_usd: Decimal | None = None
    exp_count: Decimal | None = None
    imp_count: Decimal | None = None
    balance_usd: Decimal | None = None

    @property
    def as_of(self) -> str | None:
        """기준 월 `YYYYMM`(받은 시각이 아니다). None = 합계 행."""
        return self.period

    @property
    def quality(self) -> Quality:
        """수출·수입 금액이 둘 다 있으면 ok, 하나라도 비면 invalid(빈 값을 0 으로 채우지 않는다)."""
        return _both(self.exp_usd, self.imp_usd)


class SigunguRow(BaseModel):
    """시군구별 품목별 한 행(15134343). 금액 미화 달러, 중량 없음, 시군구는 이름만(코드 없음)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    period: str | None  # priodTitle → YYYYMM [실측 필요: 표기]
    sido: str  # 요청한 시도 코드(응답에 없다)
    sigungu_name: str
    hs6: str | None
    item_name: str | None
    exp_count: Decimal | None
    exp_usd: Decimal | None
    imp_count: Decimal | None
    imp_usd: Decimal | None
    balance_usd: Decimal | None

    @property
    def as_of(self) -> str | None:
        """기준 월 `YYYYMM`(받은 시각이 아니다). None = 합계 행."""
        return self.period

    @property
    def quality(self) -> Quality:
        """수출·수입 금액이 둘 다 있으면 ok, 하나라도 비면 invalid."""
        return _both(self.exp_usd, self.imp_usd)


def _both(a: Decimal | None, b: Decimal | None) -> Quality:
    return Quality.OK if a is not None and b is not None else Quality.INVALID


class TenDayRow(BaseModel):
    """10일 단위 잠정치 한 행 — 그 달 1일부터 `span` 끝 날까지 누계(천 달러)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    kind: TenDayKind
    month: str  # YYYYMM (priodMon)
    span: str  # "01~10"·"01~20"·"01~말일"
    segment: int = Field(ge=1, le=3)  # 1 = ~10일, 2 = ~20일, 3 = 말일
    amounts: dict[str, Decimal | None]  # 열 번호 "00"~"10" → 천 달러

    @property
    def as_of(self) -> str:
        """데이터 키 `ten_day` 규칙(설계 §6.4) — `YYYYMM-1`·`-2`·`-3`."""
        return f"{self.month}-{self.segment}"

    @property
    def quality(self) -> Quality:
        """전체 열(`00`)이 있으면 estimated(관세청 **잠정치** — 확정치는 월간 15101609), 없으면
        invalid.

        잠정치를 estimated 로 두는 것은 metrics.md 의 '잠정 = estimated' 규칙을 따른 것이다
        [확인 필요 — P6 수집기에서 확정치 대조 규칙과 함께 정한다].
        """
        return Quality.ESTIMATED if self.amounts.get("00") is not None else Quality.INVALID

    def by_name(self) -> dict[str, Decimal | None]:
        """열 이름(저장한 대응) → 금액."""
        names = TEN_DAY_COLUMNS[self.kind]
        return {names[int(n)]: v for n, v in sorted(self.amounts.items())}


def ten_day_segment(span: str) -> int:
    """`01~10` → 1, `01~20` → 2, `01~28`~`01~31` → 3. 그 밖은 `CustomsFormatError`.

    말일 표기(`01~30`·`01~31`·`01~28`)가 실제 그달 말일인지는 [실측 필요] — 28~31 을 받는다.
    """
    m = _SPAN.fullmatch(span.strip())
    if m is None:
        raise CustomsFormatError(f"priodDt 형식을 모른다: {span[:20]!r}")
    end = int(m.group(1))
    if end == 10:
        return 1
    if end == 20:
        return 2
    if 28 <= end <= 31:
        return 3
    raise CustomsFormatError(f"priodDt 끝 날이 10·20·말일이 아니다: {span[:20]!r}")


def ten_day_amount_columns(row: Mapping[str, object]) -> list[str]:
    """행의 금액 열 번호 목록(정렬). 00~10 이 아니면 `TenDayColumnsChanged`."""
    cols = sorted(m.group(1) for k in row if (m := _AMOUNT_COL.fullmatch(k)))
    expected = [f"{i:02d}" for i in range(TEN_DAY_COLUMN_COUNT)]
    if cols != expected:
        raise TenDayColumnsChanged(
            f"10일 잠정치 금액 열이 00~10 이 아니다: {','.join(cols) or '없음'} — "
            "관세청 10대 구성 변경 여부를 확인하고 TEN_DAY_COLUMNS 를 고친다"
        )
    return cols


def verify_ten_day_columns(kind: TenDayKind, descriptions: Mapping[str, str]) -> None:
    """swagger 의 열 설명(`itemUsdAmtNN` → 설명)이 저장한 이름을 담는지 본다.

    빠진 열·이름이 다른 열이 하나라도 있으면 `TenDayColumnsChanged`(어느 열인지 담는다). 설명 문구의
    정확한 형식은 [실측 필요] — 저장한 이름이 설명 안에 (공백 무시) 들어 있으면 같다고 본다.
    """
    names = TEN_DAY_COLUMNS[kind]
    extra = sorted(
        k for k in descriptions if _AMOUNT_COL.fullmatch(k) and int(k[-2:]) >= len(names)
    )
    bad: list[str] = [f"{k}(새 열)" for k in extra]
    for i, name in enumerate(names):
        col = f"itemUsdAmt{i:02d}"
        desc = descriptions.get(col)
        if desc is None:
            bad.append(f"{col}(없음)")
        elif _squash(name) not in _squash(desc):
            bad.append(f"{col}({name} 아님)")
    if bad:
        raise TenDayColumnsChanged(f"{kind} 열 대응이 바뀌었다: {', '.join(bad)}")


def _squash(s: str) -> str:
    return "".join(s.split())

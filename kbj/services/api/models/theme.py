"""`/api/themes/*` 응답 모델 — 페이지 9 테마·밸류체인(ADR 0039, docs/metrics.md §31).

- `ThemeCatalog` — 대테마 분류(공개 등급 칸만: id·이름·섹션·밸류체인). 공개 파일 `themes.json` 의
  data 와 같은 모양(공개 내보내기 `themes_json` — 시험이 대조).
- `ThemeStrength`·`ThemeDetail` — 로그인(네이버 유래 편입·사유, 시세 기반 강도). 금액은 원.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from kbj.services.api.models.common import ApiModel

__all__ = [
    "ChainStrengthRow",
    "NaverThemeRef",
    "ThemeCatalog",
    "ThemeDetail",
    "ThemeInfo",
    "ThemeLeader",
    "ThemeMember",
    "ThemeMemberReason",
    "ThemeSection",
    "ThemeStats",
    "ThemeStrength",
    "ThemeStrengthRow",
    "ValueChainGroup",
]

ThemeIdT = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")]


class ThemeInfo(ApiModel):
    """대테마 하나(공개 칸)."""

    id: ThemeIdT
    name: str
    section: str
    valuechain: str | None


class ThemeSection(ApiModel):
    section: str
    themes: list[ThemeInfo]


class ValueChainGroup(ApiModel):
    """밸류체인 묶음 — 대테마 valuechain 값이 같은 대테마들(원천 순서)."""

    key: str
    themes: list[str]


class ThemeCatalog(ApiModel):
    """대테마 분류 — 섹션별(섹션은 원천 첫 등장 순서)·밸류체인별(이름순)."""

    built_at: AwareDatetime
    n_themes: int = Field(ge=0)
    sections: list[ThemeSection]
    valuechains: list[ValueChainGroup]


class ThemeLeader(ApiModel):
    """주도주 — 거래대금이 가장 큰 종목(보드 롤업 규칙)."""

    code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    name: str
    chg_pct: float | None


class ThemeStats(ApiModel):
    """강도 — 등락은 %(편입 종목 동일가중 평균 — weighted False, metrics §31), 20일 = 21거래일 전
    종가 대비(보드 ret_21d). up_ratio = 상승 / 등락을 아는 종목(없으면 None). 시총 합은 원(아는
    종목만)."""

    n_members: int = Field(ge=0)
    n_priced: int = Field(ge=0)
    chg_pct: float | None
    ret_5d: float | None
    ret_20d: float | None
    weighted: bool
    up: int = Field(ge=0)
    flat: int = Field(ge=0)
    down: int = Field(ge=0)
    unknown: int = Field(ge=0)
    up_ratio: float | None = Field(ge=0, le=1)
    n_newhigh: int = Field(ge=0)
    n_near: int = Field(ge=0)
    turnover_mult: float | None
    mktcap: int | None
    leader: ThemeLeader | None


class ThemeStrengthRow(ApiModel):
    rank: int = Field(ge=1)
    id: ThemeIdT
    name: str
    section: str
    valuechain: str | None
    stats: ThemeStats


class ChainStrengthRow(ApiModel):
    key: str
    themes: list[str]
    stats: ThemeStats


class ThemeStrength(ApiModel):
    """강도 순위 — 고른 기간(1·5·20일) 내림차순(값 없음은 뒤)."""

    trade_date: date
    period: Literal[1, 5, 20]
    built_at: AwareDatetime
    rows: list[ThemeStrengthRow]
    chains: list[ChainStrengthRow]


class NaverThemeRef(ApiModel):
    """대테마에 걸린 네이버 테마(로그인). count = 네이버 전체 종목 수(ETF 포함), in_master = 원천이
    센 마스터 안 종목 수, n_members = 받은 편입 줄 수(종목 마스터 안)."""

    no: int = Field(gt=0)
    name: str
    count: int | None
    in_master: int | None
    n_members: int = Field(ge=0)


class ThemeMemberReason(ApiModel):
    naver_no: int = Field(gt=0)
    reason: str | None


class ThemeMember(ApiModel):
    """편입 종목 한 줄 — 종목 마스터·편입 사유(로그인) + 보드 종목 하루(없으면 None)."""

    code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    name: str
    market: str
    type: str
    industry_group: str | None
    naver: list[int]
    reasons: list[ThemeMemberReason]
    chg_pct: float | None
    ret_5d: float | None
    ret_20d: float | None
    mktcap: int | None
    label: Literal["hist", "w52", "d120"] | None


class ThemeDetail(ApiModel):
    id: ThemeIdT
    name: str
    section: str
    valuechain: str | None
    built_at: AwareDatetime
    trade_date: date | None
    naver: list[NaverThemeRef]
    members: list[ThemeMember]
    stats: ThemeStats | None

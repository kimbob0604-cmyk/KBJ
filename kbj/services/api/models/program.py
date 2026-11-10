"""프로그램매매 응답 모델(docs/p5_design.md §5, F2 §0·§1.4 — `ProgramDaily`·`ProgramTop`). 모양은
웨이브 1(FND)이 고정, 본문은 F2 묶음(이 파일은 F2 만 고친다 — §1.10). 금액은 원 단위 정수.

- 순위 표는 범위를 숨기지 않는다: `scope_label`('거래대금 상위 N + 관심종목' / '전 종목(마감
  확정)') 필수
  (F2 §1.4 — 검산 ⑤).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, Field

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "ProgramDaily",
    "ProgramDayBar",
    "ProgramIntraday",
    "ProgramMinutePoint",
    "ProgramTop",
    "ProgramTopRow",
]


class ProgramDayBar(ApiModel):
    """하루 막대 — 막대 = 전체(whol) 합계(smtn) 순매수, 막대 위 상세 = 차익·비차익 × 위탁·자기."""

    date: date
    net: int | None  # 원
    arbt_net: int | None
    nabt_net: int | None
    entm_net: int | None
    onsl_net: int | None
    index_close: float | None
    quality: Quality


class ProgramDaily(ApiModel):
    market: Literal["K", "Q"]
    venue: Literal["KRX", "NXT", "TOTAL"]
    days: int
    rows: list[ProgramDayBar]


class ProgramMinutePoint(ApiModel):
    """장중 한 분 — 그 분까지 **그날 누적**(원, metrics §22). 검산 ④ 실패 분은 값 없이 invalid."""

    ts: AwareDatetime
    net: int | None  # 전체 합계 순매수
    arbt_net: int | None
    nabt_net: int | None
    quality: Quality


class ProgramIntraday(ApiModel):
    """`/api/program/intraday?market=` — 장중 시장 프로그램 누적(program.intraday 매분)."""

    market: Literal["K", "Q"]
    venue: Literal["KRX", "NXT", "TOTAL"]
    trade_date: date
    live: bool  # 오늘 장(정규장 + 단일가 수집 끝) 중인가 — 아니면 마지막 분 = 그날 누적 마감 값
    points: list[ProgramMinutePoint]  # 분 오름차순
    last: ProgramMinutePoint | None  # 마지막 유효(검산 통과) 분


class ProgramTopRow(ApiModel):
    rank: int
    code: str
    name: str | None
    price: float | None
    chg_pct: float | None
    net_value: int | None  # 원
    buy_value: int | None
    sell_value: int | None
    quality: Quality


class ProgramTop(ApiModel):
    market: Literal["K", "Q"]
    venue: Literal["KRX", "NXT", "TOTAL"]
    side: Literal["buy", "sell"]
    scope: Literal["intraday", "close"]
    scope_label: str = Field(min_length=1)  # 범위 꼬리표 — 비울 수 없다
    slot: AwareDatetime | None  # 장중 표의 슬롯(마감 표는 None)
    rows: list[ProgramTopRow]

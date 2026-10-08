"""응답 봉투·공용 모델(docs/p3_design.md §5.2).

모든 데이터 응답은 `Envelope[T]` 다 — `source`·`as_of`(시간대 필수)·`quality` 가 빠지면 모델 검증이
실패한다(절대 규칙 1). 금액은 원 단위 정수(엔진의 부동소수 금액은 readers 가 반올림해 넣는다 —
화면은 억·조로만 바꾼다).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from kbj.core.quality import Quality

__all__ = ["ApiModel", "ChipValue", "Envelope", "ErrorBody", "NoDataBody", "SourcedInt"]


class ApiModel(BaseModel):
    """응답 모델 바탕 — 모르는 필드 거부, 바꿀 수 없음."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Envelope[T](BaseModel):
    """응답 봉투 — 원천·기준 시각·품질 + 데이터."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1, pattern=r"\S")  # 예 "KRX", "KRX+KIS", "KIS(잠정)"
    as_of: AwareDatetime  # 데이터가 가리키는 시각(마감 = 그날 15:30 KST, 슬롯 = 슬롯 끝)
    quality: Quality  # 구성 값 중 가장 나쁜 것(invalid 행은 빠지고 notes 에 수)
    notes: list[str] = Field(default_factory=list)  # "NXT 미포함", "검산 불가: …" 등
    generated_at: AwareDatetime  # 응답을 만든 시각
    data: T


class SourcedInt(ApiModel):
    """원천이 붙은 원 단위 정수."""

    value: int
    source: str = Field(min_length=1)
    as_of: AwareDatetime
    quality: Quality


class ChipValue(ApiModel):
    """상단 띠 칩 값(수·문자 — 원천·시각·품질 필수)."""

    value: float | int | str
    source: str = Field(min_length=1)
    as_of: AwareDatetime
    quality: Quality


class NoDataBody(ApiModel):
    """데이터가 아직 없을 때(404) — 화면은 '아직 없음 — <작업> <예정 시각>' 으로 그린다(§6.7)."""

    code: Literal["no_data"] = "no_data"
    message: str


class ErrorBody(ApiModel):
    """오류 응답(401·403·413·415·503) — 사유 코드만, 입력값·내부 문구 없음."""

    code: str
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)

"""응답 봉투·공용 모델(docs/p3_design.md §5.2).

모든 데이터 응답은 `Envelope[T]` 다 — `source`·`as_of`(시간대 필수)·`quality` 가 빠지면 모델 검증이
실패한다(절대 규칙 1). 금액은 원 단위 정수(엔진의 부동소수 금액은 readers 가 반올림해 넣는다 —
화면은 억·조로만 바꾼다).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from kbj.core.quality import Quality

__all__ = [
    "FUTURE_AS_OF_NOTE", "ApiModel", "ChipValue", "Envelope", "ErrorBody", "NoDataBody",
    "SourcedInt",
]  # fmt: skip

FUTURE_AS_OF_NOTE = "기준 시각이 응답 시각보다 뒤라 응답 시각으로 적었다(아직 오지 않은 시각)"


class ApiModel(BaseModel):
    """응답 모델 바탕 — 모르는 필드 거부, 바꿀 수 없음."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Envelope[T](BaseModel):
    """응답 봉투 — 원천·기준 시각·품질 + 데이터."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1, pattern=r"\S")  # 예 "KRX", "KRX+KIS", "KIS(잠정)"
    # 데이터가 가리키는 시각(마감 = 그날 15:30 KST, 슬롯 = 슬롯 끝) — 응답 시각보다 뒤면 응답 시각
    as_of: AwareDatetime
    quality: Quality  # 구성 값 중 가장 나쁜 것(invalid 행은 빠지고 notes 에 수)
    notes: list[str] = Field(default_factory=list)  # "NXT 미포함", "검산 불가: …" 등
    generated_at: AwareDatetime  # 응답을 만든 시각
    data: T

    @model_validator(mode="before")
    @classmethod
    def _not_after_generated(cls, v: Any) -> Any:
        """기준 시각이 응답 시각보다 뒤면(장중 잠정 값을 그날 마감 15:30 으로 적은 것) 응답 시각으로
        당기고 notes 에 적는다 — 화면에 '기준 15:30'(아직 오지 않은 시각)이 뜨던 것(2026-10-21)."""
        if not isinstance(v, dict):
            return v
        d: dict[str, Any] = dict(v)  # pyright: ignore[reportUnknownArgumentType]
        a, g = d.get("as_of"), d.get("generated_at")
        if a is not None and g is not None and hasattr(a, "tzinfo") and hasattr(g, "tzinfo"):
            if a.tzinfo is not None and g.tzinfo is not None and a > g:
                d["as_of"] = g.astimezone(a.tzinfo)  # 원래 시간대(보통 KST)로
                d["notes"] = [*list(d.get("notes") or []), FUTURE_AS_OF_NOTE]
        return d


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

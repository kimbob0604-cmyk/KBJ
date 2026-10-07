"""수치의 출처·기준 시각·품질 (CLAUDE.md 절대 규칙 1).

모든 수치는 `source`·`as_of`·`quality` 를 함께 가진다. `Sourced[T]` 는 그 묶음이다.

- `as_of` 는 시간대가 있는 시각만 받는다(naive 시각은 검증 오류). DB 는 timestamptz 로 저장한다.
- `quality` 는 네 값뿐이다: ok·stale·estimated·invalid.
- 시계는 주입한다 — 이 모듈은 현재 시각을 스스로 읽지 않는다(CLAUDE.md §4).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Quality(StrEnum):
    """값의 품질. 문자열 값이 그대로 DB `quality` 열과 API 응답에 들어간다."""

    OK = "ok"  # 출처에서 제때 받은 값
    STALE = "stale"  # 출처 값이지만 기준 시각이 허용 범위보다 오래됐다
    ESTIMATED = (
        "estimated"  # 출처 값이 아니라 추정·보간·대체 출처로 채웠다(단정하지 않는다 — 규칙 2)
    )
    INVALID = "invalid"  # 검증에 실패했다. 화면·계산에 쓰지 않는다

    @property
    def usable(self) -> bool:
        """계산·표시에 쓸 수 있는가. invalid 만 아니다."""
        return self is not Quality.INVALID


class Sourced[T](BaseModel):
    """출처·기준 시각·품질이 붙은 값 하나. 바꿀 수 없다(frozen)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: T
    source: str = Field(min_length=1, pattern=r"\S")  # 예: "DART", "ECOS:817Y002", "KIS"
    as_of: AwareDatetime  # 값이 가리키는 시각(받은 시각이 아니다). 시간대 필수
    quality: Quality

    @property
    def usable(self) -> bool:
        return self.quality.usable

    def with_quality(self, quality: Quality) -> Sourced[T]:
        """품질만 바꾼 새 값."""
        return self.model_copy(update={"quality": quality})

    def aged(self, now: datetime, max_age: timedelta) -> Sourced[T]:
        """`now - as_of > max_age` 이면 ok 를 stale 로 낮춘 새 값. 다른 품질은 그대로 둔다.

        `now` 는 호출하는 쪽이 주입한다(벽시계 의존 금지). naive 시각이면 TypeError.
        """
        if now.tzinfo is None or now.utcoffset() is None:
            raise TypeError("now 는 시간대가 있는 시각이어야 한다")
        if max_age < timedelta(0):
            raise ValueError("max_age 는 0 이상이어야 한다")
        if self.quality is Quality.OK and now - self.as_of > max_age:
            return self.with_quality(Quality.STALE)
        return self

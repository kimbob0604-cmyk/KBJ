"""저장소 공용 도구 — 연결 공장 형, 숫자 변환, 시각 검사(docs/p3_design.md §1.1).

이 패키지는 `kbj.core`(행 자료형) 와 psycopg 만 쓴다(계약 ⑦ — 어댑터·서비스·엔진을 모른다).
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

import psycopg

# 연결 공장 — 부를 때마다 새 연결(쓰는 쪽이 `with` 로 닫는다).
# 시험은 `lambda: psycopg.connect(dsn)`,
# 서비스는 `lambda: kbj.store.db.connect(settings, service=…)`.
ConnFactory = Callable[[], psycopg.Connection[Any]]

MARKET: Final = "KR"  # P3 저장소는 KR 만 쓴다(표의 market 열 — US 보드는 P5)


def to_float(v: object) -> float | None:
    """numeric(Decimal)·int·float → float. None 은 None."""
    if v is None:
        return None
    if isinstance(v, Decimal | int | float):
        return float(v)
    raise TypeError(f"수가 아니다: {type(v).__name__}")


def to_int(v: object) -> int | None:
    """numeric·bigint → int(원 단위 정수). 소수가 있으면 오류 — 금액을 조용히 자르지 않는다."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise TypeError("bool 은 수가 아니다")
    if isinstance(v, int):
        return v
    if isinstance(v, Decimal):
        if v != v.to_integral_value():
            raise ValueError(f"정수 열에 소수 값: {v}")
        return int(v)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    raise TypeError(f"정수가 아니다: {v!r}")


def require_aware(what: str, ts: datetime | None) -> None:
    if ts is None:
        return
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"{what}: naive datetime 은 받지 않는다")


def require_loaded_by(loaded_by: str) -> None:
    if not loaded_by or not loaded_by.strip():
        raise ValueError("loaded_by(쓴 작업 이름)가 비었다")


def code_filter(codes: Collection[str] | None) -> list[str] | None:
    """`None` = 전 종목, 빈 모음 = 아무 종목도 아님(빈 결과)."""
    return None if codes is None else sorted(set(codes))


def window_start(dates_desc: list[date], n: int) -> date | None:
    """최신순 날짜 목록에서 n 개째(가장 오래된) 날짜. n <= 0 이면 오류."""
    if n <= 0:
        raise ValueError("n 은 1 이상")
    return dates_desc[min(n, len(dates_desc)) - 1] if dates_desc else None

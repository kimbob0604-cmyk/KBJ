"""공개 표 읽기 — 이름 붙은 SQL(`Query`)과 읽기 함수(`Reader`), 읽은 값 다듬기(docs/p4_design.md
§1.9·§7).

- P4 공개 파일(`filings_json`·`fin_json`·`corp_index`)은 `pub_filings`·`pub_fin` 의 SQL 만 읽는다(P3
  계약 ⑧ — 이 패키지는 저장소 `kbj.store.repos`·엔진을 import 하지 않는다. 파생값은 작업이 `pub_*`
  에 미리 쓴다 — D-P4-17). SQL 은 모듈 상수(`Query`)뿐이고 사용자 입력은 매개변수(`%(이름)s`)로만
  들어간다. `tests/unit/public_export/test_sql_schemas.py` 가 그 상수의 스키마가 `pub_` 뿐인지
  정적으로 본다.
- 운영은 `sql_reader(conn)`(공개 내보내기 역할 연결 — 읽기 전용), 단위 시험은 같은 `Query` 이름에
  같은 뜻으로 답하는 메모리 읽기(`tests/unit/public_export/pub_rows.py`)를 넣는다. 두 쪽이 같은 답을
  내는지는 Postgres 대조로 확인한다(통합 — 묶음 S 요청).
- 행은 열 이름 → 값 dict. Postgres 의 numeric 은 `Decimal` 로 오므로 `num()`(실수)·`won()`(원
  정수)으로 바꾸고, 날짜·시각은 `day()`·`iso_kst()` 로 문자열로 만든다. 반올림하지
  않는다(docs/metrics.md §0 — 반올림은 화면에서만).
- jsonb 객체는 Postgres 가 키 순서를 제 규칙(길이 → 바이트)으로 바꿔 돌려준다. 파일 글자가 원천
  (Postgres·메모리 읽기)에 따라 달라지지 않게 jsonb 에서 온 객체는 `canon()` 으로 키를 정렬해
  싣는다(같은 표 → 같은 바이트 — 요약 해시·픽스처 신선도 시험의 전제).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, LiteralString

import psycopg
from psycopg.rows import dict_row

from kbj.services.public_export.manifest import iso_kst

__all__ = [
    "DART",
    "Query",
    "Reader",
    "Row",
    "canon",
    "day",
    "num",
    "sql_reader",
    "stamp",
    "won",
]

DART: Final = "DART"
Row = dict[str, Any]


@dataclass(frozen=True)
class Query:
    """이름 붙은 SQL 한 개. `sql` 은 리터럴 문자열(모듈 상수)만 — 매개변수는 `%(이름)s`."""

    name: str
    sql: LiteralString


Reader = Callable[[Query, Mapping[str, object]], list[Row]]


def sql_reader(conn: psycopg.Connection[Any]) -> Reader:
    """공개 내보내기 연결로 `Query` 를 돌려 dict 행 목록을 돌려주는 읽기 함수."""

    def read(q: Query, params: Mapping[str, object]) -> list[Row]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(q.sql, dict(params))
            return list(cur.fetchall())

    return read


def num(v: object) -> float | None:
    """numeric·실수 → 유한한 float. 없거나 NaN·inf 면 None(JSON 에 NaN 을 넣지 않는다)."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise TypeError("bool 은 수가 아니다")
    if isinstance(v, int | float | Decimal):
        x = float(v)
        return x if math.isfinite(x) else None
    raise TypeError(f"수가 아니다: {type(v).__name__}")


def won(v: object) -> int | None:
    """원·주 정수(bigint) — Decimal 로 와도 정수여야 한다(소수면 오류 — 지어내지 않는다)."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise TypeError("bool 은 수가 아니다")
    if isinstance(v, int):
        return v
    if isinstance(v, Decimal | float):
        if v != int(v):
            raise ValueError("정수가 아닌 원·주 값")
        return int(v)
    raise TypeError(f"정수가 아니다: {type(v).__name__}")


def canon(v: object) -> Any:
    """jsonb 값 → 객체 키를 (깊이 무관) 정렬한 같은 값. 목록 순서·값은 그대로."""
    if isinstance(v, Mapping):
        return {str(k): canon(v[k]) for k in sorted(v, key=str)}  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    if isinstance(v, list | tuple):
        return [canon(x) for x in v]  # pyright: ignore[reportUnknownVariableType]
    return v


def day(v: object) -> str | None:
    """date → 'YYYY-MM-DD'. datetime 은 받지 않는다(날짜 칸에 시각이 섞이지 않게)."""
    if v is None:
        return None
    if isinstance(v, datetime) or not isinstance(v, date):
        raise TypeError("날짜(date) 칸이 아니다")
    return v.isoformat()


def stamp(v: object) -> str | None:
    """aware datetime → KST ISO(초). naive 는 오류."""
    if v is None:
        return None
    if not isinstance(v, datetime):
        raise TypeError("시각(datetime) 칸이 아니다")
    return iso_kst(v)

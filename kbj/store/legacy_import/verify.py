"""이관 검증 — 행 수·키 다이제스트·값 합(docs/p2_design.md §8.7).

- 행 수: `후보 − 버림 = 대상에서 이 원본에 해당하는 행 수`. '해당'은 이 배치의 키를 가진 대상 행 중
  원본 표시 열(`loaded_by`)이 이 매핑인 것(표시 열이 없는 표는 키가 있는 행).
- 키 다이제스트: 키 열을 `|` 로 이은 문자열(날짜는 ISO, 정수는 10진)을 바이트 순으로 정렬해 `,` 로
  이은 것의 md5. 파이썬(원본 쪽)과 SQL(`md5(string_agg(… ORDER BY k COLLATE "C"))` — 대상 쪽)이 같은
  정규화를 쓴다. 다이제스트는 내용 확인용이다(보안 용도 아님).
- 값 합: 숫자 열 합을 허용오차로 비교한다(CLAUDE.md §4 — 바이트 비교 금지).
- 보고에는 값·키를 싣지 않는다 — 수·다이제스트·합만.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from kbj.store.legacy_import.mappings import Target

REL_TOL = Decimal("1e-9")
ABS_TOL = Decimal("1e-6")


def key_part(v: object) -> str:
    if isinstance(v, datetime):
        raise TypeError("시각은 키로 쓰지 않는다")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool) or v is None:
        raise TypeError("키 값은 문자열·정수·날짜")
    return str(v)


def key_text(row: Mapping[str, object], key_cols: Sequence[str]) -> str:
    return "|".join(key_part(row[c]) for c in key_cols)


def digest_of(texts: Iterable[str]) -> str:
    """정렬(UTF-8 바이트 순 = 코드포인트 순)한 키 문자열을 ',' 로 이은 md5."""
    joined = ",".join(sorted(texts))
    return hashlib.md5(joined.encode("utf-8"), usedforsecurity=False).hexdigest()


def key_digest(rows: Iterable[Mapping[str, object]], key_cols: Sequence[str]) -> str:
    return digest_of(key_text(r, key_cols) for r in rows)


def column_sums(rows: Iterable[Mapping[str, object]], cols: Sequence[str]) -> dict[str, Decimal]:
    out = dict.fromkeys(cols, Decimal(0))
    for r in rows:
        for c in cols:
            v = r.get(c)
            if v is None:
                continue
            if isinstance(v, bool):
                raise TypeError(f"{c}: 합을 낼 수 없는 값")
            if isinstance(v, int | Decimal):
                out[c] += Decimal(v)
            else:
                raise TypeError(f"{c}: 합을 낼 수 없는 값")
    return out


def close_enough(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= max(ABS_TOL, REL_TOL * max(abs(a), abs(b)))


@dataclass(frozen=True)
class Observed:
    """대상 쪽에서 센 것(이 배치의 키·원본 표시로 거른 행)."""

    count: int
    digest: str
    sums: dict[str, Decimal] = field(default_factory=dict[str, Decimal])


@dataclass(frozen=True)
class VerifyReport:
    expected_count: int
    observed_count: int
    digest_src: str
    digest_dst: str
    sums: dict[str, tuple[Decimal, Decimal]]  # 열 → (원본, 대상)

    @property
    def count_ok(self) -> bool:
        return self.expected_count == self.observed_count

    @property
    def digest_ok(self) -> bool:
        return self.digest_src == self.digest_dst

    @property
    def sums_ok(self) -> bool:
        return all(close_enough(a, b) for a, b in self.sums.values())

    @property
    def ok(self) -> bool:
        return self.count_ok and self.digest_ok and self.sums_ok

    def problems(self) -> list[str]:
        out: list[str] = []
        if not self.count_ok:
            out.append(f"행 수 {self.expected_count} ≠ {self.observed_count}")
        if not self.digest_ok:
            out.append("키 다이제스트 불일치")
        out += [f"합 불일치: {c}" for c, (a, b) in self.sums.items() if not close_enough(a, b)]
        return out


def verify(
    target: Target, rows: Sequence[Mapping[str, object]], observed: Observed
) -> VerifyReport:
    """원본 쪽(넣으려 한 행)과 대상 쪽(`observed`)을 맞춰 본다."""
    src_sums = column_sums(rows, target.sums)
    return VerifyReport(
        expected_count=len(rows),
        observed_count=observed.count,
        digest_src=key_digest(rows, target.key),
        digest_dst=observed.digest,
        sums={c: (src_sums[c], observed.sums.get(c, Decimal(0))) for c in target.sums},
    )

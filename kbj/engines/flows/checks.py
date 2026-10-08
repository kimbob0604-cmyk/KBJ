"""검산 ①② — 원장 행마다.

근거: docs/metrics.md §2, docs/p3_design.md §4.2, ET `monitor/flow/analyze.py:reconcile`.

- ① 한 종목·하루의 외국인 + 기관 + 기타법인 + 개인 = 0(장내 순매수 합). **4구분이 모두 있을 때만**
  한다. 하나라도 없으면 `None`(검산 불가) — 0 으로 바꾸지 않는다(메인 결정 R2: KIS 종목별 TR 이
  기타법인을 주지 않으면 실데이터에서는 ① 이 불가하고, 사유를 notes 에 남긴다).
- ② 기관 7구분 합 = 기관 합계. 7구분(또는 기관 합계)이 없으면 `None`.
- 금액은 원 단위 정수라 반올림 오차가 없다 — 잔차가 0 이 아니면 실패(ET reconcile 과 같은 기준).
  실패한 행은 invalid 로 바꾸고 집계에서 빠진다(metrics §5). 이미 invalid 인 행은 검산하지 않는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final, Literal

from kbj.core.rows import LedgerCheck
from kbj.engines.flows.ledger import WHO, WHO_NAMES, Ledger, LedgerRow

__all__ = [
    "CheckFailure",
    "CheckId",
    "CheckSummary",
    "apply_checks",
    "check1",
    "check1_reason",
    "check2",
    "check2_reason",
    "ledger_checks",
]

CheckId = Literal["c1", "c2"]
_LABEL: Final[Mapping[str, str]] = {"c1": "검산 ①(4구분 합 0)", "c2": "검산 ②(7구분 합 = 기관)"}
NO_INVESTORS: Final = "투자자별 행 없음"


def check1(row: LedgerRow) -> int | None:
    """외국인 + 기관 + 기타법인 + 개인. 하나라도 없으면 None(검산 불가)."""
    vals = [row.value(w) for w in WHO]
    if any(v is None for v in vals):
        return None
    return sum(v for v in vals if v is not None)


def check1_reason(row: LedgerRow) -> str | None:
    """① 이 불가한 사유(가능하면 None) — 예 "기타법인 미제공"."""
    missing = [WHO_NAMES[w] for w in WHO if row.value(w) is None]
    if not missing:
        return None
    if len(missing) == len(WHO):
        return NO_INVESTORS
    return "·".join(missing) + " 미제공"


def check2(row: LedgerRow) -> int | None:
    """7구분 합 − 기관 합계. 7구분이나 기관 합계가 없으면 None(패널을 숨긴다 — metrics §2)."""
    if row.inst7 is None or row.inst is None:
        return None
    return sum(row.inst7.values()) - row.inst


def check2_reason(row: LedgerRow) -> str | None:
    if row.inst7 is None and row.inst is None:
        return "기관 합계·7구분 미제공"
    if row.inst7 is None:
        return "기관 7구분 미제공"
    if row.inst is None:
        return "기관 합계 미제공"
    return None


_CHECKS: Final[
    tuple[tuple[CheckId, Callable[[LedgerRow], int | None], Callable[[LedgerRow], str | None]], ...]
] = (("c1", check1, check1_reason), ("c2", check2, check2_reason))


@dataclass(frozen=True)
class CheckFailure:
    """검산 실패 한 건(잔차 ≠ 0). 그 행은 invalid 가 된다."""

    code: str
    date: date
    check_id: CheckId
    residual: int
    detail: Mapping[str, int | None] = field(default_factory=dict[str, int | None])


@dataclass(frozen=True)
class CheckSummary:
    """검산 집계 — 건수만(값 없음). `notes()` 가 응답·문서에 실을 문장."""

    checked: Mapping[str, int]
    failed: Mapping[str, int]
    unavailable: Mapping[str, int]
    reasons: Mapping[str, Mapping[str, int]]  # check_id → 사유 → 행 수
    skipped_invalid: int  # 이미 invalid 라 검산하지 않은 행

    def notes(self) -> tuple[str, ...]:
        out: list[str] = []
        for cid in ("c1", "c2"):
            if self.failed.get(cid):
                out.append(f"{_LABEL[cid]} 실패 {self.failed[cid]}행 — invalid 로 집계에서 뺐다")
            if self.unavailable.get(cid):
                why = ", ".join(f"{k} {v}" for k, v in sorted(self.reasons.get(cid, {}).items()))
                out.append(f"{_LABEL[cid]} 불가 {self.unavailable[cid]}행: {why}")
        if self.skipped_invalid:
            out.append(f"invalid {self.skipped_invalid}행 제외(검산 전)")
        return tuple(out)


def apply_checks(ledger: Ledger) -> tuple[Ledger, list[CheckFailure]]:
    """원장 전체에 ①② — 실패 행은 invalid, 집계(`CheckSummary`)는 원장 `checks`·`notes` 에."""
    checked: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    unavailable: Counter[str] = Counter()
    reasons: dict[str, Counter[str]] = {"c1": Counter(), "c2": Counter()}
    failures: list[CheckFailure] = []
    changed: list[LedgerRow] = []
    skipped = 0
    for key in sorted(ledger.rows):
        row = ledger.rows[key]
        if not row.usable:
            skipped += 1
            continue
        bad: list[str] = []
        bad_ids: list[CheckId] = []
        for cid, fn, why in _CHECKS:
            res = fn(row)
            if res is None:
                unavailable[cid] += 1
                reasons[cid][why(row) or "모름"] += 1
                continue
            checked[cid] += 1
            if res != 0:
                failed[cid] += 1
                detail: dict[str, int | None] = {w: row.value(w) for w in WHO}
                failures.append(CheckFailure(row.code, row.date, cid, res, detail))
                bad.append(f"{_LABEL[cid]} 잔차 {res}")
                bad_ids.append(cid)
        if bad:
            changed.append(row.invalidated("; ".join(bad), failed_checks=bad_ids))
    summary = CheckSummary(
        checked=dict(checked),
        failed=dict(failed),
        unavailable=dict(unavailable),
        reasons={k: dict(v) for k, v in reasons.items() if v},
        skipped_invalid=skipped,
    )
    return ledger.with_rows(changed, notes=summary.notes(), checks=summary), failures


def ledger_checks(failures: Iterable[CheckFailure], checked_at: datetime) -> list[LedgerCheck]:
    """저장 행(`prv_flows.ledger_check`, domain=stock)으로 — `FlowsRepo.put_checks` 에 넘긴다."""
    return [
        LedgerCheck(
            domain="stock",
            trade_date=f.date,
            code=f.code,
            check_id=f.check_id,
            residual=float(f.residual),
            checked_at=checked_at,
            detail=dict(f.detail),
        )
        for f in failures
    ]

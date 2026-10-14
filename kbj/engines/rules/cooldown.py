"""쿨다운 상태기 — 실시간·백테스트 공용(P8 묶음 FND, D-P8-10, metrics §30.3).

- 같은 (규칙, 종목)의 마지막 **허락된** 신호(`sent`·`suppressed`·`dry` — `ALLOWED_SIGNAL_STATUSES`)
  시각부터 `cooldown_min` 분이 지나야 허락한다(미끄러지는 창, 경계 포함). 판 번호는 보지 않는다(같은
  규칙이면 새 판도 같은 쿨다운).
- `both` 모드는 부르는 쪽이 장중·마감 신호를 함께 넘긴다(같은 쿨다운을 나눈다).
- 판정 시각 `ts` 보다 늦은 신호는 보지 않는다(미래참조 없음 — 백테스트가 시점을 하나씩 넘길 때 같은
  결과). 막힌 시점도 부르는 쪽이 `status='cooldown'` 으로 기록한다.
- notifier 의 `alert.rule` 쿨다운(3600초, subject `<rule_id>:<code>`)은 안전망 — 여기와 별개(ADR
  0005).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from kbj.core.rules_rows import ALLOWED_SIGNAL_STATUSES

__all__ = ["CooldownBook", "CooldownDecision", "PriorSignal", "allow", "last_allowed"]


class PriorSignal(Protocol):
    @property
    def rule_id(self) -> str: ...
    @property
    def code(self) -> str: ...
    @property
    def eval_ts(self) -> datetime: ...
    @property
    def status(self) -> str: ...


@dataclass(frozen=True)
class CooldownDecision:
    allowed: bool
    last_ts: datetime | None  # 마지막 허락된 신호 시각(없으면 None)
    until: datetime | None  # 막혔으면 풀리는 시각


def _aware(ts: datetime) -> None:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime 은 받지 않는다")


def last_allowed(
    prior: Iterable[PriorSignal], *, rule_id: str, code: str, before: datetime
) -> datetime | None:
    """`before` 보다 이른, 같은 (규칙, 종목)의 마지막 허락된 신호 시각."""
    _aware(before)
    best: datetime | None = None
    for s in prior:
        if s.rule_id != rule_id or s.code != code or s.status not in ALLOWED_SIGNAL_STATUSES:
            continue
        if s.eval_ts >= before:
            continue
        if best is None or s.eval_ts > best:
            best = s.eval_ts
    return best


def allow(
    prior: Iterable[PriorSignal], *, rule_id: str, code: str, ts: datetime, cooldown_min: int
) -> CooldownDecision:
    """`ts` 에 신호를 허락하는가."""
    if cooldown_min < 1:
        raise ValueError(f"cooldown_min 은 1 이상: {cooldown_min}")
    last = last_allowed(prior, rule_id=rule_id, code=code, before=ts)
    if last is None:
        return CooldownDecision(True, None, None)
    until = last + timedelta(minutes=cooldown_min)
    if ts >= until:
        return CooldownDecision(True, last, None)
    return CooldownDecision(False, last, until)


@dataclass
class _Rec:
    rule_id: str
    code: str
    eval_ts: datetime
    status: str


class CooldownBook:
    """시점을 앞으로만 넘기는 루프(백테스트·장중 재생)용 — 판정은 `allow` 를 그대로 부른다."""

    def __init__(self, prior: Iterable[PriorSignal] = ()) -> None:
        self._recs: list[_Rec] = [_Rec(s.rule_id, s.code, s.eval_ts, s.status) for s in prior]

    def decide(
        self, *, rule_id: str, code: str, ts: datetime, cooldown_min: int
    ) -> CooldownDecision:
        return allow(self._recs, rule_id=rule_id, code=code, ts=ts, cooldown_min=cooldown_min)

    def record(self, *, rule_id: str, code: str, ts: datetime, status: str) -> None:
        _aware(ts)
        self._recs.append(_Rec(rule_id, code, ts, status))

    def step(
        self,
        *,
        rule_id: str,
        code: str,
        ts: datetime,
        cooldown_min: int,
        allowed_status: str = "dry",
    ) -> CooldownDecision:
        """판정하고 기록까지(허락 → allowed_status, 막힘 → cooldown)."""
        d = self.decide(rule_id=rule_id, code=code, ts=ts, cooldown_min=cooldown_min)
        self.record(
            rule_id=rule_id, code=code, ts=ts, status=allowed_status if d.allowed else "cooldown"
        )
        return d

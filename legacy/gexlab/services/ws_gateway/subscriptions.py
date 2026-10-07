"""웹소켓 구독 계획·ATM 회전·구독 변경 순서 (PLAN §4.3, docs/phase1_design.md §4).

- 세션별 TR: 주간 선물 `H0IFCNT0`·옵션 `H0IOCNT0`, 야간 선물 `H0MFCNT0`·옵션 `H0EUCNT0`
- 옵션은 최근접 만기 ATM±atm_range 행사가의 콜·풋. ATM 위치는 호출하는 쪽이 선물가로
  정해 넘긴다(`core.chain` — 전광판 ATM 표시는 쓰지 않는다, probe_results #11a)
- 체결통보 자리는 예산에만 잡아 두고 여기서는 구독하지 않는다(Phase 8·9)
- 회전은 새 ATM 이 기존 중심에서 2행사가 이상 움직였을 때만, 해지 먼저·등록 나중
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Literal

from services.ws_gateway.budget import Budget

Session = Literal["day", "night"]
Action = Literal["unsubscribe", "subscribe"]

TR_IDS: dict[Session, dict[str, str]] = {
    "day": {"futures": "H0IFCNT0", "options": "H0IOCNT0"},
    "night": {"futures": "H0MFCNT0", "options": "H0EUCNT0"},
}
ROTATE_MIN_STRIKES = 2  # PLAN §4.3: 2행사가 이상 차이 날 때만 회전


@dataclass(frozen=True, order=True)
class Subscription:
    tr_id: str
    key: str  # 종목 단축코드


@dataclass(frozen=True)
class StrikeCodes:
    strike: Decimal
    call: str
    put: str


def plan(
    budget: Budget, futures_code: str, chain: Sequence[StrikeCodes], atm_index: int
) -> frozenset[Subscription]:
    """원하는 구독 집합. chain 은 최근접 만기 행사가 오름차순, atm_index 는 그 안의 ATM 위치.

    체인 끝에 걸리면 ATM 쪽 행사가만 남기고 잘라 낸다(개수를 채우려고 창을 밀지 않는다 —
    ATM 을 가운데 두는 것이 목적이다). 남는 건 여유로 둔다.
    """
    if not chain:
        raise ValueError("체인이 비었다")
    if not 0 <= atm_index < len(chain):
        raise ValueError(f"atm_index {atm_index} 가 체인 밖이다")
    if any(a.strike >= b.strike for a, b in pairwise(chain)):
        raise ValueError("체인은 행사가 오름차순이어야 한다")
    tr = TR_IDS[budget.session]
    subs: set[Subscription] = set()
    if budget.futures:
        subs.add(Subscription(tr["futures"], futures_code))
    r = budget.atm_range
    for sc in chain[max(0, atm_index - r) : atm_index + r + 1]:
        subs.add(Subscription(tr["options"], sc.call))
        subs.add(Subscription(tr["options"], sc.put))
    if len(subs) > budget.futures + budget.options:
        raise ValueError(f"구독 {len(subs)}건이 예산 {budget.futures + budget.options}건을 넘는다")
    return frozenset(subs)


def should_rotate(
    current_center: Decimal | None, new_center: Decimal, chain: Sequence[StrikeCodes]
) -> bool:
    """새 ATM 이 기존 구독 중심에서 ROTATE_MIN_STRIKES 행사가 이상 움직였는가.

    기존 중심이 없거나 체인에 없으면(만기 전환 등) 새로 짜야 하므로 True.
    """
    strikes = [sc.strike for sc in chain]
    if current_center is None or current_center not in strikes or new_center not in strikes:
        return True
    return abs(strikes.index(new_center) - strikes.index(current_center)) >= ROTATE_MIN_STRIKES


def changes(
    current: frozenset[Subscription], desired: frozenset[Subscription]
) -> list[tuple[Action, Subscription]]:
    """구독 변경 순서: 해지를 모두 먼저, 그다음 등록 — 도중에도 한도를 넘지 않는다."""
    unsub: list[tuple[Action, Subscription]] = [
        ("unsubscribe", s) for s in sorted(current - desired)
    ]
    sub: list[tuple[Action, Subscription]] = [("subscribe", s) for s in sorted(desired - current)]
    return [*unsub, *sub]

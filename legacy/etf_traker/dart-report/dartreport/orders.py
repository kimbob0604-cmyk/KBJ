"""수주(단일판매·공급계약체결) 공시 수집·파싱 — KBJ P4: KBJ 정본을 다시 내보낸다(두 벌 금지).

정본은 `kbj.engines.financials.orders`(함수 본문 그대로 — docs/p4_design.md D-P4-6 ④). legacy 동작
그대로: 정정공시를 거르지 않는다(원 계약과 정정이 두 행 — MIGRATION.md 'legacy 버그 목록').
"""

from kbj.engines.financials.orders import (
    CONTRACT_PATTERNS,
    DATE_RE,
    EOK,
    FIELD_ALIASES,
    Order,
    fetch_orders,
    orders_table,
)

from .client import DartClient

__all__ = [
    "CONTRACT_PATTERNS",
    "DATE_RE",
    "EOK",
    "FIELD_ALIASES",
    "DartClient",
    "Order",
    "fetch_orders",
    "orders_table",
]

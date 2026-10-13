"""재무제표 수집 및 누적→분기 환산 — KBJ P4: KBJ 정본을 다시 내보낸다(두 벌 금지).

정본은 `kbj.engines.financials.statements`(이 파일의 함수 본문을 그대로 옮겼다 — docs/p4_design.md
D-P4-6 ④). 같은 객체를 내보내므로 `dartreport.statements.to_quarterly is kbj…to_quarterly`
(tests_bridge.py). 바뀐 것은 `fetch_periods` 가 `DartClient` 대신 `financials(...)` 를 가진 아무
객체나 받는다는 것뿐이다(legacy DartClient 그대로 맞다). 이식 전 동작은 골든이 고정한다
(tests/golden/dart_report — legacy 커밋 607b761 캡처). 비공개 도우미(`_pick` 등)는 다시 내보내지
않는다 — legacy 안에서 쓰는 곳이 없고, legacy 는 kbj 비공개 이름을 가져오지 않는다.
"""

from kbj.core.fin_rows import REPRT
from kbj.engines.financials.statements import (
    BS_KEYS,
    EOK,
    IS_DIV,
    TAGS,
    Period,
    fetch_periods,
    to_annual,
    to_quarterly,
)

from .client import DartClient

__all__ = [
    "BS_KEYS",
    "EOK",
    "IS_DIV",
    "REPRT",
    "TAGS",
    "DartClient",
    "Period",
    "fetch_periods",
    "to_annual",
    "to_quarterly",
]

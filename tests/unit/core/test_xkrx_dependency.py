"""캘린더가 기대는 외부 라이브러리 동작 고정 — exchange_calendars XKRX.

GEXLAB `tests/unit/test_dependencies.py:test_xkrx_knows_2026_holidays`(43a9ed1, 7경우) 승격 — 시험
본문은 그대로다(docs/p2_design.md §1.3). 같은 파일의 vollib·psycopg·websockets 시험은 GX 엔진·수집
쪽이라 P7 까지 legacy 에 남는다.

- exchange_calendars XKRX: 휴장일 원천(GX PLAN §2.2). 2026 추석·대체공휴일이 들어 있어야 한다
"""

import exchange_calendars as xcals
import pandas as pd
import pytest


@pytest.mark.parametrize(
    ("day", "is_session"),
    [
        ("2026-09-23", True),  # 추석 연휴 전날(수)
        ("2026-09-24", False),  # 추석 전날
        ("2026-09-25", False),  # 추석
        ("2026-09-28", True),
        ("2026-10-05", False),  # 개천절(토) 대체공휴일
        ("2026-10-09", False),  # 한글날
        ("2026-12-31", False),  # 연말 휴장
    ],
)
def test_xkrx_knows_2026_holidays(day: str, is_session: bool) -> None:
    cal = xcals.get_calendar("XKRX")
    assert bool(cal.is_session(pd.Timestamp(day))) is is_session

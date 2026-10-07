"""Phase 1·2 가 기대는 외부 라이브러리의 동작을 고정한다.

- exchange_calendars XKRX: 휴장일 원천(PLAN §2.2). 2026 추석·대체공휴일이 들어 있어야 한다
- vollib: Black-76 가격·IV 역산(PLAN §5.1 의 py_vollib 후속 패키지, docs/metrics.md §0)
- psycopg 3: TimescaleDB 저장(PLAN §4.5), websockets: KIS 웹소켓(ws-gateway)
"""

import math

from vollib.black import black
from vollib.black.implied_volatility import implied_volatility


# KBJ P2: test_xkrx_knows_2026_holidays 는 kbj tests/unit/core/test_xkrx_dependency.py 로 승격했다(같은 단언이 kbj 쪽에서 돈다 — MIGRATION.md P2).


def test_vollib_black76_round_trip() -> None:
    f, k, t, r, sigma = 1100.0, 1100.0, 10 / 365, 0.0, 0.25
    price = float(black("c", f, k, t, r, sigma))
    assert math.isclose(float(implied_volatility(price, f, k, r, t, "c")), sigma, rel_tol=1e-9)


def test_db_and_websocket_clients_import() -> None:
    from importlib.metadata import version

    import psycopg
    import websockets.asyncio.client

    assert int(psycopg.__version__.split(".")[0]) >= 3
    assert int(version("websockets").split(".")[0]) >= 13
    assert callable(websockets.asyncio.client.connect)

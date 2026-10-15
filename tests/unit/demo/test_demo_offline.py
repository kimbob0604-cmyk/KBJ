"""데모는 외부 연결 0 — 합성 세계 적재와 페이지별 대표 API 가 루프백 밖으로 나가지 않는다.

`socket.socket.connect`·`socket.create_connection`·`socket.getaddrinfo` 를 감싸 루프백(·유닉스
소켓) 밖이면 기록하고 실패시킨다. 데모 세계(`demo_server.build_demo_world` — api_world + p5
매크로 + p6 + p7 진단·GEX 엔진 사이클 + p8 규칙·백테스트·일지)를 그 안에서 만들고, 데모
설정(`demo_settings`)의 앱에 TestClient 로 페이지 1~12 의 대표 API 를 부른다
(docs/p9_design.md §2.5, ADR 0034). 적재가 약 2분이라 모듈에서 한 번만 만든다.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import demo_server as ds

PW = "demo-offline-test-pw"
ATTEMPTS: list[str] = []
_LOCAL_NAMES = {"localhost", "testserver", "", None}

# (경로, 기대 상태) — 200 은 합성 데이터가 차야 하는 자리, None 은 200 또는 404 no_data(안내 문구)
ROUTES: tuple[tuple[str, int | None], ...] = (
    ("/api/market/summary", 200),  # 1 시장
    ("/api/market/ribbon", 200),
    ("/api/market/sectors", 200),
    ("/api/board/newhigh", 200),  # 2 신고가 보드
    ("/api/board/sectors", 200),
    ("/api/diag/summary", 200),  # 3 진단·GEX
    ("/api/gex/levels", 200),
    ("/api/gex/profile", 200),
    ("/api/flows/investors", 200),  # 4 수급
    ("/api/filings/feed", None),  # 5 일정·공시
    ("/api/macro/tabs", 200),  # 6 매크로(P5 합성 세계를 데모 날짜로)
    ("/api/macro/series?tab=rates", 200),
    ("/api/macro/calendar", 200),
    ("/api/trade/flash", 200),  # 7 수출입
    ("/api/trade/items", 200),
    ("/api/stock/search?q=0", None),  # 8 종목 상세
    ("/api/themes", 200),  # 9 테마·밸류체인(ADR 0039 — demo_server._load_p9 합성 테마 마스터)
    ("/api/themes/strength", 200),
    ("/api/themes/syn_1_1", 200),
    ("/api/etf/flows", 200),  # 10 ETF
    ("/api/etf/types", 200),
    ("/api/rules", 200),  # 11 규칙 알림
    ("/api/rules/signals", 200),
    ("/api/backtest/runs", 200),  # 12 백테스트·저널
    ("/api/journal/trades", 200),
    ("/api/portfolio", None),
    ("/api/watchlist", 200),
    ("/api/ops/status", 200),  # 13 운영(P9 WRAP — demo_server.attach_ops 합성 1주)
    ("/api/ops/jobs", 200),
    ("/api/ops/notify", 200),
)


def _is_local(addr: object) -> bool:
    if isinstance(addr, (str, bytes)):  # AF_UNIX 경로
        return True
    if isinstance(addr, tuple) and addr:
        host = addr[0]
        if host in _LOCAL_NAMES:
            return True
        try:
            return ipaddress.ip_address(str(host)).is_loopback
        except ValueError:
            return False
    return False


@pytest.fixture(scope="module")
def demo() -> Iterator[tuple[Any, TestClient, TestClient]]:
    real_connect = socket.socket.connect
    real_create = socket.create_connection
    real_gai = socket.getaddrinfo

    def connect(self: socket.socket, address: Any) -> Any:
        if not _is_local(address):
            ATTEMPTS.append(f"connect {address!r}")
            raise OSError("데모 시험: 루프백 밖 연결 금지")
        return real_connect(self, address)

    def create_connection(address: Any, *a: Any, **k: Any) -> socket.socket:
        if not _is_local(address):
            ATTEMPTS.append(f"create_connection {address!r}")
            raise OSError("데모 시험: 루프백 밖 연결 금지")
        return real_create(address, *a, **k)

    def getaddrinfo(host: Any, *a: Any, **k: Any) -> Any:
        if not _is_local((host,)):
            ATTEMPTS.append(f"getaddrinfo {host!r}")
            raise OSError("데모 시험: 외부 이름 풀이 금지")
        return real_gai(host, *a, **k)

    mp = pytest.MonkeyPatch()
    mp.setattr(socket.socket, "connect", connect)
    mp.setattr(socket, "create_connection", create_connection)
    mp.setattr(socket, "getaddrinfo", getaddrinfo)
    try:
        world = ds.build_demo_world("close")
        app = ds.demo_app(world, ds.demo_settings(PW, 8765), static_dir=False)
        loop = TestClient(app, base_url="http://127.0.0.1:8765")
        local = TestClient(app, base_url="http://localhost:8765")
        yield world, loop, local
    finally:
        mp.undo()


def _login(c: TestClient, origin: str) -> str:
    r = c.post(
        "/api/auth/login", json={"username": "demo", "password": PW}, headers={"Origin": origin}
    )
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def test_pages_answer_from_synthetic_world_without_network(
    demo: tuple[Any, TestClient, TestClient],
) -> None:
    _world, c, _ = demo
    _login(c, "http://127.0.0.1:8765")
    bad: list[str] = []
    for path, want in ROUTES:
        r = c.get(path)
        if want is None:
            ok = r.status_code == 200 or (
                r.status_code == 404 and r.json().get("code") == "no_data"
            )
        else:
            ok = r.status_code == want
        if not ok:
            bad.append(f"{path} → {r.status_code}")
        elif r.status_code == 200:
            body = r.json()
            if not (body.get("source") and body.get("as_of") and body.get("quality")):
                bad.append(f"{path}: source·as_of·quality 없음")
    assert bad == []
    assert ATTEMPTS == [], ATTEMPTS  # 적재·호출 내내 루프백 밖 연결 시도 0


def test_macro_moved_to_demo_dates(demo: tuple[Any, TestClient, TestClient]) -> None:
    """페이지 6 — p5 합성 매크로가 데모 시계보다 미래 날짜가 아니다(옮긴 결과)."""
    world, c, _ = demo
    _login(c, "http://127.0.0.1:8765")
    r = c.get("/api/macro/series?tab=rates")
    assert r.status_code == 200
    today = world.clock().date().isoformat()
    periods = [
        p["period"]
        for s in r.json()["data"]["series"]
        for p in s.get("points", [])
        if "period" in p
    ]
    assert periods and max(x for x in periods if len(x) == 10) <= today


def test_localhost_origin_also_logs_in_and_writes(demo: tuple[Any, TestClient, TestClient]) -> None:
    """공개 주소 없음 → Origin 을 요청 자신의 출처와 비교.

    localhost 로 열어도 로그인·쓰기가 된다."""
    _world, _, local = demo
    csrf = _login(local, "http://localhost:8765")
    h = {"Origin": "http://localhost:8765", "X-KBJ-CSRF": csrf}
    r = local.put("/api/watchlist/main/990001", headers=h)
    assert r.status_code in (200, 201), r.text
    r = local.delete("/api/watchlist/main/990001", headers=h)
    assert r.status_code == 200, r.text
    # 다른 출처는 여전히 거절(CSRF Origin 검사 유지)
    evil = local.post(
        "/api/auth/login",
        json={"username": "demo", "password": PW},
        headers={"Origin": "http://evil.example"},
    )
    assert evil.status_code == 403


@pytest.mark.parametrize(
    ("addr", "local"),
    [
        (("127.0.0.1", 8765), True),
        (("::1", 6379, 0, 0), True),
        (("localhost", 80), True),
        ("/run/redis/redis.sock", True),
        (("10.0.0.5", 443), False),
        (("api.example.com", 443), False),
        ((ds.ANY_ADDR, 80), False),
    ],
)
def test_guard_classifies_addresses(addr: object, local: bool) -> None:
    """감시가 실제로 막는지(빈 기록으로 통과하지 않게) — 루프백·유닉스 소켓만 통과."""
    assert _is_local(addr) is local

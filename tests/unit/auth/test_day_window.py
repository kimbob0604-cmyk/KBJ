"""하루(24시간 창) 동안 auth 의 발급 시각 — 메인 결정 D3 의 단언을 auth 단위에서 먼저 고정한다.

창은 2026-10-06 05:00 ~ 10-07 05:00 KST(하루 시뮬레이션 `tests/sim` 과 같은 창 — 묶음 I). 빈 Redis
에서 30초마다 step 하면:
- 접근토큰(수명 24시간, 만료 60분 전 갱신): 05:00 첫 발급, 다음 날 04:00 갱신 — 창 안 2회, 간격
  23시간 이라 **어떤 23시간 구간(반열린)에도 1회 이하**.
- 웹소켓 접속키(수명 12시간 가정, 11시간마다 갱신): 05:00·16:00·03:00 — 3회.
- 모든 발급 요청은 auth 의 발급자에서 나온다(가짜 KIS 가 받은 POST 가 전부 auth 것).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import fakeredis
import httpx
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.data.private.kis.credentials import KisCredentials
from kbj.data.private.kis.token import reader
from kbj.services.auth.issuer import APPROVAL_PATH, TOKEN_PATH
from kbj.services.auth.service import build_auth_service
from kbj.services.runtime import MemoryHealthSink
from tests.fakes.clock import FakeClock

KST = ZoneInfo("Asia/Seoul")
START = datetime(2026, 10, 6, 5, 0, tzinfo=KST)
END = START + timedelta(hours=24)


def test_one_day_issues_token_at_0500_and_0400_only() -> None:
    clock = FakeClock(START)
    posts: list[tuple[str, datetime]] = []

    def kis(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        posts.append((req.url.path, clock.now()))
        n = len(posts)
        if req.url.path == TOKEN_PATH:
            expired = (clock.now() + timedelta(hours=24)).astimezone(KST)
            return httpx.Response(
                200,
                json={
                    "access_token": f"eyJDAY{n}",
                    "expires_in": 86400,
                    "access_token_token_expired": expired.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
        return httpx.Response(200, json={"approval_key": f"WS{n}"})

    s = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        kis_app_key=SecretStr("PSappKEY0123456789abcdef"),
        kis_app_secret=SecretStr("SECRETvalue9876543210zyx"),
        service="auth",
    )
    r = fakeredis.FakeRedis()
    http = httpx.Client(
        base_url=KisCredentials.from_settings(s).base_url, transport=httpx.MockTransport(kis)
    )
    svc = build_auth_service(s, r, http, MemoryHealthSink(), now=clock.now)
    readers = reader(r, s, now=clock.now), reader(r, s, "ws_key", now=clock.now)
    while clock.now() < END:
        st = svc.step(clock.now())
        assert st.ok, st
        assert readers[0].get() and readers[1].get()  # 창 내내 읽는 쪽은 값이 있다
        clock.advance(30)

    token = [t.astimezone(KST) for p, t in posts if p == TOKEN_PATH]
    ws = [t.astimezone(KST) for p, t in posts if p == APPROVAL_PATH]
    assert [t.strftime("%m-%d %H:%M:%S") for t in token] == ["10-06 05:00:00", "10-07 04:00:00"]
    assert token[1] - token[0] >= timedelta(hours=23)  # 어떤 23시간 반열린 구간에도 1회 이하
    assert [t.strftime("%m-%d %H:%M") for t in ws] == ["10-06 05:00", "10-06 16:00", "10-07 03:00"]
    assert len(posts) == len(token) + len(ws)  # 그 밖의 발급 요청은 없다

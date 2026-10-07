"""KisClient: 캐시 우선 토큰·레이트리미터 연동 (가짜 KIS 서버 = httpx.MockTransport)."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from config.settings import Settings
from data.kis.auth_client import TOKEN_PATH
from data.kis.ratelimit import LocalRateLimiter, Priority, RateLimitTimeout, Reason
from data.kis.rest import (
    MINUTE_PATH,
    MINUTE_TR,
    KisClient,
    KisResponse,
    minute_chart_params,
    redact,
)

APP_KEY = "PSappKEY0123456789abcdef"
APP_SECRET = "SECRETvalue9876543210zyx"
TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.FAKE.TOKEN"
P_PRICE = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
TR_PRICE = "FHMIF10000000"
PARAMS = {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": "A01612"}


def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        kis_app_key=SecretStr(APP_KEY),
        kis_app_secret=SecretStr(APP_SECRET),
        kis_token_cache_path=tmp_path / "state" / "kis.token.json",
    )


class FakeKis:
    """토큰 발급과 GET 을 흉내 낸다. quote 응답은 순서대로 꺼낸다(비면 정상)."""

    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.token_posts = 0
        self.gets: list[httpx.Request] = []
        self.quotes: list[dict[str, str]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == TOKEN_PATH:
            self.token_posts += 1
            return httpx.Response(
                200,
                json={
                    "access_token": self.token,
                    "token_type": "Bearer",
                    "expires_in": 86400,
                    "access_token_token_expired": "2099-01-01 00:00:00",
                },
            )
        self.gets.append(req)
        body = self.quotes.pop(0) if self.quotes else {"rt_cd": "0", "msg_cd": "MCA00000"}
        return httpx.Response(200, json=body | {"output": {"futs_prpr": "400.00"}})


def client(tmp_path: Path, fake: FakeKis, **kw: object) -> KisClient:
    return KisClient(settings(tmp_path), transport=httpx.MockTransport(fake), **kw)  # type: ignore[arg-type]


def test_get_sends_cached_token_and_headers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeKis()
    c = client(tmp_path, fake)
    r1 = c.get(P_PRICE, TR_PRICE, PARAMS)
    r2 = c.get(P_PRICE, TR_PRICE, PARAMS, tr_cont="N")
    assert r1.ok and r2.ok
    assert fake.token_posts == 1
    h = fake.gets[0].headers
    assert h["authorization"] == f"Bearer {TOKEN}"
    assert (h["appkey"], h["appsecret"], h["tr_id"], h["custtype"]) == (
        APP_KEY,
        APP_SECRET,
        TR_PRICE,
        "P",
    )
    assert fake.gets[1].headers["tr_cont"] == "N"
    assert dict(fake.gets[0].url.params) == PARAMS
    # 두 번째 실행(새 프로세스)은 파일 캐시의 토큰을 쓴다 — probe 가 실행마다 발급하지 않는다
    fake2 = FakeKis(token="SHOULD-NOT-BE-ISSUED")
    c2 = client(tmp_path, fake2)
    assert c2.get(P_PRICE, TR_PRICE, PARAMS).ok
    assert fake2.token_posts == 0
    assert fake2.gets[0].headers["authorization"] == f"Bearer {TOKEN}"
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err and APP_KEY not in out.out + out.err


def test_secrets_include_token_and_redact(tmp_path: Path) -> None:
    c = client(tmp_path, FakeKis())
    assert TOKEN not in c.secrets()  # 아직 받지 않았다 (생성만으로 I/O 없음)
    assert not (tmp_path / "state").exists()
    c.get(P_PRICE, TR_PRICE, PARAMS)
    assert TOKEN in c.secrets()
    text = f"auth={TOKEN} key={APP_KEY} sec={APP_SECRET}"
    assert redact(text, c) == "auth=*** key=*** sec=***"
    assert TOKEN not in repr(c)


def test_token_file_is_private(tmp_path: Path) -> None:
    c = client(tmp_path, FakeKis())
    c.get(P_PRICE, TR_PRICE, PARAMS)
    path = tmp_path / "state" / "kis.token.json"
    assert path.stat().st_mode & 0o077 == 0
    assert json.loads(path.read_text())["token"]["access_token"] == TOKEN


class RecordingLimiter:
    def __init__(self, fail_after: int | None = None) -> None:
        self.acquired: list[tuple[Priority, str, float | None]] = []
        self.slowdowns = 0
        self.fail_after = fail_after

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        if self.fail_after is not None and len(self.acquired) >= self.fail_after:
            raise RateLimitTimeout(priority, tr_id, Reason.BUCKET)
        self.acquired.append((priority, tr_id, timeout))

    def on_rate_limited(self) -> float:
        self.slowdowns += 1
        return 2.0


def test_limiter_is_asked_before_each_call(tmp_path: Path) -> None:
    lim = RecordingLimiter()
    fake = FakeKis()
    c = client(tmp_path, fake, rate_limiter=lim)
    c.get(P_PRICE, TR_PRICE, PARAMS, priority=Priority.P4, timeout=0.0)
    c.get(P_PRICE, "FHPIF05030100", PARAMS)
    assert lim.acquired == [(Priority.P4, TR_PRICE, 0.0), (Priority.P2, "FHPIF05030100", None)]
    assert len(fake.gets) == 2


def test_rate_limited_slows_down_and_retries_once(tmp_path: Path) -> None:
    lim = RecordingLimiter()
    fake = FakeKis()
    limited = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}
    fake.quotes = [limited, limited, limited]
    c = client(tmp_path, fake, rate_limiter=lim)
    r = c.get(P_PRICE, TR_PRICE, PARAMS, priority=Priority.P1)
    assert r.rate_limited  # 재시도도 걸리면 그 응답을 돌려준다 (한 번만 재시도)
    assert lim.slowdowns == 1
    assert len(fake.gets) == 2 and len(lim.acquired) == 2
    fake.quotes = [limited]
    assert c.get(P_PRICE, TR_PRICE, PARAMS).ok
    assert lim.slowdowns == 2


def test_rate_limited_retry_without_permit_returns_first_response(tmp_path: Path) -> None:
    lim = RecordingLimiter(fail_after=1)
    fake = FakeKis()
    fake.quotes = [{"rt_cd": "1", "msg_cd": "EGW00201"}]
    c = client(tmp_path, fake, rate_limiter=lim)
    r = c.get(P_PRICE, TR_PRICE, PARAMS, timeout=0.0)
    assert r.rate_limited and len(fake.gets) == 1 and lim.slowdowns == 1


def test_no_limiter_means_no_retry(tmp_path: Path) -> None:
    """probe 는 리미터 없이 한도를 재야 한다 (rest_limit) — 한도초과를 그대로 본다."""
    fake = FakeKis()
    fake.quotes = [{"rt_cd": "1", "msg_cd": "EGW00201"}]
    r = client(tmp_path, fake).get(P_PRICE, TR_PRICE, PARAMS)
    assert r.rate_limited and len(fake.gets) == 1


def test_permit_timeout_propagates_before_sending(tmp_path: Path) -> None:
    fake = FakeKis()
    c = client(tmp_path, fake, rate_limiter=RecordingLimiter(fail_after=0))
    with pytest.raises(RateLimitTimeout):
        c.get(P_PRICE, TR_PRICE, PARAMS, priority=Priority.P4, timeout=0.0)
    assert fake.gets == []


class FlakyProvider:
    def __init__(self) -> None:
        self.tokens = ["stale-token", "fresh-token"]
        self.invalidated = 0

    def get(self) -> str:
        return self.tokens[0]

    def invalidate(self) -> None:
        self.invalidated += 1
        self.tokens.pop(0)


def test_rejected_token_is_invalidated_and_retried(tmp_path: Path) -> None:
    prov = FlakyProvider()
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        auth = req.headers["authorization"]
        seen.append(auth)
        if auth == "Bearer stale-token":
            return httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00123"})
        return httpx.Response(200, json={"rt_cd": "0", "msg_cd": "MCA00000"})

    c = KisClient(settings(tmp_path), token_provider=prov, transport=httpx.MockTransport(handler))
    assert c.get(P_PRICE, TR_PRICE, PARAMS).ok
    assert prov.invalidated == 1
    assert seen == ["Bearer stale-token", "Bearer fresh-token"]


def test_with_local_limiter_on_fake_clock(tmp_path: Path) -> None:
    class Clock:
        t = 1_790_553_600_000_000

        def now_us(self) -> int:
            return self.t

        def sleep(self, s: float) -> None:
            self.t += round(s * 1_000_000)

    clock = Clock()
    c = client(tmp_path, FakeKis(), rate_limiter=LocalRateLimiter(clock=clock))
    start = clock.t
    for _ in range(5):
        assert c.get(P_PRICE, TR_PRICE, PARAMS, priority=Priority.P1).ok
    assert clock.t - start == 4 * 250_000


def test_unreachable_redis_url_falls_back_to_file_cache(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """REDIS_URL 이 닿지 않는 곳(여기선 localhost:1, 연결 거부)이어도 probe 의 GET 이 돈다."""
    s = settings(tmp_path).model_copy(update={"redis_url": "redis://127.0.0.1:1/0"})
    fake = FakeKis()
    c = KisClient(s, transport=httpx.MockTransport(fake))
    with caplog.at_level(logging.WARNING):
        assert c.get(P_PRICE, TR_PRICE, PARAMS).ok
        assert c.get(P_PRICE, TR_PRICE, PARAMS).ok
    assert fake.token_posts == 1
    path = tmp_path / "state" / "kis.token.json"
    assert json.loads(path.read_text())["token"]["access_token"] == TOKEN
    assert path.stat().st_mode & 0o077 == 0
    assert "파일 캐시" in caplog.text
    out = capsys.readouterr()
    for secret in (TOKEN, APP_KEY, APP_SECRET):
        assert secret not in caplog.text + out.out + out.err


# ── 분봉 요청 도우미 ──


class _RecordingCtx:
    """probe `Ctx.call` 이 받은 인자를 남긴다 — 실측에 쓴 파라미터와 도우미를 대조한다."""

    def __init__(self) -> None:
        self.findings: dict[str, object] = {}
        self.seen: list[tuple[str, str, dict[str, str]]] = []

    def call(self, path: str, tr_id: str, params: dict[str, str], **_: object) -> KisResponse:
        self.seen.append((path, tr_id, dict(params)))
        return KisResponse(200, {"rt_cd": "0", "output2": []}, 0.0)


@pytest.mark.parametrize(
    ("market", "day", "hour"),
    [
        ("F", date(2026, 9, 28), "160000"),  # 주간 — probe DAY_END F
        ("CM", date(2026, 9, 22), "235959"),  # 야간 — #17 첫 실측 입력
        ("CM", date(2026, 9, 28), "300000"),  # 야간 (시작일, 확장 표기) — #17b, 적재가 쓰는 입력
    ],
)
def test_minute_chart_params_are_the_ones_the_probe_measured_with(
    market: str, day: date, hour: str
) -> None:
    from scripts import probe_minute_history
    from scripts.probe_common import P_MINUTE, TR_MINUTE

    ctx = _RecordingCtx()
    probe_minute_history.fetch_resp(ctx, "A01612", day, hour, market)  # type: ignore[arg-type]
    assert ctx.seen == [(MINUTE_PATH, MINUTE_TR, minute_chart_params(market, "A01612", day, hour))]
    assert (MINUTE_PATH, MINUTE_TR) == (P_MINUTE, TR_MINUTE)


@pytest.mark.parametrize("hour", ["1600", "16:00:00", "", "2359599"])
def test_minute_chart_params_want_six_digit_hours(hour: str) -> None:
    with pytest.raises(ValueError, match="HHMMSS"):
        minute_chart_params("F", "A01612", date(2026, 9, 28), hour)

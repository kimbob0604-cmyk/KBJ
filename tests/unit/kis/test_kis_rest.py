"""KisRestClient: 읽기 전용 토큰·레이트리미터 연동 (가짜 KIS 서버 = httpx.MockTransport).

GEXLAB `tests/unit/test_kis_rest.py` 13개 중 11개를 옮겼다. 시험 본문은 그대로이고, 생성자 차이는
`tests/unit/kis/conftest.py`(`GxSettings`·`KisClient` 어댑터 — 발급은 auth 대역이 한 번)가 흡수한다.
GX 와 다른 점:
- 옮기지 않음:
  `test_token_file_is_private`(:113)·`test_unreachable_redis_url_falls_back_to_file_cache` (:235) —
  토큰 파일 캐시가 없다(토큰은 Redis 에만, 설계 §1.2).
- `settings()` 에서 `kis_token_cache_path` 를 뺐다(그 설정이 없다).
- 분봉 파라미터 대조 시험은 GX probe(`legacy/gexlab/scripts/probe_minute_history.py`)를 GX 루트에서
  하위 프로세스로 불러 같은 값을 받는다(`_probe_fetch_resp`) — KBJ 시험이 legacy 를 import 하지
  않게(GX 의 `scripts`·`config` 패키지 이름이 KBJ 와 겹친다).
새로: provider 없이 만들면 TypeError(K7 — 기본 발급 경로 없음).
"""

from __future__ import annotations

import functools
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from kbj.config.settings import Settings
from kbj.data.private.kis.credentials import KisCredentials
from kbj.data.private.kis.rest import (
    MINUTE_PATH,
    MINUTE_TR,
    KisRestClient,
    minute_chart_params,
    redact,
)
from kbj.data.ratelimit import LocalRateLimiter, Priority, RateLimitTimeout, Reason
from kbj.services.auth.issuer import TOKEN_PATH
from tests.unit.kis.conftest import GxSettings, KisClient

APP_KEY = "PSappKEY0123456789abcdef"
APP_SECRET = "SECRETvalue9876543210zyx"
TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.FAKE.TOKEN"
P_PRICE = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
TR_PRICE = "FHMIF10000000"
PARAMS = {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": "A01612"}
GX_ROOT = Path(__file__).resolve().parents[3] / "legacy" / "gexlab"


def settings(tmp_path: Path) -> Settings:
    return GxSettings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        kis_app_key=SecretStr(APP_KEY),
        kis_app_secret=SecretStr(APP_SECRET),
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


def client(tmp_path: Path, fake: FakeKis, **kw: Any) -> KisRestClient:
    return KisClient(
        settings(tmp_path), transport=httpx.MockTransport(fake), cache_dir=tmp_path, **kw
    )


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
    # 두 번째 실행(새 프로세스)은 캐시(Redis)의 토큰을 쓴다 — 실행마다 발급하지 않는다
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


# ── 새로: 기본 발급 경로 없음(K7) ──


def test_client_without_a_token_provider_is_a_type_error() -> None:
    creds = KisCredentials(app_key=SecretStr(APP_KEY), app_secret=SecretStr(APP_SECRET))
    with pytest.raises(TypeError):
        KisRestClient(creds)  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
    with pytest.raises(TypeError, match="auth"):
        KisRestClient(creds, None, None)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


# ── 분봉 요청 도우미 ──

_PROBE_SCRIPT = """
import json, sys
from datetime import date
sys.path.insert(0, ".")
from data.kis.rest import KisResponse
from scripts import probe_minute_history
from scripts.probe_common import P_MINUTE, TR_MINUTE

class Ctx:  # probe `Ctx.call` 이 받은 인자를 남긴다
    def __init__(self):
        self.findings = {}
        self.seen = []

    def call(self, path, tr_id, params, **_):
        self.seen.append([path, tr_id, dict(params)])
        return KisResponse(200, {"rt_cd": "0", "output2": []}, 0.0)

out = []
for market, day, hour in json.loads(sys.argv[1]):
    ctx = Ctx()
    probe_minute_history.fetch_resp(ctx, "A01612", date.fromisoformat(day), hour, market)
    out.append(ctx.seen)
print(json.dumps({"seen": out, "consts": [P_MINUTE, TR_MINUTE]}))
"""

_MINUTE_CASES: tuple[tuple[str, date, str], ...] = (
    ("F", date(2026, 9, 28), "160000"),  # 주간 — probe DAY_END F
    ("CM", date(2026, 9, 22), "235959"),  # 야간 — #17 첫 실측 입력
    ("CM", date(2026, 9, 28), "300000"),  # 야간 (시작일, 확장 표기) — #17b, 적재가 쓰는 입력
)


@functools.cache
def _probe_runs() -> dict[str, Any]:
    """GX probe 의 `fetch_resp` 를 GX 루트에서 하위 프로세스로 부른 결과(세 경우 한 번에)."""
    cases = json.dumps([[m, d.isoformat(), h] for m, d, h in _MINUTE_CASES])
    proc = subprocess.run(  # noqa: S603 — 이 레포의 파이썬으로 고정 문자열 스크립트를 돌린다
        [sys.executable, "-c", _PROBE_SCRIPT, cases],
        cwd=GX_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def _probe_fetch_resp(market: str, day: date, hour: str) -> list[tuple[str, str, dict[str, str]]]:
    seen = _probe_runs()["seen"][_MINUTE_CASES.index((market, day, hour))]
    return [(p, t, dict(q)) for p, t, q in seen]


@pytest.mark.skipif(
    not (GX_ROOT / "scripts" / "probe_minute_history.py").exists(),
    reason="legacy GX probe 가 없다(P9 정리 뒤) — 이 대조는 그때 지운다",
)
@pytest.mark.parametrize(("market", "day", "hour"), _MINUTE_CASES)
def test_minute_chart_params_are_the_ones_the_probe_measured_with(
    market: str, day: date, hour: str
) -> None:
    seen = _probe_fetch_resp(market, day, hour)
    assert seen == [(MINUTE_PATH, MINUTE_TR, minute_chart_params(market, "A01612", day, hour))]
    p_minute, tr_minute = _probe_runs()["consts"]
    assert (MINUTE_PATH, MINUTE_TR) == (p_minute, tr_minute)


@pytest.mark.parametrize("hour", ["1600", "16:00:00", "", "2359599"])
def test_minute_chart_params_want_six_digit_hours(hour: str) -> None:
    with pytest.raises(ValueError, match="HHMMSS"):
        minute_chart_params("F", "A01612", date(2026, 9, 28), hour)

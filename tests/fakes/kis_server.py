"""가짜 KIS — REST(접근토큰·접속키 발급, 주식·GEX TR)와 마스터 배포 서버. 합성 데이터만(U3).

GEXLAB `tests/fakes/kis_server.py`(`FakeKisServer` — `token_posts`·`bearers`·`max_in_window`·
`inject`·`make_client`)의 KBJ 판이다. GX 판은 GX 모듈(poller·옵션 체인 fixture·`data.store`)에 묶여
있어 그대로 옮기지 않고, 하루 운영 시뮬레이션(tests/sim)이 쓰는 것만 같은 이름으로 다시 만들었다.
GEX 체인·분봉 세부(전광판 행사가·야간 시각 표기)는 P7 승격 때 GX 판을 옮긴다.

- **발급**: POST 토큰 경로(접근토큰 — 24시간)·접속키 경로(웹소켓 접속키). 앱키·시크릿이 맞아야 하고,
  누가 보냈는지(`transport(consumer)` 로 만든 이름)를 `posts` 에 남긴다 → '발급은 auth 에서만' 단언.
- **조회**: GET. Bearer 토큰이 이 서버가 발급했고 만료 전이어야 한다(아니면 `EGW00123`). 앱키·
  시크릿 헤더가 맞아야 한다. `tr_id` 와 경로가 TR 표(`TRS`)와 맞아야 한다(아니면 `OPSQ0002`).
- 응답 본문은 (tr_id, 파라미터)로 정해지는 **합성 값**(고정 시드 — sha256). 종목은
  합성 20개(`SYMBOLS`).
- **오류 주입** `inject(msg_cd, count=1, *, start=None, until=None, tr_id=None, consumers=None)`:
  `EGW00201`(초당 한도)·`EGW00123`(토큰 거절)·`EGW00133`(발급 1분 1회 — POST)·`HTTP500`. 시각은 가짜
  시계 기준, consumers 를 주면 그 프로세스의 요청만.
- **기록**: `calls`(시각·consumer·방법·경로·tr_id·파라미터·Bearer·결과), `token_posts`·
  `approval_posts`, `issued`(발급한 값·시각·만료), `max_in_window(초)`(어떤 반열린 창의 최대 건수).
- **마스터**: `master_transport(consumer)` — 배포 경로의 `fo_idx_code_mts.mst.zip` 을 합성 마스터
  줄(tests/fixtures/synthetic/kis/master_lines.json)로 만든 zip 으로 준다(cp949, 한 파일).

키·토큰 값은 모두 가짜다. 토큰은 JWT 모양이 아니다(공개 안전 검사에 걸리지 않게).
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import httpx
from pydantic import SecretStr
from redis import Redis

from kbj.data.private.kis.credentials import KisCredentials
from kbj.data.private.kis.master import FO_MASTER_FILE
from kbj.data.private.kis.rest import MINUTE_PATH, MINUTE_TR, KisRestClient
from kbj.data.private.kis.token import reader
from kbj.data.ratelimit import Clock, RedisRateLimiter
from kbj.services.auth.issuer import APPROVAL_PATH, TOKEN_PATH

KST: Final = ZoneInfo("Asia/Seoul")
FIX: Final = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic" / "kis"
MASTER_PATH: Final = "/common/master/" + FO_MASTER_FILE
TOKEN_LIFE: Final = timedelta(hours=24)

# 가짜 자격 — 실제 키 모양(PS+34자·base64 180자)이 아니다
APP_KEY: Final = "PSsimAPPKEY0123456789abcd"
APP_SECRET: Final = "SIMsecretVALUE9876543210zyx"

# 합성 종목 20개(코드 99xxxx — 실제 상장 코드와 겹치지 않는 대역). 이름도 합성
SYMBOLS: Final[tuple[str, ...]] = tuple(f"99{i:04d}" for i in range(10, 210, 10))

# TR → 경로. 주식 TR 은 kbj/data/private/kis/datasets.py 의 notes, GEX TR 은 GX 실측 경로
# (scripts/probe_common.py). `FHKST663300C0` 는 [추정 TR](설계 R20 — 미실측)
TRS: Final[dict[str, str]] = {
    "FHKST01010100": "/uapi/domestic-stock/v1/quotations/inquire-price",
    "FHKST01010900": "/uapi/domestic-stock/v1/quotations/inquire-investor",
    "FHPTJ04040000": "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market",
    "FHPTJ04400000": "/uapi/domestic-stock/v1/quotations/foreign-institution-total",
    "FHPST01710000": "/uapi/domestic-stock/v1/quotations/volume-rank",
    "FHPST02400000": "/uapi/etfetn/v1/quotations/inquire-price",
    "FHKST663300C0": "/uapi/domestic-stock/v1/quotations/estimate-perform",
    MINUTE_TR: MINUTE_PATH,
    "FHPIF05030100": "/uapi/domestic-futureoption/v1/quotations/display-board-callput",
    "FHPTJ04030000": "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market",
}

OK_MSG: Final = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리 되었습니다."}
ERRORS: Final[dict[str, tuple[int, dict[str, str]]]] = {
    "EGW00201": (
        500,
        {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."},
    ),
    "EGW00123": (500, {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}),
    "EGW00133": (
        403,
        {
            "error_code": "EGW00133",
            "error_description": "접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)",
        },
    ),
}


@dataclass(frozen=True)
class KisCall:
    """받은 요청 하나(값은 가짜)."""

    at: datetime
    consumer: str
    method: str
    path: str
    tr_id: str
    params: dict[str, str]
    bearer: str | None
    status: int
    msg_cd: str


@dataclass(frozen=True)
class Issued:
    kind: str  # "token" | "ws_key"
    value: str
    at: datetime
    expires_at: datetime
    consumer: str


@dataclass
class _Rule:
    code: str
    count: int
    start: datetime | None
    until: datetime | None
    tr_id: str | None
    consumers: frozenset[str] | None = None
    hits: int = 0

    def active(self, now: datetime, tr_id: str, consumer: str) -> bool:
        if self.count <= 0:
            return False
        if self.consumers is not None and consumer not in self.consumers:
            return False
        if self.start is not None and now < self.start:
            return False
        if self.until is not None and now >= self.until:
            return False
        return self.tr_id is None or self.tr_id == tr_id


def _seed(*parts: object) -> int:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _num(seed: int, lo: int, hi: int) -> int:
    return lo + seed % (hi - lo + 1)


def synthetic_output(tr_id: str, params: dict[str, str], day: str) -> dict[str, Any]:
    """TR 별 합성 응답 본문(정상). 같은 (tr_id, 파라미터, 날짜)면 같은 값."""
    code = params.get("FID_INPUT_ISCD", "") or params.get("fid_input_iscd", "")
    s = _seed(tr_id, sorted(params.items()), day)
    price = _num(s, 5_000, 300_000)
    if tr_id == "FHKST01010100":
        return {
            **OK_MSG,
            "output": {
                "stck_shrn_iscd": code,
                "stck_prpr": str(price),
                "prdy_vrss": str(_num(s >> 3, 0, 3_000) - 1_500),
                "prdy_ctrt": f"{(_num(s >> 5, 0, 600) - 300) / 100:.2f}",
                "acml_vol": str(_num(s >> 7, 1_000, 5_000_000)),
                "acml_tr_pbmn": str(price * _num(s >> 9, 1_000, 900_000)),
                "hts_avls": str(_num(s >> 11, 100, 900_000)),
            },
        }
    if tr_id == "FHKST01010900":
        rows = [
            {
                "stck_bsop_date": day,
                "prsn_ntby_qty": str(_num(s >> i, 0, 200_000) - 100_000),
                "frgn_ntby_qty": str(_num(s >> (i + 1), 0, 200_000) - 100_000),
                "orgn_ntby_qty": str(_num(s >> (i + 2), 0, 200_000) - 100_000),
                "prsn_ntby_tr_pbmn": str(_num(s >> (i + 3), 0, 9_000_000) - 4_500_000),
                "frgn_ntby_tr_pbmn": str(_num(s >> (i + 4), 0, 9_000_000) - 4_500_000),
                "orgn_ntby_tr_pbmn": str(_num(s >> (i + 5), 0, 9_000_000) - 4_500_000),
            }
            for i in range(3)
        ]
        return {**OK_MSG, "output": rows}
    if tr_id == MINUTE_TR:
        bars = [
            {
                "stck_bsop_date": day,
                "stck_cntg_hour": f"{15 - i // 60:02d}{59 - i % 60:02d}00",
                "futs_prpr": f"{400 + _num(s >> i, 0, 2_000) / 100:.2f}",
                "cntg_vol": str(_num(s >> (i + 2), 1, 5_000)),
            }
            for i in range(5)
        ]
        return {**OK_MSG, "output1": {"hts_kor_isnm": "합성선물"}, "output2": bars}
    rows = [
        {
            "rank": str(i + 1),
            "mksc_shrn_iscd": SYMBOLS[(_num(s >> i, 0, 99) + i) % len(SYMBOLS)],
            "value": str(_num(s >> (i + 1), 1, 9_000_000)),
        }
        for i in range(3)
    ]
    return {**OK_MSG, "output": rows}


def master_zip() -> bytes:
    """합성 마스터 줄로 만든 배포 zip(안에 `.mst` 한 개, cp949)."""
    lines = json.loads((FIX / "master_lines.json").read_text(encoding="utf-8"))["lines"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("fo_idx_code_mts.mst", ("\n".join(lines) + "\n").encode("cp949"))
    return buf.getvalue()


class FakeKisServer:
    """가짜 KIS REST + 마스터 배포. 시각은 주입한 시계(`now`)로 잰다."""

    def __init__(
        self,
        now: Callable[[], datetime],
        *,
        app_key: str = APP_KEY,
        app_secret: str = APP_SECRET,
        token_life: timedelta = TOKEN_LIFE,
    ) -> None:
        self._now = now
        self.app_key = app_key
        self.app_secret = app_secret
        self.token_life = token_life
        self.calls: list[KisCall] = []
        self.issued: list[Issued] = []
        self.master_downloads: list[tuple[datetime, str]] = []
        self._rules: list[_Rule] = []
        self._lock = threading.Lock()

    # ── 조립 ─────────────────────────────────────────────────────────────────────────
    def transport(self, consumer: str) -> httpx.MockTransport:
        """consumer(프로세스 이름 — auth·scheduler·gx·legacy …)가 쓰는 전송."""

        def handle(req: httpx.Request) -> httpx.Response:
            return self.handle(req, consumer)

        return httpx.MockTransport(handle)

    def master_transport(self, consumer: str) -> httpx.MockTransport:
        def handle(req: httpx.Request) -> httpx.Response:
            if req.method != "GET" or req.url.path != MASTER_PATH:
                return httpx.Response(404, text="not found")
            with self._lock:
                self.master_downloads.append((self._now(), consumer))
            return httpx.Response(200, content=master_zip())

        return httpx.MockTransport(handle)

    def inject(
        self,
        code: str,
        count: int = 1,
        *,
        start: datetime | None = None,
        until: datetime | None = None,
        tr_id: str | None = None,
        consumers: Iterable[str] | None = None,
    ) -> None:
        """다음 해당 요청 count 번에 오류를 준다(`EGW00201`·`EGW00123`·`EGW00133`·`HTTP500`)."""
        if code not in ERRORS and code != "HTTP500":
            raise ValueError(f"모르는 오류 주입: {code}")
        who = frozenset(consumers) if consumers is not None else None
        with self._lock:
            self._rules.append(_Rule(code, count, start, until, tr_id, who))

    # ── 처리 ─────────────────────────────────────────────────────────────────────────
    def _take_rule(self, now: datetime, tr_id: str, consumer: str, *, post: bool) -> str | None:
        for r in self._rules:
            if (r.code == "EGW00133") != post:
                continue
            if r.active(now, tr_id, consumer):
                r.count -= 1
                r.hits += 1
                return r.code
        return None

    def _record(
        self,
        consumer: str,
        req: httpx.Request,
        tr_id: str,
        bearer: str | None,
        status: int,
        msg_cd: str,
    ) -> None:
        params = {k: v for k, v in req.url.params.items()}
        self.calls.append(
            KisCall(
                self._now(),
                consumer,
                req.method,
                req.url.path,
                tr_id,
                params,
                bearer,
                status,
                msg_cd,
            )
        )

    def handle(self, req: httpx.Request, consumer: str = "unknown") -> httpx.Response:
        with self._lock:
            if req.method == "POST":
                return self._post(req, consumer)
            return self._get(req, consumer)

    def _post(self, req: httpx.Request, consumer: str) -> httpx.Response:
        now = self._now()
        path = req.url.path
        kind = {TOKEN_PATH: "token", APPROVAL_PATH: "ws_key"}.get(path)
        if kind is None:
            self._record(consumer, req, "", None, 404, "")
            return httpx.Response(404, json={"error_code": "404", "error_description": "없음"})
        try:
            body: dict[str, Any] = json.loads(req.content or b"{}")
        except ValueError:
            body = {}
        secret = body.get("appsecret") if kind == "token" else body.get("secretkey")
        if body.get("appkey") != self.app_key or secret != self.app_secret:
            self._record(consumer, req, "", None, 403, "EGW00103")
            return httpx.Response(
                403, json={"error_code": "EGW00103", "error_description": "유효하지 않은 AppKey"}
            )
        rule = self._take_rule(now, path, consumer, post=True)
        if rule is not None:
            status, err = ERRORS[rule]
            self._record(consumer, req, "", None, status, rule)
            return httpx.Response(status, json=err)
        n = sum(1 for i in self.issued if i.kind == kind) + 1
        if kind == "token":
            value = f"SIM-ACCESS-{n:03d}"
            expires = now + self.token_life
            self.issued.append(Issued(kind, value, now, expires, consumer))
            self._record(consumer, req, "", None, 200, "")
            expired_kst = expires.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
            return httpx.Response(
                200,
                json={
                    "access_token": value,
                    "token_type": "Bearer",
                    "expires_in": int(self.token_life.total_seconds()),
                    "access_token_token_expired": expired_kst,
                },
            )
        value = f"SIM-WS-KEY-{n:03d}"
        # 접속키 만료는 KIS 가 알려 주지 않는다 — 서버 쪽은 넉넉히 24시간 받아 준다
        self.issued.append(Issued(kind, value, now, now + timedelta(hours=24), consumer))
        self._record(consumer, req, "", None, 200, "")
        return httpx.Response(200, json={"approval_key": value})

    def _token_ok(self, token: str | None, now: datetime) -> bool:
        return any(
            i.kind == "token" and i.value == token and i.at <= now < i.expires_at
            for i in self.issued
        )

    def _get(self, req: httpx.Request, consumer: str) -> httpx.Response:
        now = self._now()
        tr_id = req.headers.get("tr_id", "")
        auth = req.headers.get("authorization", "")
        bearer = auth[len("Bearer ") :] if auth.startswith("Bearer ") else None

        def reply(status: int, body: dict[str, Any], msg_cd: str = "") -> httpx.Response:
            self._record(consumer, req, tr_id, bearer, status, msg_cd)
            return httpx.Response(status, json=body)

        if req.headers.get("appkey") != self.app_key or (
            req.headers.get("appsecret") != self.app_secret
        ):
            return reply(500, {"rt_cd": "1", "msg_cd": "EGW00103", "msg1": "유효하지 않은 AppKey"})
        if TRS.get(tr_id) != req.url.path:
            return reply(500, {"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "없는 서비스 코드"})
        rule = self._take_rule(now, tr_id, consumer, post=False)
        if rule == "HTTP500":
            self._record(consumer, req, tr_id, bearer, 500, "HTTP500")
            return httpx.Response(500, text="Internal Server Error")
        if rule is not None:
            status, err = ERRORS[rule]
            return reply(status, err, rule)
        if not self._token_ok(bearer, now):
            status, err = ERRORS["EGW00123"]
            return reply(status, err, "EGW00123")
        day = now.astimezone(KST).strftime("%Y%m%d")
        params = {k: v for k, v in req.url.params.items()}
        return reply(200, synthetic_output(tr_id, params, day))

    # ── 조회(시험) ───────────────────────────────────────────────────────────────────
    def posts(self, path: str | None = None) -> list[KisCall]:
        with self._lock:
            return [c for c in self.calls if c.method == "POST" and path in (None, c.path)]

    @property
    def token_posts(self) -> int:
        """접근토큰 발급 요청 수(성공·실패 모두)."""
        return len(self.posts(TOKEN_PATH))

    @property
    def approval_posts(self) -> int:
        return len(self.posts(APPROVAL_PATH))

    def tokens(self) -> list[Issued]:
        with self._lock:
            return [i for i in self.issued if i.kind == "token"]

    def approval_keys(self) -> list[Issued]:
        with self._lock:
            return [i for i in self.issued if i.kind == "ws_key"]

    def gets(self, consumer: str | None = None) -> list[KisCall]:
        with self._lock:
            return [c for c in self.calls if c.method == "GET" and consumer in (None, c.consumer)]

    @property
    def bearers(self) -> list[str | None]:
        return [c.bearer for c in self.gets()]

    def valid_approval_key(self, key: str) -> bool:
        now = self._now()
        return any(
            i.kind == "ws_key" and i.value == key and i.at <= now < i.expires_at
            for i in self.approval_keys()
        )

    def max_in_window(self, seconds: float = 1.0) -> int:
        """어떤 반열린 창 [t, t+seconds) 에 든 요청(발급 포함) 수의 최댓값."""
        with self._lock:
            times = sorted(c.at for c in self.calls)
        best, j = 0, 0
        width = timedelta(seconds=seconds)
        for i, t in enumerate(times):
            while times[j] <= t - width:
                j += 1
            best = max(best, i - j + 1)
        return best


def make_client(
    server: FakeKisServer,
    creds: KisCredentials,
    redis: Redis,
    *,
    consumer: str,
    clock: Clock,
    now: Callable[[], datetime],
    by: str | None = None,
) -> KisRestClient:
    """읽기 전용 토큰(reader) + 앱키 리미터(`rl:kis:<해시>`)를 끼운 KIS 클라이언트.

    발급하지 않는다(발급자는 auth 에만 — ADR 0004)."""
    limiter = RedisRateLimiter.scoped(redis, "kis", creds.app_key.get_secret_value(), clock=clock)
    provider = reader(redis, creds, now=now, by=by or consumer)
    return KisRestClient(creds, provider, limiter, transport=server.transport(consumer))


def fake_credentials() -> KisCredentials:
    return KisCredentials(app_key=SecretStr(APP_KEY), app_secret=SecretStr(APP_SECRET), env="real")


__all__ = [
    "APP_KEY",
    "APP_SECRET",
    "MASTER_PATH",
    "SYMBOLS",
    "TRS",
    "FakeKisServer",
    "Issued",
    "KisCall",
    "fake_credentials",
    "make_client",
    "master_zip",
    "synthetic_output",
]

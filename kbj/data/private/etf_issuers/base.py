"""운용사 어댑터 공통 — HTTP(호스트별 리미터·시간 제한·크기 상한·일시 오류 재시도)·파싱 도우미.

승격 원본: ET `etf_tracker_v9/collectors.py` — `_session`:19(requests 세션 → `IssuerHttp`),
`_f`:25(`parse_num`), `_is_kr`:31(`is_kr_code`), `_rows_from_table`:38(`rows_from_table`),
`isin_to_code`:59, `_ymd`:66(`ymd`), `_unesc`(`unescape_basic`). docs/p3_design.md §1.5·D-P3-12.

- 어댑터 하나 = 운용사 하나(`IssuerAdapter`): `universe() -> list[FundRef]`,
  `holdings(fund_key, day) -> (dict[종목코드, HoldingRow], 실제 기준일)`. 행의 source 는
  `ETF_ISSUERS:<운용사 키>`(예 `ETF_ISSUERS:kodex`), 품질 ok — 운용사가 공시한 PDF 그대로다.
  국내 상장 종목(6자리 단축코드)만 남긴다(해외 티커·현금·선물·채권 코드는 어댑터가 거른다).
- **실제 기준일**: 운용사가 응답에 담은 기준일을 돌려준다(요청일이 아니라 — 가짜 날짜를
  만들지 않는다).
  응답에 기준일이 없을 때만 요청일(ET 와 같다).
- HTTP(`IssuerHttp`): httpx(넘겨주기 따라가지 않음) + 부르기 전 **호스트별 scoped 리미터**
  `etf_issuers`(config/limits.yaml — `rl:etf_issuers:<호스트 해시>`, 1/s [제안]) + 조각마다 크기
  상한·전체 시간 상한(`kbj.data.http` 와 같은 방식). 일시 오류(전송 오류·408·425·429·5xx)만
  짧게 다시 부르고(429 는 리미터 감속), 그 밖의 상태는 바로 `IssuerError`(사유는 가려서). ET 의
  어댑터별 재시도(ACE·HANARO·KoAct·RISE `tries`)를 이 한 곳으로 모았다. 작업 단위 재시도는 등록부
  (`etf.collect` 3×900초)가 한다.
- 이 모듈은 kbj.services·kbj.engines 를 모른다(계약 ⑥). 운용사 실응답 fixture 는 레포에 넣지 않는다
  (로그인 등급 — 시험은 `tests/fakes/etf_issuer_server.py` 합성 응답).
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, ClassVar, Final, Literal, Protocol
from urllib.parse import urlsplit

import httpx
from redis import Redis

from kbj.core.quality import Quality
from kbj.core.rows import HoldingRow
from kbj.core.time import kst_date, utcnow
from kbj.data.http import FetchError, make_client, scrub
from kbj.data.limits import load_limits
from kbj.data.private.etf_issuers.datasets import LIMITER, SOURCE
from kbj.data.ratelimit import (
    LocalRateLimiter,
    Priority,
    RateLimitConfig,
    RateLimiter,
    RedisRateLimiter,
)

__all__ = [
    "KRCODE",
    "UA",
    "FundRef",
    "IssuerAdapter",
    "IssuerError",
    "IssuerHttp",
    "Reply",
    "holding",
    "is_kr_code",
    "isin_to_code",
    "parse_num",
    "rows_from_table",
    "source_of",
    "today_kst",
    "unescape_basic",
    "ymd",
]

UA: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
KRCODE: Final = re.compile(r"[0-9A-Z]{6}")  # 국내 종목코드 (신규 코드에 영문 포함: 0185L0 등)
MAX_BYTES: Final = 16 * 1024 * 1024
TOTAL_S: Final = 60.0
RETRY_STATUS: Final = frozenset({408, 425, 429, 500, 502, 503, 504})
RETRIES: Final = 3
BACKOFF_S: Final = 1.5


class IssuerError(FetchError):
    """운용사 호출 실패(문구의 키·쿼리 값은 가렸다). status = HTTP 상태(받았을 때)."""

    def __init__(self, issuer: str, status: int | None, reason: str) -> None:
        super().__init__(SOURCE, issuer, status, reason)
        self.issuer = issuer


@dataclass(frozen=True)
class FundRef:
    """운용사 목록의 펀드 하나 — fund_key 는 운용사 고유 키(PDF 조회에 쓴다)."""

    fund_key: str
    ticker: str | None
    name: str | None


class IssuerAdapter(Protocol):
    KEY: ClassVar[str]
    NAME: ClassVar[str]
    DEPTH: ClassVar[Literal["full", "top10"]]
    HISTORY: ClassVar[bool]  # 과거 기준일 조회가 되는가

    def universe(self) -> list[FundRef]: ...

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]: ...


# ── 파싱 도우미 (ET collectors.py 그대로) ─────────────────────────────────────────────────


def parse_num(v: object) -> float:
    """'1,234.5' → 1234.5. 읽지 못하면 0.0(ET `_f` — 빈 칸·'-' 를 0 으로)."""
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def is_kr_code(code: str | None) -> bool:
    """국내 상장 종목코드인지. 'ABT US EQUITY' 같은 해외 티커와 현금성 자산(KRD…) 배제."""
    c = (code or "").strip()
    return bool(KRCODE.fullmatch(c)) and any(ch.isdigit() for ch in c) and not c.startswith("KRD")


_TR: Final = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL: Final = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_TAG: Final = re.compile(r"<[^>]+>")


def table_cells(html: str) -> list[list[str]]:
    """HTML 표 → 행마다 칸 글자(태그를 지우고 앞뒤 공백을 턴다)."""
    return [[_TAG.sub("", x).strip() for x in _CELL.findall(tr)] for tr in _TR.findall(html)]


def rows_from_table(
    html: str,
    issuer: str,
    col_code: int = 0,
    col_name: int = 1,
    col_qty: int = 2,
    col_val: int = 3,
    col_wt: int = 4,
) -> dict[str, HoldingRow]:
    """표의 국내 종목 행 → {코드: HoldingRow}(ET `_rows_from_table`)."""
    out: dict[str, HoldingRow] = {}
    need = max(col_code, col_name, col_qty, col_val, col_wt)
    for c in table_cells(html):
        if len(c) > need and is_kr_code(c[col_code]):
            out[c[col_code]] = holding(
                issuer, c[col_code], c[col_name], parse_num(c[col_qty]), parse_num(c[col_wt]),
                parse_num(c[col_val]),
            )  # fmt: skip
    return out


# 우선주 ISIN → 단축코드 보정표.
# ISIN 본체 6번째 자리가 우선주 차수(1·2·3)인데 실제 단축코드 끝자리는 5·7·9 다.
#   삼성전자우 KR7005931001 → '005931' 이 아니라 005935
#   현대차우   KR7005381005 → 005385   현대차2우B KR7005382003 → 005387
# 이걸 안 고치면 같은 종목이 소스마다 다른 코드로 잡혀 합쳐지지 않는다.
_PREF: Final[Mapping[str, str]] = {"1": "5", "2": "7", "3": "9"}
_ISIN_KR: Final = re.compile(r"KR7([0-9A-Z]{5})([0-9])[0-9]{3}")


def isin_to_code(isin: str | None) -> str | None:
    """국내 주식·ETF ISIN(KR7…) → 6자리 단축코드. 그 외에는 None."""
    m = _ISIN_KR.fullmatch((isin or "").strip().upper())
    if not m:
        return None
    code = m.group(1) + _PREF.get(m.group(2), m.group(2))
    return code if is_kr_code(code) else None


def ymd(d: object) -> date | None:
    """'2026.08.04'·'20260804'·'2026-08-04' → date. 아니면 None."""
    s = str(d or "").replace(".", "").replace("-", "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def unescape_basic(s: str) -> str:
    """ET `_unesc` — &amp; &lt; &gt; &quot; 만."""
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')


def source_of(issuer: str) -> str:
    return f"{SOURCE}:{issuer}"


def holding(
    issuer: str, code: str, name: str | None, qty: float, wt: float, val: float
) -> HoldingRow:
    """어댑터가 만드는 구성종목 행 — source `ETF_ISSUERS:<운용사>`, 품질 ok."""
    nm = (name or "").strip() or None
    return HoldingRow(code=code, name=nm, qty=qty, wt=wt, val=val, source=source_of(issuer),
                      quality=Quality.OK)  # fmt: skip


def add_holding(rows: dict[str, HoldingRow], h: HoldingRow) -> None:
    """같은 코드가 또 오면 수량·비중·금액을 합친다(채권형 종목분류코드 중복 — ET ACE 주의 6)."""
    old = rows.get(h.code)
    if old is None:
        rows[h.code] = h
        return
    rows[h.code] = replace(
        old,
        qty=(old.qty or 0.0) + (h.qty or 0.0),
        wt=(old.wt or 0.0) + (h.wt or 0.0),
        val=(old.val or 0.0) + (h.val or 0.0),
    )


def today_kst() -> date:
    return kst_date(utcnow())


# ── HTTP ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes
    encoding: str = "utf-8"

    @property
    def text(self) -> str:
        return self.body.decode(self.encoding or "utf-8", errors="replace")


_CHARSET: Final = re.compile(r"charset=([\w-]+)", re.I)


@dataclass
class IssuerHttp:
    """운용사 HTTP 한 벌 — 어댑터 여럿이 같이 쓴다(호스트마다 리미터 하나).

    `limiter_for(host, rate_cap)` 이 그 호스트 리미터를 만든다(처음 한 번). 시험은 리미터 없이
    (`limiter_for=None`) `transport=httpx.MockTransport(...)` 를 넣는다.
    """

    client: httpx.Client
    limiter_for: Callable[[str, float | None], RateLimiter] | None = None
    max_bytes: int = MAX_BYTES
    total_s: float = TOTAL_S
    retries: int = RETRIES
    backoff_s: float = BACKOFF_S
    priority: Priority = Priority.P3
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    _limiters: dict[str, RateLimiter] = field(default_factory=dict[str, RateLimiter])
    _caps: dict[str, float] = field(default_factory=dict[str, float])
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ── 만들기 ──
    @classmethod
    def local(cls, *, transport: httpx.BaseTransport | None = None, **kw: Any) -> IssuerHttp:
        """프로세스 안 리미터(legacy shim·점검용). 속도는 config/limits.yaml `etf_issuers`."""
        base = load_limits().source(LIMITER).rate_config()

        def make(host: str, cap: float | None) -> RateLimiter:
            return LocalRateLimiter(_capped(base, cap))

        return cls(_client(transport), make, **kw)

    @classmethod
    def for_service(
        cls, redis: Redis, *, transport: httpx.BaseTransport | None = None, **kw: Any
    ) -> IssuerHttp:
        """여러 프로세스가 같이 쓰는 Redis 리미터 `rl:etf_issuers:<호스트 해시>`."""
        base = load_limits().source(LIMITER).rate_config()

        def make(host: str, cap: float | None) -> RateLimiter:
            return RedisRateLimiter.scoped(redis, LIMITER, host, _capped(base, cap))

        return cls(_client(transport), make, **kw)

    def close(self) -> None:
        self.client.close()

    def cap_rate(self, host: str, rate: float) -> None:
        """그 호스트의 속도 상한(config 보다 낮게만 — 예: KoAct Cloudflare 25요청/35초)."""
        if rate <= 0:
            raise ValueError("rate 는 0 보다 커야 한다")
        with self._lock:
            self._caps[host] = rate

    def _limiter(self, host: str) -> RateLimiter | None:
        if self.limiter_for is None:
            return None
        with self._lock:
            lim = self._limiters.get(host)
            if lim is None:
                lim = self.limiter_for(host, self._caps.get(host))
                self._limiters[host] = lim
            return lim

    # ── 부르기 ──
    def request(
        self,
        issuer: str,
        method: Literal["GET", "POST"],
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
        total_s: float | None = None,
        ok: frozenset[int] = frozenset({200}),
        retry_wait_s: float | None = None,
    ) -> Reply:
        """한 요청. `ok` 상태면 돌려주고, 일시 오류는 다시, 나머지는 `IssuerError`."""
        host = urlsplit(url).hostname or ""
        if not host:
            raise ValueError(f"절대 URL 이어야 한다: {url!r}")
        last: str = ""
        status: int | None = None
        for i in range(self.retries):
            lim = self._limiter(host)
            if lim is not None:
                lim.acquire(self.priority, host)
            try:
                status, body, enc = self._once(method, url, params, data, json_body, headers,
                                               total_s or self.total_s, issuer)  # fmt: skip
            except IssuerError:
                raise  # 크기·시간 상한 — 다시 불러도 같다
            except httpx.HTTPError as e:
                status, last = None, scrub(f"{type(e).__name__}: {e}", params)
            else:
                if status in ok:
                    return Reply(status, body, enc)
                last = f"HTTP {status} · {_snippet(body, enc)}"
                if status not in RETRY_STATUS:
                    raise IssuerError(issuer, status, scrub(f"{_path(url)} {last}", params))
                if status == 429 and lim is not None:
                    lim.on_rate_limited()
            if i < self.retries - 1:
                wait = retry_wait_s if retry_wait_s is not None else self.backoff_s
                self.sleep(wait * (i + 1))
        raise IssuerError(issuer, status, scrub(f"{_path(url)} 재시도 소진 — {last}", params))

    def _once(
        self,
        method: str,
        url: str,
        params: Mapping[str, Any] | None,
        data: Mapping[str, Any] | None,
        json_body: Any,
        headers: Mapping[str, str] | None,
        total_s: float,
        issuer: str,
    ) -> tuple[int, bytes, str]:
        deadline = self.clock() + total_s
        buf = bytearray()
        # params 를 빈 dict 로라도 넘기면 httpx 가 URL 의 쿼리를 지운다 — 있을 때만
        kw: dict[str, Any] = {"headers": dict(headers or {})}
        if params:
            kw["params"] = dict(params)
        if data is not None:
            kw["data"] = dict(data)
        if json_body is not None:
            kw["json"] = json_body
        with self.client.stream(method, url, **kw) as r:
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > self.max_bytes:
                    raise IssuerError(issuer, r.status_code, f"응답이 {self.max_bytes}B 보다 크다")
                if self.clock() > deadline:
                    raise IssuerError(issuer, r.status_code, f"응답 {total_s:g}초 초과")
            m = _CHARSET.search(r.headers.get("content-type", ""))
            return r.status_code, bytes(buf), (m.group(1) if m else "utf-8")

    def text(self, issuer: str, url: str, **kw: Any) -> str:
        return self.request(issuer, "GET", url, **kw).text

    def json(
        self,
        issuer: str,
        url: str,
        *,
        method: Literal["GET", "POST"] = "GET",
        retry_non_json: bool = False,
        allow_404: bool = False,
        **kw: Any,
    ) -> Any:
        """JSON 본문. 200 인데 JSON 이 아니면 오류(`retry_non_json` 이면 일시 오류로 보고 다시 —
        Cloudflare 챌린지 HTML, ET KoAct). `allow_404` 면 404 를 None 으로(ET ACE 미존재 펀드)."""
        ok = frozenset({200, 404}) if allow_404 else frozenset({200})
        tries = self.retries if retry_non_json else 1
        last = ""
        for i in range(tries):
            rep = self.request(issuer, method, url, ok=ok, **kw)
            if rep.status == 404:
                return None
            try:
                return json.loads(rep.text)
            except ValueError:
                last = _snippet(rep.body, rep.encoding)
            if i < tries - 1:
                wait = kw.get("retry_wait_s")
                self.sleep((wait if wait is not None else self.backoff_s) * (i + 1))
        params = kw.get("params")
        raise IssuerError(issuer, 200, scrub(f"{_path(url)} 200 이지만 JSON 이 아니다 — {last}",
                                             params))  # fmt: skip


def _capped(base: RateLimitConfig, cap: float | None) -> RateLimitConfig:
    if cap is None or cap >= base.rate:
        return base
    return replace(base, rate=cap, floor_rate=min(base.floor_rate, cap))


def _client(transport: httpx.BaseTransport | None) -> httpx.Client:
    return make_client("", transport=transport, headers={
        "User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})  # fmt: skip


def _path(url: str) -> str:
    """오류 문구에는 호스트·경로만(쿼리 값은 싣지 않는다)."""
    u = urlsplit(url)
    return f"{u.hostname or ''}{u.path}"


def _snippet(body: bytes, enc: str) -> str:
    text = " ".join(body[:2000].decode(enc or "utf-8", errors="replace").split())
    return text[:200] or "본문 없음"

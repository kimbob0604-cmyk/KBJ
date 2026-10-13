"""Yahoo v8 chart 클라이언트 — 로그인 등급(약관상, DATA_TIERS §1).

승격 원본: ETF-Traker `board/us/sources.py`(`YAHOO_CHART`:29, `parse_yahoo`:264, `yahoo_daily`:292 —
ET D-076·D-077). 파서 `parse_yahoo` 는 본문 그대로(시각 변환만 `utcfromtimestamp` → aware UTC — 같은
날짜), 원본 시험 `board/tests/test_us_sources.py:TestYahoo` 3개를
`tests/unit/data/private/test_yahoo.py` 로 옮겼다(import 경로만).

- **받아서 정규화만 한다**(ET 머리말). 판정·집계는 엔진(US·MAC 묶음)이 한다.
- 실패를 삼키지 않는다: 200 이 아니면 사유를 담아 `YahooError`, 429 는 리미터 감속
  (`on_rate_limited`) + `YahooThrottled`, 없는 심볼(404·`chart.error`)은 `YahooNotFound`.
- 못 받은 값은 None(0 으로 채우지 않는다 — ET 원칙). 종가가 없는 봉은 버린다(원본 그대로).
- **장중 봉**: 일봉 요청이라도 정규장이 열려 있으면 마지막 봉이 진행 중인 봉이다
  (`meta.currentTradingPeriod.regular` 안, `regularMarketTime` < 정규장 끝). `to_bars` 는 그 봉을
  `quality=estimated` 로 낸다 — '종가'로 쓰지 않게(SD check_futures_us_index 규칙 2) [확인 필요
  #46].
- 호스트 주소는 이 파일에만 둔다. 키가 없는 출처라 마스킹할 비밀은 없다(그래도 오류 문구는 자른다).
- 리미터는 limits.yaml `yahoo`(1/s [제안]) — `RedisRateLimiter.scoped(redis, 'yahoo', 'chart', …)`.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

import httpx
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.quality import Quality
from kbj.core.rows import Bar
from kbj.data.http import FetchError, get_capped, make_client
from kbj.data.limits import Limits, load_limits
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RedisRateLimiter

SOURCE: Final = "YAHOO"
LIMITS_SOURCE: Final = "yahoo"
ROW_SOURCE: Final = "yahoo"  # 행 source(daily_bar·prv_macro.series — 소문자, ET 와 같다)
_BASE: Final = "https://query1.finance.yahoo.com"
_CHART: Final = "/v8/finance/chart/{sym}"
TR_ID: Final = "yahoo:chart"  # 리미터 기록용 이름
MAX_BYTES: Final = 8 * 1024 * 1024
TOTAL_S: Final = 30.0
RANGES: Final = frozenset({"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max"})
INTERVALS: Final = frozenset({"1d", "1wk", "1mo"})


class YahooError(FetchError):
    """Yahoo 수집 실패(사유 포함)."""

    def __init__(self, dataset: str, status: int | None, reason: str) -> None:
        super().__init__(SOURCE, dataset, status, reason)


class YahooThrottled(YahooError):
    """429 — 리미터가 감속했다. 다시 부르기는 작업 재시도가 한다."""


class YahooNotFound(YahooError):
    """없는 심볼(404 또는 chart.error Not Found)."""


# ── 파서(ET 원본 그대로) ──────────────────────────────────────────────────────────────────


def parse_yahoo(payload: Any) -> list[dict[str, Any]]:
    """yahoo v8 차트 → 오름차순 일봉. 조정 종가가 아니라 원 종가를 쓴다.

    yahoo 는 `adjclose` 를 따로 준다. 신고가 판정은 분할이 반영된 시계열을
    요구하므로 분할 조정은 필요하지만 배당 조정은 필요 없다. v8 의 `close` 는
    이미 분할 조정값이고 배당은 반영되지 않는다 — 우리가 원하는 그것이다.
    """
    res = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
    if not res:
        return []
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    rows: list[dict[str, Any]] = []
    for i, t in enumerate(ts):

        def g(k: str, i: int = i) -> Any:
            v = q.get(k) or []
            return v[i] if i < len(v) else None

        c = g("close")
        if c is None:
            continue
        rows.append(
            dict(
                asof=_dt.datetime.fromtimestamp(t, tz=_dt.UTC).date().isoformat(),
                open=g("open"), high=g("high"), low=g("low"), close=c, volume=g("volume"),
                source="yahoo", ts=t,
            )
        )  # fmt: skip
    rows.sort(key=lambda r: r["asof"])
    return rows


def _live_period(payload: Any) -> tuple[int, int] | None:
    """정규장이 지금 열려 있으면 (시작, 끝) epoch 초 — 아니면 None."""
    res = (((payload or {}).get("chart") or {}).get("result") or [None])[0] or {}
    meta = res.get("meta") or {}
    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end, last = reg.get("start"), reg.get("end"), meta.get("regularMarketTime")
    if isinstance(start, int) and isinstance(end, int) and isinstance(last, int):
        if start <= last < end:
            return start, end
    return None
    if start <= last < end:
        return int(start), int(end)
    return None


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def to_bars(payload: Any, symbol: str) -> list[Bar]:
    """`parse_yahoo` 결과 → 일봉 행(`kbj.core.rows.Bar` — code = 심볼, venue '', source 'yahoo').

    정규장이 열린 동안의 마지막 봉은 진행 중인 봉이라 `estimated`(종가 아님 — [확인 필요 #46])."""
    rows = parse_yahoo(payload)
    live = _live_period(payload)
    out: list[Bar] = []
    for r in rows:
        vol = r.get("volume")
        in_live = live is not None and live[0] <= int(r["ts"]) < live[1]
        out.append(
            Bar(
                code=symbol, date=_dt.date.fromisoformat(r["asof"]), open=_num(r.get("open")),
                high=_num(r.get("high")), low=_num(r.get("low")), close=_num(r["close"]),
                volume=int(vol) if isinstance(vol, int | float) and vol >= 0 else None,
                turnover=None, source=ROW_SOURCE, venue="",
                quality=Quality.ESTIMATED if in_live else Quality.OK,
            )
        )  # fmt: skip
    return out


# ── 클라이언트 ────────────────────────────────────────────────────────────────────────────


def _reason(body: bytes) -> str:
    try:
        err = (json.loads(body.decode("utf-8", "replace")).get("chart") or {}).get("error") or {}
    except (ValueError, AttributeError):
        err = {}
    if isinstance(err, Mapping) and err:
        return f"{err.get('code', '')} {err.get('description', '')}".strip()[:300]  # pyright: ignore[reportUnknownMemberType]
    return body[:300].decode("utf-8", "replace")


class YahooClient:
    """`YahooClient(limiter).chart('^GSPC', '1y') -> list[Bar]`.

    시험은 `transport=` 에 가짜 서버(tests/fakes/yahoo_server.py)."""

    def __init__(
        self,
        limiter: RateLimiter,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = _BASE,
        priority: Priority = Priority.P3,
        acquire_timeout_s: float | None = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limiter = limiter
        self._priority = priority
        self._acquire_timeout = acquire_timeout_s
        self._clock = clock
        self._http = make_client(
            base_url, transport=transport, headers={"User-Agent": "Mozilla/5.0 (kbj)"}
        )

    def __repr__(self) -> str:
        return "YahooClient()"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> YahooClient:
        """Redis 리미터 `rl:yahoo:<'chart' 해시>`(limits.yaml `yahoo`). 키가 없는 출처다."""
        src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
        limiter = RedisRateLimiter.scoped(redis, LIMITS_SOURCE, "chart", src.rate_config(), clock)
        return cls(limiter, **kw)

    def close(self) -> None:
        self._http.close()

    def chart_payload(self, symbol: str, range_: str = "2y", interval: str = "1d") -> Any:
        """원 응답(JSON). 200 이 아니면 예외(사유 포함)."""
        if not symbol or any(c.isspace() for c in symbol):
            raise ValueError(f"심볼이 비었거나 공백이 있다: {symbol!r}")
        if range_ not in RANGES or interval not in INTERVALS:
            raise ValueError(f"range·interval 값이 틀렸다: {range_!r}·{interval!r}")
        dataset = f"chart:{symbol}"
        self._limiter.acquire(self._priority, TR_ID, self._acquire_timeout)
        try:
            status, body = get_capped(
                self._http, _CHART.format(sym=symbol.upper()),
                params={"range": range_, "interval": interval}, max_bytes=MAX_BYTES,
                total_s=TOTAL_S, clock=self._clock, source=SOURCE, dataset=dataset,
            )  # fmt: skip
        except httpx.HTTPError as e:
            raise YahooError(dataset, None, f"전송 실패: {type(e).__name__}") from None
        if status == 429:
            self._limiter.on_rate_limited()
            raise YahooThrottled(dataset, status, "429 한도 초과 — 감속")
        if status == 404:
            raise YahooNotFound(dataset, status, _reason(body))
        if status != 200:
            raise YahooError(dataset, status, f"HTTP {status} {_reason(body)}")
        try:
            payload = json.loads(body)
        except ValueError:
            raise YahooError(dataset, status, "JSON 이 아니다") from None
        err = ((payload or {}).get("chart") or {}).get("error")
        if err:
            raise YahooNotFound(dataset, status, _reason(body))
        return payload

    def chart(self, symbol: str, range_: str = "2y", interval: str = "1d") -> list[Bar]:
        """일봉(오름차순). 결과가 비면 빈 목록 — 부르는 쪽이 '못 받음'으로 적는다."""
        return to_bars(self.chart_payload(symbol, range_, interval), symbol)

    def charts(
        self, symbols: Sequence[str], range_: str = "2y"
    ) -> tuple[dict[str, list[Bar]], dict[str, str]]:
        """여러 심볼 — 한 심볼 실패가 다른 심볼을 멈추지 않는다(격리). (받은 것, {심볼: 사유})."""
        ok: dict[str, list[Bar]] = {}
        failed: dict[str, str] = {}
        for s in symbols:
            try:
                ok[s] = self.chart(s, range_)
            except YahooThrottled:
                raise  # 한도 — 남은 심볼을 계속 부르지 않는다
            except YahooError as e:
                failed[s] = e.reason
        return ok, failed

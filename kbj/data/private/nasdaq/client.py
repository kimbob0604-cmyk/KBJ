"""Nasdaq 스크리너·일봉 클라이언트 — 로그인 등급(약관 미확인, DATA_TIERS §1 · ADR 0001 Q10).

승격 원본: ETF-Traker `board/us/sources.py`(ET D-076·D-077) — `_num`:35·`_is_fund`:58·
`parse_screener`:65·`screener`:103·`nasdaq_symbol`:133·`nasdaq_symbol_forms`:143·
`parse_nasdaq_history`:166·`nasdaq_daily`:195. 파서는 **본문 그대로**(이름만 공개 — `_num` → `num`,
`_is_fund` → `is_fund`: legacy shim 이 kbj 비공개 이름을 import 하지 못한다), 원본 시험
`board/tests/test_us_sources.py` 의 Nasdaq 몫을
`tests/unit/data/private/test_us_sources.py` 로 옮겼다(import 경로만). 통신만 바꿨다: ET `requests`
세션 → `httpx` + 리미터(`limits.yaml nasdaq`),
실패는 `NasdaqError`(사유 포함 — ET `H.Fetch` 와 같은 자리).

- **받아서 정규화만 한다**(ET 머리말). 판정·집계는 `kbj.engines.us`.
- 실패를 삼키지 않는다: 200 이 아니면 사유를 담아 예외, 429 는 리미터 감속 + `NasdaqThrottled`,
  0행은 '거래 없음'이 아니라 예외(ET 원칙 — 빈 목록이면 '상장 첫날'처럼 읽힌다).
- 못 받은 값은 None(0 으로 채우지 않는다 — 0 은 '거래 없음', None 은 '못 받음').
- 스크리너 단위: 시가총액·가격 달러, 등락률 % [확인 필요 #47 — probe_results §7]. 실측 전이라 작업
  `us.universe`·`us.eod` 는 등록부에서 꺼져 있다(WRAP 이 켬).
- 호스트 주소는 이 파일에만. 키가 없는 출처라 마스킹할 비밀은 없다(그래도 오류 문구는 자른다).
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from typing import Any, Final

import httpx
from redis import Redis

from kbj.config.settings import Settings
from kbj.data.http import FetchError, get_capped, make_client
from kbj.data.limits import Limits, load_limits
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RedisRateLimiter

__all__ = [
    "LIMITS_SOURCE",
    "NASDAQ_REFERER",
    "SOURCE",
    "NasdaqClient",
    "NasdaqError",
    "NasdaqThrottled",
    "is_fund",
    "nasdaq_symbol",
    "nasdaq_symbol_forms",
    "num",
    "parse_nasdaq_history",
    "parse_screener",
]

SOURCE: Final = "NASDAQ"
LIMITS_SOURCE: Final = "nasdaq"
ROW_SOURCE: Final = "nasdaq"  # daily_bar 행 source(ET 와 같다)
SCREENER_SOURCE: Final = "nasdaq-screener"  # 스크리너 행 source(ET 원본 값)
_BASE: Final = "https://api.nasdaq.com"
_SCREENER: Final = "/api/screener/stocks"
_HISTORY: Final = "/api/quote/{sym}/historical"
# 스크리너는 브라우저 헤더가 아니면 403 을 준다. 사람이 보는 화면과 같은 데이터다.
NASDAQ_REFERER: Final = "https://www.nasdaq.com/market-activity/stocks/screener"
_HEADERS: Final = {
    "User-Agent": "Mozilla/5.0 (kbj)",
    "Accept": "application/json, text/plain, */*",
    "Referer": NASDAQ_REFERER,
}
MAX_BYTES: Final = 32 * 1024 * 1024  # 스크리너 전 종목(거래소 하나 약 4천 행)
TOTAL_S: Final = 60.0
HISTORY_DAYS: Final = 760  # 기본 시작 = 끝 − 760일(ET nasdaq_daily)


class NasdaqError(FetchError):
    """Nasdaq 수집 실패(사유 포함)."""

    def __init__(self, dataset: str, status: int | None, reason: str) -> None:
        super().__init__(SOURCE, dataset, status, reason)


class NasdaqThrottled(NasdaqError):
    """429 — 리미터가 감속했다. 다시 부르기는 작업 재시도가 한다."""


# ── 파서(ET 원본 그대로) ──────────────────────────────────────────────────────────────────


def num(v: Any) -> float | None:
    """'$226.80' · '1,234,567' · '0.666%' · '' → float | None.

    못 읽은 값을 0 으로 채우지 않는다. 0 은 '거래가 없었다' 는 사실이고
    None 은 '못 받았다' 는 사실이라 둘을 섞으면 브레드스가 통째로 틀린다.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in ("--", "N/A", "NA", "null"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[\$,%()\s]", "", s)
    if not s or s in ("-", "+"):
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def is_fund(name: str | None, cfg: Mapping[str, Any]) -> bool:
    if not cfg["universe"].get("exclude_funds"):
        return False
    n = f" {name or ''} "
    return any(tok.lower() in n.lower() for tok in cfg["universe"].get("exclude_name_tokens") or ())


def parse_screener(
    payload: Any, cfg: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """스크리너 응답 → 정규화된 행. 파서를 따로 두어 픽스처로 시험한다.

    반환 (rows, dropped) — dropped 는 왜 빠졌는지별 카운트다. 배너에 그대로 올린다.
    """
    data = (payload or {}).get("data") or {}
    raw = data.get("rows") or data.get("table", {}).get("rows") or []
    rows: list[dict[str, Any]] = []
    drop = dict(fund=0, no_price=0, no_mktcap=0)
    for r in raw:
        name = r.get("name") or ""
        tic = (r.get("symbol") or "").strip().upper()
        if not tic:
            continue
        if is_fund(name, cfg):
            drop["fund"] += 1
            continue
        close = num(r.get("lastsale"))
        mcap = num(r.get("marketCap"))
        if close is None:
            drop["no_price"] += 1
            continue
        if mcap is None:
            drop["no_mktcap"] += 1
        vol = num(r.get("volume"))
        rows.append(
            dict(
                ticker=tic, name=name.strip(), close=close,
                chg_pct=num(r.get("pctchange")), net_change=num(r.get("netchange")),
                volume=vol, mktcap=mcap,
                turnover=(close * vol) if (vol is not None) else None,
                turnover_is_estimate=True,  # 종가×거래량 추정. 실 거래대금이 아니다
                sector_raw=(r.get("sector") or "").strip(),
                industry_raw=(r.get("industry") or "").strip(),
                country=(r.get("country") or "").strip(),
                exchange=(r.get("exchange") or "").strip().upper() or None,
                source=SCREENER_SOURCE,
            )
        )  # fmt: skip
    return rows, drop


def nasdaq_symbol(ticker: str) -> str:
    """나스닥 API 의 심볼 표기. 클래스주는 마침표가 슬래시다 (BRK.B → BRK/B).

    **경로에 넣을 때는 인코딩해야 한다.** 스크리너가 주는 티커 자체가 이미
    `BRK/B` 라서 그대로 붙이면 URL 이 .../quote/BRK/B/historical 로 갈라져
    엉뚱한 경로가 된다 — run #2 에서 이 한 종목만 수집에 실패했다.
    """
    return urllib.parse.quote(ticker.upper().replace(".", "/"), safe="")


def nasdaq_symbol_forms(ticker: str) -> list[str]:
    """클래스주 표기 후보를 순서대로.

    인코딩만으로는 안 됐다 — run #4 에서 BRK/A·BRK/B 가 HTTP 404 였다.
    스크리너는 `BRK/B` 로 주는데 히스토리 쪽이 같은 표기를 받는다는 보장이 없다.
    **추측해서 하나를 고르지 말고 순서대로 물어본다.** 되는 표기가 답이다.
    """
    t = ticker.upper()
    forms = [t, t.replace(".", "/"), t.replace("/", "."), t.replace("/", "-"), t.replace(".", "-")]
    out: list[str] = []
    for f in forms:
        q = urllib.parse.quote(f, safe="")
        if q not in out:
            out.append(q)
    return out


def parse_nasdaq_history(payload: Any) -> list[dict[str, Any]]:
    """나스닥 히스토리 응답 → 오름차순 일봉.

    스크리너와 **같은 호스트**다. 러너에서 stooq 는 빈 응답, yahoo 는 429 를
    주는데 이 호스트는 스크리너가 통과하므로 여기가 가장 확실한 일봉 경로다
    (run #1 실측, D-077).

    날짜는 `MM/DD/YYYY`, 가격은 `$226.80` 꼴이다. 거래량이 빠진 행이 섞여 오므로
    없는 값은 None 으로 둔다 — 0 으로 채우면 거래량 배수가 거짓이 된다.
    """
    tbl = ((payload or {}).get("data") or {}).get("tradesTable") or {}
    rows: list[dict[str, Any]] = []
    for r in tbl.get("rows") or []:
        d = (r.get("date") or "").strip()
        try:
            mm, dd, yy = d.split("/")
            asof = f"{int(yy):04d}-{int(mm):02d}-{int(dd):02d}"
        except (ValueError, AttributeError):
            continue
        close = num(r.get("close"))
        if close is None:
            continue
        rows.append(
            dict(
                asof=asof, open=num(r.get("open")), high=num(r.get("high")),
                low=num(r.get("low")), close=close, volume=num(r.get("volume")),
                source=ROW_SOURCE,
            )
        )  # fmt: skip
    rows.sort(key=lambda r: r["asof"])
    return rows


# ── 클라이언트 ────────────────────────────────────────────────────────────────────────────


def _why(body: bytes) -> str:
    try:
        st = (json.loads(body.decode("utf-8", "replace")) or {}).get("status") or {}
        msg = st.get("bCodeMessage") or st.get("developerMessage")
    except (ValueError, AttributeError):
        msg = None
    return str(msg)[:200] if msg else body[:200].decode("utf-8", "replace")


class NasdaqClient:
    """`NasdaqClient(limiter).screener(cfg)`·`.daily('AAPL', start)`.

    시험은 `transport=` 에 가짜 서버(tests/fakes/nasdaq_server.py)."""

    def __init__(
        self,
        limiter: RateLimiter,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = _BASE,
        priority: Priority = Priority.P3,
        acquire_timeout_s: float | None = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limiter = limiter
        self._priority = priority
        self._acquire_timeout = acquire_timeout_s
        self._clock = clock
        self._http = make_client(base_url, transport=transport, headers=_HEADERS)

    def __repr__(self) -> str:
        return "NasdaqClient()"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> NasdaqClient:
        """Redis 리미터 `rl:nasdaq:<'api' 해시>`(limits.yaml `nasdaq`). 키가 없는 출처다."""
        src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
        limiter = RedisRateLimiter.scoped(redis, LIMITS_SOURCE, "api", src.rate_config(), clock)
        return cls(limiter, **kw)

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: Mapping[str, str], dataset: str) -> tuple[int, bytes]:
        self._limiter.acquire(
            self._priority, f"nasdaq:{dataset.split(':')[0]}", self._acquire_timeout
        )
        try:
            status, body = get_capped(
                self._http, path, params=params, max_bytes=MAX_BYTES, total_s=TOTAL_S,
                clock=self._clock, source=SOURCE, dataset=dataset,
            )  # fmt: skip
        except httpx.HTTPError as e:
            raise NasdaqError(dataset, None, f"전송 실패: {type(e).__name__}") from None
        if status == 429:
            self._limiter.on_rate_limited()
            raise NasdaqThrottled(dataset, status, "429 한도 초과 — 감속")
        return status, body

    def screener_payload(self, exchange: str) -> Any:
        """거래소 하나의 원 응답(JSON). 200 이 아니면 예외."""
        params = dict(
            tableonly="true", limit="10000", offset="0", exchange=exchange, download="true"
        )
        dataset = f"screener:{exchange}"
        status, body = self._get(_SCREENER, params, dataset)
        if status != 200:
            raise NasdaqError(
                dataset, status, f"나스닥 스크리너 {exchange} HTTP {status} — {_why(body)}"
            )
        try:
            return json.loads(body)
        except ValueError as e:
            raise NasdaqError(
                dataset, status, f"나스닥 스크리너 {exchange} 응답이 JSON 이 아니다: {e}"
            ) from None

    def screener(
        self, cfg: Mapping[str, Any], exchanges: Iterable[str] | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """전 종목 스냅샷. 거래소별로 나눠 받아 합친다(ET `screener` 본문 그대로)."""
        out: list[dict[str, Any]] = []
        drop = dict(fund=0, no_price=0, no_mktcap=0)
        seen: set[str] = set()
        for ex in exchanges or cfg["universe"]["exchanges"]:
            rows, d = parse_screener(self.screener_payload(ex), cfg)
            if not rows:
                raise NasdaqError(
                    f"screener:{ex}",
                    200,
                    f"나스닥 스크리너 {ex} 가 0행을 줬다 — 응답 형식 변경 의심",
                )
            for k, v in d.items():
                drop[k] = drop.get(k, 0) + v
            for row in rows:
                if row["ticker"] in seen:
                    continue
                seen.add(row["ticker"])
                row["exchange"] = row.get("exchange") or ex
                out.append(row)
        return out, drop

    def daily(
        self,
        ticker: str,
        start: date | str,
        end: date | str,
        assetclass: str = "stocks",
    ) -> list[dict[str, Any]]:
        """한 종목의 일봉(ET `nasdaq_daily`). ETF(SPY 등)는 assetclass='etf' 로 물어야 한다.

        날짜는 부르는 쪽이 준다(시계 주입 — ET 는 `date.today()`)."""
        s = start.isoformat() if isinstance(start, date) else start
        e = end.isoformat() if isinstance(end, date) else end
        params = dict(assetclass=assetclass, fromdate=s, todate=e, limit="9999")
        dataset = f"history:{ticker}"
        # 대부분은 첫 표기에서 끝난다. 클래스주만 두세 번 더 물어본다.
        last = None
        body = b""
        status = 0
        for sym in nasdaq_symbol_forms(ticker):
            status, body = self._get(_HISTORY.format(sym=sym), params, dataset)
            if status == 200:
                break
            last = f"HTTP {status} ({sym})"
        else:
            raise NasdaqError(
                dataset, status, f"나스닥 히스토리 {ticker} 모든 심볼 표기 실패 — {last}"
            )
        try:
            payload = json.loads(body)
        except ValueError as ex:
            raise NasdaqError(
                dataset, status, f"나스닥 히스토리 {ticker} 응답이 JSON 이 아니다: {ex}"
            ) from None
        rows = parse_nasdaq_history(payload)
        if not rows:
            # 사유는 응답이 들고 있다. 빈 리스트로 돌려주면 '거래가 없었다' 로 읽힌다.
            why = ((payload or {}).get("status") or {}).get("bCodeMessage") or (payload or {}).get(
                "message"
            )
            raise NasdaqError(dataset, status, f"나스닥 히스토리 {ticker} 0행 — {str(why)[:120]}")
        return rows

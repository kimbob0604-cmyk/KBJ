"""가짜 Nasdaq(스크리너·히스토리)·Stooq(CSV) — `httpx.MockTransport`. 합성 데이터만.

Nasdaq·Stooq 원응답과 그것으로 만든 픽스처는 레포에 넣지 않는다(로그인 등급 — CLAUDE.md §2). 응답
모양은 ET `board/us/sources.py` 파서가 읽는 칸만 흉내 낸다(실 응답 칸·단위 [확인 필요 #47]).

Nasdaq(`FakeNasdaq`)
- `GET /api/screener/stocks?exchange=<거래소>&…` → `{"data": {"rows": [...]}}` — `lastsale`
  '$12.34', `pctchange` '1.23%', `volume` '1,234,567', `marketCap` 정수 글자. 거래소별로 나뉜다.
- `GET /api/quote/<심볼>/historical?fromdate=&todate=` →
  `{"data": {"tradesTable": {"rows": [...]}}}` (날짜 MM/DD/YYYY 내림차순 — 실 응답처럼).
  클래스주는 `accept_form` 표기만 200, 나머지 404.
- 모르는 심볼 → 404 + `status.bCodeMessage`. 0행 시계열은 200 + 빈 rows + 사유.
- `status["screener"|"history"] = 코드` 로 그 종류 응답을 바꾼다(429·403 …). `empty_exchanges` 의
  거래소는 0행. 기록 `calls`.

Stooq(`FakeStooq`)
- `GET /q/d/l/?s=<sym>.us&i=d&d1=YYYYMMDD` → CSV(`Date,Open,High,Low,Close,Volume`, 오름차순).
- `blank` 의 심볼은 200 + 빈 본문(ET run #1), 모르는 심볼은 'No data', `limit_hit` 이면 한도 문구.

생성: `FakeNasdaq.synthetic(n, seed, end)`(고정 시드 랜덤워크 + ETF 한 줄),
`FakeNasdaq.from_series(series, snaps)`(합성 세계 `tests/fixtures/synthetic/us/demo.make` 그대로),
`FakeNasdaq.from_world(world)`(P5 합성 세계 `p5_world` 의 미국 유니버스·일봉).
"""

from __future__ import annotations

import random
import threading
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any, Final
from urllib.parse import quote, unquote

import httpx

EXCHANGES: Final = ("NASDAQ", "NYSE", "AMEX")
_SECTORS: Final = (
    ("Technology", "Semiconductors"),
    ("Health Care", "Major Pharmaceuticals"),
    ("Finance", "Major Banks"),
    ("Energy", "Oil & Gas Production"),
    ("Technology", "Computer Software: Prepackaged Software"),
)
END: Final = date(2026, 9, 15)


def _money(v: float | None) -> str:
    return "" if v is None else f"${v:,.2f}"


def _int(v: float | None) -> str:
    return "--" if v is None else f"{round(v):,}"


def _weekdays(end: date, n: int) -> list[date]:
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


class FakeNasdaq:
    def __init__(self) -> None:
        self.screener_rows: dict[str, list[dict[str, Any]]] = {ex: [] for ex in EXCHANGES}
        self.series: dict[str, list[dict[str, Any]]] = {}
        self.accept: dict[str, str] = {}  # 심볼 → 200 을 주는 표기(인코딩 전)
        self.status: dict[str, int] = {}
        self.empty_exchanges: set[str] = set()
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # ── 만들기 ─────────────────────────────────────────────────────────────────────────────
    def add_screener(self, exchange: str, row: Mapping[str, Any]) -> None:
        self.screener_rows.setdefault(exchange, []).append(dict(row))

    def add_series(
        self,
        symbol: str,
        n: int = 300,
        *,
        seed: int = 0,
        end: date = END,
        bars: Sequence[Mapping[str, Any]] | None = None,
        accept_form: str | None = None,
    ) -> None:
        if bars is None:
            rng = random.Random(f"{seed}:{symbol}")  # noqa: S311 — 합성
            px = 20 + rng.random() * 200
            made: list[dict[str, Any]] = []
            for d in _weekdays(end, n):
                px = max(1.0, px * (1 + rng.gauss(0.0005, 0.015)))
                made.append(
                    dict(asof=d.isoformat(), open=px, high=px * 1.01, low=px * 0.99, close=px,
                         volume=float(rng.randint(100_000, 5_000_000)))
                )  # fmt: skip
            bars = made
        self.series[symbol.upper()] = [dict(b) for b in bars]
        self.accept[symbol.upper()] = (accept_form or symbol).upper()

    @classmethod
    def synthetic(cls, n: int = 6, seed: int = 0, end: date = END) -> FakeNasdaq:
        """종목 n 개(거래소 돌아가며) + ETF 한 줄(펀드 제외 확인용) + 일봉 300."""
        f = cls()
        rng = random.Random(seed)  # noqa: S311
        for i in range(n):
            sym = f"SYN{i:02d}"
            ex = EXCHANGES[i % len(EXCHANGES)]
            sec, ind = _SECTORS[i % len(_SECTORS)]
            f.add_series(sym, seed=seed, end=end)
            last = f.series[sym][-1]
            prev = f.series[sym][-2]
            f.add_screener(
                ex,
                dict(symbol=sym, name=f"Synthetic {i} Inc. Common Stock",
                     lastsale=_money(last["close"]),
                     pctchange=f"{(last['close'] / prev['close'] - 1) * 100:.2f}%",
                     netchange=f"{last['close'] - prev['close']:.2f}",
                     volume=_int(last["volume"]),
                     marketCap=str(round(rng.uniform(6e8, 3e11))), sector=sec, industry=ind,
                     country="United States"),
            )  # fmt: skip
        f.add_screener(
            "NASDAQ",
            dict(symbol="SYNF", name="Synthetic Index ETF", lastsale="$50.00", pctchange="0.1%",
                 volume="1,000", marketCap="", sector="", industry=""),
        )  # fmt: skip
        return f

    @classmethod
    def from_series(
        cls,
        series: Mapping[str, Sequence[Mapping[str, Any]]],
        snaps: Mapping[str, Mapping[str, Any]],
    ) -> FakeNasdaq:
        """`demo.make()` 세계 그대로(스크리너는 스냅샷, 히스토리는 일봉). 거래소는 셋에 돌아가며
        나눈다(스크리너가 거래소마다 0행이면 예외 — ET 규칙)."""
        f = cls()
        for i, (sym, bars) in enumerate(series.items()):
            f.add_series(sym, bars=bars)
            s = snaps.get(sym) or {}
            f.add_screener(
                EXCHANGES[i % len(EXCHANGES)],
                dict(symbol=sym, name=s.get("name"), lastsale=_money(s.get("close")),
                     pctchange="" if s.get("chg_pct") is None else f"{s['chg_pct']:.2f}%",
                     volume=_int(s.get("volume")),
                     marketCap="" if s.get("mktcap") is None else str(round(s["mktcap"])),
                     sector=s.get("sector_raw") or "", industry=s.get("industry_raw") or ""),
            )  # fmt: skip
        return f

    @classmethod
    def from_world(cls, world: Any) -> FakeNasdaq:
        """P5 합성 세계(`p5_world`) — 유니버스·종목 일봉(지수는 Yahoo 가짜가 준다)."""
        f = cls()
        for u in world.us_universe:
            bars = [
                dict(asof=b.date.isoformat(), open=b.open, high=b.high, low=b.low, close=b.close,
                     volume=b.volume)
                for b in world.us_bars(u.symbol)
            ]  # fmt: skip
            f.add_series(u.symbol, bars=bars)
            last, prev = bars[-1], bars[-2]
            f.add_screener(
                u.exchange or "NASDAQ",
                dict(symbol=u.symbol, name=f"{u.name} ETF" if u.is_fund else u.name,
                     lastsale=_money(last["close"]),
                     pctchange=f"{(last['close'] / prev['close'] - 1) * 100:.2f}%",
                     volume=_int(last["volume"]),
                     marketCap="" if u.market_cap is None else str(round(u.market_cap)),
                     sector=u.sector or "", industry=u.industry or ""),
            )  # fmt: skip
        return f

    # ── 응답 ───────────────────────────────────────────────────────────────────────────────
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.raw_path.decode().split("?", 1)[0]
        if path == "/api/screener/stocks":
            return self._screener(req)
        parts = path.split("/")
        if len(parts) == 5 and parts[1:3] == ["api", "quote"] and parts[4] == "historical":
            return self._history(req, unquote(parts[3]))
        return httpx.Response(404, json={"message": "no route"})

    def _record(self, **kw: Any) -> None:
        with self._lock:
            self.calls.append(kw)

    def _screener(self, req: httpx.Request) -> httpx.Response:
        ex = req.url.params.get("exchange", "")
        self._record(kind="screener", exchange=ex)
        code = self.status.get("screener")
        if code and code != 200:
            return httpx.Response(code, text="denied")
        rows = [] if ex in self.empty_exchanges else self.screener_rows.get(ex, [])
        return httpx.Response(200, json={"data": {"rows": rows}})

    def _history(self, req: httpx.Request, form: str) -> httpx.Response:
        self._record(kind="history", symbol=form, params=dict(req.url.params))
        code = self.status.get("history")
        if code and code != 200:
            return httpx.Response(code, json={"status": {"bCodeMessage": f"HTTP {code}"}})
        sym = next((s for s, f in self.accept.items() if f == form.upper()), None)
        if sym is None:
            return httpx.Response(404, json={"status": {"bCodeMessage": "Symbol not exists"}})
        lo = req.url.params.get("fromdate", "0000-00-00")
        hi = req.url.params.get("todate", "9999-99-99")
        bars = [b for b in self.series[sym] if lo <= b["asof"] <= hi]
        if not bars:
            return httpx.Response(
                200, json={"data": None, "status": {"bCodeMessage": "No records found"}}
            )
        rows = []
        for b in reversed(bars):
            y, m, d = b["asof"].split("-")
            rows.append(
                dict(date=f"{m}/{d}/{y}", close=_money(b["close"]), open=_money(b.get("open")),
                     high=_money(b.get("high")), low=_money(b.get("low")),
                     volume=_int(b.get("volume")))
            )  # fmt: skip
        return httpx.Response(200, json={"data": {"tradesTable": {"rows": rows}}})


class StubLimiter:
    """리미터 대역 — 부른 횟수·감속 신호만 센다(시간을 쓰지 않는다)."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str]] = []
        self.slowed = 0

    def acquire(self, priority: Any, tr_id: str, timeout: float | None = None) -> None:
        self.calls.append((priority, tr_id))

    def on_rate_limited(self) -> float:
        self.slowed += 1
        return 0.5


class FakeStooq:
    def __init__(self) -> None:
        self.series: dict[str, list[dict[str, Any]]] = {}
        self.blank: set[str] = set()
        self.limit_hit = False
        self.calls: list[dict[str, str]] = []

    @classmethod
    def synthetic(cls, symbols: Iterable[str], seed: int = 0, end: date = END) -> FakeStooq:
        f = cls()
        for sym in symbols:
            nq = FakeNasdaq()
            nq.add_series(sym, seed=seed + 7, end=end)
            f.series[sym.upper()] = nq.series[sym.upper()]
        return f

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        if req.url.path != "/q/d/l/":
            return httpx.Response(404, text="")
        s = req.url.params.get("s", "")
        self.calls.append(dict(req.url.params))
        if self.limit_hit:
            return httpx.Response(200, text="Exceeded the daily hits limit")
        sym = next(
            (k for k in self.series if quote(k.lower().replace(".", "-")) + ".us" == s), None
        )
        if sym is not None and sym in self.blank:
            return httpx.Response(200, text="")
        if sym is None:
            return httpx.Response(200, text="No data")
        d1 = req.url.params.get("d1")
        lines = ["Date,Open,High,Low,Close,Volume"]
        for b in self.series[sym]:
            if d1 and b["asof"].replace("-", "") < d1:
                continue
            lines.append(
                f"{b['asof']},{b['open']:.4f},{b['high']:.4f},{b['low']:.4f},{b['close']:.4f},"
                f"{int(b['volume'] or 0)}"
            )
        return httpx.Response(200, text="\n".join(lines) + "\n")


__all__ = ["END", "EXCHANGES", "FakeNasdaq", "FakeStooq", "StubLimiter"]

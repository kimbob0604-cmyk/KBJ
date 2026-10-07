"""engine 시험용 입력 — 합성 체인·선물 행과 2026-09-28 합성 작은 스냅샷을 engine 입력으로.

- `synthetic_series(...)`: Black-76(r = 0) 가격으로 만든 한 시리즈의 전광판 행(호가 = 이론가 ±
  반 틱) — SYNTHETIC. 스마일 없이 σ 하나, F 는 행사가 패리티로 그대로 나온다
- `fut_row(...)`: 선물 행(주간 전광판 F·야간 단건 CM)
- `MemoryEngineStore`: engine 읽기·쓰기(`data.store.PostgresSink` 의 engine 부분) 메모리 흉내 —
  일별 지표 입력(마지막 사이클 지표·일별 이력·KRX 월물 옵션·선물 정산가)·oi_changes, 플로우 입력
  (ws-gateway 연결 사건·옵션 체결 틱 거래일과 그 앞 engine F 표본·투자자별 최신 행)·KIS REST 원문
  output(선물 지표 — 분봉 조회) 포함
- `krx_option_day(...)`·`krx_settles(...)`: KRX 일별 행 SYNTHETIC — 옵션 종가는 Black-76(r = 0)
  이론가, `IMP_VOLT` 는 그 σ(%), 야간 행(IV 없음)도 함께. 선물 정산가는 주어진 일간 로그수익률로
- `snapshot_cycle(...)`: tests/fixtures/validation 작은 스냅샷(합성 — KBJ P1
  `scripts/make_synthetic_fixtures.py`) 하나를
  `CycleInput` 으로 — 변환은 지표별 검증 리포트와 같은 `scripts.metric_report.snapshot_cycle`
  (전광판·보강 행을 OptionQuote 로 푼 뒤 체인 행으로, 행 시각 = 그 전광판 시각, S_ref 는 스냅샷의
  근월물 — 주간 전광판 F, 야간 파생이면 단건 CM)
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from core.black76 import price as black_price
from core.calendar import TradingCalendar, expiry_at, session_bounds, state_at
from core.forward import time_to_expiry
from data.store import OptionTickRecord, StoreError
from scripts.metric_report import SnapshotCycle
from scripts.metric_report import snapshot_cycle as _snapshot_cycle
from services.bus import SeriesKey
from services.engine.records import (
    LevelRecord,
    MetricRecord,
    OiChangeRecord,
    OptionIvRecord,
    StrikeGexRecord,
)
from services.poller.records import ChainRecord, FuturesRecord, InvestorRecord

KST = ZoneInfo("Asia/Seoul")
KrxOptRow = tuple[date, str, str, str, Decimal, str, Decimal | None, Decimal | None, int | None]
KrxFutRow = tuple[date, str, str, str, Decimal | None]
KisRestRow = tuple[datetime, date | None, str | None, str, dict[str, Any]]
CAL = TradingCalendar.default()
NEAR = "A01612"  # 선물 근월물 F 202612 (fixture futures_board 첫 행)


def kst(d: date, h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=KST)


def _tick(p: float) -> Decimal:
    return Decimal("0.05") if p >= 10 else Decimal("0.01")


def _q(p: float, step: Decimal) -> Decimal:
    return (Decimal(str(p)) / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step


def default_strikes(lo: str = "1050", n: int = 37, step: str = "2.5") -> list[Decimal]:
    """1050.0 ~ 1140.0, 2.5pt 간격 — 선물가 1095 둘레."""
    return [Decimal(lo) + Decimal(step) * i for i in range(n)]


def synthetic_series(
    cls: str,
    expiry: str,
    expiry_date: date,
    at: datetime,
    *,
    F: float = 1095.0,
    sigma: float = 0.25,
    strikes: Iterable[Decimal] | None = None,
    oi: int = 500,
    session: Literal["day", "night"] | None = None,
    trade_date: date | None = None,
    source: Literal["board", "fill"] = "board",
    volume: int | None = 100,
    extra: dict[str, Any] | None = None,
) -> list[ChainRecord]:
    """SYNTHETIC — 한 시리즈의 행사가별 콜·풋 행. 호가는 이론가를 틱으로 반올림한 값 ± 한 틱(3틱
    안이라 §1.1 mid), 단건(fill) 행은 last 만. session·trade_date 를 안 주면 at 의 `state_at`."""
    info = state_at(at, CAL)
    ss = session or info.session or "day"
    td = trade_date or info.trade_date or at.astimezone(KST).date()
    T = time_to_expiry(at, expiry_at(expiry_date))
    ks = list(strikes) if strikes is not None else default_strikes()
    out: list[ChainRecord] = []
    for k in ks:
        for cp in ("C", "P"):
            p = black_price("c" if cp == "C" else "p", F, float(k), T, sigma)
            tick = _tick(p)
            mid = max(_q(p, tick), Decimal("0.01"))
            row: dict[str, Any] = {
                "ts": at,
                "trade_date": td,
                "session": ss,
                "mrkt_cls": cls,
                "expiry": expiry,
                "strike": k,
                "cp": cp,
                "source": source,
                "last": mid,
                "oi": oi,
                "volume": volume,
                "iv_kis": Decimal("25.0"),
            }
            if source == "board":
                row |= {"bid": max(mid - tick, Decimal("0.01")), "ask": mid + tick}
            out.append(ChainRecord.model_validate(row | (extra or {})))
    return out


def fut_row(
    at: datetime,
    price: str = "1095.10",
    *,
    code: str = NEAR,
    session: Literal["day", "night"] | None = None,
    trade_date: date | None = None,
    remaining_days: int = 74,
    quality: Literal["ok", "invalid"] = "ok",
) -> FuturesRecord:
    """선물 한 행 — 주간은 전광판 `F`(board), 야간은 단건 `CM`(single)."""
    info = state_at(at, CAL)
    ss = session or info.session or "day"
    td = trade_date or info.trade_date or at.astimezone(KST).date()
    market, source = ("F", "board") if ss == "day" else ("CM", "single")
    return FuturesRecord(
        ts=at,
        trade_date=td,
        session=ss,
        code=code,
        market=market,
        source=source,
        price=Decimal(price),
        remaining_days=remaining_days,
        quality=quality,
    )


# ── 작은 스냅샷(합성) → engine 입력 ─────────────────────────────────────────────


def snapshot_cycle(
    path: Path,
    *,
    shift: timedelta | None = None,
    fresh_since: datetime | None = None,
    strikes: dict[SeriesKey, Sequence[Decimal]] | None = None,
) -> SnapshotCycle:
    """`scripts.metric_report.snapshot_cycle` 을 시험 캘린더(`CAL`)로 — 검증 리포트와 같은 변환."""
    return _snapshot_cycle(path, shift=shift, fresh_since=fresh_since, strikes=strikes, cal=CAL)


# ── 메모리 engine 저장소 (EngineReader + EngineSink) ──────────────────────────


@dataclass
class MemoryEngineStore:
    """`data.store.PostgresSink` 의 engine 읽기·쓰기 흉내 — 같은 뜻(세션 태그로만, upto 까지 최신
    성한 행, KIS 최종거래일 먼저, 산출은 키로 덮어쓰기 — oi_changes 첫 스냅샷 칸은 덮지 않는다).
    fail_reads·fail_writes 로 실패를 넣는다."""

    chain: list[ChainRecord] = field(default_factory=list[ChainRecord])
    futures: list[FuturesRecord] = field(default_factory=list[FuturesRecord])
    expiries: list[tuple[str, str, date, str, datetime]] = field(
        default_factory=list[tuple[str, str, date, str, datetime]]
    )
    levels: dict[tuple[Any, ...], LevelRecord] = field(
        default_factory=dict[tuple[Any, ...], LevelRecord]
    )
    metrics: dict[tuple[Any, ...], MetricRecord] = field(
        default_factory=dict[tuple[Any, ...], MetricRecord]
    )
    strike_gex: dict[tuple[Any, ...], StrikeGexRecord] = field(
        default_factory=dict[tuple[Any, ...], StrikeGexRecord]
    )
    option_iv: dict[tuple[Any, ...], OptionIvRecord] = field(
        default_factory=dict[tuple[Any, ...], OptionIvRecord]
    )
    oi_changes: dict[tuple[Any, ...], OiChangeRecord] = field(
        default_factory=dict[tuple[Any, ...], OiChangeRecord]
    )
    # KRX 일별 — (거래일, 세션, 상품군, 만기, 행사가, 콜풋, 종가, IMP_VOLT %, 거래량)
    krx_options: list[KrxOptRow] = field(default_factory=list[KrxOptRow])
    # (거래일, 세션, 상품군, 결제월, 정산가)
    krx_futures: list[KrxFutRow] = field(default_factory=list[KrxFutRow])
    krx_iv_reads: list[tuple[date, str]] = field(default_factory=list[tuple[date, str]])
    # ws-gateway 연결 사건(health_events) (시각, 종류)
    ws_events: list[tuple[datetime, str]] = field(default_factory=list[tuple[datetime, str]])
    opt_ticks: list[OptionTickRecord] = field(default_factory=list[OptionTickRecord])
    investor: list[InvestorRecord] = field(default_factory=list[InvestorRecord])
    # KIS REST 원문(raw_messages kis_rest) — (ts, 거래일, 세션, tr_id, 본문)
    kis_rest: list[KisRestRow] = field(default_factory=list[KisRestRow])
    fail_reads: bool = False
    fail_writes: set[str] = field(default_factory=set[str])
    reads: int = 0
    flow_reads: int = 0

    def add_expiry(self, cls: str, expiry: str, d: date, source: str = "kis") -> None:
        self.expiries.append((cls, expiry, d, source, datetime.now(tz=KST)))

    def _read(self) -> None:
        self.reads += 1
        if self.fail_reads:
            raise StoreError("chain_snapshots: OperationalError (시험)")

    def _flow_read(self) -> None:
        """곁일(플로우) 읽기 — 사이클 입력 읽기 수(`reads`)에 세지 않는다."""
        self.flow_reads += 1
        if self.fail_reads:
            raise StoreError("health_events: OperationalError (시험)")

    def chain_max_ts(
        self, trade_date: date, session: str, upto: datetime | None = None
    ) -> datetime | None:
        self._read()
        ts = [
            r.ts
            for r in self.chain
            if (r.trade_date, r.session) == (trade_date, session)
            and r.quality != "invalid"
            and (upto is None or r.ts <= upto)
        ]
        return max(ts, default=None)

    def chain_latest(self, trade_date: date, session: str, upto: datetime) -> list[ChainRecord]:
        self._read()
        best: dict[tuple[Any, ...], ChainRecord] = {}
        for r in self.chain:
            if (r.trade_date, r.session) != (trade_date, session) or r.ts > upto:
                continue
            if r.quality == "invalid":
                continue
            k = (r.mrkt_cls, r.expiry, r.strike, r.cp, r.source)
            if k not in best or best[k].ts < r.ts:
                best[k] = r
        return list(best.values())

    def futures_latest(self, trade_date: date, session: str, upto: datetime) -> list[FuturesRecord]:
        self._read()
        best: dict[tuple[Any, ...], FuturesRecord] = {}
        for r in self.futures:
            if (r.trade_date, r.session) != (trade_date, session) or r.ts > upto:
                continue
            if r.quality == "invalid" or r.price is None:
                continue
            k = (r.market, r.code, r.source)
            if k not in best or best[k].ts < r.ts:
                best[k] = r
        return list(best.values())

    def expiry_dates(self, since: date) -> list[tuple[str, str, date, str]]:
        self._read()
        best: dict[tuple[str, str], tuple[str, str, date, str, datetime]] = {}
        for e in self.expiries:
            k = (e[0], e[1])
            old = best.get(k)
            rank = (e[3] == "kis", e[4])
            if old is None or rank > (old[3] == "kis", old[4]):
                best[k] = e
        return [(c, x, d, s) for c, x, d, s, _ in best.values()]

    def metric_last(
        self, trade_date: date, session: str, metric: str, scope: str, key_prefix: str = ""
    ) -> list[MetricRecord]:
        self._read()
        rows = [
            m
            for m in self.metrics.values()
            if (m.trade_date, m.session, m.metric, m.scope) == (trade_date, session, metric, scope)
            and m.key.startswith(key_prefix)
        ]
        if not rows:
            return []
        last = max(m.ts for m in rows)
        return sorted((m for m in rows if m.ts == last), key=lambda m: m.key)

    def metric_history(
        self, metric: str, scope: str, key: str, first: date, last: date
    ) -> list[MetricRecord]:
        self._read()
        return sorted(
            (
                m
                for m in self.metrics.values()
                if (m.metric, m.scope, m.key) == (metric, scope, key)
                and first <= m.trade_date <= last
            ),
            key=lambda m: (m.trade_date, m.ts),
        )

    def krx_option_days(self, first: date, last: date) -> list[date]:
        self._read()
        return sorted(
            {
                r[0]
                for r in self.krx_options
                if r[1] == "day" and r[2] == "kospi200" and first <= r[0] <= last
            }
        )

    def krx_iv_rows(
        self, trade_date: date, expiry: str
    ) -> list[tuple[Decimal, str, Decimal | None, Decimal | None, int | None]]:
        self._read()
        self.krx_iv_reads.append((trade_date, expiry))
        return sorted(
            (r[4], r[5], r[6], r[7], r[8])
            for r in self.krx_options
            if r[:4] == (trade_date, "day", "kospi200", expiry)
        )

    def krx_futures_settles(self, first: date, last: date) -> list[tuple[date, str, Decimal]]:
        self._read()
        return sorted(
            (d, e, p)
            for d, ss, fam, e, p in self.krx_futures
            if ss == "day" and fam == "kospi200" and p is not None and first <= d <= last
        )

    def ws_connection_events(
        self, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> tuple[str | None, list[tuple[datetime, str]]]:
        self._flow_read()
        mine = sorted(e for e in self.ws_events if e[1] in kinds)
        before = [k for ts, k in mine if ts < start]
        return (before[-1] if before else None), [e for e in mine if start <= e[0] <= end]

    def investor_latest(
        self, trade_date: date, session: str, upto: datetime
    ) -> list[InvestorRecord]:
        self._flow_read()
        best: dict[tuple[str, str, str], InvestorRecord] = {}
        for r in self.investor:
            if (r.trade_date, r.session) != (trade_date, session) or r.ts > upto:
                continue
            if r.quality == "invalid":
                continue
            k = (r.market_code, r.sector_code, r.investor)
            if k not in best or best[k].ts < r.ts:
                best[k] = r
        return [best[k] for k in sorted(best)]

    def kis_rest_outputs(
        self, tr_id: str, after: datetime, upto: datetime, output: str = "output1"
    ) -> list[tuple[datetime, date | None, str | None, dict[str, Any]]]:
        self._flow_read()
        return [
            (ts, d, ss, body[output])
            for ts, d, ss, tr, body in sorted(self.kis_rest, key=lambda r: r[0])
            if tr == tr_id and after < ts <= upto and isinstance(body.get(output), dict)
        ]

    def opt_tick_days(self, before: date, limit: int) -> list[date]:
        self._flow_read()
        return sorted({t.trade_date for t in self.opt_ticks if t.trade_date < before})[-limit:]

    def opt_tick_history(
        self, first: date, last: date, lookback: timedelta
    ) -> list[tuple[date, str, Decimal, int, float]]:
        """틱마다 그 틱 앞 lookback 안의 같은 종목 가장 최근 engine F(option_iv) — 없으면 뺀다."""
        self._flow_read()
        out: list[tuple[date, str, Decimal, int, float]] = []
        for t in sorted(self.opt_ticks, key=lambda t: (t.trade_date, t.ts)):
            if not first <= t.trade_date <= last or not t.qty or t.mrkt_cls is None:
                continue
            if t.expiry is None or t.strike is None or t.cp is None:
                continue
            fs = [
                (o.ts, o.forward)
                for o in self.option_iv.values()
                if (o.mrkt_cls, o.expiry, o.strike, o.cp) == (t.mrkt_cls, t.expiry, t.strike, t.cp)
                and t.ts - lookback < o.ts <= t.ts
                and o.forward is not None
            ]
            if fs:
                out.append((t.trade_date, t.cp, t.strike, t.qty, max(fs)[1]))
        return out

    def _write(self, table: str) -> None:
        if table in self.fail_writes:
            raise StoreError(f"{table}: 시험 실패")

    def write_levels(self, rows: Sequence[LevelRecord]) -> None:
        self._write("levels")
        self.levels |= {(r.ts, r.scope, r.name): r for r in rows}

    def write_metrics(self, rows: Sequence[MetricRecord]) -> None:
        self._write("metrics")
        self.metrics |= {(r.ts, r.metric, r.scope, r.key): r for r in rows}

    def write_strike_gex(self, rows: Sequence[StrikeGexRecord]) -> None:
        self._write("strike_gex")
        self.strike_gex |= {(r.ts, r.mrkt_cls, r.expiry, r.strike): r for r in rows}

    def write_option_iv(self, rows: Sequence[OptionIvRecord]) -> None:
        self._write("option_iv")
        self.option_iv |= {(r.ts, r.mrkt_cls, r.expiry, r.strike, r.cp): r for r in rows}

    def write_oi_changes(self, rows: Sequence[OiChangeRecord]) -> None:
        """첫 스냅샷 칸(증감 None)은 저장된 칸을 덮지 않는다(`OI_CHANGES.keep_if_null`)."""
        self._write("oi_changes")
        for r in rows:
            k = (r.ts, r.mrkt_cls, r.expiry, r.strike, r.cp)
            if r.change is not None or k not in self.oi_changes:
                self.oi_changes[k] = r


# ── KRX 일별 (SYNTHETIC) ─────────────────────────────────────────────────────


def krx_option_day(
    d: date,
    expiry: str,
    expiry_date: date,
    *,
    F: float = 1095.0,
    sigma: float = 0.20,
    strikes: Iterable[Decimal] | None = None,
    volume: int = 10,
) -> list[KrxOptRow]:
    """SYNTHETIC — 그날 코스피200 월물 한 만기의 KRX 일별 행. 정규 행: 종가 = 주간 끝(15:45) 기준
    Black-76 이론가(0.01 반올림), IMP_VOLT = σ·100, 거래량 volume. 야간 행: IV·종가 없음."""
    T = time_to_expiry(session_bounds(d, "day")[1], expiry_at(expiry_date))
    iv = Decimal(f"{sigma * 100:.4f}")
    out: list[KrxOptRow] = []
    for k in strikes if strikes is not None else default_strikes():
        for cp in ("C", "P"):
            p = black_price("c" if cp == "C" else "p", F, float(k), T, sigma)
            close = Decimal(f"{p:.2f}") if p >= 0.01 else None
            out.append((d, "day", "kospi200", expiry, k, cp, close, iv, volume))
            out.append((d, "night", "kospi200", expiry, k, cp, None, None, 0))
    return out


def krx_settles(
    days: Sequence[date], contract: str, returns: Sequence[float], p0: float = 1100.0
) -> list[KrxFutRow]:
    """SYNTHETIC — 거래일들의 코스피200 선물 정산가(주간 행) — 둘째 날부터 주어진 일간
    로그수익률."""
    if len(returns) != len(days) - 1:
        raise ValueError("returns 는 days 보다 하나 적다")
    out: list[KrxFutRow] = []
    p = p0
    for i, d in enumerate(days):
        if i:
            p *= math.exp(returns[i - 1])
        out.append((d, "day", "kospi200", contract, Decimal(f"{p:.6f}")))
        out.append((d, "night", "kospi200", contract, None))
    return out

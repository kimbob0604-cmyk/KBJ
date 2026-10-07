"""engine 일별 지표 — `POST_DAY` 한 번 (docs/phase3_design.md §1 평가 주기, metrics §5.4·§5.5).

부작용 없다(입력 읽기·저장·발행은 services/engine/service.py). 계산은 `core.metrics.vol`.

| 지표(`metrics.metric`, 범위 all·key '') | 값 | metrics.md |
|---|---|---|
| `atm_iv_daily` | 그날 주간 마감 기준 월물 ATM IV(연율) — IV 랭크의 일별 이력 | §5.4 입력 |
| `iv_rank` | IV 랭크 (x − min)/(max − min) | §5.4 |
| `iv_percentile` | IV 퍼센타일 #{xᵢ < x}/n | §5.4 |
| `iv_hv` | ATM IV − HV20(연율) | §5.5 |

- 행의 ts = 그 거래일 주간 끝(15:45 KST), trade_date = 그 거래일, session = day — 같은 날을 다시
  계산하면 같은 키(DO UPDATE)
- 오늘 값(`atm_iv_daily`, source self): 그날 주간 마지막 사이클의 월물 `atm_iv`(scope series) 중
  그날 가장 가까운 월물(`nearest_monthly` — KIS 최종거래일 먼저, 최종거래일이 그날보다 뒤인 첫
  결제월: 15:20 뒤 만기 지난 월물은 빠진다)의 행. 그 행이 없으면(마지막 사이클에서 그 시리즈가
  실패 등) 다음 월물로 넘어가지 않고 invalid(`nearest_monthly_missing` — 그날은 다음 날 KRX 로
  백필한다). 그 사이클 시각이 주간 끝보다 90초(REST 기준, metrics §0) 넘게 앞이면 stale
  (`close_stale`) [확인 필요]
- 과거 값: 창(오늘 포함 252거래일) 안에서 그날 값이 정해지지 않은 날(`daily_settled` — 행이 없거나
  자체 값이 invalid)은 KRX 일별(`krx_opt_daily` 정규 행·`krx_fut_daily` 정산가)로 계산해
  `atm_iv_daily`(source krx — 같은 키라 자체 invalid 행을 덮는다)로 저장한다 — 다음 날부터는 다시
  계산하지 않는다(KRX 로 값을 못 낸 날도 source krx invalid 행으로 남긴다). KRX 는 다음 날 08:00 에
  내므로 오늘 것은 없다
- IV 랭크·퍼센타일·IV − HV 는 오늘 값이 없으면(invalid) null·invalid(`no_today`)
- 지표 하나의 예외는 그 지표만 invalid + health(`engine_metric_failed`). 플래그 off 는 계산·저장하지
  않는다(`atm_iv_daily` 가 off 면 이력을 남기지 않는다 — 그날 랭크엔 쓴다)
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from core.calendar import TradingCalendar, monthly_expiry, session_bounds
from core.metrics.vol import (
    DailyIv,
    IvSource,
    KrxIvRow,
    Settlement,
    iv_minus_hv,
    iv_rank,
    krx_atm_iv,
    realized_vol,
)
from core.preprocess import Quality, worst
from services.engine.evaluate import EngineHealth
from services.engine.records import MetricRecord
from services.engine.registry import Flag, resolve_flag

DAILY_ATM = "atm_iv_daily"
IV_RANK = "iv_rank"
IV_PERCENTILE = "iv_percentile"
IV_HV = "iv_hv"
DAILY_METRICS = (DAILY_ATM, IV_RANK, IV_PERCENTILE, IV_HV)
# 마감 사이클이 주간 끝보다 이만큼 넘게 앞이면 stale (metrics §0 REST 90초) [확인 필요]
CLOSE_STALE_S = 90.0
_SOURCES: frozenset[str] = frozenset({"self", "krx"})


def daily_ts(trade_date: date) -> datetime:
    """일별 행의 ts — 그 거래일 주간 끝(15:45 KST)."""
    return session_bounds(trade_date, "day")[1]


def daily_settled(r: MetricRecord) -> bool:
    """저장된 `atm_iv_daily` 행이 그날 값을 정했나 — 그러면 KRX 로 다시 계산하지 않는다. 값이 있고
    invalid 가 아니거나(자체·KRX), KRX 로 계산한 행(값을 못 냈어도 — 같은 입력이라 다시 해도
    같다). 자체 값이 invalid 인 날(마감 사이클 없음 등 — 그날 KRX 는 다음 날 08:00 에 난다)과
    원천을 모르는 행은 정해지지 않았다."""
    if r.payload.get("source") == "krx":
        return True
    return r.value is not None and r.quality != "invalid"


def monthly_last_trade(code: str, kis: Mapping[str, date], cal: TradingCalendar) -> date | None:
    """결제월 YYYYMM 의 최종거래일 — 같은 결제월 월물 옵션의 KIS 최종거래일(`series_expiries`),
    없으면 캘린더(둘째 목요일, 휴장이면 앞 거래일). 코드가 결제월 꼴이 아니면 None."""
    if code in kis:
        return kis[code]
    if len(code) != 6 or not code.isdigit() or not 1 <= int(code[4:]) <= 12:
        return None
    return monthly_expiry(int(code[:4]), int(code[4:]), cal)


def nearest_monthly(d: date, kis: Mapping[str, date], cal: TradingCalendar) -> str:
    """그날 주간 마감 기준 가장 가까운 월물 — 최종거래일이 그날보다 뒤인 첫 결제월(만기일 15:20 에
    만기 지난 월물은 마감 기준에서 빠진다)."""
    y, m = d.year, d.month
    for _ in range(24):
        code = f"{y:04d}{m:02d}"
        last = monthly_last_trade(code, kis, cal)
        if last is not None and last > d:
            return code
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    raise RuntimeError(f"{d} 뒤 2년 안에 월물 만기가 없다 — 캘린더를 확인하라")


def near_settle(
    d: date, prices: Mapping[tuple[date, str], Decimal], last_trade: Mapping[str, date]
) -> Decimal | None:
    """그날 근월물(최종거래일이 그날 이후인 결제월 중 가장 이른 것) 선물 정산가 — KRX 일별 ATM
    기준."""
    cands = [
        (last_trade[c], c)
        for (day, c) in prices
        if day == d and c in last_trade and last_trade[c] >= d
    ]
    if not cands:
        return None
    return prices[(d, min(cands)[1])]


def krx_rows(
    raw: Sequence[tuple[Decimal, str, Decimal | None, Decimal | None, int | None]],
) -> list[KrxIvRow]:
    """`krx_iv_rows` 행 → KrxIvRow. 형식이 틀린 행(음수 IV 등)은 뺀다 — 그 행사가만 없는 것으로."""
    out: list[KrxIvRow] = []
    for k, cp, close, iv, vol in raw:
        if cp not in ("C", "P"):
            continue
        try:
            out.append(
                KrxIvRow(
                    k,
                    "C" if cp == "C" else "P",
                    close,
                    None if iv is None else float(iv),
                    vol or 0,
                )
            )
        except (TypeError, ValueError):
            continue
    return out


@dataclass(frozen=True)
class KrxDay:
    """일별 값이 없는 지난 거래일 하나의 KRX 입력 — 그날 가장 가까운 월물(만기일이 그날보다 뒤) 정규
    행과 근월물 선물 정산가(ATM 기준, 없으면 None)."""

    trade_date: date
    expiry: str
    rows: Sequence[KrxIvRow]
    s_ref: Decimal | None


@dataclass(frozen=True)
class DailyInput:
    """일별 계산 입력 — 서비스가 DB 에서 읽어 넘긴다.

    close: 그날 주간 마지막 사이클의 월물 `atm_iv` 행들. history: 창 안 지난 `atm_iv_daily` 행(오늘
    것 제외). krx_days: `atm_iv_daily` 가 없는 지난 날의 KRX 입력. settles: KRX 근월물 계열 선물
    정산가(HV·KRX ATM 기준). futures_last_trade: 선물 결제월 → 최종거래일. option_last_trade:
    월물 옵션 결제월 → KIS 최종거래일(`series_expiries` — 없는 결제월은 캘린더, 오늘 가장 가까운
    월물 고르기).
    """

    trade_date: date
    close: Sequence[MetricRecord] = ()
    history: Sequence[MetricRecord] = ()
    krx_days: Sequence[KrxDay] = ()
    settles: Sequence[Settlement] = ()
    futures_last_trade: Mapping[str, date] = field(default_factory=dict[str, date])
    option_last_trade: Mapping[str, date] = field(default_factory=dict[str, date])


@dataclass(frozen=True)
class DailyResult:
    trade_date: date
    ts: datetime
    metrics: tuple[MetricRecord, ...]
    health: tuple[EngineHealth, ...]
    today: DailyIv | None


class _Daily:
    def __init__(
        self, inp: DailyInput, cal: TradingCalendar, flags: Mapping[str, Flag] | None
    ) -> None:
        self.inp = inp
        self.cal = cal
        self.flags = flags
        self.ts = daily_ts(inp.trade_date)
        self.records: list[MetricRecord] = []
        self.health: list[EngineHealth] = []

    def run(
        self,
        name: str,
        fn: Callable[[], tuple[float | None, Quality, dict[str, Any]]],
        *,
        day: date | None = None,
        keep_when_off: bool = False,
        base: Mapping[str, Any] | None = None,
    ) -> None:
        """지표 한 행. 플래그 off 면 부르지 않는다 — keep_when_off 면 불러(일별 이력은 랭크의 입력)
        저장만 하지 않는다. 예외는 그 행만 invalid + health(같은 지표는 한 묶음) — 그 행 payload 에
        base(원천 등 — `daily_settled` 가 본다)를 남긴다."""
        flag = resolve_flag(name, self.flags)
        if flag == "off" and not keep_when_off:
            return
        d = self.inp.trade_date if day is None else day
        stamp: dict[str, Any] = {"ts": daily_ts(d), "trade_date": d, "session": "day"}
        try:
            value, quality, payload = fn()
            rec = MetricRecord(
                **stamp,
                metric=name,
                scope="all",
                value=value,
                payload=payload,
                quality=quality,
                flag=flag,
            )
        except Exception as e:  # 지표 하나 — 그것만 invalid
            self.health.append(
                EngineHealth(
                    "engine_metric_failed",
                    "warning",
                    f"{name}/{d.isoformat()}: {type(e).__name__}: {e}"[:250],
                    subject=name,
                )
            )
            rec = MetricRecord(
                **stamp,
                metric=name,
                scope="all",
                value=None,
                payload={**(base or {}), "error": type(e).__name__},
                quality="invalid",
                flag=flag,
            )
        if flag != "off":
            self.records.append(rec)

    # ── 오늘 값 ──

    def today(self) -> DailyIv | None:
        """그날 주간 마감 월물 ATM IV — 그날 가장 가까운 월물(`nearest_monthly`)의 행만. 그 행이
        없으면(마지막 사이클에서 그 시리즈가 실패 등) 다음 월물로 넘어가지 않고 invalid."""
        box: list[DailyIv] = []

        def fn() -> tuple[float | None, Quality, dict[str, Any]]:
            rows = [r for r in self.inp.close if r.key.startswith("M:")]
            if not rows:
                return None, "invalid", {"source": "self", "reasons": ["no_close"]}
            inp = self.inp
            series = f"M:{nearest_monthly(inp.trade_date, inp.option_last_trade, self.cal)}"
            mine = [r for r in rows if r.key == series]
            if not mine:
                why = ["nearest_monthly_missing"]
                return None, "invalid", {"source": "self", "series": series, "reasons": why}
            r = max(mine, key=lambda r: r.ts)
            reasons: list[str] = []
            quality = r.quality
            if (self.ts - r.ts).total_seconds() > CLOSE_STALE_S:
                reasons.append("close_stale")
                quality = worst(quality, "stale")
            payload = {
                "source": "self",
                "series": r.key,
                "as_of": r.ts.isoformat(),
                "reasons": reasons,
            }
            if r.value is not None and quality != "invalid":
                box.append(DailyIv(self.inp.trade_date, r.value, "self", quality))
            return r.value, quality, payload

        self.run(DAILY_ATM, fn, keep_when_off=True, base={"source": "self"})
        return box[0] if box else None

    # ── KRX 백필 ──

    def backfill(self) -> list[DailyIv]:
        out: list[DailyIv] = []
        for k in sorted(self.inp.krx_days, key=lambda k: k.trade_date):

            def fn(k: KrxDay = k) -> tuple[float | None, Quality, dict[str, Any]]:
                payload: dict[str, Any] = {"source": "krx", "series": f"M:{k.expiry}"}
                if k.s_ref is None:
                    return None, "invalid", payload | {"reasons": ["no_s_ref"]}
                got = krx_atm_iv(k.rows, k.s_ref)
                payload |= {
                    "s_ref": float(k.s_ref),
                    "forward": got.forward.F,
                    "forward_quality": got.forward.quality,
                    "strikes": [float(x) for x in got.atm.strikes],
                    "reasons": [*got.forward.reasons, *got.atm.reasons],
                }
                if got.atm.value is not None:
                    out.append(DailyIv(k.trade_date, got.atm.value, "krx", got.atm.quality))
                return got.atm.value, got.atm.quality, payload

            base = {"source": "krx", "series": f"M:{k.expiry}"}
            self.run(DAILY_ATM, fn, day=k.trade_date, keep_when_off=True, base=base)
        return out

    def history(self) -> list[DailyIv]:
        """저장된 `atm_iv_daily`(값 있고 invalid 아닌 행) — 날마다 가장 늦은 행."""
        latest: dict[date, MetricRecord] = {}
        for r in self.inp.history:
            if r.trade_date >= self.inp.trade_date:
                continue
            old = latest.get(r.trade_date)
            if old is None or r.ts > old.ts:
                latest[r.trade_date] = r
        out: list[DailyIv] = []
        for d, r in sorted(latest.items()):
            src = r.payload.get("source")
            v = r.value
            if v is None or not (math.isfinite(v) and v > 0):
                continue
            if r.quality == "invalid" or src not in _SOURCES:
                continue
            source: IvSource = "self" if src == "self" else "krx"
            out.append(DailyIv(d, v, source, r.quality))
        return out

    # ── 랭크·IV − HV ──

    def rank(self, today: DailyIv | None, history: Sequence[DailyIv]) -> None:
        box: list[Any] = []

        def compute() -> Any:
            if not box:
                box.append(None if today is None else iv_rank(history, today, self.cal))
            return box[0]

        def payload_of(r: Any) -> dict[str, Any]:
            return {
                "n": r.n,
                "start": r.start.isoformat(),
                "end": r.end.isoformat(),
                "sources": list(r.sources),
                "reasons": list(r.reasons),
            }

        def rank_fn() -> tuple[float | None, Quality, dict[str, Any]]:
            r = compute()
            if r is None:
                return None, "invalid", {"reasons": ["no_today"]}
            return r.rank, r.quality, payload_of(r)

        def pct_fn() -> tuple[float | None, Quality, dict[str, Any]]:
            r = compute()
            if r is None:
                return None, "invalid", {"reasons": ["no_today"]}
            return r.percentile, r.quality, payload_of(r)

        self.run(IV_RANK, rank_fn)
        self.run(IV_PERCENTILE, pct_fn)

    def iv_hv(self, today: DailyIv | None) -> None:
        def fn() -> tuple[float | None, Quality, dict[str, Any]]:
            hv = realized_vol(
                self.inp.settles,
                self.inp.futures_last_trade,
                self.cal,
                end=self.inp.trade_date,
            )
            x = iv_minus_hv(
                None if today is None else today.value,
                "invalid" if today is None else today.quality,
                hv,
            )
            payload = {
                "atm_iv": x.atm_iv,
                "hv20": hv.value,
                "hv_end": None if hv.end is None else hv.end.isoformat(),
                "contracts": sorted(set(hv.contracts)),
                "missing": [d.isoformat() for d in hv.missing],
                "reasons": [*hv.reasons, *([] if today is not None else ["no_today"])],
            }
            return x.value, x.quality, payload

        self.run(IV_HV, fn)


def evaluate_daily(
    inp: DailyInput, *, cal: TradingCalendar, flags: Mapping[str, Flag] | None = None
) -> DailyResult:
    """그날의 일별 지표 — 예외를 올리지 않는다(지표 단위로 격리)."""
    run = _Daily(inp, cal, flags)
    today = run.today()
    krx = run.backfill()
    stored = run.history()
    have = {h.trade_date for h in stored}
    history = [*stored, *(k for k in krx if k.trade_date not in have)]
    run.rank(today, history)
    run.iv_hv(today)
    return DailyResult(inp.trade_date, run.ts, tuple(run.records), tuple(run.health), today)

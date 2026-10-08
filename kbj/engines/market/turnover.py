"""시장 거래대금(docs/metrics.md §1·§6.1, docs/p3_design.md §4.4).

- 확정: 그날 코스피 + 코스닥 **주식**(`STOCK_KINDS` — common·pref·spac [확인 필요 — 스팩]) 거래대금
  합(원). ETF·ETN·리츠는 넣지 않는다(레버리지 ETF 가 시장 거래대금을 부풀린다 — metrics §4-5).
  원장(원천 우선순위 krx > kis)에서 계산하고 invalid 행은 빼고 수를 센다. as_of = 그날 15:30 KST.
- 장중: 코스피(0001)·코스닥(1001) 지수 누적 거래대금 합(KIS 지수 현재가 10분 슬롯) — quality 는
  항상 estimated, as_of = 슬롯 끝. 지수 거래대금에 ETF 가 드는지 [실측 필요] — 확정값과 정의가 다를
  수 있어 화면은 '장중(지수 기준)' 으로 따로 표시한다(R23).
- 20일 평균 대비: 오늘 ÷ 직전 20영업일 평균. 직전 영업일이 20일 미만이면 있는 날로 나누고 n 을 낸다.
- 거래소: 실측 전에는 KRX 구분만(D-P3-9) — "NXT 미포함" 꼬리표는 응답(readers)이 단다.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from kbj.core.quality import Quality, Sourced
from kbj.core.rows import IndexQuote
from kbj.engines.flows.ledger import (
    MAIN_MARKETS,
    STOCK_KINDS,
    Ledger,
    LedgerRow,
    close_as_of,
    source_label,
    worst_quality,
)

__all__ = [
    "INTRADAY_INDEX_CODES",
    "DayValue",
    "TurnoverCoverage",
    "TurnoverRatio",
    "intraday_market_turnover",
    "market_turnover",
    "turnover_coverage",
    "turnover_ratio",
    "turnover_series",
]

INTRADAY_INDEX_CODES: Final[tuple[str, ...]] = ("0001", "1001")  # 코스피·코스닥 [실측 필요 #24]
SLOT_MINUTES: Final = 10


@dataclass(frozen=True)
class DayValue:
    date: date
    value: int | None
    quality: Quality | None
    source: str


@dataclass(frozen=True)
class TurnoverCoverage:
    """그날 시장 거래대금에 넣은·뺀 행 수 — 응답 notes 용."""

    n_rows: int  # 그날 원장 행 전체
    n_used: int
    n_invalid: int
    n_missing_turnover: int
    n_unknown_kind: int
    n_other_kind: int  # ETF·ETN·리츠 등(따로 집계)
    n_other_market: int  # 코넥스·모름


@dataclass(frozen=True)
class TurnoverRatio:
    today: Sourced[int] | None
    avg_prior: float | None
    ratio: float | None
    n_prior: int  # 평균에 쓴 직전 영업일 수(≤ n)


def _in_scope(r: LedgerRow, markets: Collection[str], kinds: Collection[str]) -> bool:
    return r.market in markets and r.kind in kinds


def turnover_coverage(
    ledger: Ledger,
    day: date,
    *,
    markets: Collection[str] = MAIN_MARKETS,
    kinds: Collection[str] = STOCK_KINDS,
) -> TurnoverCoverage:
    rows = ledger.day(day)
    used = invalid = missing = unknown_kind = other_kind = other_market = 0
    for r in rows:
        if r.market not in markets:
            other_market += 1
        elif r.kind is None:
            unknown_kind += 1
        elif r.kind not in kinds:
            other_kind += 1
        elif not r.usable:
            invalid += 1
        elif r.turnover is None:
            missing += 1
        else:
            used += 1
    return TurnoverCoverage(
        len(rows), used, invalid, missing, unknown_kind, other_kind, other_market
    )


def market_turnover(
    ledger: Ledger,
    day: date,
    *,
    markets: Collection[str] = MAIN_MARKETS,
    kinds: Collection[str] = STOCK_KINDS,
) -> Sourced[int] | None:
    """확정 시장 거래대금(원). 그날 쓸 행이 하나도 없으면 None(0 으로 내지 않는다)."""
    used = [
        r
        for r in ledger.day(day)
        if _in_scope(r, markets, kinds) and r.usable and r.turnover is not None
    ]
    if not used:
        return None
    return Sourced[int](
        value=sum(r.turnover for r in used if r.turnover is not None),
        source=source_label(r.sources.get("turnover", "") for r in used),
        as_of=close_as_of(day),
        quality=worst_quality(r.quality for r in used) or Quality.INVALID,
    )


def intraday_market_turnover(
    quotes: Iterable[IndexQuote],
    *,
    codes: Collection[str] = INTRADAY_INDEX_CODES,
    slot_minutes: int = SLOT_MINUTES,
) -> Sourced[int] | None:
    """가장 최근 슬롯 중 지정 지수가 **모두** 있는 슬롯의 누적 거래대금 합 — estimated.

    한 지수라도 빠진 슬롯은 쓰지 않는다(코스피만 더한 값을 시장 값으로 내지 않는다).
    """
    by_ts: dict[datetime, dict[str, IndexQuote]] = {}
    for q in quotes:
        if q.code in codes and q.quality.usable and q.turnover is not None:
            by_ts.setdefault(q.ts, {})[q.code] = q
    full = [ts for ts, got in by_ts.items() if set(codes) <= set(got)]
    if not full:
        return None
    ts = max(full)
    got = [by_ts[ts][c] for c in codes]
    return Sourced[int](
        value=sum(q.turnover for q in got if q.turnover is not None),
        source=source_label(q.source for q in got),
        as_of=ts + timedelta(minutes=slot_minutes),
        quality=Quality.ESTIMATED,
    )


def turnover_series(
    ledger: Ledger,
    end: date,
    n: int = 20,
    *,
    markets: Collection[str] = MAIN_MARKETS,
    kinds: Collection[str] = STOCK_KINDS,
) -> list[DayValue]:
    """end 이하 최근 n 영업일의 확정 시장 거래대금(오름차순). 값이 없는 날은 value None."""
    out: list[DayValue] = []
    for d in ledger.days_upto(end, n):
        v = market_turnover(ledger, d, markets=markets, kinds=kinds)
        out.append(
            DayValue(d, None, None, "") if v is None else DayValue(d, v.value, v.quality, v.source)
        )
    return out


def turnover_ratio(
    ledger: Ledger,
    day: date,
    n: int = 20,
    *,
    today: Sourced[int] | None = None,
    markets: Collection[str] = MAIN_MARKETS,
    kinds: Collection[str] = STOCK_KINDS,
) -> TurnoverRatio:
    """오늘 ÷ 직전 n 영업일 평균. `today` 를 주면(장중 지수 기준 값 등) 그것을 분자로 쓴다.

    직전 영업일은 원장에서 day 보다 앞선 영업일 — day 가 원장 마지막 날 뒤여도(장중) 된다.
    """
    cur = today if today is not None else market_turnover(ledger, day, markets=markets, kinds=kinds)
    prior_days = [d for d in ledger.trading_days if d < day][-n:]
    vals = [
        v.value
        for d in prior_days
        if (v := market_turnover(ledger, d, markets=markets, kinds=kinds)) is not None
    ]
    avg = sum(vals) / len(vals) if vals else None
    ratio = cur.value / avg if cur is not None and avg is not None and avg > 0 else None
    return TurnoverRatio(today=cur, avg_prior=avg, ratio=ratio, n_prior=len(vals))

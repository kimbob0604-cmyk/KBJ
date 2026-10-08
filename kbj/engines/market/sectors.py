"""업종 히트맵 — KRX 업종지수(docs/metrics.md §6.3, docs/p3_design.md §4.4).

SD `server.py:_scrape_naver_sectors`:3232(네이버 스크랩)의 대체. 셀은 `config/markets.yaml`
`market.sector_indices` 의 업종지수 코드 — 실측 전에는 비어 있고 화면은 '준비 중'이다
(지어내지 않는다).

- 색 = 기간(1·5·20영업일) 등락률(%) — 업종지수 종가 기준. 크기 = 업종지수 거래대금(원).
- 장중(그날 일봉이 아직 없고 장중 시세가 있으면): 현재값 = 마지막 장중 시세(estimated). 1일 등락률은
  장중 시세의 등락률을 그대로 쓰고, 5·20일은 현재값 ÷ (period) 영업일 전 종가.
- 마감 뒤: 마지막 일봉 종가 ÷ (period) 영업일 전 종가(일봉 품질 — 보통 ok).
- 자료가 없거나 기간이 모자라면 그 셀은 값 None·quality invalid·note — 0 으로 그리지 않는다.

이 board48 섹터 집계(페이지 2)와는 다른 축이다(ADR 0001 Q6) — 섞지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from kbj.core.quality import Quality
from kbj.core.rows import IndexBar, SectorQuote
from kbj.engines.flows.ledger import close_as_of, source_label

__all__ = ["PERIODS", "SectorCell", "index_return_pct", "sector_heat"]

PERIODS: Final[tuple[int, ...]] = (1, 5, 20)


@dataclass(frozen=True)
class SectorCell:
    code: str
    name: str | None
    market: str | None
    chg_pct: float | None
    turnover: int | None
    quality: Quality
    source: str
    as_of: datetime | None
    note: str | None = None


def index_return_pct(bars: Sequence[IndexBar], day: date, period: int) -> float | None:
    """지수 일봉(오름차순)에서 day 종가 ÷ period 봉 전 종가 − 1(%). 봉이 모자라면 None."""
    usable = [b for b in bars if b.date <= day and b.quality.usable and b.close is not None]
    if not usable or usable[-1].date != day or len(usable) < period + 1:
        return None
    cur, base = usable[-1].close, usable[-1 - period].close
    if cur is None or base is None or base <= 0:
        return None
    return (cur / base - 1) * 100


def _missing(code: str, name: str | None, market: str | None, why: str) -> SectorCell:
    return SectorCell(code, name, market, None, None, Quality.INVALID, "", None, why)


def sector_heat(
    index_bars: Mapping[str, Sequence[IndexBar]],
    intraday: Iterable[SectorQuote],
    codes: Sequence[str],
    period: int,
    *,
    names: Mapping[str, str] | None = None,
    slot_minutes: int = 10,
) -> list[SectorCell]:
    """업종지수 셀 목록(codes 순서). codes 가 비면 빈 목록(화면 '준비 중')."""
    if period not in PERIODS:
        raise ValueError(f"period 는 {PERIODS} 중 하나: {period!r}")
    latest: dict[str, SectorQuote] = {}
    for q in intraday:
        if q.code in codes and q.quality.usable and q.value is not None:
            cur = latest.get(q.code)
            if cur is None or q.ts > cur.ts:
                latest[q.code] = q
    out: list[SectorCell] = []
    for code in codes:
        bars = [
            b
            for b in sorted(index_bars.get(code, ()), key=lambda b: b.date)
            if b.quality.usable and b.close is not None
        ]
        q = latest.get(code)
        name = (names or {}).get(code) or (q.name if q is not None else None)
        market = q.market if q is not None else None
        live = q is not None and (not bars or q.ts.date() > bars[-1].date)
        if live and q is not None:
            if period == 1:
                chg = q.chg_pct
            else:
                base = bars[-period].close if len(bars) >= period else None
                chg = (q.value / base - 1) * 100 if q.value and base else None
            if chg is None:
                out.append(_missing(code, name, market, f"{period}일 전 종가 없음"))
                continue
            out.append(
                SectorCell(
                    code, name, market, chg, q.turnover, Quality.ESTIMATED,
                    source_label([q.source]), q.ts + timedelta(minutes=slot_minutes),
                )
            )  # fmt: skip
            continue
        if not bars:
            out.append(_missing(code, name, market, "업종지수 자료 없음"))
            continue
        last = bars[-1]
        chg = index_return_pct(bars, last.date, period)
        if chg is None:
            out.append(_missing(code, name or last.name, market, f"{period}일 전 종가 없음"))
            continue
        out.append(
            SectorCell(
                code, name or last.name, market, chg, last.turnover, last.quality,
                source_label([last.source]), close_as_of(last.date),
            )
        )  # fmt: skip
    return out

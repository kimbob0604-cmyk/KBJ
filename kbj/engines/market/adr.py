"""시장 체력 — ADR(등락비율)·지수 이격도(stock-dashboard 위험지표 화면을 옮김, 2026-11-02).

- ADR = 최근 20거래일 상승 종목 수 합 ÷ 하락 종목 수 합 × 100(시장별). 120 이상 과열, 80 이하
  바닥권으로 흔히 읽는다. 하루 상승·하락은 그 종목의 전 거래일 종가 대비(KRX 비수정 일봉 — 분할
  당일은 한 종목이 하루 잘못 셀 수 있다). 전 거래일 봉이 없는 종목(신규 상장·거래정지)은
  그날 세지 않는다.
- 이격도 = 지수 종가 ÷ N일 이동평균 × 100(N = 25·50). 100 위면 평균보다 높게 거래 중.
- 창을 다 못 채운 날은 값 없음(지어내지 않는다).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from kbj.core.rows import Bar

__all__ = [
    "ADR_COLD",
    "ADR_HOT",
    "ADR_WINDOW",
    "DISPARITY_MAS",
    "AdrPoint",
    "DayCount",
    "adr_series",
    "adr_zone",
    "daily_counts",
    "disparity",
]

ADR_WINDOW: Final = 20
ADR_HOT: Final = 120.0
ADR_COLD: Final = 80.0
DISPARITY_MAS: Final[tuple[int, ...]] = (25, 50)
NOT_STOCK_KINDS: Final[frozenset[str]] = frozenset({"etf", "etn"})


@dataclass(frozen=True)
class DayCount:
    up: int
    down: int
    flat: int

    @property
    def total(self) -> int:
        return self.up + self.down + self.flat


@dataclass(frozen=True)
class AdrPoint:
    date: date
    adr: float | None  # 창을 다 못 채웠거나 하락 합이 0 이면 None
    up20: int
    down20: int
    up: int
    down: int
    flat: int


def daily_counts(
    bars: Mapping[str, Sequence[Bar]],
    market_of: Mapping[str, str | None],
    trading_days: Sequence[date],
    *,
    markets: Collection[str] = ("KOSPI", "KOSDAQ"),
    kind_of: Mapping[str, str | None] | None = None,
) -> dict[str, dict[date, DayCount]]:
    """{시장: {날짜: 상승·하락·보합}}.

    `bars` 는 종목별 오름차순 일봉, `trading_days` 오름차순 거래일.
    """
    idx = {d: i for i, d in enumerate(sorted(set(trading_days)))}
    acc: dict[str, dict[date, list[int]]] = {m: {} for m in markets}
    for code, rows in bars.items():
        m = market_of.get(code)
        if m not in acc:
            continue
        if kind_of is not None and (kind_of.get(code) or "") in NOT_STOCK_KINDS:
            continue
        prev: Bar | None = None
        for b in rows:
            i = idx.get(b.date)
            if (
                prev is not None
                and i is not None
                and idx.get(prev.date) == i - 1
                and b.close
                and prev.close
            ):
                c = acc[m].setdefault(b.date, [0, 0, 0])
                if b.close > prev.close:
                    c[0] += 1
                elif b.close < prev.close:
                    c[1] += 1
                else:
                    c[2] += 1
            prev = b
    return {m: {d: DayCount(*v) for d, v in sorted(days.items())} for m, days in acc.items()}


def adr_series(counts: Mapping[date, DayCount], window: int = ADR_WINDOW) -> list[AdrPoint]:
    """날짜 오름차순 ADR. 앞 window−1 날은 값 없음."""
    days = sorted(counts)
    out: list[AdrPoint] = []
    for i, d in enumerate(days):
        c = counts[d]
        win = days[max(0, i - window + 1) : i + 1]
        up = sum(counts[x].up for x in win)
        dn = sum(counts[x].down for x in win)
        adr = up / dn * 100 if len(win) == window and dn > 0 else None
        out.append(AdrPoint(d, adr, up, dn, c.up, c.down, c.flat))
    return out


def adr_zone(adr: float | None) -> str | None:
    if adr is None:
        return None
    if adr >= ADR_HOT:
        return "과열"
    if adr <= ADR_COLD:
        return "바닥권"
    return "중립"


def disparity(
    closes: Iterable[tuple[date, float | None]], ma: int
) -> list[tuple[date, float | None]]:
    """[(날짜, 이격도)] — 종가 ÷ ma 일 단순이동평균 × 100(그날 포함).

    창 안에 빈 값이 있으면 None.
    """
    pts = list(closes)
    out: list[tuple[date, float | None]] = []
    for i, (d, c) in enumerate(pts):
        win = [v for _, v in pts[max(0, i - ma + 1) : i + 1]]
        if c is None or len(win) < ma or any(v is None for v in win):
            out.append((d, None))
            continue
        avg = sum(v for v in win if v is not None) / ma
        out.append((d, c / avg * 100 if avg else None))
    return out

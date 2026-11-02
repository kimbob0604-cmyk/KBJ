"""상대 시총·연도별 수익률(stock-dashboard 화면을 옮김, 2026-11-02) — 순수 계산.

■ 상대 시총 — 비율 = A 묶음 시총 합 ÷ B 묶음 시총 합(달러 환산) × 100. 묶음은 여러 종목의 합일 수
  있다(삼성전자 + 삼성전자우, 무라타 + 이비덴).
  - 현재 시총: 국내는 마지막 스냅(원), 해외는 야후 quote(현지 통화) — `prv_market.global_cap`.
  - 과거 시총은 **현재 주식 수가 그대로였다고 보고** 야후 일봉(분할 반영 종가)으로 거슬러 계산한다:
      시총_t = 시총_now × 종가_t ÷ 종가_now   (같은 야후 시계열 안에서 비율만 쓴다)
    자사주 소각·증자로 주식 수가 바뀐 구간은 그만큼 어긋난다 — 화면에 적는다.
  - 통화가 다르면 그날 환율로 달러 환산(KRW=X·JPY=X 일봉, 1달러당 현지 통화). 휴장일은 직전 값.
■ 연도별 수익률 — 월봉 연말 종가끼리의 변화율(가격 지수 — 배당 제외). 올해는 작년 말 대비(YTD).
값이 없으면 None + 사유(지어내지 않는다).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

__all__ = [
    "FX_SYMBOL",
    "INDICES",
    "PAIRS",
    "PERIOD_DAYS",
    "AnnualRow",
    "Leg",
    "PairView",
    "annual_returns",
    "daily_symbols",
    "pair_series",
    "pair_view",
]

# (야후 심볼, 국내 코드 또는 None, 통화)
Leg = tuple[str, str | None, str]
FX_SYMBOL: Final[Mapping[str, str]] = {"KRW": "KRW=X", "JPY": "JPY=X"}  # 1달러당 현지 통화
PERIOD_DAYS: Final[Mapping[str, int]] = {"6M": 183, "1Y": 366, "2Y": 731}

_HYNIX: Final[Leg] = ("000660.KS", "000660", "KRW")
_SEC: Final[Leg] = ("005930.KS", "005930", "KRW")
_SEC_PREF: Final[Leg] = ("005935.KS", "005935", "KRW")
_SEMCO: Final[Leg] = ("009150.KS", "009150", "KRW")
_MURATA: Final[Leg] = ("6981.T", None, "JPY")
PAIRS: Final[tuple[tuple[str, tuple[Leg, ...], tuple[Leg, ...]], ...]] = (
    ("SK하이닉스 / 삼성전자+우", (_HYNIX,), (_SEC, _SEC_PREF)),
    ("SK하이닉스 / 삼성전자", (_HYNIX,), (_SEC,)),
    ("SK하이닉스 / 마이크론", (_HYNIX,), (("MU", None, "USD"),)),
    ("삼성전기 / 무라타", (_SEMCO,), (_MURATA,)),
    ("삼성전기 / 무라타+이비덴", (_SEMCO,), (_MURATA, ("4062.T", None, "JPY"))),
    ("삼성전자 / TSMC", (_SEC,), (("TSM", None, "USD"),)),
    ("브로드컴 / 엔비디아", (("AVGO", None, "USD"),), (("NVDA", None, "USD"),)),
)
INDICES: Final[tuple[tuple[str, str], ...]] = (
    ("^GSPC", "S&P 500"),
    ("^IXIC", "NASDAQ"),
    ("^KS11", "KOSPI"),
    ("^KQ11", "KOSDAQ"),
)

Series = Sequence[tuple[date, float]]


def daily_symbols() -> tuple[str, ...]:
    """상대 시총에 쓰는 야후 일봉 심볼(종목 + 환율, 정렬)."""
    syms = {s for _, a, b in PAIRS for s, _, _ in (*a, *b)} | set(FX_SYMBOL.values())
    return tuple(sorted(syms))


def _asof(series: Series, d: date) -> float | None:
    """d 이하 마지막 값(휴장일 맞춤). series 는 날짜 오름차순."""
    i = bisect_right([x for x, _ in series], d)
    return series[i - 1][1] if i else None


def pair_series(
    a: Sequence[Leg],
    b: Sequence[Leg],
    hist: Mapping[str, Series],
    caps_now: Mapping[str, float],
    fx: Mapping[str, Series],
    since: date | None = None,
) -> tuple[list[tuple[date, float]], str | None]:
    """한 쌍의 [(날짜, 비율 %)] 와 못 만든 사유. 날짜는 묶음 종목 거래일의 합집합."""
    legs = (*a, *b)
    for s, _, cur in legs:
        if not hist.get(s):
            return [], f"{s} 일봉 없음"
        if not caps_now.get(s):
            return [], f"{s} 현재 시총 없음"
        if cur != "USD" and not fx.get(cur):
            return [], f"{cur} 환율 없음"
    dates = sorted({d for s, _, _ in legs for d, _ in hist[s] if since is None or d >= since})

    def usd(leg: Sequence[Leg], d: date) -> float | None:
        total = 0.0
        for s, _, cur in leg:
            series = hist[s]
            p_t, p_now = _asof(series, d), series[-1][1]
            if p_t is None or not p_now:
                return None
            rate = 1.0 if cur == "USD" else _asof(fx[cur], d)
            if not rate:
                return None
            total += caps_now[s] * p_t / p_now / rate
        return total

    out: list[tuple[date, float]] = []
    for d in dates:
        va, vb = usd(a, d), usd(b, d)
        if va and vb:
            out.append((d, round(va / vb * 100, 2)))
    return out, None


@dataclass(frozen=True)
class PairView:
    name: str
    legs: tuple[tuple[str, ...], tuple[str, ...]]
    series: list[tuple[date, float]]
    latest: float | None
    date: date | None
    high: float | None
    low: float | None
    pct_of_range: float | None  # 기간 저점 0 ~ 고점 100 중 지금 위치
    error: str | None


def pair_view(
    name: str,
    a: Sequence[Leg],
    b: Sequence[Leg],
    hist: Mapping[str, Series],
    caps_now: Mapping[str, float],
    fx: Mapping[str, Series],
    since: date | None,
) -> PairView:
    series, err = pair_series(a, b, hist, caps_now, fx, since)
    vals = [v for _, v in series]
    hi, lo = (max(vals), min(vals)) if vals else (None, None)
    last = series[-1] if series else None
    pos = (
        round((last[1] - lo) / (hi - lo) * 100, 1)
        if last is not None and hi is not None and lo is not None and hi > lo
        else None
    )
    return PairView(
        name=name,
        legs=(tuple(s for s, _, _ in a), tuple(s for s, _, _ in b)),
        series=series,
        latest=last[1] if last else None,
        date=last[0] if last else None,
        high=hi,
        low=lo,
        pct_of_range=pos,
        error=err if err else (None if series else "겹치는 날 없음"),
    )


@dataclass(frozen=True)
class AnnualRow:
    symbol: str
    name: str
    returns: dict[int, float]
    asof: date | None
    error: str | None


def annual_returns(monthly: Mapping[str, Series]) -> tuple[list[int], list[AnnualRow]]:
    """(연도 — 최근 순, 지수별 행). 연말 종가 기준, 올해는 마지막 값(YTD)."""
    rows: list[AnnualRow] = []
    years: set[int] = set()
    for sym, name in INDICES:
        m = sorted(monthly.get(sym) or [])
        if not m:
            rows.append(AnnualRow(sym, name, {}, None, "월봉 없음"))
            continue
        last_by_year: dict[int, float] = {}
        for d, c in m:
            last_by_year[d.year] = c  # 날짜 순이라 마지막 값이 연말
        rets = {
            y: round((last_by_year[y] / last_by_year[y - 1] - 1) * 100, 2)
            for y in sorted(last_by_year)
            if y - 1 in last_by_year and last_by_year[y - 1]
        }
        years |= set(rets)
        rows.append(AnnualRow(sym, name, rets, m[-1][0], None))
    return sorted(years, reverse=True), rows

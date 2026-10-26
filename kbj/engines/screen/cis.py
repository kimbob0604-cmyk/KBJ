"""cis 순추세 스크리너 — 전 거래일 종가 기준 거래량 급증 + 박스권 돌파(config/screen.yaml `cis`).

근거: "사는 주식을 사고 팔리는 주식을 판다", "오르는 주식은 계속 오른다", 가격·거래량만 본다.
숫자 문턱은 cis 본인이 공개한 적이 없어 우리 출발점이다(config 주석). 매매 권유가 아니다.

한 종목(as_of 하루)의 판정
1. 대상: 코스피·코스닥 보통주(우선주·스팩·리츠·ETF·ETN 빼기), 관리·정지·정리매매 빼기
   (상태를 모르면 넣고 센다).
2. 그날 봉이 있고 직전 `lookback_days` 거래일이 다 있어야 한다(신규 상장·정지 뒤 재개는 뺀다).
3. 유동성: 종가 ≥ `min_price`, 직전 평균 거래대금 ≥ `min_avg_turnover_won`.
4. **거래량 급증**(표에 싣는 조건): 거래량 ≥ 직전 평균 × `vol_mult` 이고
   거래대금 ≥ `min_turnover_won`.
5. 통과(cis 후보)는 4 에 더해 — 종가가 직전 최고가(고가) 위(박스권 돌파), 양봉(종가 > 시가),
   (종가−저가)/(고가−저가) ≥ `close_pos_min`(윗꼬리 짧음), 상한가 근처가 아님(`limit_pct` 미만 —
   재료로 튄 날은 따로 표시), 그 시장 지수가 `regime_ma` 일선 위(모르면 막지 않고 표시).

표는 통과 먼저, 그 안은 거래량 배수 큰 순 — 상한 `max_rows`(전체 수는 따로 센다).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from kbj.config.screen import CisCfg
from kbj.core.rows import Bar, IndexBar
from kbj.engines.board import kinds

__all__ = [
    "MARKETS",
    "CisResult",
    "CisRow",
    "Regime",
    "StockMeta",
    "regime_of",
    "screen_cis",
]

MARKETS: Final = ("KOSPI", "KOSDAQ")
# 통과하지 못한 까닭(표 칸 — 짧은 한국어)
WHY_BOX: Final = "박스권 아래"
WHY_RED: Final = "음봉"
WHY_WICK: Final = "윗꼬리"
WHY_LIMIT: Final = "상한가 근처"
WHY_REGIME: Final = "시장 약세"


@dataclass(frozen=True)
class StockMeta:
    """그날 종목 정보(스냅 → 없으면 유니버스). `flagged` 는 관리·정지 등(모르면 None)."""

    name: str | None
    market: str | None
    kind: str | None
    flagged: bool | None


@dataclass(frozen=True)
class Regime:
    """시장 국면 — 지수 종가가 n 일선 위인가(지수 봉이 모자라면 on=None)."""

    market: str
    index_code: str | None
    close: float | None
    ma: float | None
    on: bool | None


@dataclass(frozen=True)
class CisRow:
    code: str
    name: str | None
    market: str
    close: float
    chg_pct: float
    volume: int
    vol_avg: float
    vol_ratio: float
    turnover: int
    avg_turnover: float
    box_high: float
    breakout: bool
    close_pos: float
    limit_up: bool
    regime_on: bool | None
    passed: bool
    why_not: tuple[str, ...]
    stop: float  # 종가 기준 최초 손절가(다음 날 실제 진입가로 다시 잰다)
    gap_skip: float  # 다음 날 시가가 이 값을 넘으면 건너뜀


@dataclass(frozen=True)
class CisResult:
    as_of: date
    rows: tuple[CisRow, ...]
    spikes: int  # 거래량 급증 종목 수(표 상한 전)
    passed: int  # 통과 종목 수(표 상한 전)
    scanned: int  # 그날 봉이 있는 대상 종목 수
    excluded: Mapping[str, int] = field(default_factory=dict[str, int])  # 까닭 → 수
    status_unknown: int = 0  # 관리·정지 상태를 모르는 채 넣은 종목 수
    regimes: tuple[Regime, ...] = ()


def regime_of(
    market: str, codes: Sequence[str], index_bars: Mapping[str, Sequence[IndexBar]], n: int,
    as_of: date,
) -> Regime:  # fmt: skip
    """후보 코드 중 as_of 까지 봉이 n 개 이상인 첫 지수로 국면을 잰다."""
    for c in codes:
        seq = [b for b in index_bars.get(c, ()) if b.date <= as_of and b.close is not None]
        if len(seq) < n:
            continue
        closes = [float(b.close) for b in seq[-n:] if b.close is not None]
        ma = sum(closes) / n
        last = closes[-1]
        return Regime(market=market, index_code=c, close=last, ma=ma, on=last > ma)
    return Regime(market=market, index_code=None, close=None, ma=None, on=None)


def _kind(code: str, m: StockMeta) -> str:
    return m.kind or kinds.of(code, m.name)


def _mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def screen_cis(
    bars: Mapping[str, Sequence[Bar]],
    meta: Mapping[str, StockMeta],
    index_bars: Mapping[str, Sequence[IndexBar]],
    cfg: CisCfg,
    as_of: date,
) -> CisResult:
    regimes = {
        m: regime_of(m, cfg.index.get(m, ()), index_bars, cfg.regime_ma, as_of) for m in MARKETS
    }
    excluded: Counter[str] = Counter()
    unknown = 0
    scanned = 0
    rows: list[CisRow] = []
    n = cfg.lookback_days
    for code in sorted(bars):
        seq = sorted(bars[code], key=lambda b: b.date)
        if not seq or seq[-1].date != as_of:
            continue  # 그날 거래가 없다(정지·휴장 종목) — 대상이 아니다
        m = meta.get(code)
        if m is None or m.market not in MARKETS:
            excluded["시장(코스피·코스닥 밖·정보 없음)"] += 1
            continue
        if _kind(code, m) != kinds.COMMON:
            excluded["종류(우선주·스팩·리츠·ETF)"] += 1
            continue
        if m.flagged:
            excluded["관리·정지"] += 1
            continue
        scanned += 1
        today, prior = seq[-1], seq[:-1][-n:]
        if len(prior) < n:
            excluded["이력 부족"] += 1
            continue
        c, o, h, lo = today.close, today.open, today.high, today.low
        vol, to, prev_close = today.volume, today.turnover, prior[-1].close
        if c is None or o is None or h is None or lo is None or vol is None or to is None:
            excluded["값 빠짐"] += 1
            continue
        if not prev_close:
            excluded["값 빠짐"] += 1
            continue
        vol_avg = _mean([float(b.volume) for b in prior if b.volume is not None])
        avg_to = _mean([float(b.turnover) for b in prior if b.turnover is not None])
        highs = [b.high if b.high is not None else b.close for b in prior]
        box = max((x for x in highs if x is not None), default=None)
        if not vol_avg or avg_to is None or box is None:
            excluded["값 빠짐"] += 1
            continue
        if c < cfg.min_price or avg_to < cfg.min_avg_turnover_won:
            excluded["유동성 부족"] += 1
            continue
        if m.flagged is None:
            unknown += 1
        ratio = vol / vol_avg
        if ratio < cfg.vol_mult or to < cfg.min_turnover_won:
            continue  # 거래량이 튀지 않았다 — 표에 싣지 않는다
        chg = (c / prev_close - 1.0) * 100.0
        pos = (c - lo) / (h - lo) if h > lo else 1.0
        breakout = c > box
        limit_up = chg >= cfg.limit_pct
        reg = regimes[m.market].on  # pyright: ignore[reportArgumentType]
        why: list[str] = []
        if not breakout:
            why.append(WHY_BOX)
        if c <= o:
            why.append(WHY_RED)
        if pos < cfg.close_pos_min:
            why.append(WHY_WICK)
        if limit_up:
            why.append(WHY_LIMIT)
        if reg is False:
            why.append(WHY_REGIME)
        rows.append(
            CisRow(
                code=code,
                name=m.name,
                market=m.market,  # pyright: ignore[reportArgumentType]
                close=c,
                chg_pct=chg,
                volume=vol,
                vol_avg=vol_avg,
                vol_ratio=ratio,
                turnover=to,
                avg_turnover=avg_to,
                box_high=box,
                breakout=breakout,
                close_pos=pos,
                limit_up=limit_up,
                regime_on=reg,
                passed=not why,
                why_not=tuple(why),
                stop=c * (1.0 - cfg.plan.stop_pct / 100.0),
                gap_skip=c * (1.0 + cfg.plan.gap_skip_pct / 100.0),
            )
        )
    rows.sort(key=lambda r: (not r.passed, -r.vol_ratio, r.code))
    return CisResult(
        as_of=as_of,
        rows=tuple(rows[: cfg.max_rows]),
        spikes=len(rows),
        passed=sum(r.passed for r in rows),
        scanned=scanned,
        excluded=dict(excluded),
        status_unknown=unknown,
        regimes=tuple(regimes[m] for m in MARKETS),
    )

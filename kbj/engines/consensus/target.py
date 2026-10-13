"""목표가 컨센서스·투자의견 분포 — 증권사별 투자의견에서 우리가 계산한다(§4.7, metrics §10).

목표가 컨센서스(`target_consensus`)
- 창: 의견 날짜 ∈ [t − window_days, t](닫힌 구간 — 달력일).
- 증권사마다 **창 안 가장 늦은 행 하나**를 먼저 고르고, 그 행이 미제시(`is_rated` 거짓)이거나
  목표가가 없거나 0 이하면 그 증권사는 뺀다 — 의견을 거둔 증권사의 옛 목표가로 돌아가지 않는다.
- 평균·중앙값(짝수면 가운데 둘의 평균)·최고·최저·증권사 수. n ≥ min_brokers_ok(기본 3) → ok,
  1~2 → estimated, 0 → None(행을 만들지 않는다). 포함한 행 중 품질이 ok 가 아닌 것이 있으면
  estimated.

투자의견 분포(`opinion_counts`)
- 같은 창·같은 '증권사별 마지막 행' 의 의견 문구를 매수·중립·매도로 센다(`opinion_bucket`).
  문구 표는 **[확인 필요 — 의견 코드 매핑 #29]**(KIS 가 문구를 주는지 코드를 주는지 실측 전).
  모르는 문구는 `unknown`, 미제시는 `unrated` 로 따로 센다(어느 쪽으로도 밀어 넣지 않는다).
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Literal

from kbj.core.fin_rows import OpinionRow, TargetConsensus
from kbj.core.quality import Quality

__all__ = [
    "BUY_WORDS",
    "HOLD_WORDS",
    "NOT_RATED_WORDS",
    "SELL_WORDS",
    "OpinionCounts",
    "is_rated",
    "latest_by_broker",
    "opinion_bucket",
    "opinion_counts",
    "target_consensus",
]

Bucket = Literal["buy", "hold", "sell"]

# 의견 문구 → 분류 [확인 필요 — 의견 코드 매핑 #29]. 비교는 소문자·공백·'.'·'_' 를 지운 문자열
NOT_RATED_WORDS: Final = frozenset(
    {"미제시", "notrated", "nr", "n/r", "투자의견없음", "의견없음", "없음", "-", "na", "n/a"}
)
BUY_WORDS: Final = frozenset(
    {
        "매수",
        "적극매수",
        "강력매수",
        "단기매수",
        "buy",
        "strongbuy",
        "tradingbuy",
        "outperform",
        "marketoutperform",
        "overweight",
        "비중확대",
        "시장수익률상회",
        "accumulate",
        "add",
    }
)
HOLD_WORDS: Final = frozenset(
    {
        "중립",
        "보유",
        "hold",
        "neutral",
        "marketperform",
        "시장수익률",
        "비중유지",
        "equalweight",
        "inline",
    }
)
SELL_WORDS: Final = frozenset(
    {
        "매도",
        "비중축소",
        "sell",
        "underperform",
        "marketunderperform",
        "시장수익률하회",
        "underweight",
        "reduce",
    }
)


def _norm(text: str | None) -> str:
    return "".join(ch for ch in (text or "").strip().lower() if ch not in " ._\t")


def is_rated(opinion: str | None) -> bool:
    """미제시('미제시'·'Not Rated'·'NR' …)가 아니다. 문구가 없으면(None·빈 값) 미제시로 본다."""
    n = _norm(opinion)
    return bool(n) and n not in NOT_RATED_WORDS


def opinion_bucket(opinion: str | None) -> Bucket | None:
    """의견 문구 → 매수·중립·매도. 미제시·모르는 문구는 None(부르는 쪽이 따로 센다)."""
    n = _norm(opinion)
    if n in BUY_WORDS:
        return "buy"
    if n in HOLD_WORDS:
        return "hold"
    if n in SELL_WORDS:
        return "sell"
    return None


def _window(t: date, window_days: int) -> date:
    if window_days < 1:
        raise ValueError(f"window_days 는 1 이상: {window_days!r}")
    return t - timedelta(days=window_days)


def latest_by_broker(
    opinions: Iterable[OpinionRow], t: date, *, window_days: int
) -> list[OpinionRow]:
    """창 [t − window_days, t] 안 증권사마다 가장 늦은 행 하나(같은 날이면 뒤에 온 행).

    증권사 이름 순으로 돌려준다."""
    lo = _window(t, window_days)
    last: dict[str, OpinionRow] = {}
    code: str | None = None
    for o in opinions:
        if code is None:
            code = o.code
        elif o.code != code:
            raise ValueError(f"다른 종목의 의견이 섞였다: {code} · {o.code}")
        if not lo <= o.opinion_date <= t:
            continue
        cur = last.get(o.broker)
        if cur is None or o.opinion_date >= cur.opinion_date:
            last[o.broker] = o
    return [last[b] for b in sorted(last)]


def target_consensus(
    opinions: Iterable[OpinionRow],
    t: date,
    *,
    window_days: int = 90,
    min_brokers_ok: int = 3,
) -> TargetConsensus | None:
    """증권사별 마지막 목표가의 평균·중앙값·최고·최저·수(§4.7). 남는 증권사가 없으면 None."""
    if min_brokers_ok < 1:
        raise ValueError(f"min_brokers_ok 는 1 이상: {min_brokers_ok!r}")
    picked = [
        o
        for o in latest_by_broker(opinions, t, window_days=window_days)
        if is_rated(o.opinion) and o.target_price is not None and o.target_price > 0
    ]
    if not picked:
        return None
    prices = sorted(float(o.target_price) for o in picked if o.target_price is not None)
    n = len(prices)
    mean = min(max(sum(prices) / n, prices[0]), prices[-1])  # 부동소수 끝자리로 범위를 벗지 않게
    quality = Quality.OK if n >= min_brokers_ok else Quality.ESTIMATED
    if any(o.quality is not Quality.OK for o in picked):
        quality = Quality.ESTIMATED
    return TargetConsensus(
        code=picked[0].code,
        as_of=t,
        mean=mean,
        median=float(statistics.median(prices)),
        high=prices[-1],
        low=prices[0],
        n_brokers=n,
        window_days=window_days,
        source=picked[0].source,
        quality=quality,
    )


@dataclass(frozen=True)
class OpinionCounts:
    """창 안 증권사별 마지막 의견의 분포. `unrated` = 미제시, `unknown` = 표에 없는 문구."""

    buy: int
    hold: int
    sell: int
    unrated: int
    unknown: int

    @property
    def n_brokers(self) -> int:
        return self.buy + self.hold + self.sell + self.unrated + self.unknown


def opinion_counts(
    opinions: Sequence[OpinionRow], t: date, *, window_days: int = 90
) -> OpinionCounts:
    """증권사별 마지막 의견을 매수·중립·매도로 센다(미제시·모르는 문구는 따로)."""
    counts = {"buy": 0, "hold": 0, "sell": 0, "unrated": 0, "unknown": 0}
    for o in latest_by_broker(opinions, t, window_days=window_days):
        if not is_rated(o.opinion):
            counts["unrated"] += 1
            continue
        counts[opinion_bucket(o.opinion) or "unknown"] += 1
    return OpinionCounts(**counts)

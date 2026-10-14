"""국장 공포탐욕(docs/metrics.md §D7 — 로그인, §10 Q6 다섯 성분 같은 가중).

성분(0~100 — 높을수록 탐욕):
① vol = 100 − 변동성 백분위(VKOSPI, 없으면 월물 ATM IV — `estimated` + '대체: ATM IV')
② breadth = 20일선 위 비율 백분위 ③ credit_balance = 신용잔고 변화율 백분위(§D2)
④ foreign = 외국인 수급 20일 누적 백분위(§D6) ⑤ credit_spread = 100 − 신용스프레드 백분위.

점수 = 있는 성분의 평균(소수 첫째, 반올림). 성분 < `min_components` → 값 없음(invalid + 빠진 성분),
성분이 다 모이지 않았거나 대체가 있으면 estimated. 품질은 성분 입력 중 가장 나쁜 것.
구간은 `kbj.core.p7_rows.fear_greed_band`(0–24 극단적 공포 · 25–44 공포 · 45–55 중립 · 56–75 탐욕 ·
76–100 극단적 탐욕). 상태 점수(§D8): 탐욕·극단적 탐욕 +1, 공포·극단적 공포 −1, 중립 0.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from kbj.config.diag import FearGreedCfg
from kbj.core.p7_rows import FearGreedBand, FearGreedRow, fear_greed_band
from kbj.core.quality import Quality
from kbj.engines.diagnosis.bands import quantize, worst

__all__ = ["INVERTED", "SUBSTITUTE_ATM", "Component", "band_score", "fear_greed"]

INVERTED: Final[frozenset[str]] = frozenset({"vol", "credit_spread"})
SUBSTITUTE_ATM: Final = "대체: ATM IV"
_HUNDRED: Final = Decimal(100)


@dataclass(frozen=True)
class Component:
    """성분 입력 하나 — pct = 그 입력의 백분위(뒤집기 전, 없으면 None), substitute = 대체 사유."""

    pct: Decimal | None
    quality: Quality
    substitute: str | None = None


def band_score(band: FearGreedBand | None) -> int | None:
    if band is None:
        return None
    if band in ("greed", "extreme_greed"):
        return 1
    if band in ("fear", "extreme_fear"):
        return -1
    return 0


def fear_greed(
    comps: Mapping[str, Component],
    cfg: FearGreedCfg,
    *,
    day: date,
    as_of: datetime,
    source: str,
) -> FearGreedRow:
    names: Sequence[str] = cfg.components
    unknown = set(comps) - set(names)
    if unknown:
        raise ValueError(f"모르는 성분: {sorted(unknown)}")
    vals: dict[str, Decimal | None] = {}
    qs: list[Quality] = []
    reasons: list[str] = []
    for n in names:
        c = comps.get(n)
        if c is None or c.pct is None or c.quality is Quality.INVALID:
            vals[n] = None
            continue
        v = _HUNDRED - c.pct if n in INVERTED else c.pct
        vals[n] = quantize(v)
        qs.append(c.quality)
        if c.substitute:
            reasons.append(c.substitute)
            qs.append(Quality.ESTIMATED)
    have = [v for v in vals.values() if v is not None]
    missing = [n for n, v in vals.items() if v is None]
    if missing:
        reasons.append(f"빠진 성분: {', '.join(missing)}")
    if len(have) < cfg.min_components:
        reasons.append(f"성분 {len(have)}개 — {cfg.min_components}개 미만")
        return FearGreedRow(
            trade_date=day, score=None, band=None, components=vals, source=source, as_of=as_of,
            quality=Quality.INVALID, reasons=tuple(reasons),
        )  # fmt: skip
    if missing:
        qs.append(Quality.ESTIMATED)
    score = quantize(sum(have, Decimal(0)) / len(have), "0.1")
    return FearGreedRow(
        trade_date=day, score=score, band=fear_greed_band(score), components=vals, source=source,
        as_of=as_of, quality=worst(qs) or Quality.INVALID, reasons=tuple(reasons),
    )  # fmt: skip

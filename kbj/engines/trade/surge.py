"""급등·급락 엔진(순수 — 계약 ⑨, docs/p6_design.md §4.2, metrics §26).

규칙(문턱은 `config/trade.yaml surge` — [제안] 값):
- 급등: 이번 달 금액 ≥ `min_usd` ∧ YoY ≥ `surge_yoy` ∧ 3개월 YoY ≥ `surge_yoy_3m`.
- 급락: 전년 같은 달 금액 ≥ `min_usd` ∧ YoY ≤ `plunge_yoy` ∧ 3개월 YoY ≤ `plunge_yoy_3m`.
  (급락은 이번 달 값이 작아진 것이 핵심이라 수준 문턱을 전년 값에 건다 — 크던 품목이 줄어든 것만.)
- 수준 미달·YoY 없음(전년 0·None)·3개월 YoY 없음은 목록에서 뺀다. 경계값은 포함(≥·≤ — 시험).
- 순위: 급등은 YoY 내림차순, 급락은 YoY 오름차순(동률은 열쇠 순). 층·흐름·규칙마다 상위 `top`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from kbj.config.trade import SurgeCfg
from kbj.core.quality import Quality
from kbj.core.trade_rows import Flow, SurgeLevel, SurgeRow, SurgeRule, prev_ym
from kbj.engines.trade.explore import worst, yoy, yoy_3m

__all__ = ["SOURCE", "SurgeInput", "classify", "detect"]

SOURCE: Final = "KBJ:trade.derive"


@dataclass(frozen=True)
class SurgeInput:
    """열쇠 하나(HS 접두 또는 국가)의 월별 금액·품질."""

    key: str
    usd: Mapping[str, Decimal | None]
    quality: Quality = Quality.OK


def classify(
    usd: Decimal | None,
    usd_prev: Decimal | None,
    y: float | None,
    y3: float | None,
    cfg: SurgeCfg,
) -> SurgeRule | None:
    """급등·급락·해당 없음(경계 포함)."""
    if y is None or y3 is None:
        return None
    lim = Decimal(cfg.min_usd)
    if usd is not None and usd >= lim and y >= cfg.surge_yoy and y3 >= cfg.surge_yoy_3m:
        return "surge"
    if usd_prev is not None and usd_prev >= lim and y <= cfg.plunge_yoy and y3 <= cfg.plunge_yoy_3m:
        return "plunge"
    return None


def detect(
    inputs: Iterable[SurgeInput],
    *,
    ym: str,
    level: SurgeLevel,
    flow: Flow,
    cfg: SurgeCfg,
    as_of: datetime,
) -> list[SurgeRow]:
    """그 달 급등·급락 행(규칙마다 1부터 순위, 상위 `cfg.top`)."""
    found: dict[SurgeRule, list[tuple[str, float, float, Decimal | None, Quality]]] = {
        "surge": [],
        "plunge": [],
    }
    py = prev_ym(ym, 12)
    for it in inputs:
        get: Callable[[str], Decimal | None] = it.usd.get
        cur, before = get(ym), get(py)
        y, y3 = yoy(cur, before), yoy_3m(get, ym)
        rule = classify(cur, before, y, y3, cfg)
        if rule is None or y is None or y3 is None:
            continue
        found[rule].append((it.key, y, y3, cur, it.quality))
    out: list[SurgeRow] = []
    for rule, items in found.items():
        sign = -1.0 if rule == "surge" else 1.0
        items.sort(key=lambda t: (sign * t[1], t[0]))
        for rank, (key, y, y3, cur, q) in enumerate(items[: cfg.top], start=1):
            out.append(
                SurgeRow(
                    ym=ym,
                    level=level,
                    key=key,
                    flow=flow,
                    rule=rule,
                    yoy=y,
                    yoy_3m=y3,
                    usd=cur,
                    rank=rank,
                    source=SOURCE,
                    as_of=as_of,
                    quality=worst([q]) or Quality.OK,
                )
            )
    return out

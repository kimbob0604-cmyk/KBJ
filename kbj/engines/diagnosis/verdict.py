"""종합 판정(docs/metrics.md §D8 — 로그인).

성분(상태 점수 −1·0·+1): `fear_greed`(§D7 구간), `credit_spread`(§D1 σ 칸),
`breadth_ma20`·`kr_vs_global`·
`foreign_flow`(§D6), `gex`(GEX 체제 — 마지막 주간 사이클 범위 all, Flip 이 visible 일 때만: 현재가
> Flip
+1, < Flip −1, Flip 없음 0. 레벨이 아예 없거나 visible 이 아니면 성분 빠짐).
점수 S = 합. `S ≥ risk_on_at` 위험 선호 · `S ≤ risk_off_at` 위험 회피 · 그 사이 중립 · 성분 <
`min_components`
보류. 근거 = 기여 큰 셋(0 이 아닌 것 — |점수| 큰 순, 같으면 성분 순서). 이벤트 주의 꼬리표 = §D9
이벤트가
오늘~`caution_days` 거래일 뒤. 품질은 입력 중 가장 나쁜 것(보류이거나 빠진 성분이 있으면 estimated
이상).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from kbj.config.diag import DiagConfig
from kbj.core.p7_rows import DiagIndicatorRow, EventMarker, FearGreedRow, VerdictRow
from kbj.core.quality import Quality
from kbj.engines.diagnosis.bands import worst
from kbj.engines.diagnosis.fear_greed import band_score
from kbj.engines.diagnosis.indicators import score_of
from kbj.engines.diagnosis.public import judge

__all__ = [
    "PART_LABELS",
    "VERDICT_PARTS",
    "GexRegime",
    "event_flags",
    "gex_regime",
    "top_reasons",
    "verdict",
]

VERDICT_PARTS: Final[tuple[str, ...]] = (
    "fear_greed", "credit_spread", "breadth_ma20", "kr_vs_global", "foreign_flow", "gex",
)  # fmt: skip
PART_LABELS: Final[Mapping[str, str]] = {
    "fear_greed": "공포탐욕",
    "credit_spread": "신용스프레드",
    "breadth_ma20": "시장폭",
    "kr_vs_global": "한국 대 글로벌",
    "foreign_flow": "외국인 수급",
    "gex": "GEX 체제",
}


@dataclass(frozen=True)
class GexRegime:
    """GEX 체제 입력 — spot 현재가·flip(없으면 None)·visible 여부·품질."""

    spot: float | None
    flip: float | None
    visible: bool
    quality: Quality


def gex_regime(g: GexRegime | None) -> int | None:
    if g is None or not g.visible or g.quality is Quality.INVALID or g.spot is None:
        return None
    if g.flip is None:
        return 0
    if g.spot > g.flip:
        return 1
    if g.spot < g.flip:
        return -1
    return 0


def event_flags(events: Sequence[EventMarker], days: Collection[date]) -> tuple[str, ...]:
    """그 날짜들에 든 이벤트 종류(중복 없이, 종류 이름 순)."""
    return tuple(sorted({e.kind for e in events if e.date in days}))


def top_reasons(parts: Mapping[str, int], n: int) -> list[str]:
    order = {k: i for i, k in enumerate(VERDICT_PARTS)}
    nz = sorted(
        ((k, v) for k, v in parts.items() if v != 0),
        key=lambda kv: (-abs(kv[1]), order.get(kv[0], len(order)), kv[0]),
    )
    return [f"{'+' if v > 0 else '−'}{PART_LABELS.get(k, k)}" for k, v in nz[:n]]


def verdict(
    cfg: DiagConfig,
    *,
    day: date,
    as_of: datetime,
    source: str,
    fear: FearGreedRow | None,
    indicators: Sequence[DiagIndicatorRow],
    gex: GexRegime | None,
    flags: Sequence[str] = (),
) -> VerdictRow:
    by_key = {r.key: r for r in indicators}
    parts: dict[str, int] = {}
    qs: list[Quality] = []
    missing: list[str] = []
    s_fg = band_score(fear.band) if fear is not None else None
    if s_fg is not None and fear is not None:
        parts["fear_greed"] = s_fg
        qs.append(fear.quality)
    else:
        missing.append("fear_greed")
    for key in VERDICT_PARTS[1:5]:
        r = by_key.get(key)
        spec = cfg.indicators.get(key)
        s = score_of(r, spec) if r is not None and spec is not None else None
        if s is None or r is None:
            missing.append(key)
            continue
        parts[key] = s
        qs.append(r.quality)
    s_gex = gex_regime(gex)
    if s_gex is None or gex is None:
        missing.append("gex")
    else:
        parts["gex"] = s_gex
        qs.append(gex.quality)
    label, score = judge(parts, cfg.verdict)
    reasons: list[str] = []
    if missing:
        reasons.append(f"성분 없음: {', '.join(missing)}")
    q = worst(qs) or Quality.ESTIMATED
    if label == "hold":
        reasons.append(f"성분 {len(parts)}개 — {cfg.verdict.min_components}개 미만 보류")
    if label == "hold" or missing:
        q = worst((q, Quality.ESTIMATED)) or Quality.ESTIMATED
    return VerdictRow(
        trade_date=day, label=label, score=score, parts=parts, event_flags=tuple(flags),
        source=source, as_of=as_of, quality=q, reasons=tuple(reasons),
    )  # fmt: skip

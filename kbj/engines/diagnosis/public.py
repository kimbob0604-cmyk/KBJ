"""공공지표 진단(docs/metrics.md §D1~§D5) — 공개 입력만으로 지표 다섯 줄과 매크로 위험 게이지.

**공개 입력만**(계약 가 — 로그인 원천·저장소·GEX 를 모른다). 부르는
쪽(`kbj.services.engine.diag_pub`)이
공개 표(`pub_macro`·`pub_market_stats`·`pub_trade`)를 정본 함수로 관측 점(`Obs`)으로 바꿔 넘긴다:
신용스프레드 = `kbj.engines.macro.derive.spread`(§16), 수출 YoY = `kbj.engines.trade.flash`(§25 —
발표 당시).

| 키 | 값 | 관측 |
|---|---|---|
| credit_spread | AA- − 국고 3년(%p) 수준 | 일별 |
| credit_balance | 신용공여 잔고(코스피+코스닥) `change_days` 관측 변화율(%) | 금투협 일별 |
| deposits | 투자자예탁금 `change_days` 관측 변화율(%) | 금투협 일별 |
| export_momentum | 수출 속보 전체 YoY(%) — 구간마다 한 점 | 관세청 10일 |
| rate_direction | 국고 3년 `change_days` 관측 변화(bp) | 일별 |

매크로 위험 게이지(§D4): 위 다섯의 상태 점수 합 S — `S ≥ risk_on_at` 위험 선호 · `S ≤ risk_off_at`
위험
회피 · 그 사이 중립. 점수가 있는 성분이 `min_components` 미만이면 보류(점수 없음, estimated).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final

from kbj.config.diag import DiagConfig, JudgeCfg
from kbj.core.p7_rows import DiagIndicatorRow, GaugeRow, VerdictLabel
from kbj.core.quality import Quality
from kbj.engines.diagnosis.bands import Obs, worst
from kbj.engines.diagnosis.indicators import change_bp, change_pct, indicator_row, score_of

__all__ = [
    "PUBLIC_KEYS",
    "READY_EXPORT",
    "PublicInputs",
    "judge",
    "macro_gauge",
    "public_indicators",
]

PUBLIC_KEYS: Final[tuple[str, ...]] = (
    "credit_spread", "credit_balance", "deposits", "export_momentum", "rate_direction",
)  # fmt: skip
_CHANGED: Final = frozenset({"credit_balance", "deposits", "rate_direction"})
READY_EXPORT: Final = "준비 중 — 수출 속보(관세청 10일 잠정치) 없음"


@dataclass(frozen=True)
class PublicInputs:
    """공개 관측(오름차순 아니어도 됨). expected = 키 → 정상 공표라면 있어야 할 마지막 관측 날."""

    credit_spread: Sequence[Obs] = ()
    ktb3y: Sequence[Obs] = ()
    credit_loan: Sequence[Obs] = ()
    deposits: Sequence[Obs] = ()
    export_yoy: Sequence[Obs] = ()
    export_ready: bool = True
    expected: Mapping[str, date] = field(default_factory=dict[str, date])


def public_indicators(
    inp: PublicInputs, cfg: DiagConfig, *, day: date, as_of: datetime, source: str
) -> list[DiagIndicatorRow]:
    """공개 지표 다섯 줄(설정 `indicators` 의 tier public 순서)."""
    n = cfg.change_days
    series: dict[str, Sequence[Obs]] = {
        "credit_spread": inp.credit_spread,
        "credit_balance": change_pct(inp.credit_loan, n),
        "deposits": change_pct(inp.deposits, n),
        "export_momentum": inp.export_yoy,
        "rate_direction": change_bp(inp.ktb3y, n),
    }
    out: list[DiagIndicatorRow] = []
    for key in cfg.keys("public"):
        if key not in series:
            raise ValueError(f"공개 지표 {key!r} 의 계산이 없다(설정과 엔진이 어긋남)")
        why = [READY_EXPORT] if key == "export_momentum" and not inp.export_ready else []
        cap = Quality.ESTIMATED if key == "export_momentum" else None  # 잠정치 — 단정하지 않는다
        out.append(
            indicator_row(
                key, cfg.indicators[key], cfg, series[key], day=day, as_of=as_of, source=source,
                expected=inp.expected.get(key), quality_cap=cap, reasons=why,
                inputs={"change_days": n} if key in _CHANGED else None,
            )
        )  # fmt: skip
    return out


def judge(parts: Mapping[str, int], cfg: JudgeCfg) -> tuple[VerdictLabel, int | None]:
    """점수 합 → (판정, 점수). 성분 < min_components → ('hold', None)."""
    if len(parts) < cfg.min_components:
        return "hold", None
    s = sum(parts.values())
    if s >= cfg.risk_on_at:
        return "risk_on", s
    if s <= cfg.risk_off_at:
        return "risk_off", s
    return "neutral", s


def macro_gauge(
    rows: Sequence[DiagIndicatorRow], cfg: DiagConfig, *, day: date, as_of: datetime, source: str
) -> GaugeRow:
    """매크로 위험 게이지(§D4) — 공개 지표 행의 상태 점수 합."""
    parts: dict[str, int] = {}
    qs: list[Quality] = []
    missing: list[str] = []
    for r in rows:
        spec = cfg.indicators.get(r.key)
        if spec is None or spec.tier != "public":
            raise ValueError(f"게이지 성분은 공개 지표만: {r.key!r}")
        s = score_of(r, spec)
        if s is None:
            missing.append(r.key)
            continue
        parts[r.key] = s
        qs.append(r.quality)
    label, score = judge(parts, cfg.gauge)
    reasons = [f"성분 없음: {', '.join(missing)}"] if missing else []
    q = worst(qs) or Quality.ESTIMATED
    if label == "hold":
        reasons.append(f"성분 {len(parts)}개 — {cfg.gauge.min_components}개 미만 보류")
        q = worst((q, Quality.ESTIMATED)) or Quality.ESTIMATED
    elif missing:
        q = worst((q, Quality.ESTIMATED)) or Quality.ESTIMATED
    return GaugeRow(
        trade_date=day, label=label, score=score, parts=parts, source=source, as_of=as_of,
        quality=q, reasons=tuple(reasons),
    )  # fmt: skip

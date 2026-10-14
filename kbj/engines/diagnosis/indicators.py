"""진단 지표 한 줄(docs/metrics.md §D1~§D6) — 관측 이력 → `DiagIndicatorRow`, 그리고 파생(변화율·
변화 bp·누적·합).

- 값 = 그날(day) 이하 마지막 관측(결측·invalid 는 건너뜀). 기대 날짜(`expected` — 공표가 정상이면
  있어야
  할 관측 날)보다 앞이면 `stale` + '공표 지연 — <날> 값'(§D1·§D2 품질 규칙).
- 구간·백분위는 그 관측까지의 최근 `window` 개 값으로(§D0). 이력이 `min_history` 보다 짧으면 구간
  없음 + `estimated` + '이력 부족(n/최소)'.
- §D1(신용스프레드)은 σ 세 칸, 나머지는 백분위 다섯 칸. 백분위는 둘 다 싣는다(공포탐욕 성분 — §D7).
- 플래그(점수 밖): `overheat`(신용잔고 극단 높음 — §D2), `fragile`(반도체 쏠림 극단 높음 — §D6).
- 파생은 관측 순서(행 개수) 기준 — 'n 거래일 전' = 결측이 아닌 n 번째 앞 관측. 어느 한쪽이 없으면
  그 점은
  만들지 않는다(값을 지어내지 않는다). 품질은 두 점 중 나쁜 쪽.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from kbj.config.diag import DiagConfig, IndicatorCfg
from kbj.core.p7_rows import DiagIndicatorRow, DiagState
from kbj.core.quality import Quality
from kbj.engines.diagnosis.bands import (
    Obs,
    pct_rank,
    pct_state,
    quantize,
    sigma_state,
    state_score,
    worst,
)

__all__ = [
    "NO_INPUT",
    "add",
    "change_bp",
    "change_pct",
    "indicator_row",
    "rolling_sum",
    "score_of",
    "usable",
]

NO_INPUT: Final = "입력 없음"
_HUNDRED: Final = Decimal(100)


def usable(history: Sequence[Obs], day: date) -> list[Obs]:
    """day 이하·값 있음·invalid 아님, 날짜 오름차순(같은 날 둘이면 뒤의 것)."""
    by_day: dict[date, Obs] = {}
    for o in history:
        if o.day <= day and o.value is not None and o.quality is not Quality.INVALID:
            by_day[o.day] = o
    return [by_day[d] for d in sorted(by_day)]


def _pairwise(obs: Sequence[Obs], n: int) -> list[tuple[Obs, Obs]]:
    if n < 1:
        raise ValueError("n 은 1 이상")
    clean = usable(obs, date.max)
    return [(clean[i - n], clean[i]) for i in range(n, len(clean))]


def change_pct(obs: Sequence[Obs], n: int) -> list[Obs]:
    """(x_t ÷ x_{t−n} − 1) × 100. 분모 0 은 점 없음."""
    out: list[Obs] = []
    for a, b in _pairwise(obs, n):
        if a.value is None or b.value is None or a.value == 0:
            continue
        out.append(
            Obs(
                b.day,
                (b.value / a.value - 1) * _HUNDRED,
                worst((a.quality, b.quality)) or b.quality,
                b.source,
            )
        )
    return out


def change_bp(obs: Sequence[Obs], n: int) -> list[Obs]:
    """(x_t − x_{t−n}) × 100 — % 단위 금리의 bp 변화(§D5)."""
    out: list[Obs] = []
    for a, b in _pairwise(obs, n):
        if a.value is None or b.value is None:
            continue
        out.append(
            Obs(
                b.day,
                (b.value - a.value) * _HUNDRED,
                worst((a.quality, b.quality)) or b.quality,
                b.source,
            )
        )
    return out


def rolling_sum(obs: Sequence[Obs], n: int) -> list[Obs]:
    """최근 n 관측 합(그날 포함) — 수급 20일 누적(§D6). n 개가 모이기 전은 점 없음."""
    if n < 1:
        raise ValueError("n 은 1 이상")
    clean = usable(obs, date.max)
    out: list[Obs] = []
    for i in range(n - 1, len(clean)):
        part = clean[i - n + 1 : i + 1]
        total = sum((o.value for o in part if o.value is not None), Decimal(0))
        out.append(
            Obs(
                clean[i].day,
                total,
                worst(o.quality for o in part) or Quality.INVALID,
                clean[i].source,
            )
        )
    return out


def add(a: Sequence[Obs], b: Sequence[Obs]) -> list[Obs]:
    """같은 날끼리 a + b(한쪽만 있는 날은 점 없음 — 코스피+코스닥 신용잔고 등)."""
    bb = {o.day: o for o in usable(b, date.max)}
    out: list[Obs] = []
    for o in usable(a, date.max):
        p = bb.get(o.day)
        if p is None or o.value is None or p.value is None:
            continue
        src = o.source if o.source == p.source else f"{o.source}+{p.source}"
        out.append(Obs(o.day, o.value + p.value, worst((o.quality, p.quality)) or o.quality, src))
    return out


def score_of(row: DiagIndicatorRow, spec: IndicatorCfg) -> int | None:
    """지표 행의 상태 점수(§D0 — 구간 없으면 None)."""
    return state_score(row.state, spec.risk_on)


def indicator_row(
    key: str,
    spec: IndicatorCfg,
    cfg: DiagConfig,
    history: Sequence[Obs],
    *,
    day: date,
    as_of: datetime,
    source: str,
    expected: date | None = None,
    quality_cap: Quality | None = None,
    reasons: Sequence[str] = (),
    inputs: Mapping[str, object] | None = None,
) -> DiagIndicatorRow:
    """지표 하나의 그날 행. history = 그 지표 값의 관측(오름차순 아니어도 됨 — day 이하만 쓴다)."""
    obs = usable(history, day)
    extra = dict(inputs or {})
    why = list(reasons)
    if not obs:
        return DiagIndicatorRow(
            trade_date=day, key=key, value=None, unit=spec.unit, state=None, pct_rank=None,
            inputs=extra, source=source, as_of=as_of, quality=Quality.INVALID,
            reasons=tuple(why or [NO_INPUT]),
        )  # fmt: skip
    last = obs[-1]
    if last.value is None:  # usable 이 값 있는 점만 — 형 좁히기
        raise ValueError("관측 값이 없다")
    qs: list[Quality] = [last.quality]
    if quality_cap is not None:
        qs.append(quality_cap)
    if expected is not None and last.day < expected:
        qs.append(Quality.STALE)
        why.append(f"공표 지연 — {last.day.isoformat()} 값")
    window = [o.value for o in obs[-cfg.window :] if o.value is not None]
    extra.update({"obs_day": last.day.isoformat(), "n": len(window), "input_source": last.source})
    state: DiagState | None = None
    pct: Decimal | None = None
    if len(window) < cfg.min_history:
        qs.append(Quality.ESTIMATED)
        why.append(f"이력 부족({len(window)}/{cfg.min_history})")
    else:
        pct = pct_rank(window, last.value)
        if spec.section == "D1":
            state, z = sigma_state(window, last.value, cfg.sigma_band)
            extra["z"] = str(z)
        else:
            state = pct_state(pct, cfg.bands)
    flags: list[str] = []
    if state == "extreme_high" and spec.overheat_flag:
        flags.append("overheat")
    if state == "extreme_high" and spec.fragile_flag:
        flags.append("fragile")
    value = quantize(last.value, "1" if spec.unit == "원" else "0.0001")
    return DiagIndicatorRow(
        trade_date=day, key=key, value=value, unit=spec.unit, state=state, pct_rank=pct,
        inputs=extra, source=source, as_of=as_of, quality=worst(qs) or Quality.INVALID,
        reasons=tuple(dict.fromkeys(why)), flags=tuple(flags),
    )  # fmt: skip

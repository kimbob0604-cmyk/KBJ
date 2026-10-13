"""컨센서스 리비전 — 추정치·목표가 평균의 W일 변화(§4.7, docs/metrics.md §10, ADR 0015 2-5).

승격 원본: SD `revision_calculator.py` — `classify_signal`:76(경계 ±5·±15, 위쪽 포함),
`compute_revisions_for_stock`:169(기준선 = W일 이전 가장 늦은 스냅), `_days_between`:252. SD 의
`_self_test`:296 분류 표는 `tests/unit/engines/consensus/test_revision.py` 로 옮겼다.

정의
- 대상: t 이하 마지막 스냅의 FY1·FY2(`forward.fiscal_years`) × 지표 `REVISION_METRICS`(매출·
  영업이익·순이익·EPS) + 목표가 평균(`target_mean`, fiscal_ym '000000').
- 현재값 = t 이하 마지막 스냅(그 결산년월)의 값. 값이 없으면(None) 그 칸은 행을 만들지 않는다.
- 기준선 = snap_date ≤ t − W 인 스냅 가운데 그 칸에 값이 있는 가장 늦은 것(W = 7·30 —
  `config/fin.yaml consensus.revision_windows`). SD 는 '현재 스냅일 − W' 였다 — 매일 도는
  운영에서는 같다.
- pct = (현재 − 기준) ÷ |기준| × 100(반올림하지 않는다 — metrics §0). 기준선이 없거나 기준 0 →
  pct·신호 None(행은 남긴다 — "기준 없음"), 부호가 바뀌면(기준·현재가 0 아닌 반대 부호)
  `TURN`(비율 대신).
- 신호: `classify(pct, cuts)` — 수치는 `config/fin.yaml consensus.signal_pct`
  (`SignalPct.classify` — SD 경계 그대로, 위쪽 포함). 이 모듈은 수치를 다시 적지 않는다(두 벌
  금지).
- 품질: 현재 스냅이 t 보다 이르면 stale, 현재·기준 행 품질 가운데 나쁜 것.
- 알림 대상(`alertable`): 추정치 지표(목표가 평균 제외 — SD `alert_revision_signals`:5857
  처럼 추정치 리비전만)에서 |pct| ≥ 하한(`consensus.alert_min_abs_pct`)이거나 `TURN` 인 행.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import Final, Protocol

from kbj.core.fin_rows import ConsensusRow, RevisionRow, TargetConsensus
from kbj.core.quality import Quality
from kbj.engines.consensus.forward import fiscal_years, latest_snapshot, worst

__all__ = [
    "REVISION_METRICS",
    "TARGET_METRIC",
    "TARGET_YM",
    "SignalCuts",
    "alertable",
    "change",
    "classify",
    "revisions",
]

REVISION_METRICS: Final[tuple[str, ...]] = ("revenue", "op", "ni", "eps")
TARGET_METRIC: Final = "target_mean"
TARGET_YM: Final = "000000"  # 목표가 리비전의 결산년월 자리(RevisionRow 규칙)
TURN: Final = "TURN"


class SignalCuts(Protocol):
    """리비전 % → (신호, 우선순위). `kbj.config.fin.SignalPct` 가 이 모양이다."""

    def classify(self, pct: float) -> tuple[str, int]: ...


def classify(pct: float, cuts: SignalCuts) -> tuple[str, int]:
    """리비전 % → (STRONG_UP·UP·NEUTRAL·DOWN·STRONG_DOWN, 우선순위 1~3) — 경계는 위쪽 포함."""
    return cuts.classify(pct)


def change(current: float, baseline: float | None) -> tuple[float | None, bool]:
    """(pct, 부호 전환). 기준 None·0 → (None, False). 부호 전환 → (None, True)."""
    if baseline is None or baseline == 0:
        return None, False
    if current != 0 and (current > 0) != (baseline > 0):
        return None, True
    return (current - baseline) / abs(baseline) * 100.0, False


def _row(
    code: str,
    t: date,
    metric: str,
    ym: str,
    w: int,
    current: float,
    base: tuple[date, float] | None,
    cuts: SignalCuts,
    source: str,
    quality: Quality,
) -> RevisionRow:
    pct, turn = change(current, None if base is None else base[1])
    signal: str | None
    if turn:
        signal = TURN
    elif pct is not None:
        signal = classify(pct, cuts)[0]
    else:
        signal = None
    return RevisionRow(
        code=code,
        as_of=t,
        metric=metric,
        fiscal_ym=ym,
        window_days=w,
        current=current,
        baseline=None if base is None else base[1],
        baseline_date=None if base is None else base[0],
        pct=pct,
        signal=signal,
        source=source,
        quality=quality,
    )


def _windows(windows: Sequence[int]) -> tuple[int, ...]:
    ws = tuple(windows)
    if not ws or any(w < 1 for w in ws) or len(set(ws)) != len(ws):
        raise ValueError(f"windows 는 겹치지 않는 1 이상 정수: {windows!r}")
    return ws


def _value(r: ConsensusRow, metric: str) -> float | None:
    v = getattr(r, metric)
    return None if v is None else float(v)


def revisions(
    snaps: Iterable[ConsensusRow],
    t: date,
    *,
    cuts: SignalCuts,
    windows: Sequence[int] = (7, 30),
    targets: Iterable[TargetConsensus] = (),
) -> list[RevisionRow]:
    """한 종목의 리비전 행(지표·결산년월·창 순). 스냅·목표가는 그 종목의 이력(t 이후 행은 무시)."""
    ws = _windows(windows)
    rows = list(snaps)
    out: list[RevisionRow] = []
    last = latest_snapshot(rows, t)
    fy1, fy2 = fiscal_years(rows, t)
    for fy in (fy1, fy2):
        if fy is None:
            continue
        hist = sorted(
            (r for r in rows if r.fiscal_ym == fy.fiscal_ym and r.snap_date <= t),
            key=lambda r: r.snap_date,
        )
        for metric in REVISION_METRICS:
            cur = _value(fy, metric)
            if cur is None:
                continue
            for w in ws:
                cut = t - timedelta(days=w)
                base_row = next(
                    (
                        r
                        for r in reversed(hist)
                        if r.snap_date <= cut and _value(r, metric) is not None
                    ),
                    None,
                )
                q = fy.quality
                if fy.snap_date < t:
                    q = worst(q, Quality.STALE)
                base: tuple[date, float] | None = None
                if base_row is not None:
                    q = worst(q, base_row.quality)
                    bv = _value(base_row, metric)
                    base = None if bv is None else (base_row.snap_date, bv)
                out.append(_row(fy.code, t, metric, fy.fiscal_ym, w, cur, base, cuts, fy.source, q))
    tgs = sorted((x for x in targets if x.as_of <= t), key=lambda x: x.as_of)
    if tgs:
        if last and tgs[-1].code != last[0].code:
            raise ValueError(f"다른 종목의 목표가가 섞였다: {last[0].code} · {tgs[-1].code}")
        now = tgs[-1]
        for w in ws:
            cut = t - timedelta(days=w)
            prev = next((x for x in reversed(tgs) if x.as_of <= cut), None)
            q = now.quality if now.as_of == t else worst(now.quality, Quality.STALE)
            base = None
            if prev is not None:
                q = worst(q, prev.quality)
                base = (prev.as_of, prev.mean)
            out.append(_row(now.code, t, TARGET_METRIC, TARGET_YM, w, now.mean, base, cuts,
                            now.source, q))  # fmt: skip
    return out


def alertable(rows: Iterable[RevisionRow], *, min_abs_pct: float) -> list[RevisionRow]:
    """알림 대상 — 추정치 지표(목표가 평균 제외)에서 |pct| ≥ min_abs_pct 또는 부호 전환(TURN)."""
    if min_abs_pct < 0:
        raise ValueError("min_abs_pct 는 0 이상")
    out = [
        r
        for r in rows
        if r.metric in REVISION_METRICS
        and (r.signal == TURN or (r.pct is not None and abs(r.pct) >= min_abs_pct))
    ]
    return sorted(out, key=lambda r: (r.code, r.fiscal_ym, r.metric, r.window_days))

"""체인 스냅샷 → engine 사이클 입력(`CycleInput`) — 시험·검증 리포트가 같은 변환을 쓴다.

KBJ P7(묶음 ENG): GX `scripts/metric_report.py` 의 `snapshot_cycle` 을 옮겼다(엔진 시험 fixture
`tests/unit/engines/gex/engine_inputs.py`·legacy `metric_report` 가 이것을 쓴다 — 두 벌 금지).
스냅샷은 합성(`tests/fixtures/synthetic/gex/validation/`) 또는 로컬 실측(git 제외)이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

from kbj.core.calendar import TradingCalendar, state_at
from kbj.core.gex.records import ChainRecord, FuturesRecord
from kbj.engines.gex.evaluate import CycleInput, ExpiryInfo
from kbj.engines.gex.golden import shift_snapshot
from kbj.engines.gex.series import SeriesKey
from kbj.engines.gex.snapshot import Snapshot, series_rows

__all__ = ["SnapshotCycle", "load_snapshot", "snapshot_cycle"]


def load_snapshot(path: Path) -> Snapshot:
    return Snapshot.model_validate_json(path.read_bytes())


_KIND_CLS = {"MONTH": "", "WKM": "WKM", "WKI": "WKI"}


@dataclass(frozen=True)
class SnapshotCycle:
    inp: CycleInput
    labels: dict[str, str]  # 스냅샷 라벨(MONTH:202610) → engine 라벨(M:202610)


def snapshot_cycle(
    path: Path,
    *,
    shift: timedelta | None = None,
    fresh_since: datetime | None = None,
    strikes: Mapping[SeriesKey, Sequence[Decimal]] | None = None,
    cal: TradingCalendar | None = None,
) -> SnapshotCycle:
    """체인 스냅샷(probe `chain_snapshot.json` 또는 그 작은 발췌) 하나 → engine `CycleInput`.

    전광판·보강 행을 `kbj.engines.gex.snapshot.series_rows` 로 OptionQuote 로 푼 뒤 체인 행으로
    되돌린다(행 시각 = 그 전광판 시각 — core_golden 의 평가 시각과 같게). 최종거래일은 스냅샷의
    월물리스트(없으면 캘린더), S_ref 는 스냅샷의 근월물 — 주간 전광판 F, 야간(시각을 옮긴 파생
    입력)이면 단건 CM. shift: 시각만 옮긴다(`kbj.engines.gex.golden.shift_snapshot`)."""
    cal = cal or TradingCalendar.default()
    snap: Snapshot = load_snapshot(path)
    if shift is not None:
        snap = shift_snapshot(snap, shift)
    info = state_at(snap.started_kst, cal)
    if info.trade_date is None or info.session is None:
        raise ValueError(f"세션 밖 스냅샷: {snap.started_kst}")
    rows: list[ChainRecord] = []
    expiries: dict[SeriesKey, ExpiryInfo] = {}
    labels: dict[str, str] = {}
    for label in snap.boards:
        kind, _, code = label.partition(":")
        cls = _KIND_CLS[kind]
        s = series_rows(snap, label, cal)
        key = cast(SeriesKey, (cls, code))
        expiries[key] = ExpiryInfo(s.expiry_date, "kis" if s.expiry_source == "kis" else "calendar")
        labels[label] = f"{cls or 'M'}:{code}"
        for q in s.quotes:
            rows.append(
                ChainRecord(
                    ts=s.now,
                    trade_date=info.trade_date,
                    session=info.session,
                    mrkt_cls=cls,
                    expiry=code,
                    strike=q.strike,
                    cp=q.cp,
                    source=q.source,
                    last=q.last,
                    bid=q.bid,
                    ask=q.ask,
                    oi=q.oi,
                    volume=q.volume,
                    iv_kis=None if q.kis_iv_pct is None else Decimal(str(q.kis_iv_pct)),
                )
            )
    day = info.session == "day"
    fut = FuturesRecord(
        ts=snap.started_kst,
        trade_date=info.trade_date,
        session=info.session,
        code=snap.atm_ref.code,
        market="F" if day else "CM",
        source="board" if day else "single",
        price=Decimal(str(snap.atm_ref.price)),
    )
    inp = CycleInput(
        as_of=max(r.ts for r in rows),
        trade_date=info.trade_date,
        session=info.session,
        chain=rows,
        futures=[fut],
        expiries=expiries,
        near_code=snap.atm_ref.code,
        strikes=dict(strikes or {}),
        fresh_since=fresh_since,
    )
    return SnapshotCycle(inp, labels)

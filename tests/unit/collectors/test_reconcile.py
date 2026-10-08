"""KRX D+1 대조 순수 함수(kbj.services.collectors.reconcile) — 같음·불일치·한쪽만·경계(새 시험,
metrics §6.6·§6.7 '대조').
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from kbj.config.markets import ReconcileCfg
from kbj.core.quality import Quality
from kbj.core.rows import Snap
from kbj.services.collectors.reconcile import reconcile, summarize

D = date(2026, 10, 6)
AT = datetime(2026, 10, 6, 23, 5, tzinfo=UTC)
CFG = ReconcileCfg(
    close_tol_pct=0.0, turnover_tol_pct=0.5, mktcap_tol_pct=0.5, warn_mismatch_pct=1.0
)


def snap(
    code: str, close: float | None, turnover: int | None, mktcap: int | None, src: str
) -> Snap:
    return Snap(code, D, None, "KOSPI", None, close, 0.0, 1, turnover, turnover is None, mktcap,
                None, None, src, "KRX", Quality.OK)  # fmt: skip


def verdicts(kis: list[Snap], krx: list[Snap]) -> dict[tuple[str, str], str]:
    rows = reconcile(
        {s.code: s for s in kis}, {s.code: s for s in krx}, cfg=CFG, day=D, checked_at=AT
    )
    return {(r.code, r.field): r.verdict for r in rows}


def test_same_values_are_ok() -> None:
    v = verdicts([snap("A", 100.0, 1_000, 5_000, "kis")], [snap("A", 100.0, 1_000, 5_000, "krx")])
    assert set(v.values()) == {"ok"}


def test_close_must_match_exactly() -> None:
    v = verdicts([snap("A", 100.0, 1_000, 5_000, "kis")], [snap("A", 101.0, 1_000, 5_000, "krx")])
    assert v[("A", "close")] == "mismatch"


def test_turnover_tolerance_boundary_is_inclusive() -> None:
    ok = verdicts([snap("A", 1.0, 1_005, None, "kis")], [snap("A", 1.0, 1_000, None, "krx")])
    assert ok[("A", "turnover")] == "ok"  # 정확히 0.5%
    bad = verdicts([snap("A", 1.0, 1_006, None, "kis")], [snap("A", 1.0, 1_000, None, "krx")])
    assert bad[("A", "turnover")] == "mismatch"


def test_one_side_only_and_missing_fields() -> None:
    v = verdicts(
        [snap("A", 1.0, None, None, "kis"), snap("K", 1.0, 1, 1, "kis")],
        [snap("A", 1.0, 5, None, "krx"), snap("X", 1.0, 1, 1, "krx")],
    )
    assert v[("A", "turnover")] == "missing_kis"
    assert ("A", "mktcap") not in v  # 양쪽 다 없는 칸은 대조하지 않는다
    assert v[("K", "close")] == "missing_krx"
    assert v[("X", "close")] == "missing_kis"


def test_summary_counts_warns_and_keeps_big_examples() -> None:
    rows = reconcile(
        {s.code: s for s in [snap("A", 100.0, 1, 1, "kis"), snap("B", 96.0, 1, 1, "kis")]},
        {s.code: s for s in [snap("A", 100.0, 1, 1, "krx"), snap("B", 100.0, 1, 1, "krx")]},
        cfg=CFG,
        day=D,
        checked_at=AT,
    )
    s = summarize(rows, CFG)
    assert (s.checked, s.mismatch) == (2, 1)
    assert s.mismatch_pct == 50.0
    assert s.warn
    assert s.max_abs_diff_pct is not None
    assert abs(s.max_abs_diff_pct - 4.0) < 1e-9
    assert s.examples == ("B close -4.0%",)


def test_mktcap_difference_below_the_kis_unit_is_ok() -> None:
    """KIS 시총은 억원 단위 — 소형주(100억)에서 반올림 0.5억(0.5% 넘음)은 불일치가 아니다. 1억
    이상 차이는 허용치대로 본다(검증에서 찾은 경계)."""
    krx = snap("A", 1.0, None, 10_049_000_000, "krx")
    ok = verdicts([snap("A", 1.0, None, 10_000_000_000, "kis")], [krx])
    assert ok[("A", "mktcap")] == "ok"
    bad = verdicts([snap("A", 1.0, None, 10_200_000_000, "kis")], [krx])
    assert bad[("A", "mktcap")] == "mismatch"


def test_kosdaq_segment_name_is_still_kosdaq_when_recording() -> None:
    """KRX 행의 시장이 'KOSDAQ GLOBAL' 이어도 대조한다(빠지면 missing_krx 로 숨는다)."""
    from kbj.services.collectors.reconcile import record
    from tests.unit.collectors.p3_fakes import new_repos

    repos = new_repos()
    from dataclasses import replace

    k = replace(snap("A", 100.0, 1_000, None, "kis"), market="KOSDAQ")
    x = Snap("A", D, None, "KOSDAQ GLOBAL", None, 101.0, 0.0, 1, 1_000, False, None, None, None,
             "krx", "KRX", Quality.OK)  # fmt: skip
    repos.market.upsert_snapshots([k, x], loaded_by="t", received_at=AT)
    s = record(repos, D, AT, CFG)
    assert s.checked == 1 and s.mismatch == 1 and s.missing_krx == 0

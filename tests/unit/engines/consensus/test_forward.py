"""FY1·FY2·NTM(§4.7, metrics §10) — 결산월 경계·연말·3월 결산·FY2 없음·스냅 고르기."""

from __future__ import annotations

from datetime import date

import pytest

from kbj.core.fin_rows import ConsensusRow
from kbj.core.quality import Quality
from kbj.engines.consensus.forward import fiscal_years, latest_snapshot, ntm, worst

EOK = 100_000_000


def row(
    d: date,
    ym: str,
    *,
    est: bool = True,
    rev: int = 1000,
    eps: float | None = 100.0,
    ni_owner: int | None = None,
    quality: Quality = Quality.OK,
) -> ConsensusRow:
    return ConsensusRow("994010", d, ym, est, rev * EOK, rev * EOK // 10, rev * EOK // 20,
                        None if ni_owner is None else ni_owner * EOK, eps, None,
                        quality=quality)  # fmt: skip


def test_ntm_hand_computed() -> None:
    t = date(2026, 11, 16)
    rows = [
        row(t, "202512", est=False, rev=900),
        row(t, "202612", rev=1000, eps=100.0),
        row(t, "202712", rev=1200, eps=130.0, ni_owner=70),
        row(t, "202812", rev=1500),
    ]
    n = ntm(rows, t)
    assert n is not None
    w = (date(2026, 12, 31) - t).days / 365  # 45/365
    assert n.weight == pytest.approx(w)
    assert n.fy1.fiscal_ym == "202612" and n.fy2 is not None and n.fy2.fiscal_ym == "202712"
    assert n.revenue == pytest.approx(w * 1000 * EOK + (1 - w) * 1200 * EOK)
    assert n.eps == pytest.approx(w * 100 + (1 - w) * 130)
    assert n.ni_owner is None  # FY1 에 없다 — 한쪽만으로 만들지 않는다
    assert n.ebitda is None
    assert n.quality is Quality.OK and n.snap_date == t and n.as_of == t and n.source == "KIS"


def test_year_end_boundary_and_weight_clip() -> None:
    t = date(2026, 12, 31)  # 결산일 당일 — FY1 은 '결산일이 t 뒤' 라 다음 해
    rows = [row(t, "202612"), row(t, "202712"), row(t, "202812")]
    fy1, fy2 = fiscal_years(rows, t)
    assert fy1 is not None and fy1.fiscal_ym == "202712"
    assert fy2 is not None and fy2.fiscal_ym == "202812"
    n = ntm(rows, t)
    assert n is not None and n.weight == 1.0  # 365/365 → 1(자른다)
    t2 = date(2027, 1, 1)
    n2 = ntm([row(t2, "202712"), row(t2, "202812")], t2)
    assert n2 is not None and n2.weight == pytest.approx(364 / 365)


def test_march_fiscal_year() -> None:
    t = date(2026, 11, 16)
    rows = [row(t, "202603", est=False), row(t, "202703"), row(t, "202803")]
    n = ntm(rows, t)
    assert n is not None and n.fy1.fiscal_ym == "202703"
    assert n.weight == pytest.approx((date(2027, 3, 31) - t).days / 365)


def test_no_fy2_and_no_fy1() -> None:
    t = date(2026, 11, 16)
    n = ntm([row(t, "202612")], t)
    assert n is not None and n.fy2 is None and n.weight is None and n.revenue is None
    assert n.fy1.revenue == 1000 * EOK  # FY1 값은 따로 남는다
    assert ntm([row(t, "202512", est=False)], t) is None
    assert ntm([], t) is None


def test_latest_snapshot_only_and_stale() -> None:
    t = date(2026, 11, 16)
    old, new = date(2026, 11, 12), date(2026, 11, 13)
    rows = [row(old, "202612", rev=900), row(old, "202712", rev=950), row(new, "202612", rev=1000),
            row(new, "202712", rev=1100), row(date(2026, 11, 17), "202612", rev=5)]  # fmt: skip
    snap = latest_snapshot(rows, t)
    assert {r.snap_date for r in snap} == {new}
    n = ntm(rows, t)
    assert n is not None and n.fy1.revenue == 1000 * EOK and n.quality is Quality.STALE
    est = [row(t, "202612", quality=Quality.ESTIMATED), row(t, "202712")]
    got = ntm(est, t)
    assert got is not None and got.quality is Quality.ESTIMATED


def test_worst_order() -> None:
    assert worst() is Quality.OK
    assert worst(Quality.OK, Quality.STALE) is Quality.STALE
    assert worst(Quality.STALE, Quality.ESTIMATED) is Quality.ESTIMATED
    assert worst(Quality.ESTIMATED, Quality.INVALID, Quality.OK) is Quality.INVALID

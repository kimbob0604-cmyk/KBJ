"""수출 속보 엔진(metrics §25, docs/p6_design.md §4.1) — 손 계산 예시 + 합성 세계(p6_world) 대조."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import TenDayRow
from kbj.engines.trade.flash import (
    REASON_NO_PREV,
    REASON_PREV_ZERO,
    flash_chip,
    monotonic_breaks,
    new_segments,
    ratio,
    segments,
    span_label,
)
from tests.fixtures.synthetic.p6_world import build_world

T0 = datetime(2026, 11, 11, 10, 47, tzinfo=KST)
SPAN = {1: "01~10", 2: "01~20", 3: "01~30"}


def row(
    ym: str,
    seg: int,
    col: int,
    usd: int | None,
    *,
    kind: str = "exp_item",
    first: int | None = None,
    name: str | None = None,
    seen: datetime = T0,
    q: Quality = Quality.ESTIMATED,
) -> TenDayRow:
    return TenDayRow(
        kind=kind,  # pyright: ignore[reportArgumentType]
        ym=ym,
        seg=seg,
        col_no=col,
        name=name or ("전체" if col == 0 else f"품목{col}"),
        usd=None if usd is None else Decimal(usd),
        first_usd=Decimal(first if first is not None else usd) if usd is not None else None,
        first_seen_at=seen,
        span_text=SPAN[seg],
        source="DATAGO:15157908",
        as_of=seen,
        quality=q,
    )


def test_ratio_사유() -> None:
    assert ratio(Decimal(110), Decimal(100)) == (pytest.approx(0.1), None)
    assert ratio(Decimal(1), None) == (None, REASON_NO_PREV)
    assert ratio(Decimal(1), Decimal(0)) == (None, REASON_PREV_ZERO)
    assert span_label("01~31") == "1~31일"


def test_구간_YoY_기여도_손계산() -> None:
    rows = [
        row("2025-11", 1, 0, 1000), row("2025-11", 1, 1, 400), row("2025-11", 1, 2, 300),
        row("2026-11", 1, 0, 1200), row("2026-11", 1, 1, 550), row("2026-11", 1, 2, 250),
    ]  # fmt: skip
    segs = segments(rows, kind="exp_item", months=1)
    assert [(s.ym, s.seg) for s in segs] == [("2026-11", 1)]
    c = {x.col_no: x for x in segs[0].cells}
    assert c[0].yoy == pytest.approx(0.2)
    assert c[1].yoy == pytest.approx(0.375)
    assert c[1].contribution == pytest.approx(150 / 200)
    assert c[2].contribution == pytest.approx(-50 / 200)
    assert c[0].contribution == pytest.approx(1.0)
    assert segs[0].quality is Quality.ESTIMATED


def test_열_이름이_바뀌면_YoY_없음_사유() -> None:
    rows = [row("2025-11", 1, 3, 100, name="말레이시아"), row("2026-11", 1, 3, 150, name="가상국")]
    cell = segments(rows, kind="exp_item", months=1)[0].cells[0]
    assert cell.yoy is None and cell.contribution is None
    assert cell.yoy_reason is not None and "열 이름 바뀜" in cell.yoy_reason


def test_전년_없음_0_은_None() -> None:
    rows = [row("2025-11", 1, 0, 0), row("2026-11", 1, 0, 100), row("2026-11", 2, 0, 200)]
    by = {(s.ym, s.seg): s.cells[0] for s in segments(rows, kind="exp_item", months=1)}
    assert by[("2026-11", 1)].yoy is None and by[("2026-11", 1)].yoy_reason == REASON_PREV_ZERO
    assert by[("2026-11", 2)].yoy is None and by[("2026-11", 2)].yoy_reason == REASON_NO_PREV


def test_최신이_먼저_기간_자르기_전월_대비() -> None:
    rows = [
        row(ym, s, 0, 100 * s + i)
        for i, ym in enumerate(("2026-08", "2026-09", "2026-10"))
        for s in (1, 2, 3)
    ]
    segs = segments(rows, kind="exp_item", months=2)
    assert [(s.ym, s.seg) for s in segs] == [
        ("2026-10", 3), ("2026-10", 2), ("2026-10", 1),
        ("2026-09", 3), ("2026-09", 2), ("2026-09", 1),
    ]  # fmt: skip
    assert segs[0].cells[0].mom == pytest.approx((302) / (301) - 1)
    with pytest.raises(ValueError, match="months"):
        segments(rows, kind="exp_item", months=0)
    assert segments(rows, kind="imp_item", months=3) == []


def test_같은_칸_두번은_오류() -> None:
    with pytest.raises(ValueError, match="두 번"):
        segments([row("2026-11", 1, 0, 1), row("2026-11", 1, 0, 2)], kind="exp_item", months=1)


def test_칩은_발표_당시_값_분모는_전년_최신() -> None:
    rows = [
        row("2025-11", 2, 0, 1000, first=900),  # 전년은 정정된 최신 1000 이 분모
        row("2026-11", 2, 0, 1300, first=1100),  # 발표 당시 1100
        row("2026-11", 1, 0, 500),
    ]
    c = flash_chip(rows)
    assert c is not None and (c.ym, c.seg) == ("2026-11", 2)
    assert c.yoy == pytest.approx(0.1) and c.usd == 1100
    assert c.label == "1~20일" and c.text == "2026-11 1~20일 +10.0%"
    assert c.quality is Quality.ESTIMATED
    assert flash_chip([row("2026-11", 1, 0, 1, kind="imp_item")]) is None


def test_ok_라도_칩은_잠정() -> None:
    c = flash_chip([row("2026-11", 1, 0, 100, q=Quality.OK)])
    assert c is not None and c.quality is Quality.ESTIMATED and c.yoy is None


def test_새로_본_구간만_창_안() -> None:
    a = row("2026-11", 1, 0, 100, seen=T0)
    b = row("2026-10", 3, 0, 100, seen=T0 - timedelta(days=12))
    hits = new_segments([a, b], since=T0 - timedelta(days=1), until=T0)
    assert [(h.ym, h.seg) for h in hits] == [("2026-11", 1)]
    assert new_segments([a], since=T0, until=T0 + timedelta(hours=1)) == []  # 경계: since 는 제외
    with pytest.raises(ValueError, match="시간대"):
        new_segments([a], since=T0.replace(tzinfo=None), until=T0)


def test_누계_감소는_경고_목록() -> None:
    rows = [row("2026-11", 1, 0, 500), row("2026-11", 2, 0, 400), row("2026-11", 3, 0, 900)]
    assert monotonic_breaks(rows) == [("exp_item", "2026-11", 2, 0)]


def test_합성_세계_정정·열변경·최신구간() -> None:
    w = build_world()
    segs = segments(list(w.ten_day), kind="exp_item", months=2)
    assert (segs[0].ym, segs[0].seg, segs[0].span_text) == ("2026-12", 1, "01~10")
    rev = next(s for s in segs if (s.ym, s.seg) == ("2026-11", 3))
    c0 = rev.cells[0]
    assert c0.usd is not None and c0.first_usd is not None and c0.usd - c0.first_usd == 120_000_000
    assert rev.revised_at is not None
    cs = segments(list(w.ten_day), kind="exp_country", months=24)
    y2025 = [s for s in cs if s.ym.startswith("2025")]
    assert y2025 and all(s.cells[10].yoy is None for s in y2025)
    assert all(s.cells[10].yoy is not None for s in cs if s.ym.startswith("2026"))
    assert monotonic_breaks(list(w.ten_day)) == []
    chip = flash_chip(list(w.ten_day))
    assert chip is not None and (chip.ym, chip.seg) == ("2026-12", 1)

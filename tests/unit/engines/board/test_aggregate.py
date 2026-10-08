"""집계(kbj.engines.board.aggregate) — 새 시험(docs/p3_design.md §1.3: 원본 시험 없음).

미지 값은 unknown 으로 따로 세고(0·보합으로 채우지 않는다), 히트맵은 위아래에서 반씩 뜬다.
"""

from __future__ import annotations

from typing import Any

from kbj.engines.board import aggregate as agg
from kbj.engines.board.config import BoardConfig

CFG = BoardConfig.load()


def _row(code: str, chg: float | None, **kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = dict(
        code=code, name=code, chg_pct=chg, mktcap=1000.0, turnover=10.0, turnover_avg20=5.0,
        ret_5d=None, ret_21d=None, label=None, near_kind=None, sector="S",
    )  # fmt: skip
    d.update(kw)
    return d


def test_등락률을_모르면_unknown_이다() -> None:
    b = agg.breadth([_row("a", 1.0), _row("b", 0.0), _row("c", -2.0), _row("d", None)])
    assert b == dict(up=1, flat=1, down=1, total=3, unknown=1)


def test_거래대금을_모르면_합에_0으로_넣지_않고_부분이라고_적는다() -> None:
    r = agg.rollup("S", [_row("a", 1.0, turnover=None), _row("b", 2.0, turnover=30.0)])
    assert r["turnover"] == 30.0
    assert r["turnover_is_partial"] is True
    r2 = agg.rollup("S", [_row("a", 1.0, turnover=None)])
    assert r2["turnover"] is None and r2["turnover_mult"] is None


def test_시총가중_등락률과_가중_여부() -> None:
    r = agg.rollup("S", [_row("a", 10.0, mktcap=1000.0), _row("b", 0.0, mktcap=3000.0)])
    assert r["chg_pct"] == 2.5 and r["weighted"] is True
    eq = agg.rollup("S", [_row("a", 10.0, mktcap=None), _row("b", 0.0, mktcap=None)])
    assert eq["chg_pct"] == 5.0 and eq["weighted"] is False


def test_히트맵은_위아래에서_반씩_뜬다() -> None:
    n = CFG["display"]["heatmap_groups"] + 6
    rows = [_row(f"c{i}", float(n // 2 - i), sector=f"S{i:02d}") for i in range(n)]
    groups = agg.by_sector(rows)  # 등락률 내림차순
    hm = agg.heatmap(rows, groups, CFG)
    lim = CFG["display"]["heatmap_groups"]
    assert len(hm) == lim
    names = [g["group"] for g in hm]
    half = lim // 2
    assert names[: lim - half] == [g["name"] for g in groups[: lim - half]]
    assert names[lim - half :] == [g["name"] for g in groups[-half:]]
    assert hm[-1]["chg_pct"] < 0  # 하락 섹터가 반드시 실린다(청색 셀)
    assert hm[-1]["group"] == groups[-1]["name"]


def test_탐지기는_등락률을_모르면_재료반납으로_잡지_않는다() -> None:
    x = _row("a", None, giveback=0.9, giveback_pp=5.0, high_chg_pct=6.0)
    assert [e for e in agg.detect([x], CFG) if e["type"] == "material_giveback"] == []
    y = _row("b", 1.0, giveback=0.9, giveback_pp=5.0, high_chg_pct=6.0)
    assert [e["type"] for e in agg.detect([y], CFG)] == ["material_giveback"]

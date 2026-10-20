"""테마 강도·밸류체인 묶음(엔진 — metrics §31). 손으로 만든 작은 합성 입력으로 손계산과 대조."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import StockDay
from kbj.core.theme_rows import MemberRow, ThemeLinkRow, ThemeRow
from kbj.engines.board.aggregate import rollup
from kbj.engines.theme.strength import (
    EOK,
    chain_strengths,
    codes_by_theme,
    rank,
    stock_items,
    theme_strengths,
)

AT = datetime(2026, 10, 11, tzinfo=UTC)
D = date(2026, 10, 14)
Q = Quality.OK


def _theme(tid: str, ord_: int, chain: str | None = None) -> ThemeRow:
    return ThemeRow(tid, f"테마{ord_}", "섹션", chain, ord_, "s", AT, Q)


def _link(tid: str, no: int, o: int = 0) -> ThemeLinkRow:
    return ThemeLinkRow(tid, no, f"원본{no}", o, "s", AT, Q)


def _mem(no: int, code: str) -> MemberRow:
    return MemberRow(no, code, None, "s", AT, Q)


def _day(
    code: str,
    chg: float | None,
    r5: float | None,
    r21: float | None,
    cap: int | None,
    *,
    label: str | None = None,
    q: Quality = Quality.OK,
    **extra: Any,
) -> StockDay:
    return StockDay(
        code=code, date=D, name=f"종목{code}", close=1.0, chg_pct=chg, turnover=10 * EOK,
        turnover_is_estimate=False, mktcap=cap, label=label, near_kind=None, near_gap=None,
        status=None, suspect=False, sector=None, theme=None, ret_5d=r5, ret_21d=r21,
        vol_mult=None, source="krx", quality=q, extra=extra,
    )  # fmt: skip


THEMES = [_theme("a", 0, "CH"), _theme("b", 1, "CH"), _theme("c", 2)]
LINKS = [_link("a", 1), _link("a", 2, 1), _link("b", 2), _link("c", 3)]
MEMBERS = [_mem(1, "000001"), _mem(1, "000002"), _mem(2, "000002"), _mem(2, "000003"),
           _mem(3, "000009")]  # fmt: skip
DAYS = {
    "000001": _day("000001", 2.0, 4.0, 10.0, 300 * EOK, label="w52", turnover_avg20=5),
    "000002": _day("000002", -1.0, 1.0, None, 100 * EOK, turnover_avg20=10),
    "000003": _day("000003", 0.0, None, 5.0, None),
    "000004": _day("000004", 9.0, 9.0, 9.0, 1, q=Quality.INVALID),  # 계산에 쓰지 않는다
}


def test_members_are_the_union_of_linked_source_themes() -> None:
    codes = codes_by_theme(LINKS, MEMBERS)
    assert codes == {
        "a": ["000001", "000002", "000003"],
        "b": ["000002", "000003"],
        "c": ["000009"],
    }
    only = codes_by_theme(LINKS, MEMBERS, universe={"000001", "000003"})
    assert only["a"] == ["000001", "000003"] and only["c"] == []


def test_strength_matches_board_rollup_and_hand_math() -> None:
    codes = codes_by_theme(LINKS, MEMBERS)
    items = stock_items(DAYS)
    assert "000004" not in items  # invalid 은 뺀다
    st = theme_strengths(THEMES, codes, items)
    a = st["a"]
    ref = rollup("", [{**items[c], "mktcap": None} for c in codes["a"]], "theme")
    assert (a.chg_pct, a.ret_5d, a.ret_20d) == (ref["chg_pct"], ref["ret_5d"], ref["ret_21d"])
    # 동일가중(metrics §31): (2 − 1 + 0) / 3 = 0.33 — 시총 가중(1.25)이면 대형주가 테마를 덮었다
    assert math.isclose(a.chg_pct or 0, 0.33)
    assert not a.weighted and (a.up, a.flat, a.down, a.unknown) == (1, 1, 1, 0)
    assert a.up_ratio == pytest.approx(1 / 3, abs=1e-4)
    assert a.n_newhigh == 1 and a.mktcap == 400 * EOK
    assert a.leader_code in {"000001", "000002", "000003"}
    # 거래대금 배수 = 30억 / (5 + 10)억 — 20일 평균을 아는 둘만 분모(보드 롤업 규칙: 아는 것만)
    assert a.turnover_mult == pytest.approx(round(30 / 15, 2))
    c = st["c"]  # 시세 없는 종목뿐 — 값 None, 0 으로 채우지 않는다
    assert c.chg_pct is None and c.up_ratio is None and c.n_priced == 0 and c.unknown == 1


def test_rank_by_period_and_chains() -> None:
    codes = codes_by_theme(LINKS, MEMBERS)
    items = stock_items(DAYS)
    st = theme_strengths(THEMES, codes, items)
    assert [t.id for t in rank(THEMES, st, 1)][-1] == "c"  # 값 없음은 뒤
    by5 = [t.id for t in rank(THEMES, st, 5)]
    assert by5[0] == ("a" if (st["a"].ret_5d or 0) >= (st["b"].ret_5d or 0) else "b")
    with pytest.raises(ValueError):
        rank(THEMES, st, 3)
    chains = chain_strengths(THEMES, codes, items)
    assert [(c.key, c.themes) for c in chains] == [("CH", ("a", "b"))]
    assert chains[0].strength.n_members == 3  # a·b 합집합

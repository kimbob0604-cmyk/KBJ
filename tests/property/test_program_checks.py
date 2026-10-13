"""속성 시험(P5 F2 — docs/p5_design.md §8.3): 검산 ①②④ 가 합성 원장에서 언제나 0 차이이고, 한 칸만
어긋나도 반드시 잡는다. 1분 선은 빈 분을 잇지 않는다(보간 금지)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.core.p5_rows import MINUTE_INVESTORS, InvestorMinute, ProgramMarketRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.flows.minute import ORGN_PARTS, check_slot, minute_axis, minute_board
from kbj.engines.flows.program import check4

DAY = date(2026, 11, 17)
T0 = datetime(2026, 11, 17, 9, 0, tzinfo=KST)
flow = st.tuples(st.integers(0, 10_000), st.integers(0, 10_000))


def minute(parts: dict[str, tuple[int, int]], t: datetime = T0) -> dict[str, InvestorMinute]:
    """세부·외국인·기타법인·기타단체를 주면 기관 = 세부 합, 개인 = 나머지(① 이 0)인 한 분."""
    p = dict(parts)
    p["orgn"] = (sum(p[x][0] for x in ORGN_PARTS), sum(p[x][1] for x in ORGN_PARTS))
    rest = sum(p[x][1] - p[x][0] for x in ("frgn", "orgn", "etc_corp", "etc_orgt"))
    base = 20_000 + max(0, rest)
    p["prsn"] = (base, base - rest)
    return {
        i: InvestorMinute(
            t,
            DAY,
            "day",
            "SYNK",
            "SYN0",
            i,
            sv * 10,
            bv * 10,
            (bv - sv) * 10,
            sv,
            bv,
            bv - sv,
            Quality.OK,
        )
        for i, (sv, bv) in p.items()
    }


parts_st = st.fixed_dictionaries({i: flow for i in (*ORGN_PARTS, "frgn", "etc_corp", "etc_orgt")})


@settings(max_examples=60, deadline=None)
@given(parts_st)
def test_balanced_minute_passes(parts: dict[str, tuple[int, int]]) -> None:
    m = minute(parts)
    assert set(m) == set(MINUTE_INVESTORS)
    c = check_slot(m, spot=True)
    assert (c.c1_residual, c.c1_ok, c.c2_ok) == (0, True, True)


@settings(max_examples=60, deadline=None)
@given(parts_st, st.sampled_from(ORGN_PARTS), st.integers(8, 10_000))
def test_any_part_drift_breaks_check2(parts: dict[str, tuple[int, int]], who: str, d: int) -> None:
    m = minute(parts)
    r = m[who]
    m[who] = replace(r, buy_value=(r.buy_value or 0) + d, net_value=(r.net_value or 0) + d)
    assert check_slot(m, spot=True).c2_ok is False


def program_group(vals: list[tuple[int, int]]) -> list[ProgramMarketRow]:
    it = iter(vals)
    base: dict[tuple[str, str], tuple[int, int]] = {}
    for kind in ("arbt", "nabt"):
        for side in ("entm", "onsl"):
            base[(kind, side)] = next(it)
        e, o = base[(kind, "entm")], base[(kind, "onsl")]
        base[(kind, "smtn")] = (e[0] + o[0], e[1] + o[1])
    for side in ("entm", "onsl", "smtn"):
        a, n = base[("arbt", side)], base[("nabt", side)]
        base[("whol", side)] = (a[0] + n[0], a[1] + n[1])
    return [
        ProgramMarketRow(
            DAY,
            "K",
            "KRX",
            kind,  # pyright: ignore[reportArgumentType]
            side,  # pyright: ignore[reportArgumentType]
            None,
            None,
            None,
            sv,
            bv,
            bv - sv,
            None,
            "KIS",
            Quality.OK,
        )
        for (kind, side), (sv, bv) in base.items()
    ]


@settings(max_examples=60, deadline=None)
@given(st.lists(flow, min_size=4, max_size=4))
def test_program_group_balanced_has_zero_check4(vals: list[tuple[int, int]]) -> None:
    assert check4(program_group(vals)) == []


@settings(max_examples=60, deadline=None)
@given(st.lists(flow, min_size=4, max_size=4), st.integers(0, 8), st.integers(1, 1_000))
def test_program_one_cell_drift_is_caught(vals: list[tuple[int, int]], i: int, d: int) -> None:
    rows = program_group(vals)
    rows[i] = replace(rows[i], sell_value=(rows[i].sell_value or 0) + d)
    assert check4(rows) != []


@settings(max_examples=40, deadline=None)
@given(st.sets(st.integers(0, 390), max_size=60))
def test_missing_minutes_are_never_bridged(missing: set[int]) -> None:
    ax = minute_axis(T0, T0 + timedelta(minutes=390))
    parts = {i: (1, 2) for i in (*ORGN_PARTS, "frgn", "etc_corp", "etc_orgt")}
    rows = [
        r
        for k in range(391)
        if k not in missing
        for r in minute(parts, T0 + timedelta(minutes=k)).values()
    ]
    b = minute_board(rows, ax, DAY)
    frgn = next(ln for ln in b.lines if ln.investor == "frgn")
    assert [i for i, v in enumerate(frgn.values[:391]) if v is None] == sorted(missing)
    assert b.filled == 391 - len(missing)

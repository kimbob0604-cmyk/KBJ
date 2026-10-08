"""합성 원장 생성기 자기 시험 — 결정성·불변식(docs/p3_design.md §1.4 `ledger_gen.py` 행, §8.2)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from itertools import pairwise

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import INST7, Investor
from tests.fixtures.synthetic.ledger_gen import (
    EtfTruth,
    corrupt_investor,
    drop_investor,
    generate,
)


@pytest.fixture(scope="module")
def m():
    return generate(7)


def test_같은_시드는_같은_행() -> None:
    assert generate(3) == generate(3)


def test_다른_시드는_다른_행() -> None:
    assert generate(3).investors != generate(4).investors


def test_인자_하한() -> None:
    with pytest.raises(ValueError):
        generate(1, days=10)


def test_휴장일은_영업일에_없고_평일이다(m) -> None:
    assert len(m.trading_days) == 30
    assert len(m.holidays) == 2
    for h in m.holidays:
        assert h.weekday() < 5
        assert h not in m.trading_days
    assert list(m.trading_days) == sorted(m.trading_days)


def test_합성_코드만_쓴다(m) -> None:
    assert all(s.code.startswith("Q") for s in m.snaps)
    assert all(r.code.startswith("Q") for r in m.investors)
    assert all(e.code.startswith("E") for e in m.etf_days)


def _kis_rows(m):
    by: dict[tuple[str, date], dict[Investor, int]] = defaultdict(dict)
    for r in m.investors:
        if r.source == "kis":
            assert r.net_value is not None
            by[(r.code, r.date)][r.investor] = r.net_value
    return by


def test_검산_1_2_가_원자료에서_성립(m) -> None:
    for vals in _kis_rows(m).values():
        foreign = vals[Investor.FOREIGN] + vals.get(Investor.FOREIGN_OTHER, 0)
        total = (
            foreign + vals[Investor.INSTITUTION] + vals[Investor.OTHER_CORP]
            + vals[Investor.INDIVIDUAL]
        )  # fmt: skip
        assert total == 0
        assert sum(vals[i] for i in INST7) == vals[Investor.INSTITUTION]


def test_시장별_투자자는_종목_합(m) -> None:
    by: dict[tuple[str, date], dict[Investor, int]] = defaultdict(dict)
    for r in m.market_investors:
        assert r.net_value is not None
        by[(r.code, r.date)][r.investor] = r.net_value
    assert by
    for vals in by.values():
        assert (
            vals[Investor.FOREIGN] + vals[Investor.INSTITUTION] + vals[Investor.OTHER_CORP]
            + vals[Investor.INDIVIDUAL]
        ) == 0  # fmt: skip
        assert sum(vals[i] for i in INST7) == vals[Investor.INSTITUTION]


def test_거래정지일은_거래대금_0_투자자_행_없음(m) -> None:
    halted = [s for s in m.snaps if s.status_flags and "halted" in s.status_flags]
    assert halted
    inv_keys = {(r.code, r.date) for r in m.investors}
    for s in halted:
        assert s.turnover == 0
        assert (s.code, s.date) not in inv_keys


def test_마지막_날은_kis_마감과_잠정(m) -> None:
    last = m.last_day
    assert {s.source for s in m.snaps if s.date == last} == {"kis"}
    assert not [b for b in m.bars if b.date == last]
    prelim = [r for r in m.investors if r.source == "kis.prelim"]
    assert prelim and all(r.date == last and r.quality is Quality.ESTIMATED for r in prelim)


def test_상태_사례(m) -> None:
    assert m.spec("Q00016").flags == ("managed",)
    assert m.spec("Q00012").flags is None
    assert m.spec("Q00003").kind == "pref"
    assert m.spec("Q00009").market == "KONEX"


def test_ETF_참값이_공식과_같다(m) -> None:
    """순유입 = (Sₜ − Sₜ₋₁·r)·NAVₜ, 가격효과 = Sₜ₋₁·r·(NAVₜ − NAVₜ₋₁/r) — 생성 과정 ΔS 와 대조."""
    by: dict[str, list] = defaultdict(list)
    for e in m.etf_days:
        by[e.code].append(e)
    seen: set[str] = set()
    for code, rows in by.items():
        rows.sort(key=lambda e: e.date)
        assert m.etf_truth[(code, rows[0].date)].status == "new"
        for prev, cur in pairwise(rows):
            t: EtfTruth = m.etf_truth[(code, cur.date)]
            seen.add(t.status)
            r = t.ratio
            assert prev.list_shrs is not None and cur.list_shrs is not None
            assert prev.nav is not None and cur.nav is not None
            inflow = (cur.list_shrs - prev.list_shrs * r) * cur.nav
            price = prev.list_shrs * r * (cur.nav - prev.nav / r)
            assert t.inflow == pytest.approx(inflow, rel=1e-9, abs=1e-3)
            assert t.price_effect == pytest.approx(price, rel=1e-9, abs=1e-3)
    assert {"ok", "split_adjusted"} <= seen


def test_ETF_사례_분할_병합_분배금_신규_폐지(m) -> None:
    ratios = {(e.code, e.ratio) for e in m.split_events}
    assert ("E00003", 10.0) in ratios
    assert ("E00004", 0.2) in ratios
    d18 = m.trading_days[18]
    t = m.etf_truth[("E00005", d18)]
    assert t.inflow == 0
    assert t.price_effect is not None and t.price_effect < 0
    first_new = min(e.date for e in m.etf_days if e.code == "E00001")
    assert first_new == m.trading_days[10]
    meta = {x.code: x for x in m.etf_meta}
    last_e2 = max(e.date for e in m.etf_days if e.code == "E00002")
    assert meta["E00002"].delisted_on is not None and meta["E00002"].delisted_on > last_e2


def test_깨뜨리기_도구(m) -> None:
    d = m.trading_days[5]
    bad = corrupt_investor(m, "Q00000", d, Investor.FOREIGN, 1_000_000)
    diff = [(a, b) for a, b in zip(m.investors, bad.investors, strict=True) if a != b]
    assert len(diff) == 1
    assert diff[0][1].net_value - diff[0][0].net_value == 1_000_000  # type: ignore[operator]
    with pytest.raises(KeyError):
        corrupt_investor(m, "Q00000", m.holidays[0], Investor.FOREIGN, 1)
    dropped = drop_investor(m, Investor.OTHER_CORP)
    assert not [r for r in dropped.investors if r.investor is Investor.OTHER_CORP]
    assert not [r for r in dropped.market_investors if r.investor is Investor.OTHER_CORP]

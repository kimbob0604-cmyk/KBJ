"""보유 탐색·액티브 동시 편입(kbj.engines.etf.holders — metrics §19).

동시 편입 경계: 같은 날 액티브 ETF 2개 이상(1개는 제외), 비액티브·DROP·CUT 은 세지 않는다, 다른 날의
편입을 합쳐 '동시'로 세지 않는다. 합성 세계 기대값은 `p5_expect.yaml etf.consensus`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.core.p5_rows import EtfHolder
from kbj.core.quality import Quality
from kbj.core.rows import Change, Fund
from kbj.engines.etf.holders import active_consensus, fund_label, of_fund, of_stock
from kbj.store.repos import memory_repos
from tests.fixtures.synthetic.p5_world import build_world
from tests.unit.api.web_fixtures_p5.etf import RUN_DATE, load_etf

D1 = date(2026, 11, 16)
D2 = date(2026, 11, 17)
EXPECT = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "p5_expect.yaml"


def fund(fid: str, active: bool = True, name: str | None = None) -> Fund:
    return Fund(fid, "합성운용", fid, None, name or f"이름{fid}", None, active, "full", True, 0,
                "ETF_ISSUERS:synthetic")  # fmt: skip


def ch(fid: str, code: str, kind: str, day: date = D2) -> Change:
    k: Any = kind
    return Change(day, fid, code, k, f"종목{code}", D1, D1, 1, None, 1.0, None, 1.0, None, None)


def holder(
    fid: str, code: str, wt: float | None, asof: date = D1, active: bool = False
) -> EtfHolder:
    return EtfHolder(fid, None, None, active, asof, code, None, wt, 1.0, 1.0, "ETF_ISSUERS:x",
                     Quality.OK)  # fmt: skip


FUNDS = {
    "A": fund("A"),
    "B": fund("B"),
    "C": fund("C"),
    "P": fund("P", active=False),
}


def test_동시편입은_액티브_2개_이상() -> None:
    rows = active_consensus(
        [ch("A", "X", "NEW"), ch("B", "X", "IN10"), ch("A", "Y", "NEW"), ch("P", "Y", "NEW")],
        FUNDS,
    )
    assert [r.code for r in rows] == ["X"]  # Y 는 액티브 1개 + 비액티브 1개 — 제외
    x = rows[0]
    assert (x.n_funds, x.kinds, x.funds) == (2, ("NEW", "IN10"), ("이름A", "이름B"))
    assert x.run_dates == (D2,)


def test_ADD_는_세고_DROP_CUT_은_세지_않는다() -> None:
    rows = active_consensus(
        [ch("A", "X", "ADD"), ch("B", "X", "ADD"), ch("A", "Z", "DROP"), ch("B", "Z", "CUT")],
        FUNDS,
    )
    assert [(r.code, r.kinds) for r in rows] == [("X", ("ADD",))]


def test_다른_날의_편입은_합쳐_세지_않는다() -> None:
    rows = active_consensus([ch("A", "X", "NEW", D1), ch("B", "X", "NEW", D2)], FUNDS)
    assert rows == []


def test_여러_날_창은_조건을_채운_날을_합친다() -> None:
    rows = active_consensus(
        [ch("A", "X", "NEW", D1), ch("B", "X", "NEW", D1), ch("C", "X", "ADD", D2),
         ch("A", "X", "ADD", D2)],
        FUNDS,
    )  # fmt: skip
    assert len(rows) == 1
    assert rows[0].n_funds == 3 and rows[0].run_dates == (D1, D2)
    assert rows[0].kinds == ("NEW", "ADD")


def test_같은_펀드가_두_번이어도_한_번() -> None:
    assert active_consensus([ch("A", "X", "NEW"), ch("A", "X", "ADD")], FUNDS) == []


def test_순서는_펀드_수_내림차순_같으면_코드() -> None:
    rows = active_consensus(
        [ch(f, c, "NEW") for f in "AB" for c in ("Q", "P")] + [ch("C", "P", "NEW")], FUNDS
    )
    assert [r.code for r in rows] == ["P", "Q"]


def test_min_funds_는_2_이상() -> None:
    with pytest.raises(ValueError):
        active_consensus([], FUNDS, min_funds=1)


def test_종목별_담은_ETF_비중순_펀드마다_마지막_스냅() -> None:
    rows = of_stock(
        [
            holder("F1", "X", 3.0),
            holder("F2", "X", 9.0),
            holder("F1", "X", 5.0, asof=D2),  # F1 은 더 늦은 스냅 한 줄만
            holder("F3", "Y", 50.0),  # 다른 종목
            holder("F4", "X", None),  # 비중 모름 — 맨 뒤
            holder("F5", "X", 5.0, active=True),  # 같은 비중이면 액티브 먼저
        ],
        "X",
    )
    assert [(r.fund_id, r.wt) for r in rows] == [
        ("F2", 9.0), ("F5", 5.0), ("F1", 5.0), ("F4", None),
    ]  # fmt: skip
    assert rows[2].asof == D2  # 운용사 기준일이 달라도 그대로


def test_ETF_구성종목은_비중순() -> None:
    rows = of_fund([holder("F", "B", 1.0), holder("F", "A", 1.0), holder("F", "C", 7.0)])
    assert [r.code for r in rows] == ["C", "A", "B"]


def test_펀드_이름이_없으면_id() -> None:
    assert fund_label("F9", None) == "F9"
    assert fund_label("A", FUNDS["A"]) == "이름A"


def test_합성_세계_기대값() -> None:
    expect = yaml.safe_load(EXPECT.read_text(encoding="utf-8"))["etf"]
    w = build_world()
    repos = memory_repos()
    load_etf(repos, w)
    rows = active_consensus(repos.etf.changes(RUN_DATE), repos.etf.funds())
    got = [r.code for r in rows]
    assert got == expect["consensus"] == w.expect_etf_consensus()
    assert not set(expect["single_new"]) & set(got)
    holders = of_stock(
        repos.etf.holders_of(expect["consensus"][0], RUN_DATE), expect["consensus"][0]
    )
    assert all(h.is_active for h in holders) and len(holders) == 2

"""급등·급락 — 시총 하한·대상·상태·순위(docs/metrics.md §6.8). 손 행(합성)만 쓴다."""

from __future__ import annotations

import pytest

from kbj.core.quality import Quality
from kbj.engines.market.movers import MIN_MKTCAP_WON, MOVERS_N, top_movers
from tests.unit.engines.flows.flow_rows import days, lrow

D = days(2)
DAY = D[-1]
CAP = MIN_MKTCAP_WON


def test_하한_경계는_넣고_아래는_뺀다() -> None:
    rows = [
        lrow("A00001", DAY, chg_pct=3.0, mktcap=CAP),  # 경계 = 넣는다
        lrow("A00002", DAY, chg_pct=5.0, mktcap=CAP - 1),  # 하한 아래
        lrow("A00003", DAY, chg_pct=-1.0, mktcap=CAP * 10),
    ]
    m = top_movers(rows, DAY)
    assert [x.code for x in m.up] == ["A00001"]
    assert [x.code for x in m.down] == ["A00003"]
    assert m.n_eligible == 2 and m.min_mktcap == CAP


def test_대상_밖_종목과_관리종목은_빠지고_상태_모름은_남아_센다() -> None:
    rows = [
        lrow("A00001", DAY, chg_pct=1.0, mktcap=CAP, kind="pref"),
        lrow("A00002", DAY, chg_pct=2.0, mktcap=CAP, kind="etf"),
        lrow("A00003", DAY, chg_pct=3.0, mktcap=CAP, market="KONEX"),
        lrow("A00004", DAY, chg_pct=4.0, mktcap=CAP, flags=("managed",)),
        lrow("A00005", DAY, chg_pct=5.0, mktcap=CAP, flags=None),  # 상태 모름 — 남는다
        lrow("A00006", DAY, chg_pct=6.0, mktcap=CAP, kind="spac"),
        lrow("A00007", D[0], chg_pct=7.0, mktcap=CAP),  # 다른 날
    ]
    m = top_movers(rows, DAY)
    assert [x.code for x in m.up] == ["A00005"]
    assert m.n_status_unknown == 1 and m.n_eligible == 1


def test_시총_등락률_모름_invalid_는_빼고_센다() -> None:
    rows = [
        lrow("A00001", DAY, chg_pct=1.0, mktcap=None),
        lrow("A00002", DAY, chg_pct=None, mktcap=CAP),
        lrow("A00003", DAY, chg_pct=9.0, mktcap=CAP, quality=Quality.INVALID),
        lrow("A00004", DAY, chg_pct=-2.0, mktcap=CAP),
    ]
    m = top_movers(rows, DAY)
    assert (m.n_no_mktcap, m.n_no_chg, m.n_eligible) == (1, 2, 1)
    assert m.up == () and [x.code for x in m.down] == ["A00004"]


def test_순위_같으면_시총_큰_순_보합은_어느_쪽도_아니다() -> None:
    rows = [
        lrow("A00001", DAY, chg_pct=2.0, mktcap=CAP),
        lrow("A00002", DAY, chg_pct=2.0, mktcap=CAP * 3),
        lrow("A00003", DAY, chg_pct=5.0, mktcap=CAP),
        lrow("A00004", DAY, chg_pct=0.0, mktcap=CAP * 9),
        lrow("A00005", DAY, chg_pct=-4.0, mktcap=CAP),
        lrow("A00006", DAY, chg_pct=-4.0, mktcap=CAP * 2),
        lrow("A00007", DAY, chg_pct=-0.5, mktcap=CAP),
    ]
    m = top_movers(rows, DAY)
    assert [x.code for x in m.up] == ["A00003", "A00002", "A00001"]
    assert [x.code for x in m.down] == ["A00006", "A00005", "A00007"]
    assert "A00004" not in {x.code for x in (*m.up, *m.down)}
    assert m.n_eligible == 7
    top = m.up[0]
    assert (top.chg_pct, top.mktcap, top.quality) == (5.0, CAP, Quality.OK)


def test_각_n_개까지만_모자라면_있는_만큼() -> None:
    rows = [lrow(f"A{i:05d}", DAY, chg_pct=float(i + 1), mktcap=CAP) for i in range(15)]
    rows.append(lrow("B00001", DAY, chg_pct=-1.0, mktcap=CAP))
    m = top_movers(rows, DAY)
    assert len(m.up) == MOVERS_N and m.up[0].chg_pct == 15.0
    assert len(m.down) == 1
    assert len(top_movers(rows, DAY, n=3).up) == 3


def test_원천_품질은_모집단에서_비면_없다() -> None:
    rows = [
        lrow("A00001", DAY, chg_pct=1.0, mktcap=CAP),
        lrow("A00002", DAY, chg_pct=-1.0, mktcap=CAP, quality=Quality.ESTIMATED),
    ]
    m = top_movers(rows, DAY)
    assert m.quality is Quality.ESTIMATED
    assert m.source  # 손 행의 원천(krx·kis) 묶음
    empty = top_movers([], DAY)
    assert (empty.quality, empty.source, empty.n_eligible) == (None, "", 0)


def test_인자_검증() -> None:
    with pytest.raises(ValueError, match="min_mktcap"):
        top_movers([], DAY, min_mktcap=-1)
    with pytest.raises(ValueError, match="n 은"):
        top_movers([], DAY, n=0)

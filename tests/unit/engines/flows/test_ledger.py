"""일별 원장 — 원천 우선순위·기간 집계·잠정→확정(docs/metrics.md §0·§1·§5, D-P3-7)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import INST7, Investor, InvestorDay, UniverseRow
from kbj.engines.flows.ledger import (
    Ledger,
    build_ledger,
    close_as_of,
    source_label,
    sum_known,
    worst_quality,
)
from kbj.store.repos import memory_repos
from tests.fixtures.synthetic.ledger_gen import generate, ledger_inputs
from tests.unit.engines.flows.flow_rows import bar, days, inv, ledger_of, lrow, snap

D = days(6)
NOW = datetime(2026, 9, 9, 7, 0, tzinfo=UTC)


def _ledger(snaps=(), bars=(), invs=(), uni=(), tdays=None) -> Ledger:
    return build_ledger(snaps, bars, invs, uni, tdays or D)


# ── 원천 우선순위 ────────────────────────────────────────────────────────────────────────


def test_가격은_krx_가_kis_보다_앞선다() -> None:
    led = _ledger(
        snaps=[
            snap("A00001", D[0], close=1000.0, turnover=10, source="kis"),
            snap("A00001", D[0], close=1010.0, turnover=20, source="krx"),
        ]
    )
    r = led.get("A00001", D[0])
    assert r is not None
    assert (r.close, r.turnover) == (1010.0, 20)
    assert r.sources["close"] == "krx"


def test_invalid_스냅은_쓰지_않고_일봉으로() -> None:
    led = _ledger(
        snaps=[snap("A00001", D[0], close=999.0, source="kis", quality=Quality.INVALID)],
        bars=[bar("A00001", D[0], close=1000.0, turnover=5)],
    )
    r = led.get("A00001", D[0])
    assert r is not None and r.usable
    assert (r.close, r.turnover, r.chg_pct, r.mktcap) == (1000.0, 5, None, None)
    assert r.flags is None  # invalid 스냅의 상태는 쓰지 않는다 → 모름


def test_invalid_뿐이면_행이_invalid() -> None:
    led = _ledger(snaps=[snap("A00001", D[0], source="kis", quality=Quality.INVALID)])
    r = led.get("A00001", D[0])
    assert r is not None and not r.usable
    assert "가격 행이 invalid 뿐" in r.notes


def test_투자자는_kis_마감이_장중_잠정을_이긴다() -> None:
    led = _ledger(
        snaps=[snap("A00001", D[0])],
        invs=[
            *inv("A00001", D[0], foreign=5, inst=-5, other_corp=0),
            InvestorDay(
                "A00001", D[0], Investor.FOREIGN, 999, None, "kis.prelim", "KRX", Quality.ESTIMATED
            ),
        ],
    )
    r = led.get("A00001", D[0])
    assert r is not None
    assert r.foreign == 5 and r.quality is Quality.OK


def test_잠정만_있으면_estimated() -> None:
    led = _ledger(
        invs=[
            InvestorDay(
                "A00001", D[0], Investor.FOREIGN, 7, None, "kis.prelim", "KRX", Quality.ESTIMATED
            )
        ]
    )
    r = led.get("A00001", D[0])
    assert r is not None
    assert r.quality is Quality.ESTIMATED and r.foreign == 7 and r.indiv is None
    assert not r.traded  # 거래대금을 모르면 거래일로 세지 않는다


def test_외국인은_외국인_더하기_기타외국인() -> None:
    rows = [
        *inv("A00001", D[0], foreign=10, inst=0, other_corp=0, indiv=-12),
        InvestorDay("A00001", D[0], Investor.FOREIGN_OTHER, 2, None, "kis", "KRX", Quality.OK),
    ]
    r = _ledger(invs=rows).get("A00001", D[0])
    assert r is not None and r.foreign == 12


def test_기관_7구분_일부만이면_없음으로() -> None:
    rows = inv("A00001", D[0], inst=6, inst7=[1, 2, 3])
    r = _ledger(invs=rows).get("A00001", D[0])
    assert r is not None
    assert r.inst7 is None
    assert any("7구분 일부만" in n for n in r.notes)
    full = _ledger(invs=inv("A00001", D[0], inst=28, inst7=list(range(1, 8)))).get("A00001", D[0])
    assert full is not None and full.inst7 == dict(zip(INST7, range(1, 8), strict=True))


def test_invalid_투자자_행이_고르면_행이_invalid() -> None:
    rows = inv("A00001", D[0], foreign=1, inst=-1, quality=Quality.INVALID)
    r = _ledger(invs=rows).get("A00001", D[0])
    assert r is not None and not r.usable


def test_휴장일_행은_버리고_수를_남긴다() -> None:
    hol = D[-1]
    led = build_ledger([snap("A00001", D[0]), snap("A00001", hol)], [], [], [], D[:-1])
    assert led.get("A00001", hol) is None
    assert any("영업일 밖 행 1개" in n for n in led.notes)


def test_이름_시장_종류는_유니버스로_채운다() -> None:
    uni = [UniverseRow("A00001", D[0], "유니", "KOSDAQ", "pref", None, "krx", Quality.OK)]
    r = _ledger(bars=[bar("A00001", D[0])], uni=uni).get("A00001", D[0])
    assert r is not None
    assert (r.name, r.market, r.kind) == ("유니", "KOSDAQ", "pref")


def test_거래정지는_거래일이_아니다() -> None:
    led = _ledger(
        snaps=[
            snap("A00001", D[0], turnover=0, flags=("halted",)),
            snap("A00001", D[1], turnover=5),
            snap("A00001", D[2], turnover=0),
        ]
    )
    assert [r.traded for r in led.series("A00001")] == [False, True, False]


def test_원장_키가_틀리면_거부() -> None:
    r = lrow("A00001", D[0])
    with pytest.raises(ValueError):
        Ledger(rows={("A00002", D[0]): r}, trading_days=(D[0],))
    with pytest.raises(ValueError):
        Ledger(rows={("A00001", D[0]): r}, trading_days=(D[1],))


# ── 기간 집계 ────────────────────────────────────────────────────────────────────────────


def test_기간합은_일별합_휴장일_정지일_포함() -> None:
    m = generate(11)
    led = build_ledger(**ledger_inputs(m))  # type: ignore[arg-type]
    end = m.last_day
    for code in ("Q00001", "Q00002", "Q00005", "Q00000"):
        for n in (1, 5, 20):
            agg = led.window(code, end, n)
            dates = led.days_upto(end, n)
            assert agg.dates == dates
            rows = [led.get(code, d) for d in dates]
            got = [r for r in rows if r is not None and r.usable]
            assert agg.sum_turnover == sum_known(r.turnover for r in got)
            assert agg.sums_by_investor["foreign"] == sum_known(r.foreign for r in got)
            assert agg.n_traded == sum(1 for r in got if r.traded)
            if agg.n_traded:
                assert agg.avg_turnover == pytest.approx(agg.sum_turnover / agg.n_traded)  # type: ignore[operator]
    # 휴장일은 영업일에 없다 — 창이 휴장일을 세지 않는다
    assert not set(m.holidays) & set(led.days_upto(end, 30))


def test_일평균은_정지일을_분모에서_뺀다() -> None:
    rows = [
        lrow("A00001", D[0], turnover=100),
        lrow("A00001", D[1], turnover=0, traded=False, flags=("halted",)),
        lrow("A00001", D[2], turnover=300),
    ]
    agg = ledger_of(rows).window("A00001", D[2], 3)
    assert agg.sum_turnover == 400
    assert agg.n_traded == 2
    assert agg.avg_turnover == 200


def test_일평균은_거래대금을_모르는_날을_0원으로_세지_않는다() -> None:
    """거래량은 있어 거래된 날이지만 금액을 모르면 일평균 분모에서 뺀다(없는 날 ≠ 0원)."""
    rows = [
        lrow("A00001", D[0], turnover=1000),
        lrow("A00001", D[1], turnover=None, traded=True),
    ]
    agg = ledger_of(rows).window("A00001", D[1], 2)
    assert agg.n_traded == 2
    assert agg.sum_turnover == 1000
    assert agg.avg_turnover == 1000


def test_값이_하나도_없으면_None_이지_0_이_아니다() -> None:
    rows = [lrow("A00001", D[0], foreign=None, turnover=None, traded=False)]
    agg = ledger_of(rows).window("A00001", D[0], 1)
    assert agg.sum_turnover is None
    assert agg.avg_turnover is None
    assert agg.sums_by_investor["foreign"] is None


def test_invalid_행은_집계에서_빠지고_센다() -> None:
    rows = [
        lrow("A00001", D[0], turnover=100, foreign=10),
        lrow("A00001", D[1], turnover=900, foreign=90, quality=Quality.INVALID),
    ]
    agg = ledger_of(rows).window("A00001", D[1], 2)
    assert (agg.sum_turnover, agg.sums_by_investor["foreign"], agg.n_invalid) == (100, 10, 1)
    assert agg.last_mktcap is None  # 마지막 날 행이 invalid


def test_rows_desc_는_빈_날을_None_으로() -> None:
    led = ledger_of([lrow("A00001", D[0]), lrow("A00001", D[2])], D[:3])
    got = led.rows_desc("A00001", D[2])
    assert [r is None for r in got] == [False, True, False]
    assert len(led.rows_desc("A00001", D[2], 2)) == 2


def test_days_upto_n_검증() -> None:
    with pytest.raises(ValueError):
        ledger_of([lrow("A00001", D[0])]).days_upto(D[0], 0)


# ── 장중 → 확정 덮어쓰기(저장소 + 원장) ────────────────────────────────────────────────────


def test_장중_잠정이_마감_확정으로_바뀌고_차이가_남는다() -> None:
    repos = memory_repos()
    d = D[0]
    est = [
        InvestorDay("A00001", d, Investor.FOREIGN, 100, None, "kis.prelim", "KRX",
                    Quality.ESTIMATED)
    ]  # fmt: skip
    repos.flows.upsert_investor_days(est, revise=False, loaded_by="t", now=NOW, received_at=NOW)
    led1 = build_ledger([], [], [r for rs in repos.flows.days(None, d, d).values() for r in rs],
                        [], [d])  # fmt: skip
    r1 = led1.get("A00001", d)
    assert r1 is not None and (r1.foreign, r1.quality) == (100, Quality.ESTIMATED)

    final = inv("A00001", d, foreign=130, inst=-30, other_corp=0)
    rep = repos.flows.upsert_investor_days(final, revise=True, loaded_by="t", now=NOW)
    assert rep.revised == 1 and rep.revisions[0].diff == 30
    led2 = build_ledger([], [], [r for rs in repos.flows.days(None, d, d).values() for r in rs],
                        [], [d])  # fmt: skip
    r2 = led2.get("A00001", d)
    assert r2 is not None and (r2.foreign, r2.quality) == (130, Quality.OK)
    # 확정 뒤 늦게 온 잠정은 쓰지 않는다
    late = repos.flows.upsert_investor_days(est, revise=False, loaded_by="t", now=NOW)
    assert late.skipped == 1


# ── 도우미 ─────────────────────────────────────────────────────────────────────────────


def test_도우미() -> None:
    assert worst_quality([]) is None
    assert worst_quality([Quality.OK, Quality.ESTIMATED, Quality.STALE]) is Quality.ESTIMATED
    assert source_label(["kis", "krx", "KRX", "kis.prelim"]) == "KRX+KIS+KIS(잠정)"
    assert sum_known([None, None]) is None
    assert sum_known([None, 0]) == 0
    a = close_as_of(D[0])
    assert (a.hour, a.minute) == (15, 30) and a.utcoffset() is not None
